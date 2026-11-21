"""Demo reset: snapshot the game data once, put it back between rehearsals and runs.

Only for the demo. The MCP server never reads the snapshot: it lives in demo-snapshot/ at the repo root
(gitignored), and `restore` copies it back into server/data/ while the server is stopped.

    .venv/bin/python tools/demo_reset.py snapshot     # once, when the data is how you want every run to start
    .venv/bin/python tools/demo_reset.py restore      # between runs, with server.py stopped
    .venv/bin/python tools/demo_reset.py show         # what's in the snapshot

The snapshot covers the game JSON (moves, rules, players, sequences, settings, gestures) and machines/. In the copy,
every player is set to fair with zero wins/losses/draws and an empty throw history, so each run starts at 0-0.
`restore` also deletes machines Claude saved since the snapshot. It leaves history.jsonl, vision/ and fake/ alone.

The camera's model is on its SD card, not in server/data: after restarting the server, load slot 1
("load model slot 1" in chat, or husky_load_model(1)).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "server" / "data"
SNAP = ROOT / "demo-snapshot"
FILES = ["moves", "rules", "players", "sequences", "settings", "gestures"]


def server_running() -> list[str]:
    r = subprocess.run(["pgrep", "-fl", "server/server.py"], capture_output=True, text=True)
    return [ln for ln in r.stdout.splitlines() if ln.strip() and "demo_reset" not in ln]


def zero_players(path: Path) -> list[str]:
    players = json.loads(path.read_text())
    for p in players:
        p.update(mode="fair", wins=0, losses=0, draws=0, history=[], grudge_active=False)
    path.write_text(json.dumps(players, indent=2))
    return [p["name"] for p in players]


def snapshot(data: Path, snap: Path, force: bool) -> int:
    if snap.exists() and not force:
        print(f"{snap} already exists. Use --force to replace it.")
        return 1
    if snap.exists():
        shutil.rmtree(snap)
    snap.mkdir(parents=True)
    for name in FILES:
        src = data / f"{name}.json"
        if src.exists():
            shutil.copy2(src, snap / src.name)
    if (data / "machines").is_dir():
        shutil.copytree(data / "machines", snap / "machines")
    else:
        (snap / "machines").mkdir()
    names = zero_players(snap / "players.json") if (snap / "players.json").exists() else []
    print(f"Snapshot saved to {snap}")
    print(f"  players (fair, 0-0-0): {', '.join(names) or 'none'}")
    print(f"  machines: {', '.join(sorted(p.name for p in (snap / 'machines').glob('*.json'))) or 'none'}")
    return 0


def restore(data: Path, snap: Path) -> int:
    if not snap.is_dir():
        print(f"No snapshot at {snap}. Run `snapshot` first.")
        return 1
    running = server_running()
    if running and data == DATA:
        print("server.py is running. Stop it first: it keeps the data in memory and would write it back.")
        for ln in running:
            print("  " + ln)
        return 1
    for src in snap.glob("*.json"):
        shutil.copy2(src, data / src.name)
    machines = data / "machines"
    removed = []
    if machines.is_dir():
        keep = {p.name for p in (snap / "machines").glob("*")}
        for p in machines.iterdir():
            if p.name not in keep:
                removed.append(p.name)
                shutil.rmtree(p) if p.is_dir() else p.unlink()
    machines.mkdir(exist_ok=True)
    for p in (snap / "machines").glob("*"):
        shutil.copy2(p, machines / p.name)
    print(f"Restored {data} from {snap}")
    print(f"  removed machines: {', '.join(removed) or 'none'}")
    print("Next: start server.py again, then load camera model slot 1.")
    return 0


def show(snap: Path) -> int:
    if not snap.is_dir():
        print(f"No snapshot at {snap}.")
        return 1
    for p in sorted(snap.rglob("*.json")):
        print(p.relative_to(snap))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["snapshot", "restore", "show"])
    ap.add_argument("--force", action="store_true", help="snapshot: replace an existing snapshot")
    ap.add_argument("--data", type=Path, default=DATA, help=argparse.SUPPRESS)  # for testing on a scratch copy
    ap.add_argument("--snap", type=Path, default=SNAP, help=argparse.SUPPRESS)
    a = ap.parse_args()
    data, snap = a.data.resolve(), a.snap.resolve()
    if a.cmd == "snapshot":
        return snapshot(data, snap, a.force)
    if a.cmd == "restore":
        return restore(data, snap)
    return show(snap)


if __name__ == "__main__":
    sys.exit(main())
