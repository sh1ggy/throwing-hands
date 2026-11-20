# RPS-Bot: living TODO

What still needs to happen, in order of when it blocks things. The plan and the "why" live in
[`rps-robot-design-doc.md`](rps-robot-design-doc.md) (the source of truth); this file only tracks status.
Section refs (§, D, T, V, G, A) point into the design doc.

**Keeping it current:** tick items as they land (add the date and a one-line result), add new work as it
turns up, and move anything that changes the design into the design doc itself. Agents: update this file
in the same change as the work.

_Last updated: 2026-11-20 (HuskyLens link up)_

---

## Now / blockers

- [x] HuskyLens answers through the XIAO: knock OK, `request()` returns INFO (2026-11-20; see `hardware-log.md`)
- [x] Initial work committed and pushed to `main` (2026-11-20)

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

- [ ] A5: the camera is on **0.3.7**, not the latest. Decide whether to update it (needs a micro-USB **data** cable) or code around it (0x3B/0x3C unsupported; learn/forget opcodes unverified)
- [ ] First real-hardware run of `tools/train_gestures.py`: `learned_ids == 4`; one sample per LEARN?;
      0x3B/0x3C reply formats; learn/forget opcode 0x36/0x37 vs 0x2F/0x30 (R9)
- [ ] Bench pilot gesture training (IDs 1–4 = rock/paper/scissors/nothing), then **retrain on the final rig** with the backdrop and fill light
- [ ] T7: 30 throws, mixed players, ≥ 85 %
- [ ] Face registration (algorithm 0); T8: 3 players × 5 scans, ≥ 14/15
- [ ] `husky_measure_fps` on Friday, which decides whether rigged-win is viable (D10, R7)
- [ ] MediaPipe fallback ready in case G2 fails (D15)

## Software (§9.1, §9.3)

- [x] `server/huskylens.py`: protocol library
- [x] `server/serial_link.py`: direct USB-TTL transport
- [x] `server/bridge.py`: XIAO JSON-lines client (`Bridge`, `find_bridge_port`)
- [x] `tools/husky_test.py`: knock smoke test; `tools/xiao_check.py`: firmware check; `tools/train_gestures.py`
- [ ] `FakeBridge` in `bridge.py`, with canned HuskyLens frames (NFR-08). **Commit it before the overnight split** (§11.3)
- [ ] T1: protocol unit tests (knock `55 AA 11 00 2C 3C`, example block)
- [ ] T3: FakeBridge parity, with the same suite against the fake and the real bridge
- [ ] `server/hardware.py`: servo/pose/LED/relay/voice plus calibration
- [ ] `server/vision.py`: `VisionSource` with `HuskyVision` and `MediaPipeVision` (FR-16)
- [ ] `server/game.py`: rules graph, modes, players, scoring, sequences (D20)
- [ ] `server/banter.py`: bank lookup plus optional GGUF model (see `docs/banter-training-plan.md`)
- [ ] `server/server.py`: FastMCP tools for the full §9.3 catalogue; register with Claude Desktop
- [ ] `server/data/`: moves, rules, players, sequences, calibration, banter
- [ ] `CLAUDE.md` (§9.1): or point it at `AGENTS.md`
- [ ] `docs/CONNECT_TEST.md` (§9.1)
- [ ] T10 fairness, T11 banter, T12 persistence, T18 live sequence

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
- [ ] §9.1 lists `CLAUDE.md`; the repo now uses `AGENTS.md`
- [ ] Note the firmware's §9.2 interpretations in the doc: servo `ch` 0–4, `pose` takes exactly 4 values,
      µs clamped to 500–2500, `led`/`countdown` argument shapes, `voice` takes `play_ms` or `rec_ms` (not both), relay polarity
