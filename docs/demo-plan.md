# Demo plan: swapping games over MCP

**This is the source of truth for the demo we're building towards.** When a choice about the demo comes up (what
to build next, what to cut, what to rehearse), check it against this page. The design doc
([`rps-robot-design-doc.md`](rps-robot-design-doc.md)) is still the source of truth for the robot itself. Where the two
disagree, list it under "Design-doc issues to resolve" in [`TODO.md`](TODO.md) rather than editing the design doc.

_Agreed 2026-11-21. How the server does it: [`state-machine.md`](state-machine.md)._

## The idea in one line

The robot isn't a rock-paper-scissors machine. It's a gesture-game machine whose rules are a state machine that
Claude rewrites from chat. RPS is only the warm-up: halfway through the demo we get bored and talk it into a game it
has never seen.

## The two minutes

| Time | Beat | What happens underneath |
|---|---|---|
| 0:00–0:40 | **RPS, software mode.** A couple of rounds against the robot | `server.py --hand sim`: the real HuskyLens reads you, the robot is simulated. `start_machine('rps', vars={match: true, best_of: 3})` (fallback: `play_match`) |
| 0:40 | **"Ok, I'm bored."** Said out loud: the audience should hear the game is over | Nothing yet |
| 0:40–0:45 | **The prompt** (rehearsed, below) | Claude Desktop is the MCP host |
| 0:45–1:00 | **Claude invents the game.** It writes the rules as a state machine and swaps the camera to thumbs up/down | `machine_reference`, then `define_machine(…, swap_gestures=true)`: validated, camera wiped, gestures named `up`, `down`, `nothing` |
| 1:00–1:20 | **Teach it live.** "Show me thumbs up… thumbs down… now take your hand away." One test read of each | `train_gesture` ×3 (samples=8, interval_s=0.4, countdown_s=2), `read_gesture` ×2 |
| 1:20–1:50 | **Play higher or lower.** About 3 guesses; the robot gloats when you lose | `start_machine`; the server runs the game itself, Claude watches with `machine_state` |
| 1:50–2:00 | **Close.** "Sixty seconds ago it didn't know this game." | `stop_machine` |

**Stretch, only if rehearsals leave time:** change the rules mid-game from chat ("now thumbs down means higher") with
one `patch_machine`. It's the clearest proof that the rules are data, but it costs ~10 s.

## The game: higher or lower

- The robot shows a number from 1 to 10 (on the camera screen, and said aloud if voice is on).
- Thumbs up = the next number is higher, thumbs down = lower.
- The next number is drawn by the server (`random` action, never equal to the current one), never picked by Claude.
- Right: +1 and play on. Wrong: the game ends and the robot announces the score.

The fallback [`server/fallback_machines/higher_lower.json`](../server/fallback_machines/higher_lower.json) is the reference answer and
the safety net. On stage Claude writes its own from the prompt. That's the point of the demo.

## The prompt (rehearse it word for word)

> Ok, I'm bored of rock paper scissors. Make up a new game: higher or lower, numbers 1 to 10, thumbs up means
> higher, thumbs down means lower. Teach yourself thumbs up and thumbs down, then let's play. Roast me if I lose.

Keep the wording fixed so the run is predictable. The tools are generic, so a different game idea still works. It's
just less rehearsed.

## Principles (what "in the spirit" means)

1. **Made up live.** Claude writes the game from the prompt. No pre-baked game file is loaded on stage unless
   something fails.
2. **Taught live.** The thumbs gestures are trained in front of the audience. No pretrained models, no SD-card slots.
   The swap wipes the camera, and that's accepted.
3. **Rules are data, not code.** The server knows no games. A game is a state machine; changing the rules is a patch.
   No code or firmware changes between games.
4. **Fair randomness.** Anything a result depends on comes from the server's randomness, not Claude.
5. **Claude out of the tight loop.** Once started, the server runs the game (reads, timers, transitions). Claude
   authors, starts, watches and edits it.

## Targets (what "ready" means)

- [ ] Swap and setup (define the machine, wipe, train 3 gestures, 2 test reads) in **≤ 35 s**, measured on the rig
- [ ] up / down / nothing each **≥ 85 %** on `tools/gesture_check.py up down nothing` (the D9 bar)
- [ ] A thumb registers in **≤ 1 s** of being shown (tune `gesture_window_s` / `gesture_stable`)
- [ ] Claude's own machine passes validation first time in **3 of 3** rehearsals with the prompt above
- [ ] The full two minutes run **3 times in a row** without touching the keyboard except to type the prompt

## Before every run

- [ ] Retrain RPS: the previous run's swap wiped it (`train_ui.py`, or `start_machine('rps', swap_gestures=true)` and train)
- [ ] Same distance, plain backdrop, fill light as in rehearsal
- [ ] Decide on voice: laptop speech is **off unless** the server runs with `--speak` (or `RPSBOT_SPEAK=1`)
- [ ] Claude Desktop connected to the server; `list_machines` answers

## If something goes wrong (fallback ladder)

1. **Claude's machine fails validation:** the error says what's wrong; let Claude fix it once. Then the fallback:
   `start_machine('higher_lower', swap_gestures=true)` swaps the camera but does **not** start; train up / down /
   nothing, then `start_machine('higher_lower')`.
2. **A gesture reads badly:** retrain that one gesture once and make it part of the bit. If up/down stay confused,
   switch shapes (e.g. thumbs up vs flat palm down).
3. **The machine runtime misbehaves:** stay on RPS with the old engine (`play_match`). Only before the thumbs swap:
   after it, `play_match` refuses until RPS is swapped back and retrained.
4. **The server itself is broken:** `git checkout rps-deterministic`, the tagged deterministic engine.

## Open decisions

- Hardware or software mode for the thumbs game: does the printed hand react (thumbs pose, gloating), or screen and voice only?
- Voice on stage: laptop TTS (`--speak`) or text on screens only?
- Which laptop runs the demo, and what's on the venue display (see the screens plan in `designs/mcp-live-view/PLAN.md`)?
- Whether RPS opens through the machine (`start_machine('rps')`) or the old engine (`play_match`). The machine is the
  story; the engine is the fallback.
