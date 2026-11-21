# RPS-Bot: living TODO

What still needs to happen, in order of when it blocks things. The plan and the "why" live in
[`rps-robot-design-doc.md`](rps-robot-design-doc.md) (the source of truth); this file only tracks status.
Section refs (§, D, T, V, G, A) point into the design doc.

**Keeping it current:** tick items as they land (add the date and a one-line result), add new work as it
turns up. Agents: update this file in the same change as the work. **Don't edit the design doc:** the user makes
those changes. List proposed doc changes under "Design-doc issues to resolve" instead.

_Last updated: 2026-11-21 (post-demo freeze: tagged `tanda-2026-final`; handoff written; unfinished pre-demo steps left open as a record)_

**Demo target:** [`demo-plan.md`](demo-plan.md) (the two-minute run, its targets and fallbacks). **How the server works:**
[`state-machine.md`](state-machine.md).

**Demo held 2026-11-21 and went badly.** Retro, causes and what to do differently: [`demo-retro-2026-11-21.md`](demo-retro-2026-11-21.md).

**Frozen 2026-11-21 for the move off the subscription.** Start with [`handoff-2026-11-21-tanda-final.md`](handoff-2026-11-21-tanda-final.md):
the reasoning behind the build, the gaps in priority order (P0–P3) and the state outside git. The pre-demo checklist below is kept as
a record. Its open steps (5–7) are now P0 in the handoff.

## Freeze and handoff (2026-11-21)

- [x] **Frozen:** branch `tanda-2026-presentation` and tag `tanda-2026-final` at the handoff commit (code = `c0b393c`); 202 tests
      pass, 2 skipped (simulators only)
- [x] Handoff doc, `AGENTS.md` "Start here" and "Working rules" (the assistant-memory rules copied into the repo; the old
      "update the design doc" line now matches the owner's rule: agents don't edit it)
- [x] Product video committed (`designs/product-video/`, MP4 included); `server/data/` and `*.bin` gitignored
- [x] `requirements.txt` added (`mcp==2.3.0`, `pyserial==3.5`, from the venv; Python 3.10)
- [ ] **XIAO firmware state unknown:** `firwareresult.bin` looks like the Arduino `servo_bench` build. If `xiao_check.py` gets no
      answer, re-flash the CircuitPython UF2 and `circup install` before anything else
- [ ] No README for a fresh clone (what it is, setup, how to run on the fake camera). The handoff's "Suggested first session" covers it for now
- [ ] Remove the dead `rps-bot` connector from the claude.ai account (it points at a dead quick-tunnel URL); rotate `RPSBOT_TOKEN`
      on any new machine
- [ ] Copy the HuskyLens SD card's model files to the laptop (slots 1–5, 9); the protocol can't read them back

---

## Pre-demo checklist (the only list that matters until the demo)

Mirrors **"00 Next: the run list to the demo"** in [`designs/session-recap-2026-11-21.html`](../designs/session-recap-2026-11-21.html),
which is the source of truth for this list (commands there and in [`demo-runbook.md`](demo-runbook.md)). The camera is the
blocker: nothing past step 3 is worth much until gestures read ≥ 85 %. The demo runs in **software mode** (`--hand sim`), so the
PCA9685/servos and G0 on the second laptop don't block it. Faces are descoped (section "Descoped: faces").

- [x] **1. Restart `server.py`, commit the docs** (2026-11-21: 167 passed; server restarted `--http --hand sim`, tunnel left running) and the `gesture_check.py --baud` flag (leave out `firwareresult.bin` and
      `server/data/`). Picks up the swap reconnect fix and `621759d`'s stop/timer fix; 167 tests green at 11:10
- [x] **2. Faster reads: 115200 dropped (2026-11-21, user decision).** The camera makes only ~9.8 frames/s in classification and 9600
      already polls ~19/s, so a faster UART mostly re-reads the same frame. Kept: `read_window_s` 0.3 → 0.5 (~5 real frames per read,
      set in `server/data/settings.json`). Camera back on Protocol Type = Auto Detect at 9600. After trying 115200 the camera stopped
      answering through the XIAO at any baud (XIAO fine, reloaded); back on Auto Detect / 9600 the knock passes again
- [x] **3. Retrain RPS from scratch → slot 1** (2026-11-21: one pass, rock 13 / paper 15 / scissors 14 / nothing 15; 30-throw test **26/30 = 87 % PASS** at a 0.3 s vote, one player; paper→rock 2, scissors→paper 2; saved to slot 1 OK 12:50. Thin margin: add a paper or scissors pass if rehearsal misreads) on the running server's `/train`: forget all, ~40 per class in the throw position,
      paper spread wide. Done = 30-throw test ≥ 85 % (G2/T7; last 70 %), then `husky_save_model(1)`. Slot 1 holds a weak model now
- [x] **4. Up/down/nothing → slot 2** (2026-11-21: up 20 / down 20 / nothing 35 on /train, test **30/30 = 100 %** at a 0.5 s vote, saved slot 2 OK 12:59. RPS restored after: swap back + `husky_load_model(1)`; the load answered `NO_REPLY` but landed, learned 75 and the empty frame reads `nothing`. **`NO_REPLY` on a load is not a failure: check `learned_ids` before retrying**) (optional: 67 % model accepted as is). Retrain after step 2 if time allows; demo target is ≥ 85 % each
      on `gesture_check.py up down nothing -n 30 --baud 115200`. Then restore RPS (swap back, `husky_load_model(1)`)
- [ ] **5. Connection:** start the `cloudflared` quick tunnel once and leave it running, add the connector once; set up
      [`local-connection.md`](local-connection.md) option B disabled as the fallback; rehearse the handover once (runbook §E)
- [ ] **6. Panels in Claude Desktop:** `show_game` and `show_train` draw and update during one RPS round; else `/view` and `/train` in a browser
- [ ] **7. Full two-minute rehearsal** on the real camera with the prompt as written: swap + teach ≤ 35 s, Claude's game valid first
      time, a thumb registers in ≤ 1 s (tune `gesture_window_s` / `gesture_stable`), then 3 clean runs in a row; restore slot 1 before each
      **Run it from [`demo-cues.html`](../designs/demo-cues.html)** (updated 2026-11-21: RPS → "I always win" → new game). Check what
      Claude does with the "always win" prompt (`set_player_mode` or `patch_machine`; both pass on the simulator, 6/6 rounds lost),
      that the run fits ~2:10, and reset guest to fair before each run

- [x] **Demo reset script** (2026-11-21): `tools/demo_reset.py snapshot` copies the game data and machines to
      `demo-snapshot/` (gitignored, never read by the server) with every player fair and 0-0-0; `restore` puts it back
      with the server stopped and deletes machines saved since. Tried on a scratch copy. Snapshot retaken 2026-11-21
      after removing the saved `higher_lower` user copy (a reformatted duplicate of the fallback; moved to `~/rps-removed/`),
      so a restore leaves no user machines. Cue sheet updated: reset, connection and voice chips (runbook §C)

