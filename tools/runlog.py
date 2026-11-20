"""Per-run training logs (design doc D9, T7, G2).

Every training/test run writes one JSON-lines file under ``server/data/vision/runs/``,
appended and flushed line by line so a crash or Ctrl-C still leaves everything up to that point.

Line types (``"t"``):
  start  tool, job, settings, port, bridge version, git revision
  call   one HuskyLens command: method, args, status, ok, ms, plus the raw frames
         sent to and received from the camera (``tx`` / ``rx`` hex) when on real hardware
  event  free text from the tool (what the operator saw), plus optional fields
  end    outcome (ok | cancelled | error), summary, error text

Usage::

    run = RunLog("train", tool="train_ui", settings={...})
    hl = LoggedHusky(hl, lambda: run)   # every camera call is now recorded
    ...
    run.end("ok", summary={...})
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "server" / "data" / "vision" / "runs"


def git_rev() -> str | None:
    try:
        return subprocess.run(["git", "describe", "--always", "--dirty"], cwd=ROOT, capture_output=True,
                              text=True, timeout=2).stdout.strip() or None
    except Exception:
        return None


class RunLog:
    def __init__(self, job: str, **meta):
        self.started = datetime.now()
        self.t0 = time.monotonic()
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        stamp = f"{self.started:%Y%m%d_%H%M%S}"
        path = RUN_DIR / f"{stamp}_{job}.jsonl"
        n = 1
        while path.exists():  # two runs in the same second
            n += 1
            path = RUN_DIR / f"{stamp}_{job}_{n}.jsonl"
        self.path = path
        self.lock = threading.Lock()
        self.calls = 0
        self.closed = False
        self._f = path.open("a", encoding="utf-8")
        self.write("start", job=job, at=self.started.isoformat(timespec="seconds"), git=git_rev(), **meta)

    def write(self, t: str, **fields):
        with self.lock:
            if self.closed:
                return
            line = {"t": t, "s": round(time.monotonic() - self.t0, 3), **fields}
            self._f.write(json.dumps(line, default=str) + "\n")
            self._f.flush()

    def event(self, text: str, **fields):
        self.write("event", text=text, **fields)

    def call(self, **fields):
        self.calls += 1
        self.write("call", **fields)

    def end(self, outcome: str, **fields):
        self.write("end", outcome=outcome, calls=self.calls,
                   duration_s=round(time.monotonic() - self.t0, 1), **fields)
        with self.lock:
            self.closed = True
            self._f.close()

    def rel(self) -> str:
        try:
            return str(self.path.relative_to(ROOT))
        except ValueError:
            return str(self.path)


class _FrameTap:
    """Wraps a HuskyLens transport and remembers the raw frames of the last exchange."""

    def __init__(self, inner):
        self.inner = inner
        self.tx: list[str] = []
        self.rx: list[str] = []

    def husky(self, frame: bytes, expect=None, timeout_ms=None):
        self.tx.append(frame.hex())
        frames = self.inner.husky(frame, expect=expect, timeout_ms=timeout_ms)
        self.rx.extend(f.hex() for f in frames)
        return frames

    def __getattr__(self, name):
        return getattr(self.inner, name)


# HuskyLens methods that talk to the camera and are worth a log line.
LOGGED = {"knock", "set_algorithm", "learn", "forget", "set_name", "text", "clear_text", "save_model",
          "load_model", "request", "blocks", "arrows", "command", "raw"}


class LoggedHusky:
    """Proxy around ``HuskyLens`` (or a fake) that records every camera call to the current run.

    ``current`` returns the active RunLog or None; with None (e.g. the UI's idle polling)
    calls pass straight through unlogged.
    """

    def __init__(self, hl, current: Callable[[], RunLog | None]):
        self._hl = hl
        self._current = current
        self._tap = None
        if hasattr(hl, "t"):  # real HuskyLens: tap its transport for raw frames
            self._tap = _FrameTap(hl.t)
            hl.t = self._tap

    def __getattr__(self, name):
        attr = getattr(self._hl, name)
        if name not in LOGGED or not callable(attr):
            return attr

        def wrapped(*args, **kwargs):
            run = self._current()
            if run is None:
                return attr(*args, **kwargs)
            if self._tap is not None:
                self._tap.tx, self._tap.rx = [], []
            t = time.monotonic()
            entry: dict = {"method": name, "args": list(args)}
            if kwargs:
                entry["kwargs"] = kwargs
            try:
                result = attr(*args, **kwargs)
            except Exception as e:
                entry.update(error=f"{type(e).__name__}: {e}")
                raise
            else:
                entry.update(_summarise(result))
                return result
            finally:
                entry["ms"] = round((time.monotonic() - t) * 1000, 1)
                if self._tap is not None:
                    entry.update(tx=self._tap.tx, rx=self._tap.rx)
                run.call(**entry)

        return wrapped


def _summarise(result) -> dict:
    if isinstance(result, dict):
        out = {k: result[k] for k in ("ok", "status") if k in result}
        if result.get("info"):
            out["info"] = result["info"]
        if "blocks" in result:
            out["blocks"] = [{k: b.get(k) for k in ("id", "x", "y", "width", "height")} for b in result["blocks"]]
        return out
    if isinstance(result, list):  # blocks()/arrows()
        return {"blocks": [{k: b.get(k) for k in ("id", "x", "y", "width", "height")} for b in result]}
    return {"result": result}
