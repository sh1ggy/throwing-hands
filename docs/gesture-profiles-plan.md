# Plan: saved gesture profiles (swap RPS ⇄ higher-lower without retraining)

_Drafted 2026-11-21. Not in the design doc. It extends §9.3's `husky_save_model` / `husky_load_model` and D9's gesture IDs.
If it's adopted, the doc needs a line under §9.3/D9 (proposed in `TODO.md` → "Design-doc issues to resolve")._

## The problem

Both games use the camera's **one** Object Classification model. Today a swap is destructive:

- `start_machine('higher_lower', swap_gestures=true)` → `swap_gestures()` → `hl.forget()` wipes the camera, renames
  `gestures.json` to `{up:1, down:2, nothing:3}`, and you retrain from zero.
- Going back to RPS is another wipe plus a full retrain (rock, paper, scissors and nothing at ~40 samples each, then the 30-throw test).
  That doesn't fit a 2-minute demo.
- `gestures.py` says "Nothing is backed up" in so many words.

The camera can already fix this. Firmware 0.5.1b saves and loads the **current algorithm's** learned data to and from numbered SD-card slots
(0x32 / 0x33), and `HuskyLens.save_model(slot)` / `load_model(slot)` plus the `husky_save_model` / `husky_load_model` tools are already wired.
`FakeCamera` keeps saved models per (algorithm, slot). What's missing is the link between a gesture set and its slot, and a swap that loads instead of wiping.

## The idea: a "gesture profile"

A profile is a gesture set plus where its trained model lives, saved on the laptop:

```jsonc
// <data>/gesture_profiles.json
{
  "active": "rps",
  "profiles": {
    "rps":          {"slot": 1, "ids": {"rock": 1, "paper": 2, "scissors": 3, "nothing": 4},
                     "saved_at": "2026-11-22T09:40:00", "samples": {"rock": 40, "paper": 40, "scissors": 40, "nothing": 40},
                     "last_test": {"accuracy": 0.9, "n": 30, "at": "…", "run": "runs/…_test.jsonl"}},
    "higher_lower": {"slot": 2, "ids": {"up": 1, "down": 2, "nothing": 3}, "saved_at": "…", "samples": {…}, "last_test": null}
  }
}
```

- **The camera model** (the weights) lives on the SD card at `slot`. **The ID → name map and the metadata** live in this file.
  They're useless apart, so they're always saved together.
- The profile name is the gesture set's name, which is the machine name `swap_gestures` already uses (`rps`, `higher_lower`).
- Slots are assigned once per profile (the next free one, starting at 1) and never reused. **Slot 0 is kept for faces** (Face Recognition is a separate algorithm with its own slots; see the open question below).
- `gestures.json` stays the "what's on the camera right now" file. Activating a profile rewrites it, so `need_rps()`, `machine_reader` and `majority_read` don't change.

## Changes, in order

### 1. `server/gestures.py`: profiles in the registry
- Add `Profiles` (or extend `GestureSets`) with `load/save` (the same atomic tmp → replace as today), `get(name)`, `slot_for(name)`
  (assigns the next free slot), `record_save(name, ids, samples)`, `record_test(name, result)` and `activate(name)` (copies the ids into
  `GestureSets` and saves `gestures.json`).
- Pure file logic, no camera. Unit-test it in `tests/test_gestures.py`.

### 2. `server/server.py`: two new tools, and a swap that loads instead of wiping
- **`save_gestures(name=None)`**: switch to classification, `hl.save_model(slot)` for the active set, then record ids, samples and time.
  Refuse if the camera's learned-ID count is lower than the set's highest ID (the trap from the 02:40 retrain, when scissors had 0 samples).
- **`load_gestures(name)`**: switch to classification, `hl.load_model(slot)`, then `activate(name)`, re-send the names with `husky_set_name` so the
  camera's screen matches, and show the profile name with `hl.text`. Return the profile's metadata, including last_test, so Claude can say how good it is.
