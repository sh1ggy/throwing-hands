"""Gesture sets, the live gesture swap through the machine tools, and random_number, on the simulated camera.

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
    """The demo flow through the MCP tool functions: RPS -> thumbs up/down machine -> play -> back to RPS."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        server.reset_game()
        server.cam.close()
        server.cam.kind = "fake"
        server.OPTIONS["data_dir"] = self.dir
        server._gsets = None
        # training writes run logs; keep them out of the real server/data/vision/runs
        self.addCleanup(setattr, runlog, "RUN_DIR", runlog.RUN_DIR)
        runlog.RUN_DIR = Path(self.dir) / "runs"
        self.addCleanup(server.cam.close)
        self.addCleanup(server.reset_game)

    def camera(self):
        server.cam.get()
        return server.cam.link.camera

    def test_swap_reconnects_a_dropped_link(self):
        """Another program had the XIAO port meanwhile: the swap must reconnect, not fail (bug 2026-11-21)."""
        self.camera()

        def dropped(*_a, **_k):
            raise OSError(6, "Device not configured")

        server.cam.link.husky = dropped
        out = server.start_machine("higher_lower", swap_gestures=True)
        self.assertEqual(out["camera_ids"], {"up": 1, "down": 2, "nothing": 3})
        self.assertEqual(sum(server.cam.link.camera.learned[6].values()), 0)  # the new link's camera was wiped

        server.cam.link.husky = dropped  # training reconnects too
        self.assertEqual(server.train_gesture("up", samples=2, interval_s=0, countdown_s=0)["accepted"], 2)

    def test_swap_to_a_machine_train_and_back_to_rps(self):
        fake = self.camera()
        self.assertEqual(sum(fake.learned[6].values()), 60)
        with self.assertRaises(ToolError):  # the camera holds rps, higher_lower needs up/down
            server.start_machine("higher_lower")
        self.assertEqual(sum(fake.learned[6].values()), 60, "a refused start must not wipe")
        with self.assertRaises(ToolError):  # bad machines fail before anything is wiped
            server.define_machine({"name": "bad", "initial": "a", "states": {"a": {}}}, swap_gestures=True)
        self.assertEqual(sum(fake.learned[6].values()), 60)

        out = server.start_machine("higher_lower", swap_gestures=True)  # wipes, names, does not start
        self.assertEqual(out["camera_ids"], {"up": 1, "down": 2, "nothing": 3})
        self.assertFalse(server.get_runtime().running)
        self.assertEqual(sum(fake.learned[6].values()), 0)
        with self.assertRaises(ToolError):  # the built-in RPS engine can't run on the thumbs set
            server.play_round()
        for g in ("up", "down", "nothing"):
            self.assertEqual(server.train_gesture(g, samples=3, interval_s=0, countdown_s=0)["accepted"], 3)

        back = server.start_machine("rps", swap_gestures=True)
        self.assertEqual(back["camera_ids"], RPS)
        self.assertTrue(server.gsets().rps_ready())

    def test_random_number(self):
        seen = {server.random_number(1, 3, avoid=2)["value"] for _ in range(200)}
        self.assertEqual(seen, {1, 3})
        with self.assertRaises(ToolError):
            server.random_number(5, 4)
        with self.assertRaises(ToolError):
            server.random_number(2, 2, avoid=2)


if __name__ == "__main__":
    unittest.main()
