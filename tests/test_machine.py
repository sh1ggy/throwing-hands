"""State-machine language, validation, patching, runtime and the MCP machine tools (higher-or-lower end to end).

    .venv/bin/python -m unittest tests.test_machine
"""

from __future__ import annotations

import copy
import json
import random
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import runlog  # noqa: E402
import server  # noqa: E402
from machine import Context, MachineError, apply_patch, evaluate, render, validate  # noqa: E402
from machine_runtime import Runtime  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from test_game import GameCase  # noqa: E402

CTX = Context(gestures={"up", "down", "nothing"}, moves={"rock", "paper", "scissors"}, sequences={"victory", "tantrum"})


def wait_for(fn, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.01)
    raise AssertionError("timed out waiting")


def tiny(**over) -> dict:
    """A two-state machine: idle waits for a poke, then ends."""
    m = {"name": "tiny", "vars": {"n": 0}, "initial": "idle",
         "states": {"idle": {"on": {"poke": {"do": [{"add": {"n": 1}}], "to": "done"}}},
                    "done": {"final": True}}}
    m.update(over)
    return m


class ExprTest(unittest.TestCase):
    def ev(self, x, **scope):
        return evaluate(x, scope)

    def test_literals_refs_and_operators(self):
        self.assertEqual(self.ev(5), 5)
        self.assertEqual(self.ev("hi"), "hi")
        self.assertEqual(self.ev("$a", a=3), 3)
        self.assertEqual(self.ev("$a.b.1", a={"b": [1, 2]}), 2)
        self.assertEqual(self.ev("$a.7", a={7: "x"}), "x")
        self.assertIsNone(self.ev("$nope.deeper"))
        self.assertTrue(self.ev({"gt": ["$a", 1]}, a=2))
        self.assertTrue(self.ev({"and": [{"ne": ["$a", 0]}, {"not": {"eq": ["$a", 3]}}]}, a=2))
        self.assertTrue(self.ev({"or": [False, {"in": ["$g", ["up", "down"]]}]}, g="up"))
        self.assertEqual(self.ev({"add": [1, 2, 3]}), 6)
        self.assertEqual(self.ev({"idiv": [{"add": [{"mul": [2, 3]}, 1]}, 2]}), 3)
        self.assertEqual(self.ev({"max": [1, 5, 2]}), 5)
        self.assertEqual(self.ev({"len": "$xs"}, xs=[1, 2]), 2)
        self.assertEqual(self.ev({"at": ["$xs", 1]}, xs=["a", "b"]), "b")
        self.assertEqual(self.ev({"if": [{"lt": [1, 2]}, "yes", "no"]}), "yes")
        self.assertEqual(self.ev({"concat": ["a", 1, None, "b"]}), "a1b")
        self.assertEqual(self.ev({"lit": "$not_a_ref"}), "$not_a_ref")

    def test_and_or_short_circuit(self):
        self.assertFalse(self.ev({"and": [False, {"div": [1, 0]}]}))
        self.assertTrue(self.ev({"or": [True, {"div": [1, 0]}]}))

    def test_type_errors_are_machine_errors_not_crashes(self):
        for bad in ({"gt": [None, 1]}, {"div": [1, 0]}, {"add": ["a", 1]}, {"len": 5}, {"zip": [1, 2]}, {"eq": [1]}):
            with self.assertRaises(MachineError, msg=str(bad)):
                self.ev(bad)

    def test_nothing_is_ever_evaluated_as_code(self):
        self.assertEqual(self.ev("__import__('os').system('x')"), "__import__('os').system('x')")
        self.assertIsNone(self.ev("$a.__class__", a=1))
        with self.assertRaises(MachineError):
            self.ev({"__import__": ["os"]})
        with self.assertRaises(MachineError):
            validate(tiny(vars={"n": 0}, states={"idle": {"on": {"poke": {"if": {"eval": ["1+1"]}, "to": "done"}}},
                                                 "done": {"final": True}}))

    def test_templates(self):
        self.assertEqual(render("Score {score}, {who.name}!", {"score": 3.0, "who": {"name": "Ann"}}), "Score 3, Ann!")
        self.assertEqual(render("{missing} ok {x", {}), " ok {x")


