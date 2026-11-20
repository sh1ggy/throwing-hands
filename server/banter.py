"""Banter: one line per round from the game state, offline (design doc D16-B, FR-15, T11).

``Banter(data_dir, rng).line(state) -> str``

The bank is a list of ``{"text": "...", "when": {...}}``. ``text`` may use placeholders from the state
(``{player} {robot} {human} {verb} {robot_score} {human_score} {demand} {announced}``). ``when`` is a set
of conditions that must all hold for the line to be eligible:

    "outcome": "robot"                  exact match (outcome is robot / human / draw / no_read / replay)
    "outcome": ["robot", "draw"]        any of
    "streak": {"min": 3}                numeric range (min and/or max)
    "bribed": true                      booleans work the same way

A condition on a key the state doesn't have never matches, and a line whose placeholders the state can't
fill is skipped, so a line never comes out wrong. Lines with more conditions beat lines with fewer; ties
are random; nothing repeats within the last ``NO_REPEAT`` lines (T11).

State keys the game engine provides: outcome, player, robot, human, verb, robot_score, human_score (the
session score, or the match score for ``match`` lines), streak + streak_by (consecutive wins by
``robot``/``human``), match ("won"/"lost" from the robot's view), bribed, replay, glare, spectators,
family (fair / win / lose / sore / bluff: a *tone*, never spoken), demand, announced, honest.

Banter is spoken aloud to the audience, so no built-in line may name or hint at a mode (nothing about
rigging, mercy, bribes, predicting). The one in-character exception is blaming "glare" for a misread (D10).

Banter lines from Claude (the D16-B bank) are loaded from ``<data_dir>/banter.json`` when it exists: a JSON
list of the same ``{"text", "when"}`` objects, added to the built-ins.

D16-C seam: ``Banter.model`` is where the optional distilled GGUF model plugs in. Set it with
``set_model(fn)``, where ``fn(state: dict) -> str | None`` (return None to decline). It is tried first; any
exception, None, empty or repeated line falls back to the bank. Not implemented here.
"""

from __future__ import annotations

import json
import random
import string
from collections import deque
from pathlib import Path
from typing import Callable

NO_REPEAT = 10
RESULT = ["robot", "human", "draw"]

