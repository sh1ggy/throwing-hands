"""Pure RPS logic, moved out of game.py so the engine and the RPS state machine share one copy
(design doc §9.4, D10, D11). No hardware, no I/O: moves, rules, players and an rng go in, a decision comes out.

game.py's ``_pool``, ``_choose``, ``_rigged_move``, ``_effective`` and ``_update_scores`` delegate here;
rps_functions.py exposes the same logic to machines as library functions.
"""

from __future__ import annotations

from collections import Counter

HISTORY_CAP = 200


def pool(moves: list[dict], rules: list[dict]) -> list[str]:
    """The robot's fair pool: moves that appear in at least one rule."""
    used = {n for r in rules for n in (r["winner"], r["loser"])}
    return [m["name"] for m in moves if m["name"] in used]


def find_rule(rules: list[dict], a: str, b: str) -> dict | None:
    for r in rules:
        if r["winner"] == a and r["loser"] == b:
            return r
    return None


def counters(moves, rules, x: str) -> list[str]:
    return [m for m in pool(moves, rules) if find_rule(rules, m, x)]


def losers_to(moves, rules, x: str) -> list[str]:
    return [m for m in pool(moves, rules) if find_rule(rules, x, m)]


def effective_mode(mode: str, p: dict, bribed: bool) -> tuple[str, bool]:
    """(how the robot plays this round, bribed). bribery / a seen bribe card = rigged-lose with gold LEDs."""
    if mode == "bribery" or bribed:
        return "rigged_lose", True
    if mode == "grudge":
        return ("rigged_win" if p.get("grudge_active") else "fair"), False
    if mode in ("bluff", "sore_loser"):
        return "fair", False
    return mode, False


def top(rng, c: Counter) -> str:
    best = max(c.values())
    return rng.choice(sorted(k for k, v in c.items() if v == best))


def choose_move(rng, effective: str, p: dict, moves, rules, pool_: list[str]) -> tuple[str, dict]:
    """Decide the robot's move for the modes that commit at shoot: fair = uniform over the pool; predict = the
    counter to the player's likely next throw (Markov on their last throw, else overall frequency, else random)."""
    if effective == "predict":
        known = {m["name"] for m in moves}
        hist = [h for h in p["history"] if h in known]
        pred, basis = None, "random"
        if len(hist) >= 3:
            succ = Counter(hist[i + 1] for i in range(len(hist) - 1) if hist[i] == hist[-1])
            if succ:
                pred, basis = top(rng, succ), "markov"
        if pred is None and hist:
            pred, basis = top(rng, Counter(hist)), "frequency"
        if pred:
            options = counters(moves, rules, pred)
            if options:
                return rng.choice(options), {"predicted": pred, "basis": basis}
        return rng.choice(pool_), {"predicted": None, "basis": "random"}
    return rng.choice(pool_), {}


def rigged_move(rng, effective: str, human: str, moves, rules, pool_: list[str]) -> str:
    """Counter (rigged_win) or losing move (rigged_lose) for the human's throw; the same move (a draw) if the
    rules have none; a pool move if the human's move isn't in any rule."""
    options = counters(moves, rules, human) if effective == "rigged_win" else losers_to(moves, rules, human)
    if options:
        return rng.choice(options)
    return human if human in pool_ else rng.choice(pool_)


def outcome(rules: list[dict], robot: str, human: str) -> tuple[str, str | None]:
    """('robot' | 'human' | 'draw', the rule's verb) for a robot move against a human move."""
    rule_r, rule_h = find_rule(rules, robot, human), find_rule(rules, human, robot)
    return ("robot" if rule_r else "human" if rule_h else "draw"), (rule_r or rule_h or {}).get("verb")


def update_scores(session: dict, streak: dict, p: dict, result: str, human: str) -> dict:
    """Add one round to the session and the player's record; returns the new streak ({'by', 'n'})."""
    session["rounds"] += 1
    if result == "robot":
        session["robot"] += 1
        p["losses"] += 1
    elif result == "human":
        session["human"] += 1
        p["wins"] += 1
    else:
        session["draws"] += 1
        p["draws"] += 1
    if result == "draw":
        streak = {"by": None, "n": 0}
    elif streak["by"] == result:
        streak = {"by": result, "n": streak["n"] + 1}
    else:
        streak = {"by": result, "n": 1}
    p["history"] = (p["history"] + [human])[-HISTORY_CAP:]
    return streak
