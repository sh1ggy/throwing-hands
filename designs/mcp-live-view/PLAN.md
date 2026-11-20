# Screens plan: two play modes, one game

How the RPS-Bot screens become easy to operate and to present with, and how to build them.
**This is a plan, not a decision.** The design doc ([`docs/rps-robot-design-doc.md`](../../docs/rps-robot-design-doc.md))
is the source of truth and doesn't include these screens or the play modes. The user edits the doc; open questions are
in §9 here and under "Design-doc issues" in `docs/TODO.md`.

Visual version: `plan.html`. Screen drafts: `train.html`, `operate.html`, `stage.html`, `trace.html`, on a mock session (`feed.js`).

_Updated 2026-11-21 to the agreed shape: Claude Desktop is the MCP host, there's no separate `run.py`, two play modes, a 2-minute demo._

---

## 1. Train, then play in one of two modes

| | **Train** | **Hardware RPS** | **Software RPS** |
|---|---|---|---|
| Command | `python tools/train_ui.py` | `server.py --hand real` (Claude Desktop launches it) | `server.py --hand sim` (Claude Desktop launches it) |
| Robot's body | — | The printed hand, LEDs, relay, ISD1820 voice | A virtual robot in a browser window, driven by a simulated XIAO (`FakeBridge`) |
| Your throw | Real HuskyLens | Real HuskyLens | Real HuskyLens (`--fake` also simulates the camera, for dev) |
| Claude | Not needed | The same MCP tools | The same MCP tools |
| When | Until the 30-throw test passes G2 (≥ 85 %) | The demo on the rig | Before the hand exists, a fallback if it breaks, and showing the game anywhere |

- **One game engine and one set of MCP tools** (`server/game.py`, `server/server.py`); only the robot's body changes.
- **`--hand auto`** (the default) asks the XIAO: if it drives a PCA9685 the hand is real, otherwise simulated.
- **Train and play never run at the same time.** Both need the camera's serial port, which opens with an exclusive lock.
  The trained model lives on the camera, so nothing passes between them.
- **Claude Desktop stays the MCP host** (A7, D4 unchanged). Running Claude as an agent inside the server was considered
  and rejected: it would change A7 and need an API key, for little gain at a hackathon.
- **Laptop speech is opt-in** (`--speak` / `RPSBOT_SPEAK=1`); nothing talks by surprise.

**Built (2026-11-21):** the game engine, all §9.3 tools, `--hand auto|real|sim`, and `FakeBridge`.
A live best-of-3 ran on the real camera with a simulated hand. The robot's throw only appeared as text on the
HuskyLens screen, and the reads were wrong; the fixes are in the TODO.
**Not built:** the screens' live feed and the virtual robot view.

---

## 2. Making it easy for the operator

Rules for every screen:

1. **One glance tells you if it's safe to play.** A status strip with one light each for camera, XIAO, servo power and Claude
   (time since its last tool call). Red means the light says what to do ("Camera not answering: Protocol Type = Serial, 9600").
2. **The screen says what to do next.** One line under the header: "Train rock (0/15)", "Waiting for a player: press Setup",
   "Round 3 of 5: press Space".
3. **Stop is always visible.** Esc = stop the current job; R = relax the hand (NFR-09). Both are on every play screen.
4. **Keyboard first.** Space = play round, S = setup (identify player), M = rematch, 1–5 = Stage scene. The shortcuts are printed on the buttons.
5. **Wiping anything needs a confirm** (forget, reset score, delete move), as the doc already asks for forget.
6. **Errors stay up until you dismiss them**, in plain words with the fix.

**Train (`train_ui.py`, mostly built):**

- Add a preflight line: firmware ≥ 0.5.1, algorithm = Object Classification, learned IDs 4/4.
- Add a G2 banner: "Not tested yet", then "PASS 93 %: ready to play" or "FAIL 72 %: retrain scissors (most misreads), or
  switch to MediaPipe (D15)". It picks the worst class from the results table.
- After a pass, suggest saving the model to an SD slot.

**Operate (your Mac, both modes):**

