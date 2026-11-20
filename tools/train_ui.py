"""Minimal local web UI for HuskyLens gesture training (design doc D9, T7, G2).

Same procedure as tools/train_gestures.py, driven from a browser page instead of the terminal:
train each class (rock/paper/scissors/nothing = IDs 1-4), watch what the camera currently
recognises, and run the 30-throw accuracy test. Finds the camera on its own micro-USB port
(firmware >= 0.5.1, no XIAO needed) or through the XIAO bridge (D1-A), whichever answers
(server/connect.py). Through the bridge it also uses the XIAO's onboard LED for the CLI's cues.
Needs only pyserial (A6).

    python tools/train_ui.py            # real hardware, auto-detected (XIAO, then camera USB)
    python tools/train_ui.py --link usb # only the camera's micro-USB
    python tools/train_ui.py --fake     # simulated camera, no hardware needed
    open http://127.0.0.1:8765

Only one program can hold the serial port: stop train_gestures.py / husky_test.py first.
The HuskyLens can't stream its picture over UART, so the page shows what the camera
*recognises*, not the image itself. Watch the camera's own screen for that.
"""

from __future__ import annotations

import argparse
import json
import queue
import random
import sys
import threading
import time
import traceback
import webbrowser
from collections import Counter
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tools"))

from huskylens import HuskyError, HuskyLens  # noqa: E402
from runlog import LoggedHusky, RunLog  # noqa: E402
from train_gestures import CLASSES, CUE_DONE, CUE_HIT, CUE_MISS, CUE_RGB, LOG_DIR  # noqa: E402

NAME_TO_ID = {name: i for i, (name, _) in CLASSES.items()}
GESTURES = ["rock", "paper", "scissors"]
# Allowed range per setting. D9 recommends 10-20 samples per class; T7/G2 use 30 throws and a ~300 ms vote (§5).
SETTING_RANGES = {"samples": (1, 50), "interval": (0.2, 5), "countdown": (1, 10), "throw_countdown": (1, 10),
                  "window": (0.1, 2), "test_n": (3, 60)}
G2_PASS = 0.85


def throw_bucket(got: str) -> str:
    """Fold a vote result into the FR-07 classes: rock/paper/scissors/nothing/unknown."""
    if got in GESTURES:
        return got
    return "nothing" if got in ("nothing", "none") else "unknown"


class Cancelled(Exception):
    pass


# --------------------------------------------------------------------------- simulated camera
class FakeHusky:
    """Stands in for HuskyLens + bridge so the UI can be tried without hardware.

    It 'sees' whatever the UI is currently asking the player to show (``pose``), correctly
    about 90 % of the time once that class has been learned.
    """

    def __init__(self):
        self.learned: Counter = Counter()
        self.pose: str | None = None

    def knock(self):
        return {"ok": True, "status": "OK"}

    def set_algorithm(self, _):
        return {"ok": True, "status": "OK"}

    def learn(self, cid):
        time.sleep(0.05)
        self.learned[cid] += 1
        return {"ok": True, "status": "OK"}

    def forget(self):
        self.learned.clear()
        return {"ok": True, "status": "OK"}

    def set_name(self, *_):
        return {"ok": True, "status": "OK"}

    def text(self, *_):
        return {"ok": True, "status": "OK"}

    def clear_text(self):
        return {"ok": True, "status": "OK"}

    def save_model(self, _):
        return {"ok": True, "status": "OK"}

    def load_model(self, _):
        return {"ok": True, "status": "OK"}

    def blocks(self, **_):
        time.sleep(0.03)
        cid = NAME_TO_ID.get(self.pose or "", 0)
        if not cid or not self.learned[cid]:
            return []
        if cid == 4:  # a learned "nothing" class reads as ID 4, like the real camera
            return [{"id": 4, "x": 160, "y": 120, "width": 320, "height": 240, "learned": True}]
        if random.random() > 0.9:
            cid = random.choice([c for c in (1, 2, 3) if c != cid])
        return [{"id": cid, "x": 160 + random.randint(-6, 6), "y": 120 + random.randint(-6, 6),
                 "width": 300, "height": 220, "learned": True}]

    def request(self, *_a, **_k):
        b = self.blocks()
        return {"ok": True, "status": "OK", "blocks": b,
                "info": {"count": len(b), "learned_ids": len(+self.learned), "frame": 0}}


