"""Screens: what the player-facing Game tab of the live view shows, as data in the machine (agreed 2026-11-21; not in
the design doc, see docs/TODO.md).

A machine's ``view.screen`` (the default) or a state's ``screen`` (while the machine is in that state) is a list of
blocks. The server resolves them against the live variables with the machine's own expression language
(``{var}`` templates, ``$refs``, operators), so the view gets plain, ready-to-draw blocks and never evaluates
anything. Like the rest of a machine, screens are validated before they're applied and can be patched live.

Blocks: one kind key each, plus the optional modifiers ``if`` (expression; the block shows only when it holds),
``color`` and ``size``.

    {"text": "Round {round}"}                     a line of text ({var} templates, or an expression)
    {"big": "$score", "label": "Score"}           a large value with a label
    {"row": [ <block>, ... ]}                     blocks side by side (no rows inside rows)
    {"gestures": "accepted", "label": "Your move"}  gesture chips: "accepted" = the ones the current state handles,
                                                  or a list of names; the last one the camera saw is lit
    {"timer": true, "label": "Hurry"}             the current state's timeout, counting down
    {"said": true}                                the robot's last spoken line
    {"shown": true}                               the robot's last ``show`` text (its camera screen)
    {"seen": true, "label": "Robot saw"}          the last gesture the camera read in this state
    {"bar": "$lives", "max": 3, "label": "Lives"} a progress bar (value 0..max)
    {"list": "$history", "label": "So far"}       a list variable, newest last (up to 20 items)

``color``: "#rrggbb", a gesture name (its colour from ``view.colors``), good, bad, warn, dim, accent, or an
expression that gives one of those (e.g. "$call").
``size``: s, m, l or xl.
"""

from __future__ import annotations

import re

from machine import MachineError, evaluate, fmt, render

KINDS = ("text", "big", "row", "gestures", "timer", "said", "shown", "seen", "bar", "list")
MODIFIERS = {"if", "color", "size", "label", "max"}
SIZES = ("s", "m", "l", "xl")
NAMED_COLORS = ("good", "bad", "warn", "dim", "accent")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_BLOCKS = 30
PALETTE = ["#60a5fa", "#f87171", "#4ade80", "#fbbf24", "#c084fc", "#f472b6", "#2dd4bf", "#a3e635"]
NOTHING_COLOR = "#a1a1aa"


# ------------------------------------------------------------------------------------ validation

def check(c, blocks, where: str, gestures: set | None, colors: dict, depth: int = 0) -> None:
    """Validate a screen with machine.validate's checker ``c`` (collects errors, raises nothing itself)."""
    if not isinstance(blocks, list):
        return c.err(where, "a screen is a list of blocks")
    if len(blocks) > MAX_BLOCKS:
        c.err(where, f"more than {MAX_BLOCKS} blocks")
    for i, b in enumerate(blocks):
        w = f"{where}[{i}]"
        if not isinstance(b, dict):
            c.err(w, "a block is an object such as {\"text\": \"...\"}")
            continue
        kinds = [k for k in b if k in KINDS]
        extra = set(b) - set(KINDS) - MODIFIERS
        if len(kinds) != 1 or extra:
            c.err(w, f"one kind key from {list(KINDS)} plus optional {sorted(MODIFIERS)}; got {sorted(b)}")
            continue
        (kind,) = kinds
        v = b[kind]
        if "if" in b:
            c.expr(b["if"], w + " if")
        if "size" in b and b["size"] not in SIZES:
            c.err(w, f"size is one of {list(SIZES)}")
        if "color" in b:
            col = b["color"]
            if isinstance(col, dict) or (isinstance(col, str) and col.startswith("$")):
                c.expr(col, w + " color")  # resolved live, e.g. "$call" -> that gesture's colour
            elif not (isinstance(col, str) and (HEX_RE.match(col) or col in NAMED_COLORS or col in colors
                                                 or (gestures is not None and col in gestures))):
                c.err(w, f"color is #rrggbb, one of {list(NAMED_COLORS)}, a gesture name, or an expression giving one")
        if "label" in b:
            c.text(b["label"], w + " label")
        if "max" in b and kind != "bar":
            c.err(w, "max only goes with bar")
        if kind == "row":
            if depth:
                c.err(w, "no rows inside rows")
            else:
                check(c, v, w + " row", gestures, colors, depth + 1)
        elif kind in ("text", "big"):
            c.text(v, w)
            if isinstance(v, str) and len(v) > 200:
                c.err(w, "text is at most 200 characters")
        elif kind == "gestures":
            if v != "accepted":
                if not isinstance(v, list) or not all(isinstance(g, str) for g in v):
                    c.err(w, 'gestures is "accepted" or a list of gesture names')
                elif gestures is not None and set(v) - gestures:
                    c.err(w, f"unknown gesture(s) {sorted(set(v) - gestures)}")
        elif kind in ("timer", "said", "shown", "seen"):
            if v is not True:
                c.err(w, f'write {{"{kind}": true}}')
        elif kind == "bar":
            c.expr(v, w)
            if "max" not in b:
                c.err(w, "bar needs max")
            else:
                c.expr(b["max"], w + " max")
        elif kind == "list":
            c.expr(v, w)


