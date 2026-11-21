"""Game engine tests (T9-T12, T18, NFR-01, NFR-09). All on FakeBridge/FakeCamera, no real sleeping.

    .venv/bin/python -m unittest discover tests
"""

from __future__ import annotations

import json
import random
import shutil
import sys
import tempfile
import threading
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tools"))

from banter import BUILTIN, Banter  # noqa: E402
from bridge import FakeBridge  # noqa: E402
from connect import FakeCamera  # noqa: E402
from game import Game, GameError, Store, validate_sequence  # noqa: E402
from hardware import Hardware  # noqa: E402
from huskylens import HuskyLens  # noqa: E402
from vision import HuskyVision, VisionSource  # noqa: E402

FAST = {"countdown_period_ms": 60, "read_delay_s": 0.0, "read_window_s": 0.001, "result_hold_s": 0.0,
        "show_beats": False, "speak_banter": False, "face_samples": 2, "id_window_s": 0.05}


class FakeCam:
    """What server.Camera gives the game: a lock, get() -> HuskyLens, link."""

    def __init__(self, camera=None):
        self.link = FakeBridge(camera or FakeCamera(pretrained=True, seed=1))
        self.lock = threading.RLock()
        self.hl = HuskyLens(self.link)

    def get(self):
        return self.hl


class FirstRng:
    """Always takes the first option (rock when the pool is classic) and 'heads'."""

    def choice(self, seq):
        return list(seq)[0]

    def random(self):
        return 0.0


class StubVision(VisionSource):
    """Scripted reads for tests that don't need the camera."""

    name = "huskylens"

    def __init__(self, reads):
        self.reads = reads  # list of moves (consumed in order, last one repeats) or a callable

    def read_throw(self, window_s, id_to_move):
        move = self.reads() if callable(self.reads) else (self.reads.pop(0) if len(self.reads) > 1 else self.reads[0])
        return {"move": move, "id": 1, "share": 1.0, "reads": 1, "votes": {1: 1}, "read_ms": 1}

    def identify_face(self, window_s=1.5):
        return {"face_id": None, "faces_seen": [], "max_faces_in_frame": 0}

    def learn_face(self, face_id, name, samples=5):
        return {"face_id": face_id}


class GameCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.games: list[Game] = []

    def tearDown(self):
        for g in self.games:
            g.wait_idle()
            g.hw.close()

    def make(self, vision=None, rng=None, camera=None, data_dir=None, settings=None) -> Game:
        cam = FakeCam(camera)
        d = data_dir or self.tmp
        hw = Hardware(lambda: cam.link, cam.lock, d, tts=False)
        g = Game(hw, vision or HuskyVision(cam, sleep=lambda s: None), Banter(d, random.Random(3)), d,
                 sleep=lambda s: None, rng=rng or random.Random(7))
        g.cam, g.bridge = cam, cam.link
        g.settings.update(FAST)
        g.settings.update(settings or {})
        self.games.append(g)
        return g

    def see(self, g: Game, move_id):
        g.bridge.camera.pose = move_id


