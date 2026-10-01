# ADR-0017: Bootstrapping new Architects is a first-class federation responsibility

**Status:** Accepted
**Date:** 2026-05-28
**Deciders:** the operator, Federation Architect

## Context

Since v0.1.0, the Federation Architect's mission ([federation-arch.md §2](../federation-arch.md)) has been three responsibilities, all describing one loop: **Aggregate** (read producer files), **Distill** (cluster into principles and habits), **Redistribute** (propose role-doc edits back). The README frames the whole system the same way — "consolidates learnings across the operator's portfolio … and proposes role-doc edits back."

Bootstrapping — standing up a brand-new Architect from cold — was built and documented, but only as plumbing *for* that loop. [PROCESS.md](../PROCESS.md) §"Fresh-Architect onboarding steps" and the [bootstrap kit](../bootstrap-kit/) (10 templates, currently v0.6.0) cover it in full mechanical detail, but PROCESS.md frames it as "onboarding for *participating* Architects" — i.e., a precondition to get a system into the aggregation loop, not a capability with standalone value. The mission never names it; the README never mentions it.

Two facts contradict that framing:

1. **It is already operational and valued.** An earlier session bootstrapped a fresh Architect end-to-end from the kit, and the operator judged the result a success (2026-05-28). The Federation Architect performed the bootstrap — it is the actor, not a bystander to an operator activity.

2. **the operator states it as core mission.** Asked to name POGA's core mission (2026-05-28), the operator listed three things as a flat set: (1) bootstrapping new Architects; (2) curating feedback from existing Architects; (3) providing principles and habits back to the Architects, so that a lesson learned with one Architect benefits all of them. Items 2 and 3 map cleanly onto the existing Aggregate+Distill / Redistribute loop. Item 1 is absent from the stated mission entirely.

This is the same kind of gap [ADR-0003](0003-federation-architect-is-a-participant.md) closed (re-grounding a founding-brief framing) and that [P13](../principles/master.md#p13--single-writer-per-state) closed (naming existing practice as explicit principle): the practice exists and is sound; the canonical statement of it lags.

## Decision

**Bootstrapping new Architects is a first-class Federation Architect responsibility, co-equal with curation and redistribution, and named explicitly in the mission.**

The §2 mission is reframed from three aggregation-loop steps to three co-equal responsibilities (a flat list, not a ranking — the operator, 2026-05-28):

1. **Bootstrap.** Stand up new Architects from cold. Own and execute the fresh-Architect path: maintain [PROCESS.md](../PROCESS.md) and the [bootstrap kit](../bootstrap-kit/), drive the onboarding runbook, and register each new Architect in [portfolio.md](../portfolio.md). Each fresh Architect ships pre-loaded with the current accepted principles and universal habits, so it starts federation-bound rather than adopting them one receipt-ritual at a time. (Existing-Architect retrofit per [ADR-0014](0014-existing-architect-retrofit.md) is the sibling path for already-evolved Architects.)
2. **Curate.** Read each participating system's `architect-learnings.md` producer file; pull new entries; cluster across systems; distill durable cross-system observations into **principles** and **universal habits** per the [ADR-0008](0008-three-bucket-taxonomy.md) taxonomy. Maintain the registries. (This is the former **Aggregate + Distill**, collapsed under the operator's "curating feedback" framing.)
3. **Redistribute.** Propose role-doc edits to each affected Architect when a principle or universal habit is ready to ship. the operator approves; each accepted edit bumps the receiving role doc. (Unchanged — the former **Redistribute**.)

The three are co-equal. Bootstrap *feeds* curate (a bootstrapped Architect becomes a producer of learnings), but it is not subordinate to it — standing up a competent Architect to run a project is a delivered outcome in its own right.

Ownership clarification this resolves: the Federation Architect is the **actor** for bootstrapping, not merely a provider of operator tooling. PROCESS.md's audience line ("the operator, future operators, and the Federation Architect") stays — the runbook can be operator-driven — but the *responsibility* sits with the Federation Architect, consistent with how that bootstrap was actually executed.

