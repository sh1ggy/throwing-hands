"""State-machine language for the MCP runtime (agreed 2026-11-21; not in the design doc, see docs/TODO.md).

A game is one JSON document that Claude writes and patches while it runs. The server knows no games.

    {
      "name": "higher_lower",
      "description": "...",                       optional
      "gestures": ["up", "down"],                 optional: the camera gestures this machine needs ('nothing' is implied)
      "settings": {"gesture_window_s": 0.4, "gesture_stable": 2},   optional
      "view": {"title": "Higher or Lower", "hide": ["next"], "headline": ["score"]},   optional, for the live view
      "vars": {"score": 0},                       initial variables (JSON values)
      "initial": "start",
      "states": {
        "start": {
          "on_enter": [ <action>, ... ],          run on entry, in order
          "timeout_ms": 15000,                    optional; number or expression; fires the "timer" event
          "final": false,                         true = the machine ends here (no handlers allowed)
          "screen": [ <block>, ... ],             optional: the live view's Game tab while in this state (screen.py)
          "on": {                                 event -> ordered handlers, FIRST whose "if" holds wins
            "gesture:up": [ {"if": <expr>, "do": [<action>, ...], "to": "state"}, ... ],
            "timer": {"to": "start"},             one handler may be given without the list
            "enter": ..., "poke": ...
          }
        }
      }
    }

Events: ``gesture:<name>`` (a debounced camera read), ``timer`` (the state's timeout_ms), ``enter`` (right after
on_enter, so a state can branch on what on_enter computed), ``poke`` (Claude, with an optional ``$payload``).
A handler with ``to`` leaves the state (``to`` = the same state re-enters it); without ``to`` it stays.

Actions are objects with one action key (plus ``if`` and ``soft`` on any):
  speak, show, led, led_countdown, pose (+ms), perform, relax, play_sequence, wait, set, add, append, random,
  read, identify, call (+args, +into), emit, countdown, relay, voice.
Conditions/values are a small JSON expression language, never eval'd: ``$var`` references (dotted: ``$read.gesture``),
literals, and one-key operator objects such as {"gt": ["$next", "$number"]}. See ``reference()``.

``validate`` rejects a machine before it is applied; ``apply_patch`` edits one atomically (a bad patch raises and
changes nothing). The runtime that executes machines is machine_runtime.py; the RPS function library is
rps_functions.py.
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from hardware import HardwareError, check_colour, check_fingers


class MachineError(ValueError):
    """A bad machine, patch, expression or action (the MCP layer reports it to Claude)."""


BUILTIN_VARS = {"event", "payload", "state", "prev", "cfg"}   # always in scope; machine vars can't use these names
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MACHINE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,39}$")
REF_RE = re.compile(r"^\$[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_\-]+)*$")
TEMPLATE_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_\-]+)*)\}")
EVENT_KINDS = ("enter", "timer", "poke")
MAX_STATES = 60
MAX_ACTIONS = 60
MAX_DEPTH = 10
MAX_LIST = 64
MAX_TIMEOUT_MS = 600_000
SETTINGS_DEFAULT = {"gesture_window_s": 0.4, "gesture_stable": 2}

# ------------------------------------------------------------------------------------ function library


@dataclass
class Function:
    name: str
    fn: Callable
    args: dict          # argument name -> required?
    doc: str


FUNCTIONS: dict[str, Function] = {}


def function(name: str, args: dict | None = None):
    """Register a library function: ``fn(env, **args) -> JSON value``. ``env`` is the Game (hardware, data, rng)."""
    def deco(fn):
        FUNCTIONS[name] = Function(name, fn, dict(args or {}), " ".join((fn.__doc__ or "").split()))
        return fn
    return deco


# ------------------------------------------------------------------------------------ expressions
# Operators take a LIST of arguments, except the one-argument ones (not, len, abs, neg) which take the expression.
ONE_ARG = {"not", "len", "abs", "neg"}
OPS: dict[str, tuple[int, int]] = {
    "eq": (2, 2), "ne": (2, 2), "gt": (2, 2), "gte": (2, 2), "lt": (2, 2), "lte": (2, 2),
    "and": (1, 16), "or": (1, 16), "not": (1, 1), "in": (2, 2),
    "add": (2, 16), "sub": (2, 2), "mul": (2, 16), "div": (2, 2), "idiv": (2, 2), "mod": (2, 2),
    "min": (1, 16), "max": (1, 16), "abs": (1, 1), "neg": (1, 1), "len": (1, 1),
    "at": (2, 2), "concat": (1, 16), "if": (3, 3), "lit": (1, 1),
}


def lookup(path: str, scope: dict):
    """Resolve 'a.b.0' in scope (dict keys, list indexes); anything missing is None."""
    cur: Any = scope
    for part in path.split("."):
        if isinstance(cur, dict):
            if part in cur:
                cur = cur[part]
            elif part.lstrip("-").isdigit() and int(part) in cur:
                cur = cur[int(part)]
            else:
                return None
        elif isinstance(cur, list) and part.lstrip("-").isdigit():
            i = int(part)
            cur = cur[i] if -len(cur) <= i < len(cur) else None
        else:
            return None
    return cur


def _single(x: dict):
    if len(x) != 1:
        raise MachineError(f"an expression object has exactly one operator key, got {sorted(x)}")
    (op, args), = x.items()
    if op not in OPS:
        raise MachineError(f"unknown operator {op!r}; use one of {sorted(OPS)}")
    return op, args


def evaluate(x, scope: dict, depth: int = 0):
    """Evaluate an expression against ``scope``. Pure: no side effects, no eval/exec."""
    if depth > MAX_DEPTH:
        raise MachineError("expression is nested too deeply")
    if x is None or isinstance(x, (bool, int, float)):
        return x
    if isinstance(x, str):
        return lookup(x[1:], scope) if x.startswith("$") else x
    if isinstance(x, list):
        return [evaluate(i, scope, depth + 1) for i in x]
    if not isinstance(x, dict):
        raise MachineError(f"bad expression {x!r}")
    op, args = _single(x)
    if op == "lit":
        return copy.deepcopy(args)
    if op in ONE_ARG:
        v = evaluate(args, scope, depth + 1)
        try:
            return {"not": lambda: not v, "len": lambda: len(v), "abs": lambda: abs(v), "neg": lambda: -v}[op]()
        except TypeError as e:
            raise MachineError(f"{op}: cannot apply to {v!r}") from e
    if not isinstance(args, list):
        raise MachineError(f"{op} takes a list of arguments")
    lo, hi = OPS[op]
    if not lo <= len(args) <= hi:
        raise MachineError(f"{op} takes {lo}..{hi} arguments, got {len(args)}")
    if op == "and":
        for a in args:
            if not evaluate(a, scope, depth + 1):
                return False
        return True
    if op == "or":
        for a in args:
            if evaluate(a, scope, depth + 1):
                return True
        return False
    if op == "if":
        return evaluate(args[1] if evaluate(args[0], scope, depth + 1) else args[2], scope, depth + 1)
    v = [evaluate(a, scope, depth + 1) for a in args]
    try:
        if op == "eq":
            return v[0] == v[1]
        if op == "ne":
            return v[0] != v[1]
        if op == "gt":
            return v[0] > v[1]
        if op == "gte":
            return v[0] >= v[1]
        if op == "lt":
            return v[0] < v[1]
        if op == "lte":
            return v[0] <= v[1]
        if op == "in":
            return v[0] in v[1] if v[1] is not None else False
        if op == "add":
            return sum(v[1:], v[0])
        if op == "sub":
            return v[0] - v[1]
        if op == "mul":
            out = v[0]
            for n in v[1:]:
                out = out * n
            return out
        if op == "div":
            return v[0] / v[1]
        if op == "idiv":
            return v[0] // v[1]
        if op == "mod":
            return v[0] % v[1]
        if op in ("min", "max"):
            items = v[0] if len(v) == 1 and isinstance(v[0], list) else v
            return (min if op == "min" else max)(items)
        if op == "at":
            seq, i = v
            if isinstance(seq, dict):
                return seq.get(i)
            if isinstance(seq, (list, str)) and isinstance(i, int) and -len(seq) <= i < len(seq):
                return seq[i]
            return None
        if op == "concat":
            return "".join(fmt(i) for i in v)
    except (TypeError, ZeroDivisionError, ValueError) as e:
        raise MachineError(f"{op}: {e} (arguments {v!r})") from e
    raise MachineError(f"unhandled operator {op}")  # pragma: no cover


def fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


def render(text: str, scope: dict) -> str:
    """Fill {var} and {var.field} placeholders from the scope (unknown ones become empty)."""
    return TEMPLATE_RE.sub(lambda m: fmt(lookup(m.group(1), scope)), text)


def expr_refs(x, out: set | None = None) -> set:
    """Root variable names an expression refers to."""
    out = set() if out is None else out
    if isinstance(x, str) and x.startswith("$"):
        out.add(x[1:].split(".")[0])
    elif isinstance(x, list):
        for i in x:
            expr_refs(i, out)
    elif isinstance(x, dict) and len(x) == 1:
        (op, args), = x.items()
        if op != "lit":
            expr_refs(args, out)
    return out


# ------------------------------------------------------------------------------------ validation


@dataclass
class Context:
    """What a machine is checked against (the Game's moves and sequences, the camera's gestures)."""
    gestures: set | None = None       # valid gesture names (None = don't check)
    moves: set | None = None
    sequences: set | None = None
    functions: dict | None = None     # name -> Function (default: the registry)


MODIFIERS = {"if", "soft"}
EXTRA_KEYS = {"pose": {"ms"}, "call": {"args", "into"}}
ACTION_NAMES = ["speak", "show", "led", "led_countdown", "pose", "perform", "relax", "play_sequence", "wait", "set",
                "add", "append", "random", "read", "identify", "call", "emit", "countdown", "relay", "voice"]
COUNTDOWN_KEYS = {"beats", "period_ms", "colours", "words", "pump", "led", "led_beats", "voice", "show", "fist",
                  "open", "into"}


class _Check:
    """One validation run: collects every problem, then ``finish`` raises them together."""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.errs: list[str] = []
        self.known: set = set()

    def err(self, where: str, msg: str) -> None:
        self.errs.append(f"{where}: {msg}")

    # -- expressions
    def expr(self, x, where: str, depth: int = 0) -> None:
        if depth > MAX_DEPTH:
            return self.err(where, "expression is nested too deeply")
        if x is None or isinstance(x, (bool, int, float)):
            return
        if isinstance(x, str):
            if x.startswith("$") and not REF_RE.match(x):
                self.err(where, f"bad variable reference {x!r} (use $name or $name.field)")
            return
        if isinstance(x, list):
            if len(x) > MAX_LIST:
                return self.err(where, f"list longer than {MAX_LIST}")
            for i in x:
                self.expr(i, where, depth + 1)
            return
        if not isinstance(x, dict) or len(x) != 1:
            return self.err(where, f"an expression object has exactly one operator key, got {x!r}")
        (op, args), = x.items()
        if op not in OPS:
            return self.err(where, f"unknown operator {op!r}; use one of {sorted(OPS)}")
        if op == "lit":
            return
        lo, hi = OPS[op]
        if op in ONE_ARG:
            return self.expr(args, where, depth + 1)
        if not isinstance(args, list):
            return self.err(where, f"{op} takes a list of arguments")
        if not lo <= len(args) <= hi:
            return self.err(where, f"{op} takes {lo}..{hi} arguments, got {len(args)}")
        for a in args:
            self.expr(a, where, depth + 1)

    def text(self, x, where: str) -> None:
        """A string with {placeholders}, or an expression."""
        if isinstance(x, str) and not x.startswith("$"):
            return
        self.expr(x, where)

    def num(self, x, where: str, lo: float, hi: float) -> None:
        if isinstance(x, bool):
            return self.err(where, "must be a number")
        if isinstance(x, (int, float)):
            if not lo <= x <= hi:
                self.err(where, f"must be {lo}..{hi}, got {x}")
        elif isinstance(x, (str, dict)):
            self.expr(x, where)
        else:
            self.err(where, "must be a number or an expression")

    def name(self, x, where: str) -> bool:
        if not isinstance(x, str) or not NAME_RE.match(x) or x in BUILTIN_VARS:
            self.err(where, f"{x!r} is not a usable variable name (letters, digits, _; not {sorted(BUILTIN_VARS)})")
            return False
        return True

    # -- actions
    def actions(self, lst, where: str) -> list:
        if not isinstance(lst, list):
            self.err(where, "must be a list of actions")
            return []
        if len(lst) > MAX_ACTIONS:
            self.err(where, f"more than {MAX_ACTIONS} actions")
        for i, a in enumerate(lst, 1):
            self.action(a, f"{where} action {i}")
        return lst

    def action(self, a, where: str) -> None:
        if not isinstance(a, dict):
            return self.err(where, "an action is an object")
        mains = [k for k in a if k in ACTION_NAMES]
        if len(mains) != 1:
            return self.err(where, f"needs exactly one action key from {ACTION_NAMES}, got {sorted(a)}")
        k = mains[0]
        extra = set(a) - MODIFIERS - {k} - EXTRA_KEYS.get(k, set())
        if extra:
            return self.err(where, f"{k}: unexpected key(s) {sorted(extra)}")
        where = f"{where} ({k})"
        if "if" in a:
            self.expr(a["if"], where + " if")
        if "soft" in a and not isinstance(a["soft"], bool):
            self.err(where, "soft must be true or false")
        v = a[k]
        getattr(self, "a_" + k)(v, a, where)

    def a_speak(self, v, a, where):
        self.text(v, where)

    a_show = a_speak

    def a_led(self, v, a, where):
        if isinstance(v, dict):
            extra = set(v) - {"colour", "brightness"}
            if extra or "colour" not in v:
                return self.err(where, "led takes a colour or {colour, brightness}")
            self.colour(v["colour"], where)
            if "brightness" in v:
                self.num(v["brightness"], where, 0, 1)
        else:
            self.colour(v, where)

    def colour(self, c, where):
        if isinstance(c, str) and c.startswith("$") or isinstance(c, dict):
            return self.expr(c, where)
        try:
            check_colour(c)
        except HardwareError as e:
            self.err(where, str(e))

    def a_led_countdown(self, v, a, where):
        if not isinstance(v, dict) or not {"beats", "period_ms"} <= set(v) or set(v) - {"beats", "period_ms", "colours"}:
            return self.err(where, "led_countdown takes {beats, period_ms, colours?}")
        self.num(v["beats"], where, 1, 16)
        self.num(v["period_ms"], where, 20, 5000)
        if "colours" in v:
            if isinstance(v["colours"], list):
                for c in v["colours"]:
                    self.colour(c, where)
            else:
                self.expr(v["colours"], where)

    def a_pose(self, v, a, where):
        if isinstance(v, list) and not any(isinstance(i, (str, dict)) for i in v):
            try:
                check_fingers(v)
            except HardwareError as e:
                self.err(where, str(e))
        else:
            self.expr(v, where)
        if "ms" in a:
            self.num(a["ms"], where, 0, 2000)

    def a_perform(self, v, a, where):
        if isinstance(v, str) and not v.startswith("$"):
            if self.ctx.moves is not None and v.strip().lower() not in self.ctx.moves:
                self.err(where, f"unknown move {v!r} (known: {sorted(self.ctx.moves)})")
        else:
            self.expr(v, where)

    def a_relax(self, v, a, where):
        if v not in (True, "if_posed"):
            self.err(where, 'relax is true or "if_posed"')

    def a_play_sequence(self, v, a, where):
        if isinstance(v, str) and not v.startswith("$"):
            if self.ctx.sequences is not None and v.strip().lower() not in self.ctx.sequences:
                self.err(where, f"unknown sequence {v!r} (known: {sorted(self.ctx.sequences)})")
        else:
            self.expr(v, where)

    def a_wait(self, v, a, where):
        self.num(v, where, 0, 10_000)

    def _assign(self, v, where, noun):
        if not isinstance(v, dict) or not v:
            return self.err(where, f"{noun} takes an object {{var: expression}}")
        for k, e in v.items():
            if self.name(k, where):
                self.expr(e, f"{where} {k}")

    def a_set(self, v, a, where):
        self._assign(v, where, "set")

    a_add = a_set
    a_append = a_set

    def a_random(self, v, a, where):
        if not isinstance(v, dict) or "into" not in v or set(v) - {"into", "low", "high", "choice", "avoid"}:
            return self.err(where, "random takes {into, low, high, avoid?} or {into, choice: [..], avoid?}")
        self.name(v["into"], where)
        if "choice" in v:
            if "low" in v or "high" in v:
                self.err(where, "use choice or low/high, not both")
            self.expr(v["choice"], where)
        else:
            if "low" not in v or "high" not in v:
                self.err(where, "random needs low and high (or choice)")
            for key in ("low", "high"):
                if key in v:
                    self.expr(v[key], where)
        if "avoid" in v:
            self.expr(v["avoid"], where)

    def a_read(self, v, a, where):
        if not isinstance(v, dict) or "into" not in v or set(v) - {"into", "window_s"}:
            return self.err(where, "read takes {into, window_s?}")
        self.name(v["into"], where)
        if "window_s" in v:
            self.num(v["window_s"], where, 0.05, 5)

    def a_identify(self, v, a, where):
        if not isinstance(v, dict) or "into" not in v or set(v) - {"into", "window_s"}:
            return self.err(where, "identify takes {into, window_s?}")
        self.name(v["into"], where)
        if "window_s" in v:
            self.num(v["window_s"], where, 0.3, 5)

    def a_call(self, v, a, where):
        fns = self.ctx.functions if self.ctx.functions is not None else FUNCTIONS
        if not isinstance(v, str) or v not in fns:
            return self.err(where, f"unknown function {v!r}; known: {sorted(fns)}")
        spec = fns[v].args
        args = a.get("args", {})
        if not isinstance(args, dict):
            return self.err(where, "args must be an object")
        for missing in [k for k, req in spec.items() if req and k not in args]:
            self.err(where, f"{v} needs argument {missing!r}")
        for k, e in args.items():
            if k not in spec:
                self.err(where, f"{v} has no argument {k!r} (takes {sorted(spec)})")
            else:
                self.expr(e, f"{where} arg {k}")
        if "into" in a:
            self.name(a["into"], where)

    def a_emit(self, v, a, where):
        if isinstance(v, str):
            return
        if not isinstance(v, dict) or "event" not in v or set(v) - {"event", "data"}:
            return self.err(where, "emit takes a name or {event, data?}")
        if not isinstance(v["event"], str) or not NAME_RE.match(v["event"]):
            self.err(where, "event name must be letters, digits, _")
        if "data" in v:
            self.expr(v["data"], where)

    def a_countdown(self, v, a, where):
        if not isinstance(v, dict) or set(v) - COUNTDOWN_KEYS or "beats" not in v or "period_ms" not in v:
            return self.err(where, f"countdown takes {{beats, period_ms}} and optionally {sorted(COUNTDOWN_KEYS - {'beats', 'period_ms'})}")
        self.num(v["beats"], where, 1, 10)
        self.num(v["period_ms"], where, 60, 5000)
        for k, e in v.items():
            if k not in ("beats", "period_ms", "into"):
                self.expr(e, f"{where} {k}")
        if "into" in v:
            self.name(v["into"], where)

    def a_relay(self, v, a, where):
        self.expr(v, where)

    def a_voice(self, v, a, where):
        self.num(v, where, 0, 30_000)

    # -- the machine
    def finish(self, head: str):
        if self.errs:
            shown = self.errs[:12]
            more = f"\n  ... and {len(self.errs) - 12} more" if len(self.errs) > 12 else ""
            raise MachineError(head + ":\n  - " + "\n  - ".join(shown) + more)


def action_writes(a: dict) -> set:
    """Variable names an action assigns."""
    out: set = set()
    for k in ("set", "add", "append"):
        if isinstance(a.get(k), dict):
            out |= set(a[k])
    for k in ("random", "read", "identify", "countdown"):
        if isinstance(a.get(k), dict) and isinstance(a[k].get("into"), str):
            out.add(a[k]["into"])
    if isinstance(a.get("into"), str):
        out.add(a["into"])
    return out


def _handlers(raw, where: str, c: _Check) -> list[dict]:
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        c.err(where, "handlers are one {if, do, to} object or a non-empty list of them")
        return []
    out = []
    for i, h in enumerate(raw, 1):
        w = f"{where} handler {i}"
        if not isinstance(h, dict) or set(h) - {"if", "do", "to"}:
            c.err(w, f"a handler has keys if, do, to; got {h!r}")
            continue
        if "do" not in h and "to" not in h:
            c.err(w, "needs 'do' and/or 'to'")
        n = {}
        if "if" in h:
            c.expr(h["if"], w + " if")
            n["if"] = h["if"]
        n["do"] = c.actions(h.get("do", []), w + " do")
        if "to" in h:
            if not isinstance(h["to"], str):
                c.err(w, "'to' is a state name")
            n["to"] = h["to"]
        out.append(n)
    return out


def validate(machine, ctx: Context | None = None) -> dict:
    """Check a machine and return its normalised copy; raise MachineError listing every problem."""
    ctx = ctx or Context()
    c = _Check(ctx)
    if not isinstance(machine, dict):
        raise MachineError("a machine is a JSON object with name, vars, initial and states")
    m = copy.deepcopy(machine)
    extra = set(m) - {"name", "description", "gestures", "settings", "view", "vars", "initial", "states"}
    if extra:
        c.err("machine", f"unknown top-level key(s) {sorted(extra)}")
    name = m.get("name")
    if not isinstance(name, str) or not MACHINE_NAME_RE.match(name):
        c.err("name", "1-40 chars: lowercase letters, digits, - or _")
    try:
        json.dumps(m)
    except (TypeError, ValueError) as e:
        raise MachineError(f"machine isn't plain JSON: {e}") from e

    # gestures / settings / vars
    gestures = m.get("gestures")
    if gestures is not None:
        if not isinstance(gestures, list) or not all(isinstance(g, str) and re.fullmatch(r"[a-z0-9_]{1,20}", g) for g in gestures):
            c.err("gestures", "a list of names (a-z, 0-9, _)")
            gestures = None
    settings = m.get("settings", {})
    if not isinstance(settings, dict) or set(settings) - set(SETTINGS_DEFAULT):
        c.err("settings", f"an object with keys from {sorted(SETTINGS_DEFAULT)}")
    vars_ = m.get("vars", {})
    if not isinstance(vars_, dict):
        c.err("vars", "an object {name: initial value}")
        vars_ = {}
    for k in vars_:
        c.name(k, "vars")
    m["vars"] = vars_
    view = m.get("view")
    if view is not None:
        def hvar(h):  # a headline entry: "var" or {"var": "m_robot", "label": "Robot"}
            if isinstance(h, str):
                return h
            if isinstance(h, dict) and isinstance(h.get("var"), str) and set(h) <= {"var", "label"} \
                    and isinstance(h.get("label", ""), str):
                return h["var"]
            return None

        ok = isinstance(view, dict) and not set(view) - {"hide", "headline", "title", "screen", "colors"} \
            and isinstance(view.get("title", ""), str) and len(view.get("title", "")) <= 60 \
            and isinstance(view.get("hide", []), list) and all(isinstance(v, str) for v in view.get("hide", [])) \
            and isinstance(view.get("headline", []), list) and all(hvar(h) for h in view.get("headline", []))
        if not ok:
            c.err("view", 'an object {"title": text (60 max), "hide": [var names], '
                          '"headline": [var name or {"var": name, "label": text}], "screen": [blocks], '
                          '"colors": {gesture: "#rrggbb"}}')
            view = None
        else:
            heads = [hvar(h) for h in view.get("headline", [])]
            for k, names in (("hide", view.get("hide", [])), ("headline", heads)):
                unknown = [v for v in names if v not in vars_]
                if unknown:
                    c.err("view", f"{k} names vars that don't exist: {unknown}")
            both = set(view.get("hide", [])) & set(heads)
            if both:
                c.err("view", f"a var can't be both hidden and a headline: {sorted(both)}")
            if len(view.get("headline", [])) > 4:
                c.err("view", "at most 4 headline vars")
    states = m.get("states")
    if not isinstance(states, dict) or not states:
        c.err("states", "an object {name: state}; at least one")
        c.finish("machine rejected")
    if len(states) > MAX_STATES:
        c.err("states", f"more than {MAX_STATES} states")
    initial = m.get("initial")
    if initial not in states:
        c.err("initial", f"{initial!r} is not a state (states: {sorted(states)})")

    if gestures is not None:
        valid_g = set(gestures) | {"nothing"}
    elif ctx.gestures is not None:
        valid_g = set(ctx.gestures)
    else:
        valid_g = None

    import screen  # here, not at the top: screen imports this module
    colors = screen.check_colors(c, view["colors"]) if isinstance(view, dict) and "colors" in view else {}
    if isinstance(view, dict) and "screen" in view:
        screen.check(c, view["screen"], "view screen", valid_g, colors)

    norm: dict[str, dict] = {}
    for sname, st in states.items():
        w = f"state {sname!r}"
        if not isinstance(sname, str) or not NAME_RE.match(sname):
            c.err(w, "state names are letters, digits, _")
        if not isinstance(st, dict) or set(st) - {"on_enter", "on", "timeout_ms", "final", "description", "screen"}:
            c.err(w, "keys are on_enter, on, timeout_ms, final, description, screen")
            continue
        s = {"on_enter": c.actions(st.get("on_enter", []), w + " on_enter"), "on": {}}
        if "screen" in st:
            screen.check(c, st["screen"], w + " screen", valid_g, colors)
            s["screen"] = st["screen"]
        if "description" in st:
            s["description"] = str(st["description"])[:200]
        if st.get("final"):
            s["final"] = True
            if st.get("on") or "timeout_ms" in st:
                c.err(w, "a final state ends the machine, so it can't have handlers or a timeout")
        on = st.get("on", {})
        if not isinstance(on, dict):
            c.err(w, "'on' is an object {event: handlers}")
            on = {}
        for ev, raw in on.items():
            if ev in EVENT_KINDS:
                pass
            elif isinstance(ev, str) and ev.startswith("gesture:"):
                g = ev[8:]
                if valid_g is not None and g not in valid_g:
                    c.err(w, f"unknown gesture {g!r} in {ev!r} (the set is {sorted(valid_g)})")
            else:
                c.err(w, f"unknown event {ev!r}: use enter, timer, poke or gesture:<name>")
                continue
            s["on"][ev] = _handlers(raw, f"{w} on {ev}", c)
        if "timeout_ms" in st:
            c.num(st["timeout_ms"], w + " timeout_ms", 0, MAX_TIMEOUT_MS)
            s["timeout_ms"] = st["timeout_ms"]
            if "timer" not in s["on"]:
                c.err(w, "has timeout_ms but no 'timer' handler, so the timeout would do nothing")
        elif "timer" in s["on"]:
            c.err(w, "has a 'timer' handler but no timeout_ms, so it would never fire")
        norm[sname] = s

    # every variable that is read must exist somewhere (vars or written by an action)
    written = set(vars_)
    for s in norm.values():
        for a in _all_actions(s):
            written |= action_writes(a)
    known = written | BUILTIN_VARS
    for sname, s in norm.items():
        checks = [("on_enter", x) for a in s["on_enter"] for x in action_values(a)]
        for ev, hs in s["on"].items():
            for h in hs:
                checks += [(f"on {ev}", x) for x in ([h["if"]] if "if" in h else [])]
                checks += [(f"on {ev}", x) for a in h["do"] for x in action_values(a)]
        if "timeout_ms" in s:
            checks.append(("timeout_ms", s["timeout_ms"]))
        for where, x in checks:
            for r in sorted(value_roots(x) - known):
                hint = " (write each var by name, e.g. $score, not $vars.score)" if r == "vars" else ""
                c.err(f"state {sname!r} {where}", f"uses ${r} but no var or action defines it{hint}")

    # graph: targets, dead ends, reachability
    graph: dict[str, set] = {}
    for sname, s in norm.items():
        outs = set()
        for ev, hs in s["on"].items():
            for h in hs:
                if "to" in h:
                    if h["to"] not in norm:
                        c.err(f"state {sname!r} on {ev}", f"goes to unknown state {h['to']!r} (states: {sorted(norm)})")
                    else:
                        outs.add(h["to"])
        graph[sname] = outs
        if not outs and not s.get("final"):
            c.err(f"state {sname!r}", "is a dead end: no handler has a 'to' and it isn't final (add an exit or set final)")
    if initial in norm:
        seen, todo = {initial}, [initial]
        while todo:
            for t in graph.get(todo.pop(), ()):
                if t not in seen:
                    seen.add(t)
                    todo.append(t)
        for sname in norm:
            if sname not in seen:
                c.err(f"state {sname!r}", f"is unreachable from the initial state {initial!r}")
    c.finish(f"machine {name!r} rejected" if isinstance(name, str) else "machine rejected")

    # cross-references to the Game: handler gestures checked above; perform/sequence/function checked per action
    m["states"] = norm
    m["settings"] = {**settings}
    if gestures is not None:
        m["gestures"] = list(gestures)
    return m


def _all_actions(s: dict):
    yield from s["on_enter"]
    for hs in s["on"].values():
        for h in hs:
            yield from h["do"]


def action_values(a: dict) -> list:
    """Every expression or template inside one action (for the 'which variables does it read' check)."""
    out: list = [a["if"]] if "if" in a else []
    for k in ACTION_NAMES:
        if k not in a:
            continue
        v = a[k]
        if k in ("set", "add", "append") and isinstance(v, dict):
            out += list(v.values())
        elif k in ("random", "read", "identify", "countdown", "led_countdown", "led") and isinstance(v, dict):
            out += [x for key, x in v.items() if key != "into"]
        elif k == "emit" and isinstance(v, dict):
            out.append(v.get("data"))
        elif k == "call":
            out += list((a.get("args") or {}).values())
        elif k != "relax":
            out.append(v)
        if k == "pose" and "ms" in a:
            out.append(a["ms"])
    return out


def value_roots(x) -> set:
    """Root variable names an expression or {template} string reads."""
    out: set = set()
    if isinstance(x, str):
        if x.startswith("$"):
            out.add(x[1:].split(".")[0])
        else:
            out |= {m.split(".")[0] for m in TEMPLATE_RE.findall(x)}
    elif isinstance(x, list):
        for i in x:
            out |= value_roots(i)
    elif isinstance(x, dict):
        for k, v in x.items():
            if k != "lit":
                out |= value_roots(v)
    return out


def validate_for_start(m: dict, has_reader: bool) -> None:
    if not has_reader and any(ev.startswith("gesture:") for s in m["states"].values() for ev in s["on"]):
        raise MachineError("this machine listens for gestures but no camera reader is attached")


# ------------------------------------------------------------------------------------ patching

PATCH_OPS = ("set_state", "remove_state", "set_handler", "add_handler", "remove_handler", "set_on_enter",
             "set_timeout", "set_vars", "remove_vars", "set_initial", "set_settings", "set_gestures", "set_view")


def apply_patch(machine: dict, ops) -> tuple[dict, dict, list[str]]:
    """Apply patch operations to a copy of ``machine``. Returns (patched machine, live var updates, notes).

    The result is NOT validated here; the caller runs ``validate`` on it, so a bad patch changes nothing.
    """
    if not isinstance(ops, list) or not ops:
        raise MachineError(f"ops is a non-empty list of operations; each has an 'op' from {list(PATCH_OPS)}")
    m = copy.deepcopy(machine)
    live: dict = {}
    notes: list[str] = []
    states = m.setdefault("states", {})

    def need(op: dict, *keys):
        for k in keys:
            if k not in op:
                raise MachineError(f"op {n} ({kind}): missing {k!r}")

    def state_of(op: dict) -> dict:
        need(op, "state")
        if op["state"] not in states:
            raise MachineError(f"op {n} ({kind}): no state {op['state']!r} (states: {sorted(states)})")
        return states[op["state"]]

    for n, op in enumerate(ops, 1):
        if not isinstance(op, dict) or op.get("op") not in PATCH_OPS:
            raise MachineError(f"op {n}: needs 'op' from {list(PATCH_OPS)}, got {op!r}")
        kind = op["op"]
        if kind == "set_state":
            need(op, "state", "value")
            if not isinstance(op["value"], dict):
                raise MachineError(f"op {n} (set_state): value is a state object")
            notes.append(("replaced" if op["state"] in states else "added") + f" state {op['state']}")
            states[op["state"]] = copy.deepcopy(op["value"])
        elif kind == "remove_state":
            state_of(op)
            del states[op["state"]]
            notes.append(f"removed state {op['state']}")
        elif kind in ("set_handler", "add_handler", "remove_handler"):
            st = state_of(op)
            need(op, "event")
            on = st.setdefault("on", {})
            if kind == "remove_handler":
                if op["event"] not in on:
                    raise MachineError(f"op {n} (remove_handler): {op['state']} has no {op['event']!r} handler")
                del on[op["event"]]
            else:
                need(op, "handler" if kind == "add_handler" else "handlers")
                new = op["handler"] if kind == "add_handler" else op["handlers"]
                if kind == "set_handler":
                    on[op["event"]] = copy.deepcopy(new)
                else:
                    cur = on.get(op["event"], [])
                    cur = [cur] if isinstance(cur, dict) else list(cur)
                    idx = op.get("index", len(cur))
                    if not isinstance(idx, int) or not 0 <= idx <= len(cur):
                        raise MachineError(f"op {n} (add_handler): index must be 0..{len(cur)}")
                    cur.insert(idx, copy.deepcopy(new))
                    on[op["event"]] = cur
            notes.append(f"{kind} {op['state']} {op['event']}")
        elif kind == "set_on_enter":
            st = state_of(op)
            need(op, "actions")
            st["on_enter"] = copy.deepcopy(op["actions"])
            notes.append(f"set on_enter of {op['state']}")
        elif kind == "set_timeout":
            st = state_of(op)
            need(op, "ms")
            if op["ms"] is None:
                st.pop("timeout_ms", None)
            else:
                st["timeout_ms"] = op["ms"]
            notes.append(f"set timeout of {op['state']}")
        elif kind == "set_vars":
            need(op, "vars")
            if not isinstance(op["vars"], dict):
                raise MachineError(f"op {n} (set_vars): vars is an object")
            m.setdefault("vars", {}).update(copy.deepcopy(op["vars"]))
            if op.get("live", True):
                live.update(copy.deepcopy(op["vars"]))
            notes.append("set vars " + ", ".join(op["vars"]))
        elif kind == "remove_vars":
            need(op, "names")
            for v in op["names"]:
                m.get("vars", {}).pop(v, None)
            notes.append("removed vars " + ", ".join(map(str, op["names"])))
        elif kind == "set_initial":
            need(op, "state")
            m["initial"] = op["state"]
        elif kind == "set_settings":
            need(op, "settings")
            m["settings"] = {**m.get("settings", {}), **op["settings"]}
        elif kind == "set_gestures":
            need(op, "gestures")
            m["gestures"] = op["gestures"]
        elif kind == "set_view":
            need(op, "view")
            if op["view"] is None:
                m.pop("view", None)
            else:
                m["view"] = copy.deepcopy(op["view"])
            notes.append("set view")
    return m, live, notes


# ------------------------------------------------------------------------------------ storage


class MachineStore:
    """Machines in ``<data>/machines/<name>.json`` (atomic writes) over the fallback games in ``fallback_dir``.

    Fallbacks (``server/fallback_machines/``) are pre-written, tested games kept in the repo for when a game Claude
    writes live fails. Read-only: a machine Claude saves under the same name shadows it, and deleting that copy
    brings the fallback back."""

    def __init__(self, user_dir: Path, fallback_dir: Path | None = None):
        self.user_dir = Path(user_dir)
        self.fallback_dir = Path(fallback_dir) if fallback_dir else None

    def _read(self, p: Path) -> dict:
        return json.loads(p.read_text())

    def names(self) -> dict[str, str]:
        """name -> 'fallback' | 'user' (a user copy shadows the fallback)."""
        out: dict[str, str] = {}
        if self.fallback_dir and self.fallback_dir.is_dir():
            out.update({p.stem: "fallback" for p in self.fallback_dir.glob("*.json")})
        if self.user_dir.is_dir():
            out.update({p.stem: "user" for p in self.user_dir.glob("*.json")})
        return dict(sorted(out.items()))

    def get(self, name: str) -> dict:
        for d in (self.user_dir, self.fallback_dir):
            if d and (d / f"{name}.json").exists():
                try:
                    return self._read(d / f"{name}.json")
                except ValueError as e:
                    raise MachineError(f"machine file {name}.json is unreadable: {e}") from e
        raise MachineError(f"no machine called {name!r} (known: {list(self.names())})")

    def has_fallback(self, name: str) -> bool:
        return bool(self.fallback_dir and (self.fallback_dir / f"{name}.json").exists())

    def save(self, machine: dict) -> None:
        self.user_dir.mkdir(parents=True, exist_ok=True)
        p = self.user_dir / f"{machine['name']}.json"
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(machine, indent=2))
        os.replace(tmp, p)

    def delete(self, name: str) -> str:
        p = self.user_dir / f"{name}.json"
        if not p.exists():
            raise MachineError(f"no user machine {name!r} to delete" + (" (fallbacks can't be deleted)" if self.has_fallback(name) else ""))
        p.unlink()
        return "reverted to the fallback" if self.has_fallback(name) else "deleted"


def reference() -> dict:
    """The machine language in one object, for Claude (the machine_reference tool)."""
    return {
        "machine": "{name, description?, gestures?: [names], settings?, view?, vars: {..}, initial, states: {name: state}}",
        "view": "optional {title, hide: [var names], headline: [var name or {var, label}], screen: [blocks], "
                "colors: {gesture: '#rrggbb'}} for the live game view (show_game). title: the player-facing name "
                "(default: the machine name). screen (and a state's own 'screen', used while in that state): what the "
                "player-facing Game tab shows; see 'screen' below. With no screen the Game tab is built from headline. "
                "headline: up to 4 vars shown big on the player-facing Game tab, e.g. ['score'] or "
                "[{var: 'm_robot', label: 'Robot'}]. hide: vars never sent to the view, "
                "e.g. a hidden next number or the robot's committed move. The Game tab also shows the latest show text, "
                "what the robot said, which gestures the current state accepts and its timer, so write show/speak "
                "lines for the player; the Nerd tab draws the state graph, all non-hidden vars and the events",
        "gestures": "names a-z, 0-9, _ (no spaces), in camera-ID order; 'nothing' (empty zone) is added automatically, "
                    "don't list it",
        "state": {"on_enter": "[actions]", "timeout_ms": "number or expression; needs a 'timer' handler",
                  "final": "true ends the machine ('on' may be left out)", "on": "{event: handler | [handlers]}"},
        "events": {"gesture:<name>": "a debounced camera read (names from the gesture set; 'nothing' = empty zone)",
                   "timer": "the state's timeout_ms elapsed", "enter": "right after on_enter, on EVERY entry including re-entry (branch on what it computed, or move on)",
                   "poke": "Claude's poke tool, payload in $payload"},
        "handler": "{if?: expr, do?: [actions], to?: state}  first handler whose 'if' holds wins; 'to' leaves the state",
        "scope": "each var by its own name ($score, $next; there is NO $vars prefix), plus $event, $payload, $state, "
                 "$prev, $cfg.<game setting>; dotted paths reach into dicts/lists ($read.gesture, $who.name)",
        "actions": {
            "speak": "text with {var} templates (laptop voice, opt-in)",
            "show": "text with {var} templates on the camera screen (cut to 20 chars after filling in)",
            "led": "'#rrggbb' or {colour, brightness}", "led_countdown": "{beats, period_ms, colours?} (non-blocking)",
            "pose": "[thumb, index, middle, ring+little] 0..1, plus sibling ms", "perform": "move name: its pose and LED",
            "relax": "true or 'if_posed'", "play_sequence": "sequence name (blocks until done, relaxes after)",
            "wait": "ms", "set": "{var: expr}", "add": "{var: number}", "append": "{var: expr} (to a list)",
            "random": "{into, low, high, avoid?} or {into, choice: [..], avoid?}; avoid is an expression, e.g. \"$current\"", "read": "{into, window_s?} camera gesture now",
            "identify": "{into, window_s?} face scan (default 1.5 s) against the shared player list -> {name (null if "
                        "unknown or no face), known, face_id, faces_seen, faces_in_frame, spectators}. Game rules stay "
                        "in the machine or its library (RPS: rps.seat_player). Switches the camera to faces and back, "
                        "so put it in a setup state, not mid-read; no gesture events fire during it",
            "call": "'library.function' with sibling args {..} and into", "emit": "name or {event, data}",
            "countdown": "{beats, period_ms, words?, colours?, pump?, led?, show?, voice?, into?} timed beats, pump hand, 'shoot' at the end",
            "relay": "bool (fill light)", "voice": "ms of the ISD1820 clip",
            "modifiers": "any action may carry 'if': expr and 'soft': true/false (soft = a failure only warns)"},
        "expressions": {"literals": "numbers, true/false/null, plain strings, lists", "refs": "'$name', '$name.field'",
                        "operators": "{op: [args]}: " + ", ".join(sorted(o for o in OPS if o not in ONE_ARG)) +
                                     "; one-arg (no list): " + ", ".join(sorted(ONE_ARG)),
                        "escape": "{lit: '$not a ref'}"},
        "functions": {n: {"args": {a: ("required" if r else "optional") for a, r in f.args.items()}, "doc": f.doc}
                      for n, f in sorted(FUNCTIONS.items())},
        "screen": __import__("screen").__doc__.split("Blocks:", 1)[1].strip(),
        "settings": {"gesture_window_s": "camera majority-vote window per poll (0.4)",
                     "gesture_stable": "windows in a row that must agree before gesture:<name> fires (2)"},
    }
