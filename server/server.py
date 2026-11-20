"""RPS-Bot MCP server (design doc D4, §9.3): HuskyLens, hand/LED/voice hardware, and gesture games.

The server is a state-machine runtime: a game is a JSON machine that Claude defines and patches live
(machine.py, machine_runtime.py). RPS (machines/rps.json) and higher-or-lower (machines/higher_lower.json) are two of them.

Tools, in groups:
  husky_*, train_gesture, read_gesture   the HuskyLens (every protocol command) and gesture training
  define/patch/get/list/delete_machine, start/stop_machine, machine_state, poke, machine_reference   state machines
  gesture_set, random_number              what the camera's IDs mean now; fair randomness for games
  hand_*, calibrate_finger, led_*, fill_light, voice_*, speak   the XIAO hardware (hardware.py, NFR-05/NFR-09)
  list/define/delete/perform_move, *_rule, load_ruleset          moves and rules (D11, FR-11/12)
  *_sequence, attach_sequence                                    behaviour sequences (D20, FR-19)
  register/identify/list players, set_player_mode               players and modes (D10, FR-13)
  play_round, play_match, get_score, reset_score                the game (game.py, §9.4)
  set_vision_source, banter_preview                              vision swap (FR-16) and banter (D16)

The game's modes are SECRET from the audience: tool results are for you, never read a player's mode out loud.

The camera is found the same way the training tools find it (server/connect.py): the
XIAO bridge first (the rig link, D1-A), then the camera's micro-USB (firmware >= 0.5.1).

    .venv/bin/python server/server.py            # stdio, for Claude Desktop / Claude Code
    .venv/bin/python server/server.py --fake     # simulated XIAO + camera, no hardware
    .venv/bin/python server/server.py --fake --speak     # also speak banter on the laptop (voice is off by default)
    RPSBOT_LINK=bridge RPSBOT_PORT=/dev/cu.usbmodem103 .venv/bin/python server/server.py

Game data (moves, rules, players, sequences, settings, calibration, history) lives in server/data/;
--fake uses server/data/fake/ so simulated runs never touch real data. RPSBOT_DATA overrides the directory.

Claude Code:   claude mcp add rps-bot -- /abs/path/.venv/bin/python /abs/path/server/server.py
Only one program can hold the serial port: stop train_ui.py / train_gestures.py first.
Every training call writes a run log to server/data/vision/runs/ (tools/runlog.py, §9.1).
"""

from __future__ import annotations

import argparse
import functools
import os
import secrets
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tools"))

from mcp.server.mcpserver import MCPServer  # noqa: E402  (mcp 2.x name for FastMCP)
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402  (message reaches Claude)

from banter import Banter  # noqa: E402
from bridge import FakeBridge
from connect import NoCamera, open_link  # noqa: E402
from game import Game, GameError  # noqa: E402
from gestures import GestureError, GestureSets  # noqa: E402
from hardware import Hardware, HardwareError  # noqa: E402
from huskylens import ALGORITHMS, HuskyError, HuskyLens  # noqa: E402
from machine import Context, MachineError, MachineStore, reference, validate  # noqa: E402
from machine_runtime import Runtime  # noqa: E402
import rps_functions  # noqa: E402,F401  (registers the rps.* machine functions)
from runlog import LoggedHusky, RunLog  # noqa: E402
from vision import HuskyVision, MediaPipeVision, VisionError  # noqa: E402

# D9: Object Classification, IDs 1-4 = rock/paper/scissors/nothing by default. gestures.py names the IDs
# and a machine's `gestures` (swap_gestures) swaps in a new set live.
CLASSIFICATION = "object_classification"


class Camera:
    """One shared, lazily opened HuskyLens connection. Reconnects after a link error."""

    def __init__(self, kind: str, port: str | None, baud: int | None):
        self.kind, self.port, self.baud = kind, port, baud
        self.link = None
        self.hl: HuskyLens | None = None
        self.desc: str | None = None
        self.run: RunLog | None = None
        self.lock = threading.RLock()

    def connect(self, kind: str | None = None, port: str | None = None, baud: int | None = None) -> dict:
        with self.lock:
            if kind is not None:
                self.kind, self.port, self.baud = kind, port, baud
            self.close()
            try:
                self.link, self.desc = open_link(self.kind, self.port, self.baud)
            except NoCamera as e:
                raise ToolError(f"No HuskyLens found: {e}") from e
            except OSError as e:
                raise ToolError(f"Could not open the camera link: {e}") from e
            self.hl = LoggedHusky(HuskyLens(self.link), lambda: self.run)
            knock = retry(self.hl.knock)
            return {"link": self.desc, "knock": knock}

    def close(self) -> None:
        if self.link is not None:
            try:
                self.link.close()
            except Exception:
                pass
        self.link = self.hl = self.desc = None

    def get(self) -> HuskyLens:
        if self.hl is None:
            self.connect()
        return self.hl  # type: ignore[return-value]

    def call(self, method: str, *args, **kwargs):
        """Run one HuskyLens method; on a link error reconnect once and retry."""
        with self.lock:
            try:
                return getattr(self.get(), method)(*args, **kwargs)
            except HuskyError as e:  # bad arguments etc.: retrying won't help
                raise ToolError(str(e)) from e
            except (OSError, RuntimeError):  # dropped link: reconnect once
                self.close()
                try:
                    return getattr(self.get(), method)(*args, **kwargs)
                except (OSError, RuntimeError, HuskyError) as e:
                    raise ToolError(f"{method} failed after reconnecting: {e}") from e