class ValidationTest(unittest.TestCase):
    def bad(self, m, *needles):
        with self.assertRaises(MachineError) as cm:
            validate(m, CTX)
        for n in needles:
            self.assertIn(n, str(cm.exception))

    def test_good_machine_is_normalised(self):
        m = validate(tiny(), CTX)
        self.assertEqual(m["states"]["idle"]["on"]["poke"], [{"do": [{"add": {"n": 1}}], "to": "done"}])
        self.assertEqual(m["states"]["done"], {"on_enter": [], "on": {}, "final": True})

    def test_fallback_machines_validate(self):
        for f in (ROOT / "server" / "fallback_machines").glob("*.json"):
            validate(json.loads(f.read_text()), Context())

    def test_structure(self):
        self.bad({"name": "x", "initial": "a", "states": {"a": {"on": {"poke": {"to": "ghost"}}}}}, "unknown state 'ghost'")
        self.bad(tiny(initial="nowhere"), "'nowhere' is not a state")
        self.bad(tiny(name="Bad Name"), "name")
        self.bad(tiny(extra=1), "unknown top-level")
        s = tiny()
        s["states"]["orphan"] = {"final": True}
        self.bad(s, "'orphan'", "unreachable")
        s = tiny()
        s["states"]["done"] = {"on": {"poke": {"do": [{"add": {"n": 1}}]}}}
        self.bad(s, "dead end")
        s = tiny()
        s["states"]["done"] = {"final": True, "on": {"poke": {"to": "idle"}}}
        self.bad(s, "final state")

    def test_events_and_gestures(self):
        s = tiny()
        s["states"]["idle"]["on"]["gesture:sideways"] = {"to": "done"}
        self.bad(s, "unknown gesture 'sideways'")
        s = tiny()
        s["states"]["idle"]["on"]["sneeze"] = {"to": "done"}
        self.bad(s, "unknown event 'sneeze'")
        s = tiny()
        s["states"]["idle"]["timeout_ms"] = 100
        self.bad(s, "no 'timer' handler")
        s = tiny()
        s["states"]["idle"]["on"]["timer"] = {"to": "done"}
        self.bad(s, "no timeout_ms")
        s = tiny(gestures=["wave"])
        s["states"]["idle"]["on"]["gesture:wave"] = {"to": "done"}
        s["states"]["idle"]["on"]["gesture:nothing"] = {"to": "done"}
        validate(s, CTX)  # the machine's own set wins over the camera's, 'nothing' is implied

    def test_actions_functions_and_references(self):
        def with_action(a):
            s = tiny()
            s["states"]["idle"]["on_enter"] = [a]
            return s
        self.bad(with_action({"dance": 1}), "needs exactly one action key")
        self.bad(with_action({"speak": "hi", "led": "#fff000"}), "exactly one action key")
        self.bad(with_action({"call": "rps.nonsense"}), "unknown function 'rps.nonsense'")
        self.bad(with_action({"call": "rps.hold_ms", "args": {"x": 1}}), "no argument 'x'")
        self.bad(with_action({"call": "rps.outcome", "args": {"rd": 1}}), "needs argument")
        self.bad(with_action({"led": "red"}), "colour")
        self.bad(with_action({"pose": [0, 2, 0, 0]}), "[0, 1]")
        self.bad(with_action({"perform": "lizard"}), "unknown move 'lizard'")
        self.bad(with_action({"play_sequence": "nope"}), "unknown sequence")
        self.bad(with_action({"speak": "score {ghost}"}), "uses $ghost")
        self.bad(with_action({"set": {"a": "$ghost"}}), "uses $ghost")
        self.bad(with_action({"set": {"event": 1}}), "usable variable name")
        self.bad(with_action({"random": {"into": "r", "low": 1}}), "high")
        self.bad(with_action({"wait": 99999}), "0")
        self.bad(with_action({"relax": "no"}), "relax")
        self.bad(with_action({"set": {"a": {"gt": [1]}}}), "gt takes 2..2")
        self.bad(with_action({"set": {"a": {"frobnicate": [1]}}}), "unknown operator")
        validate(with_action({"set": {"fresh": 1}, "if": {"eq": ["$n", 0]}}), CTX)  # vars written by actions are known

    def test_all_problems_reported_together(self):
        with self.assertRaises(MachineError) as cm:
            validate({"name": "x", "initial": "a", "states": {"a": {"on": {"poke": [{"to": "b"}, {"to": "c"}]}}}}, CTX)
        self.assertIn("'b'", str(cm.exception))
        self.assertIn("'c'", str(cm.exception))


class RuntimeCase(GameCase):
    """A Game on the fake hardware with a Runtime whose camera reader is scripted (``self.seen``)."""

    def setUp(self):
        super().setUp()
        self.seen = {"gesture": "none", "confident": False}
        self.reads = 0

    def reader(self, window_s):
        self.reads += 1
        time.sleep(0.003)
        return {**self.seen, "confidence": 1.0}

    def look(self, gesture):
        self.seen = {"gesture": gesture, "confident": gesture not in ("none", "unknown")}

    def runtime(self, settings=None) -> Runtime:
        g = self.make(settings=settings)
        rt = Runtime(g, reader=self.reader)
        self.addCleanup(rt.stop)
        self.rt = rt
        self.g = g
        return rt

    def run_machine(self, m, vars=None, ctx=CTX):
        rt = self.rt if hasattr(self, "rt") else self.runtime()
        return rt.start(validate(m, ctx), vars)


