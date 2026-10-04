# ADR-0053: Mined ritual conformance + spot-audit cadence — replace self-reporting with evidence

**Status:** Accepted
**Date:** 2026-07-18
**Deciders:** the operator, Federation Architect

## Context

Fleet-wide ritual compliance is *claimed*, not *checked*. Every member's session rituals
(stamp at start/end, narrative written, STATUS/ROADMAP refreshed at close, role-doc
changes carrying a version bump + CHANGELOG, adoptions recorded) are enforced by adopted
habits — i.e. by LLM self-discipline — and reported by the same agent that performed them.
Self-assessment is the weakest evidence an agreeable agent produces. [ADR-0039](0039-appliable-brief-schema-and-auto-adopt-mechanism.md)
already made the stronger move for brief application ("never half-apply" became a code
guarantee); [ADR-0048](0048-federation-metrics-mining-layer.md) built the mining layer
that makes the same move possible for the rituals.

This ADR implements **R3 of the self-governance-hardening brief**
(`proposed-edits/federation-arch/pending/2026-07-14-self-governance-hardening.md`,
external consultant / Auditor role class per [ADR-0004](0004-auditor-as-separate-role-class.md)),
dispositioned by the operator this session (he took R3, session 74). Sibling of R2
([ADR-0052](0052-canon-budget-and-consolidation-ritual.md)). The in-flight concurrency
work ([ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) Phase 2)
names a mined-verification gate as the trunk merge check — the miners built here are that
gate's future evidence source; they ship now as observability, gate wiring comes with
Phase 2.

## Decision

**Ritual conformance is mined deterministically from the artifacts the rituals already
produce, by `curate/metrics.py`, read-only over every located member. Violations render
as exception-report lines and an `EVIDENCE.md` section — evidence, not accusations: a
violation line is as likely to expose a harness bug as an Architect lapse, and both are
worth knowing. The judgment layer gets a low-cadence human/Auditor spot-audit whose
*recency* is itself mined.**

### Conformance miners (mechanism 1)

Seven checks, each with a deterministic source. Exceptions are bounded to a recent
window (30 days) so the report stays actionable; full counts (including historical debt)
live in `EVIDENCE.md`.

1. **unclosed-session** — a session record with a start but no close, older than a 48h
   grace (a live session never flags itself). Journal-model repos ([ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md)):
   `sessions/journal/*.md` frontmatter `started` with no `ended`. Handoff-model repos:
   a `**Start:**` stamp with no paired `**End:**`.
2. **insane-duration** — a closed session whose span is negative or over 24h (a clock
   or stamp bug, or a close synthesized wrong). **AMENDED 2026-09-13 (WI-0207)** — a
   machine-closed journal's span is not scored; see the amendment below.
3. **missing-narrative** — a *closed* journal whose body still carries only the turn-1
   boilerplate ("Session opened."). The close ritual's step 3 (author the narrative) was
   skipped. **AMENDED 2026-09-13 (WI-0207)** — only where a session was alive to skip
   it; a machine-closed journal is counted separately and not scored. Journal-model repos only — the frontmatter boundary makes this crisp; judging
   handoff-era prose "empty" is not deterministic, so it is named unminable for that era,
   never guessed.
