"""HUSKYLENS protocol v0.5.1: frame build/parse and every command (design doc §9.6).

Frame: 55 AA 11 <len> <cmd> <data...> <checksum>
  - checksum = low byte of the sum of all preceding bytes
  - data values are 16-bit little-endian
  - replies use the same header

This module is transport-agnostic. ``HuskyLens`` talks to anything with a
``husky(frame: bytes, expect: int | None, timeout_ms: int | None) -> list[bytes]``
method (the XIAO bridge, FakeBridge or a direct USB-TTL link).
"""

from __future__ import annotations

import os
import struct
import time
from dataclasses import dataclass, field
from typing import Iterable, Protocol

HEADER = b"\x55\xaa\x11"

# --- Commands sent to the HuskyLens -------------------------------------------------
REQUEST = 0x20
REQUEST_BLOCKS = 0x21
REQUEST_ARROWS = 0x22
REQUEST_LEARNED = 0x23
REQUEST_BLOCKS_LEARNED = 0x24
REQUEST_ARROWS_LEARNED = 0x25
REQUEST_BY_ID = 0x26
REQUEST_BLOCKS_BY_ID = 0x27
REQUEST_ARROWS_BY_ID = 0x28
KNOCK = 0x2C
ALGORITHM = 0x2D
CUSTOM_NAME = 0x2F
PHOTO = 0x30
SAVE_MODEL = 0x32
LOAD_MODEL = 0x33
CUSTOM_TEXT = 0x34
CLEAR_TEXT = 0x35
LEARN = 0x36
FORGET = 0x37
SCREENSHOT = 0x39
IS_PRO = 0x3B
FIRMWARE_VERSION = 0x3C

# --- Replies from the HuskyLens -----------------------------------------------------
RETURN_INFO = 0x29
RETURN_BLOCK = 0x2A
RETURN_ARROW = 0x2B
RETURN_OK = 0x2E
RETURN_BUSY = 0x3D
RETURN_NEED_PRO = 0x3E

REPLY_NAMES = {
    RETURN_INFO: "INFO",
    RETURN_BLOCK: "BLOCK",
    RETURN_ARROW: "ARROW",
    RETURN_OK: "OK",
    RETURN_BUSY: "BUSY",
    RETURN_NEED_PRO: "NEED_PRO",
}

# Algorithm IDs for 0x2D. 7 and 8 are unconfirmed (verify at G0).
ALGORITHMS = {
    "face_recognition": 0,
    "object_tracking": 1,
    "object_recognition": 2,
    "line_tracking": 3,
    "color_recognition": 4,
    "tag_recognition": 5,
    "object_classification": 6,
    "qr_code_recognition": 7,
    "barcode_recognition": 8,
}
ALGORITHM_NAMES = {v: k for k, v in ALGORITHMS.items()}

# Request opcode table: kind -> (plain, learned_only, by_id)
REQUEST_OPS = {
    "all": (REQUEST, REQUEST_LEARNED, REQUEST_BY_ID),
    "blocks": (REQUEST_BLOCKS, REQUEST_BLOCKS_LEARNED, REQUEST_BLOCKS_BY_ID),
    "arrows": (REQUEST_ARROWS, REQUEST_ARROWS_LEARNED, REQUEST_ARROWS_BY_ID),
}

SCREEN_W, SCREEN_H = 320, 240
MAX_TEXT_LEN = 20
MAX_NAME_LEN = 20


class HuskyError(Exception):
    """A protocol-level failure (bad frame, timeout, BUSY, NEED_PRO...)."""


# --- Frame encode/decode -------------------------------------------------------------


def checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def build_frame(cmd: int, data: bytes = b"") -> bytes:
    if len(data) > 255:
        raise HuskyError(f"payload too long ({len(data)} > 255)")
    body = HEADER + bytes([len(data), cmd & 0xFF]) + data
    return body + bytes([checksum(body)])


def u16(*values: int) -> bytes:
    return b"".join(struct.pack("<H", v & 0xFFFF) for v in values)


@dataclass
class Frame:
    cmd: int
    data: bytes = b""

    @property
    def name(self) -> str:
        return REPLY_NAMES.get(self.cmd, f"0x{self.cmd:02X}")

    def words(self) -> list[int]:
        n = len(self.data) // 2
        return list(struct.unpack(f"<{n}h", self.data[: n * 2])) if n else []

    def raw(self) -> bytes:
        return build_frame(self.cmd, self.data)