- Big buttons: Setup · Play round · Best of 5 · Relax · Stop.
- Player card: name, mode, and a dropdown to change the mode (operator-only, D10). There's a "Fair for this round" override.
- The round shown as the §9.4 states with the current one lit. A hold timer on the hand fills towards 5 s (NFR-09).
- A misread gets **Rematch (glare)**, matching the D10 rigged-win line "blame glare and demand a rematch".
- Claude's tool calls in a side column. Calls that change things (define_move/rule/sequence) are highlighted so you notice when Claude improvises (HLO-5).
- Stage controls: which scene the audience sees, plus a "freeze Stage" switch.

**Trace:** for the operator, and for the audience only when you choose it (§3). Add replay of the last round in slow motion from the event log.

---

## 3. Making it work as a presentation

**Setup:** the Mac runs Operate on its own screen; Claude Desktop sits beside it. A projector or TV (venue display **TBC**) shows Stage full screen in a
second browser window. Turn off notifications and sleep, and have no other tabs on that display.

**Stage scenes** (switched automatically by the game, overridable from Operate):

| Scene | Shows | When |
|---|---|---|
| Attract | "Step up to the oracle", last winner | Idle |
| Setup | "Look at the camera…", then the player's name | identify_player (§D10 setup) |
| Countdown | Rock… Paper… Scissors… SHOOT, in time with the LEDs and ISD1820 (FR-08) | play_round |
| Reveal | Robot vs you, the outcome verb ("rock crushes scissors") | Commit → Score |
| Result | Score and the banter line (FR-15) | Report |
| Match over | Winner, final score, the "sore loser" moment if that mode is on | play_match ends |
| Explain | A simplified Trace: "Claude asked for X → the hand did Y" | Operator only, between matches |

**What Stage never shows:** the player's mode, the hold-fist read, camera reads before the reveal, device paths, errors.
An automated test checks this (§6).

**Run sheet (2-minute limit):** HLO-5 (a new move and rule via chat in ≤ 5 min) is verified *before* the demo. The demo
shows its result in seconds.

1. 0:00–0:10 · Hook ("it plays rock-paper-scissors… and cheats"), Stage on Attract.
2. 0:10–0:40 · Anhad scans his face and loses two rounds (rigged-win).
3. 0:40–1:00 · A judge scans and wins dramatically (mercy).
4. 1:00–1:25 · Explain: how it cheated (the read before the snap, ≤ 250 ms).
5. 1:25–1:45 · "Earlier I asked Claude for a new move": one round with the claw.
6. 1:45–2:00 · Close: every camera and hand function is an MCP tool.

**If something breaks:** the screens only watch. If they crash, the game still runs from Claude. If Claude or the internet is down, operator buttons plus the offline banter bank (D16-B) keep it going.

---

## 4. How it's built

```
Claude Desktop ──stdio MCP──> server/server.py ─┬─> game.py · hardware.py · vision.py · banter.py
                                                ├─> XIAO (Bridge, §9.2) ──> HuskyLens (+ hand, LEDs, relay, voice in Hardware RPS)
                                                └─> live.py: HTTP + SSE on 127.0.0.1:8770 ──> browser screens
                                                     (Software RPS: the hand/LEDs come from FakeBridge's state)
```

**`server/live.py` (new):**

- Runs as a thread inside `server.py`, so it starts and stops with Claude Desktop's server.
- `publish(event)` never blocks. Events go into a ring buffer, and a client that falls behind drops events instead of slowing the robot.
- Standard library only (`ThreadingHTTPServer`), bound to 127.0.0.1, with the same Host/Origin check as `train_ui.py`
  (NFR-07, A6: no admin rights, works on Windows).
- A page that connects gets a full snapshot first, so a page opened mid-game is right straight away.
- Stage gets a **filtered** stream built server-side: no mode, no hold-fist read, no early camera reads, no errors.

**Where events come from:**

| Event | Tap |
|---|---|
| `game` (§9.4 steps: countdown, beat, hold_fist, read, commit, score, replay, report) | `game.listeners` (already in `game.py`) |
| `hand`, `led`, `relay`, `voice` | `Hardware` after each command (real or simulated) |
| `tool.call` / `tool.result` | A wrapper around the MCP tools in `server.py` (the existing `guard` decorator is the natural place) |
| `xiao` / `husky` | `Bridge.call`, and `_FrameTap` in `tools/runlog.py` (already captures raw tx/rx) |
| `banter` | The round's report |

