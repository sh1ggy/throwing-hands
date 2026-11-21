# Hardware log

Facts about the actual devices, as found on the bench. The design doc (`docs/rps-robot-design-doc.md`) stays the source of truth for intent; this file records what the hardware really is and how it's set.

## 2026-11-20

### HuskyLens Pro
- **Firmware: 0.3.7 (stable)**, read from the on-device menu. Resolves A5 ("latest firmware: TBC"): it is **not** the v0.5.1 that `server/huskylens.py` targets.
  - Risk: learn/forget may need the older opcodes (0x2F/0x30 instead of 0x36/0x37). Override with `RPSBOT_HUSKY_LEARN_OP` / `RPSBOT_HUSKY_FORGET_OP` if LEARN fails.
  - Newer commands (text overlay, custom names, SD model save/load, is-pro, firmware query) may not exist on 0.3.7. Record which ones reply.
- Settings: **Protocol Type = Serial, UART 9600** (§8.2).

### XIAO RP2040
- Arrived running an unknown Arduino sketch (USB name "PicoArduino"); it didn't answer the §9.2 protocol. **Replaced** with CircuitPython (D3-B).
- **CircuitPython 10.3.2** (`seeeduino_xiao_rp2040`), UID 4250304B38353817.
- Bridge firmware: `firmware/circuitpython/` (`boot.py`, `code.py`, version string `cpy-bridge 0.3`). Implements `ping`, `husky`, plus a non-§9.2 `uart` (set HuskyLens baud). Other §9.2 commands reply "not implemented". `husky` replies include `rx_bytes` and `rest` (leftover raw hex) for diagnosing a silent or garbled camera.
  - CircuitPython gotcha: `bytes.hex()` returns **bytes**, not str, so `json.dumps` crashed code.py. Use `binascii.hexlify(b).decode()`. Fixed in 0.3, and the main loop now catches reply-encode errors (NFR-02).
  - Bad input (`"hex": "zz"`) gets an error reply and the bridge stays up (T4 spot check).
- macOS ports: `/dev/cu.usbmodem101` = console/REPL, `/dev/cu.usbmodem103` = bridge data channel. `server/bridge.py` finds the data port by ping.

### Link
- USB-TTL adapter: never enumerated on the Mac (nothing on the USB bus). Likely a charge-only cable or a dongle. Abandoned for the XIAO bridge (D1-A).
- XIAO → HuskyLens: knock got **no reply** (0 bytes received) at every baud, because **the HuskyLens wasn't physically wired to the XIAO yet**. Nothing is wrong with the link itself. Next: wire per §8.2 (camera T → D7, R → D6, GND, VCC → XIAO 5V), then rerun the knock.
- **Wired and working (later 2026-11-20):** knock returns OK through the XIAO (`cpy-bridge 0.4`, 9600 baud). `tools/husky_test.py` passes, so G0 is met on this Mac.
  - `request()` returns a valid INFO frame (0 blocks, 0 learned IDs, frame 0).
  - The is-pro (0x3B) and firmware (0x3C) queries only get an empty `0x21` frame back, which fits 0.3.7 not supporting them.

