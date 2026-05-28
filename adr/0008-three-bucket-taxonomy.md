# ADR-0008: Three-bucket taxonomy — principle, habit, preference

**Status:** Accepted
**Date:** 2026-05-22 (initial draft 2026-05-21; revised and accepted 2026-05-22)
**Deciders:** the user, Federation Architect

## Context

Late in bootstrap session 1, the user issued a correction that surfaced a missing distinction in the federation's model. I had read the Recipe Box Architect's role doc and silently adopted its no-compound-Bash rule for myself, reasoning from the recursive principle ([ADR-0003](0003-federation-architect-is-a-participant.md)) that *principles I help ship apply to me*. The user pushed back: that rule is a **tactical habit** Recipe Box Architect adopted because of a specific failure mode (permission-prompt fire from compound commands not matching its allow-list prefixes), not a federation-general principle. The recursive principle covers principles, not habits — and not the user's working preferences either.

Three categories were named in that exchange:

1. **Principle** — a claim about Architect quality that holds across Architects independent of any one Architect's local situation.
2. **Habit** — a concrete practice. Either implements a universal principle (and propagates with it) or addresses a localized failure mode (and stays where it was learned).
3. **Preference** — how a specific user prefers to be collaborated with. About the user, not the Architect.

The federation's primary deployable output is the set of role-doc edits that flow to participating Architects. Without a locked taxonomy, that output carries ambiguous propagation semantics and the recursive principle would over- or under-apply. The mis-adoption already happened once; without the rule written down, it will happen again.

This ADR locks the taxonomy and propagation semantics. Storage mechanics — where each artifact physically lives, how Architects on different machines read shared files — are deferred to ADR-0009. The contract that the preference storage layer must satisfy is captured in [`specs/user-profile-storage.md`](../specs/user-profile-storage.md).

## Decision

Federation entries fall into exactly one of three buckets. Each bucket has a distinct definition, propagation rule, and provenance bar. The buckets answer two questions: *what kind of claim is being made* and *how does it propagate*.

### Bucket 1: Principle

A property-level claim about Architect quality. Universal by definition.

A principle survives the test: *would this be true if the user were different?* It is about what makes a competent Architect, independent of any one Architect's local situation or any one user's taste.

- **All principles are architect-universal.** There is no narrower scope. Anything that would be a "principle but only for fiduciary systems" demotes to habit — the universality claim isn't general; it's about a specific situation or domain.
- **Propagation:** via the recursive principle ([ADR-0003](0003-federation-architect-is-a-participant.md)). When a principle is approved, every Architect — Federation Architect included — adopts it. Federation Architect drafts per-Architect role-doc edits; user approves; each Architect adopts.
- **Lives in:** `principles/master.md`.

Examples (candidates from Recipe Box Architect, pending the re-pass described in Consequences): decisions must be auditable; ambiguity must be paid down before commitment; the Architect/orchestrator-agent identity boundary must not collapse.

### Bucket 2: Habit

A concrete practice — more opinion baked in than a principle. Habits state *what an Architect should do*; principles state *what should be true of an Architect*. One principle can have multiple habit implementations.

Habits split by applicability:

**Universal habit.** The underlying situation (the failure mode the habit addresses, or the principle it implements) is defensibly present in every Architect's context.

- **Required:** the parent principle is named. If you cannot articulate what principle this habit implements, the universality claim is unsupported. Either downgrade to situation-specific, or draft the missing principle and promote them together.
- **Propagation:** federation-managed. Federation Architect drafts role-doc edits for every Architect; user approves; each lands as its own traceable adoption. Drafted in one pass, applied per-Architect — the workflow is efficient but the audit trail is preserved.
- **Lives in:** federation registry (file location TBD by ADR-0009 — likely `principles/master.md` with a `habits/` subsection or sibling `habits/master.md`).

**Situation-specific habit.** The failure mode is localized to one Architect's context.

- **Required:** failure-mode citation, with date or incident reference where possible. Cold-readable: anyone reading the role doc can see why this exists in *this* Architect.
- **Parent principle is not required.** The failure mode itself is the justification.
- **Propagation:** none. The federation may *surface* the habit to another Architect that hits the same failure mode, but adoption is per-Architect and ad hoc — not federation-managed deployment.
- **Lives in:** the originating Architect's role doc only.

