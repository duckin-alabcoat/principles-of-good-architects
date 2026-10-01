# ADR-0049: `Apply: auto` is the authoring default; `manual` requires a stated reason

**Status:** Accepted
**Date:** 2026-07-15
**Deciders:** the operator, Federation Architect

## Context

[ADR-0039](0039-appliable-brief-schema-and-auto-adopt-mechanism.md) built the strict
appliable-brief schema and the `apply-briefs` engine that applies conforming `Apply: auto`
briefs deterministically, before the model wakes. [ADR-0029](0029-receiving-architects-auto-adopt-at-startup.md)
established that the user's approval already happened upstream at registry-Accept, so a
receiving Architect's adoption is *execution*, not a second gate. Session 66 finished
zero-touch R1: the engine is lifted into the shipped `session.py apply-briefs` and
floor-promoted, so every member auto-adopts conforming briefs fleet-wide.

The engine exists — but nothing routes work to it. ADR-0039 built the capability and
deliberately left the authoring habit unchanged (Fork B, "new briefs only"). The result:
the **next** real wave after the engine shipped (the 2026-07-14 comms-surface redistribution,
~one brief per member) was still authored as `Apply: manual` prose. Consultant inspection of
one member's delivered copy found its mutations were **one role-doc artifact entry, one
optional session-end step, and a literal CHANGELOG block** — all four of which fit the strict
ops (`replace` / `changelog-prepend` / `version-bump`). It was manual by *authoring habit*,
not by necessity. Every such brief that lands `manual` costs a member (and the operator) interactive
startup time to disposition — exactly the residue zero-touch is chartered to remove.

The gap is a policy gap, not a mechanism gap: the engine reads whatever it's handed, and it's
handed prose because prose is the default the drafter reaches for. The consultant's
`2026-07-14-zero-touch-adoption` R2 names the fix — make `auto` the default the drafter must
argue *out* of, not *in* to.

A second, smaller force: `Apply: manual` today carries no obligation to say *why*. A reason-less
manual brief is indistinguishable at delivery from a brief that's manual out of habit. Without a
recorded reason there's nothing to lint against, and the habit-manual case hides among the
genuinely-manual ones.

## Decision

**Redistribution briefs are authored to the strict `Apply: auto` schema by default.** The
drafter reads the target's live role doc ([ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md)
forbids *writes* outside a target's inbox, not *reads*) and writes literal, per-target anchored
ops. **Nothing rests as `manual`.** The guiding rule (the operator's ruling, session 67): every
redistribution is automatic and carried by code — it never consumes the operator's interactive time. There are exactly two
automatic paths, and `manual` is a routing label between them, not a destination:

- **the code path** — strict-schema `Apply: auto`, applied by `session.py apply-briefs` before the
  model wakes (the default; most briefs);
- **the agent path** — the residue that genuinely can't be strict-schema'd, run by the R3 headless
  adoption runner on the Runner (`claude -p`, liveness-guarded, `Verify:`-gated), *also*
  unattended.

Concretely:

1. **Default is `auto`.** If a brief's mutations fit the four strict ops (`replace`,
   `create-file`, `version-bump`, `changelog-prepend` — [ADR-0039](0039-appliable-brief-schema-and-auto-adopt-mechanism.md)),
   author it `apply: auto`. "Fits" is judged against the target's *actual* role-doc text, read
   at authoring time, so the `~~~before` anchors are literal and unique. **Reach for the engine
   before declaring a brief unfit:** a version-line dialect that didn't match (one member wrote its
   version line in a bold-label-plus-code-span dialect) is an *engine gap to close*, not a reason to fall back to a human —
   the `VERSION_LINE` broadening this session (ADR-0039 update note, 2026-07-15) brought that member's
   comms adoption onto the code path. Prefer fixing the code over minting a `manual`.

2. **`manual` requires a stated reason — and routes to the agent path, not to the operator.** A brief
   authored `apply: manual` MUST carry a non-empty `manual-reason:` naming why the *code* path
   can't take it, so the R3 runner (and any auditor) knows why it's on the agent path. Legitimate
   reasons are genuine judgment/complexity the four ops can't express:
   - `requires target-side judgment` — a mutation depends on reading surrounding context
     (renumbering, "also update X elsewhere", set-a-flag-if).
   - `multi-file migration` / `substrate install` — beyond a single anchored role-doc edit.
   - `attended` — the rare class that must run under a human's eye (e.g. a runtime migration with
     live-state risk); even this is *scheduled*, not a standing interactive interrupt.

   What is **not** a valid reason: "the engine didn't parse this member's format" (fix the engine)
   or "it was easier to write prose" (the default this policy removes).

