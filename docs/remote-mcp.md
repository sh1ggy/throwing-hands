# Remote MCP: drive the robot from another machine (Cloudflare Tunnel)

_Added 2026-11-21. Not in the design doc (D4/§9.3 assume a local stdio server); listed under "Design-doc issues" in `TODO.md`._

The robot laptop runs the MCP server and owns the serial port, as always. A Cloudflare Tunnel gives it a public HTTPS
URL without opening any inbound ports, so it works on venue Wi-Fi with client isolation. Any Claude that can reach the URL
and has the token can define, patch and start games on the robot.

```
Claude (any machine) ──HTTPS──▶ Cloudflare ──tunnel (outbound from the laptop)──▶ server.py --http 127.0.0.1:8770 ──serial──▶ XIAO ──▶ HuskyLens
```

## 1. Start the server in HTTP mode (robot laptop)

The token lives in `.env` at the repo root (gitignored; `.env.example` shows every setting). Once:

```bash
cp .env.example .env
```

Then set `RPSBOT_TOKEN=` in `.env` to a long random string (`python3 -c 'import secrets; print(secrets.token_urlsafe(24))'`).
Keep the same token to keep the same link; change it to cut everyone off. Then:

```bash
.venv/bin/python server/server.py --http           # real camera; add --fake to test without hardware
```

- `server.py` reads `.env` at start-up. A variable already set in the shell wins over the file.
- It listens on `http://127.0.0.1:8770/mcp`, so only the tunnel can reach it. Keep the host at `127.0.0.1`.
- Every request needs the token: `Authorization: Bearer <token>`, or `?key=<token>` on the URL for clients that can't set headers.
  Without it the server replies 401. With no token in `.env`, the shell or `--token`, it generates one and prints it at start-up.
- All the other flags still apply (`--hand`, `--baud`, `--speak`, `--data`). The serial port lock is the same: stop `train_ui.py` first.

## 2a. Quick tunnel (no account, for testing)

```bash
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8770
```

It prints a `https://<random>.trycloudflare.com` URL. Your MCP URL is that plus `/mcp`.
**The URL changes every time you restart it**, and quick tunnels come with no uptime guarantee. That's fine for a test,
but don't rely on one for the demo.

Tested 2026-11-21 on `--fake`: a request without the token got 401 through the tunnel, and a full MCP session (64 tools,
`list_machines`) worked with both the query key and the bearer header.

## 2b. Named tunnel (stable hostname, for the demo)

You need a domain on your Cloudflare account. These steps use the `cf` CLI for setup and `cloudflared` as the connector.
**Not yet run against a real account**, so check each command's output as you go.

```bash
# 1. Create a tunnel managed by Cloudflare (prints its id)
cf tunnels create --name rps-bot --config-source cloudflare

# 2. Route the hostname to the local server; everything else gets a 404
cf tunnels config update <TUNNEL_ID> --body '{"config":{"ingress":[
  {"hostname":"rps.<your-domain>","service":"http://127.0.0.1:8770"},
  {"service":"http_status:404"}]}}'

# 3. DNS: a proxied CNAME pointing at the tunnel
cf dns records create -z <your-domain> --body '{"type":"CNAME","name":"rps","content":"<TUNNEL_ID>.cfargotunnel.com","proxied":true}'

# 4. Run the connector on the robot laptop (leave it running next to the server)
cf tunnels run <TUNNEL_ID>        # or: cloudflared tunnel run --token "$(cf tunnels token get <TUNNEL_ID>)"
```

Your MCP URL is `https://rps.<your-domain>/mcp`.

## 3. Connect a Claude (other machine)

**Claude Code** (connects directly from that machine, so it can send a header):
```bash
claude mcp add --transport http rps-bot https://<host>/mcp --header "Authorization: Bearer <token>"
```

**claude.ai or Claude Desktop custom connector** (tested, see below): Settings → Connectors → Add custom connector.
- **URL:** `https://<host>/mcp`. Don't forget `/mcp`: the bare tunnel URL returns 404.
- **Authentication: No sign-in.** Ignore the "Detected" tag on "Sign in now". It appears because the server answers 401
  without the header; the server has no OAuth.
- **Request headers:** name `authorization`, value `Bearer <token>` (the word `Bearer`, one space, then the value of
  `RPSBOT_TOKEN` from `.env`, without `RPSBOT_TOKEN=` or quotes). Leave **Required** ticked. The value is stored by the
  connector and never shown again, so paste it carefully.
- If a client has no header field, `https://<host>/mcp?key=<token>` works too, but the header is safer (see Security).

These connections come from Anthropic's servers, which is why the server has to be public.

**Claude Desktop through a local bridge** (keeps the token in a header): add a local server that runs
`npx mcp-remote https://<host>/mcp --header "Authorization: Bearer <token>"`.