- [x] **Product video** (2026-11-21): [`designs/product-video/`](../designs/product-video/) renders a 1:48 silent
      1920×1080 supplement (`node render.mjs` → `throwing-hands-product.mp4`; open `video.html` to preview live). Beats follow
      this plan: rig (`--hand real` / `--hand sim`), engine vs states, RPS, "I always win", "I'm bored", live teach, higher or lower
      on both hands. Animated, not footage. Still to decide: music/voice-over, and swapping in real rig clips once filmed

Also before the demo (`demo-plan.md` → Open decisions): voice on stage (`--speak` or screens only); which laptop and what's on the
venue display; final rig position, backdrop and fill light the same for training and the run. Once slots 1 and 2 are final:
copy the SD card's model files to the laptop (the protocol can't read them back).

---

## Now / blockers

- [x] **Software-mode RPS had no visible throw cue or robot throw** (venue rehearsal 2026-11-21): the countdown went only
      to the camera's tiny screen and took 1.35 s, so players threw late (`no_read`) and only saw "ROBOT: PAPER" text.
      Now: the `countdown` action logs a `countdown` output event (words, beats, period, wall-clock start); `/view` and
      the chat panel draw ROCK… PAPER… SCISSORS… SHOOT! full-screen on the wall clock, then a robot-vs-you reveal (✊✋✌️,
      who won) from the RPS vars; the last round's "ROBOT: …" line no longer lingers into the next countdown
      (`screen.py`); the browser view polls fast between games. `countdown_period_ms` 450 → 1000 in `server/data` and
      `demo-snapshot` (code default unchanged). Checked on the fake camera in a browser; games without `countdown`
      (higher_lower) are unchanged. Not yet tried on the real camera or in the Claude chat panel

- [x] HuskyLens answers through the XIAO: knock OK, `request()` returns INFO (2026-11-20; see `hardware-log.md`)
- [x] Initial work committed and pushed to `main` (2026-11-20)
- [ ] **Before the demo: a stable Claude ↔ server connection.** Decided 2026-11-21: the quick `cloudflared` tunnel, started
      once and left running all day (its URL changes only when `cloudflared` restarts; restarting `server.py` keeps it), so the
      connector is added once. Fallback: Claude Desktop on the robot laptop via `mcp-remote` → 127.0.0.1:8770
      ([`local-connection.md`](local-connection.md) option B, runbook §E), kept disabled until needed so Claude doesn't see two
      `rps-bot`s. Left: rehearse the handover once (connector smoke check, kill `cloudflared`, switch to local)
      - [ ] Venue 2026-11-21 ~15:15: the quick tunnel died (`Unauthorized: Tunnel not found`, Cloudflare deleted it) and the
        rps-bot connector was gone from Settings → Plugins; re-adding it as `…/mcp?key=<token>` (no header field in the new UI)
        failed with "Couldn't reach rps-bot". Likely cause: `/.well-known/oauth-*` answered 401, read as "needs OAuth".
        Fixed in `TokenGate`: `/.well-known/*` is now 404 (test added). Confirmed 2026-11-21 15:4x: claude.ai connected once the token was re-entered

- [x] **Flashed the HuskyLens to 0.5.1b** (2026-11-21; 1 Mbaud, because 2 Mbaud failed on macOS; no admin needed; see `hardware-log.md`)
- [x] Camera-USB link (D1-E): `husky_test.py --usb` → knock OK at 3 Mbaud, is-pro = 1 (2026-11-21)
- [x] Back on the XIAO after the flash: knock OK at 9600 with Protocol Type = Auto Detect (Serial not needed) (2026-11-21)
- [x] Gestures trained with `tools/train_ui.py` on the real camera: 4 learned IDs (2026-11-21). The first live match misread throws, see below

## Gates (§10)

- [ ] **G-pre** (Thu): V-1, V-2, V-3, V-6 pass on the test finger; servo bay and pulleys dry-fit (V-4, V-5)
- [ ] **G0** (Fri 19:30): `husky_test.py` knock passes on **both** laptops (passed on the Mac 2026-11-20; second laptop to do)
- [ ] **G1** (Sat 08:30): MCP server passes the tool sweep (T2) on real hardware
- [ ] **G2** (Sat 10:00): ≥ 85 % over 30 throws (T7); otherwise switch to MediaPipe (D15)
- [ ] **G3** (Sat 14:30): best-of-5 runs unattended 3× in a row; feature freeze

## XIAO RP2040 (firmware, §8.2, §9.2)

- [x] CircuitPython 10.3.2 on the board; `boot.py` enables the `usb_cdc` data port (2026-11-20)
- [x] Libraries installed via circup from `firmware/circuitpython/requirements.txt` (2026-11-20)
- [x] All §9.2 commands in `code.py` (`cpy-bridge 0.4`); `tools/xiao_check.py` passes everything except the servos (2026-11-20)
- [ ] Wire the PCA9685 (SDA D4, SCL D5, 3V3 logic, separate 5 V on V+) and run `tools/xiao_check.py --servos`
      (2026-11-21: SDA/SCL on D4/D5 done by Anhad; rest of the wiring drawn in [`designs/hand-breadboard.svg`](../designs/hand-breadboard.svg))
- [x] **Higher or lower drives the hand after the swap** (2026-11-21): fallback taps the number, claws, opens, wags; `machine_reference()['hand']`
      tells Claude the 2-finger shapes and idioms; stage prompt asks for the hand; `tests/test_hand_games.py` (13 scenarios, real + sim hand) green.
      Left: rehearse on the real servos (tap speed, tap visible from the audience) and check reads while the hand is in frame
- [ ] **Hand as built: index + ring/little only** (2026-11-21): confirm which PCA9685 channels they're on; tie the middle finger closed
      or fix its motor (an open middle finger makes "rock" a rude gesture); redo the moves for 2 channels; tell Claude what the hand
      can do in `machine_reference`/tool docs (no thumb, so no thumbs up or down on the hand)
- [ ] Centre all servos before the overnight split (§11.3)
- [ ] Confirm whether the relay module is active-high (the firmware assumes `on=True` → D1 high)
- [ ] Check the NeoPixels, relay click and ISD1820 PLAYL/REC on real hardware (so far only the replies have been checked)
- [ ] T4: garbage on the data port → error reply, no reset
- [ ] T19: edit `code.py` live → reload ≤ 5 s, server reconnects, G0 passes again
- [ ] Decide at G0 whether to keep CircuitPython or fall back to the Arduino bridge (D3); `firmware/arduino/` doesn't exist yet

## HuskyLens / vision (D9, §9.6)

- [x] **Throws misread in the first live match** (2026-11-21, `hardware-log.md`). Closed 2026-11-21: the fixes (115200, longer
      read window, retrain on the final rig, 30-throw test) are now steps in the pre-demo checklist
