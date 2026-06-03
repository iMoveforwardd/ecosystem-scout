#!/usr/bin/env python3
"""
Ecosystem Scout — Collector
============================
Deterministic half of the capability-scouting pipeline. Discovers NEW candidate
Skills / MCP servers / CLIs since the last run, dedupes against past decisions,
and writes a small queue for the LLM reviewer (the `ecosystem-scout` skill) to
vet. No LLM here — just diffing and signal-gathering, so it's cheap, complete,
and identical every run.

Pipeline:
  1. MCP Registry diff   — official registry, `updated_since` for true incremental sync
  2. README diff         — awesome-list READMEs, new entries vs. last snapshot
  3. GitHub enrichment   — stars / last push / license / open issues (best-effort, optional)
  4. Relevance tagging    — match against Vince's workflow domains
  5. Dedupe + queue       — drop anything already decided; export queue.json

Zero third-party dependencies (Python 3.9+ stdlib only).

Security note: This collector only fetches URLs explicitly listed in sources.json
(authored by Vince, committed to the repo). It does NOT auto-discover or
auto-follow links. Do not modify it to do so — that would expand the attack surface.

Usage:
  python collector.py --db scout.db --config sources.json --since-days 90 --enrich
  python collector.py --db scout.db --status            # show queue/decision counts
  python collector.py --db scout.db --decide URL approve "reason"   # record a decision
"""

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request

USER_AGENT = "ecosystem-scout-collector/1.0 (+personal use)"
GITHUB_REPO_RE = re.compile(r"https?://github\.com/([\w.-]+)/([\w.-]+)")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


# --------------------------------------------------------------------------- #
# HTTP helper (stdlib)
# --------------------------------------------------------------------------- #
def http_get(url, accept="application/json", timeout=30, token=None):
    """GET a URL, returning (status, text). Never raises on HTTP errors."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # network down, DNS, timeout — log and move on
        print(f"    ! fetch failed: {url} ({e})", file=sys.stderr)
        return 0, ""


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    source_id TEXT PRIMARY KEY,
    seen_urls TEXT,          -- JSON list of URLs already seen in this source
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS candidates (
    key TEXT PRIMARY KEY,    -- canonical URL or registry name
    name TEXT,
    ctype TEXT,              -- skill | mcp | cli
    source TEXT,
    tier INTEGER,
    url TEXT,
    description TEXT,
    domains TEXT,            -- JSON list of matched workflow domains
    signals TEXT,            -- JSON: stars, pushed_at, license, open_issues
    status TEXT,             -- new | queued | reviewed
    first_seen TEXT,
    last_seen TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    key TEXT PRIMARY KEY,    -- same canonical key as candidates
    name TEXT,
    decision TEXT,           -- approve | trial | watch | reject | duplicate
    reason TEXT,
    decided_at TEXT
);
"""


def db_connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def meta_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn, key, value):
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def is_decided(conn, key):
    return conn.execute("SELECT 1 FROM decisions WHERE key=?", (key,)).fetchone() is not None


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Relevance + dedupe helpers
# --------------------------------------------------------------------------- #
def match_domains(text, domain_keywords):
    t = (text or "").lower()
    hits = [d for d, kws in domain_keywords.items() if any(k in t for k in kws)]
    return hits


def is_duplicate_of_inventory(name, desc, inventory):
    blob = f"{name} {desc}".lower()
    for item in inventory.get("skills", []) + inventory.get("connectors", []):
        if item and item.lower() in blob:
            return True
    return False


def canon_key(url_or_name):
    k = (url_or_name or "").strip().lower().rstrip("/")
    return k


def upsert_candidate(conn, *, key, name, ctype, source, tier, url, description, domains, signals):
    ts = now_iso()
    existing = conn.execute("SELECT key FROM candidates WHERE key=?", (key,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE candidates SET last_seen=?, signals=?, domains=? WHERE key=?",
            (ts, json.dumps(signals), json.dumps(domains), key),
        )
        return False  # not new
    conn.execute(
        "INSERT INTO candidates(key,name,ctype,source,tier,url,description,domains,signals,status,first_seen,last_seen) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (key, name, ctype, source, tier, url, description,
         json.dumps(domains), json.dumps(signals), "new", ts, ts),
    )
    return True  # newly inserted


