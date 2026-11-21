"""The RPS function library for state machines (design doc §9.4, D10): the parts of a round that can't be written as
conditions, moved out of game.py's ``_round`` as named functions. ``env`` is the Game (hardware, data, rng, banter).

A machine calls them with ``{"call": "rps.choose_move", "args": {...}, "into": "choice"}``; fallback machine
``server/fallback_machines/rps.json`` strings them into the §9.4 round (countdown, commit-or-hold-fist, read, replay,
rigged snap, score, report, hold, hooks, best-of-N match). The logic itself is in rps_lib.py (shared with game.py).
Importing this module registers the functions.
"""

from __future__ import annotations

import time

import rps_lib
from game import GOLD, GameError, normalise_mode
from hardware import HOLD_LIMIT_S
from machine import MachineError, function
from vision import QR

FIST = [0.0, 0.0, 0.0, 0.0]


def _player(g, name: str) -> dict:
    with g.store.lock:
        return g._player_or_special(name)


@function("rps.begin_round", {"mode": False, "player": False})
def begin_round(g, mode=None, player=None) -> dict:
    """Start a round: resolve the player (default: the identified one) and mode (default: the player's, 'mercy' etc.
    normalised), work out how the robot plays it (effective mode, rigged, bribed) and number the round."""
    cfg = g.settings
    p = _player(g, player) if player else g._current_player()
    if not g._pool():
        raise GameError("no rules defined, so there is nothing to play (load_ruleset('classic'))")
    mode = normalise_mode(mode) if mode else normalise_mode(p["mode"])
    effective, bribed = g._effective(mode, p)
    g.round_no += 1
    g.emit("round_start", round=g.round_no, player=p["name"], mode=mode)
    return {"player": p["name"], "mode": mode, "effective": effective, "bribed": bribed,
            "rigged": effective in ("rigged_win", "rigged_lose"), "number": g.round_no,
            "period_ms": int(max(0.06, int(cfg["countdown_period_ms"]) / 1000) * 1000),
            "max_replays": int(cfg["max_replays"])}


@function("rps.choose_move", {"rd": True})
def choose_move(g, rd) -> dict:
    """The robot's move for the modes that commit at shoot: fair = random over the pool, predict = the counter to the
    player's likely throw (Markov, else frequency, else random). Rigged modes decide after the read: robot is null."""
    if rd["rigged"]:
        return {"robot": None, "detail": {}, "decided_t": None}
    robot, detail = g._choose(rd["effective"], _player(g, rd["player"]), g._pool())
    return {"robot": robot, "detail": detail, "decided_t": time.monotonic()}


@function("rps.bluff", {"rd": True, "choice": True})
def bluff(g, rd, choice) -> dict:
    """Bluff mode: announce a throw out loud (honest half the time) before the countdown. Other modes: nothing."""
    if rd["mode"] != "bluff":
        return {"announced": None, "honest": None}
    robot, pool = choice["robot"], g._pool()
    honest = g.rng.random() < 0.5
    announced = robot if honest else g.rng.choice([m for m in pool if m != robot] or [robot])
    g._soft(g.hw.speak, f"I'm throwing {announced}.")
    return {"announced": announced, "honest": honest}


@function("rps.shoot", {"rd": True, "choice": True})
def shoot(g, rd, choice) -> dict:
    """At 'shoot': commit the chosen move now (fair-style), or hold the fist (rigged modes decide after the read).
    The throw is sent at shoot (FR-09) but ramps over settings.throw_ms, so the hand lands with the human's rather
    than visibly ahead of it."""
    pose_ms = int(g.settings["pose_ms"])
    if not rd["rigged"]:
        g.hw.pose(g._move(choice["robot"])["fingers"], int(g.settings["throw_ms"]))
        commit_t = time.monotonic()
        g.emit("commit", round=rd["number"], held=False)
        return {"commit_t": commit_t}
    g.hw.pose(FIST, pose_ms)
    g.emit("hold_fist", round=rd["number"])
    return {"commit_t": None}