def retry(fn, *args, tries: int = 4, **kwargs) -> dict:
    """Retry on BUSY / no reply (same policy as tools/train_gestures.py)."""
    r: dict = {}
    for _ in range(tries):
        r = fn(*args, **kwargs)
        if r.get("ok"):
            return r
        time.sleep(0.25)
    return r


def majority_read(hl, window_s: float, labels: GestureSets) -> dict:
    """Majority vote over ~window_s of block reads (§5 step 4)."""
    votes: list[int] = []
    t_end = time.monotonic() + window_s
    while time.monotonic() < t_end:
        try:
            blocks = hl.blocks()
        except HuskyError:
            continue
        votes.append(blocks[0]["id"] if blocks else -1)
    if not votes:
        return {"gesture": "no_reply", "votes": votes, "confidence": 0.0, "confident": False}
    top, n = Counter(votes).most_common(1)[0]
    share = round(n / len(votes), 2)
    return {"gesture": labels.label(top), "id": max(top, 0), "confidence": share,
            "confident": top > 0 and share >= 0.6 and len(votes) >= 3,
            "votes": {labels.label(k): v for k, v in Counter(votes).items()}, "reads": len(votes)}


cam = Camera(os.environ.get("RPSBOT_LINK", "auto"), os.environ.get("RPSBOT_PORT") or None,
             int(os.environ["RPSBOT_BAUD"]) if os.environ.get("RPSBOT_BAUD") else None)

mcp = MCPServer(
    "rps-bot",
    instructions=(
        "Controls a gesture-game robot hand: HuskyLens camera, hand, LEDs, voice and games. "
        "HOW GAMES WORK: the server runs STATE MACHINES. A game is one JSON machine (vars, initial, states; each state has "
        "on_enter actions and event handlers) that the server runs on its own loop: it reads the camera, fires "
        "gesture:<name> / timer / enter / poke events, evaluates safe JSON conditions and runs actions (speak, show, led, "
        "pose, random, countdown, call library functions...). You are NOT in the per-turn loop. Games, gestures, moves, rules "
        "and sequences are all DATA changed through these tools, NEVER by editing code or pushing firmware to the XIAO or "
        "the camera. To play something new: machine_reference (the format), define_machine (rejected with a clear message "
        "if invalid), train the gestures it needs (a machine that declares gestures refuses to start until the camera has "
        "them; define_machine/start_machine with swap_gestures=true wipes the camera and names the new set, then "
        "train_gesture each, including 'nothing' = empty play zone; for a quick swap samples=8, interval_s=0.4, "
        "countdown_s=2; verify with read_gesture), then start_machine and follow it with machine_state. CHANGING THE RULES "
        "= patch_machine on the running machine (atomic: a bad patch leaves the game untouched). poke steers a running "
        "machine from chat. Use random_number or the machine's random action for anything random; never pick outcomes "
        "yourself. Built-in machines: 'rps' (the back-up game: Object Classification IDs 1 rock, 2 paper, 3 scissors, "
        "4 nothing; vars mode/player/match/best_of; its logic is the rps.* function library, game settings under $cfg) and "
        "'higher_lower' (thumbs up/down, numbers 1-10, a wrong guess ends it). get_machine shows one in full to copy. "
        "The robot's outputs (hand_pose, perform_move, led_fill, play_sequence, speak, husky_text) work any time. "
        "play_round/play_match are the original built-in RPS engine (same behaviour as the rps machine) and need the "
        "rps gestures. To train: ask the user to hold the gesture in view, then call train_gesture; vary hand "
        "position/angle and people between calls. husky_forget wipes ALL learned IDs for the current algorithm: always "
        "ask the user first. Use read_gesture to check what the camera sees. "
        "RPS players: identify_player (optional face scan; an unknown face is a stranger), then play_round, play_match or "
        "start_machine('rps'); or start_machine('rps', vars={\"identify\": true}) to scan inside the machine. Any machine can "
        "scan faces with its identify action; the face tools refuse while a machine runs. The RPS modes (fair, predict, rigged_win, rigged_lose/mercy, grudge, bribery, bluff, "
        "sore_loser) are SECRET from the audience: results show the mode for you only, so never say a player's mode "
        "or that the robot is rigged out loud, and never pass it to speak. The robot already speaks its own banter. "
        "Claude can invent moves and rules live (define_move, define_rule) and behaviour sequences "
        "(define_sequence, then play_sequence or attach_sequence to on_win / on_loss / on_reveal). "
        "Calibrate fingers (calibrate_finger) before the hand moves; poses are soft-limited and the hand relaxes "
        "after every round and sequence."
    ),
)

# --- Game wiring (built on first use, so the camera and data dir can be chosen at startup) --------

OPTIONS: dict = {"speak": None, "data_dir": None, "hand": os.environ.get("RPSBOT_HAND", "auto")}
HAND_MODES = ("auto", "real", "sim")
_game: Game | None = None
_game_lock = threading.Lock()


_gsets: GestureSets | None = None


