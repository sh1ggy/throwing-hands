"""JSON-lines client for the XIAO bridge (design doc D1-A, D2-A, §9.2).

``Bridge`` talks newline-delimited JSON to the XIAO's data serial port and also
implements the ``Transport`` protocol from ``huskylens.py``
(``husky(frame, expect=None, timeout_ms=None) -> list[bytes]``), so
``HuskyLens(Bridge(port))`` works the same as ``HuskyLens(SerialLink(port))``.

CircuitPython exposes two USB serial ports: the console/REPL and the data channel.
On macOS both are named "Seeeduino XIAO RP2040"; ``find_bridge_port()`` pings each
to find the data one.

``FakeBridge`` simulates the XIAO for working without hardware (NFR-08): the same §9.2 commands
with the firmware's argument checks (``firmware/circuitpython/code.py``), and HuskyLens frames
forwarded to ``connect.FakeCamera``. It records the last pose, LEDs, relay and voice so tests can check them.
"""

from __future__ import annotations

import itertools
import json
import time

try:
    import serial  # pyserial
    from serial.tools import list_ports
except ImportError as e:  # pragma: no cover
    raise SystemExit("pyserial is missing: run  pip install --user pyserial") from e

DEFAULT_TIMEOUT_MS = 500
MARGIN_S = 0.5  # USB round trip + firmware overhead on top of the HuskyLens timeout


class BridgeError(RuntimeError):
    pass


class Bridge:
    def __init__(self, port: str, baud: int = 115200):
        # exclusive: a second program gets "port busy" instead of interleaved replies
        self.ser = serial.Serial(port, baud, timeout=0.05, exclusive=True)
        self._ids = itertools.count(1)
        time.sleep(0.1)
        self.ser.reset_input_buffer()

    def close(self) -> None:
        self.ser.close()

    def __enter__(self) -> "Bridge":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def call(self, cmd: str, timeout_s: float = 1.0, **args) -> dict:
        rid = next(self._ids)
        self.ser.write((json.dumps({"id": rid, "cmd": cmd, **args}) + "\n").encode())
        self.ser.flush()
        deadline = time.monotonic() + timeout_s
        buf = b""
        while time.monotonic() < deadline:
            buf += self.ser.read(256)
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    reply = json.loads(line)
                except ValueError:
                    continue  # stray console text
                if reply.get("id") == rid:
                    return reply
        raise BridgeError(f"no reply to {cmd!r} within {timeout_s:.1f} s")

    def ping(self) -> dict:
        return self.call("ping")

    def set_husky_baud(self, baud: int) -> dict:
        return self.call("uart", baud=baud)

    # --- huskylens.Transport ---------------------------------------------------
    def husky(self, frame: bytes, expect: int | None = None, timeout_ms: int | None = None) -> list[bytes]:
        timeout_ms = timeout_ms or DEFAULT_TIMEOUT_MS
        args = {"hex": frame.hex(), "timeout_ms": timeout_ms}
        if expect is not None:
            args["expect"] = expect
        reply = self.call("husky", timeout_s=timeout_ms / 1000 + MARGIN_S, **args)
        return [bytes.fromhex(h) for h in reply.get("frames", [])]