@function("rps.read_throw", {"rd": True})
def read_throw(g, rd) -> dict:
    """Read the human's throw (majority vote of the vision source). 'known' = it is one of the defined moves;
    start_t / done_t are monotonic seconds for the timing checks."""
    cfg = g.settings
    g.sleep(float(cfg["read_delay_s"]))
    id_to_move = {m["husky_id"]: m["name"] for m in g.store.get("moves") if m["husky_id"] is not None}
    start_t = time.monotonic()
    read = g.vision.read_throw(float(cfg["read_window_s"]), id_to_move)
    done_t = time.monotonic()
    human = read.get("move")
    g.emit("read", round=rd["number"], move=human, votes=read.get("votes"))
    return {"move": human, "known": human is not None and g._move(human) is not None, "id": read.get("id"),
            "share": read.get("share"), "reads": read.get("reads"), "votes": read.get("votes"),
            "read_ms": read.get("read_ms"), "start_t": start_t, "done_t": done_t}


@function("rps.replay_line", {"rd": True, "replays": True})
def replay_line(g, rd, replays) -> dict:
    """Banter for an unreadable throw (blames the glare when the robot was rigged to win by looking)."""
    g.emit("replay", round=rd["number"], replays=replays)
    p = _player(g, rd["player"])
    return {"line": g.banter.line({"outcome": "replay", "replay": True, "glare": rd["rigged"],
                                   "player": g._display(p), "family": "fair", "bribed": rd["bribed"]})}


@function("rps.rigged_move", {"rd": True, "human": True})
def rigged_move(g, rd, human) -> str:
    """The counter (rigged_win) or the losing move (rigged_lose) to the human's throw; a draw if the rules have none."""
    return g._rigged_move(rd["effective"], human, g._pool())


@function("rps.reveal", {"rd": True, "choice": True, "read": True, "commit": True})
def reveal(g, rd, choice, read, commit) -> dict:
    """Rigged modes: snap to the counter / losing pose right after the read. Fair modes: keep the committed move."""
    if rd["rigged"]:
        robot = g._rigged_move(rd["effective"], read["move"], g._pool())
        g.hw.pose(g._move(robot)["fingers"], int(g.settings["snap_ms"]))
        commit_t = time.monotonic()
        g.emit("commit", round=rd["number"], held=True)
        return {"robot": robot, "commit_t": commit_t}
    return {"robot": choice["robot"], "commit_t": commit["commit_t"]}


@function("rps.outcome", {"rd": True, "robot": True, "human": True})
def outcome(g, rd, robot, human) -> dict:
    """Who won, by the rules table: outcome robot / human / draw and the rule's verb."""
    result, verb = rps_lib.outcome(g.store.get("rules"), robot, human)
    g.emit("score", round=rd["number"], outcome=result, robot=robot, human=human)
    return {"outcome": result, "verb": verb}


@function("rps.update_scores", {"rd": True, "outcome": True, "human": True})
def update_scores(g, rd, outcome, human) -> dict:
    """Score and history update: session score, streak, the player's record and throw history; grudge flips on after a
    human win; sore_loser counts its demands."""
    grudge = False
    with g.store.lock:
        p = g._player_or_special(rd["player"])
        g._update_scores(p, outcome, human)
        if rd["mode"] == "grudge" and outcome == "human" and not p.get("grudge_active"):
            p["grudge_active"] = True
            grudge = True
        g.store.save("players")
    sore = rd["mode"] == "sore_loser" and outcome == "human"
    if sore:
        g.sore_count += 1
    return {"grudge_triggered": grudge, "sore": sore, "demand": 3 if g.sore_count == 1 else 5}


@function("rps.banter", {"rd": True, "robot": True, "human": True, "oc": True, "sc": True, "bf": True,
                         "replays": True})
def banter(g, rd, robot, human, oc, sc, bf, replays) -> dict:
    """The banter line for the result, plus what to say, what to show on the camera screen (software hand only: the
    robot's throw) and the LED colour (gold when bribed, else the robot's move colour). Records the line (no repeat in 10)."""
    p = _player(g, rd["player"])
    state = {"outcome": oc["outcome"], "player": g._display(p), "robot": robot, "human": human, "verb": oc["verb"],
             "robot_score": g.session["robot"], "human_score": g.session["human"],
             "streak": g.streak["n"], "streak_by": g.streak["by"], "bribed": rd["bribed"],
             "replay": replays > 0, "glare": False,
             "spectators": g.spectators,
             "family": {"rigged_win": "win", "rigged_lose": "lose"}.get(rd["effective"], "fair")}
    if sc["sore"]:
        state.update(family="sore", demand=sc["demand"])
    elif rd["mode"] == "bluff":
        state.update(family="bluff", announced=bf["announced"], honest=bf["honest"])
    line = g.banter.line(state)
    spoken, show = line, None
    if getattr(g.hw, "simulated", False):
        show = f"ROBOT: {robot.upper()}"
        spoken = f"{robot.capitalize()}! {line}"
    return {"line": line, "spoken": spoken, "show": show, "led": GOLD if rd["bribed"] else g._move(robot)["led"]}


