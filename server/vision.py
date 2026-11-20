"""Vision sources for the game (design doc FR-16, D9, D10, D15).

The game engine only talks to a ``VisionSource``:
  - ``read_throw(window_s, id_to_move)``: majority vote over ~window_s of reads (§5 step 4)
  - ``identify_face(window_s)``: setup phase only; switches to face recognition and back (D10)
  - ``learn_face(face_id, samples)``: register a player

``HuskyVision`` uses the HuskyLens. ``MediaPipeVision`` is the D15 fallback; it isn't built yet and
says so, so ``set_vision_source`` fails clearly instead of silently.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Callable

from huskylens import HuskyError

CLASSIFICATION = "object_classification"
FACES = "face_recognition"
QR = "qr_code_recognition"  # algorithm 7, unconfirmed on the device (§9.6)


class VisionError(RuntimeError):
    pass


def majority(votes: list[int]) -> tuple[int | None, float]:
    """Most common ID and its share of the votes. -1 = nothing in view, 0 = seen but not learned."""
    if not votes:
        return None, 0.0
    top, n = Counter(votes).most_common(1)[0]
    return top, n / len(votes)


class VisionSource:
    name = "base"

    def read_throw(self, window_s: float, id_to_move: dict[int, str]) -> dict:
        raise NotImplementedError

    def identify_face(self, window_s: float = 1.5) -> dict:
        raise NotImplementedError

    def learn_face(self, face_id: int, name: str, samples: int = 5) -> dict:
        raise NotImplementedError

    def see_id(self, algorithm: str, wanted: int, window_s: float = 1.0) -> bool:
        raise NotImplementedError

    def show_text(self, text: str) -> None:
        pass


class HuskyVision(VisionSource):
    """``cam`` is server.Camera (has .lock and .get() -> HuskyLens)."""

    name = "huskylens"

    def __init__(self, cam, sleep: Callable[[float], None] = time.sleep):
        self.cam = cam
        self.sleep = sleep

    def _hl(self):
        return self.cam.get()

    def _ensure(self, hl, algorithm: str) -> None:
        if hl.algorithm is not None and hl.algorithm == _alg_id(algorithm):
            return
        for _ in range(4):
            if hl.set_algorithm(algorithm).get("ok"):
                return
            time.sleep(0.25)
        raise VisionError(f"camera won't switch to {algorithm}")

    def _votes(self, hl, window_s: float, learned_only: bool = False) -> list[int]:
        """Read until window_s has passed and there is at least one vote (giving up 0.5 s later)."""
        votes: list[int] = []
        t_end = time.monotonic() + window_s
        while True:
            now = time.monotonic()
            if (now >= t_end and votes) or now >= t_end + 0.5:
                return votes
            try:
                blocks = hl.blocks(learned_only=learned_only)
            except HuskyError:
                continue
            votes.append(blocks[0]["id"] if blocks else -1)

    def read_throw(self, window_s: float, id_to_move: dict[int, str]) -> dict:
        with self.cam.lock:
            hl = self._hl()
            self._ensure(hl, CLASSIFICATION)
            t0 = time.monotonic()
            votes = self._votes(hl, window_s)
            top, share = majority(votes)
        # FR-07: rock / paper / scissors (or a custom move with a husky_id) / nothing / unknown
        if top is None:
            move = "unknown"
        elif top == -1:
            move = "nothing"
        elif top == 0:
            move = "unknown"
        else:
            move = id_to_move.get(top, "unknown")
        return {"move": move, "id": top, "share": round(share, 2), "reads": len(votes),
                "votes": dict(Counter(votes)), "read_ms": int((time.monotonic() - t0) * 1000)}

    def identify_face(self, window_s: float = 1.5) -> dict:
        """Face IDs in view (setup only, D10). Returns the main face and how many distinct faces were seen."""
        with self.cam.lock:
            hl = self._hl()
            self._ensure(hl, FACES)
            seen: Counter = Counter()
            counts: list[int] = []
            t_end = time.monotonic() + window_s
            while time.monotonic() < t_end:
                try:
                    r = hl.request("blocks")
                except HuskyError:
                    continue
                ids = [b["id"] for b in r.get("blocks", [])]
                counts.append(len(ids))
                seen.update(i for i in ids if i > 0)
            self._ensure(hl, CLASSIFICATION)  # back to gestures for play
        face_id = seen.most_common(1)[0][0] if seen else None
        return {"face_id": face_id, "faces_seen": sorted(seen), "max_faces_in_frame": max(counts, default=0)}

    def learn_face(self, face_id: int, name: str, samples: int = 5) -> dict:
        with self.cam.lock:
            hl = self._hl()
            self._ensure(hl, FACES)
            ok = 0
            status = None
            for i in range(samples):
                hl.text(f"{name[:12]} {i + 1}/{samples}", 10, 10)
                self.sleep(0.4)
                r = hl.learn(face_id)
                status = r.get("status")
                ok += bool(r.get("ok"))
                if status == "UNSUPPORTED":
                    break
            hl.set_name(face_id, name)
            hl.clear_text()
            self._ensure(hl, CLASSIFICATION)
        if not ok:
            raise VisionError(f"camera didn't accept any face samples ({status}); firmware >= 0.5.1 needed")
        return {"face_id": face_id, "accepted": ok, "samples": samples}

    def see_id(self, algorithm: str, wanted: int, window_s: float = 1.0) -> bool:
        with self.cam.lock:
            hl = self._hl()
            try:
                self._ensure(hl, algorithm)
            except VisionError:
                return False
            votes = self._votes(hl, window_s, learned_only=True)
            self._ensure(hl, CLASSIFICATION)
        return wanted in votes

    def show_text(self, text: str) -> None:
        try:
            with self.cam.lock:
                hl = self._hl()
                hl.clear_text()
                if text:
                    hl.text(text[:20], 10, 10)
        except Exception:
            pass  # the overlay is decoration; never fail a round over it


class MediaPipeVision(VisionSource):
    name = "mediapipe"

    def __init__(self, *_a, **_k):
        raise VisionError("MediaPipe fallback (D15) isn't built yet; the game uses the HuskyLens")


def _alg_id(name: str) -> int:
    from huskylens import ALGORITHMS
    return ALGORITHMS[name]