class RuntimeTest(RuntimeCase):
    def swipe(self) -> dict:
        """Not RPS: a swipe game that identifies who is swiping, then waits for a gesture."""
        return {"name": "swipe", "gestures": ["up", "down"], "vars": {"who": None, "picked": None},
                "initial": "setup",
                "states": {"setup": {"on_enter": [{"identify": {"into": "who", "window_s": 0.3}},
                                                  {"show": "Hi {who.name}", "if": "$who.known"}],
                                     "on": {"enter": {"to": "swipe"}}},
                           "swipe": {"on": {"gesture:up": {"do": [{"set": {"picked": "$who.name"}}], "to": "done"}}},
                           "done": {"final": True}}}

    def test_identify_works_for_any_game(self):
        rt = self.runtime()
        self.see(self.g, 1)
        self.g.register_player("Ann")  # face 1, in the shared player list
        self.look("up")
        self.run_machine(self.swipe())
        self.assertTrue(rt.wait_finished(3), rt.snapshot())
        v = rt.snapshot()["vars"]
        self.assertEqual((v["who"]["name"], v["who"]["known"], v["who"]["face_id"], v["picked"]), ("Ann", True, 1, "Ann"))
        self.assertIsNone(self.g.current)  # a facts-only scan: no RPS player seated, no modes
        self.assertEqual(self.g.cam.link.camera.algorithm, 6)  # back on gestures (classification)

    def test_identify_unknown_face_is_null_not_a_stranger(self):
        rt = self.runtime()
        self.see(self.g, 2)  # a face the camera sees but nobody registered
        self.look("up")
        self.run_machine(self.swipe())
        self.assertTrue(rt.wait_finished(3), rt.snapshot())
        who = rt.snapshot()["vars"]["who"]
        self.assertEqual((who["name"], who["known"]), (None, False))

    def test_output_actions_show_in_machine_state(self):
        rt = self.runtime()
        m = tiny()
        m["states"]["done"]["on_enter"] = [{"speak": "You scored {n}"}, {"show": "BYE"}, {"emit": "game_over"}]
        self.run_machine(m)
        wait_for(lambda: rt.state == "idle")
        rt.poke()
        self.assertTrue(rt.wait_finished(2))
        said = [(e["event"], e.get("text") or e.get("name")) for e in rt.snapshot()["events"] if e.get("output")]
        self.assertEqual(said, [("speak", "You scored 1"), ("show", "BYE"), ("emit", "game_over")])

    def test_vars_prefix_gets_a_hint(self):
        m = tiny()
        m["states"]["idle"]["on_enter"] = [{"set": {"n": {"add": ["$vars.n", 1]}}}]
        with self.assertRaises(MachineError) as cm:
            validate(m, CTX)
        self.assertIn("not $vars.score", str(cm.exception))

    def test_identify_validation(self):
        for bad in ({"identify": "who"}, {"identify": {"window_s": 1}}, {"identify": {"into": "w", "window_s": 0.1}},
                    {"identify": {"into": "w", "check_bribe": True}}):
            m = tiny()
            m["states"]["idle"]["on_enter"] = [bad]
            with self.assertRaises(MachineError, msg=str(bad)):
                validate(m, CTX)

    def test_poke_with_payload_and_final_state(self):
        rt = self.runtime()
        m = tiny()
        m["states"]["idle"]["on"]["poke"]["do"] = [{"add": {"n": "$payload.by"}}]
        self.run_machine(m)
        wait_for(lambda: rt.state == "idle")
        rt.poke({"by": 5})
        self.assertTrue(rt.wait_finished(2))
        s = rt.snapshot()
        self.assertEqual((s["status"], s["state"], s["vars"]["n"]), ("finished", "done", 5))
        self.assertEqual([t["to"] for t in s["transitions"]], ["idle", "done"])
        with self.assertRaises(MachineError):
            rt.poke()

    def test_timer_fires_after_timeout_ms(self):
        rt = self.runtime()
        m = {"name": "t", "vars": {"ticks": 0}, "initial": "wait", "states": {
            "wait": {"timeout_ms": 50, "on": {"timer": {"do": [{"add": {"ticks": 1}}], "to": "next"}}},
            "next": {"timeout_ms": "$ticks", "on": {"timer": {"to": "end"}}},
            "end": {"final": True}}}
        t0 = time.monotonic()
        self.run_machine(m)
        self.assertTrue(rt.wait_finished(3))
        self.assertGreaterEqual(time.monotonic() - t0, 0.04)
        self.assertEqual(rt.snapshot()["vars"]["ticks"], 1)

    def test_enter_handlers_branch_on_what_on_enter_computed(self):
        rt = self.runtime()
        m = {"name": "e", "vars": {"x": 0}, "initial": "a", "states": {
            "a": {"on_enter": [{"random": {"into": "x", "low": 5, "high": 5}}],
                  "on": {"enter": [{"if": {"gt": ["$x", 9]}, "to": "big"}, {"to": "small"}]}},
            "big": {"final": True}, "small": {"final": True}}}
        self.run_machine(m)
        rt.wait_finished(2)
        self.assertEqual(rt.state, "small")

    def test_random_with_avoid_never_returns_it(self):
        rt = self.runtime()
        m = {"name": "r", "vars": {"seen": []}, "initial": "a", "states": {
            "a": {"on_enter": [{"random": {"into": "x", "low": 1, "high": 3, "avoid": 2}}, {"append": {"seen": "$x"}}],
                  "on": {"enter": [{"if": {"lt": [{"len": "$seen"}, 60]}, "to": "a"}, {"to": "b"}]}},
            "b": {"final": True}}}
        self.run_machine(m)
        rt.wait_finished(3)
        self.assertEqual(set(rt.snapshot()["vars"]["seen"]), {1, 3})

    def test_an_enter_loop_is_stopped_with_a_clear_error(self):
        rt = self.runtime()
        self.run_machine({"name": "loop", "initial": "a", "states": {"a": {"on": {"enter": {"to": "a"}}}}})
        rt.wait_finished(3)
        self.assertEqual(rt.status, "error")
        self.assertIn("transitions in a row", rt.error)

    def test_a_failing_action_stops_the_machine_and_relaxes_the_hand(self):
        rt = self.runtime()
        m = {"name": "f", "vars": {"z": 0}, "initial": "a", "states": {
            "a": {"on_enter": [{"pose": [0, 0, 0, 0], "ms": 50}, {"set": {"q": {"div": [1, "$z"]}}}],
                  "on": {"enter": {"to": "b"}}},
            "b": {"final": True}}}
        self.run_machine(m)
        rt.wait_finished(2)
        self.assertEqual(rt.status, "error")
        self.assertIn("state 'a' on_enter action 2 (set)", rt.error)
        self.assertIsNone(self.g.hw.fingers)  # NFR-09
        self.assertEqual(self.g.bridge.us, [None] * 5)

    def test_soft_actions_only_warn(self):
        rt = self.runtime()
        self.g.hw.led_fill = lambda *a, **k: (_ for _ in ()).throw(__import__("hardware").HardwareError("no strip"))
        m = {"name": "s", "initial": "a", "states": {
            "a": {"on_enter": [{"led": "#ff0000"}, {"speak": "hi"}], "on": {"enter": {"to": "b"}}}, "b": {"final": True}}}
        self.run_machine(m)
        rt.wait_finished(2)
        self.assertEqual(rt.status, "finished")
        self.assertIn("no strip", rt.snapshot()["warnings"][0])

    def test_stop_relaxes_and_marks_stopped(self):
        rt = self.runtime()
        m = {"name": "s", "initial": "a", "states": {"a": {"on_enter": [{"pose": [0, 0, 0, 0]}], "on": {"poke": {"to": "a"}}}}}
        self.run_machine(m)
        wait_for(lambda: self.g.hw.fingers is not None)
        self.assertTrue(rt.stop())
        self.assertEqual(rt.status, "stopped")
        self.assertIsNone(self.g.hw.fingers)

    def test_outputs_reach_the_hardware(self):
        rt = self.runtime()
        m = {"name": "o", "initial": "a", "states": {
            "a": {"on_enter": [{"led": "#123456"}, {"perform": "scissors"}, {"relay": True}, {"voice": 300},
                               {"play_sequence": "victory"}], "on": {"enter": {"to": "b"}}},
            "b": {"final": True}}}
        self.run_machine(m)
        rt.wait_finished(2)
        self.assertEqual(rt.status, "finished", rt.error)
        self.assertTrue(self.g.bridge.relay)
        self.assertEqual(self.g.bridge.voice["ms"], 300)
        self.assertEqual(self.g.bridge.leds[0], "#ffb000")  # victory's gold
        self.assertEqual(self.g.bridge.us, [None] * 5)       # a sequence relaxes at its end

    def test_countdown_pumps_the_hand_and_reports_shoot(self):
        rt = self.runtime()
        m = {"name": "c", "initial": "a", "states": {
            "a": {"on_enter": [{"countdown": {"beats": 3, "period_ms": 60, "words": ["1", "2", "3", "GO"], "into": "cd"}}],
                  "on": {"enter": {"to": "b"}}}, "b": {"final": True}}}
        self.run_machine(m)
        rt.wait_finished(2)
        poses = [c for c in self.g.bridge.calls if c["cmd"] == "pose"]
        self.assertEqual(len(poses), 6)  # fist + open on each of 3 beats
        self.assertEqual([c["cmd"] for c in self.g.bridge.calls].count("countdown"), 1)
        cd = rt.snapshot()["vars"]["cd"]
        self.assertGreaterEqual(cd["shoot_t"], cd["t0"])

    def test_gesture_events_debounce_and_do_not_refire_while_held(self):
        rt = self.runtime()
        m = {"name": "g", "vars": {"ups": 0, "downs": 0}, "gestures": ["up", "down"],
             "settings": {"gesture_window_s": 0.05, "gesture_stable": 2}, "initial": "a", "states": {
                 "a": {"on": {"gesture:up": {"do": [{"add": {"ups": 1}}], "to": "a"},
                              "gesture:down": {"do": [{"add": {"downs": 1}}]}}}}}
        # (a is not a dead end: gesture:up leaves to itself)
        self.run_machine(m)
        self.look("up")
        wait_for(lambda: rt.snapshot()["vars"]["ups"] == 1)
        time.sleep(0.15)  # still holding it, even across the self-transition
        self.assertEqual(rt.snapshot()["vars"]["ups"], 1)
        self.look("none")
        time.sleep(0.1)
        self.look("up")
        wait_for(lambda: rt.snapshot()["vars"]["ups"] == 2)
        self.look("down")
        wait_for(lambda: rt.snapshot()["vars"]["downs"] == 1)
        self.look("unknown")  # not confident: never an event
        time.sleep(0.1)
        self.assertEqual(rt.snapshot()["vars"]["downs"], 1)
        ev = [e["event"] for e in rt.snapshot(50)["events"]]
        self.assertEqual(ev.count("gesture:up"), 2)

    def test_unhandled_gestures_are_logged_not_run(self):
        rt = self.runtime()
        m = {"name": "g", "settings": {"gesture_window_s": 0.02, "gesture_stable": 1}, "initial": "a", "states": {
            "a": {"on": {"gesture:up": {"to": "b"}}}, "b": {"final": True}}}
        self.run_machine(m)
        self.look("down")
        wait_for(lambda: any(e["event"] == "gesture:down" for e in rt.snapshot()["events"]))
        self.assertEqual(rt.state, "a")
        self.assertFalse([e for e in rt.snapshot()["events"] if e["event"] == "gesture:down"][0]["handled"])

    def test_gesture_machine_needs_a_reader(self):
        g = self.make()
        rt = Runtime(g, reader=None)
        with self.assertRaises(MachineError):
            rt.start(validate({"name": "g", "initial": "a", "states": {"a": {"on": {"gesture:up": {"to": "a"}}}}}), None)


