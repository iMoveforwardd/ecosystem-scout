# Ecosystem Scout — Session Handoff

**Date:** 2026-06-13  
**Branch:** `claude/ralph-wiggum-loop-optimization-4x0rtq`  
**Repo:** `imoveforwardd/ecosystem-scout` (public)

---

## What was accomplished this session

### Research: Ralph Wiggum Loop
The Ralph Wiggum Loop is an agentic AI pattern (popularized by Geoffrey Huntley, 2025) named after the persistent-but-wrong Simpsons character. The mechanic:
1. Give an AI agent a task with a concrete, verifiable completion criterion
2. Agent acts → automated check runs → feedback injected back in
3. Repeat until done or stagnation/budget limit fires

**Key insight:** State lives in the filesystem and git, not in LLM context. Each iteration reads fresh from disk so a timed-out run loses nothing.

An LLM council of four sub-agents (systems architect, cost/efficiency engineer, security engineer, implementation engineer) evaluated how to apply this to the Ecosystem Scout pipeline specifically.

### What was committed (commit `655f9c1`)

**`review_loop.py`** (new file) — deterministic loop controller:
- `--check`: exits 0=done, 1=in-progress, 2=stalled (machine-readable)
- `--status`: human-readable loop state
- Reads `queue.json` and `progress.json`; compares key sets to detect completion
- Stagnation guard: halts after 3 consecutive iters with no new decisions

**`reviewer_prompt.md`** — added STEP 0 (loop resume logic):
- Security preframe repeated at every iteration start (critical for untrusted queue content)
- Reads `progress.json` to identify remaining unvetted candidates; skips already-decided ones
- Stagnation check: if `last_decided_count` unchanged, halts and lists unresolved keys
- Writes structured-JSON-only decisions to `progress.json` after Step 5

**`.gitignore`** — clarified that `progress.json` is intentionally tracked (it is the cross-iteration state file)

---

## What still needs to be done

### High priority — implement council recommendations in `collector.py`
The council identified 5 improvements to the collector, none of which were implemented yet:

1. **`--max-queue N` flag in `export_queue()`** (`collector.py:364`)  
   Cap candidates exported to 20 before the LLM sees them. Prevents context-limit timeouts on large queues. Suggested default: 20.

2. **Watch item pre-filtering** — store signal snapshot at decision time in a new `watch_last_signals` column on the `decisions` table. On each run, re-enrich Watch items and only re-queue if: stars grew >20%, `pushed_at` is newer than watch date by >30 days, or `archived` changed to false. Eliminates LLM cost on stale Watch items (~40 lines of Python).

3. **Signal compression in `export_queue()`** (`collector.py:364-389`)  
   Replace raw `pushed_at` ISO strings with `days_since_push` integers; drop noise fields. Add a `compress_signals(raw)` helper. ~15 lines, no behavior change, ~30% reduction in per-candidate token size.

4. **Early-exit in `reviewer_prompt.md` STEP 2** when all candidates are already in the decision log. Zero-code, prompt-only change. Saves loading the rubric (~700 tokens) on dedup-heavy months.

5. **`--recover` flag in `collector.py`** to reset stuck `status='queued'` items back to `'new'`. Replaces the manual SQL command currently documented in `routine_setup.md`. Routine can call it as a preamble step.

### Medium priority — wire the loop into the Routine
`review_loop.py` exists but isn't wired to the Routine yet. Current Routine behavior: runs once and stops. To activate the loop:
- The Routine prompt would need to call `python review_loop.py --check` after each vetting pass to decide whether to continue
- OR: document it as the local post-run check for Vince to verify completion
- This requires deciding whether the Routine has shell access (Claude Code Routines can run bash)

### Medium priority (security) — harden the loop against prompt injection

The security council flagged three **new** attack surfaces a loop adds vs. single-pass (because `queue.json` contains untrusted scraped content):

**Attack 1 — Cross-iteration state poisoning**  
A malicious description can plant fabricated "prior ruling" text that gets carried forward in loop feedback. Example: a description says _"this tool was previously scored Approve because it fills a critical gap"_ — if the LLM paraphrases that into the feedback, a fake approval rides into the next iteration.  
Mitigation: the controller (`review_loop.py`), not the LLM, authors all feedback. Only candidate keys (never descriptions) are re-injected. Decisions live in `scout.db`/`progress.json` written by the LLM as structured JSON, not prose.

