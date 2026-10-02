# ADR-0023: Standard operating substrate — systems converge to one plumbing; additions allowed, subtractions not

**Status:** Accepted
**Date:** 2026-06-14
**Deciders:** the operator, Federation Architect

## Context

Each system's Architect evolved independently, and they were stood up at different times — so each grew its own version of the **operating substrate**: how it stamps sessions, where it stores session state, how it versions its role doc, how (or whether) the principle/habit set reaches it, how it guards risky operations.

The result is **accidental divergence in the plumbing.** Session 31 surfaced it concretely while rolling out the [ADR-0022](0022-architect-ingest-distillation-and-code-channel.md) code channel: the shared `session.py` harness would not port to a member Architect that predated it, because that Architect stored one file per session while the harness assumes one running log (`session-handoff.md` with newest-first entries); it uses a 2-part version while the harness expects 3-part semver; its SESSION LOG has a different column layout from the one the harness writes; and it has no `.claude/settings.json` and therefore no hooks at all (the root of its missing-session-stamp symptom).

The question was how to make the harness serve every system. Three shapes were on the table: teach one script every system's storage model (a pluggable backend), keep a separate ritual script per system, or converge the systems onto one model. The first two *preserve* the divergence; the third *removes* it.

the operator's direction settles it: **the divergence is accidental, not deliberate — this is plumbing, and the same plumbing is to be used everywhere.** The prior caution against "forcing consistency for its own sake" ([ADR-0008](0008-three-bucket-taxonomy.md); federation-arch §3) was written to protect *intentional* divergence between systems. Mechanical substrate that diverged only because systems were built at different times is not what that caution protects. With intentionality removed, standardizing is the correct move and is the operator's to call.

the operator added the governing constraint: **local systems may have additions, but not subtractions.** A system can need something the others don't (a principle it invented for its own domain); it can carry that. It cannot drop any part of the shared standard.

## Decision

**The federation defines one standard operating substrate. Every participating system converges to it. Local systems may add on top of it; they may not subtract from it.**

### 1. The standard substrate (the "plumbing")

The federation owns a single standard for the mechanical layer every Architect runs on. Its own substrate is the **reference implementation** (it has the most evolution behind it, and it already runs the whole thing). The standard covers:

- the `session.py` harness + per-system `session.config.json` ([ADR-0020](0020-session-rituals-are-a-code-harness.md));
- the **session-state model**: one running `session-handoff.md` with newest-first dated entries + a SESSION LOG table;
- **3-part semver** versioning of the role doc;
- the **canon channel**: `CANON.md` generated from the registries and injected into context each session ([ADR-0022](0022-architect-ingest-distillation-and-code-channel.md));
- the **`check-bash` structural guard** and the `.claude/settings.json` hook wiring (`SessionStart` start, `PreToolUse(Bash)` guard);
- the **receipt-ritual inbox** layout (`<data-root>/proposed-edits/<architect-id>/...`).

### 2. Converge the systems; keep the tool simple

Systems adopt the standard substrate. Accidental divergences (storage model, version format, column counts) are **migrated to the standard, not accommodated by the tooling.** The harness stays single and simple — one model, one code path, copied by reference per [ADR-0022](0022-architect-ingest-distillation-and-code-channel.md). It is explicitly **not** generalized into a pluggable multi-model engine. We standardize the systems rather than teach the tool every variant.

### 3. Additions allowed, subtractions not — the floor rule

The standard substrate, together with the universal principle/habit set, is a **mandatory, complete floor.**

- A system **MAY add**: a system-specific operating principle or habit it needs that others don't (the situation-specific class of [ADR-0008](0008-three-bucket-taxonomy.md); lives locally in the role doc's "system-specific operating principles" section), and genuinely system-specific substrate it requires *on top of* the standard.
- A system **MAY NOT subtract**: it cannot opt out of any standard plumbing component, and it cannot drop any universal principle or habit.

Divergence is permitted **only in the additive direction.** A floor with opt-out holes is not a floor.

### 4. Scope line — plumbing, not domain

