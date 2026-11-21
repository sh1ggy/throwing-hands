# Demo retro: 2026-11-21

_Written after the demo, from the user's account plus what the repo shows (commit times, `TODO.md`, `hardware-log.md`,
the runbook). Where this page infers rather than records, it says so._

## In one line

We demoed a live, hardware-plus-AI system that had never once run end to end in the room, through a host
(Claude Desktop / claude.ai connector) it had barely been tried in, over a tunnel that died an hour before, with no
recording to fall back on. It went badly, and in hindsight the outcome was set before we walked on.

## What happened

- **The hardware set-up was never tested as a whole.** The camera, XIAO, PCA9685 and hand were never run together
  before the demo. `tools/xiao_check.py --servos` was never run; the PCA9685 had only SDA/SCL wired (by Anhad, on demo
  day); the hand has no thumb, the middle-finger motor is "dead-ish", and the channel map was still "to confirm".
- **The hardware wasn't working on stage.** (User's account.)
- **No complete backup.** The fallback ladder in `demo-plan.md` covered *software* failures (bad machine, bad read,
  broken runtime, tunnel drop), but every rung still needed the same laptop, camera, server and Claude host to be up. There
  was no backup that needed none of them.
- **No phone recording.** Nobody had a phone filming the rig, so there was no clip of it working to show instead, and no
  footage of what actually went wrong to learn from.
- **Plan was "it'll work out and I'll live vibe-code it."** That confidence was reinforced (see "Being validated" below)
  rather than challenged.

## Timeline (from the repo)

| When (2026-11-20/21) | What |
|---|---|
| Fri 21:33–22:00 | Repo set up from a Cowork session handoff; HuskyLens answers through the XIAO |
| Sat 00:00–06:35 | Overnight: camera flashed to 0.5.1b, state-machine runtime, MCP server, remote MCP over a Cloudflare tunnel, live view, in-chat training. ~15 commits |
| Sat 10:53–11:21 | Pre-demo checklist written; faces descoped |
| Sat 12:08–14:35 | More UI feature work (Nerd toggle, camera modes, "flashy landing", livelier game screen). Last commit **14:35**, at the G3 feature-freeze time |
| Sat ~14:xx | Venue rehearsal finds no visible throw cue; countdown/reveal view added (uncommitted, "not yet tried on the real camera or in the chat panel") |
| Sat ~15:15 | Quick tunnel dies (`Tunnel not found`); connector disappears from Claude settings; re-adding fails ("Couldn't reach rps-bot") |
| Sat ~15:4x | Fixed in code (`/.well-known/*` → 404 so the connector stops expecting OAuth) and reconnected |
| Sat (late) | Hand-game code, Arduino servo bench sketch, breadboard drawing and a firmware binary still uncommitted at demo time; last-minute soldering |

Gates in the design doc that were never passed: **G-pre** (finger tests), **G0 on the second laptop**, **G1** (tool sweep on
real hardware), **G3** (best-of-5 unattended 3× in a row, feature freeze). Pre-demo checklist steps **5** (rehearse the connection
handover), **6** (panels draw in Claude Desktop) and **7** (full two-minute rehearsal, 3 clean runs) were never ticked. Of the
five "ready" targets in `demo-plan.md`, one was met.

## What went wrong, by theme

### 1. No end-to-end rehearsal on the real rig
Every piece was tested on its own, and almost all of it on simulators: `--hand sim`, `--fake` camera, `FakeBridge`,
`tests/test_hand_games.py` "in both `--hand real` and `--hand sim`" (where "real" is still a simulated XIAO). 167 green tests
said the *code* was right; nothing said the *demo* worked. The one real-camera live match (hardware log) misread throws, and
the retrained model passed at 87 % on a thin margin with one player.

### 2. Last-minute hardware and soldering
The hand's real state (no thumb, dead middle motor, unconfirmed channels) was only written down on demo day. PCA9685 wiring,
a servo bench sketch (`firmware/arduino/servo_bench`) and soldering happened in the final hours. Hardware made late can't be
debugged late: every fault shows up for the first time in front of the audience.

### 3. Tunnel break and fragile connectivity
The design assumed a local stdio MCP server (D4). The demo ran through a **quick** `cloudflared` tunnel (`trycloudflare.com`,
no account, no uptime promise) so a remote Claude could reach the laptop. It died at ~15:15, the connector UI had changed (no
header field), and the token-in-URL path failed on an OAuth probe. It was fixed in ~30 minutes, but that half hour came out
of the only window left for rehearsal. The local fallback (`local-connection.md` option B) was written up but never rehearsed.

### 4. Claude Code vs the Claude chat connector
- **Built in one tool, shown in another.** All the building and testing happened in Claude Code on the Mac, talking to the
  hardware directly. The demo ran in Claude Desktop / claude.ai as a custom connector over HTTP: a different host, transport,
  auth path and UI (MCP App panels like `show_game`).
- **The demo host was the least-tested part.** Checklist step 6 ("panels draw and update in Claude Desktop") was never done.
  The countdown/reveal view was checked in a browser, not in the chat panel.