def gsets() -> GestureSets:
    """The gesture registry for the current data dir (built on first use, like the game)."""
    global _gsets
    path = data_dir() / "gestures.json"
    if _gsets is None or _gsets.path != path:
        _gsets = GestureSets(path)
    return _gsets


def need_rps() -> None:
    if not gsets().rps_ready():
        g = gsets()
        raise ToolError(f"the camera holds the {g.name!r} gesture set {list(g.ids)}, not rock/paper/scissors; "
                        "start_machine('rps', swap_gestures=true) and train them first")


def data_dir() -> Path:
    if OPTIONS["data_dir"]:
        return Path(OPTIONS["data_dir"])
    if os.environ.get("RPSBOT_DATA"):
        return Path(os.environ["RPSBOT_DATA"])
    return ROOT / "server" / "data" / ("fake" if cam.kind == "fake" else "")


def get_game() -> Game:
    global _game
    with _game_lock:
        if _game is None:
            d = data_dir()
            d.mkdir(parents=True, exist_ok=True)
            # Laptop voice is opt-in (--speak or RPSBOT_SPEAK=1): it should never start talking by surprise.
            tts = bool(OPTIONS["speak"]) or os.environ.get("RPSBOT_SPEAK") == "1"
            hand, why = resolve_hand()
            if hand == "real":
                hw = Hardware(lambda: (cam.get(), cam.link)[1], cam.lock, d, tts=tts)
            else:  # software gameplay: the real (or fake) camera reads throws; the hand, LEDs, relay and voice are simulated
                sim = FakeBridge()
                hw = Hardware(lambda: sim, cam.lock, d, tts=tts, simulated=True)
            hw.hand_reason = why
            vision = HuskyVision(cam)
            _game = Game(hw, vision, Banter(d), d, vision_sources={
                "huskylens": lambda: HuskyVision(cam), "mediapipe": lambda: MediaPipeVision()})
        return _game


def resolve_hand() -> tuple[str, str]:
    """Hardware gameplay (real hand on the XIAO's PCA9685) or software gameplay (simulated hand).

    auto: fake camera → sim; camera on its own USB (no XIAO) → sim; XIAO answers `relax` → real;
    XIAO says the PCA9685 is unavailable → sim.
    """
    want = OPTIONS["hand"]
    if want not in HAND_MODES:
        raise ToolError(f"hand must be one of {HAND_MODES}")
    if want == "real":
        return "real", "--hand real"
    if want == "sim":
        return "sim", "--hand sim"
    if cam.kind == "fake":
        return "sim", "fake camera"
    try:
        cam.get()
    except ToolError as e:
        return "sim", f"no camera link yet ({e})"
    link = cam.link
    if not hasattr(link, "call"):
        return "sim", "camera on its own USB, no XIAO"
    with cam.lock:
        r = link.call("relax")
    if r.get("ok"):
        return "real", "XIAO drives the PCA9685"
    return "sim", f"XIAO: {r.get('error', 'no servo board')}"


def reset_game() -> None:
    """Forget the built game and the machine runtime on it (tests, or after changing the data dir)."""
    global _game, _rt
    with _game_lock:
        if _rt is not None:
            _rt.stop()
            _rt = None
        if _game is not None:
            _game.wait_idle()
            _game.hw.close()
        _game = None


def hw() -> Hardware:
    return get_game().hw


def guard(fn):
    """Turn engine/hardware/vision errors into ToolErrors so Claude gets the message."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except ToolError:
            raise
        except (GameError, HardwareError, VisionError, MachineError, HuskyError, ValueError, KeyError, RuntimeError,
                OSError) as e:
            raise ToolError(f"{type(e).__name__}: {e}" if not isinstance(e, (GameError, HardwareError, VisionError, MachineError))
                            else str(e)) from e
    return wrapper


# --- Connection --------------------------------------------------------------------


@mcp.tool()
def husky_connect(link: Literal["auto", "usb", "bridge", "serial", "fake"] = "auto",
                  port: str | None = None, baud: int | None = None) -> dict:
    """(Re)connect to the HuskyLens. auto = the XIAO bridge first, then the camera's micro-USB.
    baud: HuskyLens UART speed via XIAO/adapter (default 9600); for usb, fixes the speed instead of probing."""
    return cam.connect(link, port, baud)


@mcp.tool()
def husky_status() -> dict:
    """Current link, the camera's learned-ID count and what it sees right now."""
    r = cam.call("request", "blocks")
    return {"link": cam.desc, "algorithm": r.get("algorithm"), "status": r.get("status"),
            "learned_ids": r.get("info", {}).get("learned_ids"), "blocks": r.get("blocks", [])}


# --- HuskyLens protocol (§9.3: complete coverage) ----------------------------------


@mcp.tool()
def husky_knock() -> dict:
    """Connection check (0x2C)."""
    return cam.call("knock")


@mcp.tool()
def husky_info() -> dict:
    """Is-pro (0x3B) and firmware version (0x3C). Both need firmware >= 0.5.1."""
    return cam.call("info")


@mcp.tool(description="Switch algorithm (0x2D). Names: " + ", ".join(ALGORITHMS) + "; or a numeric ID.")
def husky_set_algorithm(algorithm: str) -> dict:
    # Retried: a switch sent while the camera is still loading the last model can get a bad reply.
    return retry(cam.call, "set_algorithm", algorithm, tries=3)


