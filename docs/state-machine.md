# The state-machine MCP

_Built 2026-11-21. Not in the design doc yet: see "Design-doc issues to resolve" in [`TODO.md`](TODO.md).
The demo this serves: [`demo-plan.md`](demo-plan.md)._

The MCP server doesn't know any games. It runs **state machines**, and Claude writes and edits them from chat. A game
is a JSON document; changing the rules is a patch to it, applied while it runs. RPS is just the biggest machine.

## Why a state machine

- **Rules are data.** Any game you can describe as "in this state, when this happens, do that and go there" can be
  played without new code or firmware.
- **Claude is out of the tight loop.** The server reads the camera, runs timers and fires transitions itself. A turn
  costs no model round trip, and an LLM never judges a read.
- **Live edits are safe.** Every machine and patch is validated before it's applied. A bad edit is rejected with a
  message, and the running game carries on untouched.

## Files

| File | What it is |
|---|---|
| [`server/machine.py`](../server/machine.py) | The language: validation, the safe expression evaluator, `{var}` templates, atomic patch ops, the machine store, `reference()` |
| [`server/machine_runtime.py`](../server/machine_runtime.py) | The runtime: one background thread that turns camera reads and timers into events and runs transitions |
| [`server/rps_lib.py`](../server/rps_lib.py) | Pure RPS logic (move pool, rules, choosing a move, rigged moves, modes, outcome, scores), moved out of `game.py` |
| [`server/rps_functions.py`](../server/rps_functions.py) | The `rps.*` function library machines call |
| [`server/machines/`](../server/machines/) | Built-in machines: `higher_lower.json`, `rps.json` |
| `<data>/machines/` | Machines Claude defines (a user copy of a built-in shadows it) |
| [`server/gestures.py`](../server/gestures.py) | Which camera ID means which gesture; the gesture swap |
| `tests/test_machine.py`, `tests/test_rps_machine.py` | Runtime tests, and RPS-as-a-machine twins of the engine tests |

## A machine

```json
{
  "name": "higher_lower",
  "description": "optional",
  "gestures": ["up", "down"],
  "settings": {"gesture_window_s": 0.4, "gesture_stable": 2},
  "vars": {"score": 0},
  "initial": "start",
  "states": {
    "start": {
      "on_enter": [ {"action": "..."} ],
      "timeout_ms": 15000,
      "final": false,
      "on": {
        "gesture:up": [ {"if": {"gt": ["$next", "$number"]}, "do": [ ... ], "to": "guess"}, {"to": "game_over"} ],
        "timer": {"to": "start"}
      }
    }
  }
}
```

- `gestures`: the camera gestures it needs (`nothing` is implied).
- `on_enter`: actions run in order on entry.
- `timeout_ms`: optional, a number or an expression; fires the `timer` event.
- `final: true`: the machine ends here.
- `on`: each event has an ordered list of handlers, and the **first one whose `if` holds wins**. A single handler can
  be given without the list.
- A handler with `to` leaves the state (`to` the same state re-enters it). Without `to` it stays.

The full worked example is [`server/machines/higher_lower.json`](../server/machines/higher_lower.json). Claude gets
the whole format from the `machine_reference` tool.

### Events

| Event | Fired when |
|---|---|
| `gesture:<name>` | The camera sees a gesture of the current set, confidently, in `gesture_stable` consecutive majority-vote windows of `gesture_window_s` (defaults 2 × 0.4 s). It doesn't fire again until a different gesture is seen, so a held thumb counts once. `none` and `unknown` never fire |
| `timer` | The state's `timeout_ms` has passed since it was entered |
| `enter` | Right after `on_enter`, so a state can branch on what `on_enter` computed |
| `poke` | Claude calls `poke(payload)`; the payload is `$payload` |

### Actions

One action key per object; any action can also carry `if` (skip unless true) and `soft` (warn instead of failing).

- **Output:** `speak`, `show` (camera screen), `led`, `led_countdown`, `pose`, `perform` (a defined move), `relax`,
  `play_sequence`, `relay`, `voice`
- **Flow:** `wait`, `countdown` (blocking, with hand pumps on absolute beat times), `emit` (an event for screens)
- **Data:** `set`, `add`, `append`, `random` (into a var, with `avoid`), `read` (a gesture read into a var)
- **Logic:** `call` a library function (`rps.*`), with `args`, result `into` a var

Decoration actions (speech, screen, LEDs) only warn if they fail. Any other action fault stops the machine and relaxes
the hand, and the error shows in `machine_state`.

