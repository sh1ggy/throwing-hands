# Handoff: Tanda 2026 freeze → whoever picks this up next

_Written 2026-11-21, after the demo, when the project moved off a Claude subscription and back to usage-based billing.
Whoever picks this up (another subscription, another account, a new Claude session, or me in a month) should start here. It
records **what we built, why it took the shape it did, and what's still missing**. The design doc is still the source of truth
for the robot; this page is the map._

## The 60-second version

- **What it is.** RPS-Bot (`throwing-hands`): a robot hand plus a HuskyLens camera that plays rock-paper-scissors. Under the
  hood it's a **gesture-game machine**: games are JSON state machines that Claude writes and patches live over MCP. RPS is
  only the built-in fallback game.
- **Where it stands.** The software works on the simulators: **202 tests pass, 2 skipped** (2026-11-21, `.venv/bin/python -m
  unittest discover tests`). It has **never run end to end on the real rig, or in the host we demoed in** (Claude Desktop /
  claude.ai connector). The hand has two working channels and no thumb.
- **The demo** (Tanda 2026, Sat 2026-11-21) went badly. Why, in detail: [`demo-retro-2026-11-21.md`](demo-retro-2026-11-21.md).
  Read it before planning anything. Most of what's missing below comes from it.
- **Frozen.** Everything as presented, plus these docs, is on branch **`tanda-2026-presentation`** and tag
  **`tanda-2026-final`**. `main` carries on from the same commit. Don't move the tag or push to that branch.
- **First thing to do next:** read "Suggested first session" at the bottom.

## Frozen refs

| Ref | What it is | Get it back |
|---|---|---|
| tag `tanda-2026-final`, branch `tanda-2026-presentation` | The repo as presented at Tanda 2026, plus this handoff, the retro and the product video. Code is the same as `c0b393c` (demo-day work, committed after the demo) | `git checkout tanda-2026-final` |
| tag `rps-deterministic` | The plain RPS engine plus the full MCP server, from before the state-machine rewrite (Sat ~03:00). The "boring but works" fallback | `git checkout rps-deterministic` |

## What works, what's only simulated, what's broken

| Area | State |
|---|---|
| State-machine runtime, validation, patching, fallback machines (`rps`, `higher_lower`) | **Works, simulated.** Covered by tests; reveal lag 0–1 ms on the simulator |
| MCP server (`server/server.py`, ~60 tools), stdio and `--http` with a token | **Works.** HTTP + token checked through a quick tunnel with a friend's claude.ai account. The `/.well-known/*` → 404 fix (OAuth probe) went in on demo day |
| Live game view (`/view`, `show_game` MCP App), countdown/reveal, Nerd toggle, landing | **Works in a browser on the fake camera.** **Never seen drawing in Claude Desktop's chat panel** (checklist step 6 never done) |
| Train page (`/train`, `show_train`) | Works on the fake camera, and was used on the real camera through the browser. The panel was never seen in Claude Desktop |
| HuskyLens | **Works.** Flashed to 0.5.1b; learn/forget/save/load over serial work. RPS slot 1 scored 26/30 = 87 % at home and up/down/nothing in slot 2 scored 30/30. **The venue retrain scored 83 %** (slot 3). The model is lighting-sensitive (D9: it classifies the whole frame) |
| XIAO + CircuitPython bridge | Works (`cpy-bridge 0.5`, everything in `xiao_check.py` except servos). **It might be running the Arduino `servo_bench` sketch now** (`firwareresult.bin` looks like that build). If `xiao_check.py` gets no answer, re-flash the CircuitPython UF2 and `circup install` |
| PCA9685 + servos + hand | **Never tested as a whole.** Only SDA/SCL are wired. No thumb, the middle motor is dead-ish, the channel map is unconfirmed, and `xiao_check.py --servos` has never been run |
| Faces | Built (`identify`, `register_player`), descoped from the demo. Only shiggy = face ID 1 has been checked (10/10) |
| Banter | 60-line bank works. The LoRA plan (`banter-training-plan.md`) is a plan, no code |
| Product video | [`designs/product-video/`](../designs/product-video/): a 1:48 silent animated 1920×1080 MP4 and a 30 s cut (`node render.mjs --cut`), both committed. It's animation, not footage of the rig |

## The thought process: how it got this shape, and why