- **`list_gesture_profiles()`**: every profile with its slot, gestures, saved_at and last accuracy, plus which one is active.
- **`gesture_plan()` / `swap_gestures`** get this order:
  1. The camera already holds the machine's gestures: do nothing (as today).
  2. A saved profile matches the machine's `gestures`: **`load_gestures` and carry on.** `start_machine` then starts in the same call, with no `swap_gestures=true` needed.
  3. No profile: today's wipe + retrain path. Before wiping, **auto-save the outgoing set if it has a profile slot** (cheap insurance against losing a good RPS model to a stray swap).
- Bump `load_model`'s timeout from 3 s and measure it on the real camera (see "Verify on hardware").

### 3. Record test results against the profile
- When a 30-throw test finishes (in `train_ui.py`, or a future MCP test tool), `record_test(active, result)` stores accuracy, n and the run-log path.
  That keeps "which dataset/model is this, and how good is it" next to the model itself.
- Tag every `runs/*.jsonl` log with the active profile name (one field in `runlog.py`'s header), so runs can be grouped per set later.

### 4. `tools/train_ui.py`: make it profile-aware (small)
- Today it's hard-coded to rock/paper/scissors/nothing (`CLASSES`, `GESTURES`, the colours and the HOW strings). Read the classes from the active profile
  (`--profile higher_lower`), keeping RPS as the default with its current colours and hints. Higher-lower falls back to neutral colours and the names as hints.
- Replace the raw slot number in Save/Load with **"Save as ‹profile›"** / **"Load ‹profile›"**, both going through the same `gestures.py` functions as the MCP tools, so both tools write one shared `gesture_profiles.json`.
- If this is too much before the demo, skip it: train higher-lower through Claude with `train_gesture`, then call `save_gestures()`.

### 5. Tests (simulated, on `FakeCamera`)
- `tests/test_gestures.py`: slot assignment, record and activate round-trip, atomic save, bad names.
- `tests/test_machine.py` (or a new `test_profiles.py`): train RPS → `save_gestures` → swap to higher_lower (auto-save, wipe) → train up/down →
  `save_gestures` → `start_machine('rps')` **loads slot 1 and starts with no retrain** → `start_machine('higher_lower')` loads slot 2.
  Check `gestures.json` and `FakeCamera.learned` after each step.
- A `load_model` failure (empty slot → `UNSUPPORTED` on the fake) leaves `gestures.json` untouched and returns a clear "retrain or swap" error.

## Verify on hardware (before relying on it)

These are the unknowns. Each is one quick check through `husky_save_model` / `husky_load_model` on the real camera:

1. **SD card present and readable.** The camera needs a FAT32 micro-SD. No card means save fails and swapping falls back to retraining.
2. **Save/load really work over the XIAO at 9600 baud** (0.5.1b advertises "WROTE KNOWLEDGE DATA TO SD" / "LOADING KNOWLEDGES"; we've only checked learn, forget, names and text).
3. **The load restores accuracy:** save RPS, forget, load, then rerun a 10-throw check and compare.
4. **How long a load takes** (sets the timeout and tells us whether a mid-demo swap is a pause or a wait).
5. **Slots are per algorithm:** saving Classification slot 1 mustn't overwrite the faces. Check by saving faces and gestures to the same slot number and loading both back.
6. **Names:** does a load restore custom names, or do we re-send them (the plan re-sends anyway)?

## Demo flow once this lands

```
prep (night before):  train RPS → test ≥ 85 % → save_gestures()          → slot 1
                      start_machine('higher_lower', swap_gestures=true)  (auto-saves RPS, wipes)
                      train up/down/nothing → save_gestures()             → slot 2
on stage:             start_machine('rps')            → loads slot 1, plays
                      start_machine('higher_lower')   → loads slot 2, plays
```

## Out of scope / later
- Several versions of one set (e.g. `rps@2026-11-22`). Re-saving overwrites the slot for now. Versioning can later be just more slots plus a `versions` list.
- Copying SD files to the laptop. The protocol can't read them back, so the laptop only holds the map and metadata; the card holds the weights. **Back up the card's files by hand** after the final training.
- MediaPipe (D15) profiles would be a different backend (a local model file per profile). The profile file's shape leaves room for a `"backend"` field.
