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

**Claude Desktop or claude.ai:** Settings → Connectors → Add custom connector, URL `https://<host>/mcp?key=<token>`.
These connections come from Anthropic's servers, which is why the server has to be public, and they can't set custom
headers, which is why the token goes in the URL. _Not yet tried with a real connector._

**Claude Desktop through a local bridge** (keeps the token in a header): add a local server that runs
`npx mcp-remote https://<host>/mcp --header "Authorization: Bearer <token>"`.

Then check that `list_machines` answers.

## Rules

- **Treat the token like a password.** Anyone with it can wipe the camera, move the servos and run games. Make a new one
  after the event, and don't post a URL that has `?key=` in it.
- **One driver at a time.** There is one camera and one running machine. Two connected Claudes share them, and a second
  `start_machine` hits the one already running.
- **Stop the tunnel when you're not using it** (Ctrl-C on `cloudflared`). The server is unreachable without it.
- The game loop runs on the robot laptop, so a remote Claude only adds latency to its own tool calls, not to gesture
  reads or timers.
- For more than a token (named people logging in), put Cloudflare Access in front later. Claude's connectors would then
  need OAuth, which this server doesn't do.
