"""Exercise every XIAO command (design doc §9.2) once, harmlessly.

    python tools/xiao_check.py                 # no servo motion
    python tools/xiao_check.py --servos        # also move the fingers a little, then relax
    python tools/xiao_check.py --bridge /dev/cu.usbmodem103

LEDs stay dim, the relay ends off. Exit code 0 if everything that was tried returned ok.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

from bridge import Bridge, BridgeError, find_bridge_port  # noqa: E402

DIM = 0.05
MID = 1500


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bridge", help="XIAO data port (default: auto-detect)")
    p.add_argument("--servos", action="store_true", help="also move servos (power the PCA9685 V+ first)")
    args = p.parse_args()

    port = args.bridge or find_bridge_port()
    if not port:
        print("no XIAO bridge found (is boot.py applied? try --bridge /dev/cu.usbmodemXXX)")
        return 2
    print(f"bridge: {port}")
    failures = 0

    with Bridge(port) as b:
        def run(label, cmd, wait=0.0, **kw):
            nonlocal failures
            try:
                r = b.call(cmd, timeout_s=2.0, **kw)
            except BridgeError as e:
                r = {"ok": False, "error": str(e)}
            ok = r.get("ok")
            failures += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {label:<28} {r}")
            if wait:
                time.sleep(wait)
            return r

        run("ping", "ping")
        run("led fill (dim)", "led", fill=[0, 0, 40], brightness=DIM)
        run("led rgb list (dim)", "led", rgb=[[40, 0, 0], [0, 40, 0], [0, 0, 40]], brightness=DIM, wait=0.5)
        run("countdown", "countdown", beats=3, period_ms=200, colours=[[40, 0, 0], [40, 40, 0], [0, 40, 0]],
            brightness=DIM, wait=1.0)
        run("led off", "led", fill=[0, 0, 0])
        run("relay on", "relay", on=True, wait=0.3)
        run("relay off", "relay", on=False)
        run("voice play 100 ms", "voice", play_ms=100, wait=0.3)
        run("voice stop", "voice", play_ms=0)
        if args.servos:
            run("servo ch0 1500", "servo", ch=0, us=MID, wait=0.5)
            run("pose instant", "pose", us=[MID] * 3, ms=0, wait=0.5)
            run("pose ramp 500 ms", "pose", us=[1400, 1400, 1400], ms=500, wait=0.8)
            run("pose back", "pose", us=[MID] * 3, ms=300, wait=0.5)
            run("relax", "relax")
        else:
            print("skip  servo/pose/relax (use --servos)")
        run("relay off (final)", "relay", on=False)

    print("ALL OK" if not failures else f"{failures} command(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