class TestRounds(GameCase):
    def test_t10_fair_is_fair_and_commits_first(self):
        g = self.make(vision=StubVision(["rock"]))
        counts, before = Counter(), []
        for _ in range(300):
            r = g.play_round("fair", "guest")
            counts[r["robot"]] += 1
            before.append(r["timings"]["committed_before_shoot"])
        for move in ("rock", "paper", "scissors"):
            self.assertAlmostEqual(counts[move] / 300, 1 / 3, delta=0.05, msg=str(counts))
        self.assertTrue(all(before))

    def test_fair_commits_before_the_read_on_the_camera_path(self):
        g = self.make()
        self.see(g, 1)
        r = g.play_round("fair", "guest")
        self.assertTrue(r["timings"]["committed_before_shoot"])
        self.assertIsNone(r["timings"]["reveal_lag_ms"])
        self.assertLessEqual(r["timings"]["returned_ms_after_shoot"], 1500)  # NFR-01

    def test_t9_rigged_win_wins_every_round(self):
        g = self.make()
        for pose in (1, 2, 3) * 7:
            self.see(g, pose)
            r = g.play_round("rigged_win", "guest")
            self.assertEqual(r["outcome"], "robot", r)
            self.assertFalse(r["timings"]["committed_before_shoot"])
            self.assertIsNotNone(r["timings"]["reveal_lag_ms"])
            self.assertLessEqual(r["timings"]["reveal_lag_ms"], 250)
            self.assertGreaterEqual(r["timings"]["commit_t"], r["timings"]["read_done_t"])
        self.assertEqual(g.session["robot"], 21)

    def test_rigged_lose_always_loses(self):
        g = self.make()
        for pose in (1, 2, 3) * 3:
            self.see(g, pose)
            r = g.play_round("mercy", "guest")  # alias of rigged_lose
            self.assertEqual(r["outcome"], "human", r)
            self.assertEqual(r["mode"], "rigged_lose")

    def test_rules_outcomes_and_verbs(self):
        g = self.make(rng=FirstRng())  # robot throws rock
        self.see(g, 1)
        r = g.play_round("fair", "guest")
        self.assertEqual((r["robot"], r["human"], r["outcome"]), ("rock", "rock", "draw"))
        self.see(g, 2)
        r = g.play_round("fair", "guest")
        self.assertEqual((r["outcome"], r["verb"]), ("human", "covers"))
        self.see(g, 3)
        r = g.play_round("fair", "guest")
        self.assertEqual((r["outcome"], r["verb"]), ("robot", "crushes"))
        p = g.get_score("guest")["player"]
        self.assertEqual((p["wins"], p["losses"], p["draws"]), (1, 1, 1))
        self.assertEqual(g.session, {"robot": 1, "human": 1, "draws": 1, "rounds": 3})

    def test_unknown_read_replays_then_no_read(self):
        g = self.make()
        self.see(g, 0)  # seen but not learned -> unknown
        r = g.play_round("fair", "guest")
        self.assertEqual(r["outcome"], "no_read")
        self.assertEqual(r["replays"], 2)
        self.assertEqual(g.session["rounds"], 0)
        self.assertEqual(g.get_score("guest")["player"]["draws"], 0)

    def test_replay_then_success(self):
        g = self.make(vision=StubVision(["unknown", "rock"]))
        r = g.play_round("rigged_win", "guest")
        self.assertEqual((r["replays"], r["outcome"], r["robot"]), (1, "robot", "paper"))

    def test_hand_relaxed_after_round(self):  # NFR-09
        g = self.make()
        self.see(g, 1)
        g.play_round("rigged_win", "guest")
        g.wait_idle()
        self.assertIsNone(g.hw.fingers)
        self.assertEqual(g.bridge.us, [None] * 5)
        self.assertEqual(g.hw.held_closed_s(), 0.0)

    def test_hold_is_trimmed_to_stay_under_the_limit(self):
        g = self.make(settings={"result_hold_s": 99})
        slept = []
        g.sleep = slept.append
        self.see(g, 1)
        g.play_round("fair", "guest")
        g.wait_idle()
        self.assertLessEqual(max(slept), 4.5)

    def test_predict_exploits_a_pattern(self):
        g = self.make(vision=StubVision(["rock"]))
        results = [g.play_round("predict", "guest") for _ in range(12)]
        self.assertTrue(all(r["robot"] == "paper" for r in results[4:]), [r["robot"] for r in results])
        self.assertEqual(results[-1]["prediction"]["basis"], "markov")

    def test_grudge_turns_rigged_after_the_player_wins(self):
        g = self.make(rng=FirstRng(), vision=StubVision(["paper"]))  # robot rock vs paper: human wins round 1
        r1 = g.play_round("grudge", "guest")
        self.assertEqual((r1["outcome"], r1["effective_mode"]), ("human", "fair"))
        self.assertTrue(r1["grudge_now_active"])
        for _ in range(3):
            r = g.play_round("grudge", "guest")
            self.assertEqual((r["outcome"], r["effective_mode"]), ("robot", "rigged_win"))

    def test_bribery_throws_the_match_with_gold_leds(self):
        g = self.make()
        self.see(g, 1)
        r = g.play_round("bribery", "guest")
        g.wait_idle()
        self.assertEqual(r["outcome"], "human")
        self.assertTrue(r["bribed"])
        self.assertEqual(g.bridge.leds[0], "#ffb000")

    def test_bribe_card_through_identify_player(self):
        g = self.make()
        self.see(g, 1)
        g.register_player("Ann")  # face 1
        r = g.identify_player(check_bribe=True)
        self.assertEqual((r["player"], r["known"], r["bribed"], r["bribe_check"]), ("Ann", True, False, "no card"))
        g.bridge.camera.learned[7] = Counter({1: 1})  # the card is learned in QR recognition
        r = g.identify_player(check_bribe=True)
        self.assertTrue(r["bribed"])
        self.assertEqual(g.play_round(None)["outcome"], "human")  # Ann is fair by default, but bribed
        g.reset_score("session")
        self.assertFalse(g.bribed)

    def test_bribe_check_fails_softly(self):
        g = self.make(vision=StubVision(["rock"]))
        g.vision.see_id = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no QR here"))
        r = g.identify_player(check_bribe=True)
        self.assertFalse(r["bribed"])
        self.assertIn("could not check", r["bribe_check"])

    def test_stranger_gets_rigged_win_by_default(self):
        g2 = self.make(vision=StubVision(["rock"]), data_dir=self.tmp / "b")
        r = g2.identify_player()
        self.assertEqual((r["player"], r["known"], r["mode"]), ("stranger", False, "rigged_win"))
        self.assertEqual(g2.play_round()["effective_mode"], "rigged_win")
        g3 = self.make(data_dir=self.tmp / "c")  # camera path: a face that is seen but not learned (ID 0)
        self.see(g3, 0)
        r = g3.identify_player()
        self.assertEqual((r["player"], r["known"]), ("stranger", False))

    def test_sore_loser_tantrum_and_demand(self):
        g = self.make(vision=StubVision(["paper"]), rng=FirstRng())  # robot rock loses to paper
        r = g.play_round("sore_loser", "guest")
        g.wait_idle()
        self.assertEqual(r["outcome"], "human")
        self.assertIn("Best of 3", r["banter"])
        poses = [c for c in g.bridge.calls if c["cmd"] == "pose"]
        self.assertGreater(len(poses), 12)  # countdown pump + the tantrum
        self.assertIsNone(g.hw.fingers)

    def test_bluff_announces_and_stays_fair(self):
        g = self.make(vision=StubVision(["rock"]))
        r = g.play_round("bluff", "guest")
        self.assertIn(r["bluff"]["announced"], ("rock", "paper", "scissors"))
        self.assertEqual(r["bluff"]["honest"], r["bluff"]["announced"] == r["robot"])
        self.assertTrue(r["timings"]["committed_before_shoot"])

    def test_hooks_run_after_the_round(self):
        g = self.make(vision=StubVision(["rock"]))
        g.define_sequence("flash", [{"led": "#123456"}, {"wait": 60}])
        g.attach_sequence("on_win", "flash")
        g.play_round("rigged_win", "guest")  # robot wins -> on_win
        g.wait_idle()
        self.assertEqual(g.bridge.leds[0], "#123456")
        g.attach_sequence("on_win", None)
        g.play_round("rigged_win", "guest")
        g.wait_idle()
        self.assertNotEqual(g.bridge.leds[0], "#123456")

    def test_match_best_of_five(self):
        g = self.make()
        self.see(g, 1)
        m = g.play_match(5, "rigged_win", "guest")
        self.assertEqual((m["winner"], m["match_score"]), ("robot", {"robot": 3, "human": 0}))
        self.assertEqual(len(m["rounds"]), 3)
        self.assertTrue(m["banter"])
        with self.assertRaises(GameError):
            g.play_match(4)

    def test_listener_errors_never_break_a_round(self):
        g = self.make(vision=StubVision(["rock"]))
        events = []
        g.listeners += [lambda e: 1 / 0, events.append]
        g.play_round("fair", "guest")
        self.assertIn("report", [e["event"] for e in events])

    def test_round_logged_to_history(self):
        g = self.make(vision=StubVision(["rock"]))
        g.play_round("fair", "guest")
        g.play_round("fair", "guest")
        g.wait_idle()
        lines = (self.tmp / "history.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[1])["round"], 2)


class TestData(GameCase):
    def test_defaults_on_first_run(self):
        g = self.make()
        self.assertEqual([m["name"] for m in g.list_moves()], ["rock", "paper", "scissors"])
        self.assertEqual({m["name"]: m["led"] for m in g.list_moves()},
                         {"rock": "#ff0000", "paper": "#00ff00", "scissors": "#0040ff"})
        self.assertEqual(len(g.list_rules()), 3)
        self.assertEqual({s["name"] for s in g.list_sequences()}, {"tantrum", "victory"})
        for f in ("moves", "rules", "players", "sequences", "settings"):
            self.assertTrue((self.tmp / f"{f}.json").exists())

    def test_t12_persistence(self):
        g = self.make()
        g.define_move("claw", [0, 0, 1, 1], "#ff00ff", husky_id=5)
        g.define_rule("claw", "paper", "pierces")
        g.set_player_mode("guest", "predict")
        g.define_sequence("wave", [{"pose": [1, 1, 1, 1], "ms": 100}, {"pose": [0, 1, 1, 1], "ms": 100}, {"repeat": 2}])
        g.attach_sequence("on_loss", "wave", player="guest")
        g.vision = StubVision(["rock"])
        g.play_round("fair", "guest")
        g.wait_idle()
        g2 = self.make(data_dir=self.tmp)  # a fresh process on the same files
        self.assertIn("claw", [m["name"] for m in g2.list_moves()])
        self.assertIn({"winner": "claw", "loser": "paper", "verb": "pierces"}, g2.list_rules())
        p = g2.list_players()[0]
        self.assertEqual((p["name"], p["mode"], p["hooks"]), ("guest", "predict", {"on_loss": "wave"}))
        self.assertEqual(p["wins"] + p["losses"] + p["draws"], 1)
        self.assertIn("wave", [s["name"] for s in g2.list_sequences()])
        self.assertEqual(g2.round_no, 1)

    def test_corrupt_file_is_quarantined(self):
        (self.tmp / "moves.json").write_text("{not json")
        st = Store(self.tmp)
        self.assertEqual(len(st.get("moves")), 3)
        self.assertTrue(st.warnings)
        self.assertTrue(list(self.tmp.glob("moves.corrupt-*")))

    def test_move_validation(self):
        g = self.make()
        with self.assertRaises(GameError):
            g.define_move("bad", [0, 2, 0, 0], "#ffffff")
        with self.assertRaises(GameError):
            g.define_move("bad", [0, 0], "#ffffff")
        with self.assertRaises(GameError):
            g.define_move("bad", [0, 1, 0], "#ffffff")  # middle alone: refused
        with self.assertRaises(GameError):
            g.define_move("bad", [0, 0, 0, 0], "red")
        with self.assertRaises(GameError):
            g.define_move("bad", [0, 0, 0, 0], "#ffffff", husky_id=1)  # rock's
        with self.assertRaises(GameError):
            g.define_move("nothing", [0, 0, 0, 0], "#ffffff")
        self.assertTrue(g.define_move("rock", [0, 0, 0, 0], "#aa0000", husky_id=1)["updated"])

    def test_rule_validation(self):
        g = self.make()
        with self.assertRaises(GameError):
            g.define_rule("rock", "paper")  # paper already covers rock
        with self.assertRaises(GameError):
            g.define_rule("rock", "rock")
        with self.assertRaises(GameError):
            g.define_rule("rock", "lizard")
        g.define_move("claw", [0, 0, 1, 1], "#ff00ff")
        g.define_rule("claw", "paper", "pierces")
        with self.assertRaises(GameError):
            g.define_rule("paper", "claw")
        self.assertTrue(g.define_rule("claw", "paper", "rips")["updated"])
        g.delete_rule("claw", "paper")
        with self.assertRaises(GameError):
            g.delete_rule("claw", "paper")

    def test_custom_move_is_thrown_and_beaten(self):
        g = self.make(rng=FirstRng())
        g.define_move("claw", [0, 0, 1, 1], "#ff00ff", husky_id=5)
        g.define_rule("claw", "paper", "pierces")
        g.bridge.camera.learned[6][5] = 5
        self.see(g, 5)
        self.assertEqual(g._pool(), ["rock", "paper", "scissors", "claw"])
        r = g.play_round("rigged_win", "guest")  # human throws claw; rock/scissors don't beat it, nothing does
        self.assertEqual(r["human"], "claw")
        self.assertEqual(r["outcome"], "draw")  # no counter exists: the robot matches it
        r = g.play_round("rigged_lose", "guest")  # claw beats paper: the robot throws paper and loses
        self.assertEqual((r["robot"], r["outcome"], r["verb"]), ("paper", "human", "pierces"))

    def test_delete_move_cascades_and_classic_restores(self):
        g = self.make()
        res = g.delete_move("rock")
        self.assertEqual(len(res["rules_removed"]), 2)
        self.assertEqual(g._pool(), ["paper", "scissors"])
        out = g.load_ruleset("classic")
        self.assertEqual(out["moves_restored"], ["rock"])
        self.assertEqual(len(g.list_rules()), 3)

    def test_players(self):
        g = self.make()
        a = g.register_player("Ann")
        b = g.register_player("Bo")
        self.assertEqual((a["face_id"], b["face_id"]), (1, 2))
        with self.assertRaises(GameError):
            g.register_player("ann")
        with self.assertRaises(GameError):
            g.set_player_mode("Ann", "chaos")
        self.assertEqual(g.set_player_mode("ann", "Rigged-Win")["mode"], "rigged_win")
        self.assertEqual(g.set_player_mode("Bo", "mercy")["mode"], "rigged_lose")
        with self.assertRaises(GameError):
            g.get_score("nobody")
        self.assertTrue(g.reset_score("all")["needs_confirm"])
        self.assertEqual(g.reset_score("all", confirm=True)["reset"], "all")

    def test_identify_known_player_and_spectators(self):
        g = self.make()
        g.register_player("Ann")
        self.see(g, 1)
        r = g.identify_player()
        self.assertEqual((r["player"], r["known"], r["spectators"]), ("Ann", True, 0))
        self.assertEqual(g.play_round(None)["player"], "Ann")

    def test_set_vision_source(self):
        from vision import MediaPipeVision, VisionError
        g = self.make()
        g.vision_sources = {"mediapipe": lambda: MediaPipeVision()}
        with self.assertRaises(VisionError):
            g.set_vision_source("mediapipe")
        self.assertFalse(g.set_vision_source("huskylens")["changed"])

    def test_banter_preview_does_not_consume(self):
        g = self.make()
        before = list(g.banter.recent)
        self.assertTrue(g.banter_preview({"outcome": "human", "robot": "rock", "human": "paper"})["line"])
        self.assertEqual(list(g.banter.recent), before)


class TestSequences(GameCase):
    def bad(self, steps):
        with self.assertRaises(GameError):
            validate_sequence(steps)

    def test_t18_rejections(self):
        self.bad([{"pose": [0, 1.5, 0], "ms": 100}])
        self.bad([{"pose": [0, 0], "ms": 100}])
        self.bad([{"pose": [0, 1, 0], "ms": 100}])                              # middle alone
        self.bad([{"pose": [1, 1, 1, 1], "ms": 6000}, {"wait": 6000}])           # > 10 s
        self.bad([{"pose": [1, 1, 1, 1], "ms": 900}, {"repeat": 11}])            # too many repeats
        self.bad([{"pose": [1, 1, 1, 1], "ms": 900}, {"repeat": 10}, {"wait": 2000}])  # 11 s with repeats
        self.bad([{"pose": [0, 0, 0, 0], "ms": 150}, {"wait": 5500}])           # closed > 5 s
        self.bad([{"pose": [0, 0, 0, 0], "ms": 2000}, {"repeat": 3}])           # 6 s closed via repeats
        self.bad([{"repeat": 2}])
        self.bad([{"led": "nope"}])
        self.bad([{"voice": "record"}])
        self.bad([{"jump": 1}])
        self.bad([])

    def test_closed_then_open_is_fine(self):
        _, _, info = validate_sequence([{"pose": [0, 0, 0, 0], "ms": 2000}, {"pose": [1, 1, 1, 1], "ms": 200},
                                        {"repeat": 2}])
        self.assertEqual(info["total_ms"], 4400)
        self.assertLessEqual(info["longest_closed_ms"], 2000)

    def test_min_step_is_clamped_and_reported(self):
        stored, _, info = validate_sequence([{"pose": [1, 1, 1, 1], "ms": 10}, {"wait": 5}])
        self.assertEqual((stored[0]["ms"], stored[1]["wait"]), (60, 60))
        self.assertEqual(len(info["clamped"]), 2)

    def test_repeat_means_n_times_in_total(self):
        _, unrolled, info = validate_sequence([{"pose": [1, 1, 1, 1], "ms": 100}, {"pose": [0, 1, 1, 1], "ms": 100},
                                               {"repeat": 3}, {"led": "#ffffff"}])
        self.assertEqual(len(unrolled), 7)  # A B A B A B led
        self.assertEqual(info["total_ms"], 600)

    def test_define_play_edit_replay_and_relaxes(self):
        g = self.make()
        d = g.define_sequence("Dance", [{"pose": [0, 1, 1, 0], "ms": 120}, {"pose": [1, 1, 1, 1], "ms": 120},
                                        {"repeat": 3}, {"led": "#00ff00"}, {"voice": "play"}])
        self.assertEqual((d["name"], d["updated"], d["total_ms"]), ("dance", False, 720))
        r = g.play_sequence("dance")
        self.assertEqual(r["steps_executed"], 8)
        self.assertEqual(g.bridge.us, [None] * 5)  # relaxed at the end
        self.assertEqual(g.bridge.leds[0], "#00ff00")
        self.assertEqual(g.bridge.voice, {"mode": "play", "ms": 1000})
        self.assertTrue(g.define_sequence("dance", [{"led": "#ff0000"}])["updated"])
        g.play_sequence("dance")
        self.assertEqual(g.bridge.leds[0], "#ff0000")
        with self.assertRaises(GameError):
            g.define_sequence("dance", [{"pose": [0, 0, 0, 7]}])
        self.assertEqual(g.list_sequences()[-1]["steps"], [{"led": "#ff0000"}])  # a rejected edit changes nothing

    def test_sequence_relaxes_even_if_the_link_fails_midway(self):
        g = self.make()
        orig = g.hw.led_fill
        g.hw.led_fill = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            g.play_sequence("victory")
        g.hw.led_fill = orig
        self.assertEqual(g.bridge.us, [None] * 5)

    def test_delete_detaches(self):
        g = self.make()
        g.attach_sequence("on_win", "victory")
        res = g.delete_sequence("victory")
        self.assertEqual(res["detached"], ["global on_win"])
        self.assertIsNone(g.settings["hooks"]["on_win"])
        with self.assertRaises(GameError):
            g.attach_sequence("on_win", "victory")
        with self.assertRaises(GameError):
            g.attach_sequence("on_sneeze", None)

    def test_perform_move_shows_then_relaxes(self):
        g = self.make()
        g.perform_move("scissors")
        g.wait_idle()
        self.assertEqual(g.bridge.leds[0], "#0040ff")
        self.assertEqual(g.bridge.us, [None] * 5)

    def test_hardware_soft_limits(self):
        g = self.make()
        g.hw.calibrate(0, open_us=1200, closed_us=1800)
        g.hw.pose([0, 1, 1], 0)
        self.assertEqual(g.bridge.us[0], 1800)
        self.assertEqual(g.bridge.us[1], 1000)  # uncalibrated default endpoints
        with self.assertRaises(Exception):
            g.hw.pose([0, 1, 3], 0)

    def test_legacy_four_value_poses(self):
        """[thumb, index, middle, ring+little] from the old hand: thumb dropped, middle follows the index."""
        g = self.make()
        self.assertEqual(g.hw.pose([1, 0, 1, 0], 0)["fingers"], [0.0, 0.0, 0.0])  # demo-day fist, not a middle finger
        self.assertEqual(g.hw.pose([0, 1, 1, 0], 0)["fingers"], [1.0, 1.0, 0.0])  # scissors
        self.assertEqual(g.hw.pose([1, 1, 1, 1], 0)["fingers"], [1.0, 1.0, 1.0])
        self.assertEqual(len(g.bridge.calls[-1]["us"]), 3)

    def test_saved_four_value_moves_and_sequences_migrate(self):
        g = self.make()
        moves = g.store.get("moves")
        moves[2]["fingers"] = [0, 1, 1, 0]
        g.store.get("sequences")[0]["steps"][0]["pose"] = [0, 0, 0, 0]
        g.store.save("moves")
        g.store.save("sequences")
        g2 = Game(g.hw, g.vision, g.banter, g.store.dir, sleep=lambda s: None)
        self.assertEqual(g2._move("scissors")["fingers"], [1.0, 1.0, 0.0])
        self.assertEqual(g2.store.get("sequences")[0]["steps"][0]["pose"], [0.0, 0.0, 0.0])


class TestBanter(unittest.TestCase):
    FORBIDDEN = ("rigged", "mercy", "brib", "cheat", "predict", "fixed", "mode")

    def test_builtin_lines_never_reveal_a_mode(self):
        self.assertGreaterEqual(len(BUILTIN), 40)
        for l in BUILTIN:
            low = l["text"].lower()
            for w in self.FORBIDDEN:
                self.assertNotIn(w, low, l["text"])

    def test_no_repeat_within_ten_and_never_mismatched(self):
        rng = random.Random(11)
        b = Banter(None, random.Random(5))
        moves = ["rock", "paper", "scissors"]
        recent: list[str] = []
        for _ in range(400):
            o = rng.choice(["robot", "human", "draw"])
            robot, human = rng.choice(moves), rng.choice(moves)
            state = {"outcome": o, "player": "Ann", "robot": robot, "human": human,
                     "verb": None if o == "draw" else "beats", "robot_score": rng.randint(0, 4),
                     "human_score": rng.randint(0, 4), "family": rng.choice(["fair", "win", "lose", "sore", "bluff"]),
                     "streak": rng.randint(1, 5), "streak_by": rng.choice(["robot", "human"]),
                     "bribed": rng.random() < 0.2, "spectators": rng.randint(0, 2), "demand": 3,
                     "announced": "rock", "honest": rng.random() < 0.5}
            line = b.line(state)
            self.assertNotIn(line, recent[-10:])
            self.assertNotIn("{", line)
            recent.append(line)
            for w in self.FORBIDDEN:
                self.assertNotIn(w, line.lower())

    def test_conditions_are_respected(self):
        b = Banter(None, random.Random(1))
        for _ in range(40):
            line = b.line({"outcome": "draw", "player": "Ann", "robot": "rock", "human": "rock", "verb": None,
                           "robot_score": 1, "human_score": 1, "family": "fair"}, record=False)
            self.assertNotIn("Point to", line)
            self.assertNotIn("Best of", line)
        for _ in range(10):  # a glare replay always blames the light
            line = b.line({"outcome": "replay", "glare": True, "player": "A"}, record=False).lower()
            self.assertTrue("glare" in line or "light" in line, line)
        for _ in range(20):  # a plain misread (no glare) never blames glare
            self.assertNotIn("glare", b.line({"outcome": "replay", "glare": False, "player": "A"}, record=False))

    def test_loads_claude_bank_and_prefers_specific_lines(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        (d / "banter.json").write_text(json.dumps([
            {"text": "Custom {player} line", "when": {"outcome": "robot", "bribed": False, "streak": {"min": 5}}},
            {"text": "broken"}, {"nope": 1}]))
        b = Banter(d, random.Random(2))
        self.assertEqual(b.loaded, 2)
        state = {"outcome": "robot", "player": "Ann", "robot": "rock", "human": "scissors", "verb": "crushes",
                 "bribed": False, "streak": 5, "robot_score": 5, "human_score": 0, "family": "fair"}
        self.assertEqual(b.line(state), "Custom Ann line")
        state["streak"] = 2
        self.assertNotEqual(b.line(state), "Custom Ann line")

    def test_model_seam_falls_back_to_the_bank(self):
        b = Banter(None, random.Random(2))
        b.set_model(lambda s: "model line")
        self.assertEqual(b.line({"outcome": "draw", "player": "A", "robot": "rock"}), "model line")
        b.set_model(lambda s: 1 / 0)
        self.assertTrue(b.line({"outcome": "draw", "player": "A", "robot": "rock", "human": "rock"}))


class TestMcpSmoke(unittest.TestCase):
    """Import server.py in fake mode and call the tool functions directly (no stdio)."""

    @classmethod
    def setUpClass(cls):
        import server

        cls.server = server
        cls.tmp = Path(tempfile.mkdtemp())
        server.cam.kind = "fake"
        server.OPTIONS["data_dir"] = cls.tmp
        server.reset_game()
        g = server.get_game()
        g.settings.update(FAST)
        g.sleep = lambda s: None

    @classmethod
    def tearDownClass(cls):
        cls.server.reset_game()
        cls.server.cam.close()
        shutil.rmtree(cls.tmp, True)

    def test_tools(self):
        s = self.server
        self.assertEqual(len(s.list_moves()), 3)
        s.define_move("claw", [0.5, 0.5, 0.5], "#ff00ff", 5)
        self.assertIn("claw", [m["name"] for m in s.list_moves()])
        s.define_rule("claw", "paper", "pierces")
        r = s.play_round("fair", "guest")
        self.assertIn(r["outcome"], ("robot", "human", "draw", "no_read"))
        self.assertTrue(s.speak("hello")["spoken"] is False)  # fake mode: speech off
        self.assertEqual(s.hand_pose([1, 1, 0], 100)["fingers"], [1.0, 1.0, 0.0])
        self.assertTrue(s.hand_relax()["relaxed"])
        self.assertIn("line", s.banter_preview({"outcome": "robot"}))
        self.assertEqual(s.get_score()["session"]["rounds"] <= 1, True)
        self.assertEqual(s.reset_score("session")["reset"], "session")

    def test_errors_become_tool_errors(self):
        from mcp.server.mcpserver.exceptions import ToolError

        s = self.server
        with self.assertRaises(ToolError):
            s.define_move("x", [0, 3, 0, 0], "#ffffff")
        with self.assertRaises(ToolError):
            s.hand_pose([1, 1, 1, 9])
        with self.assertRaises(ToolError):
            s.set_vision_source("mediapipe")
        with self.assertRaises(ToolError):
            s.define_sequence("bad", [{"pose": [0, 0, 0, 0], "ms": 3000}, {"wait": 3000}])
        with self.assertRaises(ToolError):
            s.play_match(4)

    def test_every_93_game_and_hardware_tool_is_registered(self):
        names = {t.name for t in self.server.mcp._tool_manager.list_tools()}
        wanted = """hand_servo hand_pose hand_relax calibrate_finger led_fill led_countdown fill_light voice_play
            voice_record speak list_moves define_move delete_move perform_move define_sequence list_sequences
            play_sequence delete_sequence attach_sequence list_rules define_rule delete_rule load_ruleset
            register_player identify_player set_player_mode list_players play_round play_match get_score
            reset_score set_vision_source banter_preview""".split()
        self.assertEqual([n for n in wanted if n not in names], [])


if __name__ == "__main__":
    unittest.main()