def check_colors(c, colors) -> dict:
    if not isinstance(colors, dict) or not all(isinstance(k, str) and isinstance(v, str) and HEX_RE.match(v)
                                               for k, v in colors.items()):
        c.err("view colors", 'an object {gesture name: "#rrggbb"}')
        return {}
    return colors


# ------------------------------------------------------------------------------------ resolving

def gesture_colors(machine: dict) -> dict:
    """Every gesture the machine uses -> a colour: view.colors first, then a fixed palette in declaration order."""
    names = list(machine.get("gestures") or [])
    for st in machine["states"].values():
        for ev in st.get("on", {}):
            if ev.startswith("gesture:") and ev[8:] not in names:
                names.append(ev[8:])
    given = (machine.get("view") or {}).get("colors", {})
    out, i = {}, 0
    for g in names + ["nothing"]:
        if g in given:
            out[g] = given[g]
        elif g == "nothing":
            out[g] = NOTHING_COLOR
        else:
            out[g] = PALETTE[i % len(PALETTE)]
            i += 1
    return out


def default_screen(machine: dict) -> list:
    """The screen a machine gets when it defines none: its headline vars, what the robot shows and says, the gestures
    the current state takes, the timer, and what the camera saw."""
    heads = []
    for h in (machine.get("view") or {}).get("headline", []):
        var, label = (h, h) if isinstance(h, str) else (h["var"], h.get("label", h["var"]))
        heads.append({"big": f"${var}", "label": label.replace("_", " ")})
    return ([{"row": heads}] if heads else []) + [
        {"shown": True, "size": "xl"}, {"said": True}, {"gestures": "accepted", "label": "Your move"},
        {"timer": True}, {"seen": True, "label": "Robot saw", "size": "s", "color": "dim"}]


def _value(x, scope):
    if isinstance(x, str) and not x.startswith("$"):
        return render(x, scope)
    return evaluate(x, scope)


def resolve(machine: dict, state: str | None, scope: dict, events: list, status: str) -> tuple[list, str]:
    """The screen for the current state, as plain blocks. Returns (blocks, source: state|machine|auto). A block whose
    expression fails becomes {"kind": "error"} so one bad template never blanks the screen."""
    st = machine["states"].get(state) or {}
    if "screen" in st:
        blocks, source = st["screen"], "state"
    elif "screen" in (machine.get("view") or {}):
        blocks, source = machine["view"]["screen"], "machine"
    else:
        blocks, source = default_screen(machine), "auto"
    colors = gesture_colors(machine)
    accepted = [ev[8:] for ev in st.get("on", {}) if ev.startswith("gesture:")]
    outs = [e for e in events if e.get("output")]
    said = next((e.get("text") for e in reversed(outs) if e.get("event") == "speak"), None)
    shown = next((e.get("text") for e in reversed(outs) if e.get("event") == "show"), None)
    seen_ev = next((e for e in reversed(events) if not e.get("output") and str(e.get("event", "")).startswith("gesture:")), None)
    seen = seen_ev["event"][8:] if seen_ev and seen_ev.get("state") == state else None

    def color(x):
        name = evaluate(x, scope) if isinstance(x, dict) or (isinstance(x, str) and x.startswith("$")) else x
        if not isinstance(name, str):
            return None
        if name in colors:
            return colors[name]
        return name if HEX_RE.match(name) or name in NAMED_COLORS else None

    def one(b) -> dict | None:
        try:
            if "if" in b and not evaluate(b["if"], scope):
                return None
            (kind,) = [k for k in b if k in KINDS]
            out = {"kind": kind}
            for k in ("size",):
                if k in b:
                    out[k] = b[k]
            if "color" in b and color(b["color"]):
                out["color"] = color(b["color"])
            if "label" in b:
                out["label"] = fmt(_value(b["label"], scope))
            v = b[kind]
            if kind == "row":
                out["items"] = [r for r in (one(x) for x in v) if r is not None]
                return out if out["items"] else None
            if kind in ("text", "big"):
                out["text"] = fmt(_value(v, scope))
            elif kind == "gestures":
                names = accepted if v == "accepted" else v
                if not names or status != "running":
                    return None
                out["items"] = [{"name": g, "color": colors.get(g, NOTHING_COLOR), "lit": g == seen} for g in names]
            elif kind == "timer":
                if status != "running" or "timeout_ms" not in st:
                    return None
            elif kind == "said":
                if not said:
                    return None
                out["text"] = said
            elif kind == "shown":
                if not shown:
                    return None
                out["text"] = shown
            elif kind == "seen":
                if not seen:
                    return None
                out["text"], out["gesture_color"] = seen, colors.get(seen, NOTHING_COLOR)
            elif kind == "bar":
                val, mx = evaluate(v, scope), evaluate(b["max"], scope)
                out["value"], out["max"] = float(val or 0), float(mx or 0)
            elif kind == "list":
                items = evaluate(v, scope)
                out["items"] = [fmt(i) for i in (items if isinstance(items, list) else [items])][-20:]
            return out
        except (MachineError, TypeError, ValueError, KeyError) as e:
            return {"kind": "error", "text": f"{e}"[:120]}

    resolved = [r for r in (one(b) for b in blocks) if r is not None]
    if status == "finished" and source == "auto":
        resolved.append({"kind": "text", "text": "Game over", "size": "xl", "color": "accent"})
    return resolved, source