class StopAndTimingTest(RuntimeCase):
    """Runaway timers, bad waits, and stop() during blocking actions, on real (interruptible) sleeps."""

    def real_runtime(self, reader=None) -> Runtime:
        g = self.make()
        rt = Runtime(g, reader=reader or self.reader, sleep=time.sleep)
        self.addCleanup(rt.stop)
        self.g, self.rt = g, rt
        self.spoken = []
        g.hw.speak = lambda text: self.spoken.append(text)
        return rt

    def test_a_timer_below_the_minimum_is_rejected(self):
        with self.assertRaises(MachineError) as e:
            validate({"name": "z", "initial": "a", "states": {"a": {"timeout_ms": 0, "on": {"timer": {"to": "a"}}}}})
        self.assertIn("'enter' handler", str(e.exception))

    def test_a_zero_timer_from_an_expression_cannot_spin(self):
        rt = self.real_runtime()
        self.run_machine({"name": "spin", "vars": {"t": 0, "n": 0}, "initial": "a", "states": {
            "a": {"on_enter": [{"add": {"n": 1}}], "timeout_ms": "$t", "on": {"timer": {"to": "a"}}}}})
        time.sleep(0.5)
        n = rt.snapshot_vars()["n"]
        rt.stop()
        self.assertLessEqual(n, 12)   # 50 ms floor: ~10 entries in 0.5 s, not ~50,000

    def test_a_negative_wait_from_an_expression_stops_with_a_clear_error(self):
        rt = self.real_runtime()
        self.run_machine({"name": "neg", "vars": {"w": -1}, "initial": "a", "states": {
            "a": {"on_enter": [{"wait": "$w"}], "on": {"poke": {"to": "b"}}}, "b": {"final": True}}})
        rt.wait_finished(2)
        self.assertEqual(rt.status, "error")
        self.assertIn("wait evaluated to -1", rt.error)

    def test_stop_cuts_a_long_wait_short_and_the_old_run_stays_dead(self):
        rt = self.real_runtime()
        self.run_machine({"name": "slow", "initial": "a", "states": {
            "a": {"on_enter": [{"pose": [0, 0, 0, 0]}, {"wait": 8000}, {"speak": "OLD"}], "on": {"poke": {"to": "b"}}},
            "b": {"final": True}}})
        wait_for(lambda: self.g.hw.fingers is not None)
        old = rt._thread
        t0 = time.monotonic()
        self.assertTrue(rt.stop())
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertFalse(old.is_alive())
        self.assertEqual(rt.status, "stopped")
        self.assertIsNone(self.g.hw.fingers)   # NFR-09
        self.run_machine(tiny())
        time.sleep(0.2)
        self.assertEqual((rt.name, rt.status), ("tiny", "running"))
        self.assertNotIn("OLD", self.spoken)

    def test_stop_cuts_a_countdown_short(self):
        rt = self.real_runtime()
        self.run_machine({"name": "cd", "initial": "a", "states": {
            "a": {"on_enter": [{"countdown": {"beats": 10, "period_ms": 5000}}], "on": {"poke": {"to": "b"}}},
            "b": {"final": True}}})
        time.sleep(0.1)
        t0 = time.monotonic()
        rt.stop()
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertFalse(rt._thread.is_alive())

    def test_a_run_stopped_inside_a_camera_read_never_touches_the_next_run(self):
        release, blocked = __import__("threading").Event(), []

        def reader(window_s):
            if not blocked:            # only the first read blocks, like a hung camera
                blocked.append(1)
                release.wait(5)
            return {"gesture": "up", "confident": True, "confidence": 1.0}

        rt = self.real_runtime(reader)
        self.run_machine({"name": "hung", "vars": {}, "initial": "a", "states": {
            "a": {"on_enter": [{"pose": [0, 0, 0, 0]}, {"read": {"into": "r"}}, {"speak": "OLD"}],
                  "on": {"poke": {"to": "b"}}},
            "b": {"final": True}}})
        wait_for(lambda: blocked)
        old = rt._thread
        rt.stop(timeout=0.1)
        self.assertTrue(old.is_alive())        # still stuck in the read
        self.assertIsNone(self.g.hw.fingers)   # but stop() relaxed the hand anyway
        self.run_machine(tiny())
        release.set()
        old.join(2)
        self.assertFalse(old.is_alive())
        time.sleep(0.1)
        self.assertEqual((rt.name, rt.status), ("tiny", "running"))
        self.assertNotIn("r", rt.snapshot_vars())
        self.assertNotIn("OLD", self.spoken)
        rt.poke()
        rt.wait_finished(2)
        self.assertEqual((rt.status, rt.snapshot_vars()["n"]), ("finished", 1))


