"""Camera modes: any HuskyLens block algorithm as a machine input (see:<label> events), not just gestures.

    .venv/bin/python -m unittest tests.test_camera_modes
"""

from __future__ import annotations

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

import camera_modes as CM  # noqa: E402
import runlog  # noqa: E402
import screen  # noqa: E402
import server  # noqa: E402
from machine import Context, MachineError, apply_patch, validate  # noqa: E402
from machine_runtime import Runtime  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402
from test_machine import CTX, RuntimeCase, wait_for  # noqa: E402


def blk(id_, x=160, y=120, w=50, h=50):
    return {"id": id_, "x": x, "y": y, "w": w, "h": h}


def cards(**over) -> dict:
    """A QR-card game: show the red card to win, the blue one to lose."""
    m = {"name": "cards", "camera": {"mode": "qr", "labels": ["red", "blue"]},
         "settings": {"gesture_window_s": 0.02, "gesture_stable": 1},
         "vars": {"where": None, "n": 0}, "initial": "wait",
         "states": {"wait": {"on": {"see:red": {"do": [{"set": {"where": "$payload.x"}}, {"add": {"n": 1}}],
                                                 "to": "wait"},
                                    "see:blue": {"to": "lost"}}},
                    "lost": {"final": True}}}
    m.update(over)
    return m


class SummarizeTest(unittest.TestCase):
    NAMES = {1: "red", 2: "blue"}

    def test_labels_for_empty_unlearned_named_and_unnamed(self):
        self.assertEqual(CM.summarize([[]] * 3, self.NAMES)["label"], "nothing")
        self.assertEqual(CM.summarize([[blk(0)]] * 3, self.NAMES)["label"], "unknown")
        self.assertEqual(CM.summarize([[blk(2)]] * 3, self.NAMES)["label"], "blue")
        self.assertEqual(CM.summarize([[blk(7)]] * 3, self.NAMES)["label"], "id7")

    def test_the_biggest_learned_block_wins_and_the_payload_has_everything_in_frame(self):
        frame = [blk(0, w=200, h=200), blk(1, x=40, w=20, h=20), blk(2, x=250, w=60, h=60)]
        r = CM.summarize([frame] * 3, self.NAMES)
        self.assertEqual((r["label"], r["x"], r["count"]), ("blue", 250, 3))
        self.assertEqual(r["labels"], ["blue", "red", "unknown"])
        self.assertTrue(r["confident"])

    def test_majority_and_confidence(self):
        r = CM.summarize([[blk(1)], [blk(1)], [], [blk(2)]], self.NAMES)
        self.assertEqual((r["label"], r["confidence"], r["confident"]), ("red", 0.5, False))
        self.assertFalse(CM.summarize([], self.NAMES)["confident"])

    def test_mode_names_and_aliases(self):
        self.assertEqual(CM.mode_name("QR"), "qr_code")
        self.assertEqual(CM.mode_name("apriltag"), "tag")
        self.assertEqual(CM.mode_name("gestures"), "classification")
        self.assertEqual(CM.algorithm("colour"), "color_recognition")
        with self.assertRaises(CM.CameraModeError):
            CM.mode_name("line_tracking")   # arrows, not blocks


class ValidateTest(unittest.TestCase):
    def test_camera_is_normalised(self):
        m = validate(cards())
        self.assertEqual(m["camera"], {"mode": "qr_code", "labels": ["red", "blue"]})
        m = validate({"name": "t", "camera": "apriltag", "initial": "a",
                      "states": {"a": {"on": {"see:id3": {"to": "b"}}}, "b": {"final": True}}})
        self.assertEqual(m["camera"], {"mode": "tag"})

    def test_classification_labels_are_the_gestures(self):
        m = validate({"name": "t", "camera": {"mode": "gestures", "labels": ["up", "down"]}, "initial": "a",
                      "states": {"a": {"on": {"see:up": {"to": "b"}, "gesture:down": {"to": "b"}}}, "b": {"final": True}}})
        self.assertEqual((m["gestures"], m["camera"]), (["up", "down"], {"mode": "classification"}))

    def test_bad_cameras_and_events_are_rejected(self):
        bad = [
            cards(camera="line_tracking"),
            cards(camera={"mode": "qr", "labels": ["red", "nothing"]}),         # reserved
            cards(camera={"mode": "qr", "labels": ["red", "red"]}),
            cards(camera={"mode": "qr", "extra": 1}),
            {**cards(), "states": {"wait": {"on": {"gesture:red": {"to": "lost"}}}, "lost": {"final": True}}},
            {**cards(), "states": {"wait": {"on": {"see:green": {"to": "lost"}}}, "lost": {"final": True}}},
        ]
        for m in bad:
            with self.assertRaises(MachineError, msg=str(m.get("camera"))):
                validate(m)

    def test_nothing_unknown_and_unnamed_ids_are_always_valid_in_detection_modes(self):
        validate({**cards(), "states": {"wait": {"on": {"see:nothing": {"to": "wait"}, "see:unknown": {"to": "wait"},
                                                       "see:id4": {"to": "lost"}}}, "lost": {"final": True}}})

    def test_labels_are_checked_against_the_camera_when_the_machine_names_none(self):
        ctx = Context(labels={"tag": {"door", "key"}})
        m = {"name": "t", "camera": "tag", "initial": "a", "states": {"a": {"on": {"see:door": {"to": "b"}}},
                                                                       "b": {"final": True}}}
        validate(m, ctx)
        m["states"]["a"]["on"] = {"see:window": {"to": "b"}}
        with self.assertRaises(MachineError):
            validate(m, ctx)

    def test_set_camera_patch(self):
        new, _, notes = apply_patch(validate(cards()), [{"op": "set_camera", "camera": "barcode"}])
        self.assertEqual(validate(new)["camera"], {"mode": "barcode"})


