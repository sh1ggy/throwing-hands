"""The demo's game swap with the real hand: after RPS is swapped for thumbs up/down, higher or lower still drives the
hand (index + ring/little only), on the simulated camera and a simulated XIAO.

`--hand real` here means the hand goes through the camera's own link (the XIAO), exactly as on the rig; the link is a
FakeBridge, so every servo command is recorded in ``server.cam.link.calls``.

    .venv/bin/python -m unittest tests.test_hand_games
"""

from __future__ import annotations

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
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from test_machine import wait_for  # noqa: E402

UP, DOWN, NOTHING = 1, 2, 3
FAST = {"gesture_window_s": 0.08, "gesture_stable": 2}
INDEX, RING = 1, 3  # finger positions in a pose: [thumb, index, middle, ring+little]


def claude_style_game() -> dict:
    """The kind of machine Claude writes from the stage prompt: different state names from the fallback, the wag as a
    named sequence, the hand taps the number out."""
    return {
        "name": "hilo",
        "description": "Higher or lower 1-10 with the hand: tap the number, claw while waiting, open when right, wag on a loss.",
        "gestures": ["up", "down"],
        "settings": FAST,
        "vars": {"n": 0, "hidden": 0, "score": 0, "taps": 0},
        "initial": "deal",
        "states": {
            "deal": {"on_enter": [{"set": {"score": 0}}, {"random": {"into": "n", "low": 1, "high": 10}}],
                     "on": {"enter": {"to": "show_number"}}},
            "show_number": {"on_enter": [{"set": {"taps": 0}}, {"show": "{n}"}], "on": {"enter": {"to": "tap"}}},
            "tap": {"on_enter": [{"pose": [1, 1, 1, 0.5], "ms": 100}, {"wait": 200},
                                 {"pose": [1, 0, 1, 0.5], "ms": 100}, {"wait": 200}, {"add": {"taps": 1}}],
                    "on": {"enter": [{"if": {"lt": ["$taps", "$n"]}, "to": "tap"}, {"to": "wait_thumb"}]}},
            "wait_thumb": {"on_enter": [{"random": {"into": "hidden", "low": 1, "high": 10, "avoid": "$n"}},
                                        {"pose": [1, 0.5, 1, 0.5]}],
                           "on": {"gesture:up": [{"if": {"gt": ["$hidden", "$n"]}, "do": [
                                                     {"add": {"score": 1}}, {"set": {"n": "$hidden"}},
                                                     {"pose": [1, 1, 1, 1]}], "to": "show_number"},
                                                 {"to": "lost"}],
                                  "gesture:down": [{"if": {"lt": ["$hidden", "$n"]}, "do": [
                                                       {"add": {"score": 1}}, {"set": {"n": "$hidden"}},
                                                       {"pose": [1, 1, 1, 1]}], "to": "show_number"},
                                                   {"to": "lost"}]}},
            "lost": {"on_enter": [{"show": "SCORE {score}"}, {"play_sequence": "wag"}], "final": True},
        },
    }


WAG = [{"pose": [1, 1, 1, 0.5], "ms": 80}, {"pose": [1, 0.3, 1, 0.5], "ms": 80}, {"repeat": 4}]


