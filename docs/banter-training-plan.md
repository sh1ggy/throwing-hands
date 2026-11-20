# D16 Banter Training Plan

Source of truth: `resources/rps-robot-design-doc.md` (v0.8), §D16. Where this plan adds detail the doc doesn't specify, it is marked **[plan decision]**. If the two conflict, the design doc wins.

## 0. Decisions confirmed (2026-11-20)

| Topic | Decision | Doc reference |
|---|---|---|
| Scope | Full D16: dataset, banter bank (B, baseline), LoRA distilled model (C, stretch) | D16 "Selected: B as the baseline, C as the stretch replacement" |
| Persona | Smug sore-loser AI, family-friendly | D10 modes (rigged-win, sore loser, grudge, mercy) |
| Data generation | Script calls the Anthropic API with the user's key, unattended | D16 pipeline step 1 |
| Training | Unsloth LoRA on a free Colab T4, GGUF Q4, `llama-cpp-python` on CPU | D16 pipeline steps 2–3 |

## 1. Goal and success criteria

The goal is one banter line per round, generated offline from game state (FR-15). Banter is spoken on the laptop speaker (D12-A), and the engine produces it at the end of each round (§5 step 5).

| Check | Pass criterion | Source |
|---|---|---|
| T11 Banter | 50 random states: a line is returned in ≤ 2 s, with no repeats within 10 rounds | §13 |
| Offline | No network at demo time | FR-15, D16 |
| Fallback | The bank answers if the model is missing, slow or errors | D16 step 4, R8 |
| No admin | Everything installs as a user install on Windows | NFR-07, A6 |

## 2. Persona spec [plan decision]

**RPS-Bot: smug sore-loser AI.**

- **When winning:** smug, superior and theatrical. It treats every win as proof of machine supremacy.
- **When losing:** never admits it fairly. It blames glare, lag, "calibration", the spectator or the backdrop, and demands a rematch (D10 sore loser).
- **Rigged or bribed rounds:** it hints at the rigging with a wink but never confesses. Bribery is "a generous donation to robotics".
- **Mercy mode:** loses theatrically and graciously, aiming at kids, judges and birthdays.
- **Hard rules:**
  - family-friendly, with no swearing, insults about appearance or anything a kid shouldn't hear
  - 1–2 sentences and at most about 25 words, so TTS finishes before the next countdown
  - player names only as the `{player}` token
  - no emojis, because it's spoken aloud

Sample target lines (to be included in the generation prompt as style anchors):
- *"Rock beats scissors, and silicon beats {player}. Write that down."*
- *"That didn't count. There was glare. There is always glare."*
- *"Three in a row, {player}. I'd feel bad, but I wasn't built with that module."*

## 3. Game-state schema [plan decision, derived from the D16 field list]

D16 lists: moves, outcome, streak, score, player, mode, rigged/bribed flags, optional finger-curl features. Concrete schema:

```json
{
  "event": "round_result",
  "robot_move": "rock",
  "human_move": "scissors",
  "verb": "crushes",
  "outcome": "robot_win",
  "streak": 3,
  "score": {"robot": 3, "human": 1},
  "round": 4,
  "best_of": 5,
  "player": "{player}",
  "mode": "rigged_win",
  "rigged": true,
  "bribed": false,
  "spectator": false,
  "finger_curl": [0.1, 0.8, 0.9, 0.2]
}
```

| Field | Values | Notes |
|---|---|---|
| `event` | `round_result`, `misread`, `match_end`, `setup_greeting` | `misread` is the "glare" rematch from D10 rigged-win; `match_end` covers best-of results |
| `robot_move` / `human_move` | `rock`, `paper`, `scissors`, `custom`, `nothing`, `unknown` | Custom moves come from `moves.json` (D11). The sampler invents a few custom names so the model generalises |
| `verb` | From `rules.json` (D11), e.g. `crushes` | |
| `outcome` | `robot_win`, `human_win`, `draw`, `void` | |
| `streak` | Signed int; + is robot streak, − is human streak | Bucketed for the bank: ≤−3, −2, −1, 0, 1, 2, ≥3 |
| `mode` | `fair`, `predict`, `rigged_win`, `rigged_lose`, `grudge`, `bribery`, `sore_loser`, `trash_talk_bluff`, `rules_lawyer`, `spectator_blame` | All D10 modes |
| `finger_curl` | Optional, 4 floats, thumb/index/middle/ring+little | D15 says these can come from MediaPipe live. Enables lines like "half-hearted scissors" |

`player` is always the literal `{player}` token, and the runtime substitutes the real name from `players.json`. This keeps lines reusable and stops the model inventing names **[plan decision]**.

## 4. Pipeline

### Step 1: Generate the dataset (`tools/gen_banter.py`)

- **Sampler:** builds random states, stratified across mode × outcome × event.
  - It oversamples the core demo modes: fair, rigged-win, mercy, grudge and bribery (D10 "Selected").
  - Stretch modes get a smaller share.
  - Only about 30% of states carry `finger_curl`.
- **Target size:** about 1,000 states × 3 lines each, which gives about 3,000 examples. That's the top of D16's 1–3k range, and the 3 variants help with T11's no-repeat rule.
- **API calls:** each call batches about 20 states and returns strict JSON (`[{"id":…, "lines":[…]}]`). That's about 50 calls in total, well inside the 6 h Gantt slot (s4).
- **Configuration:**
  - The key comes from the `ANTHROPIC_API_KEY` env var. It never goes in the repo.
  - The model comes from `BANTER_MODEL`, defaulting to a current Claude Sonnet.
- **Resilience:**
  - It appends to `server/data/banter/raw.jsonl` and can be resumed: it skips state ids already done, and retries with backoff.
  - It logs a cost and progress summary.