# --------------------------------------------------------------------------- worker
class Trainer(threading.Thread):
    """Owns the serial link. Runs one job at a time; polls the camera when idle."""

    def __init__(self, fake: bool, bridge_port: str | None, baud: int | None, kind: str = "auto"):
        super().__init__(daemon=True)
        self.fake = fake
        self.kind = kind
        self.bridge_port = bridge_port
        self.baud = baud
        self.jobs: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.link = None
        self.hl = None
        self.fake_cam: FakeHusky | None = None
        self.run_log: RunLog | None = None  # the job being run; every camera call goes into it
        self.port = bridge_port
        self.version = None
        self.started = datetime.now()
        self.log_path = LOG_DIR / f"ui_session_{self.started:%Y%m%d_%H%M%S}.json"
        self.history: list[dict] = []
        self.s = {
            "mode": "simulated" if fake else "hardware",
            "link": "connecting",       # connecting | no_bridge | no_camera | ok
            "link_detail": "",
            "phase": "idle",            # idle | countdown | capturing | testing | busy
            "cls": None, "count": 0, "progress": [0, 0], "message": "Starting…",
            "samples": {n: 0 for n, _ in (c for c in CLASSES.values())},
            "learned_ids": None,
            "live": None,
            "test": None,
            "rate": None,               # camera reads per second while idle (D10 rigged-win, R7)
            "player": "",              # who is throwing; tags test throws (T7: mixed players)
            "led": not fake,
            "settings": {"samples": 15, "interval": 0.8, "countdown": 5, "throw_countdown": 3,
                         "window": 0.3, "test_n": 30},
            "events": [],
        }

    # ---- state helpers
    def set(self, **kw):
        with self.lock:
            self.s.update(kw)
        run = self.run_log
        if run is not None and kw.get("message"):
            run.event(kw["message"], kind="message")

    def event(self, text: str):
        with self.lock:
            ev = self.s["events"]
            ev.insert(0, f"{datetime.now():%H:%M:%S}  {text}")
            del ev[12:]
        if self.run_log is not None:
            self.run_log.event(text)

    def snapshot(self) -> str:
        with self.lock:
            return json.dumps(self.s)

    def sleep(self, seconds: float):
        if self.cancel.wait(seconds):
            raise Cancelled

    def cue(self, rgb: str | None = None, hz: float = 0):
        if self.run_log is not None:
            self.run_log.write("led", rgb=rgb or "#000000", blink_hz=hz)
        if self.fake or not self.s["led"] or not hasattr(self.link, "call"):
            return  # LED cues need the XIAO bridge
        try:
            r = self.link.call("status_led", rgb=rgb or "#000000", blink_hz=hz)
            if not r.get("ok"):
                self.set(led=False)
                self.event(f"LED cues off: {r.get('error')}")
        except Exception:
            pass  # a dead link is caught by the next camera call

    def save_log(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(json.dumps(
            {"started": self.started.isoformat(timespec="seconds"), "mode": self.s["mode"],
             "port": self.bridge_port, "jobs": self.history}, indent=2))

    # ---- connection
    def disconnect(self):
        if self.link is not None:
            try:
                self.link.close()
            except Exception:
                pass
        self.link = self.hl = None

    def connect(self) -> bool:
        if self.fake:
            self.fake_cam = FakeHusky()
            self.hl = LoggedHusky(self.fake_cam, lambda: self.run_log)
            self.set(link="ok", link_detail="simulated camera", message="Ready.")
            return True
        from connect import NoCamera, open_link

        if self.link is None:
            try:
                self.link, desc = open_link(self.kind, self.bridge_port, self.baud)
            except NoCamera as e:
                self.set(link="no_bridge", link_detail=str(e),
                         message="Plug in the camera's micro-USB (data cable) or the XIAO; it reconnects automatically.")
                return False
            except Exception as e:
                self.disconnect()
                self.set(link="no_bridge", link_detail=str(e))
                return False
            self.port = desc
            self.version = desc.split("(")[1].split(")")[0] if "(" in desc else None
            self.hl = LoggedHusky(HuskyLens(self.link), lambda: self.run_log)
        try:
            ok = self.hl.knock().get("ok")
        except Exception as e:
            self.disconnect()
            self.set(link="no_bridge", link_detail=str(e))
            return False
        if not ok:
            self.set(link="no_camera", link_detail=f"{self.port}: link OK, HuskyLens not answering",
                     message="HuskyLens not answering (through the XIAO: Protocol Type = Serial, 9600).")
            return False
        self.hl.set_algorithm("object_classification")
        self.set(link="ok", link_detail=f"{self.port} · object classification",
                 message="Ready.")
        self.event("Camera connected")
        self.cue()
        return True

    # ---- main loop
    def run(self):
        while True:
            if self.s["link"] != "ok":
                if not self.connect():
                    time.sleep(2)
                    continue
            try:
                job = self.jobs.get(timeout=0.15)
            except queue.Empty:
                self.poll_live()
                continue
            self.cancel.clear()
            name, args = job
            run = self.start_run(name, args)
            n_hist = len(self.history)
            outcome, err = "ok", None
            try:
                getattr(self, f"job_{name}")(**args)
            except Cancelled:
                outcome = "cancelled"
                self.event("Stopped")
                self.set(message="Stopped.")
            except (HuskyError, OSError, Exception) as e:  # noqa: B014 - keep the worker alive
                outcome, err = "error", traceback.format_exc()
                self.event(f"{name} failed: {e}")
                self.set(message=f"Error: {e}")
                if not self.fake:
                    self.disconnect()
                    self.set(link="connecting")
            finally:
                if self.hl is not None:
                    try:
                        self.hl.clear_text()
                    except Exception:
                        pass
                if self.fake_cam is not None:
                    self.fake_cam.pose = None
                self.cue()
                self.run_log = None
                summary = self.history[n_hist] if len(self.history) > n_hist else None
                if summary is None:
                    summary = {"job": name, **args, "outcome": outcome,
                               "at": datetime.now().isoformat(timespec="seconds")}
                    self.history.append(summary)
                summary["run_log"] = run.rel()
                run.end(outcome, summary=summary, error=err)
                self.event(f"Run log: {run.rel()}")
                self.set(phase="idle", cls=None, count=0, progress=[0, 0], live=None)
                self.save_log()

    def start_run(self, name: str, args: dict) -> RunLog:
        label = f"{name}_{args['cls']}" if "cls" in args else name
        with self.lock:
            settings = dict(self.s["settings"])
            samples = dict(self.s["samples"])
            learned = self.s["learned_ids"]
        self.run_log = RunLog(label, tool="train_ui", job_args=args, mode=self.s["mode"], port=self.port,
                              bridge_version=self.version, baud=self.baud, settings=settings,
                              samples_before=samples, learned_ids_before=learned,
                              session_log=str(self.log_path.relative_to(ROOT)))
        return self.run_log

    def poll_live(self):
        t0 = time.monotonic()
        try:
            r = self.hl.request("blocks")
        except Exception as e:
            if not self.fake:
                self.disconnect()
                self.set(link="connecting", live=None, link_detail=str(e))
            return
        dt = time.monotonic() - t0
        info = r.get("info") or {}
        with self.lock:
            prev = self.s["rate"]
            self.s["rate"] = round(1 / dt if prev is None else 0.8 * prev + 0.2 / max(dt, 1e-3), 1)
            if info.get("learned_ids") is not None:
                self.s["learned_ids"] = info["learned_ids"]
        self.set(live=self.describe(r.get("blocks", [])))

    @staticmethod
    def describe(blocks: list[dict]) -> dict:
        if not blocks:  # no block at all: no hand / nothing recognised (FR-07 "nothing")
            return {"name": "no hand", "id": None}
        b = blocks[0]
        name = CLASSES.get(b["id"], (f"id {b['id']}",))[0] if b["id"] else "unknown"
        return {"name": name, "id": b["id"], "x": b["x"], "y": b["y"], "w": b["width"], "h": b["height"]}

    def countdown(self, name: str, seconds: float, screen: str):
        rgb = CUE_RGB.get(name)
        if self.fake_cam is not None:
            self.fake_cam.pose = name
        total = int(round(seconds))
        for left in range(total, 0, -1):
            if left == total or left == 1:
                self.cue(rgb, 6 if left == 1 else 2)
            self.set(count=left)
            self.hl.text(f"{screen} {left}", 10, 10)
            self.sleep(1)
        self.set(count=0)
        self.cue(rgb)
        self.hl.text(f"{screen} GO", 10, 10)

    # ---- jobs
    def job_train(self, cls: str):
        cid = NAME_TO_ID[cls]
        st = self.s["settings"]
        n = int(st["samples"])
        self.set(phase="countdown", cls=cls, progress=[0, n], message=CLASSES[cid][1])
        self.countdown(cls, st["countdown"], cls.upper())
        self.set(phase="capturing", message="Hold it… vary position and angle a little between samples.")
        ok = 0
        for i in range(1, n + 1):
            self.sleep(st["interval"])
            r = self.hl.learn(cid)
            if r.get("ok"):
                ok += 1
                with self.lock:
                    self.s["samples"][cls] += 1
            self.set(progress=[i, n])
            self.hl.text(f"{cls.upper()} {i}/{n}", 10, 10)
        self.hl.set_name(cid, cls)
        info = self.hl.request("blocks").get("info", {})
        self.set(learned_ids=info.get("learned_ids"),
                 message=f"{cls}: {ok}/{n} samples accepted. Camera reports {info.get('learned_ids')} learned IDs.")
        self.event(f"Trained {cls}: {ok}/{n} accepted")
        if ok < n:
            self.event(f"LEARN {r.get('status')}: this HuskyLens firmware can't learn over serial "
                       "(0.3.7 only knocks/requests). Update to >= 0.5.1, or learn with the camera's button")
        self.cue(CUE_DONE)
        self.sleep(0.8)
        self.history.append({"job": "train", "cls": cls, "id": cid, "requested": n, "accepted": ok,
                             "learned_ids": info.get("learned_ids"), "at": datetime.now().isoformat(timespec="seconds")})

    def read_throw(self, window: float) -> tuple[str, list[int]]:
        votes: list[int] = []
        t_end = time.monotonic() + window
        while time.monotonic() < t_end:
            try:
                blocks = self.hl.blocks()
            except HuskyError:
                continue
            votes.append(blocks[0]["id"] if blocks else -1)
            self.set(live=self.describe(blocks))
        if not votes:
            return "no_reply", votes
        top = Counter(votes).most_common(1)[0][0]
        if top == -1:
            return "none", votes
        return (CLASSES.get(top, (f"id{top}",))[0] if top else "unknown"), votes

    def job_test(self):
        st = self.s["settings"]
        n = int(st["test_n"])
        targets = GESTURES * (n // 3) + random.sample(GESTURES, n % 3)
        random.shuffle(targets)
        t = {"n": n, "idx": 0, "target": None, "results": [], "correct": 0, "accuracy": None,
             "confusion": {g: {} for g in GESTURES}, "players": {}, "done": False}
        self.set(phase="testing", test=t, progress=[0, n], message="Accuracy test: show each gesture when asked.")
        for i, target in enumerate(targets, 1):
            with self.lock:
                t.update(idx=i, target=target)
            self.set(cls=target, progress=[i - 1, n], message=f"Throw {i}/{n}: show {target.upper()}")
            self.countdown(target, st["throw_countdown"], f"{i} {target.upper()}")
            got, votes = self.read_throw(st["window"])
            hit = got == target
            with self.lock:
                player = self.s["player"].strip() or "?"
                t["results"].append({"target": target, "got": got, "hit": hit, "player": player,
                                     "votes": dict(Counter(votes))})
                t["correct"] += hit
                t["accuracy"] = t["correct"] / i
                c = t["confusion"][target]
                b = throw_bucket(got)
                c[b] = c.get(b, 0) + 1
                pl = t["players"].setdefault(player, [0, 0])
                pl[0] += hit
                pl[1] += 1
            self.set(progress=[i, n], count=0)
            self.cue(CUE_HIT) if hit else self.cue(CUE_MISS, 8)
            self.sleep(0.6)
            self.cue()
        with self.lock:
            t["done"] = True
            acc = t["accuracy"]
        verdict = "PASS" if acc >= G2_PASS else "FAIL"
        self.set(message=f"Test done: {t['correct']}/{n} = {acc:.0%} → {verdict} (G2 needs ≥ 85 %)")
        self.event(f"Test {t['correct']}/{n} = {acc:.0%} {verdict}")
        if len(t["players"]) < 2:
            self.event("T7 wants mixed players: set the player name and swap people during the test")
        self.history.append({"job": "test", **{k: t[k] for k in ("n", "correct", "accuracy", "confusion", "players", "results")},
                             "pass": acc >= G2_PASS, "at": datetime.now().isoformat(timespec="seconds")})

    def job_forget(self):
        self.set(phase="busy", message="Forgetting everything Object Classification has learned…")
        r = self.hl.forget()
        with self.lock:
            self.s["samples"] = {k: 0 for k in self.s["samples"]}
            self.s["learned_ids"] = 0 if r.get("ok") else self.s["learned_ids"]
        self.set(message=f"Forget: {r.get('status')}")
        self.event(f"Forget: {r.get('status')}")
        self.history.append({"job": "forget", "status": r.get("status"), "at": datetime.now().isoformat(timespec="seconds")})

    def job_save(self, slot: int):
        self.set(phase="busy", message=f"Saving model to SD slot {slot}…")
        r = self.hl.save_model(slot)
        self.set(message=f"Save to slot {slot}: {r.get('status')}")
        self.event(f"Save slot {slot}: {r.get('status')}")
        self.history.append({"job": "save", "slot": slot, "status": r.get("status")})

    def job_load(self, slot: int):
        self.set(phase="busy", message=f"Loading model from SD slot {slot}…")
        r = self.hl.load_model(slot)
        self.set(message=f"Load slot {slot}: {r.get('status')}")
        self.event(f"Load slot {slot}: {r.get('status')}")
        self.history.append({"job": "load", "slot": slot, "status": r.get("status")})


# --------------------------------------------------------------------------- HTTP
def make_handler(tr: Trainer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, code: int, body: str, ctype: str = "application/json"):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                self.send(200, PAGE, "text/html")
            elif self.path == "/api/state":
                self.send(200, tr.snapshot())
            else:
                self.send(404, '{"error": "not found"}')

        def local(self) -> bool:
            """Only this page may drive the camera: blocks other websites (and DNS rebinding)
            from POSTing here, e.g. to trigger Forget."""
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            origin = self.headers.get("Origin")
            ok_hosts = ("127.0.0.1", "localhost")
            return host in ok_hosts and (origin is None or origin.split("//")[-1].rsplit(":", 1)[0] in ok_hosts)

        def do_POST(self):
            if not self.local():
                return self.send(403, '{"error": "local page only"}')
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self.send(400, '{"error": "bad json"}')
            action = self.path.removeprefix("/api/")
            busy = tr.s["phase"] != "idle" or not tr.jobs.empty()
            if action == "stop":
                tr.cancel.set()
                return self.send(200, '{"ok": true}')
            if action == "settings":
                with tr.lock:
                    for k, v in body.items():
                        if k not in SETTING_RANGES:
                            continue
                        try:
                            lo, hi = SETTING_RANGES[k]
                            tr.s["settings"][k] = min(hi, max(lo, float(v)))
                        except (TypeError, ValueError):
                            return self.send(400, json.dumps({"error": f"{k} must be a number"}))
                return self.send(200, '{"ok": true}')
            if action == "player":
                with tr.lock:
                    tr.s["player"] = str(body.get("name", ""))[:24]
                return self.send(200, '{"ok": true}')
            if tr.s["link"] != "ok":
                return self.send(409, json.dumps({"error": "camera not connected"}))
            if busy:
                return self.send(409, json.dumps({"error": "busy, stop the current job first"}))
            if action == "train" and body.get("cls") in NAME_TO_ID:
                tr.jobs.put(("train", {"cls": body["cls"]}))
            elif action == "test":
                tr.jobs.put(("test", {}))
            elif action == "forget":
                tr.jobs.put(("forget", {}))
            elif action in ("save", "load") and str(body.get("slot", "")).isdigit() and 1 <= int(body["slot"]) <= 5:
                tr.jobs.put((action, {"slot": int(body["slot"])}))
            else:
                return self.send(400, json.dumps({"error": f"bad request: {action}"}))
            self.send(200, '{"ok": true}')

    return Handler


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RPS-Bot · Train</title>
<style>
:root{--bg:#0e0f12;--line:#24262c;--text:#e8e9ec;--dim:#7d828d;  /* same tokens as designs/mcp-live-view/theme.css */

--rock:#f87171;--paper:#4ade80;--scissors:#60a5fa;--nothing:#a1a1aa;--good:#4ade80;--bad:#f87171;--warn:#fbbf24}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,sans-serif}
main{max-width:560px;margin:0 auto;padding:28px 20px 60px}
header{display:flex;align-items:center;justify-content:space-between;color:var(--dim);font-size:13px}
header b{color:var(--text);font-weight:600;font-size:15px}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--warn);margin-right:6px;vertical-align:1px}
.dot.ok{background:var(--good)}.dot.bad{background:var(--bad)}
.stage{padding:44px 0 36px;text-align:center;border-bottom:1px solid var(--line)}
.label{font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:1px;min-height:18px}
.big{font-size:60px;font-weight:700;line-height:1.1;margin:6px 0 4px;text-transform:capitalize}
.big small{font-size:.5em;font-weight:400;color:var(--dim);margin-left:10px}
.msg{color:var(--dim);min-height:22px}.msg.err{color:var(--bad)}
.bar{height:3px;background:var(--line);border-radius:2px;overflow:hidden;margin:18px auto 0;max-width:320px}
.bar i{display:block;height:100%;width:0;background:var(--text);transition:width .15s}
h2{font-size:13px;font-weight:600;margin:32px 0 6px;color:var(--dim)}h2 span{color:var(--text);margin-right:8px}
.row{display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--line)}
.row .sw{width:8px;height:8px;border-radius:50%;background:var(--c);flex:none}
.row .nm{font-weight:600;text-transform:capitalize;width:80px}
.row .hint{color:var(--dim);font-size:13px;flex:1}
.row .n{font-size:13px;color:var(--dim);min-width:74px;text-align:right}.row .n.on{color:var(--text)}
button{font:inherit;font-size:14px;color:var(--text);background:transparent;border:1px solid var(--line);border-radius:8px;padding:6px 14px;cursor:pointer}
button:hover:not(:disabled){border-color:var(--dim)}button:disabled{opacity:.35;cursor:default}
button.primary{background:var(--text);color:var(--bg);border-color:var(--text);font-weight:600}
button.stop{margin-top:20px;border-color:var(--bad);color:var(--bad)}
.test{display:flex;align-items:center;gap:16px;padding:10px 0}
.acc{font-size:22px;font-weight:700}.acc small{font-size:13px;font-weight:600;margin-left:6px}
.throws{display:flex;flex-wrap:wrap;gap:3px}.throws i{width:10px;height:10px;border-radius:2px;background:var(--line)}
table{border-collapse:collapse;font-size:13px;margin-top:8px;width:100%}
td,th{padding:5px 8px;text-align:center;border-bottom:1px solid var(--line)}th{color:var(--dim);font-weight:500}
td:first-child,th:first-child{text-align:left}td:first-child{text-transform:capitalize}.diag{color:var(--good);font-weight:700}
.note{font-size:12px;color:var(--dim);margin:6px 0 0}
details{margin-top:36px;border-top:1px solid var(--line);padding-top:14px}
summary{cursor:pointer;color:var(--dim);font-size:13px}summary:hover{color:var(--text)}
.grid{display:grid;grid-template-columns:1fr auto;gap:8px 16px;align-items:center;margin-top:14px;font-size:13px;color:var(--dim)}
input,select{font:inherit;font-size:13px;color:var(--text);background:transparent;border:1px solid var(--line);border-radius:6px;padding:4px 8px;width:80px}
.btns{display:flex;gap:8px;flex-wrap:wrap}
.meta{display:flex;justify-content:space-between;color:var(--dim);font-size:12px;margin-top:4px;min-height:18px}
.meta .warn{color:var(--warn)}
.players{font-size:13px;color:var(--dim);margin-top:6px}.players b{color:var(--text);font-weight:600}
input.wide{width:150px}
.test{flex-wrap:wrap}
@media(max-width:460px){.row .hint{display:none}.row .nm{flex:1}.big{font-size:44px}}
.events{font:12px/1.6 ui-monospace,Menlo,monospace;color:var(--dim);white-space:pre-wrap;margin:14px 0 0;max-height:180px;overflow:auto}
</style></head><body><main>
<header><b>RPS-Bot <span style="color:var(--dim);font-weight:500">· Train</span></b><span><i class="dot" id="dot"></i><span id="link">connecting…</span></span></header>
<div class="meta"><span id="learned"></span><span id="rate" title="Camera reads per second while idle. Rigged-win needs a fast read (D10, R7)."></span></div>

