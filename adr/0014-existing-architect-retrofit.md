# ADR-0014: Existing-Architect retrofit mechanism

**Status:** Accepted
**Date:** 2026-05-26
**Deciders:** the user, Federation Architect

## Context

The federation's onboarding roadmap is heavily cold-start oriented: a bootstrap kit (forthcoming, separate work) will stand up a new Architect from a template that already references the current set of accepted principles and universal habits. That path doesn't fit the Architects that *predate* federation — Recipe Box Architect today, and (whenever they're brought in) Home Hub, Trail Log, Campaign Tracker, and Arcade Architects. Each of them has an evolved role doc, possibly an `architect-learnings.md`, and possibly principle-shaped statements buried in prose. None of that was written against the current federation registries; none of it can be bulk-inherited from nothing.

Session 7's review surfaced three gaps with a common shape:

1. **Retrofit path for existing Architects.** No spec'd mechanism for reconciling an evolved role doc against the current set of federation principles and universal habits.
2. **Existing producer-side material we don't know about.** Architects evolved independently before federation existed; they may already hold principle-shaped practices in their role docs that have never been registered. Onboarding needs a discovery step.
3. **How federation work flows to the Federation Architect itself.** [ADR-0003](0003-federation-architect-is-a-participant.md) and [ADR-0008](0008-three-bucket-taxonomy.md) bind Federation Architect to every accepted principle and every accepted universal habit. [ADR-0010](0010-registries-canon-only-sidecar-history.md) makes the role doc the canonical record of adoption. But the *trigger*, *cadence*, *sweep mechanism*, and *self-promotion guard* (Federation Architect both producing and consuming federation edits) are unspec'd. Sessions 5 and 6 worked because the same session drafted and adopted in one breath; that won't scale.

The three gaps fit naturally as one ADR. Producer-side material discovery folds in as the discovery step of the initial retrofit. Federation Architect is the special case — same mechanism with a structural twist to prevent same-agent self-licensing.

This ADR depends on the receipt ritual ([ADR-0013](../briefs/adr-0013-receipt-ritual.md), in draft as of this ADR's writing). The retrofit consumes the receipt ritual to land its outputs. While ADR-0013 is `Proposed` and not yet `Accepted`, this ADR cites the brief, and its status is `Proposed (pending ADR-0013 acceptance)`.

Relevant prior ADRs:
- [ADR-0003](0003-federation-architect-is-a-participant.md) — Federation Architect is a federation participant. Underwrites the requirement that retrofit applies to Federation Architect too.
- [ADR-0008](0008-three-bucket-taxonomy.md) — three-bucket taxonomy. Sets what gets retrofitted (principles universal by definition; universal habits with parent-principle linkage; situation-specific habits don't propagate). The classification rule and the default-narrow stance govern outcome D below.
- [ADR-0010](0010-registries-canon-only-sidecar-history.md) — registries are canon-only; role doc is canon of adoption. Retrofit edits land in role docs; per-Architect adoption is recorded there with version bump and CHANGELOG entry.
- [ADR-0013](../briefs/adr-0013-receipt-ritual.md) (brief) — receipt-ritual mechanism. The retrofit's per-edit landing path; this ADR cites and uses, does not specify.

## Decision

Retrofit applies to *every* existing Architect, with Federation Architect as a special case folded into the same mechanism. Four blocks:

### 1. Trigger model — two modes

**Initial retrofit.** One-time, per existing Architect, on joining federation post-hoc. Federation does a full reconciliation pass against the current set of accepted principles and universal habits. user-triggered with the phrase *"federation, retrofit `<architect-id>` now"* (or equivalent). The initial retrofit is the new mechanism this ADR specifies.

**Ongoing retrofits.** Triggered automatically by every federation-level adoption event going forward — new principle accepted, new universal habit accepted, or status flip on an existing entry. All existing Architects re-evaluate adoption. The ongoing case rides directly on the receipt ritual: each new event produces one proposed edit per affected Architect, dropped per [ADR-0013](../briefs/adr-0013-receipt-ritual.md).

The two modes share outcome semantics (next section); they differ in scope (full registries vs. one event) and in discovery depth (next section).

### 2. Initial retrofit shape — hybrid

The initial retrofit produces **one document** (the retrofit report) for the user's review, and **N small receipt-ritual edits** for landing. One review event for the user; N standard landings via the receipt ritual.

The flow:

1. Federation Architect walks the current registries (`principles/master.md`, `habits/master.md`) against the target Architect's role doc and (where it exists) its `architect-learnings.md`. The walk classifies each entry per §3 below. Producer-side material discovery (the deep walk from role doc *to* federation) happens in this pass.
2. Federation Architect produces a single **retrofit report** at `<data-root>/retrofit-reports/<architect-id>.md` (recommended; see "Open for the user" item 1). The report has a tabular main body (one row per federation principle and per universal habit, columns *Entry / Outcome (A/B/C) / Evidence / Proposed edit*) and a prose appendix for outcome-D producer-side candidates.
3. The user reviews the report and resolves each entry. Outcome assignments are confirmed; proposed edits are approved or revised.
4. Federation Architect drops the approved edits as separate proposed-edit files in `<data-root>/proposed-edits/<architect-id>/` per [ADR-0013](../briefs/adr-0013-receipt-ritual.md). Each is an independent receipt-ritual edit with its own filename.
5. The target Architect's next session-start picks up the pending edits via its receipt-ritual sweep; the user gates each in-session per [ADR-0013](../briefs/adr-0013-receipt-ritual.md); each lands as a role-doc edit with version bump and CHANGELOG entry per [ADR-0010](0010-registries-canon-only-sidecar-history.md).
6. Once every report entry is resolved and every edit has landed, the retrofit report is retained in place (audit surface). It is not deleted.

The report is a one-time artifact per Architect. There is no second initial-retrofit; subsequent federation events flow through the ongoing-retrofit path.

### 3. Reconciliation outcomes — three from-federation, one from-Architect

The walk is two-directional. Walking *from federation registries to the Architect's role doc* produces outcomes A/B/C. Walking *from the Architect's role doc to federation* produces outcome D.

**A — adopted-in-fact.** Architect already does this in practice, often named in its own language. Action: small role-doc edit to make adoption explicit. The edit adds the entry to §"Adopted principles and universal habits" with a provenance pointer to where the practice is already de-facto-named (specific section, prose passage, or `architect-learnings.md` entry). Receipt-ritual landed.

**B — gap-to-close.** Architect doesn't currently do this; should. Action: propose adoption via the receipt ritual. The edit adds the entry to §"Adopted principles and universal habits" with the standard adoption record (parent principle for habits, citation, date). Receipt-ritual landed.

**C — not-applicable.** Per [ADR-0008](0008-three-bucket-taxonomy.md), this outcome is **impossible for principles** — principles are universal by definition; there is no `not-applicable` slot. Valid **only for universal habits** with a recorded reason. When a universal habit genuinely does not apply to a specific Architect (rare — by definition universal habits address failure modes present in every Architect's context), the exclusion becomes a `User override` entry in the Architect's role doc per [ADR-0009](0009-data-storage-mechanics.md) §"Overrides", citing the reason. The federation registry entry remains universal; the override is per-Architect.

**D — producer-side-candidate.** Architect has a practice or principle-shaped statement that federation doesn't have yet — discovered by the role-doc-to-federation walk. Examples: voice-and-style rules with cross-Architect generalization arguments; error-handling conventions; formatting habits; prose narratives that turn out to encode principle-shaped claims. Action: surface to federation's `architect-learnings.md` as a candidate for promotion under [ADR-0008](0008-three-bucket-taxonomy.md)'s classification rule (principle vs. universal habit vs. situation-specific habit). **Producer-side candidates are not auto-adopted into federation registries.** They enter the standard federation promotion flow: federation event, classification, ADR-0008 test, user-approval, registry edit. If the candidate fails the test, it stays as a situation-specific habit in the originating Architect's role doc (the [ADR-0008](0008-three-bucket-taxonomy.md) situation-specific case).

### 3a. Discovery depth — asymmetric

**Initial retrofit: deep.** Walk the *whole* role doc — Voice/Style, error-handling rules, formatting conventions, prose narratives, embedded examples. Evolved role docs accumulate principle-shaped statements that were never registered. This is a one-time cost; the depth pays off because the entire surface is searched once and surfaced as outcome-D candidates in the same report the user is already reviewing.

**Ongoing retrofit: shallow.** Walk only the Architect's `architect-learnings.md` going forward. Justified because, once federation-onboarded, the Architect maintains its own producer-side learnings file from day one — that file is the canonical surface for new producer-side material. The deep walk would re-survey territory already covered by the initial retrofit; the producer-side-learnings habit captures everything that arises after.

### 4. Self-promotion guard for Federation Architect — receipt-ritual self-applied

Federation Architect is both producer (drafts proposed edits) and consumer (adopts edits into its own role doc, per [ADR-0003](0003-federation-architect-is-a-participant.md) recursive participation). Nothing structural currently prevents same-agent same-session self-licensing — Federation Architect could draft a proposed edit and immediately adopt it without independent review. This is a real risk: the federation's only authority gate (the user) only sees what Federation Architect surfaces.

The guard is **structural session-boundary separation**, not a "rely on the user noticing" arrangement:

- **Federation Architect drops its own proposed edits in `<data-root>/proposed-edits/federation-arch/`** — same mechanism as for every other Architect.
- **A separate session-start picks up the edit fresh.** The receiving session is cold-context (or at minimum, a different session boundary) and reads the proposed-edit file as if it were any other receipt-ritual artifact. The user approves; the edit lands.
- **the user-override permitted.** Same-session adoption is allowed *only when the user explicitly approves the adoption itself* — not just the draft. Sessions 5 and 6 effectively did this (drafted and adopted habits in one breath, the user-approved at adoption); the guard formalizes the override rather than removing it. The recommended override syntax is an explicit phrase from the user — *"adopt this now,"* *"skip the receipt ritual for this,"* or equivalent (see "Open for the user" item 5). Federation Architect surfaces overridden adoptions in its end-of-session handoff (which adoption events used the override, and why), so the audit trail is preserved.
- **Scope of the guard.** The guard governs *role-doc edits that constitute adoption events* — version bumps that add to federation-arch.md §4.1 (principles) or §4.2 (universal habits). ADR drafting (architecture decisions) does not go through the guard; ADRs land same-session as today. The distinction is that adoption is a behavioral commitment binding the Architect's future conduct; an ADR is a decision record. Different shape, different risk surface.

The guard makes the recursive case explicit at §Decision rather than burying it as a footnote. Federation Architect is bound to the federation it operates; the binding includes its own promotion loop.

## Resolutions at Accept

Eight architectural choices the brief surfaced that the executing session was instructed not to decide unilaterally. Each was drafted as a recommendation with reasoning. **All eight resolved per recommendations on 2026-05-26 (session 9), after ADR-0013 was Accepted in the same session — so the dep is satisfied and this ADR's status is plain Accepted.** The Decision section above is the canonical text; this section retains the reasoning for provenance.

1. **Location of the retrofit report.**
   Recommendation: `<data-root>/retrofit-reports/<architect-id>.md`. Federation-produced one-time artifact; data per [ADR-0007](0007-github-as-system-storage-data-excluded.md) (gitignored, lives under the data root).
   Alternative considered: inline in the `inputs/` mirror. Rejected — `inputs/` is a read-only mirror of producer artifacts; the retrofit report is a federation-produced artifact, not a producer one, and mixing the two breaks the mirror's read-only invariant.

2. **Format of the retrofit report.**
   Recommendation: **tabular main body** (one row per federation principle and per universal habit; columns *Entry | Outcome (A/B/C) | Evidence | Proposed edit*) plus a **prose appendix** for outcome-D producer-side candidates (one section per candidate, with proposed name, generalization argument, recommended [ADR-0008](0008-three-bucket-taxonomy.md) classification).
   Reasoning: tabular for the per-principle/habit walk because it's compact and surveyable; prose for D because each candidate needs a writeup. The user's review effort scales with format choice.
   Alternative considered: prose throughout. Rejected — too long; surveyability suffers; the user's review cost rises without commensurate benefit.

3. **Sequencing relative to ADR-0013.**
   Recommendation: **(a) this ADR lands `Proposed (pending ADR-0013 acceptance)`**; the receipt-ritual references resolve when ADR-0013 is Accepted, at which point this ADR's status flips to plain `Proposed`.
   Reasoning: both ADRs can be drafted in parallel; pending-status is well-precedented (e.g., ADR-0004 references the Auditor pattern before ADR-0004 itself was drafted). Reject the alternative (b — serialize 0013 first, then 0014) on grounds that it serializes work that doesn't need to be serialized.

4. **ID convention for receipt-ritual edits coming from retrofit.**
   Recommendation: **the same convention [ADR-0013](../briefs/adr-0013-receipt-ritual.md) specifies for any receipt-ritual edit.** The retrofit produces *batched* edits (N edits arriving roughly together), but each is an independent edit with its own filename. Whether the batch has a shared batch-ID for traceability is itself an open design question — flag for ADR-0013 to resolve (it's the receipt-ritual ADR's territory, not this one's). If ADR-0013 lands without a batch-ID convention and the retrofit's traceability needs one, raise it as a follow-up amendment to ADR-0013 rather than encoding it here.

5. **the user-override syntax for same-session Federation Architect adoption.**
   Recommendation: an **explicit phrase from the user in-session** — *"adopt this now,"* *"skip the receipt ritual for this,"* or similar. No prescribed wording (the user's call); the federation-architect detects an unambiguous override and acts. Federation Architect surfaces the override in its end-of-session handoff entry (which adoption events used the override and why), so the audit trail is preserved.
   Reasoning: conversational override is sufficient given the user is the only authority that can grant it; a structural flag in the proposed-edit file would add a parser without paying for itself.
   Alternative considered: a structural flag in the proposed-edit file (e.g., `override-allowed: true`). Rejected — adds machinery; conversational override is precedent-aligned with how every other the user-gate works in this federation.

6. **Treatment of "adopted-in-fact" (outcome A) edits — version bump grade.**
   Recommendation: **minor** version bump on the receiving role doc.
   Reasoning: [ADR-0010](0010-registries-canon-only-sidecar-history.md) makes role-doc adoption the canonical record. Before the formal adoption entry exists, the Architect is not formally adopted regardless of de-facto behavior. Adding the formal entry is a structural change to §"Adopted principles and universal habits," which under federation-arch.md §10's pattern is a minor bump.
   Alternative considered: patch bump (treat the edit as "just making explicit what's already implicit"). Rejected on the same grounds — the adoption record's existence is the structural fact, and pre-record practice does not substitute for it.

7. **Retrofit report retention.**
   Recommendation: **stay in place** at `<data-root>/retrofit-reports/<architect-id>.md` after all entries are resolved and edits have landed. Small artifact; audit surface for the one-time retrofit event; no harm in retention.
   Reasoning: the sidecar-history pattern per [ADR-0010](0010-registries-canon-only-sidecar-history.md) does not apply because the report is not a registry — it is a one-shot artifact, not an evolving one. Retention preserves the cold-readable provenance for any future audit asking *"why was X classified as outcome B for Architect Y?"*

8. **Outcome-D candidate rejected by federation (fails [ADR-0008](0008-three-bucket-taxonomy.md)'s test).**
   Recommendation: **log in the Architect's own `architect-learnings.md`** with status `Closed (federation declined)` and the reason. Architect-local practice is unaffected — the Architect keeps doing the thing, but it does not promote to a universal habit. This is the **situation-specific habit** case from [ADR-0008](0008-three-bucket-taxonomy.md) §"Bucket 2: Habit / Situation-specific habit."
   Reasoning: preserves provenance (the candidate was considered; the federation declined; the reason is recorded) and respects the default-narrow stance from [ADR-0008](0008-three-bucket-taxonomy.md) §"Universal habit vs. situation-specific habit."
   Alternative considered: silently drop. Rejected — loses provenance; future federation passes might re-surface the candidate without knowing it was already considered.

## Alternatives Considered

- **One-time-only retrofit, no ongoing mechanism.** Rejected. The initial retrofit handles the day-one reconciliation, but federation events (new principles, new habits) accrue after onboarding; without an ongoing path, existing Architects drift out of sync with the registries and the federation's primary deployable output (per-Architect adoption) stops working for them. The two-mode design pays for itself the moment a single new event is accepted post-retrofit.
- **Receipt-ritual-only, no retrofit report.** Rejected. The initial retrofit produces N edits (one per outcome A/B per registry entry, plus outcome-D candidates as separate federation-side proposals). Surfacing N edits one-by-one through the receipt ritual gives the user no overview surface and forces them to review fragmented entries with no cross-cutting context. The report provides the one consolidated review; receipt-ritual artifacts then land the agreed edits.
- **Shallow-only discovery (skip the deep walk on initial retrofit).** Rejected. Evolved role docs hold principle-shaped statements in places (Voice/Style, error-handling, prose narratives) that the `architect-learnings.md` surface alone won't catch. The one-time deep walk converts existing evolved-Architect material into outcome-D candidates that would otherwise be invisible to the federation forever. The deep walk's cost is bounded (one-time, surveyable as a single report); the cost of skipping it is permanent.
- **No self-promotion guard for Federation Architect; trust user-as-the-only-gate to catch self-licensing.** Rejected. The user *is* the only authority, but the guard is structural (separate session-start picks up the edit fresh, with an explicit override) rather than relying on the user to notice that Federation Architect drafted *and* adopted in one move. Sessions 5 and 6 illustrate the failure mode: same-session draft-and-adopt happened, the user approved by general assent, no per-event surfacing. The guard formalizes the gate so that the default path is structural-separation and the override is named, not implicit.
- **Treat Federation Architect retrofit as a separate ADR.** Rejected. Federation Architect's special case is a structural twist (the self-promotion guard) on the same mechanism every other Architect uses. Splitting it into a parallel ADR would duplicate the trigger model, the outcomes, and the discovery rules. Same mechanism, one structural addition, one ADR.
- **Bundle producer-side material discovery as a separate ADR ("ADR-00XX-producer-side-discovery").** Rejected. The discovery step has the same trigger model and same review surface as the rest of the initial retrofit — folding it in keeps the per-Architect onboarding work as one report-and-resolve event rather than two parallel processes.

## Consequences

### Immediate

- **Federation Architect can run its own first self-application sweep** after this ADR is Accepted. The eight prior federation principles and fourteen universal habits already in `federation-arch.md` §4 reflect that work done implicitly in sessions 4–6; this ADR's Accept formalizes the path, and the self-promotion guard becomes binding for every future Federation Architect adoption event.
- **Recipe Box Architect becomes the first downstream retrofit subject.** A separate session executes the initial retrofit per this ADR — produces the report at `<data-root>/retrofit-reports/recipe-box-arch.md`, walks it with the user, drops the receipt-ritual edits. **This ADR does not propose retrofit edits to Recipe Box Architect**; that work is downstream.
- **The bootstrap kit (forthcoming, separate work) gets a placeholder reference to this ADR.** The kit's PROCESS.md / README will need to make the bootstrap-vs-retrofit distinction explicit (new Architects bootstrap from template; existing Architects retrofit per this ADR). The distinction is named here but the discrimination logic is not written in this ADR.
- **federation-arch.md will need a follow-up bump.** Adopting this ADR means Federation Architect's session-start ritual (§11) needs to include the receipt-ritual sweep check (and, by extension, the self-promotion-guard mechanics for Federation Architect's own pending edits). That bump is a separate post-Accept commit, same pattern as ADR-0011 → v0.6.0. Not part of this ADR's scope.

### On future Architect onboarding

- The federation's onboarding surface now has two paths, named explicitly: **bootstrap** (new Architects, template-driven, cold-start) and **retrofit** (existing Architects, report-driven, one-time reconciliation + ongoing receipt-ritual events). The bootstrap kit covers the first; this ADR covers the second; the two paths converge at the point both Architect types are receiving receipt-ritual edits per [ADR-0013](../briefs/adr-0013-receipt-ritual.md).
- Each retrofit produces one persisted report and N landed role-doc edits with CHANGELOG entries. The cold-readable trail for any retrofitted Architect tells the full story: which registry entries were adopted, with what evidence, at which version bumps.

### Risks

- **Drift between retrofit completion and next ongoing adoption event.** A retrofit completes against the current registries; if the registries accept new entries during the retrofit window (between report draft and last receipt-ritual edit landing), the new entries miss the retrofit and require their own receipt-ritual edits afterward. Mitigation: the retrofit report records its base versions (`principles/master.md` and `habits/master.md` revisions as of report draft); any registry change after that triggers a normal ongoing-retrofit event for that entry. No silent miss.
- **Deferred sync discipline across multi-machine clones.** [ADR-0009](0009-data-storage-mechanics.md) deferred the sync mechanism; [ADR-0011](0011-data-root-config.md) §"Open for the user" item 5 also deferred multi-machine drift on non-`Data root:` fields. Retrofit reports and receipt-ritual edits both live under the data root; if the user runs federation from machine A and the receiving Architect's session is on machine B, *something* moves the files. This ADR does not solve that — same posture as ADR-0013 (receipt ritual works within whatever sync is configured; sync itself is out of scope until federation actually spans machines concurrently).
- **Outcome-D candidates accumulate without federation review.** The initial retrofit's deep walk can produce many outcome-D candidates. If the user approves the report but defers the producer-side-candidate appendix, the candidates sit in federation's `architect-learnings.md` indefinitely. Mitigation: federation's own producer-side-learnings habit covers this surface; periodic federation passes review pending candidates. The risk is real but not specific to this ADR.
- **Federation Architect override-fatigue.** If every same-session Federation Architect adoption needs an explicit override phrase from the user, the friction may push the federation back toward draft-only sessions with deferred adoption. Acceptable cost: the explicit override is the price of the structural guard; the alternative (silent same-session self-licensing) is the failure mode the guard exists to prevent.

## References

- [ADR-0003: The Federation Architect is a federation participant](0003-federation-architect-is-a-participant.md) — recursive participation; underwrites the requirement that retrofit applies to Federation Architect.
- [ADR-0008: Three-bucket taxonomy](0008-three-bucket-taxonomy.md) — principle / habit / preference definitions; sets what gets retrofitted (outcomes A/B/C scope; outcome D's classification path).
- [ADR-0009: Data storage mechanics](0009-data-storage-mechanics.md) — data root abstraction; override mechanism (outcome C's mechanic).
- [ADR-0010: Registries are canon-only; sidecar history pattern](0010-registries-canon-only-sidecar-history.md) — role-doc-as-canon-of-adoption; retrofit edits land in role docs.
- [ADR-0013 (brief): Receipt ritual](../briefs/adr-0013-receipt-ritual.md) — receipt-ritual mechanism that landings ride on. This ADR cites and uses; does not specify. Soft dependency — this ADR's status flips when ADR-0013 is Accepted.
- [federation-arch.md](../federation-arch.md) — Federation Architect role doc (v0.6.0 as of this ADR's writing); §4.1, §4.2, §11 are touched by adoption (in a separate post-Accept commit per [ADR-0011](0011-data-root-config.md)'s pattern).
- [session-handoff.md](../session-handoff.md) — session 7 (three existing-Architect gaps surfaced) and session 8 (eight direction points locked in conversation with the user).
- [briefs/adr-0014-existing-architect-retrofit.md](../briefs/adr-0014-existing-architect-retrofit.md) — the work-package brief that produced this ADR.