These are the main decisions in order, with the reasoning at the time and whether I'd make the same call again. Timestamps
are AEST, from the commits and the prompt history (the retro has more).

### 1. Start: a robot that plays RPS, built from a design doc (Fri)
- **Thinking:** a physical hand plus a cheap AI camera (HuskyLens) plus Claude for personality and modes (fair, rigged, grudge,
  bribery…). The design doc (`rps-robot-design-doc.md`, D1–D19, gates G-pre–G3) was written first so a two-person team could
  split hardware (Anhad) and software (me).
- **Kept?** Yes. The doc is good. The mistake was treating its gates as advice instead of gates.

### 2. Camera first, because nothing works without reads (Fri night → Sat morning)
- **Thinking:** the camera was the riskiest part (old firmware 0.3.7 couldn't learn over serial). So I flashed it to 0.5.1b
  without admin rights, built a serial protocol library, a training CLI and then a web training UI, and logged every run.
- **Cost:** it ate the front half of the hackathon (firmware dead ends, the SD-card hunt, a 115200-baud detour that briefly
  killed the camera).
- **Kept?** It had to be done. I'd timebox it, and switch to MediaPipe (D15) at G2 the way the doc said, not keep pushing.

### 3. Two play modes: hardware RPS and software RPS (Sat early)
- **Thinking:** the hand was behind, so make the demo work **with or without it**. Same engine, same MCP tools; `--hand sim`
  swaps the XIAO for a simulated one while the real camera still reads you. `--hand auto` asks the XIAO whether a PCA9685 is there.
- **Kept?** Yes. It was the right call and it's why anything could be shown. But in the end the hand went back into the stage
  prompt an hour before, which undid the safety.

### 4. Claude Desktop as the MCP host; Claude never sits in the tight loop
- **Thinking:** Claude is great at *rules and talk* and slow and non-deterministic at *timing*. So the server reads the camera,
  runs timers and decides rounds itself; Claude only sets things up and comments. I rejected having the server start its own
  Claude agent: Claude Desktop is the product surface.
- **Kept?** The split is right. Picking a host that was hard to test in during the build (all the building happened in Claude
  Code) was the expensive part.

### 5. The pivot: "games are state machines Claude writes" (Sat 02:13 → built overnight)
- **Thinking:** RPS with modes is a gimmick. The interesting demo is **"it only knew RPS two minutes ago, and now it plays a
  game it invented and taught itself."** So turn the server into a general state-machine runtime (states, gesture/timer
  events, guarded transitions, safe expressions, validated atomic patches), make RPS one machine, and let Claude write new
  ones over MCP. The old engine was tagged `rps-deterministic` first, so the bet could be undone.
- **Why a state machine and not code:** rules as data can be validated before they run, patched live without breaking the
  running game, and need no new firmware. This is the design doc's rejected D11 option C (a DSL), adopted because the demo
  depends on it.
- **Kept?** Yes. **This is the idea worth carrying forward.** Its timing was the problem: invented 14 hours out, and the story
  kept changing until an hour before.

### 6. Live gesture swap and live teaching
- **Thinking:** a new game needs new gestures, so Claude can wipe the camera and name a new set (`swap_gestures`), then teach
  it on stage ("show me thumbs up…"). Saved SD-card models stay **manual backups only**, never auto-loaded, because an
  automatic load would turn the live teach into theatre (demo-plan principle 2).
- **Kept?** The honesty principle, yes. But live training in an unknown room is the riskiest thing on the stage. Next time,
  train in the room early, or show it on video.

### 7. Remote MCP over a quick Cloudflare tunnel (Sat)
- **Thinking:** "it's more impactful if someone else is driving it": a friend's claude.ai drives the robot over the internet.
  I chose a quick tunnel over Tailscale or a named tunnel because it was convenient and I had no domain.
- **Cost:** the tunnel died at ~15:15, the connector UI had changed, and fixing it took the last rehearsal window.
- **Kept?** No, not for a demo. Run the MCP host on the robot laptop (`local-connection.md` option B), or a named tunnel
  rehearsed end to end.

### 8. Screens: live view, Nerd view, Train inside the server, landing page (Sat late morning → 14:35)
- **Thinking:** the audience has to *see* the state machine and the game, so: an MCP App panel in chat, `/view` for a
  projector, a Nerd toggle showing the state graph, and the train page inside the server so retraining doesn't mean a restart.