<section class="stage">
  <div class="label" id="label"></div>
  <div class="big" id="big">—</div>
  <div class="msg" id="msg"></div>
  <div class="bar" id="barwrap" hidden><i id="bar"></i></div>
  <button class="stop" id="stop" hidden onclick="post('stop')">Stop</button>
</section>

<h2><span>1</span>Train each class</h2>
<div id="classes"></div>
<p class="note">Hold the pose in the play zone when the countdown ends. Aim for 10–20 samples per class from several hands (D9); training again adds samples. Watch the HuskyLens screen to check framing.</p>

<h2><span>2</span>Test accuracy</h2>
<div class="test">
  <input class="wide" id="player" placeholder="Who's throwing?" maxlength="24" oninput="post('player',{name:this.value})" title="Tags each throw. Change it mid-test when the next person steps in (T7: mixed players).">
  <button class="primary act" id="testbtn" onclick="post('test')">Start test</button>
  <span class="acc" id="acc"></span><div class="throws" id="throws"></div>
</div>
<table id="conf" hidden></table>
<div class="players" id="players"></div>
<p class="note" id="testnote">Random rock / paper / scissors prompts. Use several people: type who's throwing and change it as people swap (T7). Pass = 85 % or better (G2).</p>

<details>
  <summary>Model, settings &amp; log</summary>
  <div class="grid">
    <span>Model slot on SD card</span>
    <span class="btns"><select id="slot"><option>1</option><option>2</option><option>3</option><option>4</option><option>5</option></select>
      <button class="act" onclick="post('save',{slot:+slot.value})">Save</button>
      <button class="act" onclick="post('load',{slot:+slot.value})">Load</button></span>
    <span>Erase everything learned</span>
    <span><button class="act" onclick="confirm('Erase everything the camera has learned (Object Classification)?')&&post('forget')">Forget all</button></span>
  </div>
  <div class="grid" id="settings"></div>
  <pre class="events" id="events"></pre>