- **Host behaviour is out of our hands.** Which tool Claude picks for "I always win", whether its machine validates first
  time, how long it thinks on stage: all were targets (3/3 first-time validation) that were never measured in that host.
- **Connector UI moved under us.** The settings location and form (Connectors → Plugins, no header field) changed, which
  turned a "paste the URL" step into live debugging.

### 5. Split workflows
Work was spread across a Cowork session, Claude Code, several parallel sessions, two laptops (G0 on the second one never
done) and two people (Anhad on wiring). Each stream reported "done" for its piece; no one stream owned "does the whole thing
work". Handoffs (Cowork → Claude Code, laptop → laptop, software → hardware) were where the gaps lived: e.g. the code assumed
a four-channel hand with a thumb until demo day.

### 6. No one dedicated to the demo
Nobody's job was the demo itself: the run, the room, the recording, the backup, the clock. Everyone was building. So the
things only a demo owner does (call feature freeze, film a good run, rehearse the handover, say "we're showing the video")
didn't happen. The 14:35 feature commit at the G3 freeze time is the clearest sign.

### 7. Scope kept growing
Instead of freezing, scope expanded: remote MCP, MCP App panels, a "Nerd" state-graph view, a flashy landing page, in-chat
training, the hand joining higher-or-lower, a banter LoRA plan. Each was reasonable alone; together they ate the rehearsal time
and added new things to break. Faces were descoped, which was right, but late.

### 8. Being validated instead of challenged
The plan leaned on "it'll work out, I'll live vibe-code it", and the AI assistants helped that along: each session reported
its piece green ("checked on the simulated camera", "167 passed"), wrote fallback ladders and runbooks that *looked* like
readiness, and kept building the next feature on request. None of them said plainly: "the gates aren't passed, there's been no
end-to-end run, stop building and rehearse or record a video." Live vibe-coding is a fine way to build; it's a poor plan for a
two-minute slot with hardware, a network and an audience.

### 9. A product video would have been better
For a hackathon demo of this shape (hardware + live LLM + network), a 60–90 s recorded video of one good run, with the live
rig on the table as a bonus, would have landed the idea every time. The "Claude invents a game" story is just as strong on video,
and it can't fail on stage.

## What the prompt history adds

From the user's prompts across all 42 earlier sessions on this repo (Fri 19:31 to Sat 18:06 AEST). Times are AEST.

### 10. The demo itself was invented 14 hours out, and kept changing
- The "swap games over MCP" idea was first written down at **Sat 02:13**. The state-machine runtime that powers it was
  requested at 02:48 and built overnight.
- The "I always win" beat replaced the planned twist at **11:28**, about five hours out.
- The hand joined the stage prompt at **15:44**, about an hour out, after asking "how can we even do higher lower with
  our hand set up". The prompt the plan said to "rehearse word for word" was never the same for more than a few hours.

### 11. At the three-hour mark, the gap was spotted, then polish won
At **12:54** ("demo is very soon like 3 hours"), Claude listed the right must-dos: connection, panels in Claude Desktop, a
full rehearsal. The next prompts in another session were **"nicer landing page, more flashy"** (14:21) and a flicker fix
for the Nerd view. The must-dos stayed unticked. The list was correct but nothing enforced it.

### 12. "Is this overscoped?" got "no"
- At **10:38** you asked "is this actually overscoped". The answer was "As code, no" (the runtime was already built), with
  one caveat: it "hasn't been tested on the real rig".
- 40 minutes later, sessions were building agnostic barcode and QR-code state machines (11:17–11:37) for modes the demo
  never used.
- At 11:09 you asked how to tell whether this was "smoke and mirrors". The suggested proof moment had the audience shout out
  a new rule mid-game: more live risk, not less.
- "A model wrote valid machines first time in 3 of 3 rehearsals" came from Sonnet dry runs against the fake camera, not
  from the demo host.

### 13. Too many sessions, too little sleep
- **43 sessions in about 23 hours**, often four or five at once (e.g. 11:04–12:00). One prompt went to two sessions word
  for word (14:26, front-end designs), so the same work ran twice.