- [x] **RPS retrain 2026-11-21 (02:40–02:45) is incomplete** (scissors 0 samples; `learned_ids` is the highest ID, not a count).
      Superseded the same day: scissors trained, then the 02:58 test below; the equal-count retrain is in the pre-demo checklist
- [x] Bayesian read filter (proposed 2026-11-21): **tried offline on the 02:58 test, no gain** (leave-one-out naive Bayes on the
      per-frame confusion matrix: 21/30, same as the majority vote). The errors are whole throws read wrong on most frames
      (paper → rock 6/6), so smoothing can't fix them. Closed 2026-11-21; revisit only after the demo if training is good and reads still flicker
- [x] 30-throw test 2026-11-21 02:58: **21/30 = 70 %, fails G2**. Scissors 9/10, rock 7/10, paper 5/10; paper frames read as rock 33 %.
      Samples were uneven (rock 59, paper 58, scissors 30, nothing 30). Recorded; the retrain is in the pre-demo checklist
- [x] `tests/` write fake run logs into the real `server/data/vision/runs/` (the `train_up/down/nothing` files at 02:29–02:37).
      Checked 2026-11-21 11:10: the full suite (167 tests) writes nothing under `server/data/` (tests redirect `runlog.RUN_DIR`)
- [x] A5: camera updated from 0.3.7 to **0.5.1b** (2026-11-21)
- [x] Learn/forget/text/names verified on 0.5.1b over camera USB via the MCP tools: learn 0x36, forget 0x37 (R9 resolved); 5 learns → `learned_ids` 1; forget → 0 (2026-11-21)
- [x] First real-hardware run of `tools/train_gestures.py`: closed 2026-11-21, superseded. Its questions are answered (0x3B → u16 1;
      0x3C → OK only; learn/forget 0x36/0x37, R9) and training now goes through `train_ui.py` / the server's `/train` page
- [x] Bench pilot gesture training (IDs 1–4 = rock/paper/scissors/nothing): done 2026-11-21 (4 learned IDs, reads unreliable). Retrain on the final rig → pre-demo checklist
- [ ] T7: 30 throws, mixed players, ≥ 85 % (the pre-demo RPS retrain runs it)
- [x] Gesture mode reports `learned_ids` 44 (2026-11-21). Explained the same day: after a `husky_load_model` the INFO field holds the
      **sample count**, not an ID, and slot 2 (up/down/nothing) was trained 15 + 15 + 14 = 44. Nothing was trained at ID 44
- [ ] `husky_measure_fps` on Friday, which decides whether rigged-win is viable (D10, R7)
      (2026-11-20 via XIAO @ 9600, nothing learned: 18.8 req/s, 54 ms median. 2026-11-21, 4 classes learned: 19.1 req/s, 50.5 ms median,
      **camera ~9.8 fps** in classification. Recheck at 115200)
- [ ] MediaPipe fallback ready in case G2 fails (D15). **Not before the demo** (2026-11-21): `MediaPipeVision` is a stub and the
      demo's fallbacks are a retrain and the SD-slot backups. Post-demo

## Descoped: faces (not in the demo)

_Descoped 2026-11-21 (user decision, confirmed again at the pre-demo close-off): the 2-minute demo never scans faces, the ~45 min
of work would use all the slack before the G3 freeze, and every scan switches the camera off gestures. The code stays (`identify`,
`register_player`, RPS `vars.identify`), just unused in the demo. If a judge wants to play: `set_player_mode('stranger', 'rigged_win')`
in chat, no camera switch. Nothing below is worked on before the demo; the items are kept as a record for afterwards._

- **Pre-train the two team faces** (Face Recognition IDs 1 and 2) before the demo, then save to an SD slot (`husky_save_model`). No registration at the table (D10, decided 2026-11-21)
- Check that gesture and face models both survive algorithm switches (each algorithm keeps its own learned data?), since setup switches face → classification
- Stranger flow: unknown face (ID 0) or no face → **rigged-win, the robot always beats them** (confirmed by the user 2026-11-21); the two known faces → fair (confirm with Anhad)
- T8 (revised): both known faces identified ≥ 9/10 each; a third person reads as a stranger ≥ 9/10.
      2026-11-21: **shiggy = face ID 1, 10/10** via `identify_player` (seated still, not stepping in and out; raw 82/82).
      Anhad (ID 2) and the stranger test still to do
- Face learn accepts samples with **no face in frame**: the first `register_player` got 5/5 "accepted" while the camera
      detected nothing (count 0), then every scan read stranger. Have `register_player` check a face is in view first (and
      check after learning that it reads back as the new ID). Face position matters: the camera is aimed for throws
- `husky_forget` in face mode returned not-ok (2026-11-21), so stale faces up to ID 5 from earlier tests are still on
      the camera (only ID 1 was relearned). Wipe them before the demo, or a teammate may read as a stale ID