class FakeBridge:
    """Simulated XIAO bridge (cpy-bridge 0.5 behaviour) plus a simulated camera behind it."""

    US_MIN, US_MAX = 500, 2500
    NUM_FINGERS, NUM_CHANNELS = 4, 5
    LED_COUNT = 16
    COUNTDOWN_MAX_BEATS = 32
    VOICE_MAX_MS = 30000

    def __init__(self, camera=None):
        if camera is None:
            from connect import FakeCamera
            camera = FakeCamera()
        self.camera = camera
        self.version = "fake-bridge 0.5"
        self.t0 = time.monotonic()
        self.us: list[int | None] = [None] * self.NUM_CHANNELS
        self.leds: list[str] = ["#000000"] * self.LED_COUNT
        self.relay = False
        self.voice: dict | None = None
        self.calls: list[dict] = []  # every command, for tests

    def close(self) -> None:
        pass

    def __enter__(self) -> "FakeBridge":
        return self

    def __exit__(self, *exc) -> None:
        pass

    @staticmethod
    def _colour(c) -> str:
        if isinstance(c, str):
            c = c.lstrip("#")
            if len(c) != 6:
                raise ValueError("colour must be [r,g,b] or '#rrggbb'")
            int(c, 16)
            return "#" + c.lower()
        if len(c) != 3:
            raise ValueError("colour must be [r,g,b] or '#rrggbb'")
        return "#" + "".join(f"{max(0, min(255, int(v))):02x}" for v in c)

    def _clamp(self, us) -> int:
        return max(self.US_MIN, min(self.US_MAX, int(us)))

    def call(self, cmd: str, timeout_s: float = 1.0, **args) -> dict:
        self.calls.append({"cmd": cmd, **args})
        try:
            return {"id": len(self.calls), **self._handle(cmd, args)}
        except KeyError as e:
            return {"id": len(self.calls), "ok": False, "error": f"missing argument: {e}"}
        except (ValueError, TypeError) as e:
            return {"id": len(self.calls), "ok": False, "error": str(e)}

    def _handle(self, cmd: str, a: dict) -> dict:
        if cmd == "ping":
            return {"ok": True, "version": self.version, "uptime_s": round(time.monotonic() - self.t0, 1)}
        if cmd == "uart":
            return {"ok": True, "baud": int(a["baud"])}
        if cmd == "husky":
            frames = self.camera.husky(bytes.fromhex(a["hex"]), a.get("expect"), a.get("timeout_ms"))
            return {"ok": True, "frames": [f.hex() for f in frames]}
        if cmd == "servo":
            ch = int(a["ch"])
            if not 0 <= ch < self.NUM_CHANNELS:
                raise ValueError(f"ch must be 0..{self.NUM_CHANNELS - 1}")
            self.us[ch] = self._clamp(a["us"])
            return {"ok": True, "ch": ch, "us": self.us[ch]}
        if cmd == "pose":
            targets = a["us"]
            if len(targets) != self.NUM_FINGERS:
                raise ValueError(f"us must have {self.NUM_FINGERS} values")
            targets = [self._clamp(u) for u in targets]
            self.us[:self.NUM_FINGERS] = targets
            return {"ok": True, "us": targets, "ms": max(0, int(a.get("ms") or 0))}
        if cmd == "relax":
            self.us = [None] * self.NUM_CHANNELS
            return {"ok": True}
        if cmd == "led":
            if a.get("rgb") is not None:
                if len(a["rgb"]) > self.LED_COUNT:
                    raise ValueError(f"rgb has {len(a['rgb'])} pixels, strip has {self.LED_COUNT}")
                cols = [self._colour(c) for c in a["rgb"]]
                self.leds = cols + ["#000000"] * (self.LED_COUNT - len(cols))
            elif a.get("fill") is not None:
                self.leds = [self._colour(a["fill"])] * self.LED_COUNT
            elif a.get("brightness") is None:
                raise ValueError("led needs rgb, fill or brightness")
            return {"ok": True, "count": self.LED_COUNT, "brightness": a.get("brightness", 0.3)}
        if cmd == "countdown":
            beats, period = int(a["beats"]), int(a["period_ms"])
            if not 1 <= beats <= self.COUNTDOWN_MAX_BEATS or period < 20:
                raise ValueError(f"beats must be 1..{self.COUNTDOWN_MAX_BEATS} and period_ms >= 20")
            cols = [self._colour(c) for c in (a.get("colours") or ["#ffffff"])]
            if not cols:
                raise ValueError("colours is empty")
            self.leds = [cols[(beats - 1) % len(cols)]] * self.LED_COUNT
            return {"ok": True, "beats": beats, "period_ms": period, "total_ms": beats * period}
        if cmd == "relay":
            self.relay = bool(a["on"])
            return {"ok": True, "on": self.relay}
        if cmd == "voice":
            play, rec = a.get("play_ms"), a.get("rec_ms")
            if play and rec:
                raise ValueError("give play_ms or rec_ms, not both")
            ms = int(play or rec or 0)
            if not 0 <= ms <= self.VOICE_MAX_MS:
                raise ValueError(f"ms must be 0..{self.VOICE_MAX_MS}")
            if ms == 0:
                self.voice = None
                return {"ok": True, "ms": 0}
            self.voice = {"mode": "play" if play else "rec", "ms": ms}
            return {"ok": True, **self.voice}
        if cmd == "status_led":
            return {"ok": True, "rgb": a.get("rgb"), "blink_hz": a.get("blink_hz", 0)}
        return {"ok": False, "error": f"not implemented: {cmd}"}

    def ping(self) -> dict:
        return self.call("ping")

    def set_husky_baud(self, baud: int) -> dict:
        return self.call("uart", baud=baud)

    # --- huskylens.Transport ---------------------------------------------------
    def husky(self, frame: bytes, expect: int | None = None, timeout_ms: int | None = None) -> list[bytes]:
        reply = self.call("husky", hex=frame.hex(), expect=expect, timeout_ms=timeout_ms)
        return [bytes.fromhex(h) for h in reply.get("frames", [])]


def find_bridge_port() -> str | None:
    """Return the first USB serial port that answers a bridge ping."""
    for p in sorted(list_ports.comports(), key=lambda p: p.device, reverse=True):
        if "usb" not in p.device.lower() and not p.device.upper().startswith("COM"):
            continue
        try:
            with Bridge(p.device) as b:
                if b.call("ping", timeout_s=0.6).get("ok"):
                    return p.device
        except (serial.SerialException, BridgeError, OSError):
            continue
    return None