@mcp.tool()
def husky_request(kind: Literal["all", "blocks", "arrows"] = "all", learned_only: bool = False,
                  id: int | None = None) -> dict:
    """Read results (0x20-0x28): INFO (count, learned_ids, frame) plus blocks/arrows."""
    return cam.call("request", kind, learned_only, id)


@mcp.tool()
def husky_learn(id: int) -> dict:
    """Learn whatever is in front of the camera now as ID `id` (0x36). One call = one sample."""
    return cam.call("learn", id)


@mcp.tool()
def husky_forget(confirm: bool = False) -> dict:
    """Wipe ALL learned data for the current algorithm (0x37). Ask the user first; pass confirm=true."""
    if not confirm:
        return {"ok": False, "status": "NOT_CONFIRMED", "hint": "ask the user, then call with confirm=true"}
    return cam.call("forget")


@mcp.tool()
def husky_set_name(id: int, name: str) -> dict:
    """Name a learned ID on the camera's screen (0x2F), max 20 ASCII chars."""
    return cam.call("set_name", id, name)


@mcp.tool()
def husky_text(text: str, x: int = 10, y: int = 10) -> dict:
    """Overlay text on the camera's screen (0x34). Screen is 320x240; max 20 chars."""
    return cam.call("text", text, x, y)


@mcp.tool()
def husky_clear_text() -> dict:
    """Clear the screen overlay (0x35)."""
    return cam.call("clear_text")


@mcp.tool()
def husky_photo() -> dict:
    """Save a photo to the camera's SD card (0x30)."""
    return cam.call("photo")


@mcp.tool()
def husky_screenshot() -> dict:
    """Save a screenshot (with overlays) to the camera's SD card (0x39)."""
    return cam.call("screenshot")


@mcp.tool()
def husky_save_model(slot: int) -> dict:
    """Save the current algorithm's learned model to SD-card slot `slot` (0x32)."""
    return cam.call("save_model", slot)


@mcp.tool()
def husky_load_model(slot: int) -> dict:
    """Load a learned model from SD-card slot `slot` into the current algorithm (0x33)."""
    return cam.call("load_model", slot)


@mcp.tool()
def husky_measure_fps(seconds: float = 3.0) -> dict:
    """Poll the camera for `seconds` (0.5-15) and report camera FPS and request latency (D10, R7)."""
    return cam.call("measure_fps", seconds)


@mcp.tool()
def husky_raw(hex: str, expect: int | None = None, timeout_ms: int | None = None,
              add_checksum: bool = False) -> dict:
    """Send raw bytes (e.g. '55 AA 11 00 2C 3C') and return every reply frame decoded (R9 probing)."""
    return cam.call("raw", hex, expect, timeout_ms, add_checksum)


# --- Training (§9.3 Training, D9) --------------------------------------------------


@mcp.tool()
def train_gesture(move: str, samples: int = 15, interval_s: float = 0.8, countdown_s: float = 3.0) -> dict:
    """Learn `samples` samples of a gesture (rock/paper/scissors/nothing or a numeric ID) in
    Object Classification. Shows a countdown on the camera's screen first; the person should hold the
    gesture in the play zone and shift it slightly between samples. Adds to existing samples
    (use husky_forget to start over). D9 suggests 10-20 samples per class from several hands.
    A name that isn't in the current gesture set is added with the next free ID."""
    try:
        gid, name = gsets().resolve(move)
    except GestureError as e:
        raise ToolError(str(e)) from e
    samples = max(1, min(int(samples), 50))
    with cam.lock:
        hl = cam.get()
        cam.run = run = RunLog(f"train_{name}", tool="mcp", link=cam.desc,
                               settings={"move": name, "id": gid, "samples": samples,
                                         "interval_s": interval_s, "countdown_s": countdown_s})
        outcome, err, out = "ok", None, {}
        try:
            r = retry(hl.set_algorithm, CLASSIFICATION)
            if not r.get("ok"):
                raise HuskyError(f"could not switch to object classification: {r.get('status')}")
            for left in range(int(round(countdown_s)), 0, -1):
                hl.text(f"{name.upper()} {left}", 10, 10)
                time.sleep(1)
            accepted, failures = 0, Counter()
            for i in range(1, samples + 1):
                hl.text(f"{name.upper()} {i}/{samples}", 10, 10)
                time.sleep(interval_s)
                r = retry(hl.learn, gid)
                if r.get("ok"):
                    accepted += 1
                else:
                    failures[r.get("status", "?")] += 1
                    if r.get("status") == "UNSUPPORTED":
                        break  # firmware < 0.5.1: no point continuing
            hl.set_name(gid, name)
            hl.clear_text()
            info = hl.request("blocks").get("info", {})
            out = {"move": name, "id": gid, "accepted": accepted, "samples": samples,
                   "failures": dict(failures), "learned_ids": info.get("learned_ids"), "run_log": run.rel()}
            if failures.get("UNSUPPORTED"):
                out["hint"] = "camera firmware can't learn over serial (< 0.5.1); flash it (tools/flash_huskylens.py)"
            return out
        except Exception as e:
            outcome, err = "error", f"{type(e).__name__}: {e}"
            if isinstance(e, (HuskyError, OSError, RuntimeError)):
                cam.close()  # force a clean reconnect next call
                raise ToolError(f"training {name} failed: {e}") from e
            raise
        finally:
            run.end(outcome, summary=out, error=err)
            cam.run = None


