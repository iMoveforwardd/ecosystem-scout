# Wiring the Automated Trigger

Two mechanisms work together: **Option A** (Routine) is the engine that runs
monthly and hands you a brief; **Option C** (SessionStart hook) is the safety
net that reminds you when candidates are waiting. Wire both.

---

## Option A — Claude Code Routine (primary: fully autonomous)

A **Routine** is a saved prompt + repo + connectors + environment that runs on
Anthropic's cloud infrastructure on a schedule, a webhook, or a GitHub event —
your laptop can be closed.

Setup:
1. Push this repo to a **private** GitHub remote (see `git init` instructions below).
2. In Claude Code, go to **Routines** and create a new Routine:
   - Point it at this repo (`AI Engineering/ecosystem-scout/`)
   - Set the prompt file: `reviewer_prompt.md`
   - Set `GITHUB_TOKEN` via **Claude Code → Settings → Cloud Environment** (credential
     vault — stored securely, never committed to the repo, injected at runtime).
     Use a fine-grained PAT scoped to public repos read-only.
     The collector's `--github-token "$GITHUB_TOKEN"` picks it up automatically.
   - Set the trigger: monthly cron, e.g. `0 9 1 * *` (9am on the 1st)
   - Optionally wire a webhook `/fire` endpoint for on-demand runs
3. Each run creates a session you can open and continue to confirm decisions.

Security constraint: `GITHUB_TOKEN` goes in the Routine's cloud env config only.
It must never be committed to this repo.

Note: Routines are in research preview as of mid-2026. Confirm the exact setup
steps in the Claude Code docs when wiring — request/response shapes may shift.

---

## Option C — SessionStart hook (safety net — wire today)

This does **not** scan. It fires when you open a Claude Code session and prints
a one-line reminder if candidates are waiting or a scan is overdue. Use it
alongside Option A so need-driven (pull) review works too.

Wire it via `hooks/settings_snippet.json` — see the SessionStart hook section
in that file. The hook command is already set to the correct Windows path.

---

## Git init (required for Option A)

From this directory:
```
git init
git add collector.py sources.json reviewer_prompt.md routine_setup.md hooks/ .env.example .gitignore CLAUDE.md README.md
git commit -m "Initial ecosystem scout system"
```

Create a **private** repo on GitHub, then:
```
git remote add origin https://github.com/vjlewis55/ecosystem-scout.git
git push -u origin main
```

Do NOT commit: `.env`, `scout.db`, `queue.json` — all are in `.gitignore`.

---

## Recommended combination

- **Option A** (Routine) on a monthly cron = the engine. ~80% of the value.
- **Option C** (SessionStart hook) = the nudge, so pull-model review works too.
- Start with both. Option A won't fire until you push the repo and wire the Routine.

---

## Recovery: stuck items in 'queued' state

If the reviewer step fails mid-run, items may be stuck in 'queued' state and
won't resurface automatically. Reset them with:

```
sqlite3 "%SCOUT_DB%" "UPDATE candidates SET status='new' WHERE status='queued'"
```

Or use the `--status` flag first to confirm the counts:
```
python collector.py --db "%SCOUT_DB%" --status
```
