"""JSON-lines client for the XIAO bridge (design doc D1-A, D2-A, §9.2).

``Bridge`` talks newline-delimited JSON to the XIAO's data serial port and also
implements the ``Transport`` protocol from ``huskylens.py``
(``husky(frame, expect=None, timeout_ms=None) -> list[bytes]``), so
``HuskyLens(Bridge(port))`` works the same as ``HuskyLens(SerialLink(port))``.

CircuitPython exposes two USB serial ports: the console/REPL and the data channel.
On macOS both are named "Seeeduino XIAO RP2040"; ``find_bridge_port()`` pings each
to find the data one.

TODO (§9.1): FakeBridge simulator for NFR-08.
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
        self.ser = serial.Serial(port, baud, timeout=0.05)
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