@dataclass
class ParseResult:
    frames: list[Frame] = field(default_factory=list)
    rest: bytes = b""
    dropped: int = 0  # bytes discarded while resyncing (bad header/checksum)


def split_frames(buf: bytes) -> ParseResult:
    """Pull complete, checksum-valid frames out of ``buf``. Leftover bytes go in ``rest``."""
    out = ParseResult()
    i = 0
    n = len(buf)
    while i < n:
        j = buf.find(HEADER, i)
        if j < 0:
            # keep a possible partial header at the tail
            keep = 0
            for k in (2, 1):
                if buf[n - k :] == HEADER[:k]:
                    keep = k
                    break
            out.dropped += (n - i) - keep
            i = n - keep
            break
        out.dropped += j - i
        i = j
        if n - i < 6:
            break
        length = buf[i + 3]
        total = length + 6
        if n - i < total:
            break
        frame = buf[i : i + total]
        if checksum(frame[:-1]) != frame[-1]:
            out.dropped += 1
            i += 1
            continue
        out.frames.append(Frame(frame[4], bytes(frame[5:-1])))
        i += total
    out.rest = bytes(buf[i:])
    return out


def parse_frames(raw: bytes | Iterable[bytes]) -> list[Frame]:
    if isinstance(raw, (bytes, bytearray)):
        return split_frames(bytes(raw)).frames
    frames: list[Frame] = []
    for chunk in raw:
        frames.extend(split_frames(bytes(chunk)).frames)
    return frames


def decode(frames: list[Frame]) -> dict:
    """Turn reply frames into structured JSON (FR-03)."""
    result: dict = {"status": "OK"}
    blocks, arrows, other = [], [], []
    for f in frames:
        if f.cmd == RETURN_OK:
            result["status"] = "OK"
        elif f.cmd == RETURN_BUSY:
            result["status"] = "BUSY"
        elif f.cmd == RETURN_NEED_PRO:
            result["status"] = "NEED_PRO"
        elif f.cmd == RETURN_INFO:
            w = f.words() + [0] * 5
            result["info"] = {"count": w[0], "learned_ids": w[1], "frame": w[2] & 0xFFFF}
        elif f.cmd == RETURN_BLOCK:
            x, y, w, h, i = (f.words() + [0] * 5)[:5]
            blocks.append({"id": i, "x": x, "y": y, "width": w, "height": h, "learned": i > 0})
        elif f.cmd == RETURN_ARROW:
            x0, y0, x1, y1, i = (f.words() + [0] * 5)[:5]
            arrows.append(
                {"id": i, "x_origin": x0, "y_origin": y0, "x_target": x1, "y_target": y1, "learned": i > 0}
            )
        else:
            other.append(describe(f))
    if "info" in result or blocks or arrows:
        result["blocks"] = blocks
        result["arrows"] = arrows
    if other:
        result["other_frames"] = other
    if not frames:
        result["status"] = "NO_REPLY"
    return result


def describe(f: Frame) -> dict:
    """Best-effort description of an undocumented reply (0x3B / 0x3C formats are TBC)."""
    printable = "".join(chr(b) if 32 <= b < 127 else "." for b in f.data)
    return {
        "cmd": f"0x{f.cmd:02X}",
        "name": f.name,
        "data_hex": f.data.hex(),
        "ascii": printable,
        "u16": [w & 0xFFFF for w in f.words()],
    }


def parse_hex(text: str) -> bytes:
    cleaned = "".join(ch for ch in text if ch not in " :,-\n\t")
    if cleaned.lower().startswith("0x"):
        cleaned = cleaned[2:]
    try:
        return bytes.fromhex(cleaned)
    except ValueError as e:
        raise HuskyError(f"bad hex: {e}") from None


