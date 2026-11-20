"""How reliably does the camera tell the current gesture set apart? (e.g. thumbs up vs thumbs down)

For each gesture you hold it in the play zone, press Enter, and the script takes N majority-vote
reads (the same vote read_gesture uses) and prints a confusion table. Run it after training a set
(start_machine(swap_gestures=true) + train_gesture through the MCP server, or train_ui.py); stop the MCP server
first, only one program can hold the serial port.

    .venv/bin/python tools/gesture_check.py                    # every gesture in the current set, 20 reads each
    .venv/bin/python tools/gesture_check.py up down -n 30
    .venv/bin/python tools/gesture_check.py --fake             # simulated camera

Silent: prints only. Exit 0 if every gesture scores >= --pass (default 85 %, the D9 go/no-go).
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tools"))

from connect import NoCamera, open_link  # noqa: E402
from gestures import GestureSets  # noqa: E402
from huskylens import HuskyError, HuskyLens  # noqa: E402


def vote(hl: HuskyLens, window_s: float) -> int:
    votes: list[int] = []
    t_end = time.monotonic() + window_s
    while time.monotonic() < t_end or not votes:
        try:
            blocks = hl.blocks()
        except HuskyError:
            continue
        votes.append(blocks[0]["id"] if blocks else -1)
    return Counter(votes).most_common(1)[0][0]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("gestures", nargs="*", help="default: every gesture in the current set")
    p.add_argument("-n", type=int, default=20, help="reads per gesture")
    p.add_argument("--window", type=float, default=0.6, help="seconds per majority-vote read")
    p.add_argument("--pass", dest="pass_", type=float, default=0.85)
    p.add_argument("--data", default=str(ROOT / "server" / "data"), help="data dir holding gestures.json")
    p.add_argument("--fake", action="store_true")
    p.add_argument("--link", default="auto")
    p.add_argument("--port")
    args = p.parse_args()

    data = Path(args.data) / ("fake" if args.fake and args.data == str(ROOT / "server" / "data") else "")
    gs = GestureSets(data / "gestures.json")
    wanted = args.gestures or list(gs.ids)
    try:
        link, desc = open_link("fake" if args.fake else args.link, args.port)
    except NoCamera as e:
        print(f"no camera: {e}")
        return 2
    hl = HuskyLens(link)
    hl.set_algorithm("object_classification")
    print(f"camera: {desc}\nset: {gs.name} {gs.ids}\n")

    results: dict[str, Counter] = {}
    for g in wanted:
        gid = gs.ids.get(g)
        if gid is None:
            print(f"skip {g!r}: not in the {gs.name!r} set")
            continue
        input(f"Hold {g.upper()} in the play zone (move it a little between reads), then press Enter...")
        hits = Counter()
        for i in range(args.n):
            hits[gs.label(vote(hl, args.window))] += 1
            print(f"\r  {i + 1}/{args.n}", end="", flush=True)
        results[g] = hits
        print(f"\r  {g}: {hits[g]}/{args.n} = {hits[g] / args.n:.0%}   misreads: "
              f"{dict((k, v) for k, v in hits.items() if k != g) or 'none'}")
    link.close()

    ok = all(r[g] / args.n >= args.pass_ for g, r in results.items())
    print(f"\n{'PASS' if ok else 'FAIL'} (threshold {args.pass_:.0%})")
    return 0 if ok and results else 1


if __name__ == "__main__":
    sys.exit(main())
