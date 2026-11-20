"""Flash the HuskyLens to firmware 0.5.1b (needed for learn/forget/names/text/SD over serial).

Firmware 0.3.7 only supports knock, algorithm and request over UART (docs/hardware-log.md), so
training over serial needs >= 0.5.1. This wraps DFRobot's kflash.py and only ever targets the
HuskyLens's own USB port (Silicon Labs CP210x, VID 0x10C4), never the XIAO.

Setup (already done once; files are git-ignored under vendor/):
    vendor/huskylens-uploader/kflash.py
    vendor/huskylens-uploader/HUSKYLENSWithModelV0.5.1bNorm.kfpkg
    (from github.com/HuskyLens/HUSKYLENSUploader, checked against GitHub's blob hashes)

Before flashing:
    1. Unplug the Gravity cable from the HuskyLens (it flashes over micro-USB; one power source only, §8.3).
    2. Plug the HuskyLens's micro-USB into the Mac with a DATA cable (BOM: charge-only cables won't enumerate).

    python tools/flash_huskylens.py            # finds the port, asks, flashes
    python tools/flash_huskylens.py --check    # only look for the port
    python tools/flash_huskylens.py --slow     # 115200 baud if 1 Mbaud fails (slow: ~15 min)
    python tools/flash_huskylens.py --baud 2000000   # DFRobot's default; failed at 85 % on macOS 26

After flashing: unplug/replug the micro-USB, then on the camera set General Settings >
Protocol Type = Serial, UART speed 9600 (settings are reset), reconnect the Gravity cable and run
tools/husky_test.py.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from serial.tools import list_ports

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "huskylens-uploader"
KFLASH = VENDOR / "kflash.py"
FIRMWARE = VENDOR / "HUSKYLENSWithModelV0.5.1bNorm.kfpkg"
FIRMWARE_SIZE = 10226671
SILABS_VID = 0x10C4


def find_port() -> str | None:
    ports = [p for p in list_ports.comports() if p.vid == SILABS_VID]
    for p in ports:
        print(f"  found {p.device}  ({p.description}, {p.manufacturer})")
    if len(ports) > 1:
        sys.exit("More than one CP210x device is plugged in: unplug the others so the right one gets flashed.")
    return ports[0].device if ports else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="only look for the HuskyLens port")
    ap.add_argument("--slow", action="store_true", help="flash at 115200 baud")
    ap.add_argument("--baud", type=int, default=1_000_000,
                    help="flash speed (default 1 Mbaud: 2 Mbaud errored out with Apple's CP210x driver)")
    ap.add_argument("--yes", action="store_true", help="don't ask before flashing")
    args = ap.parse_args()

    for f in (KFLASH, FIRMWARE):
        if not f.exists():
            sys.exit(f"Missing {f.relative_to(ROOT)}. See the docstring for where it comes from.")
    if FIRMWARE.stat().st_size != FIRMWARE_SIZE:
        sys.exit(f"{FIRMWARE.name} is {FIRMWARE.stat().st_size} bytes, expected {FIRMWARE_SIZE}: re-download it.")

    print("Looking for the HuskyLens (Silicon Labs CP210x) on USB…")
    port = find_port()
    if not port:
        print("Not found. Check: micro-USB DATA cable (not charge-only) into the HuskyLens, camera screen on.\n"
              "On Apple-silicon Macs, click Allow if macOS asks whether the accessory may connect.\n"
              "No driver install is needed: macOS ships Apple's CP210x driver (AppleUSBSLCOM, VID 0x10C4,\n"
              "PID 0xEA60/0xEA70), so the camera appears as /dev/cu.usbserial-*. Only very old macOS needs\n"
              "Silicon Labs' VCP driver (admin).")
        return 2
    if args.check:
        print("HuskyLens port OK.")
        return 0

    print(f"\nAbout to flash {FIRMWARE.name} to {port}.")
    print("This replaces the camera firmware and resets its settings and anything it has learned.")
    print("Don't unplug it until kflash says it's finished (about 1-3 minutes).")
    if not args.yes and input("Type 'flash' to continue: ").strip() != "flash":
        print("Cancelled.")
        return 1

    cmd = [sys.executable, "-I", str(KFLASH), "-p", port, "-b", "115200" if args.slow else str(args.baud), str(FIRMWARE)]
    print("$ " + " ".join(cmd))
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc == 0:
        print("\nDone. Next:\n"
              "  1. Unplug and replug the HuskyLens micro-USB; check Settings > Version shows 0.5.1b.\n"
              "  2. General Settings > Protocol Type = Serial, UART speed 9600.\n"
              "  3. Unplug micro-USB, reconnect the Gravity cable to the XIAO, run tools/husky_test.py.")
    else:
        print(f"\nkflash exited with {rc}. If it failed partway, retry with --slow; the camera's bootloader "
              "survives a failed flash, so it can be re-flashed.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
