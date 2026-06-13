#!/usr/bin/env python3
"""
review_loop.py — Ralph Wiggum loop harness for Ecosystem Scout.

Wraps the LLM reviewer in a deterministic check-loop: read progress.json,
compare decided keys against queue.json, halt when all candidates have a
decision or when stagnation is detected.

In Routine context: the loop logic is embedded in reviewer_prompt.md (STEP 0).
This script is for local testing and serves as the canonical reference for the
loop's exit criterion and stagnation guard.

Usage:
  python review_loop.py --queue queue.json --progress progress.json --status
  python review_loop.py --queue queue.json --progress progress.json --check
"""

import argparse
import json
import sys


STAGNATION_LIMIT = 3   # halt after N consecutive iters with no new decisions


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def queue_keys(queue):
    return {c["key"] for c in queue.get("candidates", [])}


def decided_keys(progress):
    return set((progress or {}).get("decisions", {}).keys())


def is_done(queue, progress):
    return queue_keys(queue).issubset(decided_keys(progress))


def check(args):
    queue = load_json(args.queue)
    if not queue:
        print(f"[loop] ERROR: {args.queue} not found — run collector first", file=sys.stderr)
        sys.exit(1)

    progress = load_json(args.progress) or {"decisions": {}, "iteration": 0, "last_decided_count": -1}

    total = len(queue.get("candidates", []))
    decided = decided_keys(progress)
    remaining = queue_keys(queue) - decided

    print(f"[loop] Queue total:  {total}")
    print(f"[loop] Decided:      {len(decided)}")
    print(f"[loop] Remaining:    {len(remaining)}")
    print(f"[loop] Iteration:    {progress.get('iteration', 0)}")

    if is_done(queue, progress):
        print("[loop] STATUS: DONE — all candidates have decisions")
        sys.exit(0)

    # Stagnation check
    last_count = progress.get("last_decided_count", -1)
    if last_count == len(decided) and progress.get("iteration", 0) > 0:
        stagnation = progress.get("stagnation_count", 0) + 1
        if stagnation >= STAGNATION_LIMIT:
            print(f"[loop] STATUS: STALLED — {STAGNATION_LIMIT} iters with no progress")
            print(f"[loop] Unresolved keys: {sorted(remaining)}")
            sys.exit(2)
        print(f"[loop] WARNING: no progress last iter (stagnation {stagnation}/{STAGNATION_LIMIT})")
    else:
        print(f"[loop] STATUS: IN PROGRESS — {len(remaining)} candidates remaining")

    sys.exit(1)   # not done, not stalled — continue loop


def status(args):
    queue = load_json(args.queue)
    progress = load_json(args.progress)

    if not queue:
        print("No queue.json found.")
        return

    total = len(queue.get("candidates", []))
    decided = decided_keys(progress) if progress else set()
    remaining = queue_keys(queue) - decided

    print(f"Queue:     {total} candidates")
    print(f"Decided:   {len(decided)}")
    print(f"Remaining: {len(remaining)}")

    if progress:
        print(f"Iteration: {progress.get('iteration', 0)}")
        for key, rec in progress.get("decisions", {}).items():
            print(f"  {rec.get('verdict','?'):10s}  {key}")
    else:
        print("No progress.json — reviewer has not started yet.")


def main():
    ap = argparse.ArgumentParser(description="Ralph Wiggum loop harness for Ecosystem Scout")
    ap.add_argument("--queue",    default="queue.json")
    ap.add_argument("--progress", default="progress.json")
    ap.add_argument("--check",  action="store_true",
                    help="Exit 0=done, 1=in-progress, 2=stalled")
    ap.add_argument("--status", action="store_true",
                    help="Print human-readable loop state")
    args = ap.parse_args()

    if args.check:
        check(args)
    else:
        status(args)


if __name__ == "__main__":
    main()
