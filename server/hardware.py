"""Hand, LEDs, fill light and voice through the XIAO (design doc §8.2, §9.2, §9.5).

Fingers are normalised (D11): 0 = closed, 1 = open, in channel order thumb (CH0), index (CH1),
middle (CH2), ring+little (CH3). Each value maps through ``calibration.json`` to a pulse width that
never leaves the calibrated open/closed endpoints (NFR-05). A watchdog relaxes the hand if any finger
has been held closed for more than ``HOLD_LIMIT_S`` (NFR-09).

``link`` is anything with ``call(cmd, **args) -> dict`` speaking §9.2: ``bridge.Bridge`` on the rig,
``bridge.FakeBridge`` without hardware. ``lock`` is shared with the camera, because both talk over the
same serial port.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

FINGERS = ["thumb", "index", "middle", "ring_little"]
HOLD_LIMIT_S = 5.0          # NFR-09
CLOSED_BELOW = 0.5          # a finger value below this counts as "closed" for the hold limit
RAW_US_RANGE = (600, 2400)  # what calibrate_finger may drive a channel to while finding endpoints
DEFAULT_CAL = {"open_us": 1000, "closed_us": 2000, "calibrated": False}
FIRMWARE_LED_COUNT = 16


class HardwareError(RuntimeError):
    pass


def check_fingers(fingers) -> list[float]:
    if not isinstance(fingers, (list, tuple)) or len(fingers) != len(FINGERS):
        raise HardwareError(f"fingers must be {len(FINGERS)} values ({', '.join(FINGERS)}), 0 = closed .. 1 = open")
    out = []
    for v in fingers:
        v = float(v)
        if not 0.0 <= v <= 1.0:
            raise HardwareError(f"finger values must be in [0, 1], got {v} (NFR-05)")
        out.append(v)
    return out


def check_colour(c) -> str:
    if isinstance(c, (list, tuple)) and len(c) == 3:
        return "#" + "".join(f"{max(0, min(255, int(v))):02x}" for v in c)
    s = str(c).strip().lstrip("#")
    if len(s) != 6:
        raise HardwareError(f"colour must be '#rrggbb' or [r, g, b], got {c!r}")
    try:
        int(s, 16)
    except ValueError as e:
        raise HardwareError(f"colour must be '#rrggbb' or [r, g, b], got {c!r}") from e
    return "#" + s.lower()


class Hardware:
    def __init__(self, link_provider: Callable[[], object], lock: threading.RLock, data_dir: Path,
                 tts: bool = True, simulated: bool = False):
        self._link_provider = link_provider
        self.simulated = simulated  # software gameplay: no physical hand/LEDs; outputs go to a FakeBridge
        self.lock = lock
        self.cal_path = data_dir / "calibration.json"
        self.cal = self._load_cal()
        self.tts = tts
        self.fingers: list[float] | None = None      # last commanded pose, None = relaxed
        self.closed_since: list[float | None] = [None] * len(FINGERS)
        self.led = "#000000"
        self.relay_on = False
        self._stop = threading.Event()
        self._watchdog = threading.Thread(target=self._watch, daemon=True, name="hold-watchdog")
        self._watchdog.start()

    # ------------------------------------------------------------------ plumbing
    def link(self):
        link = self._link_provider()
        if not hasattr(link, "call"):
            raise HardwareError("the hand needs the XIAO bridge; the camera is connected over its own USB. "
                                "Plug in the XIAO and reconnect (husky_connect link='bridge')")
        return link

    def call(self, cmd: str, **args) -> dict:
        with self.lock:
            r = self.link().call(cmd, **args)
        if not r.get("ok"):
            raise HardwareError(f"XIAO {cmd}: {r.get('error', r)}")
        return r

    def close(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ calibration (§9.5)
    def _load_cal(self) -> list[dict]:
        try:
            data = json.loads(self.cal_path.read_text())
            return [{**DEFAULT_CAL, **data[i]} for i in range(len(FINGERS))]
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            return [dict(DEFAULT_CAL) for _ in FINGERS]

    def _save_cal(self) -> None:
        self.cal_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cal_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cal, indent=2))
        tmp.replace(self.cal_path)

    def finger_index(self, finger) -> int:
        if isinstance(finger, int) or str(finger).isdigit():
            i = int(finger)
        else:
            name = str(finger).lower().replace("+", "_").replace(" ", "_")
            name = {"ring": "ring_little", "little": "ring_little", "ring_little": "ring_little"}.get(name, name)
            if name not in FINGERS:
                raise HardwareError(f"finger must be 0-3 or one of {FINGERS}")
            i = FINGERS.index(name)
        if not 0 <= i < len(FINGERS):
            raise HardwareError("finger must be 0-3 (CH0 thumb .. CH3 ring+little)")
        return i

    def calibrate(self, finger, open_us: int | None = None, closed_us: int | None = None,
                  test_us: int | None = None) -> dict:
        """Set a finger's endpoints, or drive it to a raw pulse width while finding them."""
        i = self.finger_index(finger)
        lo, hi = RAW_US_RANGE
        for name, v in (("open_us", open_us), ("closed_us", closed_us), ("test_us", test_us)):
            if v is not None and not lo <= int(v) <= hi:
                raise HardwareError(f"{name} must be {lo}..{hi} µs")
        if test_us is not None:
            self.call("servo", ch=i, us=int(test_us))
            self.fingers = None  # raw position: no longer a known normalised pose
        if open_us is not None:
            self.cal[i]["open_us"] = int(open_us)
        if closed_us is not None:
            self.cal[i]["closed_us"] = int(closed_us)
        if open_us is not None or closed_us is not None:
            if self.cal[i]["open_us"] == self.cal[i]["closed_us"]:
                raise HardwareError("open_us and closed_us must differ")
            self.cal[i]["calibrated"] = True
            self._save_cal()
        return {"finger": FINGERS[i], "channel": i, **self.cal[i], "test_us": test_us}

    def to_us(self, fingers: list[float]) -> list[int]:
        out = []
        for i, v in enumerate(fingers):
            c = self.cal[i]
            out.append(int(round(c["closed_us"] + (c["open_us"] - c["closed_us"]) * v)))
        return out

    # ------------------------------------------------------------------ hand
    def pose(self, fingers, ms: int = 150) -> dict:
        f = check_fingers(fingers)
        ms = max(0, min(int(ms), 2000))
        us = self.to_us(f)
        self.call("pose", us=us, ms=ms)
        now = time.monotonic()
        for i, v in enumerate(f):
            if v < CLOSED_BELOW:
                if self.closed_since[i] is None:
                    self.closed_since[i] = now
            else:
                self.closed_since[i] = None
        self.fingers = f
        return {"fingers": f, "us": us, "ms": ms}

    def servo(self, finger, value: float) -> dict:
        """Move one finger (0 closed .. 1 open), leaving the others where they are."""
        i = self.finger_index(finger)
        base = list(self.fingers) if self.fingers else [1.0] * len(FINGERS)
        base[i] = value
        return self.pose(base, ms=150)

    def relax(self) -> dict:
        self.call("relax")
        self.fingers = None
        self.closed_since = [None] * len(FINGERS)
        return {"relaxed": True}

    def held_closed_s(self) -> float:
        now = time.monotonic()
        return max((now - t for t in self.closed_since if t is not None), default=0.0)

    def _watch(self) -> None:
        while not self._stop.wait(0.25):
            if self.held_closed_s() > HOLD_LIMIT_S:
                try:
                    self.relax()
                except Exception:
                    self.closed_since = [None] * len(FINGERS)  # link gone: nothing to hold

    # ------------------------------------------------------------------ LEDs, light, voice
    def led_fill(self, colour, brightness: float | None = None) -> dict:
        c = check_colour(colour)
        args = {"fill": c}
        if brightness is not None:
            args["brightness"] = max(0.0, min(1.0, float(brightness)))
        self.call("led", **args)
        self.led = c
        return {"led": c, **({"brightness": args["brightness"]} if "brightness" in args else {})}

    def led_off(self) -> dict:
        return self.led_fill("#000000")

    def led_countdown(self, beats: int, period_ms: int, colours: list | None = None) -> dict:
        cols = [check_colour(c) for c in (colours or ["#ffffff"])]
        r = self.call("countdown", beats=int(beats), period_ms=int(period_ms), colours=cols)
        self.led = cols[(int(beats) - 1) % len(cols)]
        return {"beats": r.get("beats"), "period_ms": r.get("period_ms"), "total_ms": r.get("total_ms")}

    def fill_light(self, on: bool) -> dict:
        self.call("relay", on=bool(on))
        self.relay_on = bool(on)
        return {"fill_light": self.relay_on}

    def voice_play(self, ms: int) -> dict:
        return self.call("voice", play_ms=int(ms))

    def voice_record(self, ms: int) -> dict:
        return self.call("voice", rec_ms=int(ms))

    # ------------------------------------------------------------------ laptop speech (§9.3 speak)
    def speak(self, text: str) -> dict:
        """Say a line on the laptop speaker without blocking. macOS `say`, Windows SAPI, Linux espeak."""
        text = " ".join(str(text).split())[:400]
        if not text:
            return {"spoken": False, "reason": "empty"}
        if not self.tts:
            return {"spoken": False, "reason": "speech off", "text": text}
        system = platform.system()
        if system == "Darwin" and shutil.which("say"):
            cmd = ["say", text]
        elif system == "Windows":
            safe = text.replace("'", "''")
            cmd = ["powershell", "-NoProfile", "-Command",
                   f"Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{safe}')"]
        elif shutil.which("espeak"):
            cmd = ["espeak", text]
        else:
            return {"spoken": False, "reason": "no speech engine found", "text": text}
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"spoken": True, "text": text}

    def status(self) -> dict:
        return {"hand": "simulated" if self.simulated else "real", "fingers": self.fingers, "relaxed": self.fingers is None, "held_closed_s": round(self.held_closed_s(), 2),
                "led": self.led, "fill_light": self.relay_on,
                "calibration": [{"finger": FINGERS[i], **c} for i, c in enumerate(self.cal)]}