- Confirm the QR algorithm id (7?) and that the bribe card is learned as ID `settings.bribe_card_id` (default 1) in QR recognition; the check fails soft until then
- Pre-train faces 1 and 2 with `register_player` (prep), then set their modes (`set_player_mode`); strangers default to rigged_win (`settings.stranger_mode`, per the user's decision above)

## Software (§9.1, §9.3)

- [x] `server/huskylens.py`: protocol library
- [x] `server/serial_link.py`: direct USB-TTL transport
- [x] `server/bridge.py`: XIAO JSON-lines client (`Bridge`, `find_bridge_port`)
- [x] `tools/husky_test.py`: knock smoke test; `tools/xiao_check.py`: firmware check; `tools/train_gestures.py`
- [x] `tools/train_ui.py`: local web UI for gesture training (train per class, live readout, 30-throw test, save/load), with `--fake` for no hardware. Tested in simulated mode only (2026-11-20). Simplified to a single minimal column (live readout → 1 Train → 2 Test, model/settings/log folded away), errors now stay visible, Stop button fixed (2026-11-20). Used on the real camera through the XIAO to train 4 classes (2026-11-21); its 30-throw test hasn't been run on hardware yet
- [x] XIAO onboard-LED cues for training (`status_led`, an extension beyond §9.2, in `cpy-bridge 0.5`) (2026-11-20)
- [x] Per-run training logs (`tools/runlog.py`): one JSON-lines file per UI job / CLI run in `server/data/vision/runs/`, with every camera call's raw tx/rx frames and status, messages, LED cues and outcome (2026-11-20). Tested with the simulated camera and a fake 0.3.7 transport; not yet on real hardware
- [x] `train_ui.py` requirements pass (2026-11-21): FR-07 classes in the test table (ID 4 `nothing` no longer lands in "other"),
      player tag per throw + per-player accuracy (T7), live learned-ID count and reads/s (D10/R7), settings range-checked
      (0 throws no longer crashes), POSTs only from the local page. Tested on `--fake` only
- [x] Screen drafts in `designs/mcp-live-view/` (not in the design doc), one shared theme and header, in order of use:
      Train (D9/T7/G2), Operate (your Mac), Stage (audience, never shows the mode), Trace (how it works). All on a mock
      session (`feed.js`) built from §9.3/§9.4/D10/D20; `train_ui.py` uses the same colours and header (2026-11-21)
- [x] Screens plan (closed 2026-11-21: the live game view shipped; its open decisions are in `demo-plan.md` → Open decisions): [`designs/mcp-live-view/PLAN.md`](../designs/mcp-live-view/PLAN.md) (visual: `plan.html`), updated 2026-11-21 to
      the agreed shape (two play modes, `server.py` hosts the view, Claude Desktop the MCP host, Train separate, 2-min demo).
      Open: its decisions (put the screens in the design doc? which laptop runs the demo? venue display?)
- [x] `FakeBridge` in `bridge.py` (NFR-08): all §9.2 commands with the firmware's checks, `FakeCamera` behind it (pretrained: gestures 1–4, faces 1–3); `open_link('fake')` returns it (2026-11-21). **Commit it before the overnight split** (§11.3)
- [ ] Post-demo: T1: protocol unit tests (knock `55 AA 11 00 2C 3C`, example block)
- [ ] Post-demo: T3: FakeBridge parity, with the same suite against the fake and the real bridge
- [x] `server/hardware.py`: servo/pose/LED/relay/voice plus calibration, soft limits, 5 s hold watchdog (2026-11-21; exercised through the game tests on `FakeBridge`, not on real servos)
- [x] `server/vision.py`: `VisionSource` with `HuskyVision` (2026-11-21, fake camera only); `MediaPipeVision` is a stub that raises `VisionError` until D15 is built (still open)
- [x] `server/game.py`: `Store` + `Game`: moves/rules/players/sequences CRUD, all modes (fair, predict, rigged_win, rigged_lose/mercy, grudge, bribery, bluff, sore_loser), §9.4 round with replays, matches, hooks, scoring (2026-11-21; simulated only, 49 tests pass)
- [x] Fair-mode throw no longer lands visibly ahead of the human's (2026-11-21): new setting `throw_ms` (default 300)
      ramps the robot's move at "shoot" instead of `pose_ms` (120). The pose is still sent at shoot (FR-09, T10 unchanged),
      rigged modes still hold the fist. 184 tests pass; **tune `throw_ms` on the real servos at the rehearsal**
- [x] Train page step **3 · Save to card** (2026-11-21): visible save that picks the demo slot from the gesture set
      (RPS → 1, up/down → 2, others default to 3) and asks first if the test hasn't passed 85 % or the slot is wrong; shows
      the last save result. Save left the collapsed section (Load stays there). 185 tests pass; tried on the simulated camera
- [x] Train page shows **on camera** instead of "not trained" when a model was loaded from the SD card (2026-11-21): the
      per-gesture counts are only what the page trained this session; the camera doesn't report them
- [x] Train page no longer blames the firmware for any missed sample (2026-11-21): it lists the real failures (e.g. `NO_REPLY ×1`)
      and only suggests flashing on `UNSUPPORTED`
- [x] `/view` has a **Train ↗** link (browser only, hidden in the chat panel) (2026-11-21). The view cookie now also opens
      `/train` (user decision 2026-11-21: only `/mcp` stays key-only), cookie path `/`. 186 tests pass; checked on the simulated camera
- [x] Live view **landing screen** and a flashier game screen (2026-11-21): when no game is on (none yet, or the last
      was stopped) the view shows an animated RPS-Bot landing (the hand pumps and reveals each gesture the camera knows, an
      "I know" chip row, a "Let's play rock paper scissors" prompt). It says nothing about Claude or new games, so the twist
      stays hidden. The game screen gets the ambient backdrop, shimmer title, numbers that pop on change, lit gestures that
      pulse and a hot timer. The styling is per block kind, so games Claude writes get it too. The view polls now carry
      `gesture_set`. 186 tests pass; checked on the fake camera (landing, running game, Nerd)
- [x] Live view redraws patch instead of replacing (2026-11-21): the layout is built once per mode and each Nerd section
      (graph, now, robot, vars, events) and the game screen swap only when their own HTML changes, so Nerd no longer flickers or
      jumps back to the top. Checked on the fake camera: scroll held through 5 state changes; 186 tests pass
- [x] `server/banter.py`: 60-line built-in bank, `data/banter.json` loader, no repeat in 10; `Banter.set_model()` is the empty seam for the GGUF model (D16-C, not built) (2026-11-21)
- [x] `server/connect.py` + `UsbLink` in `serial_link.py`: camera micro-USB link (D1-E, from HUSKYLENSPython) and
      auto-detect (XIAO first, then camera USB), used by `husky_test.py`, `train_gestures.py`, `train_ui.py` and the MCP server.
      Ports now open with an exclusive lock (2026-11-20). Probe/fallback tested on a pty; auto→bridge knock passed on real
      hardware; **camera USB not yet tested on hardware** (needs fw 0.5.1)
- [x] `server/server.py` first slice (mcp 2.3 `MCPServer`): all §9.3 HuskyLens tools + `husky_connect`, `husky_status`,
      `train_gesture`, `read_gesture`; `--fake` uses `FakeCamera` (now behind `FakeBridge`). Tested in-process on the fake (2026-11-20)
- [x] MCP server through the XIAO on real hardware: auto picks the XIAO, stdio launch OK, knock/status/read/fps work,
      `train_gesture` correctly reports `UNSUPPORTED` on fw 0.3.7 (2026-11-20; see `hardware-log.md`)
- [x] **Remote MCP over HTTP** (2026-11-21, [`remote-mcp.md`](remote-mcp.md)): `server.py --http [HOST:PORT]` serves streamable HTTP at
      `/mcp` (default 127.0.0.1:8770) behind a token (`RPSBOT_TOKEN` in the gitignored repo-root `.env`, which `server.py` now loads at start-up; or `--token`; sent as a Bearer header or `?key=`). DNS-rebinding protection is
      off so tunnel hostnames work; the token does that job. Tested on `--fake` locally and through a `cloudflared` quick tunnel (401
      without the token; a full session with both token forms). 116 tests pass
- [x] **claude.ai custom connector through a Cloudflare quick tunnel, real camera** (2026-11-21): a friend added the connector on their
      own account (No sign-in + `authorization: Bearer <token>` request header, URL ending `/mcp`) and drove the robot's MCP tools
      remotely; confirmed working by the user. Test script and security notes in `remote-mcp.md` (Tested, Security)
- [x] Named Cloudflare Tunnel for a stable URL (`remote-mcp.md` §2b): **not doing it** (2026-11-21). No domain on the account; the demo
      uses a quick tunnel left running all day, with the local `mcp-remote` connection as the fallback
- [ ] Post-demo: remote-access hardening, if it goes beyond supervised tests: Cloudflare Access + OAuth, a read-only tool profile for
      spectators, per-person tokens (`remote-mcp.md` → Security)
- [x] Register the MCP server with Claude and run a training session through it after the 0.5.1 flash: done 2026-11-21 (claude.ai
      connector over the tunnel; up/down/nothing trained through `train_gesture` and `/train`). Claude Desktop on the robot laptop is the
      handover rehearsal in the pre-demo checklist
- [x] `server/server.py`: the rest of §9.3 (hardware, moves, sequences, rules, players, play, score, vision source, banter) (2026-11-21; 52 tools, tool functions tested directly on `--fake`, not over stdio and not on hardware). `--fake` uses `server/data/fake/`. Laptop speech is **opt-in** in every mode (`--speak` or `RPSBOT_SPEAK=1`), after it spoke by surprise in a real test (2026-11-21)
- [x] `server/data/`: moves, rules, players, sequences, settings, history.jsonl created on first run (calibration.json by the hardware layer; `banter.json` is optional) (2026-11-21)
- [ ] Post-demo: `CLAUDE.md` (§9.1): or point it at `AGENTS.md` (listed under design-doc issues)
- [ ] Post-demo: `docs/CONNECT_TEST.md` (§9.1); `demo-runbook.md` covers the connection checks until then
- [x] T10 fairness (300 rounds, 33 % +/- 5), T11 banter (no repeat in 10), T12 persistence, T18 sequence validation, T9 rigged-win on the simulator (reveal lag ~0 ms; the real lag depends on the camera), NFR-09 relax: `tests/test_game.py`, `.venv/bin/python -m unittest discover tests` (2026-11-21). **Still to run on real hardware:** T9 (lag <= 250 ms), T10 on the real hand, T17, T18 from chat
- [x] Game on the real camera with a simulated hand (`--hand sim`, auto-detected when the XIAO has no PCA9685): connect, read,
      fair round OK, 665 ms after shoot (2026-11-21; see `hardware-log.md`)
- [x] First live best-of-3 with a person throwing (fair, simulated hand): robot won 2–1; every round 733–849 ms after shoot
      (NFR-01 ✓), but **the user says the reads were wrong**. Fixes under HuskyLens / vision (2026-11-21)
- [ ] Hardware gameplay (not needed for the software-mode demo): once the PCA9685 is wired, `--hand auto` should pick `real`; calibrate fingers first (`calibrate_finger`)
- [x] **Two modes, one game engine and one set of MCP tools** (agreed and built 2026-11-21; not in the design doc):
      **Hardware RPS** = `server.py --hand real` (printed hand, LEDs, relay, ISD1820). **Software RPS** = `server.py --hand sim`
      (real HuskyLens reads you; a simulated XIAO plays the robot; `--fake` also simulates the camera). `--hand auto` picks
      by asking the XIAO for the PCA9685. Claude Desktop stays the MCP host in both
- [x] **Software RPS view**: closed 2026-11-21, superseded by the live game view (`show_game` / `/view`) further down, which draws
      any machine including RPS. Original plan, for the record: the "virtual robot" in a browser: a small HTTP + SSE server inside `server.py` on 127.0.0.1:8770,
      fed by `game.listeners` (round steps) and the simulated XIAO's pose/LED changes; a page based on the Stage draft
      (`designs/mcp-live-view/stage.html`) with an animated hand, LED ring, countdown, robot vs you, score and banter.
      It never shows the player's mode. Operate/Trace later, same feed
- [ ] Hardware mode only (not the demo): run a best-of-5 through Claude on the rig; check `returned_ms_after_shoot` <= 1500 (NFR-01) and the 5 s hold on real servos
- [x] **Deterministic RPS fallback committed** and tagged `rps-deterministic` (2026-11-21): the working `game.py` engine and the
      full MCP server, before the state-machine rewrite. To get it back: `git checkout rps-deterministic`
- [x] **State-machine runtime** (2026-11-21; direction agreed the same day, not in the design doc): the MCP runs a state machine
      (states, `gesture:<name>` / `timer` / `enter` / `poke` events, guarded transitions with safe JSON conditions, 19 actions)
      on its own thread and Claude edits it live. `server/machine.py` (language, validation, atomic patch ops, store),
      `machine_runtime.py` (the loop), `rps_lib.py` (pure RPS logic **moved** out of `game.py`, which now delegates to it),
      `rps_functions.py` (the `rps.*` function library), `server/machines/higher_lower.json` and `rps.json` (built-ins; now `server/fallback_machines/`). Tools:
      `define_machine`, `patch_machine`, `get_machine`, `list_machines`, `delete_machine`, `start_machine`, `stop_machine`,
      `machine_state`, `poke`, `machine_reference`. RPS runs as a machine: every behaviour test in `tests/test_game.py`
      has a machine twin in `tests/test_rps_machine.py` (26 tests; reveal lag 0-1 ms and round ready 25-27 ms after shoot on the
      simulator; engine and machine pick identical moves on the same seeds). 114 tests pass. Simulated only
- [x] **Machine runtime holes fixed** (2026-11-21): `stop_machine` during a `wait`/`countdown`/`play_sequence` used to block 3 s and
      leave the old run alive beside the next machine (it then spoke and drove the hand). Stops now cut those short; each run has its own
      stop flag, so a run stuck in a camera read can't touch the next one, and the hand relaxes at once. Timers floor at 50 ms (a 0 ms
      self-loop ran ~100k entries/s); `wait` from an expression is checked when it runs. 6 tests in `StopAndTimingTest`; 167 pass
