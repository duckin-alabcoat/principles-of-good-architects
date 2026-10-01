# ADR-0068: Retire the declared standard-version — detection is the only version signal

**Status:** Accepted
**Date:** 2026-07-23
**Deciders:** the operator, Federation Architect (session 90)

## Context

[ADR-0047](0047-versioned-standard-substrate-and-rollout-reconciliation.md) gave the standard substrate a version number and built two things to read it: a member-side self-check ([`standard_check.py`](../standard_check.py)) and a federation-side fleet parity view ([`curate/standard_version.py`](../curate/standard_version.py)). Both compared **two** signals:

- **declared** — a `standard_version` field in the member's own `session.config.json`, a self-reported claim.
- **detected** — what the member's files actually prove it can do, computed by the capability detectors.

ADR-0047 already ruled that detection is authoritative and a claim outrunning it is drift. The declared field's only remaining job was catching the case where a member *claims* a version it does not have.

The v1.4.0 rollout (session 90) surfaced that this axis has never functioned. Every established member read `declared: —` — unstamped. The reason is structural, not an oversight: `session.config.json` is the member's **authored** file, so the federation cannot write it ([ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md) — the federation writes only its own generated substrate), and no member-side code writes it either. Nothing in the system has ever set the field.

The single exception proves the point. One member carries `standard_version: v1.3.0` — added by hand in a dedicated commit, not seeded by the kit, whose `session.config.json` template has no such field. Within the same session it was already stale: that member detects v1.4.0 while still claiming v1.3.0.

So the axis is a hand-maintained copy of a value that code already computes correctly — the exact pattern [P16](../principles/master.md#p16--avoid-duplication) prohibits, and the harm is the one P16 names: the copy drifts silently, and it drifted within hours of being written.

An initial reading of the evidence attributed the field to bootstrap seeding. That was an inference; checking the kit template and the member's git history disproved it ([`probe-live-state-before-acting-on-a-report`](../habits/master.md#probe-live-state-before-acting-on-a-report)). The correction matters, because "the kit seeds it" would have implied a fix (stop seeding, add an update step) where the real finding implies retirement.

## Decision

**Retire the declared standard-version axis. What a member can do is the only version signal.**

1. `evaluate()` no longer reads `standard_version` and no longer returns a `declared` key.
2. `_classify()` derives status from the detectors alone: `drift` (stale capability not removed), `below-floor`, `behind`, `clean`.
3. The `undeclared` status is deleted. It described a condition that was universal and permanent, so it carried no information.
4. Both renderers drop the `declared` column and report detected-only.
5. The federation's own `session.config.json` drops the field.
6. A leftover `standard_version` in any member's config is **inert** — not read, not compared, not reported. No brief is issued to remove it; a dead field costs nothing, and asking every Architect to delete a line the code ignores is churn without benefit.

Detection remains authoritative, read-only, and detect-don't-heal, exactly as ADR-0047 established. This ADR removes the vestigial half of that decision; it does not weaken the surviving half.

## Alternatives Considered

**Restore the axis — have each member stamp its own declaration at adoption.** Rejected. It makes every Architect hand-maintain a number that code derives with certainty, which is a P16 violation adopted deliberately. It would need a brief to every member, a step in the adoption ritual, and a new conformance miner to catch the members who forget — machinery whose entire yield is detecting a lie no member has ever told, in a system where the honest answer is already computed locally at every session start.

**Keep the field but auto-write it at session end.** Rejected as the worst of both. A value written by code and then read back by code is a cache, not a check: it can only ever agree with the detectors, so the comparison becomes tautological while the storage and staleness risk remain real.

**Leave it alone.** Rejected. Nothing was unsafe, but the fleet view showed a permanent `undeclared` column on every healthy member — a standing false signal that trains the reader to ignore the report, which is precisely how a real finding gets missed later.

## Consequences

- The fleet parity view reads clean: the members now render `ok: v1.4.0 (current)` where they previously rendered `undeclared` forever. A future exception will stand out instead of hiding in a column that always looked wrong.
- One less thing an Architect must remember at adoption, and one less field that can be wrong.
- **Loss, stated plainly:** if a member's `standard_check.py` were itself tampered with or replaced, the detectors would report on the tampered manifest with nothing to contradict them. The declared field was never a real defense here either — it is member-authored, so the same actor could edit both — but the theoretical cross-check is gone. The genuine control is the federation-side **harness-currency** axis, which byte-compares each member's shipped substrate against the federation's own copy; that is unaffected and remains the reason a member cannot silently diverge.
- That one member keeps an inert `standard_version` line until its own Architect happens to tidy it. Harmless by construction, and pinned by a test asserting a wildly wrong claim changes no verdict.

## References

- [ADR-0047](0047-versioned-standard-substrate-and-rollout-reconciliation.md) — versioned standard substrate + rollout reconciliation; establishes detection-is-authoritative, which this ADR carries to its conclusion.
- [ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md) — the federation writes only its own generated substrate, never a target's authored files. The structural reason the field could never be federation-maintained.
- [P16 `avoid-duplication`](../principles/master.md#p16--avoid-duplication) and [`single-source-and-deliver`](../habits/master.md#single-source-and-deliver) — the hand-maintained duplicate is the prohibited move.
- [`probe-live-state-before-acting-on-a-report`](../habits/master.md#probe-live-state-before-acting-on-a-report) — the kit-seeding inference was checked against the kit and the member's git history before being acted on.
- Session 90 — the v1.4.0 rollout that surfaced the dead axis.