def resolve_algorithm(algorithm: int | str) -> int:
    if isinstance(algorithm, int) or (isinstance(algorithm, str) and algorithm.isdigit()):
        a = int(algorithm)
        if not 0 <= a <= 0xFFFF:
            raise HuskyError(f"algorithm id out of range: {a}")
        return a
    key = str(algorithm).strip().lower().replace(" ", "_").replace("colour", "color")
    aliases = {
        "face": "face_recognition",
        "tracking": "object_tracking",
        "object": "object_recognition",
        "line": "line_tracking",
        "color": "color_recognition",
        "tag": "tag_recognition",
        "apriltag": "tag_recognition",
        "classification": "object_classification",
        "qr": "qr_code_recognition",
        "qr_code": "qr_code_recognition",
        "barcode": "barcode_recognition",
    }
    key = aliases.get(key, key)
    if key not in ALGORITHMS:
        raise HuskyError(f"unknown algorithm {algorithm!r}; known: {sorted(ALGORITHMS)}")
    return ALGORITHMS[key]


# --- Payload builders (mirroring HUSKYLENSArduino) ------------------------------------


def name_payload(id_: int, name: str) -> bytes:
    raw = name.encode("ascii", "replace")[:MAX_NAME_LEN]
    size = len(raw) + 1  # include terminator
    if size % 2:
        size += 1  # library pads to an even length
    return bytes([id_ & 0xFF, size]) + raw.ljust(size, b"\x00")


def text_payload(text: str, x: int, y: int) -> bytes:
    raw = text.encode("ascii", "replace")[:MAX_TEXT_LEN]
    if not (0 <= x <= SCREEN_W and 0 <= y <= SCREEN_H):
        raise HuskyError(f"text position out of range: ({x}, {y}); screen is {SCREEN_W}x{SCREEN_H}")
    # x > 255 is sent as a flag byte 0xFF plus x % 255 (as the Arduino/Python libraries do)
    xb = bytes([0xFF, x % 255]) if x > 255 else bytes([0x00, x])
    return bytes([len(raw)]) + xb + bytes([y]) + raw


# --- High-level client ---------------------------------------------------------------


class Transport(Protocol):
    def husky(self, frame: bytes, expect: int | None = None, timeout_ms: int | None = None) -> list[bytes]: ...


def _env_op(name: str, default: int) -> int:
    v = os.environ.get(name)
    return int(v, 0) if v else default


