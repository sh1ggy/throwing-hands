"""Run a machine on the fake camera and serve the live game view, with no Claude and no hardware.

    .venv/bin/python tools/view_demo.py [machine] [--port 8771]

Opens nothing by itself: browse to http://127.0.0.1:8771/view. Restarts the machine when it finishes, so the view
always has something to show (rehearsing the projector layout, checking a machine Claude wrote).
"""
import argparse
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.argv, argv = sys.argv[:1], sys.argv[1:]

import server as S  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("machine", nargs="?", default="rps", help="a machine name, or a path to a machine JSON to define first")
ap.add_argument("--port", type=int, default=8771)
ap.add_argument("--data", help="game data directory (default: a fresh temp dir, so your data is never touched)")
args = ap.parse_args(argv)

S.cam.kind = "fake"
S.OPTIONS["data_dir"] = args.data or tempfile.mkdtemp(prefix="rpsbot-view-")
if args.machine.endswith(".json"):  # a game written elsewhere (e.g. by Claude): define it, then run it by name
    import json
    m = json.loads(Path(args.machine).read_text())
    S.define_machine(m, swap_gestures=bool(m.get("gestures")))  # temp data + fake camera: wiping is harmless
    for g in [*m.get("gestures", []), "nothing"] if m.get("gestures") else []:
        S.train_gesture(g, samples=3, interval_s=0, countdown_s=0)
    args.machine = m["name"]
S.game_view.serve_side(f"127.0.0.1:{args.port}", lambda rev, events: S.game_view.payload(S.get_runtime(), rev, events))
print(f"live game view: http://127.0.0.1:{args.port}/view  (machine {args.machine!r}, fake camera)", flush=True)
rt = S.get_runtime()
while True:
    if rt.status != "running":
        try:
            out = S.start_machine(args.machine)
            if "machine" not in out or out.get("status") != "running":  # needs its gestures: train them on the fake
                S.start_machine(args.machine, swap_gestures=True)
                for g in [*S.get_machine(args.machine).get("gestures", []), "nothing"]:
                    S.train_gesture(g, samples=3, interval_s=0, countdown_s=0)
                out = S.start_machine(args.machine)
            print(out.get("machine") or out, flush=True)
        except Exception as e:  # a bad machine: say why and try again, the view stays up
            print(f"start failed: {e}", flush=True)
            time.sleep(3)
    time.sleep(2)
