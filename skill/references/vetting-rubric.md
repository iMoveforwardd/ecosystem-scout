# Vetting Rubric

Score each candidate across these dimensions, then map to a recommendation. The security and provenance checks are gating: a single hard red flag forces **Reject** or, at best, **Trial** — high marks elsewhere do not buy it back.

---

## A. Provenance & trust (gating)

- **Source tier** — Tier 1 (official/namespace-verified) > Tier 2 (curated community) > Tier 3 (open). Tier alone can move a borderline candidate.
- **Namespace verification** (MCPs) — is it published under a verified reverse-DNS namespace in the official registry (`io.github.x/…`, `com.vendor/…`)? Verified = strong trust signal. Unverified fork claiming to be an official server = red flag.
- **Maintainer reputation** — known org/individual, or anonymous throwaway? Cross-check the GitHub profile.
- **Code visibility** — can you actually read the source (SKILL.md, MCP entrypoint, CLI code)? **If not visible, it cannot be Approved.**

## B. Security (gating — this is the whole point)

Skills, MCPs, and CLIs run code with Vince's permissions and, for connectors, his data. For anything that runs locally or holds scopes:

- **What it executes** — read the entrypoint. Does it run shell commands, eval, network calls, install dependencies at runtime?
- **What it accesses** — filesystem paths, env vars, credentials. A skill that reads `~/.ssh`, `.env`, or wallet/keychain paths is an automatic **Reject** unless that's its legitimate, stated purpose.
- **Phone-home / exfiltration** — does it send data to an external endpoint not core to its function? Any covert outbound traffic = **Reject**.
- **OAuth scopes** (connectors) — least-privilege check. A "read my calendar" connector requesting full Drive write is over-scoped → at most **Trial**, ideally Reject.
- **Dependency hygiene** — pinned deps? Known-vulnerable packages? Suspicious transitive deps?
- **Secrets handling** — does it ever ask to embed API keys/tokens in source instead of env/MCP config? Bad practice → downgrade.

> Rule of thumb: if you would not run it on a machine that has CourtOS data or Vince's legal/financial files on it without first watching what it does, it is **Trial**, not **Approve**.

## C. Maintenance health

- **Last activity** — last commit/release. < 90 days = healthy; 90–180 = aging (Watch-leaning); > 12 months with open issues = likely abandoned (Reject-leaning).
- **Adoption** — stars, forks, downloads/installs. Context-relative: a niche legal MCP with 80 stars can be fine; a generic tool with 80 stars is unproven.
- **Issue health** — open-issue ratio and whether maintainers respond. A high open ratio with no responses signals neglect.
- **Release discipline** — versioned releases, changelog, semver.

## D. Fit

- **Workflow match** — maps cleanly to one of Vince's domains (legal/CourtOS, trades consulting, real estate, performance, personal ops)?
- **Gap vs. duplicate** — fills a real gap, or duplicates something he already runs? Duplicates → Reject (note the existing tool).
- **Integration cost** — install/config effort vs. payoff. High effort + marginal payoff → Watch.
- **Format quality** (skills) — proper YAML frontmatter, progressive disclosure, clear triggering description? Sloppy skills misfire and waste context.

---

## Mapping scores to a recommendation

| Recommendation | Conditions |
|---|---|
| **✅ Approve** | No security/provenance red flags · code visible & clean · healthy maintenance · clear non-duplicate fit · acceptable least-privilege scopes. Tier 1 sources clear this bar most easily. |
| **🧪 Trial** | Relevant and promising, but runs code or holds scopes that warrant isolated testing first, OR provenance is good but adoption is thin. Approve-able *after* a clean trial. Always specify the safe trial method. |
| **👀 Watch** | Relevant but immature: low adoption, young repo, aging maintenance, or unverified provenance with no way to test safely yet. Recheck next cycle; note the specific signal that would promote it. |
| **🚫 Reject** | Any hard security/exfiltration flag · over-scoped with no need · abandoned · unreadable code · or poor/duplicate fit. Always record the reason so it stays rejected. |

## Safe-trial methods (for the Trial tier)

- **Skill** — install into a non-sensitive workspace, run its own test prompts, read what it does on a throwaway file before pointing it at real data.
- **MCP/connector** — connect with the *minimum* scopes, test against non-sensitive data, watch the actual tool calls it makes, then decide.
- **CLI** — run in a container/sandbox or a scratch directory; inspect network and filesystem activity before using on real projects.

## Hard red flags (instant Reject)

- Requests credentials, keys, or wallet/keychain access without a legitimate stated purpose.
- Covert outbound network calls / telemetry to undisclosed endpoints.
- Obfuscated or unreadable code where readable code is expected.
- Impersonates an official namespace it doesn't own.
- Abandoned (>12 months) while handling sensitive data or auth.
- Asks Vince to paste secrets directly into source files.