</details>
</main>
<script>
const C={rock:'var(--rock)',paper:'var(--paper)',scissors:'var(--scissors)',nothing:'var(--nothing)'};
const HOW={rock:'Closed fist',paper:'Flat open hand',scissors:'Index + middle finger',nothing:'Empty play zone, no hand'};
const SET=[['samples','Samples per class (D9: 10–20)',1,1,50],['interval','Seconds between samples',.1,.2,5],['countdown','Countdown before training (s)',1,1,10],
  ['throw_countdown','Countdown per test throw (s)',1,1,10],['window','Majority-vote window per throw (s, §5: 0.3)',.1,.1,2],['test_n','Throws per test (T7: 30)',3,3,60]];
const $=id=>document.getElementById(id);
let note=null,noteUntil=0;
classes.innerHTML=Object.keys(C).map(k=>`<div class="row" style="--c:${C[k]}"><i class="sw"></i><span class="nm">${k}</span>
  <span class="hint">${HOW[k]}</span><span class="n" id="n-${k}"></span><button class="act" onclick="post('train',{cls:'${k}'})">Train</button></div>`).join('');
settings.innerHTML=SET.map(([k,l,s,lo,hi])=>`<span>${l}</span><input type="number" step="${s}" min="${lo}" max="${hi}" id="s-${k}" onchange="post('settings',{${k}:this.value})">`).join('');
async function post(a,b={}){const r=await fetch('/api/'+a,{method:'POST',body:JSON.stringify(b)});
  if(!r.ok){note=(await r.json()).error;noteUntil=Date.now()+4000}}
