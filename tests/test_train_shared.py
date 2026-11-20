"""The Train page inside the MCP server (/train): tools/train_ui.py's trainer on the server's camera link.

Agreed 2026-11-21 (not in the design doc): one program, so training no longer means stopping the server.
"""

from __future__ import annotations

import json
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
import test_machine  # noqa: E402  (sets sys.path for server; imported as a module so pytest doesn't rerun its tests)
from test_machine import wait_for  # noqa: E402

import game_view  # noqa: E402
import server  # noqa: E402
import train_ui  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

FAST = {"samples": 3, "interval": 0.2, "countdown": 1, "throw_countdown": 1, "test_n": 3, "window": 0.1}


class Base(unittest.TestCase):
    setUp = test_machine.MachineToolsTest.setUp  # the simulated camera and a temp data dir, without re-running its tests


class SharedTrainerTest(Base):
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, train_ui, "LOG_DIR", train_ui.LOG_DIR)
        train_ui.LOG_DIR = Path(self.dir) / "vision"
        self.tr = train_ui.Trainer(False, None, None, shared=server.TrainHost())
        self.addCleanup(self.tr.join, 5)
        self.addCleanup(self.tr.stop)
        self.tr.start()

    def snap(self) -> dict:
        return json.loads(self.tr.snapshot())  # also marks the page as open

    def ready(self):
        wait_for(lambda: self.snap()["link"] == "ok", 10)
        self.assertEqual(self.tr.handle("settings", FAST)[0], 200)

    def idle(self, timeout=20):
        wait_for(lambda: self.snap()["phase"] == "idle" and self.tr.jobs.empty(), timeout)

    def test_trains_and_tests_on_the_servers_camera(self):
        self.ready()
        s = self.snap()
        self.assertEqual([c[0] for c in s["classes"]], ["rock", "paper", "scissors", "nothing"])
        self.assertTrue(s["mode"].startswith("server"))
        self.assertEqual(self.tr.handle("train", {"cls": "paper"}), (200, {"ok": True}))
        wait_for(lambda: self.snap()["phase"] != "idle", 5)
        self.idle()
        self.assertEqual(self.snap()["samples"]["paper"], 3)
        self.assertEqual(self.tr.handle("test", {})[0], 200)
        wait_for(lambda: (self.snap().get("test") or {}).get("done"), 30)
        t = self.snap()["test"]
        self.assertEqual(len(t["results"]), 3)
        self.assertEqual(set(t["confusion"]), {"rock", "paper", "scissors"})

    def test_classes_follow_the_servers_gesture_set(self):
        self.ready()
        server.gsets().start("thumbs", ["up", "down", "nothing"])
        s = self.snap()
        self.assertEqual([c[0] for c in s["classes"]], ["up", "down", "nothing"])
        self.assertEqual(s["gestures"], ["up", "down"])
        self.assertEqual(self.tr.handle("train", {"cls": "rock"})[0], 400)  # not in the set any more
        self.assertEqual(self.tr.handle("train", {"cls": "up"})[0], 200)
        wait_for(lambda: self.snap()["phase"] != "idle", 5)
        self.idle()
        self.assertEqual(self.snap()["samples"]["up"], 3)

    def test_paused_while_a_game_runs(self):
        self.ready()
        self.addCleanup(setattr, server.TrainHost, "blocked", server.TrainHost.__dict__["blocked"])
        server.TrainHost.blocked = staticmethod(lambda: "A game is running")
        self.assertEqual(self.snap()["blocked"], "A game is running")
        code, out = self.tr.handle("forget", {})
        self.assertEqual(code, 409)
        self.assertIn("game", out["error"])

    def test_leaves_the_camera_alone_when_nobody_watches(self):
        self.ready()
        self.tr.viewed = 0.0
        time.sleep(0.5)
        before = self.fake.frame
        time.sleep(1.0)
        self.assertEqual(self.fake.frame, before)  # no polling: the frame counter didn't move


class TrainerVetoesTest(Base):
    def test_tools_refuse_while_the_page_runs_a_job(self):
        class Busy:
            def busy(self):
                return True

        self.addCleanup(setattr, server, "_trainer", server._trainer)
        server._trainer = Busy()
        with self.assertRaises(ToolError):
            server.start_machine("rps")
        with self.assertRaises(ToolError):
            server.train_gesture("rock", samples=1, interval_s=0, countdown_s=0)

    def test_stdio_side_server_serves_train_and_blocks_other_origins(self):
        self.addCleanup(setattr, train_ui, "LOG_DIR", train_ui.LOG_DIR)
        train_ui.LOG_DIR = Path(self.dir) / "vision"
        tr = train_ui.Trainer(False, None, None, shared=server.TrainHost())
        self.addCleanup(tr.join, 5)
        self.addCleanup(tr.stop)
        tr.start()
        game_view.serve_side("127.0.0.1:8799", lambda rev, ev: {}, train=(lambda: tr, server.train_page))
        base = "http://127.0.0.1:8799"
        page = urllib.request.urlopen(base + "/train").read().decode()
        self.assertIn("Train", page)
        state = json.loads(urllib.request.urlopen(base + "/train/api/state").read())
        self.assertIn("classes", state)
        req = urllib.request.Request(base + "/train/api/player", data=b'{"name":"ana"}', method="POST",
                                     headers={"Origin": "https://evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 403)
        req = urllib.request.Request(base + "/train/api/player", data=b'{"name":"ana"}', method="POST")
        self.assertEqual(json.loads(urllib.request.urlopen(req).read()), {"ok": True})


class TrainPanelTest(Base):
    """show_train: the Train page as an MCP App, driven through train_state / train_action like the game view."""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, train_ui, "LOG_DIR", train_ui.LOG_DIR)
        train_ui.LOG_DIR = Path(self.dir) / "vision"
        self.addCleanup(setattr, server, "_trainer", server._trainer)
        server._trainer = None
        self.addCleanup(lambda: server._trainer and (server._trainer.stop(), server._trainer.join(5)))

    def test_panel_resource_and_tools(self):
        page = server.train_resource()
        self.assertIn('const SOURCE="mcp"', page)
        self.assertIn('const SOURCE="http"', server.train_page())
        meta = {t.name: t.meta for t in server.mcp._tool_manager.list_tools()} if hasattr(server.mcp, "_tool_manager") else None
        if meta is not None:
            self.assertEqual(meta["show_train"]["ui"]["resourceUri"], server.TRAIN_URI)
            self.assertEqual(meta["train_state"]["ui"]["visibility"], ["app"])
        first = server.show_train()
        self.assertEqual([c[0] for c in first["classes"]], ["rock", "paper", "scissors", "nothing"])
        wait_for(lambda: server.train_state()["link"] == "ok", 10)
        self.assertEqual(server.train_action("settings", {"samples": 2, "interval": 0.2, "countdown": 1}), {"ok": True})
        self.assertEqual(server.train_action("train", {"cls": "rock"}), {"ok": True})
        self.assertIn("error", server.train_action("train", {"cls": "rock"}))  # busy: the panel shows the reason
        wait_for(lambda: server.train_state()["samples"]["rock"] == 2, 15)

    def test_instructions_open_both_panels_on_start_game(self):
        text = server.mcp.instructions
        self.assertIn("show_game", text)
        self.assertIn("show_train", text)
        self.assertIn("start game", text)
