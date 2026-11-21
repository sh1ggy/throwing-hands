"""Game engine: moves, rules, players, modes, sequences, scoring and the round state machine
(design doc §5, §9.4, D10, D11, D20; FR-09..FR-19).

    Store   JSON files in a data dir (moves, rules, players, sequences, settings, history.jsonl), atomic writes,
            defaults created on first run (FR-17, D11).
    Game    play_round / play_match plus the CRUD behind the §9.3 Game tools.

A round follows §9.4: Countdown (LED + optional ISD1820 clip + hand pump) -> Commit at "shoot" (fair, predict,
bluff, grudge while inactive, sore_loser) or HoldFist (rigged modes) -> Read (vision majority vote) -> for
rigged modes commit the counter / losing pose after the read -> Score -> Replay on an unknown or empty read
(max 2, then "no_read") -> Report (LED, banter, score, hooks) -> the hand relaxes after ``result_hold_s``
(NFR-09). ``play_round`` returns as soon as the report is done; the hold, the hooks and the relax run on a short
worker thread so the call returns within 1.5 s of "shoot" (NFR-01). The next hand action waits for it.

Modes are secret from the audience: results are for the operator/Claude, banter and speech never name a mode.

Interpretations the design doc leaves open (also listed in the build report):
  * ``{"repeat": n}`` plays the steps since the start, or since the previous repeat, n times in total.
  * Grudge: fair until the player beats the robot; from the next round on, rigged-win (flag on the player).
  * Bribery: rigged-lose with gold LEDs; ``identify_player(check_bribe=True)`` sets it for the rest of the match
    (cleared at match end, ``reset_score`` or the next identify_player).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Callable

import rps_lib
from banter import Banter
from hardware import (CLOSED_BELOW, FINGERS, HOLD_LIMIT_S, HardwareError, check_colour, check_fingers)
from vision import QR, VisionError, VisionSource

CLASSIC_MOVES = ["rock", "paper", "scissors"]
RESERVED_MOVE_NAMES = {"nothing", "unknown", "none", "guest", "stranger"}
RESERVED_HUSKY_IDS = {0, 4}  # 0 = seen but not learned, 4 = "nothing" (D9)
MODES = ["fair", "predict", "rigged_win", "rigged_lose", "grudge", "bribery", "bluff", "sore_loser"]
MODE_ALIASES = {"mercy": "rigged_lose", "rigged": "rigged_win"}
EVENTS = ["on_reveal", "on_win", "on_loss"]
GOLD = "#ffb000"
BEAT_WORDS = ["ROCK", "PAPER", "SCISSORS", "SHOOT"]
FIST = [0.0, 0.0, 0.0, 0.0]
PUMP_OPEN = [0.25] * 4
HISTORY_CAP = rps_lib.HISTORY_CAP

MIN_STEP_MS = 60           # D20: every timed step is clamped to at least this
MAX_STEP_MS = 10_000
MAX_SEQUENCE_S = 10.0      # D20
MAX_REPEAT = 10            # D20
MAX_UNROLLED_STEPS = 400


class GameError(ValueError):
    """Bad input or an impossible request (the MCP layer reports it to Claude)."""


def default_moves() -> list[dict]:
    return [
        {"name": "rock", "fingers": [0, 0, 0, 0], "led": "#ff0000", "husky_id": 1},
        {"name": "paper", "fingers": [1, 1, 1, 1], "led": "#00ff00", "husky_id": 2},
        {"name": "scissors", "fingers": [0, 1, 1, 0], "led": "#0040ff", "husky_id": 3},
    ]


def classic_rules() -> list[dict]:
    return [
        {"winner": "rock", "loser": "scissors", "verb": "crushes"},
        {"winner": "paper", "loser": "rock", "verb": "covers"},
        {"winner": "scissors", "loser": "paper", "verb": "cuts"},
    ]


def default_sequences() -> list[dict]:
    return [
        {"name": "tantrum", "steps": [  # the D20 example
            {"pose": [0, 1, 1, 0], "ms": 120},
            {"pose": [0, 0, 0, 0], "ms": 120},
            {"repeat": 3},
            {"led": "#ff0000"},
            {"voice": "play", "ms": 1000},
            {"wait": 400},
            {"pose": [1, 1, 1, 1], "ms": 300},
        ]},
        {"name": "victory", "steps": [
            {"led": GOLD},
            {"pose": [1, 1, 1, 1], "ms": 150},
            {"pose": [0, 1, 1, 0], "ms": 150},
            {"repeat": 3},
            {"pose": [1, 1, 1, 1], "ms": 300},
            {"wait": 200},
        ]},
    ]


def default_settings() -> dict:
    return {
        "hooks": {"on_reveal": None, "on_win": None, "on_loss": None},
        "countdown_period_ms": 450,
        "countdown_colours": ["#ff3030", "#ffb000", "#30ff60", "#ffffff"],
        "voice_countdown": False,
        "show_beats": True,
        "pose_ms": 120,
        "throw_ms": 300,  # fair-style throw at shoot: sent at shoot, ramps open about as fast as a human throws
        "snap_ms": 80,
        "read_delay_s": 0.1,
        "read_window_s": 0.3,
        "result_hold_s": 1.5,
        "speak_banter": True,
        "max_replays": 2,
        "default_mode": "fair",
        "stranger_mode": "rigged_win",  # strangers always lose (confirmed by the user 2026-11-21; TODO)
        "bribe_card_id": 1,
        "id_window_s": 1.5,
        "face_samples": 5,
    }


FILES = {"moves": default_moves, "rules": classic_rules, "players": list,
         "sequences": default_sequences, "settings": default_settings}


class Store:
    """JSON files in ``data_dir``, written atomically (temp file + rename). Defaults appear on first run."""

    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.warnings: list[str] = []
        self.data: dict = {}
        for name, factory in FILES.items():
            self.data[name] = self._load(name, factory)

    def path(self, name: str) -> Path:
        return self.dir / f"{name}.json"

    def _load(self, name: str, factory):
        p = self.path(name)
        default = factory()
        if not p.exists():
            self._write(p, default)
            return default
        try:
            data = json.loads(p.read_text())
            if type(data) is not type(default):
                raise ValueError(f"expected {type(default).__name__}")
        except (OSError, ValueError) as e:
            bad = p.with_suffix(f".corrupt-{int(time.time())}")
            try:
                p.replace(bad)
            except OSError:
                pass
            self.warnings.append(f"{p.name} was unreadable ({e}); moved to {bad.name} and reset to defaults")
            self._write(p, default)
            return default
        if isinstance(default, dict):  # new settings keys appear without clobbering the user's values
            for k, v in default.items():
                data.setdefault(k, v)
        return data

    @staticmethod
    def _write(path: Path, data) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)

    def get(self, name: str):
        return self.data[name]

    def save(self, name: str) -> None:
        with self.lock:
            self._write(self.path(name), self.data[name])

    # history.jsonl: one line per round, append only
    @property
    def history_path(self) -> Path:
        return self.dir / "history.jsonl"

    def append_history(self, entry: dict) -> None:
        with self.lock, self.history_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def history_count(self) -> int:
        try:
            with self.history_path.open() as f:
                return sum(1 for _ in f)
        except OSError:
            return 0


# ------------------------------------------------------------------------------------ sequences (D20)


def validate_sequence(steps) -> tuple[list[dict], list[dict], dict]:
    """Check a sequence per D20. Returns (stored steps, unrolled steps, info).

    Steps: ``{"pose": [4 values 0..1], "ms"}``, ``{"led": colour}``, ``{"voice": "play", "ms"}``,
    ``{"wait": ms}``, ``{"relay": bool}`` and ``{"repeat": n}``.
    ``repeat`` plays the steps since the start (or the previous repeat) n times in total.
    Timed steps are clamped to at least MIN_STEP_MS (D20) and the clamps are reported.
    """
    if not isinstance(steps, list) or not steps:
        raise GameError("steps must be a non-empty list")
    stored: list[dict] = []
    unrolled: list[dict] = []
    segment: list[dict] = []
    clamped: list[str] = []
    for n, raw in enumerate(steps, 1):
        if not isinstance(raw, dict):
            raise GameError(f"step {n}: must be an object")
        keys = set(raw) - {"ms"}
        if len(keys) != 1:
            raise GameError(f"step {n}: needs exactly one of pose, led, voice, wait, relay, repeat (got {sorted(raw)})")
        key = keys.pop()
        if "ms" in raw and key not in ("pose", "voice"):
            raise GameError(f"step {n}: 'ms' only goes with pose or voice")

        def ms_of(default: int, v=None) -> int:
            try:
                ms = int(raw.get("ms", default) if v is None else v)
            except (TypeError, ValueError) as e:
                raise GameError(f"step {n}: ms must be a number") from e
            if ms < MIN_STEP_MS:
                clamped.append(f"step {n}: {ms} ms raised to {MIN_STEP_MS} ms")
                ms = MIN_STEP_MS
            if ms > MAX_STEP_MS:
                raise GameError(f"step {n}: {ms} ms is longer than the {MAX_STEP_MS // 1000} s sequence limit")
            return ms

        if key == "pose":
            try:
                fingers = check_fingers(raw["pose"])
            except HardwareError as e:
                raise GameError(f"step {n}: {e}") from e
            step = {"pose": fingers, "ms": ms_of(150)}
        elif key == "led":
            try:
                step = {"led": check_colour(raw["led"])}
            except HardwareError as e:
                raise GameError(f"step {n}: {e}") from e
        elif key == "voice":
            if raw["voice"] != "play":
                raise GameError(f"step {n}: voice must be \"play\" (recording is a setup job, not a behaviour)")
            ms = ms_of(1000)
            step = {"voice": "play", "ms": min(ms, 30_000)}
        elif key == "wait":
            step = {"wait": ms_of(MIN_STEP_MS, raw["wait"])}
        elif key == "relay":
            step = {"relay": bool(raw["relay"])}
        elif key == "repeat":
            try:
                times = int(raw["repeat"])
            except (TypeError, ValueError) as e:
                raise GameError(f"step {n}: repeat must be a whole number") from e
            if not 1 <= times <= MAX_REPEAT:
                raise GameError(f"step {n}: repeat must be 1..{MAX_REPEAT} (D20)")
            if not segment:
                raise GameError(f"step {n}: repeat has nothing to repeat (put steps before it)")
            unrolled += [dict(s) for _ in range(times - 1) for s in segment]
            segment = []
            stored.append({"repeat": times})
            if len(unrolled) > MAX_UNROLLED_STEPS:
                raise GameError("sequence expands to too many steps")
            continue
        else:
            raise GameError(f"step {n}: unknown step type {key!r} (pose, led, voice, wait, relay, repeat)")
        stored.append(step)
        unrolled.append(step)
        segment.append(step)
        if len(unrolled) > MAX_UNROLLED_STEPS:
            raise GameError("sequence expands to too many steps")

    # timeline: total length (D20) and how long any finger stays closed (NFR-09)
    t = 0.0
    closed_since: list[float | None] = [None] * len(FINGERS)
    longest = 0.0
    for s in unrolled:
        if "pose" in s:
            for i, v in enumerate(s["pose"]):
                if v < CLOSED_BELOW:
                    if closed_since[i] is None:
                        closed_since[i] = t
                else:
                    closed_since[i] = None
            t += s["ms"] / 1000
        elif "wait" in s:
            t += s["wait"] / 1000
        for c in closed_since:
            if c is not None:
                longest = max(longest, t - c)
        if t > MAX_SEQUENCE_S:
            raise GameError(f"sequence is longer than {MAX_SEQUENCE_S:.0f} s once repeats are counted (D20)")
        if longest > HOLD_LIMIT_S:
            raise GameError(f"a finger would stay closed for {longest:.1f} s; the limit is {HOLD_LIMIT_S:.0f} s (NFR-09)")
    return stored, unrolled, {"total_ms": int(round(t * 1000)), "longest_closed_ms": int(round(longest * 1000)),
                              "steps_played": len(unrolled), "clamped": clamped}


# ------------------------------------------------------------------------------------ the game


def normalise_mode(mode: str) -> str:
    m = re.sub(r"[\s\-/]+", "_", str(mode).strip().lower())
    m = MODE_ALIASES.get(m, m)
    if m not in MODES:
        raise GameError(f"unknown mode {mode!r}; use one of {MODES} (or 'mercy')")
    return m


def _name_key(s: str) -> str:
    return str(s).strip().lower()


class Game:
    def __init__(self, hw, vision: VisionSource, banter: Banter, data_dir, sleep: Callable[[float], None] = time.sleep,
                 rng=None, vision_sources: dict[str, Callable[[], VisionSource]] | None = None):
        self.hw = hw
        self.vision = vision
        self.banter = banter
        self.sleep = sleep
        self.rng = rng or secrets.SystemRandom()
        self.vision_sources = vision_sources or {}
        self.store = Store(Path(data_dir))
        self.listeners: list[Callable[[dict], None]] = []
        self.current: str | None = None        # name of the identified player
        self.spectators = 0
        self.bribed = False                    # QR bribe card seen; lasts for the rest of the match
        self.session = {"robot": 0, "human": 0, "draws": 0, "rounds": 0}
        self.streak = {"by": None, "n": 0}
        self.sore_count = 0
        self.round_no = self.store.history_count()
        self._busy = threading.Lock()          # held from the start of a round to the end of its hold/relax
        self.last_after_error: str | None = None

    # ------------------------------------------------------------------ events
    def emit(self, event: str, **data) -> None:
        for fn in list(self.listeners):
            try:
                fn({"event": event, "t": time.time(), **data})
            except Exception:
                pass  # a screen must never break a round

    @property
    def settings(self) -> dict:
        return self.store.get("settings")

    def wait_idle(self) -> None:
        """Block until the post-round hold, hooks and relax are done (tests, shutdown)."""
        with self._busy:
            pass

    def _soft(self, fn, *a, **k):
        """Decoration (LEDs, speech, voice clip): a failure there shouldn't cost a round."""
        try:
            return fn(*a, **k)
        except HardwareError as e:
            self.last_after_error = str(e)
            return None

    # ------------------------------------------------------------------ moves
    def list_moves(self) -> list[dict]:
        return [dict(m) for m in self.store.get("moves")]

    def _move(self, name: str) -> dict | None:
        for m in self.store.get("moves"):
            if m["name"] == _name_key(name):
                return m
        return None

    def define_move(self, name: str, fingers, led, husky_id: int | None = None) -> dict:
        key = _name_key(name)
        if not re.fullmatch(r"[a-z0-9][a-z0-9 _\-]{0,19}", key):
            raise GameError("move name must be 1-20 characters: letters, digits, space, - or _")
        if key in RESERVED_MOVE_NAMES:
            raise GameError(f"{key!r} is reserved")
        try:
            f = check_fingers(fingers)
            c = check_colour(led)
        except HardwareError as e:
            raise GameError(str(e)) from e
        if husky_id is not None:
            husky_id = int(husky_id)
            if husky_id in RESERVED_HUSKY_IDS or husky_id < 0:
                raise GameError(f"husky_id {husky_id} is reserved (0 = unlearned, 4 = 'nothing', D9)")
            for m in self.store.get("moves"):
                if m["husky_id"] == husky_id and m["name"] != key:
                    raise GameError(f"husky_id {husky_id} already belongs to {m['name']!r}")
        with self.store.lock:
            existing = self._move(key)
            rec = {"name": key, "fingers": f, "led": c, "husky_id": husky_id}
            if existing:
                existing.update(rec)
            else:
                self.store.get("moves").append(rec)
            self.store.save("moves")
        return {**rec, "updated": existing is not None}

    def delete_move(self, name: str) -> dict:
        """Delete any move, including rock/paper/scissors; the rules that mention it go with it.
        ``load_ruleset('classic')`` brings the classic three back."""
        key = _name_key(name)
        with self.store.lock:
            if self._move(key) is None:
                raise GameError(f"no move called {name!r}")
            moves = self.store.get("moves")
            moves[:] = [m for m in moves if m["name"] != key]
            rules = self.store.get("rules")
            gone = [r for r in rules if key in (r["winner"], r["loser"])]
            rules[:] = [r for r in rules if r not in gone]
            self.store.save("moves")
            self.store.save("rules")
        return {"deleted": key, "rules_removed": gone}

    def perform_move(self, name: str) -> dict:
        m = self._move(name)
        if m is None:
            raise GameError(f"no move called {name!r}")
        self._busy.acquire()
        try:
            self.hw.pose(m["fingers"], self.settings["pose_ms"])
            self._soft(self.hw.led_fill, m["led"])
        except BaseException:
            self._busy.release()
            raise
        self._spawn_after(lambda: (self.sleep(2.0), self.hw.relax()))
        return {"performed": m["name"], "led": m["led"], "relaxes_in_s": 2.0}

    # ------------------------------------------------------------------ rules
    def list_rules(self) -> list[dict]:
        return [dict(r) for r in self.store.get("rules")]

    def define_rule(self, winner: str, loser: str, verb: str = "beats") -> dict:
        w, l = _name_key(winner), _name_key(loser)
        for n, label in ((w, winner), (l, loser)):
            if self._move(n) is None:
                raise GameError(f"no move called {label!r}; define_move first")
        if w == l:
            raise GameError("a move can't beat itself; same-vs-same is always a draw")
        verb = " ".join(str(verb).split())[:20] or "beats"
        with self.store.lock:
            rules = self.store.get("rules")
            if any(r["winner"] == l and r["loser"] == w for r in rules):
                raise GameError(f"contradiction: {l} already beats {w}; delete_rule({l!r}, {w!r}) first")
            for r in rules:
                if r["winner"] == w and r["loser"] == l:
                    r["verb"] = verb
                    self.store.save("rules")
                    return {**r, "updated": True}
            rec = {"winner": w, "loser": l, "verb": verb}
            rules.append(rec)
            self.store.save("rules")
        return {**rec, "updated": False}

    def delete_rule(self, winner: str, loser: str) -> dict:
        w, l = _name_key(winner), _name_key(loser)
        with self.store.lock:
            rules = self.store.get("rules")
            keep = [r for r in rules if not (r["winner"] == w and r["loser"] == l)]
            if len(keep) == len(rules):
                raise GameError(f"no rule {winner!r} beats {loser!r}")
            rules[:] = keep
            self.store.save("rules")
        return {"deleted": {"winner": w, "loser": l}, "rules_left": len(keep)}

    def load_ruleset(self, name: str = "classic") -> dict:
        if _name_key(name) != "classic":
            raise GameError("the only built-in ruleset is 'classic'")
        with self.store.lock:
            restored = []
            for m in default_moves():
                if self._move(m["name"]) is None:
                    taken = {x["husky_id"] for x in self.store.get("moves")}
                    self.store.get("moves").append({**m, "husky_id": m["husky_id"] if m["husky_id"] not in taken else None})
                    restored.append(m["name"])
            self.store.get("rules")[:] = classic_rules()
            self.store.save("moves")
            self.store.save("rules")
        return {"ruleset": "classic", "rules": self.list_rules(), "moves_restored": restored}

    # rule helpers (the logic lives in rps_lib.py, shared with the state-machine library)
    def _pool(self) -> list[str]:
        """The robot's fair pool: moves that appear in at least one rule."""
        return rps_lib.pool(self.store.get("moves"), self.store.get("rules"))

    def _rule(self, a: str, b: str) -> dict | None:
        return rps_lib.find_rule(self.store.get("rules"), a, b)

    def _counters(self, x: str) -> list[str]:
        return rps_lib.counters(self.store.get("moves"), self.store.get("rules"), x)

    def _losers_to(self, x: str) -> list[str]:
        return rps_lib.losers_to(self.store.get("moves"), self.store.get("rules"), x)

    # ------------------------------------------------------------------ sequences (D20)
    def define_sequence(self, name: str, steps) -> dict:
        key = _name_key(name)
        if not re.fullmatch(r"[a-z0-9][a-z0-9 _\-]{0,29}", key):
            raise GameError("sequence name must be 1-30 characters: letters, digits, space, - or _")
        stored, _unrolled, info = validate_sequence(steps)
        with self.store.lock:
            seqs = self.store.get("sequences")
            existing = next((s for s in seqs if s["name"] == key), None)
            if existing:
                existing["steps"] = stored
            else:
                seqs.append({"name": key, "steps": stored})
            self.store.save("sequences")
        return {"name": key, "updated": existing is not None, "steps": stored, **info}

    def list_sequences(self) -> list[dict]:
        out = []
        for s in self.store.get("sequences"):
            _, _, info = validate_sequence(s["steps"])
            out.append({"name": s["name"], "steps": s["steps"], "total_ms": info["total_ms"],
                        "attached": self._attachments(s["name"])})
        return out

    def _sequence(self, name: str) -> dict:
        for s in self.store.get("sequences"):
            if s["name"] == _name_key(name):
                return s
        raise GameError(f"no sequence called {name!r}")

    def _attachments(self, name: str) -> list[str]:
        out = [f"global {e}" for e, n in self.settings["hooks"].items() if n == name]
        for p in self.store.get("players"):
            out += [f"{p['name']} {e}" for e, n in (p.get("hooks") or {}).items() if n == name]
        return out

    def _run_sequence(self, seq: dict, sleep: Callable[[float], None] | None = None) -> dict:
        """Play a sequence. ``sleep`` lets the machine runtime pass a sleep that stop() can cut short."""
        sleep = sleep or self.sleep
        _, unrolled, info = validate_sequence(seq["steps"])
        done = 0
        try:
            for s in unrolled:
                if "pose" in s:
                    self.hw.pose(s["pose"], s["ms"])
                    sleep(s["ms"] / 1000)
                elif "led" in s:
                    self.hw.led_fill(s["led"])
                elif "voice" in s:
                    self.hw.voice_play(s["ms"])
                elif "wait" in s:
                    sleep(s["wait"] / 1000)
                elif "relay" in s:
                    self.hw.fill_light(s["relay"])
                done += 1
        finally:
            try:
                self.hw.relax()  # NFR-09: every sequence ends relaxed
            except HardwareError:
                pass
        return {"name": seq["name"], "steps_executed": done, "total_ms": info["total_ms"], "relaxed": True}

    def play_sequence(self, name: str) -> dict:
        seq = self._sequence(name)
        with self._busy:
            return self._run_sequence(seq)

    def delete_sequence(self, name: str) -> dict:
        seq = self._sequence(name)
        with self.store.lock:
            self.store.get("sequences").remove(seq)
            detached = self._attachments(seq["name"])
            for e, n in list(self.settings["hooks"].items()):
                if n == seq["name"]:
                    self.settings["hooks"][e] = None
            for p in self.store.get("players"):
                for e, n in list((p.get("hooks") or {}).items()):
                    if n == seq["name"]:
                        del p["hooks"][e]
            for f in ("sequences", "settings", "players"):
                self.store.save(f)
        return {"deleted": seq["name"], "detached": detached}

    def attach_sequence(self, event: str, name: str | None, player: str | None = None) -> dict:
        """Run sequence ``name`` on on_win / on_loss (the robot wins / loses) / on_reveal. ``name=None`` detaches.
        ``player`` attaches it to one player instead of globally; a player's hook wins over the global one."""
        if event not in EVENTS:
            raise GameError(f"event must be one of {EVENTS}")
        if name:
            name = self._sequence(name)["name"]
        with self.store.lock:
            if player:
                p = self._player_or_special(player)
                hooks = p.setdefault("hooks", {})
                if name:
                    hooks[event] = name
                else:
                    hooks.pop(event, None)
                self.store.save("players")
            else:
                self.settings["hooks"][event] = name
                self.store.save("settings")
        return {"event": event, "sequence": name, "player": player}

    def _hook(self, event: str, p: dict) -> str | None:
        name = (p.get("hooks") or {}).get(event) or self.settings["hooks"].get(event)
        if name and any(s["name"] == name for s in self.store.get("sequences")):
            return name
        return None

    # ------------------------------------------------------------------ players
    def _find_player(self, name: str) -> dict | None:
        for p in self.store.get("players"):
            if _name_key(p["name"]) == _name_key(name):
                return p
        return None

    def _new_player(self, name: str, face_id: int | None, mode: str) -> dict:
        p = {"name": name, "face_id": face_id, "mode": mode, "wins": 0, "losses": 0, "draws": 0, "history": []}
        self.store.get("players").append(p)
        return p

    def _player_or_special(self, name: str) -> dict:
        p = self._find_player(name)
        if p:
            return p
        key = _name_key(name)
        if key == "guest":
            p = self._new_player("guest", None, "fair")
        elif key == "stranger":
            p = self._new_player("stranger", None, normalise_mode(self.settings["stranger_mode"]))
        else:
            raise GameError(f"no player called {name!r}")
        self.store.save("players")
        return p

    def list_players(self) -> list[dict]:
        return [{**p, "history": p["history"][-10:]} for p in self.store.get("players")]

    def register_player(self, name: str, face_id: int | None = None) -> dict:
        """Prep only (D10): learn a face ahead of the demo. Not used at the table."""
        name = " ".join(str(name).split())[:20]
        if not name:
            raise GameError("name is required")
        if _name_key(name) in ("guest", "stranger") or self._find_player(name):
            raise GameError(f"{name!r} is already taken")
        used = {p["face_id"] for p in self.store.get("players") if p["face_id"] is not None}
        if face_id is None:
            face_id = next(i for i in range(1, 10_000) if i not in used)
        face_id = int(face_id)
        if face_id < 1 or face_id in used:
            raise GameError(f"face id {face_id} is invalid or already used")
        learned = self.vision.learn_face(face_id, name, self.settings["face_samples"])  # VisionError propagates
        with self.store.lock:
            p = self._new_player(name, face_id, normalise_mode(self.settings["default_mode"]))
            self.store.save("players")
        return {**p, "learned": learned}

    def scan_faces(self, window_s: float | None = None) -> dict:
        """Who is in front of the camera, as facts with no game rules (the machine ``identify`` action, any game):
        the main face matched against the shared player list. name is None for an unknown face or none."""
        r = self.vision.identify_face(float(self.settings["id_window_s"] if window_s is None else window_s))
        face_id = r.get("face_id")
        known = next((p for p in self.store.get("players") if face_id is not None and p["face_id"] == face_id), None)
        in_frame = int(r.get("max_faces_in_frame", 0))
        return {"name": known["name"] if known else None, "known": known is not None, "face_id": face_id,
                "faces_seen": r.get("faces_seen", []), "faces_in_frame": in_frame, "spectators": max(0, in_frame - 1)}

    def identify_player(self, check_bribe: bool = False) -> dict:
        """Setup scan (D10): face -> known player, else a stranger (default rigged-win). Optionally look for the
        bribe card (QR). Algorithm switching happens here, never in a round."""
        return self.seat_player(self.scan_faces(), check_bribe)

    def seat_player(self, scan: dict, check_bribe: bool = False) -> dict:
        """RPS meaning of a face scan (``scan_faces``): the known player or a stranger becomes the current player,
        spectators are counted, the bribe and sore-loser count reset; optionally look for the bribe card."""
        cfg = self.settings
        face_id = scan.get("face_id")
        known = self._find_player(scan["name"]) if scan.get("name") else None
        p = known or self._player_or_special("stranger")
        self.current = p["name"]
        self.spectators = max(0, int(scan.get("spectators", 0)))
        self.bribed = False
        self.sore_count = 0
        self.vision.show_text(p["name"] if known else "STRANGER")
        out = {"player": p["name"], "known": known is not None, "face_id": face_id, "mode": p["mode"],
               "faces_seen": scan.get("faces_seen", []), "spectators": self.spectators}
        if check_bribe:
            try:
                seen = bool(self.vision.see_id(QR, int(cfg["bribe_card_id"]), 1.0))
                out["bribe_check"] = "card seen" if seen else "no card"
            except Exception as e:  # QR algorithm id is unconfirmed on the device: fail soft
                seen = False
                out["bribe_check"] = f"could not check ({type(e).__name__}: {e})"
            self.bribed = seen
        out["bribed"] = self.bribed
        self.emit("identify", **out)
        return out

    def set_player_mode(self, name: str, mode: str) -> dict:
        m = normalise_mode(mode)
        with self.store.lock:
            p = self._player_or_special(name)
            p["mode"] = m
            p["grudge_active"] = False
            self.store.save("players")
        return {"player": p["name"], "mode": m}

    def _current_player(self) -> dict:
        return self._player_or_special(self.current or "guest")

    # ------------------------------------------------------------------ score
    def get_score(self, player: str | None = None) -> dict:
        out = {"session": dict(self.session)}
        if player:
            p = self._find_player(player)
            if not p:
                raise GameError(f"no player called {player!r}")
            out["player"] = {k: p[k] for k in ("name", "wins", "losses", "draws", "mode")}
        else:
            out["players"] = [{k: p[k] for k in ("name", "wins", "losses", "draws", "mode")}
                              for p in self.store.get("players")]
        out["current_player"] = self.current
        return out

    def reset_score(self, scope: str = "session", confirm: bool = False) -> dict:
        """scope: 'session', 'all' (every player's stats; needs confirm=True) or a player's name.
        history.jsonl (the log of past rounds) is never erased."""
        s = _name_key(scope)
        if s == "session":
            self.session = {"robot": 0, "human": 0, "draws": 0, "rounds": 0}
            self.streak = {"by": None, "n": 0}
            self.bribed = False
            self.sore_count = 0
            return {"reset": "session"}
        with self.store.lock:
            if s == "all":
                if not confirm:
                    return {"reset": None, "needs_confirm": True, "hint": "ask the user, then call with confirm=true"}
                self.reset_score("session")
                for p in self.store.get("players"):
                    self._zero(p)
                self.store.save("players")
                return {"reset": "all"}
            p = self._find_player(scope)
            if not p:
                raise GameError(f"no player called {scope!r}")
            self._zero(p)
            self.store.save("players")
        return {"reset": p["name"]}

    @staticmethod
    def _zero(p: dict) -> None:
        p.update(wins=0, losses=0, draws=0, history=[], grudge_active=False)

    def set_vision_source(self, name: str) -> dict:
        key = _name_key(name)
        if key == self.vision.name:
            return {"vision": key, "changed": False}
        factory = self.vision_sources.get(key)
        if factory is None:
            raise GameError(f"unknown vision source {name!r}; use one of {sorted(self.vision_sources) or [self.vision.name]}")
        self.vision = factory()  # MediaPipeVision raises VisionError until D15 is built
        return {"vision": key, "changed": True}

    def banter_preview(self, state: dict | None = None) -> dict:
        state = dict(state or {})
        if state.get("robot") and state.get("human") and "verb" not in state:
            r = self._rule(_name_key(state["robot"]), _name_key(state["human"]))
            if r:
                state["verb"] = r["verb"]
            else:
                r = self._rule(_name_key(state["human"]), _name_key(state["robot"]))
                state["verb"] = r["verb"] if r else None
        return {"line": self.banter.preview(state)}

    # ------------------------------------------------------------------ modes
    def _effective(self, mode: str, p: dict) -> tuple[str, bool]:
        """(how the robot plays this round, bribed). bribery / a seen bribe card = rigged-lose with gold LEDs."""
        return rps_lib.effective_mode(mode, p, self.bribed)

    def _choose(self, effective: str, p: dict, pool: list[str]) -> tuple[str, dict]:
        """Decide the robot's move for the modes that commit at shoot."""
        return rps_lib.choose_move(self.rng, effective, p, self.store.get("moves"), self.store.get("rules"), pool)

    def _rigged_move(self, effective: str, human: str, pool: list[str]) -> str:
        """Counter (rigged_win) or losing move (rigged_lose) for the human's throw."""
        return rps_lib.rigged_move(self.rng, effective, human, self.store.get("moves"), self.store.get("rules"), pool)

    # ------------------------------------------------------------------ the round
    def _spawn_after(self, fn: Callable[[], object]) -> None:
        """Run ``fn`` on a worker, then release the busy lock (held by the caller). Keeps play_round's
        return inside NFR-01 while the hold, hooks and relax finish."""
        def run():
            try:
                fn()
            except Exception as e:
                self.last_after_error = f"{type(e).__name__}: {e}"
                try:
                    self.hw.relax()
                except Exception:
                    pass
            finally:
                self._busy.release()

        try:
            threading.Thread(target=run, daemon=True, name="after-round").start()
        except BaseException:
            self._busy.release()
            raise

    def _wait_until(self, t: float) -> None:
        d = t - time.monotonic()
        if d > 0:
            self.sleep(d)

    def play_round(self, mode: str | None = None, player: str | None = None) -> dict:
        self._busy.acquire()  # waits for the previous round's hold/relax
        try:
            result, after = self._round(mode, player)
        except BaseException:
            try:
                self.hw.relax()
            except Exception:
                pass
            self._busy.release()
            raise
        self._spawn_after(after)
        return result

    def _round(self, mode_arg: str | None, player_arg: str | None):
        cfg = self.settings
        with self.store.lock:
            p = self._player_or_special(player_arg) if player_arg else self._current_player()
        pool = self._pool()
        if not pool:
            raise GameError("no rules defined, so there is nothing to play (load_ruleset('classic'))")
        mode = normalise_mode(mode_arg) if mode_arg else normalise_mode(p["mode"])
        effective, bribed = self._effective(mode, p)
        rigged = effective in ("rigged_win", "rigged_lose")
        id_to_move = {m["husky_id"]: m["name"] for m in self.store.get("moves") if m["husky_id"] is not None}
        period = max(0.06, int(cfg["countdown_period_ms"]) / 1000)
        max_replays = int(cfg["max_replays"])
        replays, warnings, reads = 0, [], []
        self.round_no += 1
        number = self.round_no
        self.emit("round_start", round=number, player=p["name"], mode=mode)

        robot = human = None
        read: dict = {}
        detail: dict = {}
        tm: dict = {}
        announced = honest = None

        while True:
            if self.hw.fingers is not None:
                self.hw.relax()  # every attempt starts from a relaxed hand (NFR-09)
            decided_t = None
            if not rigged:
                robot, detail = self._choose(effective, p, pool)
                decided_t = time.monotonic()
            if mode == "bluff":
                honest = self.rng.random() < 0.5
                announced = robot if honest else self.rng.choice([m for m in pool if m != robot] or [robot])
                self._soft(self.hw.speak, f"I'm throwing {announced}.")
            # Countdown: on-device LEDs, optional recorded clip, hand pump, beat words on the camera screen
            t0 = time.monotonic()
            self._soft(self.hw.led_countdown, 4, int(period * 1000), cfg["countdown_colours"])
            if cfg["voice_countdown"]:
                self._soft(self.hw.voice_play, int(period * 4000))
            self.emit("countdown", round=number, period_ms=int(period * 1000))
            half_ms = int(period * 500)
            for beat in range(3):
                self._wait_until(t0 + beat * period)
                if cfg["show_beats"]:
                    self.vision.show_text(BEAT_WORDS[beat])
                self.hw.pose(FIST, half_ms)
                self.emit("beat", round=number, beat=beat + 1, word=BEAT_WORDS[beat])
                self._wait_until(t0 + beat * period + period / 2)
                self.hw.pose(PUMP_OPEN, half_ms)
            self._wait_until(t0 + 3 * period)
            shoot_t = time.monotonic()
            if cfg["show_beats"]:
                self.vision.show_text(BEAT_WORDS[3])
            # Commit at shoot (fair-style) or hold the fist (rigged)
            commit_t = None
            if not rigged:
                self.hw.pose(self._move(robot)["fingers"], int(cfg["throw_ms"]))
                commit_t = time.monotonic()
                self.emit("commit", round=number, held=False)
            else:
                self.hw.pose(FIST, int(cfg["pose_ms"]))
                self.emit("hold_fist", round=number)
            self.sleep(float(cfg["read_delay_s"]))
            read_start_t = time.monotonic()
            read = self.vision.read_throw(float(cfg["read_window_s"]), id_to_move)
            read_done_t = time.monotonic()
            human = read.get("move")
            reads.append(read.get("votes"))
            self.emit("read", round=number, move=human, votes=read.get("votes"))
            known = human is not None and self._move(human) is not None
            if known:
                break
            # Replay: blame the glare when the robot is the one who would have won by looking (rigged modes)
            if replays >= max_replays:
                break
            replays += 1
            self.hw.relax()
            self.emit("replay", round=number, replays=replays)
            line = self.banter.line({"outcome": "replay", "replay": True, "glare": rigged,
                                     "player": self._display(p), "family": "fair", "bribed": bribed})
            if cfg["speak_banter"]:
                self._soft(self.hw.speak, line)
            self.sleep(0.4)

        if not known:
            return self._no_read(number, p, mode, effective, replays, read, reads, shoot_t, t0, warnings)

        if rigged:  # commit the counter / losing pose after the read
            robot = self._rigged_move(effective, human, pool)
            self.hw.pose(self._move(robot)["fingers"], int(cfg["snap_ms"]))
            commit_t = time.monotonic()
            self.emit("commit", round=number, held=True)

        # Score
        outcome, verb = rps_lib.outcome(self.store.get("rules"), robot, human)
        self.emit("score", round=number, outcome=outcome, robot=robot, human=human)

        # Report
        led = GOLD if bribed else self._move(robot)["led"]
        self._soft(self.hw.led_fill, led)
        grudge_triggered = False
        with self.store.lock:
            self._update_scores(p, outcome, human)
            if mode == "grudge" and outcome == "human" and not p.get("grudge_active"):
                p["grudge_active"] = True
                grudge_triggered = True
            self.store.save("players")
        sore = mode == "sore_loser" and outcome == "human"
        if sore:
            self.sore_count += 1
        state = {"outcome": outcome, "player": self._display(p), "robot": robot, "human": human, "verb": verb,
                 "robot_score": self.session["robot"], "human_score": self.session["human"],
                 "streak": self.streak["n"], "streak_by": self.streak["by"], "bribed": bribed,
                 "replay": replays > 0, "glare": False, "spectators": self.spectators,
                 "family": {"rigged_win": "win", "rigged_lose": "lose"}.get(effective, "fair")}
        if sore:
            state.update(family="sore", demand=3 if self.sore_count == 1 else 5)
        elif mode == "bluff":
            state.update(family="bluff", announced=announced, honest=honest)
        line = self.banter.line(state)
        spoken = line
        if getattr(self.hw, "simulated", False):
            # software gameplay: no hand to look at, so the camera screen and the voice give the robot's throw
            self.vision.show_text(f"ROBOT: {robot.upper()}")
            spoken = f"{robot.capitalize()}! {line}"
        if cfg["speak_banter"]:
            self._soft(self.hw.speak, spoken)

        t_end = time.monotonic()
        timings = {
            # all in ms from the start of the countdown
            "shoot_t": int(round((shoot_t - t0) * 1000)),
            "commit_t": int(round((commit_t - t0) * 1000)),
            "read_done_t": int(round((read_done_t - t0) * 1000)),
            "reveal_lag_ms": int(round((commit_t - read_done_t) * 1000)) if rigged else None,
            "committed_before_shoot": bool(not rigged and decided_t <= shoot_t and commit_t <= read_start_t),
            "returned_ms_after_shoot": int(round((t_end - shoot_t) * 1000)),
            "read_ms": read.get("read_ms"),
        }
        result = {
            "round": number, "hand": "simulated" if getattr(self.hw, "simulated", False) else "real",
            "player": p["name"], "mode": mode, "effective_mode": effective, "robot": robot,
            "human": human, "outcome": outcome, "verb": verb, "banter": line,
            "score": {"session": dict(self.session),
                      "player": {k: p[k] for k in ("name", "wins", "losses", "draws")}},
            "replays": replays, "timings": timings, "votes": read.get("votes"), "read": {
                k: read.get(k) for k in ("id", "share", "reads")},
        }
        if bribed:
            result["bribed"] = True
        if grudge_triggered:
            result["grudge_now_active"] = True
        if announced is not None:
            result["bluff"] = {"announced": announced, "honest": honest}
        if detail:
            result["prediction"] = detail
        if warnings or self.last_after_error:
            result["warnings"] = warnings + ([self.last_after_error] if self.last_after_error else [])
        self.store.append_history({"ts": time.time(), "round": number, "player": p["name"], "mode": mode,
                                   "effective_mode": effective, "robot": robot, "human": human,
                                   "outcome": outcome, "verb": verb, "replays": replays, "bribed": bribed,
                                   "timings": timings})
        self.emit("report", round=number, outcome=outcome, banter=line)

        hooks = [h for h in (self._hook("on_reveal", p),
                             self._hook("on_win", p) if outcome == "robot" else None,
                             self._hook("on_loss", p) if outcome == "human" else None) if h]
        if sore and not self._hook("on_loss", p) and any(s["name"] == "tantrum" for s in self.store.get("sequences")):
            hooks.append("tantrum")
        return result, lambda: self._after(hooks)

    def _display(self, p: dict) -> str:
        return "challenger" if p["name"] in ("guest", "stranger") else p["name"]

    def _after(self, hooks: list[str]) -> None:
        """Hold the result, then relax (NFR-09), then play any attached hook sequences."""
        hold = float(self.settings["result_hold_s"])
        hold = max(0.0, min(hold, HOLD_LIMIT_S - 0.5 - self.hw.held_closed_s()))
        self.sleep(hold)
        self.hw.relax()
        for name in hooks:
            try:
                self._run_sequence(self._sequence(name))
            except (GameError, HardwareError) as e:
                self.last_after_error = f"hook {name}: {e}"
        self.hw.relax()
        self.emit("idle")

    def _update_scores(self, p: dict, outcome: str, human: str) -> None:
        self.streak = rps_lib.update_scores(self.session, self.streak, p, outcome, human)

    def _no_read(self, number, p, mode, effective, replays, read, reads, shoot_t, t0, warnings):
        line = self.banter.line({"outcome": "no_read", "player": self._display(p), "family": "fair"})
        if self.settings["speak_banter"]:
            self._soft(self.hw.speak, line)
        self.emit("report", round=number, outcome="no_read", banter=line)
        result = {"round": number, "player": p["name"], "mode": mode, "effective_mode": effective, "robot": None,
                  "human": None, "outcome": "no_read", "verb": None, "banter": line,
                  "score": {"session": dict(self.session),
                            "player": {k: p[k] for k in ("name", "wins", "losses", "draws")}},
                  "replays": replays, "votes": read.get("votes"), "all_votes": reads,
                  "timings": {"returned_ms_after_shoot": int(round((time.monotonic() - shoot_t) * 1000))}}
        self.store.append_history({"ts": time.time(), "round": number, "player": p["name"], "mode": mode,
                                   "outcome": "no_read", "replays": replays})
        return result, lambda: self._after([])

    # ------------------------------------------------------------------ match
    def play_match(self, best_of: int = 5, mode: str | None = None, player: str | None = None) -> dict:
        best_of = int(best_of)
        if best_of < 1 or best_of % 2 == 0 or best_of > 21:
            raise GameError("best_of must be an odd number from 1 to 21")
        need = best_of // 2 + 1
        score = {"robot": 0, "human": 0}
        rounds: list[dict] = []
        winner = None
        was_bribed = self.bribed
        for _ in range(best_of * 5):
            r = self.play_round(mode, player)
            rounds.append(r)
            if r["outcome"] in score:
                score[r["outcome"]] += 1
            if max(score.values()) >= need:
                winner = max(score, key=score.get)
                break
        p = self._player_or_special(player) if player else self._current_player()
        line = None
        if winner:
            line = self.banter.line({"outcome": "match", "match": "won" if winner == "robot" else "lost",
                                     "player": self._display(p), "robot_score": score["robot"],
                                     "human_score": score["human"], "family": "fair"})
            if self.settings["speak_banter"]:
                self._soft(self.hw.speak, line)
        if was_bribed or self.bribed:
            self.bribed = False  # the bribe lasts for the rest of the match
        self.emit("match_end", winner=winner, score=score)
        return {"best_of": best_of, "winner": winner, "match_score": score, "banter": line,
                "player": p["name"], "rounds": rounds}