function render(s){
  const ok=s.link==='ok',busy=s.phase!=='idle';
  dot.className='dot'+(ok?' ok':s.link==='connecting'?'':' bad');
  link.textContent=s.mode==='simulated'?'simulated':ok?'connected':s.link==='connecting'?'connecting…':s.link==='no_camera'?'no camera':'not found';
  link.title=s.link_detail||'';
  document.querySelectorAll('.act').forEach(b=>b.disabled=busy||!ok);
  $('stop').hidden=!busy;
  const L=s.live,cls=s.cls,t=s.test;
  let label='',big='—',color='',sub='';
  if(!ok){label='Not connected';big='—'}
  else if(!busy){label='Camera sees';big=L?L.name:'—';color=L&&C[L.name]||(L&&L.name==='no hand'?'var(--dim)':'')}
  else if(s.phase==='busy'){label='Working';big='…'}
  else{
    label=s.phase==='testing'?`Throw ${t.idx} of ${t.n} · show`:s.phase==='capturing'?'Hold it':'Get ready · show';
    big=cls||'';color=C[cls]||'';
    sub=s.count?s.count:s.phase==='capturing'?`${s.progress[0]}/${s.progress[1]}`:'';
  }
  $('label').textContent=label;
  $('big').innerHTML=big+(sub!==''?`<small>${sub}</small>`:'');$('big').style.color=color;
  const err=note&&Date.now()<noteUntil;
  msg.textContent=err?note:(s.message||'');msg.className='msg'+(err?' err':'');
  barwrap.hidden=!(busy&&s.progress[1]);bar.style.width=s.progress[1]?(100*s.progress[0]/s.progress[1])+'%':'0';
  learned.textContent=ok&&s.learned_ids!=null?`Camera has ${s.learned_ids} learned ID${s.learned_ids==1?'':'s'} (want 4)`:'';
  rate.textContent=ok&&s.rate!=null&&!busy?`${s.rate} reads/s`:'';
  if(document.activeElement!==$('player'))$('player').value=s.player||'';
  for(const k in s.samples){const e=$('n-'+k);e.textContent=s.samples[k]?s.samples[k]+(s.samples[k]==1?' sample':' samples'):'not trained';e.className='n'+(s.samples[k]?' on':'')}
  players.innerHTML=t&&t.players&&Object.keys(t.players).length?'By player: '+Object.entries(t.players).map(([p,[h,n]])=>`<b>${p}</b> ${h}/${n}`).join(' · ')+
    (t.done&&Object.keys(t.players).length<2?' <span style="color:var(--warn)">· T7 wants mixed players</span>':''):'';
  if(t&&t.accuracy!=null){const pass=t.accuracy>=.85;
    acc.innerHTML=`${Math.round(t.accuracy*100)}%`+(t.done?`<small style="color:var(--${pass?'good':'bad'})">${pass?'PASS':'FAIL'}</small>`:`<small style="color:var(--dim)">${t.correct}/${t.results.length}</small>`)}
  else acc.textContent='';
  throws.innerHTML=t?Array.from({length:t.n},(_,i)=>{const r=t.results[i];return `<i style="background:${r?(r.hit?'var(--good)':'var(--bad)'):''}" title="${r?r.target+' → '+r.got:''}"></i>`}).join(''):'';
  conf.hidden=!(t&&t.results.length);testnote.hidden=!!(t&&t.results.length);
  if(t&&t.results.length){const cols=['rock','paper','scissors','nothing','unknown'];
    conf.innerHTML='<tr><th>shown → read as</th>'+cols.map(c=>`<th>${c}</th>`).join('')+'</tr>'+
    ['rock','paper','scissors'].map(r=>{const row=t.confusion[r]||{};
      return `<tr><td style="color:${C[r]}">${r}</td>`+cols.map(c=>`<td class="${c===r&&row[c]?'diag':''}">${row[c]||'·'}</td>`).join('')+'</tr>'}).join('')}
  for(const [k] of SET){const el=$('s-'+k);if(document.activeElement!==el)el.value=s.settings[k]}
  events.textContent=s.events.join('\n');
}
async function loop(){try{render(await (await fetch('/api/state')).json())}catch(e){link.textContent='UI server stopped';dot.className='dot bad'}setTimeout(loop,150)}
loop();
</script></body></html>"""


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fake", action="store_true", help="simulated camera, no hardware")
    p.add_argument("--link", choices=("auto", "usb", "bridge"), default="auto",
                   help="XIAO bridge, camera micro-USB, or auto (XIAO first)")
    p.add_argument("--bridge", metavar="PORT", help="XIAO data port (implies --link bridge)")
    p.add_argument("--baud", type=int, help="HuskyLens UART baud via the XIAO (default 9600)")
    p.add_argument("--port", type=int, default=8765, help="HTTP port")
    p.add_argument("--open", action="store_true", help="open the page in the browser")
    args = p.parse_args()

    tr = Trainer(args.fake, args.bridge, args.baud, "bridge" if args.bridge else args.link)
    tr.start()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(tr))
    url = f"http://127.0.0.1:{args.port}"
    print(f"Gesture training UI on {url}  ({'simulated' if args.fake else 'hardware'}; Ctrl-C to quit)")
    if args.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        tr.cancel.set()
        tr.cue()
        if tr.history:
            tr.save_log()
            print(f"Log: {tr.log_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
