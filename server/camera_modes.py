"""Camera modes for state machines: any HuskyLens block algorithm as a game input (agreed 2026-11-21; not in the
design doc, see docs/TODO.md).

A machine says which mode it reads with ``"camera"``; the runtime turns what that mode sees into ``see:<label>``
events. Classification (the default) is the gesture path and keeps ``gesture:<name>`` as well.

Every detection mode answers the same way: the camera returns blocks (x, y, w, h, id) of what it recognises; an ID
the camera has learned is named from the mode's label set (``labels_<mode>.json``), ``id<N>`` if unnamed, ``unknown``
for something it sees but hasn't learned (ID 0), and ``nothing`` for an empty frame. A read is a majority vote over a
window, like a gesture read. QR and barcode payload text is never returned, only learned IDs (§9.3).
"""

from __future__ import annotations

import re
import time
from collections import Counter

CLASSIFICATION = "classification"
NOTHING, UNKNOWN = "nothing", "unknown"
RESERVED = {NOTHING, UNKNOWN}
ID_LABEL_RE = re.compile(r"^id([1-9][0-9]{0,2})$")

# mode -> HuskyLens algorithm (huskylens.ALGORITHMS). Line tracking returns arrows, not blocks, so it isn't a mode.
MODES = {
    CLASSIFICATION: "object_classification",
    "qr_code": "qr_code_recognition",       # algorithm 7, unconfirmed on the device (§9.6)
    "barcode": "barcode_recognition",       # algorithm 8, unconfirmed
    "tag": "tag_recognition",
    "color": "color_recognition",
    "object": "object_recognition",
    "tracking": "object_tracking",
    "face": "face_recognition",
}
ALIASES = {
    "gesture": CLASSIFICATION, "gestures": CLASSIFICATION, "object_classification": CLASSIFICATION,
    "qr": "qr_code", "qrcode": "qr_code", "qr_code_recognition": "qr_code",
    "barcode_recognition": "barcode",
    "apriltag": "tag", "tags": "tag", "tag_recognition": "tag",
    "colour": "color", "colors": "color", "colours": "color", "color_recognition": "color",
    "object_recognition": "object", "objects": "object",
    "object_tracking": "tracking",
    "faces": "face", "face_recognition": "face",
}


class CameraModeError(ValueError):
    pass


def mode_name(x) -> str:
    """A mode or alias -> the canonical mode name."""
    key = str(x).strip().lower().replace(" ", "_").replace("-", "_")
    key = ALIASES.get(key, key)
    if key not in MODES:
        raise CameraModeError(f"unknown camera mode {x!r}; use one of {sorted(MODES)}")
    return key


def algorithm(mode: str) -> str:
    return MODES[mode_name(mode)]


def label_of(block_id: int | None, names: dict[int, str]) -> str:
    if block_id is None or block_id < 0:
        return NOTHING
    if block_id == 0:
        return UNKNOWN
    return names.get(block_id, f"id{block_id}")


def main_block(blocks: list[dict]) -> dict | None:
    """The block that counts for a read: the biggest learned one, else the biggest unlearned one."""
    if not blocks:
        return None
    learned = [b for b in blocks if b.get("id", 0) > 0]
    pool = learned or blocks
    return max(pool, key=lambda b: b.get("w", 0) * b.get("h", 0))


def summarize(frames: list[list[dict]], names: dict[int, str]) -> dict:
    """Majority vote over per-read block lists. The payload (position, everything in frame) comes from the last read
    that agreed with the winner."""
    if not frames:
        return {"label": "no_reply", "id": None, "confidence": 0.0, "confident": False, "reads": 0, "votes": {}}
    per = []
    for blocks in frames:
        b = main_block(blocks)
        per.append((label_of(b["id"] if b else None, names), b, blocks))
    votes = Counter(lbl for lbl, _, _ in per)
    top, n = votes.most_common(1)[0]
    share = round(n / len(per), 2)
    _, b, blocks = next(p for p in reversed(per) if p[0] == top)
    out = {"label": top, "id": b["id"] if b else None, "confidence": share,
           "confident": share >= 0.6 and len(per) >= 3, "reads": len(per), "votes": dict(votes),
           "count": len(blocks), "labels": sorted({label_of(x["id"], names) for x in blocks})}
    if b:
        out.update({k: b.get(k) for k in ("x", "y", "w", "h")})
    return out


def read(hl, mode: str, window_s: float, names: dict[int, str]) -> dict:
    """Read ``mode`` for about ``window_s`` (at least one reply, giving up 0.5 s after the window). The caller holds
    the camera lock and has already switched the algorithm."""
    from huskylens import HuskyError
    frames: list[list[dict]] = []
    t0 = time.monotonic()
    t_end = t0 + window_s
    while True:
        now = time.monotonic()
        if (now >= t_end and frames) or now >= t_end + 0.5:
            break
        try:
            frames.append(hl.blocks())
        except HuskyError:
            continue
    out = summarize(frames, names)
    out["mode"] = mode_name(mode)
    out["read_ms"] = int((time.monotonic() - t0) * 1000)
    return out