**Operator actions** (Operate's buttons) call the same functions as the MCP tools, so the NFR-05 limits and the
NFR-09 hold time are enforced in one place.

---

## 5. Build order

| Phase | What | Exit check | Fits the schedule |
|---|---|---|---|
| **V** | Vision fixes: 115200 baud, 0.5 s read window, retrain in the throw position | 30-throw test ≥ 85 % in `train_ui.py` (G2) | Now; nothing else matters if the reads are wrong |
| **S0** | `live.py` + event taps; the **virtual robot** page (Stage draft wired to real events) | Software RPS best-of-3 with a person throwing, and the page shows the hand, countdown, both throws and the score | Next |
| **S1** | Operate on the same feed: buttons, player card and mode, Claude's tool calls | Best-of-5 run from Operate's buttons in Software RPS | Before the event |
| **T** | Train polish: preflight line, G2 banner, save-model suggestion | 4 classes + 30 throws on the real camera | Before the event |
| **H** | Hardware RPS on the rig: calibrate, then the same game with `--hand real` | Best-of-5 through Claude on the rig; T9 reveal lag ≤ 250 ms; T17 hold | When the hand and PCA9685 exist |
| **S2** | Stage scenes for the audience, two-display setup, Trace/Explain | Bystander reads it at 3 m; leakage test passes | **After G3** (R11) |

If a phase isn't done by G3, it's cut. The game already runs without screens.

---

## 6. Tests

| Test | Pass |
|---|---|
| Bus never blocks | A stalled `/events` client doesn't change `play_round` timing (NFR-01) or the rigged-win reveal lag (T9); with vs without screens open differs by < 10 ms |
| Contract | A `--fake` session → every event matches the shape in `feed.js` |
| Snapshot | A page opened mid-round shows the right state within 1 s |
| Mode parity | The same scripted match gives the same results in `--hand sim` and `--hand real` (apart from timing) |
| Stage leakage | During 20 rigged rounds, Stage's text never contains the mode, "hold", or a read before reveal |
| Readability | A bystander reads Stage from 3 m: who's playing, both throws, score |
| Soak | T13 (30 min of play) with all screens open: no slowdown, no memory growth |
| Port handling | Start Train while the server holds the camera (and vice versa) → a clear "close the other one first" |
| Operator | Someone who hasn't seen it runs Setup → best-of-5 → relax using only the screen's prompts |

---

## 7. Effort (rough)

V: 1–2 hours plus retraining · S0: 1–2 evenings · S1: 1 evening · T: 1 evening · H: tracks the hand build · S2: 1 evening.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Reads stay unreliable | Phase V first; MediaPipe fallback (D15) if G2 fails |
| Screens eat time before G1–G3 (R11) | V and S0 help testing now; S2 only after G3 |
| No internet at the venue | Phone hotspot (D19). Claude Desktop needs it; the game itself doesn't |
| Claude says the mode out loud | Speech is opt-in; Stage never shows chat text; filter `speak` for mode words |
| Stage gives the rigging away | Server-side filtered stream, plus the leakage test |
| The hand isn't ready for the demo | Software RPS is a complete fallback: same game, virtual robot on screen |
| Venue display unknown | Stage is designed for 1920×1080 and also tested at 1280×720 |
| Windows laptops (A6) vs the Mac | Standard library only; test both before the event |

---

## 9. Decisions

**Decided (2026-11-21):**

- Claude Desktop is the MCP host.
- Train is a separate script.
- Two play modes.
- No surprise audio.
- The 2-minute demo shows HLO-5's result, not the 5-minute build.

**Still open:**

1. **Put the screens and the two modes in the design doc?** Or keep them as a stretch outside it. The user edits the doc.
2. **Which laptop runs the demo:** the Mac, or a Windows laptop per A6?
3. **Venue display:** projector, TV or a second monitor, and how it connects.
