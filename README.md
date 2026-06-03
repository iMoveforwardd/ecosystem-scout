# Ecosystem Scout

Automated capability-scouting pipeline for Vince's Claude ecosystem. Runs
monthly (via a Cloud Routine) to discover new Skills, MCP servers, and CLIs,
vet them against a security-first rubric, and produce a brief for approval.

## How it works

```
monthly cron ──▶ Routine (cloud) runs reviewer_prompt.md
                     │ 1. runs
                     ▼
  ┌──────────────┐  queue.json  ┌──────────────┐
  │ collector.py │─────────────▶│ LLM Reviewer │
  │  → scout.db  │◀──decisions──│ (the skill)  │
  └──────────────┘  (rej/dupe)  └──────┬───────┘
                                       ▼
SessionStart hook ─ nudge        Review Brief
("N waiting")                    (you approve)
```

- **Collector** — diff engine, no LLM, no pip install. Scans the MCP Registry +
  10 awesome lists, dedupes against known inventory, writes `queue.json`.
- **Reviewer** — the `ecosystem-scout` skill vets the queue against the security
  rubric and produces a brief. You confirm Approve/Trial/Watch.
- **Hook** — SessionStart nudge; silent when the queue is empty.

## Quick start

```cmd
:: First run — establish baseline (queues little by design)
python collector.py --db "%SCOUT_DB%" --config sources.json --queue "%SCOUT_QUEUE%" --since-days 90

:: Every run after — surface only what's genuinely new
python collector.py --db "%SCOUT_DB%" --config sources.json --queue "%SCOUT_QUEUE%" --enrich --github-token "%GITHUB_TOKEN%"

:: Status check
python collector.py --db "%SCOUT_DB%" --status

:: Record a decision
python collector.py --db "%SCOUT_DB%" --decide "<candidate-key>" approve "installed and working"
```

## Setup

1. Copy `.env.example` to `.env`, fill in `GITHUB_TOKEN` (fine-grained PAT, read-only public repos).
2. Create `C:\Users\vjlew\ecosystem-scout\` (outside Google Drive — no sync conflicts).
3. Wire the SessionStart hook: merge `hooks/settings_snippet.json` into `~/.claude/settings.json`.
4. Push this repo to a private GitHub remote, then create a Routine in Claude Code pointing at
   `reviewer_prompt.md`. Set `GITHUB_TOKEN`, `SCOUT_DB`, `SCOUT_QUEUE` in the Routine's cloud env.
5. See `routine_setup.md` for full wiring instructions.

## Skill

The reviewer skill lives globally at `C:\Users\vjlew\.claude\skills\ecosystem-scout\`.
Invoke it manually with `/ecosystem-scout` or say "run my capability review."

## Security notes

- `GITHUB_TOKEN` never committed to this repo — Routine cloud env only.
- `scout.db` and `queue.json` are gitignored. They live at `C:\Users\vjlew\ecosystem-scout\`.
- The collector only fetches URLs listed in `sources.json` — no dynamic URL discovery.
- Descriptions in `queue.json` are untrusted external content. The reviewer prompt warns the LLM.
- Nothing auto-installs. Approve/Trial/Watch require user confirmation.
