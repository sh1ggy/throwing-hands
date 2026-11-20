"""Train and test HuskyLens gesture classification (design doc D9, test T7, gate G2).

Object Classification (algorithm 6), IDs 1/2/3/4 = rock/paper/scissors/nothing.
10-20 samples per class from several hands. Go/no-go: >= 85 % over 30 throws.

Usage (camera on a USB-TTL adapter, Protocol Type = Serial on the HuskyLens):
    pip install --user pyserial
    python tools/train_gestures.py --list-ports
    python tools/train_gestures.py --port /dev/cu.usbserial-XXXX
    python tools/train_gestures.py --port COM5 --test-only        # re-run the 30-throw test
    python tools/train_gestures.py --port COM5 --save-slot 1      # also back up to SD slot 1
    python tools/train_gestures.py --port COM5 --auto --yes       # countdowns instead of Enter
    python tools/train_gestures.py --bridge --auto --yes          # via the XIAO bridge (D1-A)
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from huskylens import HuskyError, HuskyLens  # noqa: E402

CLASSES = {  # D9: IDs 1/2/3/4
    1: ("rock", "Make a FIST in the play zone."),
    2: ("paper", "Show a FLAT OPEN HAND in the play zone."),
    3: ("scissors", "Show INDEX + MIDDLE fingers (scissors) in the play zone."),
    4: ("nothing", "EMPTY play zone: no hand, just the backdrop."),
}
NAME_TO_ID = {name: i for i, (name, _) in CLASSES.items()}
VARY_TIP = "Between samples: shift position/angle a little, swap hands, swap people if you can."
LOG_DIR = ROOT / "server" / "data" / "vision"


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip().lower()
    except EOFError:
        return ""


def wait_for_user(hl: HuskyLens, args, prompt: str, screen: str, seconds: float) -> None:
    """Enter prompt normally; in --auto mode a countdown on the terminal and the HuskyLens screen."""
    if not args.auto:
        ask(prompt)
        return
    print(prompt.rstrip(" .") + f" (auto: {seconds:.0f} s)", flush=True)
    for left in range(int(round(seconds)), 0, -1):
        hl.text(f"{screen} {left}", 10, 10)
        print(f"  {left}...", flush=True)
        time.sleep(1)
    hl.text(f"{screen} GO", 10, 10)


def retry(fn, *args, tries: int = 4, **kwargs) -> dict:
    """Call a HuskyLens command, retrying on BUSY / no reply."""
    r = {}
    for _ in range(tries):
        r = fn(*args, **kwargs)
        if r.get("ok"):
            return r
        time.sleep(0.25)
    return r


def open_link(args):
    """USB-TTL adapter (--port) or the XIAO bridge (--bridge [PORT])."""
    if args.bridge is None:
        from serial_link import SerialLink

        return SerialLink(args.port, args.baud)
    from bridge import Bridge, find_bridge_port

    port = args.bridge if args.bridge != "auto" else find_bridge_port()
    if not port:
        sys.exit("No XIAO bridge answered a ping. Is code.py running and boot.py applied (hard reset)?")
    link = Bridge(port)
    print(f"Bridge on {port}: {link.ping()}")
    link.set_husky_baud(args.baud)
    args.port = port
    return link


def connect(args) -> tuple[object, HuskyLens]:
    link = open_link(args)
    hl = HuskyLens(link)
    r = retry(hl.knock)
    if not r.get("ok"):
        link.close()
        sys.exit(
            f"Knock failed ({r.get('status')}). Check: Protocol Type = Serial on the HuskyLens, "
            f"baud matches ({args.baud}), TX->R and RX->T crossed, common GND."
        )
    print("Knock OK (G0).")
    r = retry(hl.set_algorithm, "object_classification")
    if not r.get("ok"):
        link.close()
        sys.exit(f"Could not switch to Object Classification: {r}")
    print("Algorithm: object classification (6).")
    return link, hl


def train(hl: HuskyLens, args) -> dict:
    log: dict = {"samples_per_class": args.samples, "classes": {}}

    if not args.no_forget:
        if args.auto and not args.yes:
            print("--auto without --yes: not wiping existing learned data (pass --yes to FORGET).")
            log["forgot"] = False
        elif args.yes or ask("Wipe what Object Classification has already learned (FORGET)? [y/N] ") == "y":
            r = retry(hl.forget)
            print(f"Forget: {r.get('status')}")
            log["forgot"] = r.get("ok", False)
        else:
            print("Keeping existing learned data.")

    print(f"\nTraining {args.samples} samples per class, one every {args.interval:.1f} s.")
    print(VARY_TIP)
    for cid, (name, how) in CLASSES.items():
        hl.text(f"TRAIN {name.upper()}", 10, 10)
        print(f"\n=== ID {cid}: {name.upper()} ===\n{how}")
        wait_for_user(hl, args, "Get in position, then press Enter to start... ",
                      name.upper(), args.countdown)
        ok = 0
        for i in range(1, args.samples + 1):
            print(f"  sample {i:>2}/{args.samples} ", end="", flush=True)
            time.sleep(args.interval)
            r = retry(hl.learn, cid)
            ok += bool(r.get("ok"))
            if args.auto:
                hl.text(f"{name.upper()} {i}/{args.samples}", 10, 10)
            print("ok" if r.get("ok") else f"FAILED ({r.get('status')})")
        hl.set_name(cid, name)
        log["classes"][name] = {"id": cid, "learn_ok": ok}
        print(f"  {name}: {ok}/{args.samples} learn commands accepted.")

    hl.clear_text()
    info = hl.request("blocks").get("info", {})
    log["learned_ids_reported"] = info.get("learned_ids")
    print(f"\nCamera reports {info.get('learned_ids')} learned IDs (expect 4).")
    return log


def read_throw(hl: HuskyLens, window_s: float) -> tuple[str, list[int]]:
    """Majority vote over ~window_s of reads (design doc §5 step 4)."""
    votes: list[int] = []
    t_end = time.monotonic() + window_s
    while time.monotonic() < t_end:
        try:
            blocks = hl.blocks()
        except HuskyError:
            continue
        votes.append(blocks[0]["id"] if blocks else -1)
    if not votes:
        return "no_reply", votes
    top = Counter(votes).most_common(1)[0][0]
    if top == -1:
        return "none", votes
    return CLASSES.get(top, (f"id{top}",))[0] if top else "unknown", votes


def test(hl: HuskyLens, args) -> dict:
    targets = ["rock", "paper", "scissors"] * (args.test // 3)
    targets += random.sample(["rock", "paper", "scissors"], args.test - len(targets))
    random.shuffle(targets)

    print(f"\n=== Accuracy test: {len(targets)} throws (T7, gate G2 needs >= 85 %) ===")
    print("Mix hands and people. For each prompt: hold the gesture in the play zone, press Enter.")
    results = []
    for n, target in enumerate(targets, 1):
        hl.text(f"{n}/{len(targets)} {target.upper()}", 10, 10)
        wait_for_user(hl, args, f"[{n:>2}/{len(targets)}] show {target.upper():<8} then Enter ",
                      f"{n} {target.upper()}", args.throw_countdown)
        got, votes = read_throw(hl, args.window)
        hit = got == target
        results.append({"target": target, "got": got, "votes": votes})
        print(f"        -> {got}{'' if hit else '   <-- MISS'}")
    hl.clear_text()

    correct = sum(r["target"] == r["got"] for r in results)
    acc = correct / len(results)
    confusion: dict[str, Counter] = {}
    for r in results:
        confusion.setdefault(r["target"], Counter())[r["got"]] += 1

    print(f"\nAccuracy: {correct}/{len(results)} = {acc:.0%}  ->  {'PASS' if acc >= 0.85 else 'FAIL'} (>= 85 %)")
    print("Confusion (target: read counts):")
    for t in ("rock", "paper", "scissors"):
        print(f"  {t:<9} {dict(confusion.get(t, {}))}")
    if acc < 0.85:
        print(
            "Below 85 %: add samples for the weak classes, tighten the backdrop/lighting, or on the "
            "night switch to the MediaPipe fallback (D15)."
        )
    return {"correct": correct, "total": len(results), "accuracy": acc, "pass": acc >= 0.85,
            "confusion": {k: dict(v) for k, v in confusion.items()}, "throws": results}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", help="serial port, e.g. COM5 or /dev/cu.usbserial-XXXX")
    p.add_argument("--bridge", nargs="?", const="auto", metavar="PORT",
                   help="go through the XIAO bridge (§9.2); PORT optional, auto-detected by ping")
    p.add_argument("--baud", type=int, default=9600, help="HuskyLens UART baud")
    p.add_argument("--list-ports", action="store_true")
    p.add_argument("--samples", type=int, default=15, help="samples per class (D9: 10-20)")
    p.add_argument("--interval", type=float, default=0.8, help="seconds between samples")
    p.add_argument("--test", type=int, default=30, help="test throws (D9/T7: 30)")
    p.add_argument("--window", type=float, default=0.3, help="majority-vote window, s (§5: ~300 ms)")
    p.add_argument("--test-only", action="store_true", help="skip training, only run the accuracy test")
    p.add_argument("--no-test", action="store_true")
    p.add_argument("--no-forget", action="store_true", help="add to existing learned data")
    p.add_argument("--yes", action="store_true", help="don't ask before FORGET")
    p.add_argument("--save-slot", type=int, help="save the model to this SD-card slot afterwards")
    p.add_argument("--auto", action="store_true",
                   help="no Enter prompts: timed countdowns (terminal + HuskyLens screen) instead")
    p.add_argument("--countdown", type=float, default=6, help="--auto: seconds before each class")
    p.add_argument("--throw-countdown", type=float, default=3, help="--auto: seconds before each test throw")
    p.add_argument("--note", default="", help="free text for the log, e.g. 'bench pilot'")
    args = p.parse_args()

    if args.list_ports or not (args.port or args.bridge):
        from serial_link import available_ports

        ports = available_ports()
        print("Serial ports:\n  " + ("\n  ".join(ports) if ports else "(none found)"))
        if not args.port:
            print("\nRe-run with --port <device>.")
        return

    link, hl = connect(args)
    log = {"when": datetime.now().isoformat(timespec="seconds"), "port": args.port,
           "baud": args.baud, "note": args.note}
    try:
        if not args.test_only:
            log["train"] = train(hl, args)
        if not args.no_test:
            log["test"] = test(hl, args)
        if args.save_slot is not None:
            r = retry(hl.save_model, args.save_slot)
            print(f"Save model to SD slot {args.save_slot}: {r.get('status')}")
            log["saved_slot"] = args.save_slot if r.get("ok") else None
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        link.close()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / f"gesture_run_{datetime.now():%Y%m%d_%H%M%S}.json"
        path.write_text(json.dumps(log, indent=2))
        print(f"Log: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