# In-character lines only. Never say what mode is running.
BUILTIN: list[dict] = [
    # --- the robot wins -------------------------------------------------------------------------
    {"text": "{robot} {verb} {human}. Better luck next time, {player}.", "when": {"outcome": "robot"}},
    {"text": "My {robot} {verb} your {human}. Physics is on my side.", "when": {"outcome": "robot"}},
    {"text": "Point to me. {robot_score} to {human_score}.", "when": {"outcome": "robot"}},
    {"text": "Did you see that? Of course you did. It was right in front of you.", "when": {"outcome": "robot"}},
    {"text": "{human}? Bold. Wrong, but bold.", "when": {"outcome": "robot"}},
    {"text": "Too easy, {player}.", "when": {"outcome": "robot", "family": "win"}},
    {"text": "Never in doubt.", "when": {"outcome": "robot", "family": "win"}},
    {"text": "I'd say I'm sorry, {player}, but my speakers don't have that setting.", "when": {"outcome": "robot"}},
    {"text": "That's what {robot} is for.", "when": {"outcome": "robot"}},
    {"text": "Three fingers of fury, one of finesse.", "when": {"outcome": "robot"}},
    {"text": "That's {robot_score} for me. I'm not counting. Okay, I'm counting.", "when": {"outcome": "robot"}},
    # --- the human wins -------------------------------------------------------------------------
    {"text": "Your {human} {verb} my {robot}. Enjoy it.", "when": {"outcome": "human"}},
    {"text": "Fine. Point to {player}. {human_score} to {robot_score}.", "when": {"outcome": "human"}},
    {"text": "Lucky. I'll allow it, once.", "when": {"outcome": "human"}},
    {"text": "My servos were distracted. That's my story.", "when": {"outcome": "human"}},
    {"text": "I let you have that one. Probably.", "when": {"outcome": "human"}},
    {"text": "Well played, {player}. I hate that.", "when": {"outcome": "human"}},
    {"text": "{human} over {robot}. I'll be thinking about that all night.", "when": {"outcome": "human"}},
    {"text": "You got me. Impressive, {player}.", "when": {"outcome": "human", "family": "lose"}},
    {"text": "Ouch. Well done.", "when": {"outcome": "human", "family": "lose"}},
    {"text": "That one didn't count. Best of {demand}.", "when": {"outcome": "human", "family": "sore"}},
    {"text": "Did the camera blink? That wasn't a real throw. Best of {demand}.",
     "when": {"outcome": "human", "family": "sore"}},
    {"text": "Score? What score? Best of {demand}, {player}.", "when": {"outcome": "human", "family": "sore"}},
    {"text": "Somebody behind you is breathing too loudly. That cost me the round.",
     "when": {"outcome": "human", "spectators": {"min": 1}}},
    {"text": "I'd blame the audience, but I'll blame your friend. Yes, you in the back.",
     "when": {"outcome": "human", "spectators": {"min": 1}}},
    # --- draws ----------------------------------------------------------------------------------
    {"text": "Both {robot}. Great minds. Mine's bigger.", "when": {"outcome": "draw"}},
    {"text": "A draw. Nobody wins, nobody learns anything.", "when": {"outcome": "draw"}},
    {"text": "Stop copying me, {player}.", "when": {"outcome": "draw"}},
    {"text": "Same throw. Suspicious.", "when": {"outcome": "draw"}},
    {"text": "Stalemate. Again?", "when": {"outcome": "draw"}},
    {"text": "We match. That never happens.", "when": {"outcome": "draw"}},
    {"text": "{robot} meets {robot}. Nothing happens. How dull.", "when": {"outcome": "draw"}},
    {"text": "A tie, {player}. Let's break it.", "when": {"outcome": "draw"}},
    # --- any result (fills the pool so nothing repeats within ten lines) ---------------------------
    {"text": "Interesting choice, {player}.", "when": {"outcome": RESULT}},
    {"text": "I've seen better. I've also seen worse.", "when": {"outcome": RESULT}},
    {"text": "Right. Let's go again.", "when": {"outcome": RESULT}},
    {"text": "The crowd goes mild.", "when": {"outcome": RESULT}},
    {"text": "I was built for this.", "when": {"outcome": RESULT}},
    # --- streaks --------------------------------------------------------------------------------
    {"text": "That's {streak} in a row for me. I'd say I'm sorry.", "when": {"outcome": "robot", "streak_by": "robot",
                                                                           "streak": {"min": 3}}},
    {"text": "{streak} straight. This is going in the highlight reel.", "when": {"outcome": "robot",
                                                                                "streak_by": "robot",
                                                                                "streak": {"min": 3}}},
    {"text": "{streak} in a row, {player}? The robot uprising is going well.", "when": {"outcome": "human",
                                                                                      "streak_by": "human",
                                                                                      "streak": {"min": 3}}},
    {"text": "{streak} wins in a row. I need to check my sensors.", "when": {"outcome": "human",
                                                                          "streak_by": "human",
                                                                          "streak": {"min": 3}}},
    # --- bribed (gold lights) -------------------------------------------------------------------
    {"text": "I'm feeling golden today. Take the round.", "when": {"outcome": "human", "bribed": True}},
    {"text": "Something shiny has put me in a generous mood.", "when": {"outcome": "human", "bribed": True}},
    {"text": "Go on, {player}. You've earned a little shine.", "when": {"outcome": "human", "bribed": True}},
    # --- bluff ----------------------------------------------------------------------------------
    {"text": "I said {announced} and I meant it.", "when": {"family": "bluff", "honest": True,
                                                            "outcome": ["robot", "human", "draw"]}},
    {"text": "Did I say {announced}? I lied. I'm a robot.", "when": {"family": "bluff", "honest": False,
                                                                    "outcome": ["robot", "human", "draw"]}},
    # --- matches --------------------------------------------------------------------------------
    {"text": "Match to me, {robot_score} to {human_score}. Please clap.", "when": {"match": "won"}},
    {"text": "That's the match. {player}, you were a worthy opponent. Barely.", "when": {"match": "won"}},
    {"text": "Game over. I win. Naturally.", "when": {"match": "won"}},
    {"text": "The match is yours, {player}, {human_score} to {robot_score}. I demand a rematch.", "when": {"match": "lost"}},
    {"text": "I'm not angry. I'm just very, very quiet. Well played.", "when": {"match": "lost"}},
    {"text": "You win the match. My lawyers will be in touch.", "when": {"match": "lost"}},
    # --- replays and misreads -------------------------------------------------------------------
    {"text": "The glare off the lens threw me. Again.", "when": {"outcome": "replay", "glare": True}},
    {"text": "That light was in my eyes. Rematch, {player}.", "when": {"outcome": "replay", "glare": True}},
    {"text": "I couldn't see that through the glare. Go again.", "when": {"outcome": "replay", "glare": True}},
    {"text": "I didn't catch that. Throw again, {player}.", "when": {"outcome": "replay", "glare": False}},
    {"text": "Too slow, too shy, or too weird. Again.", "when": {"outcome": "replay", "glare": False}},
    {"text": "Show me a proper throw this time.", "when": {"outcome": "replay", "glare": False}},
    {"text": "No throw, no round. We'll call it a draw of effort.", "when": {"outcome": "no_read"}},
    {"text": "I give up on that one. We'll start over.", "when": {"outcome": "no_read"}},
    {"text": "That round never happened. Let's pretend.", "when": {"outcome": "no_read"}},
]