Example of universal: "write an Alternatives section in every ADR" (implements: decisions must be auditable). Example of situation-specific: "no compound Bash commands" in Recipe Box Architect (addresses: allow-list-prefix permission-prompt fire — specific to Recipe Box's tooling configuration).

### Bucket 3: Preference

How a specific user prefers to be collaborated with. About the *user*, not the Architect.

- A preference survives the test: *is this about the user, or about the Architect?* If the only justification is user taste, it's a preference.
- **Propagation:** by user identity, not federation mechanism. Wherever the user is the user, the profile applies. Single base profile per user; per-Architect overrides where the user scopes a preference ("be more exploratory with Federation, more terse with Recipe Box").
- **Provenance bar:** citation of the user statement (date + paraphrase or quote). Lower bar than principles by design — preferences are cheap to add and cheap to revise.
- **Lives in:** user-scoped profile file, addressed by `<user-id>`. Per-Architect overrides live in the Architect's role doc. Storage contract: [`specs/user-profile-storage.md`](../specs/user-profile-storage.md). Mechanics: ADR-0009.

The federation in current use is single-user (the user), but the model is portable: the same shape works for any user, and adding a second user means a second profile file. No specific user is baked into the architecture.

### Bucket comparison

| | Principle | Habit (universal) | Habit (situation-specific) | Preference |
|---|---|---|---|---|
| **Claim about** | Architect quality | Architect practice | Architect practice in a specific context | User preference |
| **Universality** | Universal by definition | Universal by claim (defensible) | Localized to a failure mode | Across the user's portfolio |
| **Federation-managed?** | Yes | Yes | No (reference only) | Yes (single base + overrides) |
| **Propagation mechanism** | Recursive principle | Per-Architect role-doc edits, drafted federation-wide | None | User identity |
| **Parent principle required?** | N/A | Yes | No | No |
| **Provenance bar** | Generalization argument on the page | Parent principle named + failure-mode-breadth argument | Failure-mode citation | User statement citation |

### Classification rules

**Principle vs. universal habit.**
A principle states what should be true of the Architect. A universal habit states what the Architect should do to produce that property. If your candidate names a property, it's a principle. If it names a practice, it's a habit. When the same idea has a "what should be true" formulation and a "what to do" formulation, both can exist — the property is the principle and the practice is a habit downstream of it.

**Universal habit vs. situation-specific habit.**
Two questions, both must pass for "universal":

1. Is the underlying failure mode (or principle the habit implements) defensibly present in *every* Architect's context? Argument on the page.
2. Can you name the parent principle? If not, the universality claim is unsupported.

Default to **situation-specific** when uncertain. Promotion to universal is a federation event with the argument written down, user-approved. The cost of misclassifying universal as specific is that adoption is manual where it could have been managed. The cost of the reverse — misclassifying specific as universal — is the no-compound-Bash failure: shipping a habit to Architects where the underlying situation doesn't apply. The asymmetry says: default narrow.

**Habit vs. preference.**
If the rule addresses a failure mode the Architect encounters in its work, it's a habit. If the rule expresses user taste with no independent Architect-quality argument, it's a preference. Resist the move of recasting every preference as a habit by labeling "user dissatisfaction" the failure mode — that collapses the propagation distinction (habits propagate by failure-mode breadth + parent-principle; preferences propagate by user identity) and loses the point of the taxonomy.

### Propagation rules

**Principles** propagate via the recursive principle ([ADR-0003](0003-federation-architect-is-a-participant.md)). Promoted → Federation Architect drafts per-Architect role-doc edits → user approves → each Architect adopts. Federation Architect adopts too.

**Universal habits** propagate by the same workflow as principles: federation drafts role-doc edits for every Architect, user approves, each adoption is its own traceable role-doc event. One draft, N adoptions. Auto-sync without per-Architect adoption commits is rejected — see Alternatives.

**Situation-specific habits** do not propagate. The federation may scan role docs and surface candidates when another Architect appears to have the same failure mode, but adoption is per-Architect and not federation-managed.

**Preferences** propagate by user identity. The base profile is read by every Architect bound to the user; per-Architect overrides live with the Architect. See [`specs/user-profile-storage.md`](../specs/user-profile-storage.md) for the storage contract.

### Demotion paths

Symmetric to promotion. Each is a federation event with the argument written down, user-approved.

- **Principle → habit (universal).** Rare. Argument: the "principle" turned out to be a practice, not a property. Parent-principle linkage may need to be drafted for any habits that depended on it.
- **Universal habit → situation-specific habit.** The mis-adoption recovery path. Argument: the failure-mode-breadth claim was wrong; the situation doesn't apply universally. Role-doc edits in non-applying Architects are reversed; the habit retains its citation in the originating Architect's role doc with the (now narrower) failure-mode scope.
- **Habit ↔ preference.** Promote a "preference" to a habit if an independent Architect-quality argument is found. Demote a "habit" to a preference if it turns out to be pure user taste.

### Provenance bars

| Bucket | Required |
|---|---|
| Principle | Generalization argument on the page — why this is a claim about Architect quality, independent of any one Architect or user. Cross-system support is evidence, not requirement. |
| Universal habit | Parent principle named. Failure-mode-breadth argument on the page — why the underlying situation is present in every Architect's context. |
| Situation-specific habit | Failure-mode citation with date or incident reference. Cold-readable why-this-Architect. |
| Preference | Citation of the user statement (date + paraphrase or quote sufficient). |

## Alternatives Considered

- **Two buckets (principles + everything else).** Rejected — collapses the failure mode that triggered this ADR. The recursive principle would still over-apply, because "everything else" lumps user preferences and Architect habits together with no propagation distinction.
- **Scoped principles** (`<system-id>-specific` or domain-specific principles, e.g., a fiduciary-systems-only subset). Rejected — collapses the principle/habit boundary. Anything that needs a scope qualifier on a property claim is either a habit or a per-domain subset invisible at the federation level. Keeps the principle bucket sharp.
- **Bake habits into principles with applicability constraints.** Rejected — overloads scope tags to mean two unrelated things (where can it apply vs. is the underlying situation universal). Cleaner to keep the bucket as the propagation axis.
- **Single-bucket habits without the universal/situation-specific split.** Rejected — the propagation question demands the split. Federation-managed deployment looks different from reference-only logging.
- **Universal habits auto-propagate without per-Architect role-doc edits** (one shared file, all Architects read it). Rejected — auto-sync removes the per-Architect audit trail and the failure-recovery path. If a "universal" classification turns out to be wrong, you need to know what landed where to reverse it. Each role-doc adoption is a traceable event.
- **Collapse preferences into habits** by treating user dissatisfaction as a failure mode. Rejected — different propagation mechanism (user identity vs. failure-mode breadth + parent principle). Collapsing loses the distinction the model exists to make.
- **More buckets** (e.g., split habits into tactical vs. tooling, or split preferences into stylistic vs. operational). Rejected as premature. The current splits fall out of real classification needs; further splits should wait for actual ambiguity in practice.
- **Decide storage shape in this ADR.** Rejected — taxonomy and storage are separable. ADR-0009 will pick mechanics; the storage contract for preferences is at [`specs/user-profile-storage.md`](../specs/user-profile-storage.md).

## Consequences

### Immediate

- `principles/master.md` (priority in the session handoff) can be stood up with only principles in it. Universal habits get a registry destination decided in ADR-0009.
- Every entry in any federation artifact carries a bucket tag. Universal habits additionally name a parent principle.
- The Recipe Box Architect's no-compound-Bash rule is locked as a situation-specific habit. Not federation-deployed; remains in Recipe Box's role doc with its allow-list-prefix failure-mode citation.
- **Recipe Box candidate re-pass required.** Several entries tagged as principles in session 2 may be habits whose parent principle needs to be extracted. Likely re-classifications:
  - "Session-end handoff before sign-off" → universal habit. Parent principle candidate: *session context must survive cold-restart; continuity is the Architect's responsibility, not the user's memory.*
  - "Versioned role doc + CHANGELOG" → universal habit. Parent principle candidate: *role-doc evolution must be auditable over time.*
  - "Exhaust-questions in planning" → universal habit. Parent principle candidate: *ambiguity is paid down before commitment, not during execution.*
  - The re-pass is its own task; the principle extractions above are starting points, not decisions.
- The shared user-profile artifact gets a name slot: `<user-id>-profile.md` (or whatever ADR-0009 picks). For the current single-user instance, that resolves to a `user-profile.md`-shaped file. The shape is portable; the instance is configuration.

### Scope of ADR-0003 (recursive principle)

ADR-0003 currently states that principles apply to Federation Architect. Under this taxonomy, **universal habits should also apply to Federation Architect** by the same logic: the federation deploys them to every participating Architect, and Federation Architect is a participant. This may warrant a one-line clarification to ADR-0003 (or a note in its References) once this ADR is Accepted. Preferences propagate by user identity, so they apply to Federation Architect by virtue of the user being the user — no ADR-0003 amendment needed for that.

### Deferred to ADR-0009

- Habit registry file location and format.
- User-profile storage mechanics, satisfying [`specs/user-profile-storage.md`](../specs/user-profile-storage.md).
- Promotion/demotion workflow details — what triggers a review, how the argument is captured, how the user approves.
- Re-confirmation cadence for stale preferences and habits.

### Risks

- **Misclassified universal habits.** A habit tagged universal that isn't will auto-roll to every Architect — the no-compound-Bash failure replayed at higher cost. Mitigation: the parent-principle requirement (no universality claim without a named parent principle), the federation-event classification gate, and the symmetric demotion path.
- **Bucket creep and prestige creep.** Every entry gets tagged "principle" because that's the prestigious bucket; or every habit gets tagged "universal" for the same reason. Mitigation: default-narrow rules (default to habit between principle/habit; default to situation-specific between the two habit types), the parent-principle bar for universal habits.
- **Over-tagging friction.** Every entry now carries a bucket and, for universal habits, a parent-principle link. The friction is real but on the right side — entries that won't propagate cleanly are exactly the ones that need the most attention up front.

## References

- [ADR-0001: Record federation decisions as ADRs](0001-use-adrs.md) — drives this ADR's existence.
- [ADR-0003: Federation Architect is a federation participant](0003-federation-architect-is-a-participant.md) — the recursive principle whose scope this ADR clarifies (principles and universal habits ride it; preferences propagate by user identity).
- [ADR-0007: GitHub as system storage; data excluded by policy and gitignore](0007-github-as-system-storage-data-excluded.md) — establishes that the user profile is data and therefore gitignored.
- [`specs/user-profile-storage.md`](../specs/user-profile-storage.md) — the storage contract ADR-0009 must satisfy.
- [federation-arch.md](../federation-arch.md) — to be updated to v0.4.0 in a subsequent session step, formally adopting principles (and the participation extension to universal habits) per [ADR-0003](0003-federation-architect-is-a-participant.md).
- [session-handoff.md](../session-handoff.md) — 2026-05-21 entries record the originating context.