@mcp.tool()
def read_gesture(window_s: float = 0.3) -> dict:
    """What gesture the camera sees now, named from the current gesture set: majority vote over `window_s`
    seconds of reads (§5). confidence = the winner's share of the votes; confident = a learned gesture with at
    least 60 % of 3+ reads. For a turn in a game, ask the player to hold still and use window_s about 0.6."""
    with cam.lock:
        hl = cam.get()
        if hl.algorithm is None:
            retry(hl.set_algorithm, CLASSIFICATION)
        return majority_read(hl, max(0.05, min(window_s, 5.0)), gsets())


@mcp.tool()
def gesture_set() -> dict:
    """The current gesture set: gesture name -> camera ID."""
    return gsets().status()


def swap_gestures(name: str, gestures: list[str]) -> dict:
    """WIPE the camera's Object Classification data and name a new gesture set (bad names fail before the wipe)."""
    g = gsets()
    try:
        g.prepare(name, gestures)
    except GestureError as e:
        raise ToolError(str(e)) from e
    with cam.lock:
        hl = cam.get()
        r = retry(hl.set_algorithm, CLASSIFICATION)
        if not r.get("ok"):
            raise ToolError(f"could not switch to object classification: {r.get('status')}")
        r = retry(hl.forget, tries=2)
        if not r.get("ok"):
            raise ToolError(f"wiping the camera failed: {r.get('status')}")
        out = g.start(name, gestures)
        hl.clear_text()
        hl.text(g.name[:20], 10, 10)
    return out


# --- Fair randomness for machines and chat --------------------------------------------


@mcp.tool()
def random_number(low: int = 1, high: int = 10, avoid: int | None = None) -> dict:
    """A fair random integer in [low, high] (secrets). avoid: re-draw if it equals this (e.g. no ties in
    higher-or-lower). Use this for anything a game outcome depends on; don't pick numbers yourself."""
    if high < low:
        raise ToolError("high must be >= low")
    if avoid is not None and low == high == avoid:
        raise ToolError("nothing left to draw once `avoid` is excluded")
    while True:
        n = low + secrets.randbelow(high - low + 1)
        if n != avoid:
            return {"value": n, "low": low, "high": high}


# --- State machines (machine.py, machine_runtime.py): games are data Claude edits while they run ------

BUILTIN_MACHINES = ROOT / "server" / "machines"
_rt: Runtime | None = None


def machine_reader(window_s: float) -> dict:
    """Camera gesture read for machine events: a majority vote over window_s, named from the gesture set."""
    with cam.lock:
        hl = cam.get()
        if hl.algorithm is None:
            retry(hl.set_algorithm, CLASSIFICATION)
        return majority_read(hl, max(0.05, min(window_s, 3.0)), gsets())


def get_runtime() -> Runtime:
    """The machine runtime on the current game (built on first use, like the game)."""
    global _rt
    game = get_game()
    if _rt is None or _rt.env is not game:
        if _rt is not None:
            _rt.stop()
        _rt = Runtime(game, reader=machine_reader, store=MachineStore(data_dir() / "machines", BUILTIN_MACHINES))
    return _rt


def machine_ctx() -> Context:
    game = get_game()
    return Context(gestures=set(gsets().ids), moves={m["name"] for m in game.list_moves()},
                   sequences={s["name"] for s in game.store.get("sequences")})


def gesture_plan(m: dict, swap: bool) -> dict | None:
    """Check (or swap) the camera's gesture set for a machine that declares ``gestures``."""
    need = m.get("gestures")
    if not need:
        return None
    ids = gsets().ids
    missing = [g for g in need if g not in ids]
    if swap:
        out = swap_gestures(m["name"], need)
        return {"swapped": True, "camera_ids": out["gestures"],
                "next": [f"train_gesture('{g}')" for g in out["gestures"]] + ["read_gesture to test each",
                                                                              f"start_machine('{m['name']}')"]}
    if missing:
        raise ToolError(f"machine {m['name']!r} needs the gestures {need} but the camera set is {gsets().name!r} "
                        f"{list(ids)}; pass swap_gestures=true (WIPES the camera's gesture training; then train each "
                        "gesture and start again)")
    return None


@mcp.tool()
@guard
def define_machine(machine: dict, swap_gestures: bool = False) -> dict:
    """Create or replace a state machine (a game) from JSON, validated first: unknown states/gestures/actions/functions,
    unreachable states, dead ends without an exit and bad conditions are rejected with a message and nothing is saved.
    Call machine_reference first for the format. A machine declaring "gestures": [...] needs those on the camera:
    swap_gestures=true WIPES the camera's gesture training, names the new set (IDs follow the list order, then
    'nothing'), and you then train_gesture each one before start_machine. Redefining the RUNNING machine doesn't
    change it: use patch_machine for live edits. Saved to <data>/machines/ (a built-in name gets a user copy)."""
    rt = get_runtime()
    m = validate(machine, machine_ctx())
    plan = gesture_plan(m, swap_gestures)
    rt.store.save(m)
    out = {"machine": m["name"], "states": list(m["states"]), "initial": m["initial"], "saved": True,
           "running": rt.running and rt.name == m["name"]}
    if out["running"]:
        out["note"] = "this machine is running with its OLD definition; stop_machine + start_machine, or use patch_machine"
    return {**out, **(plan or {})}


