"""Free play: any gesture game you describe in chat, refereed by Claude (beyond RPS, D11/FR-11 taken further).

The server doesn't know the rules of these games; it holds them so Claude (and a screen) can see them:
name, rules text, gestures, players, scores, free-form state and a log of turns. Claude runs the game
turn by turn with game_turn (countdown + gesture read) and update_game (scores/state).

The current game persists to <data>/freeplay.json so a server restart mid-demo keeps the score.
RPS isn't one of these: it's the built-in engine in game.py (play_round / play_match).
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class FreeplayError(ValueError):
    pass


class Freeplay:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.game: dict | None = None
        if self.path.exists():
            self.game = json.loads(self.path.read_text()) or None

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.game, indent=2))
        tmp.replace(self.path)

    def require(self) -> dict:
        if self.game is None or self.game.get("ended"):
            raise FreeplayError("no game running: start one with new_game")
        return self.game

    def new(self, name: str, rules: str, gestures: list[str], players: list[str] | None) -> dict:
        rules = str(rules).strip()
        if not rules:
            raise FreeplayError("describe the rules (they're shown back to you every turn)")
        players = [str(p).strip() for p in (players or ["player"]) if str(p).strip()]
        self.game = {"name": str(name).strip(), "rules": rules, "gestures": list(gestures), "players": players,
                     "scores": {p: 0 for p in players}, "state": {}, "turns": [], "notes": [],
                     "started": time.time(), "ended": None}
        self.save()
        return self.status()

    def record_turn(self, entry: dict) -> int:
        g = self.require()
        g["turns"].append({"n": len(g["turns"]) + 1, "t": time.time(), **entry})
        self.save()
        return len(g["turns"])

    def update(self, scores: dict | None = None, set_scores: dict | None = None,
               state: dict | None = None, note: str | None = None) -> dict:
        g = self.require()
        for p, d in (scores or {}).items():
            g["scores"][p] = g["scores"].get(p, 0) + _num(d, p)
        for p, v in (set_scores or {}).items():
            g["scores"][p] = _num(v, p)
        for k, v in (state or {}).items():
            if v is None:
                g["state"].pop(k, None)
            else:
                g["state"][k] = v
        if note:
            g["notes"].append(str(note)[:200])
        self.save()
        return self.status()

    def end(self, summary: str = "") -> dict:
        g = self.require()
        g["ended"] = time.time()
        g["summary"] = summary
        self.save()
        return self.status()

    def status(self, last_turns: int = 5) -> dict:
        if self.game is None:
            return {"game": None}
        g = self.game
        return {"game": g["name"], "rules": g["rules"], "gestures": g["gestures"], "players": g["players"],
                "scores": g["scores"], "state": g["state"], "turns": len(g["turns"]),
                "recent_turns": g["turns"][-last_turns:], "notes": g["notes"][-5:],
                "running": not g.get("ended")}


def _num(v, who) -> int | float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise FreeplayError(f"score for {who!r} must be a number, got {v!r}")
    return v