@function("rps.finish_round", {"rd": True, "cd": True, "choice": True, "read": True, "rv": True, "oc": True,
                               "bn": True, "sc": True, "bf": True, "replays": True})
def finish_round(g, rd, cd, choice, read, rv, oc, bn, sc, bf, replays) -> dict:
    """Close a scored round: timings (committed_before_shoot, reveal_lag_ms, returned_ms_after_shoot), the result
    record, the history line, the 'report' event, and the hook sequences to play afterwards ({result, hooks})."""
    t_end = time.monotonic()
    rigged, p = rd["rigged"], _player(g, rd["player"])
    t0, shoot_t, commit_t = cd["t0"], cd["shoot_t"], rv["commit_t"]
    timings = {
        "shoot_t": int(round((shoot_t - t0) * 1000)),
        "commit_t": int(round((commit_t - t0) * 1000)),
        "read_done_t": int(round((read["done_t"] - t0) * 1000)),
        "reveal_lag_ms": int(round((commit_t - read["done_t"]) * 1000)) if rigged else None,
        "committed_before_shoot": bool(not rigged and choice["decided_t"] <= shoot_t and commit_t <= read["start_t"]),
        "returned_ms_after_shoot": int(round((t_end - shoot_t) * 1000)),
        "read_ms": read["read_ms"],
    }
    result = {
        "round": rd["number"], "hand": "simulated" if getattr(g.hw, "simulated", False) else "real",
        "player": p["name"], "mode": rd["mode"], "effective_mode": rd["effective"], "robot": rv["robot"],
        "human": read["move"], "outcome": oc["outcome"], "verb": oc["verb"], "banter": bn["line"],
        "score": {"session": dict(g.session), "player": {k: p[k] for k in ("name", "wins", "losses", "draws")}},
        "replays": replays, "timings": timings, "votes": read["votes"],
        "read": {"id": read["id"], "share": read["share"], "reads": read["reads"]},
    }
    if rd["bribed"]:
        result["bribed"] = True
    if sc["grudge_triggered"]:
        result["grudge_now_active"] = True
    if rd["mode"] == "bluff":
        result["bluff"] = {"announced": bf["announced"], "honest": bf["honest"]}
    if choice["detail"]:
        result["prediction"] = choice["detail"]
    if g.last_after_error:
        result["warnings"] = [g.last_after_error]
    g.store.append_history({"ts": time.time(), "round": rd["number"], "player": p["name"], "mode": rd["mode"],
                            "effective_mode": rd["effective"], "robot": rv["robot"], "human": read["move"],
                            "outcome": oc["outcome"], "verb": oc["verb"], "replays": replays, "bribed": rd["bribed"],
                            "timings": timings})
    g.emit("report", round=rd["number"], outcome=oc["outcome"], banter=bn["line"])
    hooks = [h for h in (g._hook("on_reveal", p),
                         g._hook("on_win", p) if oc["outcome"] == "robot" else None,
                         g._hook("on_loss", p) if oc["outcome"] == "human" else None) if h]
    if sc["sore"] and not g._hook("on_loss", p) and any(s["name"] == "tantrum" for s in g.store.get("sequences")):
        hooks.append("tantrum")
    return {"result": result, "hooks": hooks}