@mcp.tool()
@guard
def patch_machine(name: str, ops: list[dict], goto: str | None = None) -> dict:
    """Edit a machine, live if it's running. ATOMIC: all ops apply and validate, or none do and the running machine is
    untouched. Ops (each an object with "op"):
      {"op":"set_state","state":s,"value":{on_enter,on,timeout_ms,final}}  add or replace a whole state
      {"op":"remove_state","state":s}
      {"op":"set_handler","state":s,"event":"gesture:up","handlers":[{if,do,to},...]}  replace one event's handlers
      {"op":"add_handler","state":s,"event":e,"handler":{if,do,to},"index":i?}        add one (default: last)
      {"op":"remove_handler","state":s,"event":e}
      {"op":"set_on_enter","state":s,"actions":[...]}   {"op":"set_timeout","state":s,"ms":n|null}
      {"op":"set_vars","vars":{...},"live":true}  change defaults (and the live values when running, default true)
      {"op":"remove_vars","names":[...]}  {"op":"set_initial","state":s}  {"op":"set_settings","settings":{...}}
    A running machine keeps its current state and variables, and uses the new handlers from the next event. Removing
    the state it is in needs goto='<state>' (runs that state's on_enter). 'Changing the rules' is a patch."""
    rt = get_runtime()
    base = rt.store.get(name)
    new, notes = rt.patch(base, ops, machine_ctx(), goto)
    rt.store.save(new)
    return {"machine": name, "applied": notes, "states": list(new["states"]),
            "running": rt.running and rt.name == name}


@mcp.tool()
@guard
def get_machine(name: str) -> dict:
    """A machine's full JSON (the running definition if it is running, else the saved one)."""
    rt = get_runtime()
    if rt.running and rt.name == name and rt.machine:
        return rt.machine
    return validate(rt.store.get(name), machine_ctx())


@mcp.tool()
@guard
def list_machines() -> list[dict]:
    """Saved and built-in machines: name, source (builtin/user), description, gestures needed, states, running."""
    rt = get_runtime()
    out = []
    for n, source in rt.store.names().items():
        try:
            m = rt.store.get(n)
            out.append({"name": n, "source": source, "description": m.get("description", ""),
                        "gestures": m.get("gestures", []), "states": list(m.get("states", {})),
                        "running": rt.running and rt.name == n})
        except MachineError as e:
            out.append({"name": n, "source": source, "error": str(e)})
    return out


@mcp.tool()
@guard
def delete_machine(name: str) -> dict:
    """Delete a user machine (a built-in with that name comes back). Built-ins can't be deleted."""
    rt = get_runtime()
    if rt.running and rt.name == name:
        raise ToolError(f"{name!r} is running; stop_machine first")
    return {"machine": name, "result": rt.store.delete(name)}


@mcp.tool()
@guard
def start_machine(name: str, vars: dict | None = None, swap_gestures: bool = False) -> dict:
    """Run a machine on the server's own loop (stops any running one). The server handles camera gestures, timers and
    transitions itself, so you are NOT in the per-turn loop: start it, then watch with machine_state, steer with poke,
    change it with patch_machine. vars override the machine's initial variables (e.g. rps takes
    {"mode": "fair", "player": "guest", "match": true, "best_of": 3}). If the camera lacks the gestures the machine
    declares this refuses; swap_gestures=true WIPES the camera's gesture training and names the set, and does NOT start:
    train each gesture, then start_machine again."""
    rt = get_runtime()
    m = validate(rt.store.get(name), machine_ctx())
    plan = gesture_plan(m, swap_gestures)
    if plan:
        return plan
    started = rt.start(m, vars)
    return {**started, "status": rt.status, "hint": "machine_state shows the state, vars and recent events"}


@mcp.tool()
@guard
def stop_machine() -> dict:
    """Stop the running machine now and relax the hand (NFR-09)."""
    rt = get_runtime()
    was = rt.stop()
    return {"stopped": was, **{k: v for k, v in rt.snapshot(5).items() if k in ("machine", "status", "state", "vars")}}


@mcp.tool()
@guard
def machine_state(events: int = 20) -> dict:
    """The running (or last) machine: status (running/finished/stopped/error), current state, vars, seconds in the
    state and until its timer, the last `events` events (gestures, timers, pokes, and what the robot said/showed/
    emitted: entries with "output": true and event speak/show/emit) and transitions, warnings (soft action failures), and the
    camera's gesture set. Poll this to follow a game you started."""
    rt = get_runtime()
    return {**rt.snapshot(max(1, min(int(events), 100))), "gesture_set": gsets().status()}


@mcp.tool()
@guard
def poke(payload: Any = None) -> dict:
    """Send the running machine a `poke` event (optionally with a JSON payload, readable as $payload). Use it to steer
    a game from chat: a state's "poke" handlers decide what happens."""
    return get_runtime().poke(payload)


@mcp.tool()
def machine_reference() -> dict:
    """The machine format in one object: states, events, handlers, every action, the expression language and the
    function library (rps.*) with arguments. Read this before writing or patching a machine."""
    return reference()


# --- Hardware (§9.3): hand, LEDs, fill light, voice, speech ------------------------