## Alternatives Considered

- **Leave the mission as the three-step aggregation loop; just refresh the README to mention bootstrapping.** Rejected. The README is the symptom; the mission statement is the cause. Surfacing bootstrapping in the front door while the role doc's own mission omits it leaves the two out of sync and re-creates the gap the moment the README is next rewritten from the role doc.
- **Add bootstrapping as a fourth mission item, keeping Aggregate and Distill separate.** Rejected in favor of matching the operator's own three-item framing (curate = aggregate+distill). Aggregate and Distill are two mechanical phases of one responsibility ("curate feedback"); splitting them at mission grain over-articulates the loop relative to how the operator describes the system. The aggregate/distill granularity is preserved in the responsibility's description and in PROCESS.md's flow diagram, not lost.
- **Treat bootstrapping as an operator activity POGA merely provides tooling for (PROCESS + kit), not a Federation Architect responsibility.** Rejected. That bootstrap had the Federation Architect perform it directly, and kit/PROCESS maintenance has always happened in Federation Architect sessions. Naming it an operator-only activity would contradict the practice and orphan the kit's ownership.

## Consequences

- **MAJOR role-doc bump** for [federation-arch.md](../federation-arch.md) per its §10 versioning policy (mission change → MAJOR): v0.11.3 → **v1.0.0**. The §10 `1.0.0` milestone gates (a producer file ingested, a principle shipped+accepted in another Architect's repo, an Auditor review) are superseded by this bump — see the §10 amendment below.
- **§10 milestone amended.** The original three `1.0.0` gates were a bootstrap-era proxy for "the system is real." A live fresh-Architect bootstrap plus a named, exercised three-part mission is a stronger maturity signal than that checklist. §10's `1.0.0` definition is rewritten to record that 1.0.0 lands on this mission reframe, with the original gates noted as the superseded prior definition.
- **§2 rewritten** to the three co-equal responsibilities above. **§3 "What I do"** gains explicit bootstrap bullets (own PROCESS.md and the kit; execute fresh-Architect onboarding; register new Architects in the portfolio).
- **README rewritten** — currently stale (Status "Bootstrapping, 2026-05-21"; directory layout omits `bootstrap-kit/`, `habits/`, `users/`; mission framed as aggregation-loop only). The refresh surfaces all three mission responsibilities and corrects the stale state. This is the GitHub front door for both the private repo and the sanitized public mirror.
- **PROCESS.md framing nudge** — the "onboarding for participating Architects" framing softens; bootstrapping is named as a federation responsibility the runbook serves, not merely a precondition. Mechanical steps unchanged.
- **Public mirror drifts** until re-synced. The mirror is manual-sync (no automation); the README/role-doc changes won't reach the public repo until a manual regen. Track as owed work, not blocking.
- **No new constraint on other Architects.** This ADR reframes the Federation Architect's own mission; it ships nothing into any participating Architect's role doc. No receipt ritual required.
- **Does not change the kit or PROCESS mechanics.** The 8-step runbook and 10 templates are untouched; only their framing in the mission/README changes.

## Resolved

- **Version number → 1.0.0** (the operator, 2026-05-28). The mission reframe lands as **v1.0.0**; §10's milestone definition is amended to match (original three gates noted as superseded). the operator also confirmed version-number choices are Architect-owned and need not be cleared with him going forward.

## References

- [federation-arch.md §2](../federation-arch.md) — the mission being reframed.
- [ADR-0003: Federation Architect is a participant](0003-federation-architect-is-a-participant.md) — prior instance of re-grounding a founding-brief framing.
- [ADR-0014: Existing-Architect retrofit](0014-existing-architect-retrofit.md) — the sibling onboarding path (retrofit vs. fresh-bootstrap).
- [PROCESS.md](../PROCESS.md) — the fresh-Architect runbook this ADR elevates.
- [bootstrap-kit/README.md](../bootstrap-kit/README.md) — the kit the bootstrap responsibility owns.
- Source conversation: Federation Architect session 15 (2026-05-28) — the operator's three-item mission statement.