@function("rps.no_read", {"rd": True, "cd": True, "read": True, "replays": True, "all_votes": True})
def no_read(g, rd, cd, read, replays, all_votes) -> dict:
    """The throw was unreadable after the replays: banter, a history line, and a 'no_read' result ({result, hooks})."""
    p = _player(g, rd["player"])
    line = g.banter.line({"outcome": "no_read", "player": g._display(p), "family": "fair"})
    g.emit("report", round=rd["number"], outcome="no_read", banter=line)
    result = {"round": rd["number"], "player": p["name"], "mode": rd["mode"], "effective_mode": rd["effective"],
              "robot": None, "human": None, "outcome": "no_read", "verb": None, "banter": line,
              "score": {"session": dict(g.session), "player": {k: p[k] for k in ("name", "wins", "losses", "draws")}},
              "replays": replays, "votes": read["votes"], "all_votes": all_votes,
              "timings": {"returned_ms_after_shoot": int(round((time.monotonic() - cd["shoot_t"]) * 1000))}}
    g.store.append_history({"ts": time.time(), "round": rd["number"], "player": p["name"], "mode": rd["mode"],
                            "outcome": "no_read", "replays": replays})
    return {"result": result, "hooks": []}


@function("rps.hold_ms")
def hold_ms(g) -> int:
    """How long to hold the result before relaxing: settings.result_hold_s, trimmed so no finger stays closed past
    the 5 s limit (NFR-09)."""
    hold = float(g.settings["result_hold_s"])
    hold = max(0.0, min(hold, HOLD_LIMIT_S - 0.5 - g.hw.held_closed_s()))
    return int(hold * 1000)


@function("rps.match_banter", {"rd": True, "winner": True, "m_robot": True, "m_human": True})
def match_banter(g, rd, winner, m_robot, m_human) -> str:
    """The banter line for the end of a match."""
    p = _player(g, rd["player"])
    return g.banter.line({"outcome": "match", "match": "won" if winner == "robot" else "lost",
                          "player": g._display(p), "robot_score": m_robot, "human_score": m_human, "family": "fair"})


@function("rps.end_match", {"winner": True, "m_robot": True, "m_human": True})
def end_match(g, winner, m_robot, m_human) -> dict:
    """End of a match: the bribe lasts only for the match, then emit 'match_end'."""
    g.bribed = False
    g.emit("match_end", winner=winner, score={"robot": m_robot, "human": m_human})
    return {"winner": winner}


@function("rps.seat_player", {"who": True, "check_bribe": False})
def seat_player(g, who, check_bribe=False) -> dict:
    """RPS meaning of an ``identify`` scan (D10): the known player, else a stranger (settings.stranger_mode), becomes
    the current player; spectators counted, bribe and sore-loser count reset; optionally look for the bribe card."""
    if not isinstance(who, dict):
        raise MachineError("rps.seat_player needs the result of an identify action")
    return g.seat_player(who, bool(check_bribe))


@function("rps.identify_player", {"check_bribe": False})
def identify_player(g, check_bribe=False) -> dict:
    """Shortcut for an identify action followed by rps.seat_player."""
    return g.identify_player(bool(check_bribe))


@function("rps.player_mode", {"player": False})
def player_mode(g, player=None) -> str:
    """A player's mode (default: the identified player's)."""
    p = _player(g, player) if player else g._current_player()
    return normalise_mode(p["mode"])


# ------------------------------------------------------------------ driving the fallback machine


def run_rps(rt, machine: dict, mode=None, player=None, match=False, best_of=1, timeout: float = 30.0,
            identify=False) -> dict:
    """Run the fallback 'rps' machine to its end and return the round result (or the match summary).
    identify=True runs the setup face scan first (the machine's identify var)."""
    if match:
        best_of = int(best_of)
        if best_of < 1 or best_of % 2 == 0 or best_of > 21:
            raise GameError("best_of must be an odd number from 1 to 21")
    rt.start(machine, {"mode": mode, "player": player, "match": bool(match), "best_of": int(best_of),
                       "identify": bool(identify)})
    if not rt.wait_finished(timeout):
        raise MachineError("the rps machine did not finish in time")
    snap = rt.snapshot()
    if snap["status"] != "finished":
        raise MachineError(f"rps machine {snap['status']}: {snap['error']}")
    v = snap["vars"]
    if not match:
        return v["result"]
    p = _player(rt.env, v["rd"]["player"])
    return {"best_of": best_of, "winner": v["winner"], "match_score": {"robot": v["m_robot"], "human": v["m_human"]},
            "banter": v["mline"], "player": p["name"], "rounds": v["rounds"]}