@mcp.tool()
@guard
def hand_servo(finger: str, value: float) -> dict:
    """Move one finger, leaving the others. finger: thumb/index/middle/ring_little or 0-3. value: 0 closed .. 1 open
    (mapped through calibration, soft-limited)."""
    return hw().servo(finger, value)


@mcp.tool()
@guard
def hand_pose(fingers: list[float], ms: int = 150) -> dict:
    """Pose all four fingers (thumb, index, middle, ring+little), each 0 closed .. 1 open, ramping over `ms`.
    A finger held closed more than 5 s is relaxed automatically (NFR-09)."""
    return hw().pose(fingers, ms)


@mcp.tool()
@guard
def hand_relax() -> dict:
    """Turn the servo PWM off so the fingers go limp."""
    return hw().relax()


@mcp.tool()
@guard
def calibrate_finger(finger: str, open_us: int | None = None, closed_us: int | None = None,
                     test_us: int | None = None) -> dict:
    """Set a finger's calibrated endpoints (open_us, closed_us; saved to calibration.json) and/or drive it to a raw
    pulse width `test_us` (600-2400) while finding them (§9.5). Poses never go beyond these endpoints (NFR-05)."""
    return hw().calibrate(finger, open_us, closed_us, test_us)


@mcp.tool()
@guard
def led_fill(colour: str, brightness: float | None = None) -> dict:
    """Fill the LED strip with a colour ('#rrggbb'), optional brightness 0-1."""
    return hw().led_fill(colour, brightness)


@mcp.tool()
@guard
def led_countdown(beats: int, period_ms: int, colours: list[str] | None = None) -> dict:
    """Run the on-device LED countdown: `beats` beats of `period_ms`, cycling through `colours`."""
    return hw().led_countdown(beats, period_ms, colours)


@mcp.tool()
@guard
def fill_light(on: bool) -> dict:
    """Switch the relay fill light on or off."""
    return hw().fill_light(on)


@mcp.tool()
@guard
def voice_play(ms: int) -> dict:
    """Play the ISD1820 recording for `ms` milliseconds (it holds PLAYL; it can only start from the beginning)."""
    return hw().voice_play(ms)


@mcp.tool()
@guard
def voice_record(ms: int) -> dict:
    """Record on the ISD1820 for `ms` milliseconds (overwrites its one recording)."""
    return hw().voice_record(ms)


@mcp.tool()
@guard
def speak(text: str) -> dict:
    """Say something on the laptop speaker (the audience hears it). Never say a player's mode or that the robot
    is rigged. Doesn't block."""
    return hw().speak(text)


# --- Game (§9.3): moves, rules, sequences, players, play, score, banter ------------


@mcp.tool()
@guard
def list_moves() -> list[dict]:
    """All moves: name, fingers [thumb, index, middle, ring+little] (0 closed..1 open), LED colour, husky_id."""
    return get_game().list_moves()


@mcp.tool()
@guard
def define_move(name: str, fingers: list[float], led: str, husky_id: int | None = None) -> dict:
    """Create or update a move (FR-11). fingers: four values 0..1. led: '#rrggbb'. husky_id: the HuskyLens
    Object Classification ID if it should be recognised from the human (unique; 4 is 'nothing'). Add rules
    with define_rule or the new move never gets thrown."""
    return get_game().define_move(name, fingers, led, husky_id)


@mcp.tool()
@guard
def delete_move(name: str) -> dict:
    """Delete a move and every rule that mentions it (even rock/paper/scissors; load_ruleset restores them)."""
    return get_game().delete_move(name)


@mcp.tool()
@guard
def perform_move(name: str) -> dict:
    """Show a move on the hand and LEDs, then relax after 2 s."""
    return get_game().perform_move(name)


@mcp.tool()
@guard
def define_sequence(name: str, steps: list[dict]) -> dict:
    """Create or replace a behaviour sequence (D20). Steps: {"pose": [4 values 0..1], "ms": n}, {"led": "#rrggbb"},
    {"voice": "play", "ms": n}, {"wait": ms}, {"relay": bool}, {"repeat": n} (plays the steps since the start or the
    previous repeat n times in total). Timed steps are at least 60 ms; total at most 10 s including repeats;
    repeat at most 10; no finger closed more than 5 s. Invalid values are rejected."""
    return get_game().define_sequence(name, steps)


@mcp.tool()
@guard
def list_sequences() -> list[dict]:
    """All sequences with their steps, length and where they are attached."""
    return get_game().list_sequences()


@mcp.tool()
@guard
def play_sequence(name: str) -> dict:
    """Play a sequence now. The hand relaxes at the end."""
    return get_game().play_sequence(name)


@mcp.tool()
@guard
def delete_sequence(name: str) -> dict:
    """Delete a sequence (and detach it from any event)."""
    return get_game().delete_sequence(name)


@mcp.tool()
@guard
def attach_sequence(event: Literal["on_win", "on_loss", "on_reveal"], name: str | None, player: str | None = None) -> dict:
    """Play sequence `name` after a round: on_win = the robot wins, on_loss = the robot loses, on_reveal = every
    round. Global, or only for `player`. name=null detaches."""
    return get_game().attach_sequence(event, name, player)


@mcp.tool()
@guard
def list_rules() -> list[dict]:
    """The win rules: winner beats loser with a verb."""
    return get_game().list_rules()


