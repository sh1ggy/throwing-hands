"""HuskyLens protocol smoke test (design doc §9.1, gate G0, assumption A5).

Knocks the HuskyLens and reads is-pro / firmware, over the camera's own micro-USB
(firmware >= 0.5.1), the XIAO bridge (D1-A) or a USB-TTL adapter (D1-B, --port).

    python tools/husky_test.py                         # auto: the XIAO, then camera micro-USB
    python tools/husky_test.py --usb                   # camera micro-USB only
    python tools/husky_test.py --bridge /dev/cu.usbmodem103
    python tools/husky_test.py --port /dev/cu.usbserial-XXXX   # USB-TTL adapter
    python tools/husky_test.py --husky-baud 115200     # after changing it on the camera
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from huskylens import HuskyLens  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--usb", nargs="?", const="", metavar="PORT", help="camera's micro-USB (default: by USB ID)")
    g.add_argument("--bridge", help="XIAO data port")
    g.add_argument("--port", help="USB-TTL adapter port instead of the XIAO")
    p.add_argument("--husky-baud", type=int, help="HuskyLens UART baud (§8.2; default 9600, --usb probes)")
    p.add_argument("--tries", type=int, default=3)
    args = p.parse_args()

    from connect import NoCamera, open_link

    if args.port:
        kind, port = "serial", args.port
    elif args.bridge:
        kind, port = "bridge", args.bridge
    elif args.usb is not None:
        kind, port = "usb", args.usb or None
    else:
        kind, port = "auto", None
    try:
        link, desc = open_link(kind, port, args.husky_baud)
    except NoCamera as e:
        print(f"No camera found: {e}")
        return 2
    print(f"Link: {desc}")

    hl = HuskyLens(link)
    try:
        r = {}
        for _ in range(args.tries):
            r = hl.knock()
            if r.get("ok"):
                break
        print(f"knock: {r}")
        if not r.get("ok"):
            print("FAIL (G0). Through the XIAO: Protocol Type = Serial, baud matches, "
                  "D6/TX -> R and D7/RX -> T (crossed), common GND, camera powered. "
                  "Over micro-USB: firmware >= 0.5.1 and a data cable.")
            return 1
        for name in ("is_pro", "firmware_version"):
            try:
                print(f"{name}: {getattr(hl, name)()}")
            except Exception as e:  # informational only
                print(f"{name}: error {e!r}")
        print("PASS (G0): knock OK")
        return 0
    finally:
        link.close()


if __name__ == "__main__":
    sys.exit(main())
