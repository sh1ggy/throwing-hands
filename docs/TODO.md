# RPS-Bot: living TODO

What still needs to happen, in order of when it blocks things. The plan and the "why" live in
[`rps-robot-design-doc.md`](rps-robot-design-doc.md) (the source of truth); this file only tracks status.
Section refs (§, D, T, V, G, A) point into the design doc.

**Keeping it current:** tick items as they land (add the date and a one-line result), add new work as it
turns up. Agents: update this file in the same change as the work. **Don't edit the design doc:** the user makes
those changes. List proposed doc changes under "Design-doc issues to resolve" instead.

_Last updated: 2026-11-21 (remote MCP over HTTP + Cloudflare Tunnel; saved SD-card models allowed as backups)_

**Demo target:** [`demo-plan.md`](demo-plan.md) (the two-minute run, its targets and fallbacks). **How the server works:**
[`state-machine.md`](state-machine.md).

---

## Now / blockers

- [x] HuskyLens answers through the XIAO: knock OK, `request()` returns INFO (2026-11-20; see `hardware-log.md`)
- [x] Initial work committed and pushed to `main` (2026-11-20)

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
- [ ] Centre all servos before the overnight split (§11.3)
- [ ] Confirm whether the relay module is active-high (the firmware assumes `on=True` → D1 high)
- [ ] Check the NeoPixels, relay click and ISD1820 PLAYL/REC on real hardware (so far only the replies have been checked)
- [ ] T4: garbage on the data port → error reply, no reset
- [ ] T19: edit `code.py` live → reload ≤ 5 s, server reconnects, G0 passes again
- [ ] Decide at G0 whether to keep CircuitPython or fall back to the Arduino bridge (D3); `firmware/arduino/` doesn't exist yet

## HuskyLens / vision (D9, §9.6)

