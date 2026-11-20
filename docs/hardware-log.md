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