# --------------------------------------------------------------------------- #
# Stage 1 — MCP Registry diff
# --------------------------------------------------------------------------- #
def scan_registry(conn, cfg, since_iso, domain_keywords, inventory):
    base = cfg["registry"]["base_url"]
    limit = cfg["registry"].get("page_limit", 50)
    new_count = 0
    cursor = None
    print(f"[1/3] MCP Registry diff (updated_since={since_iso})")
    while True:
        url = f"{base}?limit={limit}&updated_since={since_iso}"
        if cursor:
            url += f"&cursor={cursor}"
        status, body = http_get(url)
        if status != 200 or not body:
            print(f"    registry returned status {status}; skipping registry scan", file=sys.stderr)
            break
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            print("    registry returned non-JSON; skipping", file=sys.stderr)
            break
        servers = data.get("servers", [])
        for entry in servers:
            srv = entry.get("server", entry)
            name = srv.get("name", "")
            desc = srv.get("description", "")
            repo = (srv.get("repository") or {}).get("url", "")
            key = canon_key(name)  # reverse-DNS namespace is the stable id
            if not key or is_decided(conn, key):
                continue
            domains = match_domains(f"{name} {desc}", domain_keywords)
            dup = is_duplicate_of_inventory(name, desc, inventory)
            signals = {"namespace_verified": True, "repo": repo,
                       "registry_status": (entry.get("_meta", {})
                                           .get("io.modelcontextprotocol.registry/official", {})
                                           .get("status"))}
            if dup:
                conn.execute(
                    "INSERT OR IGNORE INTO decisions(key,name,decision,reason,decided_at) VALUES(?,?,?,?,?)",
                    (key, name, "duplicate", "matches existing inventory", now_iso()))
                continue
            if upsert_candidate(conn, key=key, name=name, ctype="mcp",
                                source="official-registry", tier=1, url=repo or name,
                                description=desc, domains=domains, signals=signals):
                new_count += 1
        cursor = (data.get("metadata") or {}).get("next_cursor") or data.get("next_cursor")
        if not cursor:
            break
    conn.commit()
    print(f"    + {new_count} new registry candidate(s)")
    return new_count


# --------------------------------------------------------------------------- #
# Stage 2 — README diff (awesome lists)
# --------------------------------------------------------------------------- #
SKIP_HOSTS = ("img.shields.io", "awesome.re", "badge", "github.com/sponsors",
              "githubusercontent.com", "/blob/", "/actions", "/stargazers",
              "/network/", "/issues", "/pulls", "/wiki", "twitter.com",
              "x.com", "discord", "youtube.com", "youtu.be",
              "docs.claude.com", "www.anthropic.com", "anthropic.com/news",
              "anthropic.com/engineering", "modelcontextprotocol.io/docs")


def extract_entries(markdown):
    """Return list of (name, url, context_line) for real candidate links.
    Skips images, badges, anchors, and obvious non-project links."""
    entries = []
    seen_local = set()
    for line in markdown.splitlines():
        for m in MD_LINK_RE.finditer(line):
            start, text, url = m.start(), m.group(1).strip(), m.group(2).strip()
            if start > 0 and markdown_char_before(line, m) == "!":
                continue  # image link ![...](...)
            if url.startswith("#") or not url.startswith("http"):
                continue
            low = url.lower()
            if any(h in low for h in SKIP_HOSTS):
                continue
            # keep project links: github repos or other external project pages
            gh = GITHUB_REPO_RE.search(url)
            if gh and gh.group(2).lower() in ("", "topics", "search"):
                continue
            key = url.rstrip("/")
            if key in seen_local:
                continue
            seen_local.add(key)
            entries.append((text, url, line.strip()))
    return entries


def markdown_char_before(line, match):
    """Char immediately preceding the '[' of a markdown link, or ''."""
    idx = match.start() - 1
    return line[idx] if idx >= 0 else ""


