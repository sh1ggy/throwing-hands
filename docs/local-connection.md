# Local connection: Claude Desktop on the robot laptop, no tunnel

_Written 2026-11-21. The quick tunnel's URL changes on every restart and there's no domain for a named tunnel, so for the
demo Claude Desktop runs on the same laptop as the camera and talks to `server.py` directly. Nothing leaves the laptop
except the chat itself._

Two ways to do it. **Recommendation: B, local HTTP through `mcp-remote`.** It keeps the runbook's terminal flow (the
server lives in a terminal you can Ctrl-C and restart, and Desktop reconnects on its own). A is simpler on paper but
Desktop owns the server process, which makes restarts and freeing the serial port awkward mid-demo.

Legend: ✅ tested 2026-11-21 (with `--fake`, `--data /tmp/...`, a Python MCP client standing in for Desktop) ·
⚠️ not tested (needs the real Claude Desktop or the real camera).

Paths below assume the repo is at `/Users/shiggy/repos/throwing-hands` and Node comes from nvm
(`/Users/shiggy/.nvm/versions/node/v22.21.1/bin`). Fix them if either moves.

Claude Desktop's config file is `~/Library/Application Support/Claude/claude_desktop_config.json` (Settings → Developer →
Edit Config). Today it has no `mcpServers` key: add one next to `preferences`. Quit and reopen Desktop after editing.
**Use only one of A or B**, and turn the old tunnel custom connector off (Settings → Connectors) so Claude doesn't see
two `rps-bot`s.

---

## B. Local HTTP + `mcp-remote` (recommended)

The server runs in a terminal exactly as in the runbook (`--http`, token from `.env`). Desktop launches `mcp-remote`, a
small stdio↔HTTP bridge, which connects to `http://127.0.0.1:8770/mcp` with the token in a header.

### Config

```json
{
  "mcpServers": {
    "rps-bot": {
      "command": "/Users/shiggy/.nvm/versions/node/v22.21.1/bin/npx",
      "args": ["-y", "mcp-remote@0.16.7", "http://127.0.0.1:8770/mcp",
               "--header", "Authorization:${RPSBOT_AUTH}"],
      "env": {
        "PATH": "/Users/shiggy/.nvm/versions/node/v22.21.1/bin:/usr/bin:/bin",
        "RPSBOT_AUTH": "Bearer <RPSBOT_TOKEN from .env>"
      }
    }
  }
}
```

- **Header syntax.** `mcp-remote` replaces `${RPSBOT_AUTH}` with the env value itself, so the space in `Bearer <token>`
  never sits inside an arg. ✅ Tested. The literal form `"--header", "Authorization: Bearer <token>"` also works on macOS
  (Desktop passes args as an array, no shell). ✅ Tested. Prefer the env form: it's what `mcp-remote` documents, and the
  token lives in one place.
- **`PATH` in `env` is required.** Desktop doesn't load your shell profile, and `npx` starts with `#!/usr/bin/env node`.
  Without nvm's bin on `PATH` it dies with `env: node: No such file or directory`. ✅ Tested (fails without, works with).
  Don't use `/Users/shiggy/Library/pnpm/npx` (pnpm's shim, which is first on your shell `PATH`); use nvm's.
- `http://` is fine: `mcp-remote` only insists on HTTPS for non-localhost hosts.
- `mcp-remote@0.16.7` is pinned (latest on 2026-11-21) so a new release can't change behaviour on demo day.
- Print the token to paste: `grep '^RPSBOT_TOKEN=' .env | cut -d= -f2-`. It ends up in plain text in Desktop's
  config, which is a local file on your laptop: fine. Change it in both places if you change it.

### Start

1. Terminal: `.venv/bin/python server/server.py --http --hand sim --baud 115200` (add `--fake` to rehearse without the
   camera). It reads `.env` from the repo root whatever the cwd. ✅ Tested from `/tmp`: the `.env` token was used, no
   generated token printed.
2. Open Claude Desktop (or quit and reopen it after editing the config). Desktop starts `mcp-remote`.
3. **Chat:** "List the machines and tell me the camera status." (`list_machines`, `husky_status`).
   ✅ Tested through `mcp-remote` with a client: 69 tools, both calls OK. ⚠️ Not yet tried in the real Desktop.

**Do the first launch before the venue.** `npx -y` downloads `mcp-remote` on first use (into `~/.npm/_npx`). Once cached
it should start offline. ⚠️ Offline start not tested: turn Wi-Fi off once at home and check.

### Projector and Train pages

