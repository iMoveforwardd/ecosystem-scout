#!/usr/bin/env python3
"""
Ecosystem Scout — SessionStart nudge
====================================
A *hook*, not a scanner. Fires when a Claude Code session starts, reads the
scout DB, and prints a one-line reminder if candidates are waiting or a scan is
overdue. Stdout from a SessionStart hook is injected as context, so this is the
"pull-model safety net": when you sit down to work, you're reminded the backlog
is ready — without anything heavy running.

Always exits 0 (a noisy hook that blocks your session is worse than no hook).

DB path is read from SCOUT_DB env var (set in .env or the Routine's cloud config).
Fallback: C:\Users\vjlew\ecosystem-scout\scout.db
"""
import datetime as dt
import os
import sqlite3
import sys

DB = os.environ.get("SCOUT_DB", r"C:\Users\vjlew\ecosystem-scout\scout.db")
OVERDUE_DAYS = int(os.environ.get("SCOUT_OVERDUE_DAYS", "30"))


def main():
    if not os.path.exists(DB):
        return  # nothing set up yet; stay silent
    try:
        conn = sqlite3.connect(DB)
        waiting = conn.execute(
            "SELECT COUNT(*) FROM candidates WHERE status IN ('new','queued')"
        ).fetchone()[0]
        row = conn.execute("SELECT value FROM meta WHERE key='last_run'").fetchone()
    except Exception:
        return  # never break a session over a nudge

    bits = []
    if waiting:
        bits.append(f"{waiting} capability candidate(s) awaiting review")
    if row:
        try:
            last = dt.datetime.strptime(row[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
            days = (dt.datetime.now(dt.timezone.utc) - last).days
            if days >= OVERDUE_DAYS:
                bits.append(f"last ecosystem scan was {days} days ago")
        except Exception:
            pass

    if bits:
        print("🔭 Ecosystem Scout: " + "; ".join(bits) +
              ". Say 'run my capability review' to vet them.")


if __name__ == "__main__":
    main()
    sys.exit(0)