def scan_readmes(conn, cfg, domain_keywords, inventory):
    print("[2/3] README diff (awesome lists)")
    total_new = 0
    for src in cfg["readme_sources"]:
        sid = src["id"]
        status, md = http_get(src["raw"], accept="text/plain")
        if status != 200 or not md:
            print(f"    - {sid}: fetch failed (status {status})", file=sys.stderr)
            continue
        entries = extract_entries(md)
        current_urls = {canon_key(u) for _, u, _ in entries}

        row = conn.execute("SELECT seen_urls FROM snapshots WHERE source_id=?", (sid,)).fetchone()
        seen = set(json.loads(row["seen_urls"])) if row else set()
        first_run = row is None

        new_here = 0
        for name, url, ctx in entries:
            key = canon_key(url)
            if key in seen or is_decided(conn, key):
                continue
            domains = match_domains(f"{name} {ctx}", domain_keywords)
            # Relevance gate (applies EVERY run): keep only items that touch a
            # workflow domain, or come from an official Tier-1 source.
            relevant = bool(domains) or src["tier"] == 1
            # First contact with a source = establish baseline. Queue nothing
            # from broad Tier-2/3 lists (that's the "boil the ocean" trap);
            # only tiny, trusted Tier-1 sources may seed on first run.
            queueable = relevant and (not first_run or src["tier"] == 1)
            if not queueable:
                continue
            if is_duplicate_of_inventory(name, ctx, inventory):
                conn.execute(
                    "INSERT OR IGNORE INTO decisions(key,name,decision,reason,decided_at) VALUES(?,?,?,?,?)",
                    (key, name, "duplicate", "matches existing inventory", now_iso()))
                continue
            desc = ctx.split(" - ", 1)[1] if " - " in ctx else ctx
            if upsert_candidate(conn, key=key, name=name, ctype=src["type"],
                                source=sid, tier=src["tier"], url=url,
                                description=desc[:300], domains=domains, signals={}):
                new_here += 1

        conn.execute(
            "INSERT INTO snapshots(source_id,seen_urls,fetched_at) VALUES(?,?,?) "
            "ON CONFLICT(source_id) DO UPDATE SET seen_urls=excluded.seen_urls, fetched_at=excluded.fetched_at",
            (sid, json.dumps(sorted(seen | current_urls)), now_iso()))
        conn.commit()
        tag = " (first run — baseline recorded)" if first_run else ""
        print(f"    - {sid}: {len(entries)} entries, + {new_here} new{tag}")
        total_new += new_here
    print(f"    + {total_new} new README candidate(s)")
    return total_new


# --------------------------------------------------------------------------- #
# Stage 3 — GitHub enrichment (best-effort)
# --------------------------------------------------------------------------- #
def enrich_github(conn, token=None, sleep=0.8):
    print("[3/3] GitHub enrichment")
    rows = conn.execute("SELECT key,url,signals FROM candidates WHERE status='new'").fetchall()
    enriched = 0
    for row in rows:
        m = GITHUB_REPO_RE.search(row["url"] or "")
        if not m:
            continue
        owner, repo = m.group(1), m.group(2)
        if repo.endswith(".git"):
            repo = repo[:-4]
        status, body = http_get(f"https://api.github.com/repos/{owner}/{repo}", token=token)
        if status == 403:
            print("    rate-limited by GitHub API; stopping enrichment "
                  "(set GITHUB_TOKEN to raise the limit)", file=sys.stderr)
            break
        if status != 200 or not body:
            continue
        try:
            d = json.loads(body)
        except json.JSONDecodeError:
            continue
        sig = json.loads(row["signals"] or "{}")
        sig.update({
            "stars": d.get("stargazers_count"),
            "pushed_at": d.get("pushed_at"),
            "open_issues": d.get("open_issues_count"),
            "license": (d.get("license") or {}).get("spdx_id"),
            "archived": d.get("archived"),
        })
        conn.execute("UPDATE candidates SET signals=? WHERE key=?", (json.dumps(sig), row["key"]))
        enriched += 1
        time.sleep(sleep)  # be polite to the API
    conn.commit()
    print(f"    enriched {enriched} candidate(s)")
    return enriched