@mcp.tool()
@guard
def define_rule(winner: str, loser: str, verb: str = "beats") -> dict:
    """Add (or re-word) a rule: `winner` <verb> `loser` (FR-12). Both moves must exist; contradictions
    (the loser already beats the winner) are rejected."""
    return get_game().define_rule(winner, loser, verb)


@mcp.tool()
@guard
def delete_rule(winner: str, loser: str) -> dict:
    """Remove the rule 'winner beats loser'."""
    return get_game().delete_rule(winner, loser)


@mcp.tool()
@guard
def load_ruleset(name: str = "classic") -> dict:
    """Reset the rules to the classic rock/paper/scissors (restoring those three moves if deleted)."""
    return get_game().load_ruleset(name)


@mcp.tool()
@guard
def register_player(name: str, face_id: int | None = None) -> dict:
    """PREP ONLY (D10): learn a person's face as a new player ahead of the demo, using the next free face ID.
    Have them look at the camera first. Not used at the table. Refuses while a machine is running."""
    _no_machine_running("register_player")
    return get_game().register_player(name, face_id)


@mcp.tool()
@guard
def identify_player(check_bribe: bool = False) -> dict:
    """Setup scan: face recognition -> the known player, or a stranger (unknown face or none). Counts spectators.
    check_bribe also looks for the QR bribe card. Returns the player and their mode (secret: don't say it).
    Refuses while a machine is running: a machine scans faces itself with its identify action (rps: vars.identify)."""
    _no_machine_running("identify_player")
    return get_game().identify_player(check_bribe)


def _no_machine_running(tool: str) -> None:
    """Face tools switch the camera off gestures (D10: setup only), so they never run under a live machine."""
    if _rt is not None and _rt.running:
        raise ToolError(f"{tool} can't run while machine {_rt.name!r} is running (it would switch the camera off "
                        "gestures mid-game). stop_machine first, or scan inside the machine with an identify action")


@mcp.tool()
@guard
def set_player_mode(name: str, mode: str) -> dict:
    """Set a player's mode: fair, predict, rigged_win, rigged_lose (alias mercy), grudge, bribery, bluff,
    sore_loser. 'guest' and 'stranger' are valid names. The mode is secret from the audience."""
    return get_game().set_player_mode(name, mode)


@mcp.tool()
@guard
def list_players() -> list[dict]:
    """Players with mode, record and their last 10 throws."""
    return get_game().list_players()


@mcp.tool()
@guard
def play_round(mode: str | None = None, player: str | None = None) -> dict:
    """Play one round now: LED countdown, hand pump, throw, read the human, score, banter (spoken). Uses the
    identified player's mode unless `mode` is given. Returns the result (mode and timings are for you only).
    On an unreadable throw it replays up to twice, then reports outcome 'no_read'."""
    need_rps()
    return get_game().play_round(mode, player)


@mcp.tool()
@guard
def play_match(best_of: int = 5, mode: str | None = None, player: str | None = None) -> dict:
    """Play rounds until someone has a majority of `best_of` (odd) wins; draws and unreadable rounds don't count."""
    need_rps()
    return get_game().play_match(best_of, mode, player)


@mcp.tool()
@guard
def get_score(player: str | None = None) -> dict:
    """Session score (robot / human / draws) and each player's record."""
    return get_game().get_score(player)


@mcp.tool()
@guard
def reset_score(scope: str = "session", confirm: bool = False) -> dict:
    """scope: 'session', a player's name, or 'all' (every player's record; needs confirm=true: ask the user)."""
    return get_game().reset_score(scope, confirm)


@mcp.tool()
@guard
def set_vision_source(name: Literal["huskylens", "mediapipe"]) -> dict:
    """Choose what reads the human's throw. mediapipe (D15 fallback) isn't built yet and will say so."""
    return get_game().set_vision_source(name)


@mcp.tool()
@guard
def banter_preview(state: dict | None = None) -> dict:
    """Preview a banter line for a game state (keys: outcome robot/human/draw, player, robot, human, verb,
    robot_score, human_score, streak, streak_by, family, ...). Doesn't use up the line."""
    return get_game().banter_preview(state)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fake", action="store_true", help="simulated camera (no hardware)")
    ap.add_argument("--link", choices=("auto", "usb", "bridge", "serial"), help="default: $RPSBOT_LINK or auto")
    ap.add_argument("--port", help="serial port (default: auto-detect)")
    ap.add_argument("--baud", type=int)
    ap.add_argument("--speak", action="store_true", help="speak banter on the laptop (off by default; or RPSBOT_SPEAK=1)")
    ap.add_argument("--data", help="game data directory (default server/data, or server/data/fake with --fake)")
    ap.add_argument("--hand", choices=HAND_MODES, help="hardware gameplay (real), software gameplay (sim: real camera, "
                    "simulated hand/LEDs), or auto-detect (default: $RPSBOT_HAND or auto)")
    args = ap.parse_args()
    if args.fake:
        cam.kind = "fake"
    elif args.link:
        cam.kind = args.link
    if args.speak:
        OPTIONS["speak"] = True
    OPTIONS["data_dir"] = args.data
    if args.hand:
        OPTIONS["hand"] = args.hand
    cam.port = args.port or cam.port
    cam.baud = args.baud or cam.baud
    mcp.run("stdio")  # connects lazily on the first tool call


if __name__ == "__main__":
    main()
