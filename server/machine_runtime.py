"""Runs a state machine (machine.py) on a background thread: the server runs the loop, Claude is not in it.

    rt = Runtime(game, reader=read_fn)       # game: the Game (hardware, vision, data, rng); reader(window_s) -> gesture read
    rt.start(validated_machine, {"mode": "fair"})
    rt.poke({"x": 1}); rt.state(); rt.patch(...); rt.stop()

Determinism: one thread dispatches one event at a time, and a transition runs its actions and follows its ``to``
(then the new state's ``enter`` handlers) in the same call, so a read -> transition -> pose chain has no poll tick in
it (RPS rigged-win needs the counter-pose within 250 ms of the read, NFR-01/T9). Events that arrive while actions
run (a poke during a countdown) queue and are handled in order afterwards.

Gesture events: while the current state has ``gesture:*`` handlers the loop reads the camera (``reader`` = a majority
vote over ``gesture_window_s``). A gesture fires once ``gesture_stable`` consecutive confident windows agree, and not
again until a different window has been seen (a held thumb doesn't re-fire across turns).

Faults (an action raising) stop the machine, relax the hand (NFR-09) and are reported by ``state()``. ``sleep`` and
``clock`` are injectable so tests run fast, like Game.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from collections import deque
from typing import Callable

from hardware import FINGERS, HardwareError
from machine import (FUNCTIONS, MAX_TIMEOUT_MS, MAX_WAIT_MS, MIN_TIMEOUT_MS, SETTINGS_DEFAULT, MachineError, MachineStore,
                     Context, apply_patch, evaluate, render, validate, validate_for_start)
from vision import VisionError

MAX_CHAIN = 200          # transitions followed without waiting for an event (stops enter-handler loops)
MAX_QUEUE = 50
SOFT = {"speak", "show", "led", "led_countdown", "relay", "voice", "emit"}   # decoration: failure only warns
FIST = [0.0] * len(FINGERS)
PUMP_OPEN = [0.25] * len(FINGERS)
DEFAULT_COLOURS = ["#ff3030", "#ffb000", "#30ff60", "#ffffff"]


class _Stop(Exception):
    pass


class _Fault(Exception):
    pass


class Runtime:
    def __init__(self, env, reader: Callable[[float], dict] | None = None, store: MachineStore | None = None,
                 sleep: Callable[[float], None] | None = None, clock: Callable[[], float] = time.monotonic):
        self.env = env
        self.reader = reader
        self.store = store
        self.sleep = sleep or env.sleep
        self.clock = clock
        self.machine: dict | None = None
        self.name: str | None = None
        self.rev = 0                    # bumps when a machine starts or a patch swaps the running definition
        self.vars: dict = {}
        self.state: str | None = None
        self.prev: str | None = None
        self.status = "idle"            # idle | running | finished | stopped | error
        self.error: str | None = None
        self.events: deque = deque(maxlen=100)
        self.transitions: deque = deque(maxlen=50)
        self.warnings: deque = deque(maxlen=20)
        self.started_at: float | None = None
        self.entered_at: float | None = None
        self._deadline: float | None = None
        self._q: deque = deque()
        self._wake = threading.Event()
        self._stop = threading.Event()   # the CURRENT run's stop flag; each start() makes a new one
        self._local = threading.local()  # .stop = the flag of the run this loop thread belongs to
        self._thread: threading.Thread | None = None
        self._vlock = threading.RLock()
        self._plock = threading.RLock()
        self._last_fired: str | None = None
        self._cand: tuple[str | None, int] = (None, 0)
        self._payload = None
        self._event = None

    # ------------------------------------------------------------------ public API
    @property
    def running(self) -> bool:
        return self.status == "running"

    def start(self, machine: dict, vars: dict | None = None) -> dict:
        """Run a validated machine (stopping any running one first). ``vars`` override the machine's initial vars."""
        validate_for_start(machine, self.reader is not None)
        self.stop()
        with self._plock, self._vlock:
            self.machine = machine
            self.name = machine["name"]
            self.rev += 1
            self.vars = copy.deepcopy(machine["vars"])
            for k, v in (vars or {}).items():
                self.vars[k] = copy.deepcopy(v)
            self.state = self.prev = None
            self.status = "running"
            self.error = None
            self.events.clear()
            self.transitions.clear()
            self.warnings.clear()
            self._q.clear()
            # A fresh flag, never a cleared one: a stopped run still blocked in a camera read keeps its own (set)
            # flag, so it can't come back to life and drive the hand alongside this run.
            self._stop = threading.Event()
            self._wake.clear()
            self._deadline = None
            self._last_fired = None
            self._cand = (None, 0)
            self.started_at = self.clock()
        self._thread = threading.Thread(target=self._loop, args=(self._stop,), daemon=True, name=f"machine-{self.name}")
        self._thread.start()
        return {"machine": self.name, "initial": machine["initial"], "vars": self.snapshot_vars()}

    def stop(self, timeout: float = 3.0) -> bool:
        """Stop the running machine (relaxes the hand). True if one was running."""
        t = self._thread
        was = self.status == "running"
        if t is not None and t.is_alive():
            self._stop.set()
            self._wake.set()
            if t is not threading.current_thread():
                t.join(timeout)
        if was and self.status == "running":
            self.status = "stopped"
        if t is not None and t.is_alive() and t is not threading.current_thread():
            # Still blocked (a camera read or face scan can't be interrupted). Its flag is set, so it exits without
            # touching anything once that returns; relax the hand now rather than then (NFR-09).
            try:
                self.env.hw.relax()
            except Exception:
                pass
        return was

    def wait_finished(self, timeout: float = 30.0) -> bool:
        t = self._thread
        if t is not None:
            t.join(timeout)
        return not (t is not None and t.is_alive())

    def poke(self, payload=None) -> dict:
        if not self.running:
            raise MachineError("no machine is running (start_machine first)")
        try:
            json.dumps(payload)
        except (TypeError, ValueError) as e:
            raise MachineError(f"poke payload must be JSON: {e}") from e
        if len(self._q) >= MAX_QUEUE:
            raise MachineError("too many pending events; the machine is busy")
        self._q.append(("poke", copy.deepcopy(payload)))
        self._wake.set()
        return {"queued": "poke", "state": self.state}

    def snapshot_vars(self) -> dict:
        with self._vlock:
            return json.loads(json.dumps(self.vars, default=str))

    def snapshot(self, last: int = 20) -> dict:
        with self._vlock:
            return {
                "machine": self.name, "rev": self.rev, "status": self.status, "state": self.state, "error": self.error,
                "vars": json.loads(json.dumps(self.vars, default=str)),
                "in_state_s": round(self.clock() - self.entered_at, 2) if self.entered_at is not None and self.running else None,
                "timeout_in_s": round(max(0.0, self._deadline - self.clock()), 2) if self._deadline is not None and self.running else None,
                "events": list(self.events)[-last:], "transitions": list(self.transitions)[-last:],
                "warnings": list(self.warnings)}

    def scope(self) -> dict:
        """The variables and builtins expressions see right now (the live view resolves screens with it)."""
        return copy.deepcopy(self._scope())

    def definition(self) -> dict | None:
        """The running (or last) machine's definition, as a copy (live views draw its graph)."""
        with self._vlock:
            return copy.deepcopy(self.machine)

    def patch(self, machine: dict, ops: list, ctx: Context, goto: str | None = None) -> tuple[dict, list[str]]:
        """Apply ``ops`` to ``machine`` (the stored or the running definition), validate, and swap it in if it is
        the running one. Atomic: any error leaves everything as it was. Returns (new machine, notes)."""
        with self._plock:
            running_this = self.running and self.name == machine["name"]
            base = self.machine if running_this else machine
            new, live, notes = apply_patch(base, ops)
            new = validate(new, ctx)
            if running_this:
                if self.state not in new["states"] and goto is None:
                    raise MachineError(f"the patch removes state {self.state!r}, which the machine is in right now; "
                                       "pass goto='<state>' to move it somewhere else")
                if goto is not None and goto not in new["states"]:
                    raise MachineError(f"goto: no state {goto!r}")
                validate_for_start(new, self.reader is not None)
                with self._vlock:
                    self.machine = new
                    self.rev += 1
                    for k, v in new["vars"].items():
                        self.vars.setdefault(k, copy.deepcopy(v))
                    self.vars.update(live)
                    self._retime(new)
                if goto is not None:
                    self._q.append(("goto", goto))
                    self._wake.set()
                notes.append("applied to the running machine" + (f", moving to {goto}" if goto else ""))
        return new, notes

    def _retime(self, machine: dict) -> None:
        """After a patch: the current state's timeout (changed, added or removed) counts from when it was entered."""
        st = machine["states"].get(self.state)
        if st is None or self.entered_at is None:
            return
        if "timeout_ms" not in st:
            self._deadline = None
            return
        try:
            ms = evaluate(st["timeout_ms"], self._scope())
            self._deadline = self.entered_at + max(MIN_TIMEOUT_MS, min(float(ms), MAX_TIMEOUT_MS)) / 1000
        except (MachineError, TypeError, ValueError):
            self._deadline = None

    # ------------------------------------------------------------------ the loop
    def _halted(self) -> bool:
        """This loop thread's run was stopped (or replaced by a newer start())."""
        return getattr(self._local, "stop", self._stop).is_set()

    def _pause(self, seconds: float) -> None:
        """Sleep in the machine thread, cut short by stop(). Raises _Stop if the run was stopped meanwhile."""
        stop = getattr(self._local, "stop", self._stop)
        if seconds > 0:
            if self.sleep is time.sleep:
                stop.wait(seconds)
            else:
                self.sleep(seconds)   # an injected test sleep
        if stop.is_set():
            raise _Stop()

    def _loop(self, stop: threading.Event) -> None:
        self._local.stop = stop
        try:
            self._goto(self.machine["initial"], "start")
            while not stop.is_set() and self.status == "running":
                ev = self._next_event()
                if ev is None:
                    break
                name, payload = ev
                if name == "goto":
                    self._goto(payload, "patch")
                else:
                    self._dispatch(name, payload)
        except _Stop:
            pass
        except _Fault as e:
            self.error = str(e)
            self.status = "error"
        except Exception as e:  # a bug in a function must never leave the hand clamped
            self.error = f"{type(e).__name__}: {e}"
            self.status = "error"
        finally:
            if stop is not self._stop:
                return  # a stale run: a newer machine owns the status, the hand and the event stream now
            if self.status == "running":
                self.status = "stopped"
            if self.status in ("stopped", "error"):
                try:
                    self.env.hw.relax()
                except Exception:
                    pass
            self.env.emit("machine_end", machine=self.name, status=self.status, error=self.error)

    def _scope(self) -> dict:
        with self._vlock:
            s = dict(self.vars)
        s.update(event=self._event, payload=self._payload, state=self.state, prev=self.prev,
                 cfg=getattr(self.env, "settings", {}))
        return s

    def _next_event(self):
        while not self._halted():
            if self._q:
                return self._q.popleft()
            now = self.clock()
            if self._deadline is not None and now >= self._deadline:
                self._deadline = None
                return ("timer", None)
            st = self.machine["states"][self.state]
            if self.reader is not None and any(e.startswith("gesture:") for e in st["on"]):
                ev = self._poll_gesture()
                if ev:
                    return ev
                continue
            wait = 0.5 if self._deadline is None else max(0.0, min(0.5, self._deadline - now))
            self._wake.wait(wait)
            self._wake.clear()
        return None

    def _poll_gesture(self):
        cfg = {**SETTINGS_DEFAULT, **self.machine.get("settings", {})}
        try:
            r = self.reader(float(cfg["gesture_window_s"]))
        except Exception as e:
            self._warn(f"camera read failed: {type(e).__name__}: {e}")
            self._wake.wait(0.5)
            return None
        g = r.get("gesture") if r.get("confident") else None
        if g is None or g != self._cand[0]:
            self._cand = (g, 1 if g else 0)
        else:
            self._cand = (g, self._cand[1] + 1)
        if g != self._last_fired:
            self._last_fired = None   # any different window (even an unclear one) re-arms
        if g and g != self._last_fired and self._cand[1] >= int(cfg["gesture_stable"]):
            self._last_fired = g
            return (f"gesture:{g}", {"gesture": g, "confidence": r.get("confidence")})
        return None

    def _warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def _log_event(self, name: str, payload, handled: bool) -> None:
        self.events.append({"t": round(time.time(), 2), "event": name, "state": self.state, "handled": handled,
                            **({"payload": payload} if payload is not None else {})})

    # ------------------------------------------------------------------ events and transitions
    def _dispatch(self, name: str, payload) -> None:
        self._event, self._payload = name, payload
        target = self._handle(name, payload)
        if target is not None:
            self._goto(target, name)

    def _handle(self, name: str, payload) -> str | None:
        """Run the first matching handler of the current state; return its ``to`` (or None)."""
        st = self.machine["states"][self.state]
        self._event, self._payload = name, payload
        handlers = st["on"].get(name)
        if name != "enter":
            self._log_event(name, payload, bool(handlers))
        for i, h in enumerate(handlers or [], 1):
            if "if" in h and not self._truthy(h["if"], f"on {name} handler {i} if"):
                continue
            self._run(h["do"], f"on {name}")
            return h.get("to")
        return None

    def _truthy(self, expr, where: str) -> bool:
        try:
            return bool(evaluate(expr, self._scope()))
        except MachineError as e:
            raise _Fault(f"state {self.state!r} {where}: {e}") from e

    def _goto(self, target: str | None, trigger: str) -> None:
        chain = 0
        while target is not None:
            if self._halted():
                raise _Stop()
            chain += 1
            if chain > MAX_CHAIN:
                raise _Fault(f"more than {MAX_CHAIN} transitions in a row without waiting (an 'enter' loop?), stopped in {self.state!r}")
            m = self.machine
            if target not in m["states"]:
                raise _Fault(f"transition to unknown state {target!r}")
            self.prev, self.state = self.state, target
            self._deadline = None
            self.transitions.append({"t": round(time.time(), 2), "from": self.prev, "to": target, "event": trigger})
            self.env.emit("machine_state", machine=self.name, state=target, prev=self.prev)
            st = m["states"][target]
            self._event, self._payload = "enter", None
            self._run(st["on_enter"], "on_enter")
            if st.get("final"):
                self.status = "finished"
                self.entered_at = self.clock()
                raise _Stop()
            self.entered_at = self.clock()
            st = self.machine["states"].get(target, st)   # a patch may have landed during on_enter
            if "timeout_ms" in st:
                try:
                    ms = evaluate(st["timeout_ms"], self._scope())
                except MachineError as e:
                    raise _Fault(f"state {target!r} timeout_ms: {e}") from e
                if isinstance(ms, bool) or not isinstance(ms, (int, float)) or ms < 0:
                    raise _Fault(f"state {target!r}: timeout_ms evaluated to {ms!r}")
                # Floor: a 0 ms timer that re-enters its own state would otherwise spin (pose/speech floods).
                self._deadline = self.entered_at + max(MIN_TIMEOUT_MS, min(float(ms), MAX_TIMEOUT_MS)) / 1000
            target = self._handle("enter", None)
            trigger = "enter"

    # ------------------------------------------------------------------ actions
    def _run(self, actions: list, where: str) -> None:
        for i, a in enumerate(actions, 1):
            if self._halted():
                raise _Stop()
            if "if" in a and not self._truthy(a["if"], f"{where} action {i} if"):
                continue
            kind = next(k for k in a if k in _KINDS)
            soft = a.get("soft", kind in SOFT)
            try:
                getattr(self, "_do_" + kind)(a[kind], a)
            except _Stop:
                raise
            except _Fault:
                raise
            except Exception as e:
                msg = f"state {self.state!r} {where} action {i} ({kind}): {type(e).__name__}: {e}" if not isinstance(e, (MachineError, HardwareError, VisionError)) else f"state {self.state!r} {where} action {i} ({kind}): {e}"
                if soft:
                    self._warn(msg)
                else:
                    raise _Fault(msg) from e

    def _ev(self, x):
        return evaluate(x, self._scope())

    def _text(self, x) -> str:
        if isinstance(x, str) and not x.startswith("$"):
            return render(x, self._scope())
        v = self._ev(x)
        return "" if v is None else str(v)

    def _set(self, name: str, value) -> None:
        if self._halted():   # e.g. a camera read that returned after stop(): its result belongs to no one
            raise _Stop()
        with self._vlock:
            self.vars[name] = value

    def _wait_until(self, t: float) -> None:
        self._pause(t - self.clock())

    def _say(self, kind: str, **data) -> None:
        """Log an output action in the event list, so machine_state shows what the robot said/showed/emitted."""
        self.events.append({"t": round(time.time(), 2), "event": kind, "output": True, "state": self.state, **data})

    def _do_speak(self, v, a):
        text = self._text(v)
        self._say("speak", text=text)
        self.env.hw.speak(text)

    def _do_show(self, v, a):
        text = self._text(v)
        self._say("show", text=text)
        self.env.vision.show_text(text)

    def _do_led(self, v, a):
        if isinstance(v, dict):
            self.env.hw.led_fill(self._ev(v["colour"]), self._ev(v["brightness"]) if "brightness" in v else None)
        else:
            self.env.hw.led_fill(self._ev(v))

    def _do_led_countdown(self, v, a):
        cols = self._ev(v["colours"]) if "colours" in v else None
        self.env.hw.led_countdown(int(self._ev(v["beats"])), int(self._ev(v["period_ms"])), cols)

    def _do_pose(self, v, a):
        self.env.hw.pose(self._ev(v), int(self._ev(a["ms"])) if "ms" in a else self.env.settings.get("pose_ms", 150))

    def _do_perform(self, v, a):
        name = str(self._ev(v))
        m = self.env._move(name)
        if m is None:
            raise MachineError(f"no move called {name!r}")
        self.env.hw.pose(m["fingers"], int(self.env.settings["pose_ms"]))
        self._soft_call(self.env.hw.led_fill, m["led"])

    def _soft_call(self, fn, *args):
        try:
            return fn(*args)
        except HardwareError as e:
            self._warn(str(e))

    def _do_relax(self, v, a):
        if v is True or self.env.hw.fingers is not None:
            self.env.hw.relax()

    def _do_play_sequence(self, v, a):
        seq = self.env._sequence(str(self._ev(v)))
        with self.env._busy:
            self.env._run_sequence(seq, sleep=self._pause)

    def _do_wait(self, v, a):
        ms = self._ev(v)
        if isinstance(ms, bool) or not isinstance(ms, (int, float)) or ms < 0:
            raise MachineError(f"wait evaluated to {ms!r}; it needs a number of ms, 0..{MAX_WAIT_MS}")
        self._pause(min(float(ms), MAX_WAIT_MS) / 1000)

    def _do_set(self, v, a):
        vals = {k: self._ev(e) for k, e in v.items()}
        for k, x in vals.items():
            self._set(k, x)

    def _do_add(self, v, a):
        for k, e in v.items():
            d = self._ev(e)
            cur = self.vars.get(k)
            try:
                self._set(k, (0 if cur is None else cur) + d)
            except TypeError as err:
                raise MachineError(f"add: cannot add {d!r} to {k}={cur!r}") from err

    def _do_append(self, v, a):
        for k, e in v.items():
            x = self._ev(e)
            with self._vlock:
                cur = self.vars.get(k)
                if cur is None:
                    cur = []
                if not isinstance(cur, list):
                    raise MachineError(f"append: {k} is not a list")
                self.vars[k] = cur + [x]

    def _do_random(self, v, a):
        rng = self.env.rng
        avoid = self._ev(v["avoid"]) if "avoid" in v else None
        if "choice" in v:
            options = self._ev(v["choice"])
            if not isinstance(options, list) or not options:
                raise MachineError("random: choice must be a non-empty list")
        else:
            lo, hi = self._ev(v["low"]), self._ev(v["high"])
            if not all(isinstance(n, int) and not isinstance(n, bool) for n in (lo, hi)) or hi < lo:
                raise MachineError(f"random: low/high must be whole numbers with high >= low, got {lo!r}, {hi!r}")
            options = list(range(lo, hi + 1))
        options = [o for o in options if o != avoid]
        if not options:
            raise MachineError("random: nothing left to draw once 'avoid' is excluded")
        self._set(v["into"], rng.choice(options))

    def _do_read(self, v, a):
        if self.reader is None:
            raise MachineError("no camera reader attached")
        r = self.reader(float(self._ev(v["window_s"])) if "window_s" in v else float(
            {**SETTINGS_DEFAULT, **self.machine.get("settings", {})}["gesture_window_s"]))
        self._set(v["into"], {k: r.get(k) for k in ("gesture", "confidence", "confident")})

    def _do_identify(self, v, a):
        scan = getattr(self.env, "scan_faces", None)
        if scan is None:
            raise MachineError("no face camera attached")
        try:
            self._set(v["into"], scan(float(self._ev(v["window_s"])) if "window_s" in v else None))
        finally:  # the camera was on faces: start gesture debouncing afresh, so no stale window fires
            self._cand = (None, 0)
            self._last_fired = None

    def _do_call(self, v, a):
        fn = FUNCTIONS.get(v)
        if fn is None:
            raise MachineError(f"unknown function {v!r}")
        args = {k: self._ev(e) for k, e in (a.get("args") or {}).items()}
        out = fn.fn(self.env, **args)
        if "into" in a:
            self._set(a["into"], out)

    def _do_emit(self, v, a):
        if isinstance(v, str):
            self._say("emit", name=v)
            self.env.emit(v, machine=self.name)
        else:
            data = self._ev(v["data"]) if "data" in v else {}
            self._say("emit", name=v["event"], data=data)
            self.env.emit(v["event"], machine=self.name, **(data if isinstance(data, dict) else {"data": data}))

    def _do_relay(self, v, a):
        self.env.hw.fill_light(bool(self._ev(v)))

    def _do_voice(self, v, a):
        self.env.hw.voice_play(int(self._ev(v)))

    def _do_countdown(self, v, a):
        """Beats at ``period_ms`` on absolute times (no drift): word on screen, hand pump (fist then slightly open
        for half a beat each), then the last word at 'shoot'. ``into`` gets {t0, shoot_t} (monotonic seconds)."""
        o = {k: self._ev(x) for k, x in v.items() if k != "into"}
        beats = max(1, min(10, int(o["beats"])))                     # the validator's ranges, for expressions too
        period = max(0.06, min(5.0, int(o["period_ms"]) / 1000))
        words = o.get("words") or []
        show = bool(o.get("show", True)) and bool(words)
        pump = bool(o.get("pump", True))
        fist, opened = o.get("fist") or FIST, o.get("open") or PUMP_OPEN
        hw, vision = self.env.hw, self.env.vision
        t0 = time.monotonic()
        if o.get("led", True):
            self._soft_call(hw.led_countdown, int(o.get("led_beats", beats)), int(period * 1000),
                            o.get("colours") or DEFAULT_COLOURS)
        if o.get("voice"):
            self._soft_call(hw.voice_play, int(period * 4000))
        self.env.emit("countdown", machine=self.name, period_ms=int(period * 1000))
        half_ms = int(period * 500)
        for beat in range(beats):
            self._until(t0 + beat * period)
            if show and beat < len(words):
                vision.show_text(str(words[beat]))
            if pump:
                hw.pose(fist, half_ms)
            self.env.emit("beat", machine=self.name, beat=beat + 1, word=words[beat] if beat < len(words) else None)
            self._until(t0 + beat * period + period / 2)
            if pump:
                hw.pose(opened, half_ms)
        self._until(t0 + beats * period)
        shoot_t = time.monotonic()
        if show and len(words) > beats:
            vision.show_text(str(words[beats]))
        if "into" in v:
            self._set(v["into"], {"t0": t0, "shoot_t": shoot_t})

    def _until(self, t: float) -> None:
        self._pause(t - time.monotonic())


_KINDS = {"speak", "show", "led", "led_countdown", "pose", "perform", "relax", "play_sequence", "wait", "set", "add",
          "append", "random", "read", "identify", "call", "emit", "countdown", "relay", "voice"}