# --------------------------------------------------------------------------- #
# Export queue for the reviewer
# --------------------------------------------------------------------------- #
def export_queue(conn, path):
    rows = conn.execute(
        "SELECT * FROM candidates WHERE status='new' "
        "ORDER BY tier ASC, (domains != '[]') DESC"
    ).fetchall()
    queue = []
    for r in rows:
        queue.append({
            "key": r["key"], "name": r["name"], "type": r["ctype"],
            "source": r["source"], "tier": r["tier"], "url": r["url"],
            "description": r["description"],
            "domains": json.loads(r["domains"] or "[]"),
            "signals": json.loads(r["signals"] or "{}"),
            "first_seen": r["first_seen"],
        })
    out = {
        "generated_at": now_iso(),
        "count": len(queue),
        "candidates": queue,
    }
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    # mark exported items as queued so the next run doesn't re-export them
    conn.execute("UPDATE candidates SET status='queued' WHERE status='new'")
    conn.commit()
    return len(queue)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def cmd_status(conn):
    c = conn.execute("SELECT status, COUNT(*) n FROM candidates GROUP BY status").fetchall()
    d = conn.execute("SELECT decision, COUNT(*) n FROM decisions GROUP BY decision").fetchall()
    last = meta_get(conn, "last_run", "never")
    print(f"Last run: {last}")
    print("Candidates:", {r["status"]: r["n"] for r in c} or "none")
    print("Decisions :", {r["decision"]: r["n"] for r in d} or "none")
    print()
    print("Recovery: if a review failed mid-run and items are stuck in 'queued' state,")
    print("  run: sqlite3 <db> \"UPDATE candidates SET status='new' WHERE status='queued'\"")


def cmd_decide(conn, key, decision, reason):
    key = canon_key(key)
    name = (conn.execute("SELECT name FROM candidates WHERE key=?", (key,)).fetchone() or {})
    name = name["name"] if name else key
    conn.execute(
        "INSERT INTO decisions(key,name,decision,reason,decided_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET decision=excluded.decision, reason=excluded.reason, decided_at=excluded.decided_at",
        (key, name, decision, reason, now_iso()))
    conn.execute("UPDATE candidates SET status='reviewed' WHERE key=?", (key,))
    conn.commit()
    print(f"recorded: {decision} — {key}")


def main():
    ap = argparse.ArgumentParser(description="Ecosystem Scout collector")
    ap.add_argument("--db", default="scout.db")
    ap.add_argument("--config", default="sources.json")
    ap.add_argument("--queue", default="queue.json", help="output queue path for the reviewer")
    ap.add_argument("--since-days", type=int, default=None,
                    help="lookback window; default = since last run (or 90 on first run)")
    ap.add_argument("--enrich", action="store_true", help="fetch GitHub stars/activity (slower)")
    ap.add_argument("--github-token", default=None, help="raises GitHub API rate limit")
    ap.add_argument("--status", action="store_true", help="print counts and exit")
    ap.add_argument("--decide", nargs=3, metavar=("KEY", "DECISION", "REASON"),
                    help="record a decision and exit")
    args = ap.parse_args()

    conn = db_connect(args.db)

    if args.status:
        cmd_status(conn)
        return
    if args.decide:
        cmd_decide(conn, *args.decide)
        return

    with open(args.config) as f:
        cfg = json.load(f)
    domain_keywords = cfg.get("domain_keywords", {})
    inventory = cfg.get("known_inventory", {})

    # Determine the incremental window.
    if args.since_days is not None:
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.since_days)
    else:
        last = meta_get(conn, "last_registry_sync")
        if last:
            since = dt.datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        else:
            since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"=== Ecosystem Scout collector — {now_iso()} ===")
    scan_registry(conn, cfg, since_iso, domain_keywords, inventory)
    scan_readmes(conn, cfg, domain_keywords, inventory)
    if args.enrich:
        enrich_github(conn, token=args.github_token)

    n = export_queue(conn, args.queue)
    meta_set(conn, "last_run", now_iso())
    meta_set(conn, "last_registry_sync", now_iso())
    conn.commit()
    print(f"\nQueue written to {args.queue}: {n} candidate(s) awaiting review.")
    if n == 0:
        print("Nothing new worth reviewing this cycle — a valid result.")


if __name__ == "__main__":
    main()