### HuskyLens firmware 0.3.7: what the serial protocol supports
- Probed through the XIAO bridge (2026-11-20). **Supported:** knock (0x2C), algorithm (0x2D) and request (0x20, returns INFO 0x29). These reply with OK (0x2E) or data.
- **Unsupported:** every other opcode tried returns an empty `0x21` frame, the same as a made-up opcode (0x50). That includes learn 0x36, forget 0x37, the "older" 0x2F/0x30, text 0x34/0x35, save/load 0x32/0x33, screenshot 0x39, is-pro 0x3B and firmware 0x3C.
- So **learning over serial is impossible on 0.3.7**. The first UI training run reported "15/15 accepted" while the camera still had 0 learned IDs, because `huskylens.py` counted any reply as OK. It now reports empty `0x21` replies as `UNSUPPORTED`.
- Options: update the camera to ≥ 0.5.1 (needs a micro-USB data cable; this also unlocks names, text overlay and SD save/load for FR-01), or train with the camera's own learning button and use serial only to read results and run the test.
- **Decision (2026-11-20): flash to `HUSKYLENSWithModelV0.5.1bNorm.kfpkg`**, the newest stable build in DFRobot's HUSKYLENSUploader repo. The `Class` builds only do object classification, and we need face recognition too.
  - The firmware and DFRobot's `kflash.py` are in `vendor/huskylens-uploader/` (git-ignored); their git blob hashes match GitHub.
  - `tools/flash_huskylens.py` only targets the CP210x (VID 0x10C4), so it can't hit the XIAO.
  - ~~No CP210x driver is installed on this Mac yet; the Silicon Labs VCP driver is needed (admin rights).~~ **Corrected 2026-11-20:** macOS 26.5 ships Apple's `com.apple.DriverKit-AppleUSBSLCOM.dext`, which matches VID 0x10C4 PID 0xEA60/0xEA70 (CP2102/2102N/2104/2105). No driver install or admin is needed; the camera shows up as `/dev/cu.usbserial-*`. `kflash.py` needs only pyserial, and `/dev/cu.*` is user-writable, so flashing needs no `sudo` either.

### Why 0.3.7 can't learn over serial (evidence, 2026-11-20)
1. **Camera behaviour:** knock and algorithm get OK (0x2E); request gets INFO. Learn, forget, names, text, SD, screenshot, is-pro and firmware all get the same empty `0x21` frame as a made-up opcode (0x50).
2. **DFRobot protocol history** (`HUSKYLENSArduino/HUSKYLENS Protocol.md`):
   - Mar 2020, protocol v0.1: only 0x20–0x2E (requests, knock, algorithm, OK). That is exactly what our camera supports.
   - Aug 2020, protocol v0.5.1: added 0x2F names, 0x30 photo, 0x32/0x33 SD save/load, 0x34/0x35 text, 0x36 learn, 0x37 forget, 0x39 screenshot, 0x3B is-pro.
   - Feb 2021: added 0x3C firmware, 0x3D BUSY, 0x3E NEED_PRO.
3. **Firmware publish dates** (`HUSKYLENSUploader`):
   - 0.3.3, 0.3.7 and 0.4.5 came out in Jan 2020; 0.4.6–0.4.9 in Mar 2020. All of these predate the protocol expansion.
   - 0.5.1Norm (Sep 2020) is the first build after it. 0.5.1bNorm was republished in Jun 2025 (its `HuskyLens.bin` is dated Mar 2025).
4. **Firmware binaries** (`strings` on `HuskyLens.bin`):
   - 0.3.7 (`0.3.7Stable`) has only the on-device learn UI ("Learn %d", "Forget!").
   - 0.5.1b adds SD card, screenshot, knowledge save/load ("SUCCESFULLY WROTE KNOWLEDGE DATA TO SD", "LOADING KNOWLEDGES"), names ("saving names") and the custom-text limit ("Message text is too long. Please make less than 200 chars").
   - None of these strings exist in 0.3.7.

**Conclusion:** learning, names, text and SD over serial need firmware **≥ 0.5.1**. Pressing the camera's learn button works on 0.3.7. The 0.3.7 package is in `vendor/huskylens-fw-0.3.7/` (hash checked) in case we ever need to roll back.

### MCP server through the XIAO (2026-11-20)
- `server/server.py` with no flags auto-picked the XIAO (`bridge /dev/cu.usbmodem103 (cpy-bridge 0.5) @ 9600`). The stdio launch (`--link bridge`) lists all 19 tools and the knock passes.
- On firmware 0.3.7: knock OK, `husky_status` and `read_gesture` work (0 learned IDs, nothing in view). Is-pro and firmware get `UNSUPPORTED`. `train_gesture` stops after one `UNSUPPORTED` learn and returns the "flash it" hint, as designed.
- First `husky_measure_fps` reading (2 s, 9600 baud, through the XIAO): **18.8 requests/s**, median latency **54 ms** (max 55), camera frame counter **~48 fps**. The 9600 UART is the bottleneck. Recheck at 115200 for D10/R7.
- Close `train_ui.py` before starting the MCP server. Both want the XIAO port, and only tools started after the exclusive-lock change get a clean "port busy" error.

