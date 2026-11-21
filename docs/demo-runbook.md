# Demo runbook: every command, in order

_Written 2026-11-21 for the current setup: a quick `cloudflared` tunnel and a Claude Desktop custom connector. The plan, the
targets and the fallback ladder are in [`demo-plan.md`](demo-plan.md). If they disagree, the plan wins. Before the
demo, replace the quick tunnel with something stable (see "Now / blockers" in [`TODO.md`](TODO.md))._

Shell commands run from the repo root. Lines marked **Chat:** are things you ask Claude in Claude Desktop (the tool it
should call is in `code`).

---

## A. Once, on the final rig (server stopped)

Only one program can hold the camera's serial port, so close `server.py` before running any `tools/` script.

```bash
.venv/bin/python -m pytest -q tests                  # everything green (152 tests on 2026-11-21)
```
```bash
.venv/bin/python tools/husky_test.py                 # camera answers through the XIAO: knock OK
```
```bash
.venv/bin/python tools/xiao_check.py                 # every XIAO command OK (add --servos with V+ powered)
```

**Slot 1, RPS backup.** Train rock=1, paper=2, scissors=3, nothing=4 in that order (forget first, then 3 × 15 per class), run
the 30-throw test until it passes 85 %, and save to slot 1 from the page:
```bash
.venv/bin/python tools/train_ui.py --open
```

**Slot 2, thumbs backup.** Start the server (section B), then:
- **Chat:** "Swap to higher or lower and train it: up, then down, then nothing, 15 samples each, three times per class. Then save the model to slot 2."
  (`start_machine('higher_lower', swap_gestures=true)`, `train_gesture` × 9, `husky_save_model(2)`)
- Stop the server and check every class reaches at least 85 %:
```bash
.venv/bin/python tools/gesture_check.py up down nothing -n 30
```
Copy the SD card's model files to the laptop by hand afterwards (the protocol can't read them back).

---

## B. Every session: bring everything up

**No tunnel:** to run Claude Desktop on this laptop instead (no terminal 1, URL never changes), see [`local-connection.md`](local-connection.md).

Use three terminals. Keep them open, and **never restart the tunnel** unless you have to, because its URL changes when you do.

**Terminal 1, the tunnel:**
```bash
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8770
```
Copy the `https://<random>.trycloudflare.com` URL it prints.

**Terminal 2, the server** (software mode, as in the plan; this restarts freely and the URL stays the same):
```bash
.venv/bin/python server/server.py --http --hand sim
```
- Use `--hand real` for hardware mode (the printed hand, LEDs and relay).
- Add `--speak` only if you've decided on laptop voice. **It makes sound.**
- Add `--fake` to rehearse without the camera.

**Terminal 3, the projector view** (opens the live view locally, so it doesn't go through the tunnel):
```bash
open "http://127.0.0.1:8770/view?key=$(grep '^RPSBOT_TOKEN=' .env | cut -d= -f2- | tr -d "\"'")"
```
Make the window full screen on the venue display. The **Game** tab is the default; **Nerd** shows the state graph.

**Claude Desktop** (only when the tunnel URL changed): Settings → Connectors → rps-bot → edit:
- **URL:** `https://<random>.trycloudflare.com/mcp` (include `/mcp`)
- **Request header:** `authorization` = `Bearer <RPSBOT_TOKEN from .env>`

Print the token to copy it:
```bash
grep '^RPSBOT_TOKEN=' .env | cut -d= -f2-
```

After a server restart: start a new chat, or toggle the connector off and on.

**Smoke check:**
- **Chat:** "List the machines and tell me the camera status." (`list_machines`, `husky_status`): `rps` and `higher_lower` show as fallbacks, and the camera answers.

---

## C. Before every run

- **Chat:** "Restore rock paper scissors: swap the gestures back to RPS, then load model slot 1, then read my throw three times." Show rock, paper, scissors.
  (`start_machine('rps', swap_gestures=true)`, `husky_load_model(1)`, `read_gesture` × 3; retrain with `train_ui.py` only if slot 1 is missing or reads badly)
- **Chat:** "Open the live game view." (`show_game`): the panel shows in the chat. The projector page from section B picks up any game by itself.
- Check the room: same distance, plain backdrop and fill light as in rehearsal.

---

## D. The run (two minutes)

The on-screen version with every prompt and what to say: [`demo-cues.html`](../designs/demo-cues.html).

1. **Chat:** "Let's play rock paper scissors, best of 3." (`start_machine('rps', vars={match: true, best_of: 3})`). Play.
2. Say out loud: **"Ok, I'm bored."**
3. **Chat**, word for word:
   > Ok, I'm bored of rock paper scissors. Make up a new game: higher or lower, numbers 1 to 10, thumbs up means
   > higher, thumbs down means lower. Teach yourself thumbs up and thumbs down, then let's play. Roast me if I lose.
4. Follow Claude's training cues: thumbs up, thumbs down, then take your hand away.
5. Play one or two guesses. The Game tab shows the game Claude wrote, with no code change.
6. Hand out of frame. **Chat:** "Plot twist: from now on thumbs down means higher and thumbs up means lower. Change it
   while the game is running, don't restart it." (one `patch_machine`, live). Play one guess the new way.
7. Close: "Two minutes ago it only knew rock paper scissors." **Chat:** "Stop the game." (`stop_machine`)

---

## E. If something goes wrong

The full fallback ladder is in [`demo-plan.md`](demo-plan.md#if-something-goes-wrong-fallback-ladder). The quick version:

| Problem | Do |
|---|---|
| Claude's machine is rejected twice | **Chat:** "Use the fallback higher_lower." (`start_machine('higher_lower', swap_gestures=true)`, train up/down/nothing, `start_machine('higher_lower')`) |
| A gesture reads badly | Retrain that gesture once. Still bad: **Chat:** "Load model slot 2." (`husky_load_model(2)`) |
| The machine runtime misbehaves (before the thumbs swap) | **Chat:** "Play a best of 3 with play_match." |
| The server is broken | Ctrl-C terminal 2, then the commands below, then restart the server |

```bash
git stash
```
```bash
git checkout rps-deterministic
```

| Problem | Do |
|---|---|
| Panel doesn't show in the chat | Carry on: the projector page is the same view |
| Connector says unauthorised | Re-paste the header: `Bearer ` + the token, with no quotes |
| The tunnel or venue network drops | Leave the server running. Ctrl-C `cloudflared`, enable the local `rps-bot` in Claude Desktop on this laptop ([`local-connection.md`](local-connection.md) option B), start a new chat, run the smoke check. Turn the tunnel connector off so Claude doesn't see two `rps-bot`s |

---

## F. Afterwards

- **Chat:** "Stop the game and restore rock paper scissors from slot 1." (`stop_machine`, `start_machine('rps', swap_gestures=true)`, `husky_load_model(1)`)
- Ctrl-C the tunnel (terminal 1) when you're done: the server is unreachable without it. Ctrl-C the server (terminal 2).