- [x] **Built-in machines relabelled as fallbacks** (2026-11-21): `server/machines/` → `server/fallback_machines/`, `list_machines`
      reports `source: "fallback"`, each description starts "Fallback game", and the server instructions tell Claude to write its own game
      when asked for a new one and use a fallback only when that fails. Machine names (`rps`, `higher_lower`) unchanged
- [x] **Saved gesture profiles** (planned 2026-11-21, **closed: no automatic load**, principle 2; [`gesture-profiles-plan.md`](gesture-profiles-plan.md)): save each gesture set's
      trained model to its own HuskyLens SD slot (rps → 1, higher_lower → 2) plus its ID map in `<data>/gesture_profiles.json`, so
      `start_machine` loads the right model instead of wiping and retraining. First check on the camera that SD save/load works over the XIAO.
      Still on hold: principle 2 now allows saved models as **manual backups** (slot 1 RPS, slot 2 up/down/nothing, 2026-11-21), but not an
      automatic load on swap, which would replace the live teach. The manual backup steps are under "Saved backups on the SD card" further down
- [x] **`--http` opens the live view by itself** (2026-11-21): `/view?key=` in the default browser once the server listens
      (`--no-open` / `RPSBOT_OPEN=0` to skip). The page drops the key from its address bar on load (projector), and the keyed
      `/view` load sets an HttpOnly cookie accepted on `/view` paths only, so reloads work; the cookie gets 401 on `/mcp` and
      `/train`. Checked in the preview browser on the fake server
