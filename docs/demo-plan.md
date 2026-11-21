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
| 0:00–0:30 | **RPS, software mode.** A fair best of 3 against the robot | `server.py --hand sim`: the real HuskyLens reads you, the robot is simulated. `start_machine('rps', vars={match: true, best_of: 3})` (fallback: `play_match`) |
| 0:30–1:00 | **Change the rules: "I always win."** Then another best of 3, and the robot loses every round on purpose | Claude sets the player's mode to `rigged_lose` (`set_player_mode`, or `patch_machine` `set_vars` on the running game), then `start_machine('rps')`. The robot reads your throw, then throws the losing move |
| 1:00 | **"I'm bored of the whole game."** Said out loud | Nothing yet |
| 1:00–1:10 | **The prompt** (rehearsed, below) | Claude Desktop is the MCP host |
| 1:10–1:25 | **Claude invents the game.** It writes the rules as a state machine and swaps the camera to thumbs up/down | `machine_reference`, then `define_machine(…, swap_gestures=true)`: validated, camera wiped, gestures named `up`, `down`, `nothing` |
| 1:25–1:45 | **Teach it live.** "Show me thumbs up… thumbs down… now take your hand away." One test read of each | `train_gesture` ×3 (samples=8, interval_s=0.4, countdown_s=2), `read_gesture` ×2 |
| 1:45–2:05 | **Play higher or lower.** About 3 guesses; the robot gloats when you lose | `start_machine`; the server runs the game itself, Claude watches with `machine_state` |
| 2:05–2:10 | **Close.** "Two minutes ago it only knew rock paper scissors." | `stop_machine` |

Changed 2026-11-21 (user decision): the run builds up from the game, to the game's rules changed, to a whole new game. The
"I always win" beat replaces the thumbs-down-means-higher twist. Checked on the simulated camera: after a live mode change
mid-match (either route), the robot lost 6 rounds out of 6. Honest framing: "always win" switches on a mode RPS already has;
the invention is the new game. If the run is long, cut either RPS best of 3 to one round.
Presenter notes with the exact prompts: [`demo-cues.html`](../designs/demo-cues.html).

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
2. **Taught live.** The thumbs gestures are trained in front of the audience. The swap wipes the camera, and that's
   accepted. Saved models on the camera's SD card are **backups only** (decided 2026-11-21): RPS in slot 1 is loaded before
   the run, and up / down / nothing in slot 2 is loaded only if the live teach fails. Never load slot 2 in place of the live teach.
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

Every command for setup, the run and recovery, in order: [`demo-runbook.md`](demo-runbook.md).

- [ ] Undo the last run's "always win": `set_player_mode('guest', 'fair')`, and `delete_machine('rps')` if `list_machines` shows a
      user copy of `rps` (a `patch_machine` saves one)
- [ ] Restore RPS: the previous run's swap wiped it. `start_machine('rps', swap_gestures=true)` (sets the RPS names),
      then `husky_load_model(1)` and a quick read of each throw. Retrain only if slot 1 is missing or reads badly
- [ ] Same distance, plain backdrop, fill light as in rehearsal
- [ ] Decide on voice: laptop speech is **off unless** the server runs with `--speak` (or `RPSBOT_SPEAK=1`)
- [ ] Claude Desktop connected to the server; `list_machines` answers

## If something goes wrong (fallback ladder)

1. **Claude's machine fails validation:** the error says what's wrong; let Claude fix it once. Then the fallback:
   `start_machine('higher_lower', swap_gestures=true)` swaps the camera but does **not** start; train up / down /
   nothing, then `start_machine('higher_lower')`.
2. **A gesture reads badly:** retrain that one gesture once and make it part of the bit. If it's still bad,
   `husky_load_model(2)` (the saved up / down / nothing backup; the swap has already set up=1, down=2, nothing=3, so it
   matches). If up/down stay confused even then, switch shapes (e.g. thumbs up vs flat palm down).
3. **The machine runtime misbehaves:** stay on RPS with the old engine (`play_match`). Only before the thumbs swap:
   after it, `play_match` refuses until RPS is swapped back and retrained.
4. **The server itself is broken:** `git checkout rps-deterministic`, the tagged deterministic engine.

## Saved models (backups)

Train both sets on the final rig (position, backdrop, fill light; camera at 9600 on Auto Detect, `read_window_s` 0.5), save each to the camera's SD card, and
copy the card's files to the laptop by hand (the protocol can't read them back). A slot holds only the weights: the
gesture IDs come from the training order, so always train **RPS as rock=1, paper=2, scissors=3, nothing=4** and
**higher or lower as up=1, down=2, nothing=3**.

| Slot | Set | Train with | Bar before saving |
| --- | --- | --- | --- |
| 1 | rock / paper / scissors / nothing, ~40 samples each (equal counts) | `train_ui.py` (forget first, then 3 × 15 per class) | 30-throw test ≥ 85 % (G2) |
| 2 | up / down / nothing, ~45 samples each | `start_machine('higher_lower', swap_gestures=true)`, then `train_gesture` (samples=15) 3× per class | `gesture_check.py up down nothing -n 30` ≥ 85 % each |

Checked on the real camera 2026-11-21: save → forget → load restores the model; a load takes ~1.2 s.

## Open decisions

- Hardware or software mode for the thumbs game: does the printed hand react (thumbs pose, gloating), or screen and voice only?
- Voice on stage: laptop TTS (`--speak`) or text on screens only?
- Which laptop runs the demo, and what's on the venue display (see the screens plan in `designs/mcp-live-view/PLAN.md`)?
  Partly decided 2026-11-21: the live view shown is the chat panel (`show_game`); the plain-HTTP `/view` page is a backup
  only, kept open in a browser tab behind Claude Desktop.
- ~~Whether RPS opens through the machine or the old engine~~ Decided 2026-11-21: the machine (`start_machine('rps')`), so the
  live view shows it; `play_match` is the fallback.
- ~~Face scan in setup~~ Decided 2026-11-21: not in the demo. Faces are descoped (TODO "Descoped: faces").