class HandAfterSwapTest(unittest.TestCase):
    hand = "real"

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        server.reset_game()
        server.cam.close()
        server.cam.kind = "fake"
        server.OPTIONS["data_dir"] = self.dir
        self.addCleanup(server.OPTIONS.__setitem__, "hand", server.OPTIONS["hand"])
        server.OPTIONS["hand"] = self.hand
        server._gsets = None
        self.addCleanup(setattr, runlog, "RUN_DIR", runlog.RUN_DIR)
        runlog.RUN_DIR = Path(self.dir) / "runs"
        self.addCleanup(server.cam.close)
        self.addCleanup(server.reset_game)
        server.cam.get()
        g = server.get_game()
        g.sleep = lambda s: None
        g.rng = random.Random(4)
        g.settings.update(speak_banter=False)
        g.hw.tts = False
        self.poses: list[tuple[float, list[float], str | None]] = []
        real_pose = g.hw.pose

        def spy(fingers, ms=150):
            out = real_pose(fingers, ms)
            rt = server.get_runtime()
            self.poses.append((time.monotonic(), list(out["fingers"]), rt.state if rt.running else None))
            return out
        g.hw.pose = spy

    # ------------------------------------------------------------------ helpers
    def fake(self):
        return server.cam.link.camera

    def hand_cmds(self, link=None) -> list[dict]:
        link = link or server.hw().link()
        return [c for c in link.calls if c["cmd"] in ("pose", "servo", "relax")]

    def swap_and_train(self, start):
        """What Claude does after the prompt: swap (wipes the camera), teach up / down / nothing."""
        out = start()
        self.assertEqual(out["camera_ids"], {"up": 1, "down": 2, "nothing": 3})
        for gname in ("up", "down", "nothing"):
            self.assertEqual(server.train_gesture(gname, samples=3, interval_s=0, countdown_s=0)["accepted"], 3)
        self.fake().pose = NOTHING

    def state(self):
        return server.machine_state(50)

    def play_right_then_wrong(self, number_var, hidden_var, guess_state):
        s = wait_for(lambda: (x := self.state())["state"] == guess_state and x["vars"][hidden_var] and x)
        first = s["vars"][number_var]
        self.fake().pose = UP if s["vars"][hidden_var] > s["vars"][number_var] else DOWN
        s = wait_for(lambda: (x := self.state())["vars"]["score"] == 1 and x["state"] == guess_state and x)
        second = s["vars"][number_var]
        self.fake().pose = NOTHING  # empty play zone re-arms
        time.sleep(0.3)
        s = wait_for(lambda: (x := self.state())["state"] == guess_state and x)
        self.fake().pose = DOWN if s["vars"][hidden_var] > s["vars"][number_var] else UP
        wait_for(lambda: self.state()["status"] == "finished", timeout=8)
        return first, second

    def taps_in(self, state="tap") -> list[list[list[float]]]:
        """Pose runs grouped by consecutive visits to the tap state."""
        runs, cur = [], None
        for _, f, st in self.poses:
            if st == state:
                if cur is None:
                    cur = []
                    runs.append(cur)
                cur.append(f)
            else:
                cur = None
        return runs

    def assert_hand_safe(self, poses):
        for f in poses:
            self.assertEqual((f[0], f[2]), (1, 1), f"thumb and middle aren't fitted: keep them at 1 (open), got {f}")
            self.assertGreaterEqual(f[RING], 0.5, f"ring+little held closed during play trips the 5 s watchdog: {f}")

    # ------------------------------------------------------------------ scenarios
    def test_fallback_after_swap_drives_the_hand(self):
        """RPS hand works, swap to the fallback higher_lower, teach, play: taps = number, claw, open, wag, relax."""
        server.perform_move("rock")  # before the swap the hand plays RPS
        self.assertTrue(self.hand_cmds(), "perform_move reached the hand")
        self.swap_and_train(lambda: server.start_machine("higher_lower", swap_gestures=True))
        m = server.get_machine("higher_lower")
        m["settings"] = FAST
        server.define_machine(m)
        server.start_machine("higher_lower")
        first, second = self.play_right_then_wrong("number", "next", "guess")

        taps = self.taps_in()
        closes = [sum(1 for f in run if f[INDEX] == 0) for run in taps]
        self.assertEqual(closes[:2], [first, second], "the index taps each number out exactly")
        self.assert_hand_safe([f for run in taps for f in run])
        guess = [f for _, f, st in self.poses if st == "guess"]
        self.assertIn([1, 0.5, 1, 0.5], guess, "claw while waiting")
        self.assertIn([1, 1, 1, 1], guess, "opens on a right guess")
        wag = [f for _, f, st in self.poses if st == "game_over"]
        self.assertGreaterEqual(sum(1 for f in wag if f[INDEX] == 0.3), 4, "wags on a loss")
        self.assertEqual(self.hand_cmds()[-1]["cmd"], "relax", "ends relaxed")
        self.assertIsNone(server.hw().fingers)

    def test_camera_is_quiet_while_tapping(self):
        """The tap state has no gesture handler, so no camera read shares the XIAO with the servo commands."""
        self.swap_and_train(lambda: server.start_machine("higher_lower", swap_gestures=True))
        server.start_machine("higher_lower")
        wait_for(lambda: self.state()["state"] == "guess")
        server.stop_machine()
        calls = server.cam.link.calls
        pose_idx = [i for i, c in enumerate(calls) if c["cmd"] == "pose"]
        tap_poses = len(self.taps_in()[0])
        between = calls[pose_idx[0]:pose_idx[tap_poses]]  # first tap .. the claw that ends the taps
        self.assertFalse([c for c in between if c["cmd"] == "husky"], "no camera traffic during the taps")

    def test_claude_style_game_with_a_sequence(self):
        """A game Claude writes itself (own state names, wag as a named sequence) after define_machine(swap=true)."""
        server.define_sequence("wag", WAG)
        out = server.define_machine(claude_style_game(), swap_gestures=True)
        self.assertEqual(out["camera_ids"], {"up": 1, "down": 2, "nothing": 3})
        for gname in ("up", "down", "nothing"):
            server.train_gesture(gname, samples=3, interval_s=0, countdown_s=0)
        self.fake().pose = NOTHING
        server.start_machine("hilo")
        first, second = self.play_right_then_wrong("n", "hidden", "wait_thumb")
        closes = [sum(1 for f in run if f[INDEX] == 0) for run in self.taps_in()]
        self.assertEqual(closes[:2], [first, second])
        before = len(self.hand_cmds())
        self.assertGreater(before, 0)
        s = self.state()
        self.assertEqual((s["state"], s["status"]), ("lost", "finished"))
        wag_cmds = [c for c in server.hw().link().calls if c["cmd"] == "pose"][-8:]
        self.assertEqual(len(wag_cmds), 8, "the wag sequence reached the hand")
        self.assertEqual(self.hand_cmds()[-1]["cmd"], "relax", "a sequence relaxes after it")

    def test_swap_after_a_dropped_link_still_reaches_the_hand(self):
        """The swap reconnects a dropped XIAO link; the hand must follow the new link, not the dead one."""
        old = server.cam.link

        def dropped(*_a, **_k):
            raise OSError(6, "Device not configured")
        old.husky = dropped
        self.swap_and_train(lambda: server.start_machine("higher_lower", swap_gestures=True))
        new = server.cam.link
        self.assertIsNot(new, old)
        server.start_machine("higher_lower")
        wait_for(lambda: self.state()["state"] == "guess")
        server.stop_machine()
        self.assertTrue([c for c in new.calls if c["cmd"] == "pose"], "poses went to the reconnected link")
        self.assertFalse([c for c in old.calls if c["cmd"] == "pose"], "nothing went to the dead link")

    def test_a_bad_pose_is_refused_before_the_wipe(self):
        """Claude writes a 2-value pose (index, ring+little): refused with a clear message, the camera is not wiped."""
        learned = sum(self.fake().learned[6].values())
        bad = claude_style_game()
        bad["states"]["wait_thumb"]["on_enter"][1] = {"pose": [0.5, 0.5]}
        with self.assertRaises(ToolError) as cm:
            server.define_machine(bad, swap_gestures=True)
        self.assertIn("4 values", str(cm.exception))
        self.assertEqual(sum(self.fake().learned[6].values()), learned, "a refused machine must not wipe the camera")

    def test_stop_during_the_taps_relaxes_at_once(self):
        self.swap_and_train(lambda: server.start_machine("higher_lower", swap_gestures=True))
        server.get_runtime().sleep = time.sleep  # real waits between taps
        server.start_machine("higher_lower")
        wait_for(lambda: self.state()["state"] == "tap")
        t0 = time.monotonic()
        server.stop_machine()
        self.assertLess(time.monotonic() - t0, 0.6, "stop cuts the tap wait short")
        wait_for(lambda: server.hw().fingers is None)
        self.assertEqual(self.hand_cmds()[-1]["cmd"], "relax")

    def test_tap_timing(self):
        """Server overhead per tap stays tiny: N taps take about N x 0.4 s (the waits), not more."""
        self.swap_and_train(lambda: server.start_machine("higher_lower", swap_gestures=True))
        server.get_runtime().sleep = time.sleep
        server.start_machine("higher_lower")
        s = wait_for(lambda: (x := self.state())["state"] == "guess" and x, timeout=8)
        server.stop_machine()
        run = [(t, f) for t, f, st in self.poses if st == "tap"]
        n = s["vars"]["number"]
        self.assertEqual(sum(1 for _, f in run if f[INDEX] == 0), n)
        span = run[-1][0] - run[0][0]
        self.assertAlmostEqual(span, 0.4 * n - 0.2, delta=0.05 + 0.01 * n)


class SimHandAfterSwapTest(HandAfterSwapTest):
    """Software mode (--hand sim): the same games run; the hand's commands go to the simulated bridge, not the XIAO."""
    hand = "sim"

    def test_swap_after_a_dropped_link_still_reaches_the_hand(self):
        self.skipTest("the simulated hand has its own link")

    def test_camera_is_quiet_while_tapping(self):
        self.skipTest("the simulated hand doesn't share the camera link")

    def test_hand_commands_stay_off_the_xiao(self):
        self.swap_and_train(lambda: server.start_machine("higher_lower", swap_gestures=True))
        server.start_machine("higher_lower")
        wait_for(lambda: self.state()["state"] == "guess")
        server.stop_machine()
        self.assertFalse([c for c in server.cam.link.calls if c["cmd"] == "pose"])
        self.assertTrue(self.hand_cmds())


if __name__ == "__main__":
    unittest.main()