- **Validation pass** (`tools/clean_banter.py`), which drops any line that:
  - is over 25 words
  - contains emojis, a banned-word list, or a literal real name
  - is a near-duplicate (normalised text plus fuzzy match)
  - contradicts the state, e.g. claims a win on `human_win` (mode-aware, since a sore loser *may* deny losing)
- **Output:** `server/data/banter/dataset.jsonl`, a 90/10 train/eval split by state id (D16 step 2).

### Step 2: Build the banter bank (D16-B, baseline)

- **Key:** `(event, mode, outcome, streak_bucket, rigged, bribed, match_point)`.
- **Lookup order:** exact key first. If there's no match, relax fields in a fixed order (finger_curl, then match_point, then streak bucket, then rigged/bribed, then mode → `fair`). A guaranteed generic bucket per outcome comes last.
- **No-repeat:** a ring buffer of the last 10 lines served (T11).
- **Output:** `server/data/banter/bank.json`, built from the full cleaned dataset. The eval split is only held out from training.

### Step 3: Fine-tune (D16-C, stretch; Colab notebook `tools/banter_lora.ipynb`)

- **Base model [plan decision]:** Qwen2.5-0.5B-Instruct first, for the fastest CPU inference. Try 1.5B only if the 0.5B quality is poor and latency allows. Both fit D16's 0.5–1.5B range.
- **Format:** chat template with a system prompt (short persona), the user message (compact state JSON) and the assistant reply (one line).
- **Unsloth LoRA settings:** 4-bit base, r=16, alpha=16, 2–3 epochs, max seq len 256. That should be minutes on a T4, matching D16's "minutes to tens of minutes".
- **Eval on the 10% held-out set:**
  - format and length pass rate
  - banned-word hits
  - repeat rate over 10 consecutive generations
  - a quick manual read of 30 samples

### Step 4: Export (D16 step 3)

- Merge LoRA and convert to GGUF `q4_k_m` in the notebook, then download to `server/data/banter/banter-q4.gguf`. Expected size is about 400 MB for 0.5B.
- `.gitignore` the GGUF and `raw.jsonl`. Commit `bank.json` so the baseline works from a fresh clone **[plan decision]**.

### Step 5: Engine integration (`server/banter.py`, D16 step 4)

- `banter(state) -> str`:
  - Start model generation in a background thread at reveal, so it overlaps the result LED animation (D16-C).
  - Wait up to the deadline, then fall back to the bank.
  - Substitute `{player}` afterwards and record the line in the no-repeat buffer.
- If the model output fails validation (length, banned words, empty), use the bank.
- `BANTER_BACKEND=bank|model|auto` env switch. Default is `auto`, which uses the model if the GGUF is present and the bank otherwise.
- MCP tool `banter_preview(state)` (§9.3) returns the line and which backend produced it.

### Step 6: Test

| Test | What | Pass |
|---|---|---|
| T11 | 50 random states through `banter()` on the demo laptop | ≤ 2 s each; no repeats within 10 |
| Unit: sampler | All modes/events produced; schema valid | 100% |
| Unit: bank | Every possible key resolves to a line | No empty result |
| Unit: fallback | Model missing, slow (forced timeout) or bad output | Bank line returned |
| Latency | GGUF on the actual Windows laptop's CPU | p95 ≤ 2 s; otherwise ship the bank only (R8) |

## 5. Schedule (from §12 Gantt; placeholder dates)

| When | Task | Gantt id |
|---|---|---|
| Fri ~01:30, 6 h | Run `gen_banter.py` unattended overnight | s4 |
| Sat 08:30 | Run the cleaner and build `bank.json` (minutes) | – |
| Sat 12:00, 150 min | Colab fine-tune, export, latency test, wire up `auto` | s6 |
| Sat 13:00 | T11 inside the integrated test plan | i3 |

Owner: the teammate (software), per §11.1 "banter pipeline".

**Pre-event prep [plan decision]:** write and dry-run the scripts and notebook on about 50 states before kickoff. On the night, the only jobs are the full run and the training.

## 6. Issues and conflicts to resolve

1. **Gantt vs R11:** the Gantt puts "Banter bank + LoRA stretch" at 12:00–14:30, but R11 says stretch items run only after G3 (14:30). Proposal: build the bank in s6, and only start LoRA in s6 if G1/G2 have passed; otherwise defer it until after G3.
2. **Anthropic usage terms (D16 note):** check them before step 3 (fine-tuning on Claude outputs). This is a gate for the stretch only. The bank (B) doesn't involve training.
3. **`llama-cpp-python` without admin (NFR-07):** compiling it from source on Windows needs build tools. Verify on Friday that a prebuilt CPU wheel installs with `pip install --user` (the project publishes a CPU wheel index). If it doesn't, ship the bank only.
4. **Hackathon rules (A3):** confirm whether code and scripts may be written before kickoff, and whether the dataset may be generated before Friday night.
5. **API key and spend:** confirm the key is available on the teammate's machine overnight, and set a spending cap.
6. **TTS engine:** §9.3 lists `speak` (laptop TTS) but doesn't name an engine. The line length limit assumes about 2–3 words/s.

## 7. Deliverables

```
tools/gen_banter.py          state sampler + Claude API generator (resumable)
tools/clean_banter.py        validation, dedupe, train/eval split
tools/build_bank.py          bank.json builder
tools/banter_lora.ipynb      Colab: Unsloth LoRA, eval, GGUF export
server/banter.py             banter(state), bank lookup, model + timeout, no-repeat
server/data/banter/          dataset.jsonl, bank.json, (banter-q4.gguf, gitignored)
tests/test_banter.py         sampler, bank coverage, fallback, T11 harness
```