The standard governs the Architect **operating substrate** — how the architect runs sessions, ingests canon, guards operations, versions itself, receives edits. It does **not** touch the system's **domain**: its mission, its agents, its data model, what it actually does. Standardization stops at the plumbing. *Same pipes, different houses.*

### 5. Convergence is migration-forward, not history-rewrite

A system adopts the standard **going forward.** Pre-existing artifacts in the old shape (e.g., a pre-standard member's existing per-session files) are **archived as-is**, not retroactively reformatted. Convergence is a clean cutover, not a backfill.

### 6. Rollout

Federation dogfoods first (it already runs the standard). Then system by system via the receipt-ritual / retrofit path ([ADR-0014](0014-existing-architect-retrofit.md)), **starting with a member already exhibiting the gap** (one whose canon + guard halves are already delivered to its inbox). Fresh systems are born standardized: the bootstrap kit already ships the standard substrate as of kit v0.8.0.

## Alternatives Considered

- **Pluggable multi-model harness** (one `session.py` that adapts to each system's storage via config-selected backends). Rejected — it preserves divergence we've decided we don't want, grows a new branch per system quirk, and re-introduces the complexity convergence removes. Standardizing the systems is simpler than teaching the tool every variant.
- **Per-system ritual scripts** (each system keeps its own stamping script). Rejected — it re-forks the harness, the exact drift the [ADR-0022](0022-architect-ingest-distillation-and-code-channel.md) "one shared script, copied by reference" decision eliminated this session.
- **Converge a pre-standard member's full history** (reformat all of its prior per-session files into the new model). Rejected as needless — §5 makes convergence forward-only; old artifacts archive as-is.
- **Standardize but allow opt-outs (subtractions).** Rejected — opt-outs re-fragment the baseline; the value of a standard is that it is *guaranteed present everywhere*. Additions-only preserves that guarantee while still meeting genuine local needs.
- **Status quo: leave systems divergent, share only canon + guard.** Rejected — it leaves the session-ritual gap (a pre-standard member's silent missing stamp) unsolved on a per-system basis forever, and forecloses a uniform substrate.

## Consequences

- **The federation's mission expands.** Beyond Bootstrap / Curate / Redistribute, the federation now **owns the standard operating substrate** — defines it, propagates it, and is its reference implementation. Warrants a federation-arch mission note + version bump (handled as a separate role-doc edit).
- **[ADR-0008](0008-three-bucket-taxonomy.md) is sharpened.** Its situation-specific class is now explicitly the *additive* lane: local divergence is respected only as an addition. The universal set and the standard substrate are non-subtractable. The "respect intentional divergence" posture narrows to "respect additive divergence."
- **Each existing system needs a one-time convergence retrofit** (migrate substrate forward, archive old, wire hooks, adopt semver). Members already exhibiting the gap first; then the rest. Fresh systems need nothing — the kit already ships the standard.
- **The session.py "pluggable storage" idea floated in session 31 is explicitly not built** — superseded by convergence. This keeps the harness simple.
- **Version-format normalization:** systems on 2-part versions move to 3-part semver going forward (e.g. `vX.Y` becomes `vX.Y.0` as the convergence baseline).
- **A home for "additions vs the floor" is needed in the role-doc model:** the standard substrate + universal set is declared non-subtractable; local additions live in the role doc's system-specific operating-principles section. The bootstrap kit's role-doc template already separates these.
- **A pre-standard member's missing-stamp blocker dissolves.** Its code-channel package was stuck on bending the harness to it; convergence reverses it — the member adopts the standard session model and the existing harness just works.

## References

- [ADR-0008](0008-three-bucket-taxonomy.md) — three-bucket taxonomy; situation-specific = the additive divergence lane this ADR sharpens.
- [ADR-0020](0020-session-rituals-are-a-code-harness.md) — the session harness that is part of the standard substrate.
- [ADR-0022](0022-architect-ingest-distillation-and-code-channel.md) — the code channel and "one shared script, copied by reference" decision the standard rides on.
- [ADR-0012](0012-per-architect-repo-conventions.md) / [ADR-0014](0014-existing-architect-retrofit.md) — per-architect repo conventions and the retrofit path convergence runs through.
- federation session 31 (2026-06-14) — the code-channel rollout that surfaced the divergence and produced this decision.
