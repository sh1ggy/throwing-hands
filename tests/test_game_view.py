"""Live game view (server/game_view.py): the MCP App wiring, the payload it polls, hiding vars, and the HTTP page.

    .venv/bin/python -m unittest tests.test_game_view
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from test_machine import MachineToolsTest, wait_for  # noqa: E402  (sets sys.path for server)

import game_view  # noqa: E402
import server  # noqa: E402
from machine import MachineError, validate  # noqa: E402

# A game "Claude invents": nothing in the server knows it. Rock scores, paper ends it; the secret is hidden.
INVENTED = {"name": "rock_counter", "view": {"hide": ["secret"]}, "vars": {"rocks": 0, "secret": 42},
            "initial": "wait",
            "states": {"wait": {"on_enter": [{"show": "ROCKS {rocks}"}],
                                "on": {"poke": {"do": [{"add": {"rocks": 1}}], "to": "wait"}, "gesture:paper": {"to": "end"}}},
                       "end": {"final": True}}}


class GameViewTest(MachineToolsTest):
    def view(self, rev=None):
        return server.game_view_state(rev)

    def test_any_machine_gets_a_graph_and_hidden_vars_never_leave(self):
        server.define_machine(INVENTED)
        self.fake.pose = 3
        server.start_machine("rock_counter")
        v = wait_for(lambda: (x := self.view())["state"] == "wait" and x)
        self.assertEqual(sorted(v["definition"]["states"]), ["end", "wait"])
        self.assertNotIn("secret", v["vars"])
        self.assertNotIn("secret", v["definition"]["vars"])
        self.assertNotIn("42", json.dumps(v))
        self.assertEqual(v["hidden"], ["secret"])
        self.assertIn("secret", server.machine_state()["vars"])  # Claude still sees everything
        # the definition only comes back when it changed
        self.assertNotIn("definition", self.view(v["rev"]))

    def test_a_live_patch_bumps_rev_and_resends_the_definition(self):
        server.define_machine(INVENTED)
        self.fake.pose = 3
        server.start_machine("rock_counter")
        rev = wait_for(lambda: self.view()["rev"])
        server.patch_machine("rock_counter", [{"op": "set_state", "state": "bonus", "value": {"on": {"poke": {"to": "wait"}}}},
                                              {"op": "add_handler", "state": "wait", "event": "timer", "handler": {"to": "bonus"}},
                                              {"op": "set_timeout", "state": "wait", "ms": 60000}])
        v = self.view(rev)
        self.assertGreater(v["rev"], rev)
        self.assertIn("bonus", v["definition"]["states"])
        server.stop_machine()

    def test_show_game_is_an_mcp_app(self):
        async def check():
            tools = {t.name: t for t in await server.mcp.list_tools()}
            self.assertEqual(tools["show_game"].meta["ui"]["resourceUri"], game_view.URI)
            self.assertEqual(tools["game_view_state"].meta["ui"]["visibility"], ["app"])
            (res,) = list(await server.mcp.read_resource(game_view.URI))
            self.assertEqual(res.mime_type, game_view.MIME)
            self.assertNotIn("/*THEME*/", res.content)
            self.assertIn('const SOURCE = "mcp"', res.content)
        asyncio.run(check())
        self.assertIn("rev", server.show_game())

    def test_side_server_serves_the_page_and_the_state(self):
        import socket
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        game_view.serve_side(f"127.0.0.1:{port}", lambda rev, events: {"rev": 1, "status": "idle", "asked": rev})
        base = f"http://127.0.0.1:{port}"
        html = wait_for(lambda: urllib.request.urlopen(base + "/view", timeout=2).read().decode())
        self.assertIn('const SOURCE = "http"', html)
        self.assertEqual(json.loads(urllib.request.urlopen(base + "/view/state?rev=7", timeout=2).read())["asked"], 7)


SCREENED = {**INVENTED, "name": "screened",
            "view": {"title": "Rock Counter", "hide": ["secret"], "headline": [{"var": "rocks", "label": "Rocks"}],
                     "screen": [{"text": "default screen"}]},
            "states": {**INVENTED["states"],
                       "wait": {**INVENTED["states"]["wait"], "timeout_ms": 60000,
                                "on": {**INVENTED["states"]["wait"]["on"], "timer": {"to": "wait"}}, "screen": [
                           {"big": "$rocks", "label": "Rocks so far"},
                           {"text": "the secret is {secret}", "if": {"gt": ["$rocks", 0]}},
                           {"text": "colour me", "color": {"if": [{"gt": ["$rocks", 0]}, "paper", "#123456"]}},
                           {"gestures": "accepted"}, {"timer": True}, {"shown": True},
                           {"text": "$boom", "if": {"div": [1, 0]}}]}}}


def resolved(view):
    return {b["kind"]: b for b in view["screen"]}


class ScreenTest(MachineToolsTest):
    def test_a_state_screen_resolves_live_on_the_server(self):
        server.define_machine(SCREENED)
        self.fake.pose = 3
        server.start_machine("screened")
        v = wait_for(lambda: (x := server.game_view_state())["state"] == "wait" and x.get("screen") and x)
        self.assertEqual(v["screen_source"], "state")
        kinds = [b["kind"] for b in v["screen"]]
        self.assertEqual(kinds[:2], ["big", "text"])           # the secret line is hidden while rocks == 0
        self.assertEqual(v["screen"][0], {"kind": "big", "label": "Rocks so far", "text": "0"})
        self.assertEqual(v["screen"][1]["color"], "#123456")
        self.assertEqual([g["name"] for g in resolved(v)["gestures"]["items"]], ["paper"])
        self.assertIn("timer", kinds)
        self.assertEqual(resolved(v)["shown"]["text"], "ROCKS 0")
        self.assertEqual(resolved(v)["error"]["kind"], "error")  # a failing block says so; the rest still draws
        server.poke()
        v = wait_for(lambda: (x := server.game_view_state())["vars"]["rocks"] == 1 and x)
        texts = [b.get("text") for b in v["screen"]]
        self.assertIn("the secret is 42", texts)                 # the author chose to reveal it
        self.assertNotIn("secret", v["vars"])
        colour = next(b for b in v["screen"] if b.get("text") == "colour me")["color"]
        self.assertRegex(colour, r"^#[0-9a-f]{6}$")             # "paper" -> its palette colour
        server.stop_machine()

    def test_no_screen_gets_a_default_from_the_headline(self):
        server.define_machine(INVENTED | {"view": {"hide": ["secret"], "headline": ["rocks"]}})
        self.fake.pose = 3
        server.start_machine("rock_counter")
        v = wait_for(lambda: (x := server.game_view_state())["state"] == "wait" and x.get("screen") and x)
        self.assertEqual(v["screen_source"], "auto")
        self.assertEqual(v["screen"][0]["kind"], "row")
        self.assertEqual(v["screen"][0]["items"][0]["text"], "0")
        server.stop_machine()


class ViewValidationTest(unittest.TestCase):
    def test_bad_screens_are_rejected_with_where(self):
        bad_screens = ([{"bogus": 1}], [{"row": [{"row": []}]}], [{"bar": "$rocks"}], [{"timer": 3}],
                       [{"text": "x", "size": "huge"}], [{"text": "x", "color": "mauve"}], [{"gestures": ["wave"]}],
                       "not a list")
        for scr in bad_screens:
            with self.assertRaises(MachineError, msg=scr):
                validate({**INVENTED, "gestures": ["paper"], "view": {"screen": scr}})
        with self.assertRaises(MachineError) as cm:
            validate({**INVENTED, "states": {**INVENTED["states"], "end": {"final": True, "screen": [{"said": 1}]}}})
        self.assertIn("state 'end' screen[0]", str(cm.exception))
        validate(SCREENED)

    def test_view_hide_must_name_real_vars(self):
        validate(INVENTED)
        for bad in ({"hide": ["nope"]}, {"hide": "secret"}, {"show": []}, []):
            with self.assertRaises(MachineError):
                validate({**INVENTED, "view": bad})


if __name__ == "__main__":
    unittest.main()