### Flashed to 0.5.1b (2026-11-21)
- Camera micro-USB enumerates as `/dev/cu.usbserial-1130`, **CP2102N** (VID 0x10C4, PID 0xEA60), on Apple's built-in driver. No install or admin needed.
- **Attempt 1, 2 Mbaud** (DFRobot default): entered ISP fine, then "Error Count Exceeded" at 85.6 % of `HuskyLens.bin`. Exit 1.
- **Attempt 2, 1 Mbaud:** the cable was knocked out at 20 %, which left kflash hung on a dead port. Killed it.
- **Attempt 3, 1 Mbaud:** all 15 files written, no errors, rebooted. Exit 0. `flash_huskylens.py` now defaults to 1 Mbaud.
- During the flash the screen is black (ROM bootloader). That's expected.
- **Camera-USB link (D1-E) works:** `husky_test.py --usb` → knock OK at **3 Mbaud** (huskylib's default).
- **0x3B is-pro reply format:** frame `0x3B` with u16 `1` (Pro).
- **0x3C firmware:** plain OK (0x2E) with no version text. 0x3C post-dates 0.5.1 (protocol Feb 2021).
- Firmware on the camera's own screen: **Pro 0.5.1bNorm** (checked by the user).
- **Learn/forget demo over camera USB through the MCP tools** (`husky_connect usb` → `train_gesture rock ×5` → `husky_forget`):
  - Object classification: all 5 learns accepted, `learned_ids` 0 → 1. One block came back: ID 1, 224×224 at (160, 112).
  - `read_gesture` → rock (21/21 reads).
  - Text overlay and names work.
  - `husky_forget` → OK, and `learned_ids` dropped back to 0.
  - So **learn is 0x36 and forget is 0x37** on 0.5.1b (R9 resolved). Object classification always reports the frame as one 224×224 block.
- **Through the XIAO after the flash:** micro-USB unplugged, Gravity cable on the XIAO, camera Protocol Type left on **Auto Detect**. `husky_test.py` → knock OK at 9600, is-pro → u16 1. Auto Detect is enough; Serial isn't required. The XIAO's ports came back as `/dev/cu.usbmodem11301`/`11303` (the names change with the USB socket; auto-detect finds the bridge by ping).

### Game on the real camera, software gameplay (2026-11-21)
- Wired: only the HuskyLens, on the XIAO (`/dev/cu.usbmodem11303`, cpy-bridge 0.5, 9600). No PCA9685, LEDs, relay or ISD1820.
- `server.py` now has `--hand auto|real|sim` (`RPSBOT_HAND`). **Auto picked sim:** the XIAO answered `relax` with
  "PCA9685 unavailable … No pull up found on SDA or SCL". So the camera is real and the hand, LEDs, relay and voice are a `FakeBridge`.
  With a simulated hand the robot's throw shows on the HuskyLens screen ("ROBOT: SCISSORS").
- Camera had **4 learned IDs** (trained with `train_ui.py`). An empty play zone read `nothing` (ID 4), but the votes were mixed
  (`{0: 2, 4: 4, 3: 4}` over 10 reads), so the "nothing" class is close to scissors. Retrain on the final rig with the backdrop.
- `husky_measure_fps` (2 s, 9600 via XIAO, object classification): **19.1 req/s**, median latency **50.5 ms** (max 110), camera **~9.8 fps**.
  That's much lower than the ~48 fps counter on 0.3.7 with nothing learned. Classification with 4 classes is slower, and it matters for rigged-win (R7).
- One fair `play_round` with nobody throwing: one replay, then read scissors. Robot paper, so human wins.
  `committed_before_shoot` true (FR-09), read 302 ms, **returned 665 ms after shoot** (NFR-01 ≤ 1500 ✓).
- The laptop voice (macOS `say`) spoke the banter by surprise. **The voice is now opt-in** (`--speak` or `RPSBOT_SPEAK=1`).
- **Live best-of-3, fair, a person throwing** (voice off, simulated hand, 0.45 s beats, 0.3 s read window):
  robot won 2–1 with one draw. Camera read paper, rock, rock, rock.
  - **The user says the reads were wrong.** Two rounds were bare majorities (paper 3/5; rock 3/6) and two were rock 6/6.
  - Only **5–6 votes per 0.3 s window** at ~19 reads/s (9600 baud).
  - Every round returned **733–849 ms after shoot** (NFR-01 ✓).
  - Likely causes: too few votes per window, and training done away from the throw position or lighting.
  - The robot's throw was only visible as text on the HuskyLens screen. There's no software view yet.


### The hand as built (2026-11-21, reported by the user)
- **No thumb.** There is no thumb servo or tendon.
- **Index: one motor. Ring + little: one motor.** These are the only two working channels.
- **Middle: motor dead-ish, not set up yet.** Not wired, so for now the finger has no tendon pull.
- The elastic return cord (H-2, D7) holds every finger open, so an unpowered finger **stands open**. With the middle finger
  open, the RPS "rock" pose (everything else closed) shows the middle finger. Tie the middle finger closed (or fix its motor) before
  the hand goes in front of anyone.
- Which PCA9685 channels the two motors are on is still to confirm. The code assumes §8.2: CH0 thumb, CH1 index, CH2 middle,
  CH3 ring+little, and `pose` always takes 4 values.
- **SDA/SCL wired by Anhad** to XIAO D4/D5 (2026-11-21), as in §8.2. Breadboard wiring for the real hand (XIAO, HuskyLens,
  PCA9685, 2 servos, separate 5 V supply): [`designs/hand-breadboard.svg`](../designs/hand-breadboard.svg).

## 2026-11-21 (after the demo)

### New XIAO RP2040 and servos
- A **new XIAO RP2040** replaced the old one, PCA9685 and servos connected. It arrived on Arduino firmware (USB 2e8a:000a, one
  serial port, no CIRCUITPY drive).
  - Odd: its USB serial / CircuitPython UID is **4250304B38353817**, the same value logged for the old board on 2026-11-20.
    Either that earlier entry was copied from this board, or the boards really are the same. Worth a look at the label.
- Flashed: 1200-baud touch on `/dev/cu.usbmodem101` → `RPI-RP2` (UF2 Bootloader v3.0) → **CircuitPython 10.3.2**
  (`seeeduino_xiao_rp2040`); `circup install -r firmware/circuitpython/requirements.txt`; copied `boot.py` and `code.py`;
  `microcontroller.reset()` from the REPL. Ports as before: `101` console, `103` bridge.
- Bridge: `ping` → `cpy-bridge 0.5`.
- **PCA9685 found** at 0x40: `servo` ch 0–4 at 1500 → 1700 → 1500 µs, then `relax`, all `ok: true`. That proves the I²C link
  and the driver; it does **not** prove which finger is on which channel, or that each servo moved (needs eyes on the hand).
- Not run: the relay and voice steps of `tools/xiao_check.py` (they make sound).
- **Channel map, confirmed by eye** (each channel wiggled 1300 ↔ 1700 µs three times, owner watching):

  | PCA9685 channel | Finger |
  |---|---|
  | CH0 | index |
  | CH1 | middle (the motor works now) |
  | CH2 | ring + little (one motor) |
  | CH3, CH4 | nothing connected |

  No thumb. This differs from §8.2 (CH0 thumb, CH1 index, CH2 middle, CH3 ring+little) and from `server/hardware.py`, which
  still follows §8.2. Listed under "Design-doc issues to resolve" in `TODO.md`.