DEFAULT_STATE = {"outcome": "robot", "player": "challenger", "robot": "rock", "human": "scissors",
                 "verb": "crushes", "robot_score": 0, "human_score": 0, "streak": 1, "streak_by": "robot",
                 "bribed": False, "replay": False, "glare": False, "spectators": 0, "family": "fair"}


def _fields(text: str) -> set[str]:
    return {f.split(".")[0].split("[")[0] for _, f, _, _ in string.Formatter().parse(text) if f}


def matches(when: dict, state: dict) -> bool:
    for key, want in (when or {}).items():
        if key not in state or state[key] is None:
            return False
        have = state[key]
        if isinstance(want, dict):
            if not isinstance(have, (int, float)) or isinstance(have, bool):
                return False
            if "min" in want and have < want["min"]:
                return False
            if "max" in want and have > want["max"]:
                return False
        elif isinstance(want, list):
            if have not in want:
                return False
        elif have != want:
            return False
    return True


class Banter:
    def __init__(self, data_dir: Path | None = None, rng: random.Random | None = None):
        self.rng = rng or random.Random()
        self.recent: deque[str] = deque(maxlen=NO_REPEAT)
        self.bank: list[dict] = [dict(l) for l in BUILTIN]
        self.model: Callable[[dict], str | None] | None = None  # D16-C seam
        self.loaded = 0
        self.load_error: str | None = None
        if data_dir is not None:
            self._load(Path(data_dir) / "banter.json")

    def _load(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            extra = json.loads(path.read_text())
            good = [{"text": str(l["text"]), "when": dict(l.get("when") or {})}
                    for l in extra if isinstance(l, dict) and l.get("text")]
        except (OSError, ValueError, TypeError, KeyError) as e:
            self.load_error = f"{path.name}: {e}"
            return
        self.bank += good
        self.loaded = len(good)

    # ----------------------------------------------------------------------------- D16-C seam
    def set_model(self, fn: Callable[[dict], str | None] | None) -> None:
        """Plug in the distilled model (D16-C): ``fn(state) -> str | None``. The bank stays the fallback."""
        self.model = fn

    def _from_model(self, state: dict) -> str | None:
        if self.model is None:
            return None
        try:
            text = self.model(state)
        except Exception:
            return None
        text = " ".join(str(text).split()) if text else ""
        return text if text and text not in self.recent else None

    # ----------------------------------------------------------------------------- the bank
    def candidates(self, state: dict) -> list[tuple[int, str]]:
        out = []
        for l in self.bank:
            when = l.get("when") or {}
            if not matches(when, state):
                continue
            if any(state.get(f) is None for f in _fields(l["text"])):
                continue
            try:
                text = l["text"].format_map(state)
            except (KeyError, IndexError, ValueError):
                continue
            out.append((len(when), text))
        return out

    def line(self, state: dict, record: bool = True) -> str:
        """Pick a line for ``state``. ``record=False`` previews without using up the line."""
        state = dict(state)
        text = self._from_model(state)
        if text is None:
            text = self._from_bank(state)
        if record:
            self.recent.append(text)
        return text

    def _from_bank(self, state: dict) -> str:
        cands = self.candidates(state)
        if not cands:
            return "Again?"
        recent = list(self.recent)
        for spec in sorted({s for s, _ in cands}, reverse=True):
            fresh = [t for s, t in cands if s == spec and t not in recent]
            if fresh:
                return self.rng.choice(fresh)
        # everything eligible was used in the last NO_REPEAT lines: reuse the stalest one
        texts = [t for _, t in cands]
        return min(texts, key=lambda t: recent.index(t))

    def preview(self, state: dict) -> str:
        return self.line({**DEFAULT_STATE, **state}, record=False)