class HuskyLens:
    """Every HUSKYLENS protocol command, returning parsed JSON-friendly dicts."""

    def __init__(self, transport: Transport, learn_op: int | None = None, forget_op: int | None = None):
        self.t = transport
        # v0.5.1 uses 0x36/0x37; some older libraries differ. Override via env if G0 says so.
        self.learn_op = learn_op if learn_op is not None else _env_op("RPSBOT_HUSKY_LEARN_OP", LEARN)
        self.forget_op = forget_op if forget_op is not None else _env_op("RPSBOT_HUSKY_FORGET_OP", FORGET)
        self.algorithm: int | None = None  # last algorithm we set (the device can't be queried)

    # low level
    def send(self, cmd: int, data: bytes = b"", expect: int | None = None, timeout_ms: int | None = None) -> list[Frame]:
        raw = self.t.husky(build_frame(cmd, data), expect=expect, timeout_ms=timeout_ms)
        return parse_frames(raw)

    def command(self, cmd: int, data: bytes = b"", timeout_ms: int | None = None) -> dict:
        frames = self.send(cmd, data, timeout_ms=timeout_ms)
        result = decode(frames)
        if result["status"] in ("BUSY", "NEED_PRO", "NO_REPLY"):
            result["ok"] = False
        else:
            result["ok"] = True
        return result

    def raw(self, hex_frame: str, expect: int | None = None, timeout_ms: int | None = None, add_checksum: bool = False) -> dict:
        data = parse_hex(hex_frame)
        if add_checksum:
            data = data + bytes([checksum(data)])
        frames = parse_frames(self.t.husky(data, expect=expect, timeout_ms=timeout_ms))
        return {
            "sent": data.hex(),
            "frames": [describe(f) for f in frames],
            "decoded": decode(frames),
        }

    # commands
    def knock(self) -> dict:
        return self.command(KNOCK)

    def is_pro(self) -> dict:
        frames = self.send(IS_PRO)
        return {"frames": [describe(f) for f in frames], **_status(frames)}

    def firmware_version(self) -> dict:
        frames = self.send(FIRMWARE_VERSION)
        info = {"frames": [describe(f) for f in frames], **_status(frames)}
        text = "".join(chr(b) for f in frames for b in f.data if 32 <= b < 127)
        if text:
            info["version_text"] = text
        return info

    def info(self) -> dict:
        return {"is_pro": self.is_pro(), "firmware": self.firmware_version()}

    def set_algorithm(self, algorithm: int | str) -> dict:
        a = resolve_algorithm(algorithm)
        r = self.command(ALGORITHM, u16(a), timeout_ms=1500)  # switching takes a moment
        if r["ok"]:
            self.algorithm = a
        r["algorithm"] = ALGORITHM_NAMES.get(a, a)
        return r

    def request(self, kind: str = "all", learned_only: bool = False, id: int | None = None) -> dict:
        kind = kind.lower()
        if kind not in REQUEST_OPS:
            raise HuskyError(f"kind must be one of {list(REQUEST_OPS)}")
        plain, learned, by_id = REQUEST_OPS[kind]
        if id is not None:
            r = self.command(by_id, u16(id))
        elif learned_only:
            r = self.command(learned)
        else:
            r = self.command(plain)
        if self.algorithm is not None:
            r["algorithm"] = ALGORITHM_NAMES.get(self.algorithm, self.algorithm)
        return r

    def blocks(self, learned_only: bool = False, id: int | None = None) -> list[dict]:
        r = self.request("blocks", learned_only, id)
        return r.get("blocks", [])

    def learn(self, id: int) -> dict:
        if not 1 <= id <= 0xFFFF:
            raise HuskyError("learn id must be >= 1")
        return self.command(self.learn_op, u16(id), timeout_ms=1500)

    def forget(self) -> dict:
        return self.command(self.forget_op, timeout_ms=1500)

    def set_name(self, id: int, name: str) -> dict:
        return self.command(CUSTOM_NAME, name_payload(id, name))

    def text(self, text: str, x: int = 10, y: int = 10) -> dict:
        return self.command(CUSTOM_TEXT, text_payload(text, x, y))

    def clear_text(self) -> dict:
        return self.command(CLEAR_TEXT)

    def photo(self) -> dict:
        return self.command(PHOTO, timeout_ms=2000)

    def screenshot(self) -> dict:
        return self.command(SCREENSHOT, timeout_ms=2000)

    def save_model(self, slot: int) -> dict:
        return self.command(SAVE_MODEL, u16(slot), timeout_ms=3000)

    def load_model(self, slot: int) -> dict:
        return self.command(LOAD_MODEL, u16(slot), timeout_ms=3000)

    def measure_fps(self, seconds: float = 3.0) -> dict:
        """Poll REQUEST and use the INFO frame counter to estimate camera FPS and request rate."""
        seconds = max(0.5, min(float(seconds), 15.0))
        t0 = time.monotonic()
        polls = 0
        first_frame = last_frame = None
        distinct = set()
        latencies = []
        while time.monotonic() - t0 < seconds:
            t = time.monotonic()
            r = self.request("all")
            latencies.append((time.monotonic() - t) * 1000)
            polls += 1
            info = r.get("info")
            if info:
                fn = info["frame"]
                distinct.add(fn)
                if first_frame is None:
                    first_frame = fn
                last_frame = fn
        elapsed = time.monotonic() - t0
        camera_fps = None
        if first_frame is not None and last_frame is not None:
            delta = (last_frame - first_frame) & 0xFFFF
            camera_fps = round(delta / elapsed, 1) if delta else None
        latencies.sort()
        return {
            "seconds": round(elapsed, 2),
            "polls": polls,
            "requests_per_s": round(polls / elapsed, 1),
            "distinct_frames": len(distinct),
            "camera_fps": camera_fps,
            "request_latency_ms": {
                "median": round(latencies[len(latencies) // 2], 1) if latencies else None,
                "max": round(latencies[-1], 1) if latencies else None,
            },
        }


def _status(frames: list[Frame]) -> dict:
    if not frames:
        return {"ok": False, "status": "NO_REPLY"}
    if any(f.cmd == RETURN_NEED_PRO for f in frames):
        return {"ok": False, "status": "NEED_PRO"}
    if any(f.cmd == RETURN_BUSY for f in frames):
        return {"ok": False, "status": "BUSY"}
    return {"ok": True, "status": "OK"}