3. **A delivery-time lint enforces (2).** `curate/check-apply.py` (sibling of
   `curate/check-brief.py`) refuses a brief that declares `apply: manual` with no
   `manual-reason`, or that declares no recognized apply mode at all. Run it on every brief
   before delivery, alongside `check-brief.py`. This is the [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)
   pattern ([P15](../principles/master.md#p15--code-for-mechanism-not-judgment)): the
   habit-manual default recurred immediately after the engine shipped, so the check becomes a
   tool, not a thing to remember.

4. **Capability adoptions fold their judgment into the adopted text.** When a brief adopts an
   *optional* capability (like `comms/`), the deterministic part — the role-doc artifact
   registration + CHANGELOG + version bump — is authored as `auto` ops, and the judgment part
   ("author a note when a session warrants it") lives **inside the adopted section itself**, not
   as a separate pending instruction. The member adopts the capability with zero turns and reads
   the when-to-use guidance in its own role doc thereafter. No pending item lingers.

The lint's contract and the schema it enforces are documented for authors in `PROCESS.md`; the
`apply-briefs` engine remains the executable specification of what actually applies.

## Alternatives Considered

- **Leave authoring to the drafter's discretion (status quo, Fork B).** Rejected — that *is* the
  gap. Discretion defaulted to prose the moment the engine shipped; the capability earns nothing
  until authoring changes. A default that must be argued *out of* is the lever.
- **Retrofit the strict schema onto historical prose briefs.** Still rejected (ADR-0039 §4):
  rewriting already-applied or in-flight prose briefs buys nothing. The policy is forward-looking
  — it binds the *next* brief authored, not the archive.
- **Enforce the default with a hard guard that refuses any `manual` brief.** Rejected — a small
  residue genuinely can't be strict-schema'd (multi-file migrations, real judgment). But that
  residue is *not* left to the operator — it routes to the R3 agent path, also automatic. The guard is on
  the *missing reason*, not on `manual` itself. Refusing `manual` outright would just push drafters
  to write a fake `auto` that surfaces at the target anyway.
- **Make the lint dry-run every `auto` brief against the target and fail if it wouldn't apply.**
  Attractive (it catches a broken `auto` brief at delivery, not at the target's startup), but the
  target's own `apply-briefs --dry-run` already provides exactly this check against the target's
  live repo, and baking target-repo resolution into a federation-side lint duplicates the engine.
  Kept out of the lint; the authoring workflow instead verifies each `auto` brief with the
  target's own `--dry-run` before delivery ([P18](../principles/master.md#p18--verify-everything)).

## Consequences

- **Nothing reaches the operator's interactive time.** Routine briefs apply on the code path before the
  model wakes; the residue routes to the R3 agent path on the Runner. `manual` names the routing,
  never a request for the operator to disposition a brief at his own startup. The `manual-reason` is the
  handoff to the R3 runner (and the auditor), not to the operator.
- **Authoring costs more up front, per brief.** The drafter must read each target's live role doc
  and write literal per-target anchors instead of one prose brief adapted N ways. That cost is
  paid once by the federation (the actor with the whole fleet in view) instead of N times by N
  members at startup — the correct place for it ([P10](../principles/master.md#p10--architect-owns-operational-substrate)).
- **A new delivery-lint step.** `check-apply.py` joins `check-brief.py` in the pre-delivery
  routine. Both are cheap, fail-loud, and federation-only.
- **`manual` becomes self-documenting.** Every future manual brief records its own reason, so an
  auditor (or a later distillation pass) can see at a glance which manual briefs were necessary
  and which were habit — and the habit ones stop happening because the lint makes the reason
  mandatory.
- **Engine gap closed, not worked around.** One member's role-doc version line
  (a bold-label-plus-code-span dialect) didn't match the apply engine's strict `VERSION_LINE`, which would
  have forced a `manual`. Under *everything-code*, that is an engine bug: `VERSION_LINE` was
  broadened (style-preserving, ADR-0039 update note 2026-07-15) so that member auto-adopts like every
  other member. The lesson is doctrine now (Decision §1): a parse gap is fixed in code, never
  paid down with a human.
- **Immediate workload (this session, R2.3):** redraft the in-flight comms-surface wave. Live-state
  check found some of the "pending" targets had *already applied* the manual
  brief — the federation's staging view was stale ([`probe-live-state-before-acting-on-a-report`](../habits/master.md#probe-live-state-before-acting-on-a-report)).
  The remaining pending targets — **including the member with the odd version line** — all get strict-schema
  `auto` redrafts, each verified against the target's own `apply-briefs --dry-run`. Zero briefs land
  `manual`.

## References

- [ADR-0039](0039-appliable-brief-schema-and-auto-adopt-mechanism.md) — the strict schema + engine this routes work to.
- [ADR-0029](0029-receiving-architects-auto-adopt-at-startup.md) — the user gate is upstream at registry-Accept; adoption is execution.
- [ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md) — delivery writes only into a target's inbox; reading the target's role doc to author anchors is allowed.
- [ADR-0031](0031-delivery-integrity-self-contained-briefs.md) — self-contained, per-target briefs; `check-brief.py` is the sibling delivery guard.
- [ADR-0046](0046-per-system-comms-surface.md) — the comms-surface capability whose adoption wave is R2.3's first `auto` workload.
- `2026-07-14-zero-touch-adoption` (consultant brief, R2) — the prompting recommendation.
- [P15 `code-for-mechanism-not-judgment`](../principles/master.md#p15--code-for-mechanism-not-judgment) · [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence) · [P18 `verify-everything`](../principles/master.md#p18--verify-everything).
