"""The RPS round as a state machine (server/fallback_machines/rps.json + the rps.* function library): the behaviour tests of
tests/test_game.py (T9-T12, NFR-01, NFR-09) run against the machine instead of Game.play_round / play_match.
All on FakeBridge/FakeCamera with injected sleeps, no real speech.

    .venv/bin/python -m unittest tests.test_rps_machine
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import rps_functions  # noqa: E402
from game import GameError  # noqa: E402
from machine import Context, MachineError, validate  # noqa: E402
from machine_runtime import Runtime  # noqa: E402
from test_game import FakeCam, FirstRng, GameCase, StubVision  # noqa: E402

RPS = validate(json.loads((ROOT / "server" / "fallback_machines" / "rps.json").read_text()), Context())


class RpsMachineCase(GameCase):
    def make(self, *a, **k):
        g = super().make(*a, **k)
        g.rt = Runtime(g)
        self.addCleanup(g.rt.stop)
        return g

    def round(self, g, mode=None, player=None):
        return rps_functions.run_rps(g.rt, RPS, mode, player)

    def match(self, g, best_of=5, mode=None, player=None):
        return rps_functions.run_rps(g.rt, RPS, mode, player, match=True, best_of=best_of)


class TestRoundsAsMachine(RpsMachineCase):
    def test_t10_fair_is_fair_and_commits_first(self):
        g = self.make(vision=StubVision(["rock"]))
        counts, before = Counter(), []
        for _ in range(300):
            r = self.round(g, "fair", "guest")
            counts[r["robot"]] += 1
            before.append(r["timings"]["committed_before_shoot"])
        for move in ("rock", "paper", "scissors"):
            self.assertAlmostEqual(counts[move] / 300, 1 / 3, delta=0.05, msg=str(counts))
        self.assertTrue(all(before))

    def test_fair_commits_before_the_read_on_the_camera_path(self):
        g = self.make()
        self.see(g, 1)
        r = self.round(g, "fair", "guest")
        self.assertTrue(r["timings"]["committed_before_shoot"])
        self.assertIsNone(r["timings"]["reveal_lag_ms"])
        self.assertLessEqual(r["timings"]["returned_ms_after_shoot"], 1500)  # NFR-01

    def test_t9_rigged_win_wins_every_round(self):
        g = self.make()
        lags, returned = [], []
        for pose in (1, 2, 3) * 7:
            self.see(g, pose)
            r = self.round(g, "rigged_win", "guest")
            self.assertEqual(r["outcome"], "robot", r)
            self.assertFalse(r["timings"]["committed_before_shoot"])
            self.assertLessEqual(r["timings"]["reveal_lag_ms"], 250)
            self.assertGreaterEqual(r["timings"]["commit_t"], r["timings"]["read_done_t"])
            lags.append(r["timings"]["reveal_lag_ms"])
            returned.append(r["timings"]["returned_ms_after_shoot"])
        self.assertEqual(g.session["robot"], 21)
        self.assertLessEqual(max(returned), 1500)
        print(f"\n[rps machine] reveal_lag_ms max {max(lags)}, returned_ms_after_shoot max {max(returned)} (21 rounds)")

    def test_rigged_lose_always_loses(self):
        g = self.make()
        for pose in (1, 2, 3) * 3:
            self.see(g, pose)
            r = self.round(g, "mercy", "guest")  # alias of rigged_lose
            self.assertEqual(r["outcome"], "human", r)
            self.assertEqual(r["mode"], "rigged_lose")

    def test_rules_outcomes_and_verbs(self):
        g = self.make(rng=FirstRng())  # robot throws rock
        self.see(g, 1)
        r = self.round(g, "fair", "guest")
        self.assertEqual((r["robot"], r["human"], r["outcome"]), ("rock", "rock", "draw"))
        self.see(g, 2)
        r = self.round(g, "fair", "guest")
        self.assertEqual((r["outcome"], r["verb"]), ("human", "covers"))
        self.see(g, 3)
        r = self.round(g, "fair", "guest")
        self.assertEqual((r["outcome"], r["verb"]), ("robot", "crushes"))
        p = g.get_score("guest")["player"]
        self.assertEqual((p["wins"], p["losses"], p["draws"]), (1, 1, 1))
        self.assertEqual(g.session, {"robot": 1, "human": 1, "draws": 1, "rounds": 3})

    def test_unknown_read_replays_then_no_read(self):
        g = self.make()
        self.see(g, 0)  # seen but not learned -> unknown
        r = self.round(g, "fair", "guest")
        self.assertEqual(r["outcome"], "no_read")
        self.assertEqual(r["replays"], 2)
        self.assertEqual(g.session["rounds"], 0)
        self.assertEqual(g.get_score("guest")["player"]["draws"], 0)

    def test_replay_then_success(self):
        g = self.make(vision=StubVision(["unknown", "rock"]))
        r = self.round(g, "rigged_win", "guest")
        self.assertEqual((r["replays"], r["outcome"], r["robot"]), (1, "robot", "paper"))

    def test_max_replays_is_a_game_setting(self):
        g = self.make(vision=StubVision(["unknown"]), settings={"max_replays": 0})
        self.assertEqual(self.round(g, "fair", "guest")["replays"], 0)

    def test_hand_relaxed_after_round(self):  # NFR-09
        g = self.make()
        self.see(g, 1)
        self.round(g, "rigged_win", "guest")
        self.assertIsNone(g.hw.fingers)
        self.assertEqual(g.bridge.us, [None] * 5)
        self.assertEqual(g.hw.held_closed_s(), 0.0)

    def test_hold_is_trimmed_to_stay_under_the_limit(self):
        g = self.make(settings={"result_hold_s": 99})
        self.assertLessEqual(rps_functions.hold_ms(g), 4500)
        g.hw.pose([0, 0, 0, 0], 100)
        self.assertLessEqual(rps_functions.hold_ms(g), 4500)

    def test_the_result_is_held_then_relaxed(self):
        import time
        g = self.make(settings={"result_hold_s": 0.2})
        self.see(g, 1)
        t0 = time.monotonic()
        r = self.round(g, "rigged_win", "guest")
        self.assertGreaterEqual(time.monotonic() - t0, 0.18)       # the hold really waits (NFR-09: under 5 s)
        self.assertLess(r["timings"]["returned_ms_after_shoot"], 150)  # but the result was ready before it
        self.assertIsNone(g.hw.fingers)

    def test_predict_exploits_a_pattern(self):
        g = self.make(vision=StubVision(["rock"]))
        results = [self.round(g, "predict", "guest") for _ in range(12)]
        self.assertTrue(all(r["robot"] == "paper" for r in results[4:]), [r["robot"] for r in results])
        self.assertEqual(results[-1]["prediction"]["basis"], "markov")

    def test_grudge_turns_rigged_after_the_player_wins(self):
        g = self.make(rng=FirstRng(), vision=StubVision(["paper"]))  # robot rock vs paper: human wins round 1
        r1 = self.round(g, "grudge", "guest")
        self.assertEqual((r1["outcome"], r1["effective_mode"]), ("human", "fair"))
        self.assertTrue(r1["grudge_now_active"])
        for _ in range(3):
            r = self.round(g, "grudge", "guest")
            self.assertEqual((r["outcome"], r["effective_mode"]), ("robot", "rigged_win"))

    def test_bribery_throws_the_match_with_gold_leds(self):
        g = self.make()
        self.see(g, 1)
        r = self.round(g, "bribery", "guest")
        self.assertEqual(r["outcome"], "human")
        self.assertTrue(r["bribed"])
        self.assertEqual(g.bridge.leds[0], "#ffb000")

    def test_bribe_card_through_identify_player(self):
        g = self.make()
        self.see(g, 1)
        g.register_player("Ann")  # face 1
        r = rps_functions.identify_player(g, check_bribe=True)
        self.assertEqual((r["player"], r["known"], r["bribed"], r["bribe_check"]), ("Ann", True, False, "no card"))
        g.bridge.camera.learned[7] = Counter({1: 1})  # the card is learned in QR recognition
        r = rps_functions.identify_player(g, check_bribe=True)
        self.assertTrue(r["bribed"])
        self.assertEqual(self.round(g)["outcome"], "human")  # Ann is fair by default, but bribed
        g.reset_score("session")
        self.assertFalse(g.bribed)

    def test_stranger_gets_rigged_win_by_default(self):
        g2 = self.make(vision=StubVision(["rock"]), data_dir=self.tmp / "b")
        r = rps_functions.identify_player(g2)
        self.assertEqual((r["player"], r["known"], r["mode"]), ("stranger", False, "rigged_win"))
        self.assertEqual(self.round(g2)["effective_mode"], "rigged_win")

    def test_setup_state_identifies_the_player(self):
        g = self.make()
        self.see(g, 1)
        g.register_player("Ann")  # face 1, fair by default
        g.set_player_mode("Ann", "rigged_lose")
        r = rps_functions.run_rps(g.rt, RPS, identify=True)
        self.assertEqual((r["player"], r["mode"], r["outcome"]), ("Ann", "rigged_lose", "human"))
        self.assertEqual(g.rt.snapshot()["vars"]["seat"]["known"], True)

    def test_setup_state_unknown_face_is_a_stranger(self):
        g = self.make(vision=StubVision(["rock"]))
        r = rps_functions.run_rps(g.rt, RPS, identify=True)
        self.assertEqual((r["player"], r["effective_mode"]), ("stranger", "rigged_win"))

    def test_no_setup_scan_unless_asked(self):
        g = self.make(vision=StubVision(["rock"]))
        g.current = "nobody-scanned"
        g.vision.identify_face = lambda *a: self.fail("scanned faces without identify=true")
        with self.assertRaises(MachineError):
            rps_functions.run_rps(g.rt, RPS)  # current player is used as-is: an unknown name errors in begin_round

    def test_sore_loser_tantrum_and_demand(self):
        g = self.make(vision=StubVision(["paper"]), rng=FirstRng())  # robot rock loses to paper
        r = self.round(g, "sore_loser", "guest")
        self.assertEqual(r["outcome"], "human")
        self.assertIn("Best of 3", r["banter"])
        poses = [c for c in g.bridge.calls if c["cmd"] == "pose"]
        self.assertGreater(len(poses), 12)  # countdown pump + the tantrum
        self.assertIsNone(g.hw.fingers)

    def test_bluff_announces_and_stays_fair(self):
        g = self.make(vision=StubVision(["rock"]))
        r = self.round(g, "bluff", "guest")
        self.assertIn(r["bluff"]["announced"], ("rock", "paper", "scissors"))
        self.assertEqual(r["bluff"]["honest"], r["bluff"]["announced"] == r["robot"])
        self.assertTrue(r["timings"]["committed_before_shoot"])

    def test_hooks_run_after_the_round(self):
        g = self.make(vision=StubVision(["rock"]))
        g.define_sequence("flash", [{"led": "#123456"}, {"wait": 60}])
        g.attach_sequence("on_win", "flash")
        self.round(g, "rigged_win", "guest")  # robot wins -> on_win
        self.assertEqual(g.bridge.leds[0], "#123456")
        g.attach_sequence("on_win", None)
        self.round(g, "rigged_win", "guest")
        self.assertNotEqual(g.bridge.leds[0], "#123456")

    def test_match_best_of_five(self):
        g = self.make()
        self.see(g, 1)
        m = self.match(g, 5, "rigged_win", "guest")
        self.assertEqual((m["winner"], m["match_score"]), ("robot", {"robot": 3, "human": 0}))
        self.assertEqual(len(m["rounds"]), 3)
        self.assertTrue(m["banter"])
        with self.assertRaises(GameError):
            self.match(g, 4)

    def test_draws_and_no_reads_dont_count_in_a_match(self):
        g = self.make(rng=FirstRng(), vision=StubVision(["rock"]))  # robot rock vs rock: all draws
        m = self.match(g, 3, "fair", "guest")
        self.assertIsNone(m["winner"])
        self.assertEqual(len(m["rounds"]), 15)  # the same best_of * 5 cap as play_match

    def test_listener_errors_never_break_a_round(self):
        g = self.make(vision=StubVision(["rock"]))
        events = []
        g.listeners += [lambda e: 1 / 0, events.append]
        self.round(g, "fair", "guest")
        self.assertIn("report", [e["event"] for e in events])
        self.assertIn("machine_state", [e["event"] for e in events])

    def test_round_logged_to_history(self):
        g = self.make(vision=StubVision(["rock"]))
        self.round(g, "fair", "guest")
        self.round(g, "fair", "guest")
        lines = (self.tmp / "history.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[1])["round"], 2)

    def test_the_machine_matches_the_engine_on_the_same_script(self):
        """Same data, same seeds, same scripted reads: Game.play_round and the machine pick the same robot moves."""
        import random
        runs = {}
        for kind in ("engine", "machine"):
            g = self.make(vision=StubVision(["rock", "paper", "scissors", "rock", "rock"]), rng=random.Random(5),
                          data_dir=self.tmp / kind)
            out = []
            for mode in ("fair", "predict", "rigged_win", "rigged_lose", "fair"):
                r = g.play_round(mode, "guest") if kind == "engine" else self.round(g, mode, "guest")
                g.wait_idle()
                out.append((r["robot"], r["human"], r["outcome"], r["verb"]))
            runs[kind] = out
        self.assertEqual(runs["engine"], runs["machine"])

    def test_a_missing_ruleset_is_a_clear_machine_error(self):
        g = self.make(vision=StubVision(["rock"]))
        for r in g.list_rules():
            g.delete_rule(r["winner"], r["loser"])
        with self.assertRaises(MachineError) as cm:
            self.round(g, "fair", "guest")
        self.assertIn("no rules defined", str(cm.exception))
        self.assertIsNone(g.hw.fingers)

    def test_stopping_mid_round_relaxes_the_hand(self):
        g = self.make(vision=StubVision(["rock"]), settings={"result_hold_s": 30})
        g.rt.start(RPS, {"mode": "rigged_win", "player": "guest"})
        for _ in range(500):
            if g.rt.state == "hold":
                break
            threading.Event().wait(0.01)
        self.assertEqual(g.rt.state, "hold")
        self.assertTrue(g.rt.stop())
        self.assertIsNone(g.hw.fingers)


if __name__ == "__main__":
    unittest.main()