class CameraRuntimeTest(RuntimeCase):
    """A machine in a detection mode, on a scripted camera function."""

    def setUp(self):
        super().setUp()
        self.frames = []          # the camera's current view, as blocks
        self.modes = []

    def camera(self, mode, window_s):
        self.modes.append(mode)
        time.sleep(0.003)
        return CM.summarize([list(self.frames)] * 3, {1: "red", 2: "blue"})

    def cam_runtime(self) -> Runtime:
        g = self.make()
        self.rt = rt = Runtime(g, reader=self.reader, camera=self.camera)
        self.g = g
        self.addCleanup(rt.stop)
        return rt

    def test_see_fires_once_per_change_with_the_position(self):
        rt = self.cam_runtime()
        rt.start(validate(cards()))
        self.frames = [blk(1, x=99)]
        wait_for(lambda: rt.snapshot_vars()["n"] == 1)
        time.sleep(0.1)
        self.assertEqual(rt.snapshot_vars(), {"where": 99, "n": 1}, "a held card fires once")
        self.frames = []
        wait_for(lambda: any(e["event"] == "see:nothing" for e in rt.snapshot()["events"]))
        self.frames = [blk(1, x=10)]
        wait_for(lambda: rt.snapshot_vars()["n"] == 2)
        self.assertEqual(rt.snapshot_vars()["where"], 10)
        self.frames = [blk(2)]
        rt.wait_finished(2)
        self.assertEqual((rt.status, rt.state), ("finished", "lost"))
        self.assertEqual(set(self.modes), {"qr_code"})
        self.assertEqual(self.reads, 0, "the gesture reader is never used in qr mode")

    def test_read_action_in_the_machines_mode(self):
        rt = self.cam_runtime()
        self.frames = [blk(2, x=5)]
        rt.start(validate(cards(states={
            "wait": {"on_enter": [{"read": {"into": "r"}}], "on": {"enter": {"to": "done"}}}, "done": {"final": True}})))
        rt.wait_finished(2)
        r = rt.snapshot_vars()["r"]
        self.assertEqual((r["label"], r["x"], r["confident"]), ("blue", 5, True))

    def test_a_detection_machine_needs_a_camera(self):
        rt = Runtime(self.make(), reader=self.reader)
        with self.assertRaises(MachineError):
            rt.start(validate(cards()))

    def test_a_classification_state_may_listen_with_see(self):
        rt = self.cam_runtime()
        rt.start(validate({"name": "g", "gestures": ["up", "down"],
                           "settings": {"gesture_window_s": 0.02, "gesture_stable": 1}, "initial": "a",
                           "states": {"a": {"on": {"see:up": {"to": "b"}}}, "b": {"final": True}}}, CTX))
        self.look("up")
        rt.wait_finished(2)
        self.assertEqual(rt.status, "finished")
        self.assertTrue(any(e["event"] == "see:up" and e["handled"] for e in rt.snapshot()["events"]))

    def test_the_view_shows_see_labels_like_gestures(self):
        m = validate(cards())
        blocks, _ = screen.resolve(m, "wait", {"n": 0, "where": None},
                                   [{"event": "see:red", "state": "wait", "handled": True}], "running")
        chips = next(b for b in blocks if b["kind"] == "gestures")
        self.assertEqual([(c["name"], c["lit"]) for c in chips["items"]], [("red", True), ("blue", False)])


class ServerCameraModesTest(unittest.TestCase):
    """Through the MCP tool functions on the fake camera: a QR machine next to the RPS gestures."""

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

    def test_set_up_train_read_and_play_a_qr_machine(self):
        with self.assertRaises(ToolError):   # the camera has no qr labels yet
            server.define_machine(cards())
        out = server.define_machine(cards(), swap_gestures=True)
        self.assertEqual((out["camera_mode"], out["camera_ids"]), ("qr_code", {"red": 1, "blue": 2}))
        self.assertEqual(sum(self.fake.learned[6].values()), 60, "the rps gestures are untouched")
        for label in ("red", "blue"):
            self.assertEqual(server.train_gesture(label, samples=2, interval_s=0, countdown_s=0, mode="qr")["accepted"], 2)
        self.assertEqual(dict(self.fake.learned[7]), {1: 2, 2: 2})
        with self.assertRaises(ToolError):
            server.train_gesture("nothing", samples=1, interval_s=0, countdown_s=0, mode="qr")

        self.fake.pose = 2
        self.assertEqual(server.read_gesture(0.1, mode="qr")["label"], "blue")
        self.fake.pose = 1
        self.assertEqual(server.read_gesture(0.1)["gesture"], "rock", "classification reads switch back")
        self.assertEqual(server.gesture_set()["labels"]["qr_code"]["ids"], {"red": 1, "blue": 2})

        self.fake.pose = 1
        server.start_machine("cards")
        rt = server.get_runtime()
        wait_for(lambda: rt.snapshot_vars()["n"] >= 1)
        self.fake.pose = 2
        rt.wait_finished(3)
        self.assertEqual((rt.status, rt.state), ("finished", "lost"))
        self.assertEqual([m for m in server.list_machines() if m["name"] == "cards"][0]["camera"], "qr_code")

    def test_unnamed_ids_need_no_setup(self):
        m = cards(camera="tag", states={"wait": {"on": {"see:id1": {"to": "lost"}}}, "lost": {"final": True}})
        server.define_machine(m)
        self.fake.pose = 1
        self.fake.learned.setdefault(5, __import__("collections").Counter())[1] = 3
        server.start_machine("cards")
        rt = server.get_runtime()
        rt.wait_finished(3)
        self.assertEqual(rt.state, "lost")


if __name__ == "__main__":
    unittest.main()
