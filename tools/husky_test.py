"""HuskyLens protocol smoke test (design doc §9.1, gate G0, assumption A5).

Knocks the HuskyLens and reads is-pro / firmware, through either the XIAO bridge
(D1-A, default) or a direct USB-TTL adapter (D1-B, --port).

    python tools/husky_test.py                         # auto-find the XIAO bridge
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
    g.add_argument("--bridge", help="XIAO data port (default: auto-detect)")
    g.add_argument("--port", help="USB-TTL adapter port instead of the XIAO")
    p.add_argument("--husky-baud", type=int, default=9600, help="HuskyLens UART baud (§8.2)")
    p.add_argument("--tries", type=int, default=3)
    args = p.parse_args()

    if args.port:
        from serial_link import SerialLink

        link = SerialLink(args.port, args.husky_baud)
        print(f"USB-TTL link on {args.port} @ {args.husky_baud}")
    else:
        from bridge import Bridge, find_bridge_port

        port = args.bridge or find_bridge_port()
        if not port:
            print("No XIAO bridge found. Is the XIAO plugged in and running firmware/circuitpython?")
            return 2
        link = Bridge(port)
        print(f"XIAO bridge on {port}: {link.ping()}")
        print(f"HuskyLens UART: {link.set_husky_baud(args.husky_baud)}")

    hl = HuskyLens(link)
    try:
        r = {}
        for _ in range(args.tries):
            r = hl.knock()
            if r.get("ok"):
                break
        print(f"knock: {r}")
        if not r.get("ok"):
            print("FAIL (G0). Check: HuskyLens Protocol Type = Serial, baud matches, "
                  "D6/TX -> R and D7/RX -> T (crossed), common GND, camera powered.")
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