4. **roledoc-no-bump** — a commit that changed the repo's role doc (`session.config.json`
   `role_doc`) without changing its `**Version:**` line, over a bounded 90-day window —
   the [`versioned-role-doc-and-changelog`](../habits/master.md#versioned-role-doc-and-changelog)
   habit (C4), mined fleet-wide from git instead of warned locally by the pre-commit hook.
   Mined cheaply: blob-id comparison per commit (one batch `cat-file`), content read only
   for the few commits where the blob changed.
5. **status-stale-at-close** — the newest session close post-dates `STATUS.md`
   `last_active` by more than a day: the close ritual ran without the STATUS refresh
   (or the harness's `end` stamp failed). Distinct from the cadence *staleness* signal —
   this compares close-to-STATUS, not STATUS-to-today.
6. **roadmap-missing / roadmap-stale** — no `ROADMAP.md` (a standard deliverable,
   [ADR-0030](0030-roadmap-deliverable-format-federation-owned.md)), or its `**As of:**`
   date more than 7 days behind the newest session close.
7. **applied-unstamped** — a brief in `applied/` with no `applied:` / `Applied on`
   record: an adoption happened that left no auditable stamp ("adoption reports match
   `applied/` records", the minable core). Exception-listed only when recent (mtime
   window); historical unstamped briefs are an `EVIDENCE.md` count. **AMENDED 2026-09-13
   (WI-0207)** — scoped to briefs a writer could actually have stamped; see below.

Per system, violations aggregate to **one** exception line (`conformance` kind, count +
breakdown); the per-item detail is an `EVIDENCE.md` table. All checks are read-only and
fail-soft (an unreadable artifact is skipped, never fatal; a repo without the artifact
class — no journals, no config — simply isn't checked on that axis).

### Spot-audit cadence (mechanism 2)

The miners cover the mechanical layer; whether a session's *claims* match its artifacts
is judgment. A **monthly Auditor-class spot audit** (one sampled system per pass, per
[ADR-0004](0004-auditor-as-separate-role-class.md)) reads a recent session's artifacts
against its handoff/journal claims. Its record is a dated file
**`audits/YYYY-MM-DD-<system-id>.md`** in the federation repo (tracked; convention in
`audits/README.md`). The miner reads only the newest record's date: none on record →
"spot-audit never run" exception; newest older than 35 days (30d cadence + grace) →
"spot-audit overdue". The audit is judgment (P15's LLM half); the cadence enforcement is
code. The fleet knowing conformance is *sampled* is the deterrent — the audit itself
stays cheap.

### Member pilot gates (mechanism 3)

Converting a member pilot's G1–G5 self-reported gates to artifact-backed evidence is
dispositioned **at gate time**, using these same miners where they apply — not built
here (test projects stay opaque to the federation; the gate session works from the
project's own artifacts).

## Alternatives Considered

- **Members self-report conformance in STATUS.md.** Rejected — the exact self-reporting
  this ADR replaces; same ground [ADR-0047](0047-versioned-standard-substrate-and-rollout-reconciliation.md)
  rejected a declared rollout marker (a self-reported field can lie).
- **Enforce (block) instead of mine (observe).** Rejected for now: blocking lives at a
  gate (ADR-0051 Phase 2's merge gate is the natural home); observation must come first
  so the first weeks separate harness bugs from real lapses before anything blocks.
- **Judge handoff-era prose for missing narrative.** Rejected — not deterministic; named
  unminable for that era rather than guessed (P15 / no-fabricated-data).
- **Per-violation exception lines.** Rejected — a repo with historical debt would flood
  the attention list; one aggregated line per system keeps it boring-or-actionable, with
  the full table in `EVIDENCE.md`.
- **A separate conformance script.** Rejected — the mining layer (ADR-0048) exists
  precisely so new signals ride one `mine()` core and its renderings (P16).

## Consequences

- Ritual compliance claims become checkable: the exception report now carries per-system
  conformance lines and a spot-audit recency line; `EVIDENCE.md` gains a conformance
  section. First runs will surface historical debt (e.g. journals closed without
  narrative) — that is the miner working, same as the restore drill's first findings.
- New artifact: `audits/` (tracked) — spot-audit records, one per audit, newest date is
  the mined signal. New maintenance: actually running the monthly audit; the miner nags
  when it lapses.
- The `--status` startup probe inherits the new checks; all stay read-only/fail-open.
  The role-doc git check is blob-id-batched (the [ADR-0052](0052-canon-budget-and-consolidation-ritual.md)
  trend-miner technique) so the startup path stays fast.
- `NOT_YET_MINED` shrinks on the adoption axis and names the handoff-era narrative check
  as permanently unminable for that era.
- When ADR-0051 Phase 2 lands, the merge gate consumes these miners as its verification
  evidence — R3 is its load-bearing input, already running by then.

**Reality status (per [`mark-adr-reality-status`](../habits/master.md#mark-adr-reality-status)):**
miners + spot-audit recency mining + `audits/` convention **Built** this session
(`curate/metrics.py`, `tests/test_metrics.py`); the first spot audit has not yet run
(the miner surfaces exactly that); pilot gate conversion deferred to gate time.
The 2026-09-13 amendment (WI-0207) is **Built** — `informational` tier, the
`MANUAL_STAMP_CUTOVER` scoping and the named `--evidence` command all ship with tests.

## Amendment — a violation is evidence, and some facts are neither (2026-09-13, WI-0207)

This ADR's core claim — *"a violation line is as likely to expose a harness bug as an
Architect lapse, and both are worth knowing"* — was right, and for fourteen months the
federation's own number was **overwhelmingly the harness**. Measured before this
change: 36 recent violations for `federation`, of which 21 were
conditions the substrate makes **impossible to satisfy**.

- **`applied-unstamped` was a 100% category error.** 16 briefs fired it fleet-wide; 16
  were `apply: manual`. `session.py file_applied_brief()` — the only `applied:` writer
  that existed — runs on the auto-apply path ONLY, so a brief the Architect applied by
  hand could never carry the stamp the check demanded. The check's docstring named the
  exemption it *had* thought about (pre-schema briefs with no fence); `apply: manual`
  was never considered, so every manual apply the federation ever did was permanently
  counted as an audit gap.
- **`missing-narrative` was majority machine-closed.** 15 recent narrative-less closes,
  11 of them written by `reap`, `janitor` or `resolve-orphan` after the owning session
  was already dead. A session that died cannot narrate itself. Only 4 were human closes
  worth reading.
- **`insane-duration` was partly the same.** A journal showing 35.3h because the reaper
  closed it from its death evidence is the correct close producing a violation.

**The cost is not the count, it is that nobody reads it** — 4 real lapses buried under 21
impossible ones is the exact failure a conformance metric exists to prevent.

### D1 — a third answer: `informational`, counted and shown, never scored

`mine_conformance` now returns `{recent, historic, informational}`. Machine closes
(`MACHINE_CLOSERS`: `janitor`, `reap`, `resolve-orphan`, `worktree-remove`) route to
`informational` as `machine-closed-no-narrative` / `machine-closed-duration`. They ride
the system's existing exception row as a `(+N not scored: …)` rider and get their own
EVIDENCE.md block; a system whose ONLY findings are unscored gets no attention row at all.

`session.py end` is deliberately NOT a machine closer — it is code, but it is the live
session's own close, and a session that reached it could have written a narrative. Nor is
`reconstruction`: an Architect rebuilding a close by hand is a person. A journal that does
not say who closed it stays **scored** — the honest direction, keeping an unknown visible
rather than quietly excusing it ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)).

### D2 — `applied-unstamped` is scoped by what could have been stamped, NOT exempted

**The fix is not an exemption.** Exempting manual briefs would leave a manual apply with
no receipt at all, forever, and
[`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)
warns that an exemption defaulting to pass does not merely fail to detect a gap, it
certifies one. What changed is the **substrate**: WI-0223 gave `curate/adopt-runner.py` a
`file_and_stamp_brief()` that files `pending/` → `applied/` and stamps the receipt itself,
**minting a frontmatter fence** where the brief has none. From the day that writer started
filing, a manual brief *can* be stamped and a missing stamp is a real gap again.

So the check asks the only question that means anything — **could ANY writer have stamped
this?** — with a hardcoded `MANUAL_STAMP_CUTOVER = 2026-09-04`. This is **the operator's date**,
ruled on WI-0256 (session ~241) for the sibling guard in `session.py`, adopted here
unchanged so the two surfaces cannot disagree about the same brief. The legacy briefs are
structurally out of scope rather than exempted by a flag someone can later flip.

The gate also **widens**: a brief filed after the cutover is in scope whatever its shape,
because `_inject_stamp` mints a fence and `has_frontmatter` no longer bounds what can
carry a receipt. Measured before choosing that scope — zero post-cutover fenceless briefs
exist today, so the widening costs nothing now and keeps the check's teeth for the class
that can next occur.

### D3 — the exception line names the command, not a file that does not exist

The row ended *"detail in EVIDENCE.md"*. `EVIDENCE.md` is written on the `--evidence` path
only, never at startup, and is gitignored — so the line routinely pointed a reader at an
**absent file**, and the one thing that would have resolved it (the command) was the thing
not printed. The row now ends `— detail: run 'python3 curate/metrics.py --evidence'`.

### Result, measured

`federation` 36 → **14** scored violations, with 12 shown as not-scored; one member's row
disappears entirely (its only finding was a `resolve-orphan` close). The two
`applied-unstamped` survivors are both briefs filed 2026-09-06, after the cutover — real
missing receipts. Every change is pinned by tests over fixture journals and briefs of each
shape, and all six mutations reverting them turn the suite red.

**Not changed:** the wanted *human-facing* file-and-stamp verb (WI-0207 ask (a)) stays with
WI-0256, which owns the question of whether it is federation-only or fleet-facing. This
amendment touches `curate/metrics.py` only, which is not in `push-substrate.py`'s
`BYTE_IDENTICAL` manifest and therefore ships to no member.

## References

- Consultant brief: `proposed-edits/federation-arch/pending/2026-07-14-self-governance-hardening.md` § R3.
- [ADR-0048](0048-federation-metrics-mining-layer.md) — the mining layer this rides.
- [ADR-0052](0052-canon-budget-and-consolidation-ritual.md) — sibling R2; the batch git technique reused here.
- [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) Phase 2 — the future merge gate that consumes these miners.
- [ADR-0039](0039-appliable-brief-schema-and-auto-adopt-mechanism.md) — the precedent move (claim → code guarantee).
- [ADR-0004](0004-auditor-as-separate-role-class.md) — the Auditor role class the spot audit instantiates.
- [P15](../principles/master.md#p15--code-for-mechanism-not-judgment) / [P18](../principles/master.md#p18--verify-everything) / [`no-fabricated-data`](../habits/master.md#no-fabricated-data) — the disciplines applied.