class PatchTest(RuntimeCase):
    def machine(self):
        return validate({"name": "p", "vars": {"score": 0}, "gestures": ["up", "down"],
                         "settings": {"gesture_window_s": 0.02, "gesture_stable": 1}, "initial": "play", "states": {
                             "play": {"on": {"gesture:up": {"do": [{"add": {"score": 1}}], "to": "play"},
                                             "gesture:down": {"to": "over"}}},
                             "over": {"final": True}}}, CTX)

    def test_a_bad_patch_changes_nothing_and_the_game_keeps_running(self):
        rt = self.runtime()
        m = self.machine()
        rt.start(m, None)
        wait_for(lambda: rt.state == "play")
        before = copy.deepcopy(rt.machine)
        for ops in ([{"op": "set_handler", "state": "play", "event": "gesture:up", "handlers": {"to": "ghost"}}],
                    [{"op": "remove_state", "state": "over"}],              # gesture:down now leads nowhere valid
                    [{"op": "set_vars", "vars": {"event": 1}}],
                    [{"op": "frobnicate"}], [], [{"op": "remove_state", "state": "nope"}]):
            with self.assertRaises(MachineError, msg=str(ops)):
                rt.patch(m, ops, CTX)
        self.assertEqual(rt.machine, before)
        self.assertTrue(rt.running)

    def test_changing_the_rules_live(self):
        rt = self.runtime()
        m = self.machine()
        rt.start(m, None)
        self.look("up")
        wait_for(lambda: rt.snapshot()["vars"]["score"] == 1)
        new, notes = rt.patch(m, [{"op": "set_handler", "state": "play", "event": "gesture:up", "handlers": [
            {"do": [{"add": {"score": 10}}], "to": "play"}]}, {"op": "set_vars", "vars": {"bonus": 7}}], CTX)
        self.assertIn("applied to the running machine", notes[-1])
        self.look("none")
        time.sleep(0.05)
        self.look("up")
        wait_for(lambda: rt.snapshot()["vars"]["score"] == 11)  # the new rule, with the score kept
        self.assertEqual(rt.snapshot()["vars"]["bonus"], 7)

    def test_removing_the_current_state_needs_goto(self):
        rt = self.runtime()
        m = self.machine()
        rt.start(m, None)
        wait_for(lambda: rt.state == "play")
        ops = [{"op": "set_state", "state": "lobby", "value": {"on": {"poke": {"to": "lobby"}}}},
               {"op": "remove_state", "state": "play"}, {"op": "remove_state", "state": "over"},
               {"op": "set_initial", "state": "lobby"}]
        with self.assertRaises(MachineError) as cm:
            rt.patch(m, ops, CTX)
        self.assertIn("goto", str(cm.exception))
        self.assertEqual(rt.state, "play")
        rt.patch(m, ops, CTX, goto="lobby")
        wait_for(lambda: rt.state == "lobby")
        self.assertTrue(rt.running)

    def test_apply_patch_ops_are_pure(self):
        m = self.machine()
        snap = copy.deepcopy(m)
        new, live, notes = apply_patch(m, [{"op": "set_vars", "vars": {"a": 1}, "live": False},
                                          {"op": "remove_handler", "state": "play", "event": "gesture:down"}])
        self.assertEqual(m, snap)
        self.assertEqual(live, {})
        self.assertNotIn("gesture:down", new["states"]["play"]["on"])