### Expressions

Never `eval`'d. A value is a literal, a `$var` reference (dotted: `$read.gesture`, `$cfg.read_window_s`), or a one-key
operator object:

- **Comparison:** `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`
- **Logic:** `and`, `or`, `not`, `if`
- **Arithmetic:** `add`, `sub`, `mul`, `div`, `idiv`, `mod`, `min`, `max`, `abs`, `neg`
- **Lists and text:** `len`, `at`, `concat`, `lit`

`$event`, `$payload`, `$state`, `$prev` and `$cfg.<game setting>` are always in scope. Strings in text actions can use
`{var}` templates.

## Tools

| Tool | Does |
|---|---|
| `machine_reference` | The format, every action, the expression language and the function library. Claude reads it before writing a machine |
| `define_machine(machine, swap_gestures)` | Validate and save a machine. `swap_gestures=true` wipes the camera's gesture training and names the machine's gestures |
| `start_machine(name, vars, swap_gestures)` | Run it on the server's loop (stops any running one). Refuses if the camera lacks its gestures. With `swap_gestures=true` it swaps and doesn't start: train, then start again |
| `patch_machine(name, ops, goto)` | Atomic live edit: `set_state`, `remove_state`, `set_handler`, `add_handler`, `remove_handler`, `set_on_enter`, `set_timeout`, `set_vars`, `remove_vars`, `set_initial`, `set_settings`, `set_gestures`. The running machine keeps its state and vars |
| `machine_state(events)` | Status, current state, vars, timers, recent events and transitions, warnings |
| `poke(payload)` | Send the running machine a `poke` event |
| `stop_machine` | Stop now and relax the hand |
| `get_machine`, `list_machines`, `delete_machine` | Inspect and manage saved machines (built-ins can't be deleted) |
| `train_gesture`, `read_gesture`, `gesture_set`, `random_number` | Teaching and checking gestures; fair randomness outside a machine |

## Swapping games

1. `define_machine(new_game, swap_gestures=true)` (or `start_machine(name, swap_gestures=true)` for a saved one).
   The machine is validated **before** anything is wiped. The camera's classification data is wiped and the
   machine's gestures are named, in order, plus `nothing`.
2. `train_gesture` each gesture, including `nothing`. Classification labels the whole frame, so without an "empty"
   class every empty frame would read as a gesture.
3. `read_gesture` each to check (`confident: true`).
4. `start_machine(name)`.

Nothing is backed up. Going back to RPS is another swap plus training. `play_round` and `play_match` refuse while a
non-RPS gesture set is loaded.

## RPS as a machine

[`server/machines/rps.json`](../server/machines/rps.json) is the design doc's round (§9.4) and match as states:
`start → round_start → attempt → shoot → (replay →) reveal → report → hold → hooks → round_end → … → done`, with
`no_read` after too many replays. The parts that aren't rules (choosing a move per mode, predict's Markov model,
rigged counters, outcomes, banter, scores, face ID) are `rps.*` functions:

`begin_round`, `choose_move`, `bluff`, `shoot`, `read_throw`, `replay_line`, `rigged_move`, `reveal`, `outcome`,
`update_scores`, `banter`, `finish_round`, `no_read`, `hold_ms`, `match_banter`, `end_match`, `identify_player`,
`player_mode`

Start it with `start_machine('rps', vars={"mode": "fair", "player": "guest", "match": true, "best_of": 3})`.
Game settings come from the same `settings.json` as the engine, as `$cfg`.

**Parity with the engine.** Every behaviour test of the old engine has a machine twin (`tests/test_rps_machine.py`),
covering all modes, replays, hooks, NFR-09 relax and best-of-N. On the same seeds both pick identical moves and
outcomes. On the simulator: rigged-win reveal lag 0–1 ms (≤ 250 ms required), round ready 25–31 ms after shoot
(≤ 1500 ms). Real-camera numbers aren't measured yet.

`play_round` / `play_match` still run the old engine in `game.py`. It's the deterministic fallback, also tagged in git
as `rps-deterministic`.

## Limits (for now)

- One machine runs at a time; no sub-machines; `to` targets are fixed names.
- A running machine isn't restored after a server restart (definitions are saved; state and vars aren't).
- The gesture defaults add ~0.8 s per gesture on a real camera; tune per machine with `settings`.
- Everything is tested on the simulated camera only, not on real hardware or over stdio.