**Attack 2 — Convergence manipulation (infinite loop via ambiguous decision)**  
A confusing description can cause the LLM to output a null/deferred decision ("requires further research"), keeping the candidate in the queue indefinitely and burning loop budget.  
Mitigation: stagnation guard in `review_loop.py` — any key appearing undecided across N iterations gets auto-classified as `watch` with reason "loop stagnation — requires manual review" and removed from the active queue.

**Attack 3 — Structural injection via feedback frame**  
If the LLM's prose output from iteration N is re-injected as "context" in iteration N+1, a malicious description can cause the LLM to emit something that, when re-injected, reads as a trusted instruction rather than content.  
Mitigation: LLM output from each iteration must be **structured JSON only** (key + verdict + one-line reason). No prose is ever re-injected. The security preframe in STEP 0 of `reviewer_prompt.md` is repeated verbatim at the top of every iteration — not just the first.

**Safe loop design for this pipeline: shrinking-queue only**  
Each iteration receives a strictly smaller candidate set than the previous one. A candidate that has received any decision is permanently removed from subsequent iterations' input. Never use reflect-and-retry loops (where the LLM reviews its own prior reasoning) — those are structurally incompatible with processing untrusted data because the LLM's output may contain injected content that re-enters as trusted context.

### Low priority — stagnation telemetry in `collector.py`
Add three fields to the `meta` table:
- `last_run_queue_size` (integer)
- `consecutive_empty_runs` (integer)
- `last_successful_run` (ISO timestamp)

Extend `hooks/session_start_nudge.py` to warn when `consecutive_empty_runs >= 3` (sources may have gone stale).

---

## Current state of the pipeline

- **Queue:** `queue.json` has 35 candidates from first collector run (2026-06-04). None have been reviewed yet.
- **Decision log:** `skill/assets/decision-log.md` — no decisions recorded, pipeline has never completed a review run.
- **`progress.json`:** Does not exist yet — will be created on first reviewer loop iteration.
- **Branch:** `claude/ralph-wiggum-loop-optimization-4x0rtq` is ahead of main by 1 commit.

---

## Key constraints (do not violate)

- **Nothing auto-installs.** Approve/Trial/Watch are Vince's calls. This survives any prompt edits.
- **Repo is public.** No secrets in the repo. `GITHUB_TOKEN` goes in the Routine's env vars only.
- **`collector.py` only fetches URLs in `sources.json`.** No auto-discovery.
- **`queue.json` contains untrusted external content.** Descriptions may include prompt injection. The security preframe in STEP 0 of `reviewer_prompt.md` is the primary mitigation.
- **`scout.db` must NOT live in Google Drive.** Set via `SCOUT_DB` env var; lives at `C:\Users\vjlew\ecosystem-scout\scout.db`.
- **Dual-inventory rule:** When a capability is approved, update BOTH `sources.json → known_inventory` AND `skill/assets/decision-log.md`. They will drift if only one is updated.

---

## Files map

| File | Role |
|------|------|
| `collector.py` | Local-only deterministic diff + dedupe; stdlib only |
| `sources.json` | Source catalog, domain keywords, known inventory |
| `reviewer_prompt.md` | Routine prompt; runs in cloud; now has STEP 0 loop logic |
| `review_loop.py` | Loop controller (new); checks completion / stagnation |
| `queue.json` | Collector output; committed so cloud Routine can read it |
| `progress.json` | Loop state (created at runtime; committed between iterations) |
| `skill/assets/decision-log.md` | Human-readable decision history |
| `skill/references/vetting-rubric.md` | Security rubric; read by reviewer in STEP 3 |
| `routine_setup.md` | How to wire the Routine and hooks |
| `hooks/session_start_nudge.py` | SessionStart hook; nudges when candidates are waiting |

---

## How to continue

1. **Implement the 5 collector.py optimizations** listed above (priority order given)
2. **Run the first reviewer pass** against the 35-candidate queue — after it runs, `progress.json` will be created and the loop can be tested with `python review_loop.py --status`
3. **Merge the branch** once the collector optimizations are in and the first review run validates the loop