Then check that `list_machines` answers.

## Tested

| Date | Client | Tunnel | Camera | Auth | Result |
| --- | --- | --- | --- | --- | --- |
| 2026-11-21 | Python MCP client (local script) | quick tunnel | `--fake` | Bearer header and `?key=` | ✓ 401 without the token; full session (64 tools, `list_machines`) with either |
| 2026-11-21 | **claude.ai custom connector**, on a friend's account | quick tunnel | **real HuskyLens via the XIAO** | No sign-in + `authorization: Bearer <token>` request header | ✓ **Working** (confirmed by the user): the connector connected and the friend drove the robot's MCP tools remotely |

Not yet tested: the named tunnel (§2b), Claude Code with `--header`, and `mcp-remote`.

The test script used with the friend (they type, you make the gestures on a video call):
1. `machine_reference`, `list_machines`, `husky_status`: connection and camera
2. `read_gesture` while you show rock, then paper
3. `start_machine('rps')`, best of 3, `machine_state` after each round
4. `patch_machine` to best of 5, restart, one round
5. The demo prompt (`demo-plan.md`): Claude writes its own higher-or-lower, swaps and trains the thumbs, plays. **This wipes
   the RPS training**: save it first (`husky_save_model(1)`) and restore it afterwards (`start_machine('rps', swap_gestures=true)`,
   `husky_load_model(1)`)
6. `stop_machine`

## Rules

- **One driver at a time.** There is one camera and one running machine. Two connected Claudes share them, and a second
  `start_machine` hits the one already running.
- **Stop the tunnel when you're not using it** (Ctrl-C on `cloudflared`). The server is unreachable without it.
- The game loop runs on the robot laptop, so a remote Claude only adds latency to its own tool calls, not to gesture
  reads or timers.

## Security

The whole defence is **one shared token on a public URL**. That's fine for a supervised session with a friend, but keep
these in mind.

**What the token gives** (all 64 tools, with no roles or read-only mode):
- **Physical hardware:** servos (finger poses, calibration), LEDs, the relay and fill light, and voice playback/recording on
  the ISD1820. Someone moving the hand while a person's fingers are near it, or recalibrating it badly, is the main
  physical risk. Keep a hand on the power while someone remote is driving.
- **The camera:** `husky_forget` and any `swap_gestures` **wipe** the trained gestures, and `husky_save_model` can
  overwrite your SD backup slots. `husky_photo` / `husky_screenshot` save images to the camera's SD card (not sent over MCP).
- **People in the room:** face recognition (`identify_player`, `register_player`) tells the remote Claude who is in front of
  the camera by name, and lets it register new faces. Ask before anyone gets scanned.
- **Your data:** machines, moves, rules, players, sequences and settings in `server/data/` can be created, changed or
  deleted. Commit or copy anything you care about first.
- **Laptop speech**, only if you started the server with `--speak`.

**What it doesn't give:** no tool runs shell commands or code. Machines are JSON with validated conditions, machine names
are restricted to `[a-z0-9_-]` (no path tricks), and speech passes text as an argument, not through a shell. The server
listens on `127.0.0.1` only, so the tunnel is the only way in.

**Where things can leak:**
- **The token.** Anyone with it has everything above until you change it. Send it in a DM, never a group chat, a
  screenshot or a commit (`.env` is gitignored; check with `git check-ignore .env`). The connector stores it on the
  friend's Anthropic account, so **change `RPSBOT_TOKEN` after the session** and they're locked out even though the
  connector still exists.
- **`?key=` URLs** end up in browser history, logs and screenshots. Prefer the header.
- **Data leaves the room.** Every tool result (gesture reads, player names, game state) goes to the remote person's Claude
  on Anthropic's servers, and onto their account's chat history.
- **The public URL.** Quick-tunnel URLs are random but public, and scanners find them. Without the token they only get 401,
  and the token is 32 random characters, so guessing it is not realistic. There's no rate limit, though, so a busy client
  can still spam the server; stop the tunnel if your terminal fills with 401s.
- **Prompt injection runs both ways.** The remote Claude acts on whatever is in its chat and tool results, and your robot
  does whatever that Claude asks. Only share the link with people you trust to drive the hardware.

**Before you share it:** back up RPS to SD slot 1 and commit or copy `server/data/`; leave `--speak` off unless you want it;
be on a call watching the robot.
**After:** Ctrl-C `cloudflared` and the server, put a new token in `.env`, and ask your friend to delete the connector.

**Stronger options, if this becomes more than a supervised test:** a named tunnel with Cloudflare Access in front (per-person
logins; Claude's connectors would then need OAuth, which this server doesn't do yet), a read-only tool profile for
spectators, and a per-person token so you can lock out one person without changing everyone's.