class MachineToolsTest(unittest.TestCase):
    """Higher-or-lower end to end through the MCP tool functions on the simulated camera."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        server.reset_game()
        server.cam.close()
        server.cam.kind = "fake"
        server.OPTIONS["data_dir"] = self.dir
        server._gsets = None
        self.addCleanup(setattr, runlog, "RUN_DIR", runlog.RUN_DIR)
        runlog.RUN_DIR = Path(self.dir) / "runs"
        self.addCleanup(server.cam.close)
        self.addCleanup(server.reset_game)
        server.cam.get()
        self.fake = server.cam.link.camera
        g = server.get_game()
        g.sleep = lambda s: None
        g.rng = random.Random(4)
        g.settings.update(speak_banter=False)
        g.hw.tts = False  # tests never speak, whatever RPSBOT_SPEAK says

    def state(self, events=20):
        return server.machine_state(events)

    def start_higher_lower(self):
        server.start_machine("higher_lower", swap_gestures=True)  # wipes the camera and names up/down/nothing
        for gname in ("up", "down", "nothing"):
            server.train_gesture(gname, samples=3, interval_s=0, countdown_s=0)
        # fast reads: a patch to the example machine's settings (this is also how Claude would tune it)
        m = server.get_machine("higher_lower")
        m["settings"] = {"gesture_window_s": 0.08, "gesture_stable": 2}
        server.define_machine(m)
        self.fake.pose = 3  # an empty play zone (otherwise the fake camera shows random gestures)
        return server.start_machine("higher_lower")

    def test_higher_or_lower_is_playable(self):
        self.start_higher_lower()
        s = wait_for(lambda: (x := self.state())["state"] == "guess" and x["vars"]["next"] and x)
        right = "up" if s["vars"]["next"] > s["vars"]["number"] else "down"
        self.fake.pose = {"up": 1, "down": 2}[right]
        s = wait_for(lambda: (x := self.state())["vars"]["score"] == 1 and x["state"] == "guess" and x)
        self.assertEqual(s["vars"]["number"] > 0, True)
        time.sleep(0.4)  # still holding the same thumb: must not count as a second answer
        self.assertEqual(self.state()["vars"]["score"], 1)
        self.fake.pose = 3  # empty play zone re-arms
        time.sleep(0.4)
        s = wait_for(lambda: (x := self.state())["state"] == "guess" and x)
        wrong = "down" if s["vars"]["next"] > s["vars"]["number"] else "up"
        self.fake.pose = {"up": 1, "down": 2}[wrong]
        s = wait_for(lambda: (x := self.state())["status"] == "finished" and x)
        self.assertEqual((s["state"], s["vars"]["score"]), ("game_over", 1))
        self.assertEqual(server.hw().link().leds[0], "#ff0000")
        self.assertEqual(s["transitions"][0]["to"], "start")

    def test_numbers_never_repeat_the_previous_one(self):
        self.start_higher_lower()
        self.fake.pose = 3
        for round_ in range(1, 7):
            s = wait_for(lambda: (x := self.state())["state"] == "guess" and x["vars"]["next"] and x)
            self.assertNotEqual(s["vars"]["next"], s["vars"]["number"])
            server.poke()  # the machine's own poke handler restarts it: a new number, a new draw
            wait_for(lambda: sum(t["to"] == "start" for t in self.state(100)["transitions"]) == round_ + 1)

    def test_timeout_asks_again(self):
        self.start_higher_lower()
        wait_for(lambda: self.state()["state"] == "guess")
        server.patch_machine("higher_lower", [{"op": "set_timeout", "state": "guess", "ms": 60}])
        s = wait_for(lambda: any(e["event"] == "timer" for e in self.state()["events"]) and self.state())
        self.assertEqual(s["status"], "running")

    def test_patch_changes_the_running_game(self):
        self.start_higher_lower()
        wait_for(lambda: self.state()["state"] == "guess")
        out = server.patch_machine("higher_lower", [
            {"op": "set_handler", "state": "guess", "event": "gesture:up", "handlers": [
                {"do": [{"add": {"score": 5}}], "to": "game_over"}]}])
        self.assertTrue(out["running"])
        self.fake.pose = 1
        s = wait_for(lambda: (x := self.state())["status"] == "finished" and x)
        self.assertEqual(s["vars"]["score"], 5)
        with self.assertRaises(ToolError):
            server.patch_machine("higher_lower", [{"op": "set_handler", "state": "guess", "event": "gesture:up",
                                                   "handlers": {"to": "nowhere"}}])
        # the saved copy is the patched one and the fallback is untouched
        self.assertEqual(server.get_machine("higher_lower")["states"]["guess"]["on"]["gesture:up"][0]["to"], "game_over")
        fallback = json.loads((ROOT / "server" / "fallback_machines" / "higher_lower.json").read_text())
        self.assertEqual(fallback["states"]["guess"]["on"]["gesture:up"][0]["to"], "reveal")

    def test_define_list_delete_and_errors(self):
        names = {m["name"]: m for m in server.list_machines()}
        self.assertEqual((names["rps"]["source"], names["higher_lower"]["source"]), ("fallback", "fallback"))
        mine = {"name": "mine", "initial": "a", "states": {"a": {"on": {"poke": {"to": "b"}}}, "b": {"final": True}}}
        out = server.define_machine(mine)
        self.assertEqual((out["saved"], out["states"]), (True, ["a", "b"]))
        self.assertEqual({m["name"]: m["source"] for m in server.list_machines()}["mine"], "user")
        server.start_machine("mine")
        wait_for(lambda: self.state()["state"] == "a")
        with self.assertRaises(ToolError):
            server.delete_machine("mine")  # running
        server.poke({"x": 1})
        wait_for(lambda: self.state()["status"] == "finished")
        self.assertEqual(server.delete_machine("mine")["result"], "deleted")
        for bad, msg in (({"name": "x", "initial": "a", "states": {"a": {"on": {"poke": {"to": "zz"}}}}}, "unknown state"),
                         ({"name": "x"}, "states")):
            with self.assertRaises(ToolError) as cm:
                server.define_machine(bad)
            self.assertIn(msg, str(cm.exception))
        with self.assertRaises(ToolError):
            server.start_machine("nope")
        with self.assertRaises(ToolError):
            server.poke()  # nothing running
        self.assertEqual(server.stop_machine()["stopped"], False)
        ref = server.machine_reference()
        self.assertIn("rps.reveal", ref["functions"])
        self.assertIn("actions", ref)

    def test_face_tools_refuse_while_a_machine_runs(self):
        server.define_machine({"name": "mine", "initial": "a",
                               "states": {"a": {"on": {"poke": {"to": "b"}}}, "b": {"final": True}}})
        server.start_machine("mine")
        wait_for(lambda: self.state()["state"] == "a")
        for call in (lambda: server.identify_player(), lambda: server.register_player("Ann")):
            with self.assertRaises(ToolError) as cm:
                call()
            self.assertIn("stop_machine first", str(cm.exception))
        server.stop_machine()
        self.assertEqual(server.identify_player()["player"], "stranger")

    def test_machine_tools_are_registered_and_free_play_is_gone(self):
        names = {t.name for t in server.mcp._tool_manager.list_tools()}
        for n in ("define_machine patch_machine get_machine list_machines delete_machine start_machine stop_machine "
                  "machine_state poke machine_reference gesture_set random_number train_gesture read_gesture").split():
            self.assertIn(n, names)
        for n in "new_game game_turn update_game current_game end_game".split():
            self.assertNotIn(n, names)


if __name__ == "__main__":
    unittest.main()
