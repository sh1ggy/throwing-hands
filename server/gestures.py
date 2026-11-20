"""Gesture sets: which HuskyLens Object Classification ID means which gesture (D9, plus live game swaps).

The camera only stores numbers. This registry names them and persists to <data>/gestures.json so a
restart keeps the meaning. The default set is D9's rps (1 rock, 2 paper, 3 scissors, 4 nothing): only the static back-up the
robot starts with. Any other game replaces it through MCP; no firmware is ever pushed for a new game.

A swap is one call: the caller wipes the camera, ``start`` names the new IDs, and the gestures are
trained live. Nothing is backed up; going back to RPS is another swap plus training.
``nothing`` is always part of a set: classification labels the whole frame, so without an
"empty play zone" class every empty frame would read as one of the gestures.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

RPS = {"rock": 1, "paper": 2, "scissors": 3, "nothing": 4}
NAME_RE = re.compile(r"^[a-z0-9_]{1,20}$")


class GestureError(ValueError):
    pass


def clean(name: str) -> str:
    n = str(name).strip().lower().replace(" ", "_").replace("-", "_")
    if not NAME_RE.match(n):
        raise GestureError(f"gesture name {name!r}: use 1-20 of a-z, 0-9, _")
    return n


class GestureSets:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.name = "rps"
        self.ids: dict[str, int] = dict(RPS)
        if self.path.exists():
            d = json.loads(self.path.read_text())
            self.name = d.get("name", "rps")
            self.ids = {k: int(v) for k, v in d.get("ids", RPS).items()}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"name": self.name, "ids": self.ids}, indent=2))
        tmp.replace(self.path)

    def names(self) -> dict[int, str]:
        return {v: k for k, v in self.ids.items()}

    def label(self, gid: int) -> str:
        if gid == -1:
            return "none"
        if gid == 0:
            return "unknown"
        return self.names().get(gid, f"id{gid}")

    def resolve(self, move: str | int) -> tuple[int, str]:
        """Name or numeric ID -> (id, name). A new name gets the next free ID."""
        if isinstance(move, int) or str(move).strip().isdigit():
            i = int(move)
            if i < 1:
                raise GestureError("gesture IDs start at 1")
            return i, self.names().get(i, f"id{i}")
        n = clean(move)
        if n not in self.ids:
            self.ids[n] = max(self.ids.values(), default=0) + 1
            self.save()
        return self.ids[n], n

    def rps_ready(self) -> bool:
        """True when rock/paper/scissors sit on the IDs the RPS game expects."""
        return all(self.ids.get(k) == v for k, v in RPS.items() if k != "nothing")

    def status(self) -> dict:
        return {"set": self.name, "gestures": dict(self.ids)}

    @staticmethod
    def prepare(name: str, gestures: list[str]) -> tuple[str, list[str]]:
        """Validate a new set before anything is wiped. IDs follow the list order, then nothing."""
        set_name = clean(name)
        names = [clean(g) for g in gestures]
        if not names:
            raise GestureError("give at least one gesture")
        if "nothing" not in names:
            names.append("nothing")
        if len(set(names)) != len(names):
            raise GestureError(f"duplicate gesture names in {names}")
        return set_name, names

    def start(self, name: str, gestures: list[str]) -> dict:
        """Name a new set (call after the camera has been wiped)."""
        self.name, names = self.prepare(name, gestures)
        self.ids = {g: i for i, g in enumerate(names, start=1)}
        self.save()
        return self.status()