- **Cost:** this was polish during the window that should have been rehearsal. The last feature commit landed at the G3
  freeze time.
- **Kept?** The live view and the Nerd graph are what make the idea legible. The landing page and flash could have waited.

### 9. Faces descoped (Sat 10:57)
- **Thinking:** the two-minute demo never scans a face, and every scan switches the camera off gestures.
- **Kept?** Right call, made too late.

### 10. After the demo: retro and product video (Sat evening)
- **Thinking:** be honest about what happened (the retro), and make something that shows the idea and can't fail (the video).
- **Kept?** Yes. The video should have come first, not last.

### The honest summary
The idea (rules as data, Claude writes the game, the server plays it in real time) is good and works in software. I lost
the demo to scope and sequencing: no end-to-end run on the real rig, no run in the real host, a fragile network path, hardware
finished in the last hour, too many parallel sessions, and AI assistants that reported "my piece is green" instead of "the whole
thing has never run." The retro's "Do differently next time" list is the checklist for the next attempt.

## The gaps, in priority order

Full status is in [`TODO.md`](TODO.md); this is the short list that matters.

### P0: make it actually work once, end to end
1. **Rig smoke test.** Re-flash the XIAO to CircuitPython if needed, wire the PCA9685 (5 V on V+), confirm the channels for the
   two working motors, tie off or fix the middle finger, run `tools/xiao_check.py --servos`, then `calibrate_finger`.
2. **Redo the hand moves for two channels.** The code still assumes §8.2's four channels (CH0 thumb…CH3 ring+little) and
   `pose` takes four values.
