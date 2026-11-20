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
        self.ser = serial.Serial(port, baud, timeout=0.02, exclusive=True)
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


# --- HuskyLens's own micro-USB port (CP210x) --------------------------------------
#
# Ported from DFRobot's HUSKYLENSPython (huskylib.py, "USB connection"): with firmware
# >= 0.5.1 the camera answers the same protocol on its micro-USB port, so training needs
# no XIAO and no wiring, just the data cable that's also used for flashing.
#
# What we take from huskylib's connect sequence:
#   - DTR and RTS held low *before* the port opens. They drive the K210's reset/boot
#     pins (that's how kflash enters the bootloader), so a normal open resets the camera.
#   - ~2 s for the camera to finish booting, then a few knocks to wake the link.
#   - 3 Mbaud by default.
# What we don't take: its value decoding (``low + 255 + high`` mangles anything > 255) and
# its bare ``except`` that turns errors into empty lists. huskylens.py does both properly.

SILABS_VID = 0x10C4  # CP210x on the HuskyLens mainboard (also what flash_huskylens.py targets)
USB_BAUDS = (3_000_000, 115_200, 9_600)  # huskylib default first, then the camera's UART speeds
KNOCK_FRAME = bytes.fromhex("55aa11002c3c")


def find_husky_usb_port() -> str | None:
    """The HuskyLens's micro-USB port, found by USB vendor ID (never the XIAO)."""
    ports = sorted((p for p in list_ports.comports() if p.vid == SILABS_VID), key=lambda p: p.device)
    # macOS lists each device as /dev/cu.* and /dev/tty.*; prefer cu (no carrier-detect wait)
    for p in ports:
        if "/tty." not in p.device:
            return p.device
    return ports[0].device if ports else None


class UsbLink(SerialLink):
    """Direct link over the HuskyLens's micro-USB (no XIAO). Same ``husky()`` as SerialLink."""

    def __init__(self, port: str, baud: int | None = None, settle_s: float = 2.0):
        ser = serial.Serial(baudrate=9600, timeout=0.02, exclusive=True)  # standard speed to open; probe() sets the real one
        ser.dtr = False  # set before open() so the camera isn't reset (see above)
        ser.rts = False
        ser.port = port
        ser.open()
        self.ser = ser
        time.sleep(settle_s)
        self.ser.reset_input_buffer()
        self.baud = self.probe([baud] if baud else list(USB_BAUDS))

    def probe(self, bauds: list[int]) -> int | None:
        """Knock at each baud until one answers; leaves the port at that baud. None = no answer."""
        for b in bauds:
            try:
                self.ser.baudrate = b  # re-configures in place; DTR/RTS stay low
            except (OSError, ValueError, serial.SerialException):
                continue  # this driver can't do that speed (e.g. 3 Mbaud); try the next
            for _ in range(3):
                frames = split_frames(b"".join(self.husky(KNOCK_FRAME, timeout_ms=300))).frames
                if any(f.cmd == 0x2E for f in frames):
                    return b
        return None