Same as the runbook (token-gated, on the server's port):
- Projector: `open "http://127.0.0.1:8770/view?key=$(grep '^RPSBOT_TOKEN=' .env | cut -d= -f2- | tr -d "\"'")"`
- Train: the same with `/train?key=...`
✅ Tested: 401 without the key, 200 with the `.env` token.

**MCP Apps (`show_game`, `show_train`).** ✅ Through `mcp-remote`, both tools keep their `_meta.ui.resourceUri`, both
`ui://rps-bot/...` resources list, `show_game` answers, and reading `ui://rps-bot/game-view` returns the page with mime
`text/html;profile=mcp-app`. So everything Desktop needs reaches it. ⚠️ Whether Desktop actually draws the panel for
this local server isn't tested. If it doesn't, carry on with the projector page (runbook E).

### Restart after a crash

- **Server crashed:** run the same command again in the terminal. **No Desktop action needed:** `mcp-remote` notices the
  old session is gone and opens a new one on the next tool call. ✅ Tested: killed and restarted the server under a
  connected `mcp-remote`; the next two `list_machines` calls succeeded (log: `Re-established session ... after server
  expiry`). A running game is lost with the process, as always: start it again.
- **`mcp-remote` itself died** (Desktop shows the server as failed): Settings → Developer → `rps-bot` → restart, or quit
  and reopen Desktop. ⚠️ Not tested in Desktop.

### Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `rps-bot` failed in Desktop, log says `env: node: No such file or directory` | no `PATH` in `env` | add it (✅ reproduced) |
| Log says `RegistrationRejectedError ... 401 missing or wrong token` | wrong / stale token, or `Bearer` missing | re-copy from `.env` (✅ reproduced; it fails cleanly, no browser pop-up) |
| `ECONNREFUSED 127.0.0.1:8770` | server not running yet | start it; restart `rps-bot` in Desktop if it gave up (⚠️) |
| `husky_*` errors about the serial port | `train_ui.py` or another script holds it | Ctrl-C that, retry |
| Two sets of rps-bot tools | old tunnel connector still on | turn it off in Connectors |

Logs: Desktop's side at `~/Library/Logs/Claude/mcp-server-rps-bot.log` (⚠️ path from Desktop's usual layout, not checked
with this entry); the server's in its terminal. `mcp-remote` also keeps a small state folder at `~/.mcp-auth/`; deleting it
is harmless.

### With the runbook's three-terminal flow

Fits directly. Terminal 1 (the tunnel) goes away, terminal 2 (server) and terminal 3 (projector `open`) are unchanged,
and the "Claude Desktop (only when the tunnel URL changed)" step disappears: the URL never changes. Running a `tools/`
script (runbook A) = Ctrl-C terminal 2, run it, start the server again; Desktop reconnects by itself.

---

## A. Stdio (Desktop launches the server)

### Config

```json
{
  "mcpServers": {
    "rps-bot": {
      "command": "/Users/shiggy/repos/throwing-hands/.venv/bin/python",
      "args": ["/Users/shiggy/repos/throwing-hands/server/server.py", "--hand", "sim", "--baud", "115200", "--view"]
    }
  }
}
```

No `cwd` or `env` needed:
- **`.env`** is read from the repo root found from the script's own path (`ROOT = Path(__file__).resolve().parents[1]`),
  not the cwd. ✅ Tested from `/tmp` (see B, same loader).
- **Data dir** defaults to `<repo>/server/data` (or `server/data/fake` with `--fake`), also from `ROOT`. ✅ Tested from
  `/tmp` with `--data /tmp/...`: files went there; the default path is built the same way.
- **No token** in stdio mode; `/view` and `/train` need none either.

### Start

Open Claude Desktop. It starts the server itself (the camera link opens lazily, on the first tool call). Smoke check as
in B. ✅ Tested with a client launching it from `/tmp` with a bare `PATH` and `--fake --view 127.0.0.1:8791`:
initialize, 69 tools, `list_machines` and `husky_status` OK, `show_game`/`show_train` metadata and `ui://` resources
present. ⚠️ Not yet tried in the real Desktop or on the real camera.

### Projector and Train pages

`http://127.0.0.1:8771/view` and `http://127.0.0.1:8771/train`, no key. ✅ Tested: `/view`, `/view/state`, `/train`,
`/train/api/state` all 200. The runbook's terminal-3 `open` command becomes `open http://127.0.0.1:8771/view`.

### Restart after a crash

Settings → Developer → `rps-bot` → restart, or quit and reopen Desktop. There's no terminal to Ctrl-C. When Desktop closes
the server's stdin the process exits and frees port 8771 (✅ tested) and the serial port with it.

### Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `rps-bot` disconnects right at start, log ends `OSError: [Errno 48] Address already in use` | something already on 8771: a leftover server, the `game-view-fake` preview (`.claude/launch.json` uses 8771), another `--view` | kill it (`lsof -nP -iTCP:8771 -sTCP:LISTEN`), restart `rps-bot`; or move the view, e.g. `"--view", "127.0.0.1:8772"` (✅ crash reproduced) |
| `husky_*` errors about the serial port | `server.py --http` or `train_ui.py` still running | stop it; restart `rps-bot` |
| Need to run a `tools/` script (runbook A) | the server holds the port as long as Desktop is open | quit Desktop (or disable `rps-bot`), run the script, reopen |
| Server errors | | `~/Library/Logs/Claude/mcp-server-rps-bot.log` (stderr goes there; ⚠️ path not checked) |

### With the runbook's three-terminal flow

Poorer fit. The tunnel and server terminals both go away (Desktop is the server), the projector URL changes to port 8771,
and every server restart or `tools/` script goes through Desktop's UI instead of a terminal. Fine for a calm rehearsal,
worse when something breaks in front of an audience.

---

## Why B

| | A. Stdio | B. HTTP + `mcp-remote` |
|---|---|---|
| Moving parts | Desktop + server | Desktop + `mcp-remote` + server in a terminal |
| Restart server | Desktop UI | Ctrl-C / ↑ Enter; Desktop reconnects itself ✅ |
| See server output | log file | the terminal |
| Free the serial port for a `tools/` script | quit Desktop | Ctrl-C |
| Token | none | in Desktop's config |
| Needs Node | no | yes (`PATH` in `env`) |
| Matches the runbook | needs rewording | drop the tunnel, rest unchanged |

The tunnel setup stays documented in [`remote-mcp.md`](remote-mcp.md) for driving from another machine.
