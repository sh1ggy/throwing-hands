"""Live game view: one HTML page that draws ANY running state machine (agreed 2026-11-21; not in the design doc,
see docs/TODO.md).

The page knows no games. Two tabs:
  - Game (player-facing): the machine's own screen (screen.py), resolved here into plain blocks, or a default one.
  - Nerd: the state graph from the definition with the current state lit, the timer, the vars and the events.
A game Claude invents mid-demo gets both, and restyles the Game tab by patching its screen.

Two ways to open it, one page:
  - MCP App: the ``show_game`` tool points at ``ui://rps-bot/game-view``; Claude Desktop draws the page in the chat
    and the page polls the ``game_view_state`` tool through the host.
  - Plain HTTP (the stage/projector): ``GET /view`` and ``GET /view/state`` on the --http server (token-gated), or
    the stdio server's ``--view HOST:PORT`` side server (localhost, no token).

The view only reads: it never sends events or changes a machine, so Claude stays the only writer and stays out of
the per-turn loop. Vars named in the machine's ``view.hide`` are removed here, before anything leaves the server.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import screen

VIEWS = Path(__file__).resolve().parent / "views"
URI = "ui://rps-bot/game-view"
MIME = "text/html;profile=mcp-app"   # MCP Apps (SEP-1865) HTML resource


def page(source: str) -> str:
    """The view as one self-contained document (MCP Apps can't load sibling files). source: 'mcp' or 'http'."""
    html = (VIEWS / "game.html").read_text()
    theme = (VIEWS / "theme.css").read_text()
    return html.replace("/*THEME*/", theme).replace("__SOURCE__", source)


def _hide(obj, hidden: set[str]):
    return {k: v for k, v in obj.items() if k not in hidden} if isinstance(obj, dict) else obj


def payload(rt, rev: int | None = None, events: int = 40, gesture_set: dict | None = None) -> dict:
    """What the view polls: the snapshot, plus the definition when it changed since ``rev`` (a start or a patch),
    with hidden vars removed from both."""
    snap = rt.snapshot(max(1, min(int(events), 100)))
    definition = rt.definition()
    hidden = set(((definition or {}).get("view") or {}).get("hide", []))
    snap["vars"] = _hide(snap.get("vars"), hidden)
    snap["hidden"] = sorted(hidden)
    if gesture_set is not None:
        snap["gesture_set"] = gesture_set
    if definition is not None:
        try:
            snap["screen"], snap["screen_source"] = screen.resolve(definition, snap.get("state"), rt.scope(),
                                                                   rt.snapshot(100)["events"], snap.get("status"))
        except Exception as e:  # the Game tab says so; the Nerd tab still works
            snap["screen"], snap["screen_source"] = [{"kind": "error", "text": f"screen: {e}"[:160]}], "error"
    if definition is not None and rev != snap["rev"]:
        definition["vars"] = _hide(definition.get("vars"), hidden)
        snap["definition"] = definition
    return snap


def serve_side(bind: str, get_payload, train=None) -> threading.Thread:
    """For the stdio server: serve the view on HOST:PORT in a daemon thread (stdlib, no token: keep it on 127.0.0.1).
    get_payload(rev, events) -> dict. train = (get_trainer, page) also serves the Train page at /train; its POSTs
    drive the camera, so they're accepted only from a localhost page (blocks other sites and DNS rebinding)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    host, _, port = bind.rpartition(":")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # stdout/stderr belong to the MCP transport
            pass

        def _send(self, code: int, body: str, ctype: str) -> None:
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path in ("/", "/view"):
                self._send(200, page("http"), "text/html; charset=utf-8")
            elif train and u.path == "/train":
                self._send(200, train[1](), "text/html; charset=utf-8")
            elif train and u.path == "/train/api/state":
                self._send(200, train[0]().snapshot(), "application/json")
            elif u.path == "/view/state":
                try:
                    rev = int(q["rev"][0]) if q.get("rev") else None
                    body = json.dumps(get_payload(rev, int(q.get("events", ["40"])[0])), default=str)
                    self._send(200, body, "application/json")
                except Exception as e:  # the page shows it and keeps polling
                    self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}), "application/json")
            else:
                self._send(404, '{"error": "not found"}', "application/json")

        def do_POST(self):
            u = urlparse(self.path)
            if not train or not u.path.startswith("/train/api/"):
                return self._send(404, '{"error": "not found"}', "application/json")
            local = ("127.0.0.1", "localhost")
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            origin = self.headers.get("Origin")
            if host not in local or (origin and origin.split("//")[-1].rsplit(":", 1)[0] not in local):
                return self._send(403, '{"error": "local page only"}', "application/json")
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._send(400, '{"error": "bad json"}', "application/json")
            code, out = train[0]().handle(u.path.removeprefix("/train/api/"), body if isinstance(body, dict) else {})
            self._send(code, json.dumps(out), "application/json")

    srv = ThreadingHTTPServer((host or "127.0.0.1", int(port)), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="game-view")
    t.start()
    return t
