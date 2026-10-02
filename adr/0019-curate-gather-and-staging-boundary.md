# ADR-0019: Curate pass is a code gather plus human review, not an agent harness

**Status:** Accepted
**Date:** 2026-06-04
**Deciders:** the operator, Federation Architect

## Context

The **curate pass** is the federation step that reads the Architects' producer
learnings, finds what recurs or generalizes, and promotes the durable claims to
principles or universal habits (per [ADR-0008](0008-three-bucket-taxonomy.md),
written into the registries per [ADR-0009](0009-data-storage-mechanics.md) /
[ADR-0010](0010-registries-canon-only-sidecar-history.md)). Until now it has been
prose discipline executed freehand by the Federation Architect in-session.

Session 19 produced a design ([`design/curate-pass-harness.md`](../design/curate-pass-harness.md))
proposing to turn the pass into a multi-agent pipeline: discover → extract →
cluster → classify → adversarial verify → propose → stage, 13 agent roles, with
a judge panel and gold-set behind a `distinct_system_count >= 2` trigger. Even
its own "walking skeleton" reduction kept five-plus agent roles for the gather
half.

the operator's ruling (session 21): **this is over-engineered for the problem.** The job
the federation actually needs right now is small — *gather the new suggestions,
then the operator and the Federation Architect review them on a regular basis.* The
corpus is single-producer scale (29 producer entries across two files — the
Federation Architect's own and one other member's; the rest have only role-doc
mirrors, no producer files). At this size the judgment is cheap enough to keep
human, and a fleet of agents to read-and-group two files buys nothing.

Two governing instructions from the operator shaped the decision:

1. For now, the pass only gathers the suggestions; the operator and the Federation
   Architect review them together on a regular cadence — the scope.
2. Any step that can be done in code is done in code — the mechanism: push
   every deterministic step out of LLM judgment and into a script.

## Decision

**The curate pass is a deterministic gather script plus a human review. No
multi-agent harness is built.** The split between code and LLM follows the operator's
"code where possible" rule:

**Code (`curate/gather.py`) — mechanical, no judgment:**
- Discover the producer files: root `architect-learnings.md` and
  `inputs/*-learnings.md`.
- Split each file into entries on the date-header delimiter (`## <YYYY-MM-DD>`,
  which matches both on-disk producer formats and excludes section headers and
  literal template lines).
- Identify which entries are new or changed since the last review, by content
  hash, tracked in a cursor.
- Emit a `REVIEW.md` listing every new/changed entry **verbatim**, with its
  `source:line` locator and content-hash id, grouped by source file.

The script makes **no judgment**: it does not classify, does not cluster by
meaning, does not score universality, does not write canon. Code that *could*
cluster lexically is deliberately excluded — it fakes intelligence and misleads;
grouping by meaning is left to the review where it belongs.

**LLM + human — judgment only:**
- the operator and the Federation Architect review the gathered list together on a
  regular cadence, deciding which entries (if any) become principles or habits.
- The Federation Architect writes the keepers into the registries.

**Staging boundary (structural, by location):**
- `curate-runs/` holds the generated `REVIEW-*.md` files. It is **gitignored** —
  ephemeral staging output, not canon. Nothing the gather step writes can reach
  the registries.
- `curate/gather.py` (the script) and `curate/seen.json` (the cursor) are
  **system, tracked in git** — the cursor carries review state across
  machines so nothing is re-surfaced or dropped between sessions.

**The promotion gate is unchanged from [ADR-0009](0009-data-storage-mechanics.md) /
[ADR-0010](0010-registries-canon-only-sidecar-history.md), and the gather step
does not touch it.** The Federation Architect drafts a survivor as a `Proposed`
entry in the registry (autonomous, per federation-arch §9); the operator approves; the
Federation Architect flips `Proposed → Accepted` in place and writes the matching
`*-history.md` sidecar line, **quoting the operator's approval verbatim** into that line
as the authorization record. (This last point — quote the approval into the
sidecar so the gate is a checkable git artifact rather than in-session
discipline — is the one piece of the session-19 design carried forward; it
corrects the design's own canon-contradicting "Proposed never lives in the
registry" invariant, which contradicted ADR-0009/0010 and is rejected.)

## Alternatives Considered

- **Build the full multi-agent harness** ([`design/curate-pass-harness.md`](../design/curate-pass-harness.md)).
  Rejected — over-engineered for a single-producer, 29-entry corpus. The
  adversarial machinery exists to resist the universality-by-frequency trap
  across *multiple corroborating sources*; today there is zero cross-system
  corroboration to adjudicate. It costs more to build and run than the
  Federation Architect reading the files.
- **Build the "walking skeleton" — the gather half as agents.** Rejected — even
  the skeleton wrapped mechanical work (list files, quote entries, dedup, group,
  format) in five-plus agent roles. None of that needs an LLM; per the operator's "code
  where possible" rule it is a script.
- **Let code cluster the entries (lexical/keyword grouping).** Rejected — it
  fakes the one genuinely hard judgment (what generalizes across systems) and
  misleads more than it helps. Code does less and stays trustworthy; the
  Federation Architect groups in review.
- **No cursor — re-review everything each pass.** Rejected — re-litigates settled
  entries every cycle. The content-hash cursor is the one small piece of state
  genuinely worth coding; an edited entry's hash changes, so a materially revised
  lesson correctly re-surfaces.

## Consequences

- The federation can run the curate pass today, at near-zero cost, with the
  honest expected yield the session-19 stress-test predicted: a short surface
  list, almost nothing auto-promoted. That is the pass working correctly.
- Role-doc mirrors (`inputs/*-arch.md`) are **not** gathered by this script. The
  "suggestions" are producer-file entries; mirrors are already-adopted role-doc
  state, a weaker and more complex signal (delta-against-baseline). They are out
  of scope for now and can be added if a real need appears.
- [`design/curate-pass-harness.md`](../design/curate-pass-harness.md) is **shelved,
  not deleted** — it remains a reference for the deferred harness. The trigger to
  revisit it is concrete: a **second domain producer file exists** *and* the
  corpus has outgrown what the Federation Architect can review in a single
  context. Neither holds today.
- No effect on any participating Architect — this is the federation's own
  internal tooling.
- Implements the front-end of the [ADR-0009](0009-data-storage-mechanics.md)
  promotion workflow (the gather → review step that precedes drafting a
  `Proposed` entry); does not change the workflow itself.

## References

- [`design/curate-pass-harness.md`](../design/curate-pass-harness.md) — the
  deferred multi-agent design this ADR shelves; source of the carried-forward
  "quote the approval into the sidecar" gate hardening.
- [ADR-0008: Three-bucket taxonomy](0008-three-bucket-taxonomy.md) — the
  principle/habit/preference classification applied during review.
- [ADR-0009: Data storage mechanics](0009-data-storage-mechanics.md) — the
  promotion workflow this gather step feeds.
- [ADR-0010: Registries are canon-only; sidecar history uniform](0010-registries-canon-only-sidecar-history.md) — the sidecar line, now carrying the operator's quoted approval.
- [ADR-0018: Adopted principles and habits are system, not data](0018-adopted-principles-and-habits-are-system.md) — the system/data line `curate/` (system) vs `curate-runs/` (ephemeral) follows.
- Source conversation: Federation Architect session 21 (2026-06-04).