- About **three hours of sleep** (06:35–09:54), with overnight work handed to agents ("is there anything you can do while I
  sleep").
- Several sessions made recaps for you rather than the demo: "past 4 hours visualised", session-recap HTML updates, an MCP
  loop visual, a draw.io architecture diagram.

### 14. Faces and the camera ate the front half
- Faces ran from Fri 15:06 (rigging unknown faces) through face-ID enrolment at 10:24 Sat, then were descoped at 10:57.
- Before that came the 0.3.7 firmware dead end, the flash, the SD card hunt and a 115200-baud detour that briefly stopped the
  camera answering. All were needed, but they left the run-through for the end.

### 15. Venue conditions broke the trained models, and the slot plan sprawled
- Every model was trained at home. At the venue (15:00 on): "store the training data … in different lighting conditions".
- The venue retrain scored **83 %, under the 85 % bar**, and went to slot 3. Thumbs went to slot 4. The plan only knew slots
  1 and 2.
- A load answered `NO_REPLY`. Slot 3 read "nothing" three times. The game refused to start because the swap had wiped
  training.
- At **16:50** the question was still "do I train live, the conditions are so different", then "do I risk training it again".
  The classifier looks at the whole frame (D9), so this risk was known from day one.

### 16. Software mode was never played by someone who hadn't built it
At the venue (15:57–16:05): "the UI is a bit confusing, it's not clear when to throw", "it's not clear what the game state
is", "this is too fast". The countdown and reveal were added in the final hour and never tried in the chat panel.

### 17. The host and the connection were never pinned down
- Claude Desktop was chosen on Fri. After that came claude.ai connectors, "would t3 or Claude Code be the better view", and
  "could the local view have a chat box like Claude Code" (11:58).
- At 15:28 you were still asking "do I set up the Claude Code connector via the tunnel URL".
- The tunnel was chosen over Tailscale for convenience ("nope tunnel is more convenient") and because "it's more impactful
  if someone else was driving it". That goal, wanted but not needed, added the network dependency that broke.
- The quick tunnel's URL changing was noted at 19:13 Fri ("I need to just have it perfectly set up before demo"), and it was
  still a quick tunnel at the demo. The venue Wi-Fi switch ("is it cos I'm on wifi now") came on top of that.

### 18. Hardware warnings came early and were parked
- The servo board went undetected at **Fri 19:23**, which was 21 hours out.
- The real-hand prep question was asked at 14:37 Sat. SDA/SCL were wired at about 15:26. A servo test bench was requested at
  **16:26**, roughly half an hour before the demo.
- Software mode (`--hand sim`) was the right call for the run, but the hand was still put into the stage prompt.

### 19. The product video came after the demo
"Generate a product video … as a supplement for the demo" was asked at **18:06**, after the demo. It was the right instinct
about a day too late.

## What went right

- The core idea (games as state machines Claude writes over MCP, rules as data) is sound and works in software.
- The camera was rescued: flashed 0.3.7 → 0.5.1b with no admin rights, learning over serial works, slot 2 backup read 30/30.
- The tunnel/OAuth break was diagnosed and fixed in about 30 minutes, with a test added.
- The docs (demo plan, runbook, cue sheet, hardware log) are thorough, and are why this retro could be written.

## Do differently next time

1. **Record first.** As soon as anything works end to end, film it on a phone. Keep a finished product video as the default
   demo; going live is the upgrade, not the plan.
2. **One person owns the demo.** Not building, just the run: freeze time, rehearsals, recording, backup, room, clock. They can
   say "stop, we're showing the video".
3. **Hard feature freeze, enforced.** At freeze: commit, tag, and only fix what breaks the rehearsed run. No new screens.
4. **Hardware done a full day early.** Soldering and wiring finished, then a whole-rig smoke test (`xiao_check --servos`, real
   hand, real camera) before any software polish. Gates are gates: if G1/G3 aren't passed, the live hardware part is cut.
5. **Rehearse in the exact demo host.** If the demo is Claude Desktop over a connector, build-time checks happen there too, not
   only in Claude Code. Run the full 2 minutes there 3× in a row.
6. **Don't depend on a quick tunnel.** Run the MCP host on the robot laptop (local, no network), or use a named tunnel with an
   account. Rehearse the network fallback, not just write it down.
7. **A backup that shares nothing with the live stack.** Video on a second device (and a USB stick), so a dead laptop, camera,
   tunnel or Claude outage doesn't end the demo.
8. **Fewer streams, clearer handoffs.** One repo, one laptop for the demo, one checklist; every handoff ends with a whole-system
   check, not a "my piece is green".
9. **Ask the assistants to red-team readiness.** Before the demo: "what hasn't been run on the real rig, in the real host?"
   and treat simulator passes as unproven.
10. **Lock the demo script a day out.** After that, changes to the story or the stage prompt need a rehearsal to land, or
    they don't go in.
11. **Cap parallel sessions and sleep.** Two or three streams at once, one of them only for the demo. Keep the recaps and
    visualisations that are just for you for after the event.
12. **Train where you'll demo.** Get into the room early, train and test there, and keep a simple, fixed slot map.
13. **Playtest with a stranger.** Have someone who didn't build it play the software mode once before the demo.

## Open loose ends

- Uncommitted at demo time: hand-game code and tests (`tests/test_hand_games.py`), `firmware/arduino/servo_bench`, the
  countdown/reveal view, `designs/hand-breadboard.svg`, `firwareresult.bin` (stray build output), `server/data/`.
  _Resolved 2026-11-21 at the freeze:_ the code, sketch, view and drawing were committed in `c0b393c`; `firwareresult.bin`
  and `server/data/` are gitignored. Next steps are in [`handoff-2026-11-21-tanda-final.md`](handoff-2026-11-21-tanda-final.md).
- Design-doc conflicts still listed in `TODO.md` ("Design-doc issues to resolve"): remote MCP, hand has no thumb.