- [ ] **Throws misread in the first live match** (2026-11-21, `hardware-log.md`). Fixes, in order:
      1. Raise the XIAO→camera UART to 115200 (`--baud 115200` and the camera's own setting) for more reads per window
      2. Lengthen `read_window_s` from 0.3 to ~0.5 s in the game's `settings.json`; check reveal lag still ≤ 250 ms for rigged-win (T9)
      3. Retrain all 4 classes in the exact throw position and lighting (backdrop and fill light once they exist)
      4. Run the 30-throw test in `train_ui.py` until ≥ 85 % (G2) before trusting live play

- [ ] **RPS retrain 2026-11-21 (02:40–02:45) is incomplete**: rock 59, paper 58, nothing 30, **scissors 0** samples. The camera's
      `learned_ids` = 4 is the highest ID, not a count, so it hid the missing class. Train scissors, then even out every class
      to the same count (the classifier favours classes with more samples), then run the 30-throw test (none run since)
- [ ] Bayesian read filter (proposed 2026-11-21): **tried offline on the 02:58 test, no gain** (leave-one-out naive Bayes on the
      per-frame confusion matrix: 21/30, same as the majority vote). The errors are whole throws read wrong on most frames
      (paper → rock 6/6), so smoothing can't fix them. Revisit only once training is good
- [ ] 30-throw test 2026-11-21 02:58: **21/30 = 70 %, fails G2**. Scissors 9/10, rock 7/10, paper 5/10; paper frames read as rock 33 %.
      Samples still uneven (rock 59, paper 58, scissors 30, nothing 30). Next: forget all, retrain every class at the same count
      (~40) in the throw position, paper with fingers spread wide, then retest
- [ ] `tests/` write fake run logs into the real `server/data/vision/runs/` (the `train_up/down/nothing` files at 02:29–02:37)
- [x] A5: camera updated from 0.3.7 to **0.5.1b** (2026-11-21)
- [x] Learn/forget/text/names verified on 0.5.1b over camera USB via the MCP tools: learn 0x36, forget 0x37 (R9 resolved); 5 learns → `learned_ids` 1; forget → 0 (2026-11-21)
- [ ] First real-hardware run of `tools/train_gestures.py`: `learned_ids == 4`; one sample per LEARN?;
      0x3B/0x3C reply formats (done 2026-11-21: 0x3B → u16 1; 0x3C → OK only); learn/forget opcode 0x36/0x37 vs 0x2F/0x30 (R9)
- [ ] Bench pilot gesture training (IDs 1–4 = rock/paper/scissors/nothing): done 2026-11-21 (4 learned IDs, reads unreliable). **Retrain on the final rig** with the backdrop and fill light
- [ ] T7: 30 throws, mixed players, ≥ 85 %
- [ ] **Pre-train the two team faces** (Face Recognition IDs 1 and 2) before the demo, then save to an SD slot (`husky_save_model`). No registration at the table (D10, decided 2026-11-21)
- [ ] Check that gesture and face models both survive algorithm switches (each algorithm keeps its own learned data?), since setup switches face → classification
- [ ] Stranger flow: unknown face (ID 0) or no face → **rigged-win, the robot always beats them** (confirmed by the user 2026-11-21); the two known faces → fair (confirm with Anhad)
- [ ] T8 (revised): both known faces identified ≥ 9/10 each; a third person reads as a stranger ≥ 9/10
- [ ] `husky_measure_fps` on Friday, which decides whether rigged-win is viable (D10, R7)
      (2026-11-20 via XIAO @ 9600, nothing learned: 18.8 req/s, 54 ms median. 2026-11-21, 4 classes learned: 19.1 req/s, 50.5 ms median,
      **camera ~9.8 fps** in classification. Recheck at 115200)
- [ ] MediaPipe fallback ready in case G2 fails (D15)

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
- [ ] Screens plan: [`designs/mcp-live-view/PLAN.md`](../designs/mcp-live-view/PLAN.md) (visual: `plan.html`), updated 2026-11-21 to
      the agreed shape (two play modes, `server.py` hosts the view, Claude Desktop the MCP host, Train separate, 2-min demo).
      Open: its decisions (put the screens in the design doc? which laptop runs the demo? venue display?)
- [x] `FakeBridge` in `bridge.py` (NFR-08): all §9.2 commands with the firmware's checks, `FakeCamera` behind it (pretrained: gestures 1–4, faces 1–3); `open_link('fake')` returns it (2026-11-21). **Commit it before the overnight split** (§11.3)
- [ ] T1: protocol unit tests (knock `55 AA 11 00 2C 3C`, example block)
- [ ] T3: FakeBridge parity, with the same suite against the fake and the real bridge
- [x] `server/hardware.py`: servo/pose/LED/relay/voice plus calibration, soft limits, 5 s hold watchdog (2026-11-21; exercised through the game tests on `FakeBridge`, not on real servos)
- [x] `server/vision.py`: `VisionSource` with `HuskyVision` (2026-11-21, fake camera only); `MediaPipeVision` is a stub that raises `VisionError` until D15 is built (still open)
- [x] `server/game.py`: `Store` + `Game`: moves/rules/players/sequences CRUD, all modes (fair, predict, rigged_win, rigged_lose/mercy, grudge, bribery, bluff, sore_loser), §9.4 round with replays, matches, hooks, scoring (2026-11-21; simulated only, 49 tests pass)
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
- [ ] Named Cloudflare Tunnel for the demo (`cf tunnels create` … in `remote-mcp.md` §2b, not yet run against the account); then connect
      a claude.ai / Desktop custom connector with `?key=` and check `list_machines` from another machine
- [ ] Register the MCP server with Claude Code / Claude Desktop (`--link bridge`) and run a training session through Claude after the 0.5.1 flash
- [x] `server/server.py`: the rest of §9.3 (hardware, moves, sequences, rules, players, play, score, vision source, banter) (2026-11-21; 52 tools, tool functions tested directly on `--fake`, not over stdio and not on hardware). `--fake` uses `server/data/fake/`. Laptop speech is **opt-in** in every mode (`--speak` or `RPSBOT_SPEAK=1`), after it spoke by surprise in a real test (2026-11-21)
- [x] `server/data/`: moves, rules, players, sequences, settings, history.jsonl created on first run (calibration.json by the hardware layer; `banter.json` is optional) (2026-11-21)
- [ ] `CLAUDE.md` (§9.1): or point it at `AGENTS.md`
- [ ] `docs/CONNECT_TEST.md` (§9.1)
- [x] T10 fairness (300 rounds, 33 % +/- 5), T11 banter (no repeat in 10), T12 persistence, T18 sequence validation, T9 rigged-win on the simulator (reveal lag ~0 ms; the real lag depends on the camera), NFR-09 relax: `tests/test_game.py`, `.venv/bin/python -m unittest discover tests` (2026-11-21). **Still to run on real hardware:** T9 (lag <= 250 ms), T10 on the real hand, T17, T18 from chat
- [x] Game on the real camera with a simulated hand (`--hand sim`, auto-detected when the XIAO has no PCA9685): connect, read,
      fair round OK, 665 ms after shoot (2026-11-21; see `hardware-log.md`)
- [x] First live best-of-3 with a person throwing (fair, simulated hand): robot won 2–1; every round 733–849 ms after shoot
      (NFR-01 ✓), but **the user says the reads were wrong**. Fixes under HuskyLens / vision (2026-11-21)
- [ ] Hardware gameplay: once the PCA9685 is wired, `--hand auto` should pick `real`; calibrate fingers first (`calibrate_finger`)
- [ ] **Two modes, one game engine and one set of MCP tools** (agreed 2026-11-21; not in the design doc):
      **Hardware RPS** = `server.py --hand real` (printed hand, LEDs, relay, ISD1820). **Software RPS** = `server.py --hand sim`
      (real HuskyLens reads you; a simulated XIAO plays the robot; `--fake` also simulates the camera). `--hand auto` picks
      by asking the XIAO for the PCA9685. Claude Desktop stays the MCP host in both
- [ ] **Software RPS view** (the "virtual robot" in a browser): a small HTTP + SSE server inside `server.py` on 127.0.0.1:8770,
      fed by `game.listeners` (round steps) and the simulated XIAO's pose/LED changes; a page based on the Stage draft
      (`designs/mcp-live-view/stage.html`) with an animated hand, LED ring, countdown, robot vs you, score and banter.
      It never shows the player's mode. Operate/Trace later, same feed
- [ ] Run a best-of-5 through Claude on the rig; check `returned_ms_after_shoot` <= 1500 (NFR-01) and the 5 s hold on real servos
- [ ] Confirm the QR algorithm id (7?) and that the bribe card is learned as ID `settings.bribe_card_id` (default 1) in QR recognition; the check fails soft until then
- [ ] Pre-train faces 1 and 2 with `register_player` (prep), then set their modes (`set_player_mode`); strangers default to rigged_win (`settings.stranger_mode`, per the user's decision above)
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
- [x] **Built-in machines relabelled as fallbacks** (2026-11-21): `server/machines/` → `server/fallback_machines/`, `list_machines`
      reports `source: "fallback"`, each description starts "Fallback game", and the server instructions tell Claude to write its own game
      when asked for a new one and use a fallback only when that fails. Machine names (`rps`, `higher_lower`) unchanged
- [ ] **Saved gesture profiles** (planned 2026-11-21, [`gesture-profiles-plan.md`](gesture-profiles-plan.md)): save each gesture set's
      trained model to its own HuskyLens SD slot (rps → 1, higher_lower → 2) plus its ID map in `<data>/gesture_profiles.json`, so
      `start_machine` loads the right model instead of wiping and retraining. First check on the camera that SD save/load works over the XIAO.
      Still on hold: principle 2 now allows saved models as **manual backups** (slot 1 RPS, slot 2 up/down/nothing, 2026-11-21), but not an
      automatic load on swap, which would replace the live teach. The manual backup steps are under "Saved backups on the SD card" further down
- [x] Documented the demo plan as the demo's source of truth (`docs/demo-plan.md`, linked from `AGENTS.md`) and the state-machine
      server (`docs/state-machine.md`) (2026-11-21)
- [ ] Hit the demo targets in `demo-plan.md`: swap + setup ≤ 35 s, up/down/nothing ≥ 85 %, a thumb registers in ≤ 1 s, Claude's own
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
- [ ] **Next for the machines:** run `higher_lower` and `rps` on the real camera through Claude Desktop (tune `gesture_window_s` /
      `gesture_stable`; the defaults 0.4 s x 2 windows add ~0.8 s per thumb); measure the real reveal lag in the rps machine (T9)
- [ ] Decide whether `play_round` / `play_match` become thin wrappers over `start_machine('rps')` (their tests pass against the machine,
      so it is safe) and then delete the duplicated `_round` in `game.py`. Kept both for now: the engine is the tagged fallback
- [x] **Face ID for any machine** (2026-11-21): built-in `identify` action (`{"identify": {"into": "who"}}`) returns
      only who's there (`name`, `known`, `face_id`, `spectators`, ...) from the shared player list; the game decides what it means
      (RPS: `rps.seat_player`, stranger = rigged-win). `rps.json` has an optional `setup` state (`vars.identify`, `check_bribe`) =
      §9.4's Setup. `identify_player` / `register_player` now refuse while a machine runs (D10: no algorithm switch mid-game). 116 tests pass
- [ ] Per-player data that outlives a machine run (e.g. a swipe game's likes, a higher-or-lower best score): a built-in
      `remember`-style action keyed by player name. Machine vars are lost when the machine ends
- [ ] A running machine is not restored after a server restart (definitions are saved in `<data>/machines/`, the state and vars are not)
- [ ] Feed the live-view screens from the machine: the runtime already emits `machine_state`, `countdown`, `beat`, `round_start`, `report`,
      `match_end`, `idle` on `game.listeners`; the HTTP/SSE view (item above) is not built
- [ ] Machine limits to revisit once Claude has written a few: one machine at a time; `to` targets are static; no sub-machines; gesture
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
- [ ] The demo swap wipes the RPS training: restore it with `start_machine('rps', swap_gestures=true)` + `husky_load_model(1)` before every demo run (retrain with `train_ui.py` only if slot 1 is missing or reads badly)
- [ ] Hardware: train up/down/nothing (samples=8, interval 0.4 s), then `tools/gesture_check.py up down nothing` → ≥ 85 % each.
      If up and down get confused, try other shapes (e.g. thumbs up vs flat palm down)
- [ ] Time the on-stage swap (Claude's `define_machine(..., swap_gestures=true)` + 3 trainings + 2 test reads); target ≤ 35 s (`demo-plan.md`)
- [ ] Banter bank format: `banter.py` reads `<data_dir>/banter.json` as `[{text, when}]`, but `docs/banter-training-plan.md` Step 2 writes `server/data/banter/bank.json` keyed by `(event, mode, outcome, …)`. Align the loader when the generator is built

- [x] **RPS is only the static back-up game** (user, 2026-11-21): server instructions and `gestures.py` now say so; new games, gestures, moves and rules are MCP data (`define_machine`, `define_move`, `define_rule`, …), never code or firmware pushes to the XIAO/camera. Nothing RPS-specific lives in `firmware/circuitpython/code.py`
- [ ] Doc: D9/§9.3 present RPS as *the* game; reword it as the default back-up with chat-defined games on top (with the live-swap item above)

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
- [ ] Note the firmware's §9.2 interpretations in the doc: servo `ch` 0–4, `pose` takes exactly 4 values,
      µs clamped to 500–2500, `led`/`countdown` argument shapes, `voice` takes `play_ms` or `rec_ms` (not both), relay polarity
- [ ] Doc: make HuskyLens firmware ≥ 0.5.1 an explicit requirement (A5, HLO-1), add a "flash the camera" step before G0, record that R9 happened
- [ ] Add the non-§9.2 firmware extras to the doc: `uart` (set HuskyLens baud) and `status_led` (onboard RGB LED: `rgb`, `blink_hz`, `brightness`)
- [ ] **Two play modes** (agreed 2026-11-21): Hardware RPS (`--hand real`) and Software RPS (`--hand sim`, a virtual robot view), `--hand auto` detection. Not in the doc
- [ ] **Demo is 2 minutes** (new constraint, 2026-11-21): HLO-5 (a new move via chat in ≤ 5 min) is verified beforehand, not shown live. A1 still TBC
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
- [ ] Strangers → rigged-win and "no registration at the table" (user decisions, 2026-11-21) refine D10/FR-13; the doc says fair is the default
- [ ] **Game-engine interpretations** (2026-11-21, `server/game.py`), for the user to accept or change:
      `{"repeat": n}` = play the steps since the start or the previous repeat n times in total; steps < 60 ms clamped up; extra `{"relay": bool}` step;
      grudge turns rigged-win from the **next round** after a human win; bribery = the mode, or the QR card in `identify_player(check_bribe=True)`, lasting the match;
      `delete_move` may delete the classic three (and their rules); the fair pool = moves in at least one rule; a rigged round with no counter plays a draw;
      the hand relaxes ~1.5 s after the result and hooks run after that (NFR-09); unknown and nothing reads both trigger a replay (max 2);
      `voice_countdown` off until the ISD1820 clip is recorded; bluff announces at the start of the countdown; sore loser demands best of 3, then 5
