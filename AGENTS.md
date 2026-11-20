# AGENTS.md

## Source of truth

**[`docs/rps-robot-design-doc.md`](docs/rps-robot-design-doc.md) is the source of truth for this project.**

- Read it before you design, wire, or code anything. Decisions (D1–D19), the pin map (§8.2), power rules (§8.3), repo layout (§9.1), the XIAO command protocol (§9.2) and the tests (§13) all live there.
- If anything else conflicts with it (handoff notes, plans, code comments, the PDF copy), the design doc wins.
- `docs/rps-robot-design-doc.pdf` is an export of the `.md`. Edit the `.md`, not the PDF.
- If the code needs to differ from the doc, update the doc (and its revision history, §18) in the same change, or flag the conflict to the user. Don't let them drift apart silently.

## Status

[`docs/TODO.md`](docs/TODO.md) is the living checklist of everything left to do. Check it at the start of a session, and update it in the same change as the work: tick items with a date and a one-line result, and add new items as they come up.