- [x] **Machines aren't tied to gestures** (2026-11-21): `server/camera_modes.py` + `camera` on a machine, `see:<label>` events in
      any detection mode, per-mode labels, `mode` on `train_gesture`/`read_gesture`, live view shows labels like gestures.
      `read_gesture`/the machine reader now switch the camera back to classification if another mode left it elsewhere (they
      used to read whatever mode was last set). 17 tests in `tests/test_camera_modes.py`, fake camera only
- [ ] Confirm on the real camera: QR/barcode are algorithms 7/8, `learn(id)` over serial learns a centred QR code/tag/colour,
      and `see:` latency in a detection mode (one `husky_test.py`-style check per mode used)
- [x] Documented the demo plan as the demo's source of truth (`docs/demo-plan.md`, linked from `AGENTS.md`) and the state-machine
      server (`docs/state-machine.md`) (2026-11-21)
- [x] Moved to the pre-demo checklist (2026-11-21). Was: hit the demo targets in `demo-plan.md`: swap + setup ≤ 35 s, up/down/nothing ≥ 85 %, a thumb registers in ≤ 1 s, Claude's own
      machine validates first time 3/3, the full two minutes 3× in a row
- [x] **Dry run of the two minutes, simulated camera, Sonnet playing Claude Desktop** (2026-11-21): every beat passed
      (RPS best-of-3 machine 10.5 s, play_match fallback, swap, train up/down/nothing 17.3 s, 4 guesses with a roast, live
      patch_machine, stop, fallback ladder). Sonnet's own machine failed validation once: the reference listed `$vars` in
      scope, so it wrote `$vars.score`. Fixed: the reference says vars are `$name` with no prefix, and the error now hints it.
      Also fixed: speak/show/emit now appear in `machine_state` events (`"output": true`), so Claude can see the roast; and
      demo-plan.md's fallback rungs 1 and 3 corrected (swap doesn't start; play_match refuses after the thumbs swap)
- [x] **3 fresh Sonnet attempts at the rehearsed prompt** (2026-11-21, simulated camera, after the `$vars` fix): 3/3 machines
      validated first time and played right (scored, ended on a wrong guess, roasted, 1–10 never equal). Their guesses
      written into `machine_reference`: `avoid` is an expression, `show` takes `{var}` and is cut to 20 chars after filling in,
      `nothing` is implied, `final` states need no `on`, `enter` fires on every entry. Not yet the demo target: that's 3/3 in
      Claude Desktop with whatever model it runs
- [x] Moved to the pre-demo checklist (2026-11-21). Was: **next for the machines:** run `higher_lower` and `rps` on the real camera through Claude Desktop (tune `gesture_window_s` /
      `gesture_stable`; the defaults 0.4 s x 2 windows add ~0.8 s per thumb); measure the real reveal lag in the rps machine (T9)
- [ ] Post-demo: decide whether `play_round` / `play_match` become thin wrappers over `start_machine('rps')` (their tests pass against the machine,
      so it is safe) and then delete the duplicated `_round` in `game.py`. Kept both for now: the engine is the tagged fallback
- [x] **Face ID for any machine** (2026-11-21): built-in `identify` action (`{"identify": {"into": "who"}}`) returns
      only who's there (`name`, `known`, `face_id`, `spectators`, ...) from the shared player list; the game decides what it means
      (RPS: `rps.seat_player`, stranger = rigged-win). `rps.json` has an optional `setup` state (`vars.identify`, `check_bribe`) =
      §9.4's Setup. `identify_player` / `register_player` now refuse while a machine runs (D10: no algorithm switch mid-game). 116 tests pass
- [ ] Post-demo: per-player data that outlives a machine run (e.g. a swipe game's likes, a higher-or-lower best score): a built-in
      `remember`-style action keyed by player name. Machine vars are lost when the machine ends
- [ ] Post-demo: a running machine is not restored after a server restart (definitions are saved in `<data>/machines/`, the state and vars are not)
- [x] **Live game view** (2026-11-21, `server/game_view.py`, `server/screen.py`, `server/views/game.html`): one page for ANY machine,
      as an MCP App in the chat (`show_game`) and over HTTP for a projector (`/view` on `--http`, or `--view` with stdio). A Game
      tab (player-facing, drawn from the machine's `view.screen` or a state's `screen`, resolved on the server) and a Nerd tab
      (state graph, timer, vars, events). Machine format gains `view` {title, hide, headline, colors, screen} and a per-state
      `screen`; `set_view` patch op. Fallbacks hide their secrets (RPS: mode, choice, commit, rd). `tools/view_demo.py` runs a machine on the
      fake camera with the view. Tested over HTTP and the tool calls with an invented game (152 tests pass); **not yet seen in Claude Desktop**
- [x] `/view` is a backup, not the projector (2026-11-21, user decision): the chat panel (`show_game`) is what the audience
      sees; `/view` stays open in a tab behind as the fallback. `demo-cues.html`, `demo-runbook.md` and `demo-plan.md` say so
- [x] Nerd is a toggle, not a tab (2026-11-21, user ask): the header switch (or `N`, or `#nerd`) puts the state graph, vars and
      events beside the game, so both are on one screen; stacked below the game when narrow and in the chat panel. Checked in a
      browser on the fake camera (1440 wide split, 800 wide stacked); **not yet seen in Claude Desktop**
- [ ] (Pre-demo checklist) Open `show_game` in Claude Desktop (stdio and remote) and check the panel draws, polls and resizes; fall back to `/view` if it doesn't
- [x] RPS goes through the machine (2026-11-21): "let's play rps" ran `play_round`, which the view can't see. The MCP
      instructions and `play_round`/`play_match` docs now steer Claude to `start_machine('rps')`; the old engine is the fallback
- [x] **Train page inside the server** (2026-11-21, user decision; reverses "Train separate"): `server.py` serves `tools/train_ui.py`'s
      page at `/train?key=<token>` on `--http` (and `/train` on the stdio `--view` side server, localhost POSTs only), sharing the
      server's camera link, so retraining no longer means stopping the server. Classes follow the current gesture set (rps or
      thumbs); training pauses while a machine runs; `start_machine`/`train_gesture` refuse while a page job runs; it only polls the
      camera while the page is open. `train_ui.py` still runs on its own. Tested on the fake camera (`tests/test_train_shared.py`)
      and in a browser; **not yet on the real camera**