3. **One full run in the real host.** Claude Desktop on the robot laptop over `local-connection.md` option B (no tunnel). Check
   that `show_game`/`show_train` draw and update in the chat panel; RPS → "I always win" → new game. Measure the demo-plan targets
   (swap + teach ≤ 35 s, Claude's machine valid first time 3/3, thumb read ≤ 1 s).
4. **Film it** on a phone as soon as it works once.

### P1: make it reliable
5. **Reads.** Retrain RPS on the final rig in the final light to ≥ 85 % with more than one player (T7). If it won't hold, build the
   MediaPipe fallback (D15; `MediaPipeVision` is a stub).
6. **Measure on hardware:** T9 rigged-win reveal lag ≤ 250 ms, T10 fairness on the real hand, `throw_ms`, tap speed, the 5 s hold
   watchdog, NFR-01 on a real best-of-5.
7. **Playtest software mode with someone who didn't build it** (the venue feedback was "not clear when to throw", "too fast").

### P2: product and code
8. A running machine isn't restored after a server restart; per-player memory that outlives a machine (`remember` action).
9. `play_round`/`play_match` → thin wrappers over `start_machine('rps')`, then delete the duplicate `_round` in `game.py`.
10. Machine-language limits (one machine at a time, static `to`, no sub-machines, countdown as one blocking action).
11. Remote-access hardening if it ever goes beyond supervised tests: Cloudflare Access/OAuth, a read-only spectator profile,
    per-person tokens (`remote-mcp.md` → Security).
12. Banter: align `banter.py`'s format with the training plan; check Anthropic's terms before any LoRA on Claude outputs.
13. Product video: music/voice-over, and real rig footage once it exists.

### P3: docs and the design doc
14. **The design doc is out of date** with what was built (state machines, remote MCP, two modes, live view, camera modes,
    the hand without a thumb, faces descoped, 2-minute demo). Every conflict is listed under "Design-doc issues to resolve" in
    `TODO.md`. **The owner edits the design doc, not agents.** Decide which of those to accept.
15. T1 protocol unit tests, T3 FakeBridge parity, `CONNECT_TEST.md`, and a `CLAUDE.md` that points at `AGENTS.md` (§9.1).

## State that lives outside git

None of this is in the repo. A fresh clone won't have it.

| What | Where | Notes |
|---|---|---|
| MCP token | `.env` at the repo root (`RPSBOT_TOKEN`), gitignored | Make a new one on a new machine. Anyone with the old one could drive the robot while a tunnel is up; no tunnel is running now |
| Runtime data | `server/data/` (gitignored as of this commit) | Settings, players, history, saved machines, ~40 training-run logs. The server recreates the defaults on first run. Settings that differ from the code defaults: `read_window_s` 0.5, `countdown_period_ms` 1000 |
| Demo snapshot | `demo-snapshot/` (gitignored) | `tools/demo_reset.py restore` puts it back |
| Removed user machine | `~/rps-removed/higher_lower.user-copy.json` | A reformatted duplicate of the fallback; safe to delete |
| Trained camera models | HuskyLens SD card: slot 1 RPS (87 %, home light), slot 2 up/down/nothing, slot 3 venue RPS (83 %), slot 4 thumbs, slot 5 a 3-class test, slot 9 an older RPS | The protocol can't read models back; copy the card's files to the laptop to keep them |
| XIAO firmware | On the board | CircuitPython `cpy-bridge 0.5`, unless `servo_bench` was flashed last (see above) |
| Camera firmware | On the camera | 0.5.1b, Protocol Type = Auto Detect, 9600 baud. Don't try 115200 again (it stopped the camera answering) |
| Product video render deps | `designs/product-video/node_modules` is a symlink to `/tmp/vidrender/node_modules` | Gone after a reboot. `npm i puppeteer-core` and ffmpeg to re-render |
| HuskyLens flasher and firmware image | `vendor/huskylens-uploader/` (gitignored) | Needed only to re-flash the camera (`tools/flash_huskylens.py`); its docstring says where to get them |
| `firwareresult.bin` | Repo root, gitignored | A stray firmware build (likely `servo_bench`). Not needed |
| claude.ai connector | The account that added it | Points at a dead quick-tunnel URL; remove it |
| Assistant memory | `~/.claude/projects/…/memory/` on this Mac | Not in the repo and not shared across accounts; its rules are copied into `AGENTS.md` → "Working rules" |

## Working rules for the next assistant

These were set during the hackathon and live in `AGENTS.md` → "Working rules", because memory doesn't carry over to a new account:

- **Don't edit the design doc** (`rps-robot-design-doc.md` or its PDF). List conflicts under "Design-doc issues to resolve" in
  `TODO.md`; the owner decides.
- **No surprise audio.** Laptop speech is opt-in (`--speak` / `RPSBOT_SPEAK=1`); say so before any test that can make sound
  (TTS, ISD1820, relay click).
- **Commit straight to `main`**, no PRs. Never commit `.env`, `server/data/` or firmware binaries.
- **Simulator passes aren't proof.** Say what ran on real hardware, and in which host, every time you report status.
- Keep `TODO.md` current in the same change as the work.

## Suggested first session

1. Read this page, then [`demo-retro-2026-11-21.md`](demo-retro-2026-11-21.md), then [`demo-plan.md`](demo-plan.md) and
   [`state-machine.md`](state-machine.md).
2. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` (if the venv is gone; `requirements.txt` was added at
   the freeze from what the venv had, and there's no README yet), then
   `.venv/bin/python -m unittest discover tests`, which should give 202 tests, OK (skipped=2).
3. With no hardware: `.venv/bin/python tools/view_demo.py higher_lower` to see a machine run on the fake camera.
4. Decide on a goal before building anything: **(a)** a working rig and a filmed run (P0), **(b)** a software-only product
   (the hand as an option), or **(c)** park it. If (a), start with the rig smoke test and nothing else.

## Doc map

| Doc | What it's for |
|---|---|
| [`rps-robot-design-doc.md`](rps-robot-design-doc.md) | Source of truth for the robot (owner edits only) |
| [`demo-plan.md`](demo-plan.md) | The two-minute demo: beats, prompt, targets, fallbacks |
| [`demo-retro-2026-11-21.md`](demo-retro-2026-11-21.md) | What went wrong and what to do differently |
| [`state-machine.md`](state-machine.md) | How the server runs games |
| [`TODO.md`](TODO.md) | Full status, gates and design-doc conflicts |
| [`demo-runbook.md`](demo-runbook.md), [`../designs/demo-cues.html`](../designs/demo-cues.html) | Every command in order, and the presenter cue sheet |
| [`local-connection.md`](local-connection.md), [`remote-mcp.md`](remote-mcp.md) | Connecting Claude: local (recommended) and over a tunnel |
| [`hardware-log.md`](hardware-log.md) | What was measured on real hardware |
| [`gesture-profiles-plan.md`](gesture-profiles-plan.md), [`banter-training-plan.md`](banter-training-plan.md) | Plans, mostly not built |
| [`handoff-2026-11-20.md`](handoff-2026-11-20.md) | The earlier Cowork → Claude Code handoff (historical) |
