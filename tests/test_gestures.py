"""Free play (any game described in chat), the gesture swap behind it, and random_number, on the simulated camera.

    .venv/bin/python -m unittest tests.test_gestures
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tools"))

import server  # noqa: E402
import runlog  # noqa: E402
from gestures import RPS, GestureError, GestureSets  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402


class GestureSetsTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)

    def test_defaults_to_rps(self):
        g = GestureSets(self.dir / "g.json")
        self.assertEqual(g.ids, RPS)
        self.assertTrue(g.rps_ready())
        self.assertEqual(g.label(2), "paper")
        self.assertEqual(g.label(-1), "none")
        self.assertEqual(g.label(0), "unknown")

    def test_start_adds_nothing_and_persists(self):
        g = GestureSets(self.dir / "g.json")
        g.start("Higher Lower", ["Up", "down"])
        self.assertEqual(g.ids, {"up": 1, "down": 2, "nothing": 3})
        self.assertFalse(g.rps_ready())
        again = GestureSets(self.dir / "g.json")
        self.assertEqual(again.name, "higher_lower")
        self.assertEqual(again.label(2), "down")
        again.start("rps", ["rock", "paper", "scissors"])
        self.assertEqual(again.ids, RPS)
        self.assertTrue(again.rps_ready())

    def test_new_name_gets_next_id_and_bad_names_fail(self):
        g = GestureSets(self.dir / "g.json")
        self.assertEqual(g.resolve("thumbs up"), (5, "thumbs_up"))
        self.assertEqual(g.resolve(7), (7, "id7"))
        with self.assertRaises(GestureError):
            g.resolve("rock!")
        with self.assertRaises(GestureError):
            g.start("x", ["up", "up"])


class SwapToolsTest(unittest.TestCase):
    """The demo flow through the MCP tool functions: RPS -> thumbs up/down -> play -> back to RPS."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        server.reset_game()
        server.cam.close()
        server.cam.kind = "fake"
        server.OPTIONS["data_dir"] = self.dir
        server._gsets = None
        server._fp = None
        # training writes run logs; keep them out of the real server/data/vision/runs
        self.addCleanup(setattr, runlog, "RUN_DIR", runlog.RUN_DIR)
        runlog.RUN_DIR = Path(self.dir) / "runs"
        self.addCleanup(server.cam.close)
        self.addCleanup(server.reset_game)

    def camera(self):
        server.cam.get()
        return server.cam.link.camera

    def test_describe_play_change_and_back_to_rps(self):
        fake = self.camera()
        with self.assertRaises(ToolError):  # bad names fail before anything is wiped
            server.new_game("x", "rules", ["up", "up"])
        self.assertEqual(sum(fake.learned[6].values()), 60)
        with self.assertRaises(ToolError):
            server.game_turn()  # no game yet

        rules = "Higher or lower, 1-10. Thumbs up = next is higher. Right +1, wrong ends it."
        out = server.new_game("higher_lower", rules, ["up", "down"])
        self.assertEqual(out["camera_ids"], {"up": 1, "down": 2, "nothing": 3})
        self.assertEqual(out["rules"], rules)
        self.assertEqual(out["scores"], {"player": 0})
        self.assertEqual(sum(fake.learned[6].values()), 0)
        with self.assertRaises(ToolError):  # the built-in RPS can't run on the thumbs set
            server.play_round()

        for g in ("up", "down", "nothing"):
            self.assertEqual(server.train_gesture(g, samples=3, interval_s=0, countdown_s=0)["accepted"], 3)

        fake.pose = 1
        t = server.game_turn("HIGHER OR LOWER?", countdown_beats=0, window_s=0.1, expect=["up", "down"])
        self.assertEqual((t["turn"], t["gesture"], t["accepted"]), (1, "up", True))
        fake.pose = 3  # empty play zone: not an expected answer, so it retries then gives up
        t = server.game_turn(countdown_beats=0, window_s=0.1, expect=["up", "down"], retries=1)
        self.assertFalse(t["accepted"])
        self.assertEqual(len(t["attempts"]), 2)

        server.update_game(scores={"player": 1}, state={"number": 7})
        g = server.update_game(state={"number": None}, note="streak 1", rules="Now thumbs down = higher.")
        self.assertEqual(g["scores"], {"player": 1})
        self.assertEqual(g["state"], {})
        self.assertEqual(g["rules"], "Now thumbs down = higher.")
        with self.assertRaises(ToolError):
            server.update_game(scores={"player": "lots"})

        server._fp = None  # survives a server restart
        self.assertEqual(server.current_game()["turns"], 2)
        self.assertFalse(server.end_game("player wins")["running"])
        with self.assertRaises(ToolError):
            server.game_turn()

        back = server.new_game("rps", "classic", ["rock", "paper", "scissors"])
        self.assertEqual(back["camera_ids"], RPS)
        self.assertTrue(server.gsets().rps_ready())

    def test_new_game_reusing_known_gestures(self):
        self.camera()
        out = server.new_game("odds", "rock = odd, paper = even", ["Rock", "paper"], players=["a", "b"], retrain=False)
        self.assertEqual(out["camera_ids"], {"rock": 1, "paper": 2})
        self.assertEqual(out["scores"], {"a": 0, "b": 0})
        self.assertEqual(sum(server.cam.link.camera.learned[6].values()), 60, "retrain=false must not wipe")
        with self.assertRaises(ToolError):
            server.new_game("x", "r", ["thumbs"], retrain=False)

    def test_random_number(self):
        seen = {server.random_number(1, 3, avoid=2)["value"] for _ in range(200)}
        self.assertEqual(seen, {1, 3})
        with self.assertRaises(ToolError):
            server.random_number(5, 4)
        with self.assertRaises(ToolError):
            server.random_number(2, 2, avoid=2)


if __name__ == "__main__":
    unittest.main()
