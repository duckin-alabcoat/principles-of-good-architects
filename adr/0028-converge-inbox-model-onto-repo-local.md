# ADR-0028: Converge the federation inbox model onto repo-local; a custom-runtime member's Option B is the vehicle

**Status:** Accepted
**Date:** 2026-06-21
**Deciders:** the operator, Federation Architect

## Context

[ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md) made redistribution delivery concrete (the federation copies a proposed-edit brief into the target's configured inbox over the shared volume) but had to accommodate **two inbox models that coexist today**:

| Model | Who | Inbox read at session start |
|---|---|---|
| **Repo-local** (the kit standard) | every kit-based Architect | `<target-repo>/proposed-edits/<arch-id>/pending/` (the `inbox:` value in `session.config.json`) |
| **Read-federation-direct** | A member on a custom runtime | the federation data root's `proposed-edits/<arch-id>/pending/`, read cross-volume; no local copy |

ADR-0027 left federation-wide convergence as an explicit, un-taken follow-up ("converge the inbox model federation-wide … Tracked, not done here") and floated read-federation-direct as the likely target. Two things since then sharpen the call:

1. **The precondition ADR-0027 named is met.** ADR-0027's *Alternatives* rejected universal read-direct "for now" because it couples every session to the shared volume being mounted and would force re-pointing every kit `session.config.json`. Session 41 made the shared volume + `ssh runner` **standard substrate** ([ADR-0026](0026-cross-machine-execution-is-standard-substrate.md)) and validated it live — so the volume is now dependable. But the *offline-coupling* objection to read-direct does not go away: an Architect that reads its inbox from its own repo works with the volume unmounted; one that reads the federation data root cross-volume does not.

2. **Only a member on a custom runtime reads federation-direct, and Option B moves such a member onto the repo-local model.** Option B migrates a custom-runtime member to the reference `session.py` + `session.config.json`, which carries the kit's repo-local inbox. So the inbox-model convergence and Option B are not two problems — **Option B is the convergence**.

This ADR resolves both ADR-0027's deferred convergence follow-up and the Option B follow-up together, and (per the convergence brief's prediction) sanctions a custom-runtime member's interim Option-A divergence.

## Decision

**Converge the federation onto the repo-local inbox model — the existing kit standard — not onto read-federation-direct.** Every Architect reads its inbox from its own repo (`proposed-edits/<arch-id>/pending/`, the `session.config.json` `inbox:` value); the federation delivers uniformly by copying each brief into that repo-local inbox over the shared volume ([ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md) §1). The read-federation-direct special case is retired once every member that reads federation-direct migrates.

Concretely:

1. **Option B is the convergence step.** Migrate a custom-runtime member to the reference `session.py` + `session.config.json`, re-homing any of its startup work that is not part of the session ritual outside the ritual harness. On migration, a custom-runtime member's federation inbox becomes repo-local (`<its repo>/proposed-edits/<arch-id>/pending/`) and the federation begins delivering to a custom-runtime member by copy like every other Architect.
2. **Interim Option-A divergence is sanctioned.** Until B lands, a custom-runtime member stays on Option A (its own runtime + injected `CANON.md`/`STANDARD.md`) reading the federation data root direct. This is explicitly permitted additive divergence under [ADR-0023](0023-standard-operating-substrate.md); it is interim, not the target.
3. **No change for the kit-based Architects.** They are already on the target model. Convergence is a one-system migration, not a fleet change.
4. **Sequencing — Option A before Option B.** A custom-runtime member's still-pending Option-A brief (canon + standard injection) lands first; it is the floor-meeting convergence and is low-risk. Option B (the runtime migration) is the larger, separate piece and follows.

## Alternatives Considered

- **Converge onto read-federation-direct everywhere (ADR-0027's floated target).** Rejected. It would re-point every kit `session.config.json` at a discovered federation path, make every Architect's inbox sweep fail when the volume is unmounted, and move the fleet *off* its current working standard to chase a custom-runtime member — backwards. Repo-local keeps each Architect self-contained and offline-safe; the federation already delivers into repo-local inboxes by copy and that path is validated.
- **Leave both models permanently (do nothing).** Rejected as the standing state, kept as the *interim* state. Two models is a permanent model-awareness tax on every delivery and a standing exception in the standard substrate ([ADR-0023](0023-standard-operating-substrate.md) wants same pipes everywhere). Acceptable short-term (a custom runtime may be load-bearing), not as the end state.
- **Do Option B without first landing Option A.** Rejected. Option A delivers the convergence that matters (shared canon + standard injection) at low risk and is already drafted/delivered; Option B is a larger runtime migration. Bundling them delays the cheap, high-value piece behind the expensive one.

## Consequences

- **One inbox model federation-wide once a custom-runtime member migrates.** Delivery stops being model-aware: always copy-into-repo-local-inbox. The ADR-0027 read-direct branch and its model table collapse to a single row.
- **Every Architect's inbox is offline-safe** (in its own repo), preserving the property the kit standard already has.
- **Option B is now scoped and sanctioned**, closing the follow-up owed since session 37. The migration is non-trivial — a custom runtime may do more at startup than the session ritual, and that work must be re-homed outside `session.py` — and must be performed **in that member's repo by its own Architect session** ([P13](../principles/master.md#p13--single-writer-per-state) / [P4](../principles/master.md#p4--identity-boundaries-non-collapsing)); the federation drafts/scopes, it does not execute in that member's repo.
- **Interim two-model state persists until B lands**, so the federation must stay model-aware on delivery in the meantime (it already is).
- **Downstream work:** (1) land Option A (drafted, delivered, pending a custom-runtime member's session); (2) draft + execute the Option B migration brief; (3) on B landing, update [ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md)'s model table to single-row and retire the read-direct branch (via a short superseding note, not in-place edit of the Accepted ADR). **Step (3) done 2026-09-26 (WI-0012):** the note is at the head of ADR-0027. The old location now refuses by name on both sides: `deliver.RetiredInbox` on send, and the mail poller's `retired` bucket for a brief left there.

## References

- [ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md) — shared-volume inbox delivery; whose convergence follow-up this ADR resolves.
- [ADR-0026](0026-cross-machine-execution-is-standard-substrate.md) — the shared volume + `ssh runner` substrate that made the precondition true.
- [ADR-0023](0023-standard-operating-substrate.md) — standard operating substrate ("same pipes everywhere"; additive divergence allowed) — the policy this convergence serves.
- [ADR-0013](0013-receipt-ritual.md) — receipt ritual; the inbox sweep both models implement.
- A custom-runtime member's canon/standard convergence brief (a proposed edit in its inbox) — Option A (pending) + the Option B follow-up this ADR scopes.
- Federation session 43 (2026-06-21) — the occasion for taking up the deferred convergence decision.