- [x] Train panel in the chat (2026-11-21): `show_train` opens the Train page as an MCP App (`ui://rps-bot/train`), polling
      `train_state` and pressing buttons through `train_action` (both app-only). The MCP instructions open `show_game` and `show_train`
      when the user starts a game. Forget asks with a second click instead of `confirm()` (panels are sandboxed). Tested with a
      stand-in host in the browser and in `tests/test_train_shared.py` (160 pass); **not yet seen in Claude Desktop / claude.ai**
- [x] Port the Operate/Stage/Train drafts onto the live view, or retire them: **retired for the demo** (2026-11-21). The Game tab covers
      Stage for any game, the Nerd tab covers Trace, and `/train` is Train. The drafts stay in `designs/mcp-live-view/` for later
- [ ] Post-demo: machine limits to revisit once Claude has written a few: one machine at a time; `to` targets are static; no sub-machines; gesture
      events only for confident reads of the current gesture set (`none` / `unknown` never fire); the countdown is one blocking action
      (absolute-time beats, so no drift) rather than a state per beat
- [x] **Free play: any gesture game over MCP** (2026-11-21), **replaced by state machines the same day**: `server/freeplay.py` and the
      tools `new_game`, `game_turn`, `update_game`, `current_game`, `end_game` (and `tests/test_gestures.py`'s free-play test) are gone.
      Higher or lower is now the fallback machine `higher_lower`. Kept: `gesture_set`, `random_number`, `train_gesture`,
      `read_gesture` (with `confidence`/`confident`) and `server/gestures.py`. The gesture swap (wipe the camera and name a new set)
      is `define_machine(..., swap_gestures=true)` / `start_machine(..., swap_gestures=true)` for any machine that declares `gestures`;
      `play_round`/`play_match` still refuse while another gesture set is loaded
- [ ] **Saved backups on the SD card** (user decision 2026-11-21; `demo-plan.md` principle 2 and "Saved models" updated): RPS → slot 1,
      up/down/nothing → slot 2 (backup for the live teach only). Steps: (a) SD card in; save → forget → load → read check, and time the load;
      (b) set 115200 baud and `read_window_s` ~0.5 first; (c) train and test both sets on the final rig to ≥ 85 %, save; (d) copy the card's files to the laptop
      - [x] Card in and working (2026-11-21): the microSD slot is on the board edge, hidden by the silicone case. Save + load to slot 9 over the XIAO
        at 9600 baud: both OK, no SD error on screen, save 317-1182 ms, load ~1.15 s. Restore proven (2026-11-21): saved the current RPS model to slot 1 (slot 9 has an earlier copy),
        forget → learned 0, load slot 1 (1241 ms) → learned 60 again, same as before. But the model saved was weak: re-run read check
        rock 15 %, paper 57 %, scissors 65 %, nothing 0 %. Retrain RPS (rock vs scissors is the hard pair), then re-save slot 1.
        Note: after a load the INFO `learned_ids` field reads as the sample count (60 = 4 × 15, 45 = 3 × 15), not the ID count.
        A 3-gesture test model (rock/paper/scissors, no nothing) is in slot 5; the web trainer only allows slots 1-5
      - [x] Up/down/nothing saved to slot 2 (2026-11-21), trained on the server's /train page (15/15/14 samples). Its 30-throw test: 20/30 = 67 %
        (up and down confused only 2×; 8 throws read as nothing, likely late hands or hand in the `nothing` samples). User accepted it as is for now
- [x] **Fixed 2026-11-21:** `Camera.live()` knocks an open link and reconnects once; used by `swap_gestures` and `train_gesture`. Test `test_swap_reconnects_a_dropped_link` (fails without the fix); 161 tests pass. Running servers need a restart to pick it up. **Was: the gesture swap didn't reconnect a dropped camera link** (found 2026-11-21): `swap_gestures()` in `server.py` uses
      `cam.get()` directly, so after another tool had the XIAO port, `define_machine(..., swap_gestures=true)` failed with
      `SerialException: Device not configured` until a `husky_knock` (which goes through `cam.call`'s reconnect) fixed it. Same path as the live demo swap
- [ ] Every run (`demo-plan.md` → Before every run): the demo swap wipes the RPS training: restore it with `start_machine('rps', swap_gestures=true)` + `husky_load_model(1)` before every demo run (retrain with `train_ui.py` only if slot 1 is missing or reads badly)
- [x] Moved to the pre-demo checklist ("Up/down/nothing retrain → slot 2") (2026-11-21). First pass 20/30 = 67 %
- [x] Moved to the pre-demo checklist (2026-11-21). Was: time the on-stage swap (Claude's `define_machine(..., swap_gestures=true)` + 3 trainings + 2 test reads); target ≤ 35 s (`demo-plan.md`)
- [ ] Post-demo: banter bank format: `banter.py` reads `<data_dir>/banter.json` as `[{text, when}]`, but `docs/banter-training-plan.md` Step 2 writes `server/data/banter/bank.json` keyed by `(event, mode, outcome, …)`. Align the loader when the generator is built

- [x] **RPS is only the static back-up game** (user, 2026-11-21): server instructions and `gestures.py` now say so; new games, gestures, moves and rules are MCP data (`define_machine`, `define_move`, `define_rule`, …), never code or firmware pushes to the XIAO/camera. Nothing RPS-specific lives in `firmware/circuitpython/code.py`
- [ ] Post-demo (design-doc owner): D9/§9.3 present RPS as *the* game; reword it as the default back-up with chat-defined games on top (with the live-swap item above)

## Hardware build (§7, §8; mostly Anhad)

- [ ] Print the test finger and palm section; run V-1/V-2 force, V-3 travel, V-6 cord fit
- [ ] Servo bay CAD (V-4) and MG90S pulley CAD (V-5); print them; dry-fit
- [ ] Print the full hand (H-3); hand onto the bay, tendons, thumb lock (V-7)
- [ ] Cut and assemble the frame (2020 stub, 350 mm camera post); mount the camera outside the hand's view cone
- [ ] Solder the perfboard (§8.4): fuses F1–F4, 1000 µF caps, 330 Ω NeoPixel resistor, star ground
- [ ] T14 electrical inspection **before first power-on**
- [ ] Calibrate the fingers; T5 pose distinctness; T6 pose speed ≤ 400 ms; T17 hold time
- [ ] Lighting, audio (ISD1820 recordings), backdrop
- [ ] T13: 30 min soak with no brown-out

## Shopping / BOM (§11.2, §15)

- [ ] micro-USB data cable
- [ ] PH2.0-4P to Dupont lead or PCB header (depends on the Gravity cable's far end, open item 6)
- [ ] 3 mm shock cord (backup: beading elastic from Spotlight)
- [ ] Perfboard, female headers, screw terminals, Dupont headers
- [ ] Inline blade fuse holders plus 1/2/3 A fuses
- [ ] 5 V ≥ 5 A supply (D14; or ATX if one is available)
- [ ] Foam board backdrop
- [ ] Check stock: 1000 µF caps, 330 Ω resistor, 74AHCT125, M3/M5 hardware, 2020 lengths, PLA, MG90S count, braided line

## Open items and TBCs (§2, §16)

- [ ] A1/A2: kickoff and demo times; when each person leaves and returns
- [ ] A3: whether pre-printing is allowed; makerspace hours and overnight printing policy
- [ ] D14: main power source
- [ ] Whether the teammate has a USB-TTL adapter (decides who keeps the XIAO overnight, §11.3)
- [ ] The far-end connector type on the HuskyLens Gravity cable (§8.1)

## Design-doc issues to resolve

- [ ] Gantt puts "Banter bank + LoRA stretch" at Sat 12:00–14:30, but R11 says stretch items only run after G3 (14:30)
- [ ] Check Anthropic's terms on training a model on Claude outputs before any LoRA fine-tune (D16)
- [ ] Check that a prebuilt CPU wheel of llama-cpp-python installs on Windows without admin rights (A6)
- [ ] If gesture profiles land: §9.3/D9 say nothing about one camera holding several gesture sets via SD slots (see `gesture-profiles-plan.md`)
- [ ] **Remote MCP** (2026-11-21): D4/§9.3 assume a local stdio server; `server.py --http` + a Cloudflare Tunnel lets a Claude on
      another machine drive the robot (`remote-mcp.md`). The doc has no network transport, auth or tunnel
- [ ] §9.1 lists `CLAUDE.md`; the repo now uses `AGENTS.md`
- [ ] **The hand has no thumb** (2026-11-21, `hardware-log.md`): only index and ring+little have working motors; the middle motor is
      dead-ish and not wired. §7/D-thumb, CH0 thumb (§8.2), V-7 and the rock/paper/scissors poses assume four curling channels
- [ ] Note the firmware's §9.2 interpretations in the doc: servo `ch` 0–4, `pose` takes exactly 4 values,
      µs clamped to 500–2500, `led`/`countdown` argument shapes, `voice` takes `play_ms` or `rec_ms` (not both), relay polarity
- [ ] Doc: make HuskyLens firmware ≥ 0.5.1 an explicit requirement (A5, HLO-1), add a "flash the camera" step before G0, record that R9 happened
- [ ] Add the non-§9.2 firmware extras to the doc: `uart` (set HuskyLens baud) and `status_led` (onboard RGB LED: `rgb`, `blink_hz`, `brightness`)
- [ ] **Two play modes** (agreed 2026-11-21): Hardware RPS (`--hand real`) and Software RPS (`--hand sim`, a virtual robot view), `--hand auto` detection. Not in the doc
- [ ] **Demo is 2 minutes** (new constraint, 2026-11-21): HLO-5 (a new move via chat in ≤ 5 min) is verified beforehand, not shown live. A1 still TBC
- [ ] **Camera modes for machines** (2026-11-21, built): a machine's `camera` picks any HuskyLens block algorithm (classification,
      qr_code, barcode, tag, color, object, tracking, face) and fires `see:<label>`; `train_gesture`/`read_gesture` take `mode`,
      `swap_gestures` sets up whichever mode, per-mode labels in `<data>/labels_<mode>.json`. Not in the doc: §9.3 tool signatures,
      §9.1 `server/camera_modes.py`, and D9 (classification is now the default input, not the only one). Fits D10's "switch only
      during setup": a machine reads one mode for the whole run; mid-game switching was left out
- [ ] **D11 rejected a scripting DSL as over-scoped** (option C); the state-machine language is one. Record in D11 that C was adopted
      2026-11-21 because the demo depends on it (the author to accept)
- [ ] **State-machine MCP** (2026-11-21, built): the server is a general state-machine runtime that Claude edits; RPS (§9.4's round
      state machine, D10 modes) is one machine definition (`server/fallback_machines/rps.json`). A core architecture change to D4/§9.3/§9.4:
      §9.3 needs `define_machine`, `patch_machine`, `get_machine`, `list_machines`, `delete_machine`, `start_machine`, `stop_machine`,
      `machine_state`, `poke`, `machine_reference`; §9.1 needs `server/machine.py`, `machine_runtime.py`, `rps_lib.py`,
      `rps_functions.py`, `server/fallback_machines/`, `<data>/machines/`. §9.4's round is now data (`rps.json`): the countdown is one
      `countdown` action (hand pumps on absolute beat times), the match is outer states, and the post-round hold/hooks run in
      the machine (so a machine round finishes after the hold, while `play_round` still returns at the report). A machine
      keeps game settings under `$cfg` (the same `settings.json`)
- [ ] **Live game swap** (2026-11-21): the robot plays any gesture game described in chat, not only RPS (the doc's
      scope is RPS; FR-11/12 only add moves and rules within RPS). D9 fixes classification IDs 1–4 to rock/paper/scissors/nothing,
      but a machine's `gestures` rename them (`swap_gestures`). §9.3 lacks `gesture_set`, `random_number`; §9.1 lacks
      `server/gestures.py`, `tools/gesture_check.py`. (The earlier `new_game`/`game_turn`/... free-play tools were removed the same day)
- [ ] §9.4's `Setup` state is now optional in the RPS machine (`vars.identify`), and face scanning is a generic machine
      action (`identify`) rather than RPS-only; the MCP face tools refuse while a machine runs
- [ ] **Live game view** (2026-11-21, built): §9.3 lacks `show_game`/`game_view_state`, the `ui://` MCP App resource and the `/view`
      HTTP routes (`--view`); the machine format gains `view` and a per-state `screen`. The doc has no screens at all (see the screens plan)
- [ ] **Train page inside the server** (2026-11-21, user decision): `/train`, `show_train`, `train_state`/`train_action` share the
      server's camera link; the doc (and the earlier "Train separate" agreement) has training as a separate script
- [ ] **Faces descoped from the demo** (2026-11-21): D10/FR-13/§9.4 have a face-scan setup before play; the demo skips it (faces are a stretch goal)
- [ ] Strangers → rigged-win and "no registration at the table" (user decisions, 2026-11-21) refine D10/FR-13; the doc says fair is the default
- [ ] **Game-engine interpretations** (2026-11-21, `server/game.py`), for the user to accept or change:
      `{"repeat": n}` = play the steps since the start or the previous repeat n times in total; steps < 60 ms clamped up; extra `{"relay": bool}` step;
      grudge turns rigged-win from the **next round** after a human win; bribery = the mode, or the QR card in `identify_player(check_bribe=True)`, lasting the match;
      `delete_move` may delete the classic three (and their rules); the fair pool = moves in at least one rule; a rigged round with no counter plays a draw;
      the hand relaxes ~1.5 s after the result and hooks run after that (NFR-09); unknown and nothing reads both trigger a replay (max 2);
      `voice_countdown` off until the ISD1820 clip is recorded; bluff announces at the start of the countdown; sore loser demands best of 3, then 5
