# AGENTS.md

## Start here

**The Tanda 2026 demo is over and the repo is frozen at tag `tanda-2026-final` (branch `tanda-2026-presentation`).**
Read [`docs/handoff-2026-11-21-tanda-final.md`](docs/handoff-2026-11-21-tanda-final.md) first: where things stand, the reasoning
behind the build, the gaps in priority order and the state that lives outside git. Then read the retro,
[`docs/demo-retro-2026-11-21.md`](docs/demo-retro-2026-11-21.md). Don't push to the frozen branch or move the tag.

## Source of truth

**[`docs/rps-robot-design-doc.md`](docs/rps-robot-design-doc.md) is the source of truth for this project.**

- Read it before you design, wire, or code anything. Decisions (D1–D19), the pin map (§8.2), power rules (§8.3), repo layout (§9.1), the XIAO command protocol (§9.2) and the tests (§13) all live there.
- If anything else conflicts with it (handoff notes, plans, code comments, the PDF copy), the design doc wins.
- `docs/rps-robot-design-doc.pdf` is an export of the `.md`.
- **Agents don't edit the design doc** (the `.md` or the PDF); the owner does. If the code needs to differ from the doc, list the
  conflict under "Design-doc issues to resolve" in `docs/TODO.md` and tell the user. Don't let them drift apart silently.

## Demo target

**[`docs/demo-plan.md`](docs/demo-plan.md) is the source of truth for the demo we're building towards:** the
two-minute run (RPS, "I'm bored", Claude invents and teaches itself a new game live), its principles, targets and
fallbacks. Check work against it when deciding what to build, cut or rehearse. It doesn't override the design doc on
how the robot is built; list any conflict under "Design-doc issues to resolve" in `docs/TODO.md`.

[`docs/state-machine.md`](docs/state-machine.md) explains how the server does it: games are state machines Claude
writes and patches over MCP.

## Status

[`docs/TODO.md`](docs/TODO.md) is the living checklist of everything left to do. Check it at the start of a session, and update it in the same change as the work: tick items with a date and a one-line result, and add new items as they come up.

## Working rules

Set by the owner during the hackathon (2026-11-20/21). They used to live only in one machine's assistant memory, so they're here now:

- **Don't edit the design doc** (see above). `docs/TODO.md`, `docs/hardware-log.md` and plan files are fine to edit.
- **No surprise audio.** Laptop speech stays opt-in (`--speak` / `RPSBOT_SPEAK=1`). Say so before any test that can make sound
  (TTS, the ISD1820 voice module, the relay click).
- **Commit straight to `main`**, no branches or PRs. Never commit `.env`, `server/data/` or firmware binaries.
- **Simulator passes aren't proof.** When you report status, say what ran on real hardware and in which Claude host, and what
  only ran on `--fake` / `--hand sim` / `FakeBridge`. See the retro, theme 8.
- **Two play modes, one engine:** hardware (`server.py --hand real`) and software (`--hand sim`); Claude Desktop is the MCP host;
  the audience view never shows a player's mode.
