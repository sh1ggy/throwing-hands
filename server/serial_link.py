"""Direct USB-TTL link to the HuskyLens (design doc D1 option B).

Implements the ``Transport`` protocol from ``huskylens.py``:
``husky(frame, expect=None, timeout_ms=None) -> list[bytes]``.

Wiring: adapter TX -> HuskyLens R, adapter RX -> HuskyLens T, GND -> GND.
Power the HuskyLens from the adapter's 5 V *or* its own micro-USB, never both
(§8.3 power rule 2: one source per net). HuskyLens setting: Protocol Type = Serial,
9600 baud to start (§8.2).

Needs pyserial: ``pip install --user pyserial``.
"""

from __future__ import annotations

import time

try:
    import serial  # pyserial
    from serial.tools import list_ports
except ImportError as e:  # pragma: no cover
    raise SystemExit("pyserial is missing: run  pip install --user pyserial") from e

from huskylens import RETURN_INFO, split_frames

DEFAULT_TIMEOUT_MS = 500


def available_ports() -> list[str]:
    return [f"{p.device}  ({p.description})" for p in list_ports.comports()]


class SerialLink:
    def __init__(self, port: str, baud: int = 9600):
        self.ser = serial.Serial(port, baud, timeout=0.02)
        time.sleep(0.1)
        self.ser.reset_input_buffer()

    def close(self) -> None:
        self.ser.close()

    def __enter__(self) -> "SerialLink":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def husky(self, frame: bytes, expect: int | None = None, timeout_ms: int | None = None) -> list[bytes]:
        """Send one frame and return the complete reply frames (raw bytes each).

        Stops when ``expect`` frames have arrived, or, if ``expect`` is None, after one
        non-INFO frame, or after INFO plus the object count it announces.
        """
        deadline = time.monotonic() + (timeout_ms or DEFAULT_TIMEOUT_MS) / 1000
        self.ser.reset_input_buffer()  # drop stale bytes from earlier replies
        self.ser.write(frame)
        self.ser.flush()

        buf = b""
        frames = []
        while time.monotonic() < deadline:
            chunk = self.ser.read(64)
            if not chunk:
                if self._done(frames, expect):
                    break
                continue
            buf += chunk
            res = split_frames(buf)
            buf = res.rest
            frames.extend(res.frames)
            if self._done(frames, expect):
                break
        return [f.raw() for f in frames]

    @staticmethod
    def _done(frames, expect: int | None) -> bool:
        if not frames:
            return False
        if expect is not None:
            return len(frames) >= expect
        first = frames[0]
        if first.cmd == RETURN_INFO:
            count = (first.words() + [0])[0]
            return len(frames) >= 1 + count
        return True
