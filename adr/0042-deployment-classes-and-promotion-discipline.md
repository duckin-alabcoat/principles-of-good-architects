# ADR-0042: Deployment classes and promotion discipline

**Status:** Accepted
**Date:** 2026-07-12
**Deciders:** the operator, Federation Architect
**Accepted:** 2026-07-12 (session 55) — the operator's explicit Accept: (1) class-scope via `binds-to`, no new bucket; (2) the §3 cloud obligation set, framed as initial/revisable; (3) promotion discipline as federation canon.

## Context

The federation's doctrine had, until now, been written for local systems: a Claude-Code Architect governing a repo that runs on demand, or that a member may run as an always-on resident daemon. The obligations the federation ships — the substrate floor ([ADR-0023](0023-standard-operating-substrate.md), re-framed by [ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md)), the habit registry ([ADR-0008](0008-three-bucket-taxonomy.md)) — implicitly assumed that shape. That shape has no **public endpoint** and no **third-party billing relationship**, and so the doctrine never had to say anything about failure modes that only exist when a system is exposed to the internet and to a metered cloud account.

A cloud-deployed member would break that assumption. Take a hypothetical member `example-api`: it serves a public endpoint from metered cloud infrastructure and has a spending ceiling it must never cross. A system of that shape needs a set of disciplines no local system does:

- A **promotion pipeline** — dev (local) → test (CI) → stage → prod — where reaching prod is a *promotion*, not a deployment, and every promotion states its one-command rollback.
- A **local-first development rule** stated where every session inherits it: iterate locally against an offline stack; the cloud is touched only to release, never to try things.
- A **hard financial circuit-breaker** that stops spend automatically at a declared cap, plus the spending ceiling as a screened decision filter on every design choice.

These are not the accidents of any one system; they are the obligations that *any* cloud-deployed system will carry. A curate harvest surfaced `hard-financial-kill-switch` and `hard-cost-ceiling-as-design-constraint` as producer-side candidates and explicitly **parked them for this ADR** rather than the general curate flow, because they are class-scoped, not architect-universal: they apply where a system talks to a billing provider, and nowhere else.

The federation therefore needs a doctrine that (a) names the deployment surfaces a member can have, (b) says how obligations bind to a surface without inflating the universal floor, and (c) captures the promotion discipline as federation canon rather than leaving each system to re-derive it in its own ADRs. The move is to scope a practice to a **class of systems** rather than to all of them, and it lives one axis over from [ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md): that ADR classifies by **runtime** (what agent builds the system), this one classifies by **deployment surface** (where and how the system runs). The two are orthogonal: a cloud-deployed system may run on any runtime binding and carries this ADR's obligations either way.

## Decision

Define **deployment classes** — a classification of every federation member by its deployment surface — and bind obligations to classes so that a class's members inherit exactly the disciplines their surface forces, no more.

### 1. The three deployment classes

A system's **deployment class** describes the surface on which its domain runs. It is orthogonal to its **runtime binding** ([ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md)): the class is about *where the work is exposed*, the binding is about *what agent produces it*. Every member is exactly one class.

| Class | Deployment surface |
|---|---|
| **local-tool** | Runs on demand on a user machine; no always-on process, no public surface, no external billing. |
| **local-resident** | An always-on daemon on a user machine; a live process, but still no public endpoint and no third-party billing. |
| **cloud-deployed** | A publicly reachable endpoint on metered third-party infrastructure with a billing relationship. |

The classes are **cumulative in exposure, not in substrate**: a local-resident system carries everything a local-tool does plus always-on obligations; a cloud-deployed system carries a public surface and a billing relationship on top. The class captures the *marginal* obligations a surface introduces.

### 2. Class-scoping: how obligations bind

- **Universal principles bind every class.** A principle ([ADR-0008](0008-three-bucket-taxonomy.md), Bucket 1) is a claim about Architect quality independent of any system's situation; deployment class does not narrow it. `principles/master.md` applies to a cloud-deployed system exactly as to a local-tool one.

- **Habits may declare the class(es) they bind to.** This extends [ADR-0008](0008-three-bucket-taxonomy.md)'s **situation-specific** lane: where a habit's failure mode is present only on a particular deployment surface, the habit declares `binds-to: <class>` instead of relying on an ad-hoc "not applicable here" override in each non-matching system. A class-bound habit is federation-managed for its class — drafted once, adopted by each member of that class — the same one-draft/N-adoptions workflow [ADR-0008](0008-three-bucket-taxonomy.md) defines, scoped to the class roster instead of the whole fleet.

- **The retrofit report records the system's deployment class**, and the class determines the applicable habit set. A system's conformance is then judged against *its class's* obligations, not against a flat universal set with per-system exceptions. This replaces the ad-hoc not-applicable override with a declared, cold-readable scope: an auditor reads the class off the retrofit report and knows which class-bound habits are in force.

Class-scoping is a **scoping mechanism inside the existing taxonomy, not a fourth bucket.** A class-bound habit is still a habit; the class is the situation. `binds-to: all` (or an absent binding) is the default and covers every existing entry, so no current habit changes.

### 3. Cloud-deployed class obligations (initial set)

A cloud-deployed system MUST satisfy the following. These are stated as the **initial** set — refined and extended as more cloud systems appear (§5), not frozen here.

1. **A hard cost-cap with automatic enforcement.** A structural circuit-breaker that halts spend at a declared ceiling **without a human in the loop** — for example, a budget alert wired to an action that stops billable use once the cap is reached. "Automatic" is load-bearing: an alert that merely emails a human is not a cost-cap. The ceiling is a declared design constraint, screened against every choice, not a runtime afterthought.

2. **A promotion pipeline with staged, pre-exposure verification.** Changes reach production through dev → test → stage → prod (§4), where the stage step exercises the real deployment surface **before any user traffic reaches it**, and promotion to prod is a deliberate traffic move with a one-command rollback on record.

3. **An authenticated public surface.** The public endpoint rejects unauthenticated requests as its baseline posture; the authentication check is the system's own, not a property of an edge it cannot guarantee. Each cloud member states its residual risk honestly in its own records, rather than assuming an edge protection it has not verified.

This is an **initial** set on purpose. It does not claim completeness. Each further cloud-deployed system is expected to add or sharpen obligations, and that revision is the intended path (§5), not a failure of this ADR.

### 4. Promotion discipline

Capture the promotion model as federation canon for the cloud-deployed class:

- **Iteration happens where failure is free.** Development is local, against an offline/local stack; the metered cloud is touched only to release, never to try things (the local-first rule). Development speed comes from this rule.
- **Verification precedes exposure.** Automated gates (CI: tests + build, with branch protection) run before merge, and a stage exercises the real surface before any user sees a change.
- **Promotion is deliberate.** Production is reached by an explicit promotion step (a traffic flip), distinct from deployment. "Deploy" builds and stages; "promote" exposes.
- **Rollback is one step.** Every promotion has a single-command rollback recorded at the moment of promotion, so recovery never requires reconstruction under pressure.

Each cloud member records its own binding of this discipline in its own ADRs; this ADR states the federation-level obligation, not the provider-specific mechanics, which are a per-system binding detail.

### 5. Restraint clause — hold at three classes

**Do not sub-class until several cloud-deployed systems exist to compare.** Three classes, and the cloud-deployed obligation set of §3, are the whole taxonomy. No `cloud-deployed-high-stakes`, no `local-resident-multi-agent`, no per-provider tier — those are speculative until real instances prove what actually generalizes versus what was one system's accident. This mirrors [ADR-0008](0008-three-bucket-taxonomy.md)'s default-narrow discipline and its "more buckets is premature" rejection: the classes fall out of real members, and further splits wait for real ambiguity in practice, not anticipated need. When a cloud member onboards, its retrofit is the occasion to revisit §3 and decide what was universal-to-the-class versus what was one system's accident.

## Alternatives Considered

- **No classes — keep a flat universal floor with per-system "not applicable" overrides.** The status quo. Rejected: it forces every non-cloud system to carry (and explicitly opt out of) obligations that can never apply to it, and it hides *why* an obligation is off in a given system inside an ad-hoc override rather than in a declared class. Class-scoping makes the scope cold-readable and the applicable set derivable from one recorded fact.

- **Make the cloud obligations universal habits (bind to all).** Rejected — this is exactly the [ADR-0008](0008-three-bucket-taxonomy.md) no-compound-Bash failure at fleet scale: a billing kill-switch is meaningless for a system with no billing relationship, and a promotion pipeline is meaningless for a local-tool with no deployment surface. The failure mode is genuinely localized to the cloud surface; the honest classification is class-bound, not universal.

- **A new fourth taxonomy bucket ("deployment obligation").** Rejected — it duplicates the habit machinery. A class-bound obligation is a habit whose situation is the deployment class; [ADR-0008](0008-three-bucket-taxonomy.md)'s situation-specific lane already carries it, extended with a declared class scope. A new bucket would fork propagation semantics for no gain.

- **Fold deployment class into the runtime binding ([ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md)).** Rejected — they are orthogonal. A cloud system could run on any runtime; a Claude system could be local-tool or cloud-deployed. Collapsing the two axes would mislabel cloud obligations as runtime obligations, and the next cloud system on a different runtime would wrongly escape them.

- **Define richer sub-classes now** (e.g., split cloud-deployed by provider, by data-sensitivity, or by traffic tier). Rejected per §5 — speculative taxonomy ahead of real instances. One instance cannot tell us what generalizes.

- **Leave promotion discipline to each member's own ADRs.** Rejected — it is a class obligation, not one member's quirk. Every cloud member needs the same iterate-where-free / verify-before-expose / promote-deliberately / rollback-in-one-step discipline; canonizing it here is what makes it inheritable instead of re-derived.

## Consequences

- **The retrofit report gains a `deployment-class` field**, and conformance is judged against the class's obligation set. Downstream: the reconcile/status surface should learn to read a member's class and report open (unmet) class obligations the way it reports other drift — not built here.
- **A cloud-deployed member's own records are the concrete binding of the class.** Whatever member takes the class, its own promotion, local-first and cost-cutoff records are what the class obligations point at.
- **`hard-financial-kill-switch` and `hard-cost-ceiling-as-design-constraint` get a home** — they land as cloud-deployed class-bound habits under §3, resolving the parked outcome-D harvest items rather than pushing them through general curate.
- **The habit registry / role-doc format needs a `binds-to` scope** for class-bound habits (default `all`). Small: existing entries default to universal and are unchanged. Location and exact syntax are a build detail, gated on Accept.
- **New obligation surface for the cloud class only.** Only the cloud-deployed class carries §3; the cost is proportionate and the local classes are descriptive (they name what such systems already are) rather than newly demanding.
- **A revision path is built in.** §3 is explicitly initial; each cloud member's retrofit is the scheduled occasion to refine it. This ADR is not superseded by that revision — the obligation *set* is expected to grow within this doctrine.
- **Depends on the taxonomy staying intact.** Class-scoping rides [ADR-0008](0008-three-bucket-taxonomy.md)'s habit machinery; do not collapse the principle/habit boundary or the situation-specific lane while class-bound habits exist.

## References

- [ADR-0008](0008-three-bucket-taxonomy.md) — three-bucket taxonomy; class-scoping extends the situation-specific habit lane and inherits its propagation semantics.
- [ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md) — runtime-agnostic substrate floor; the orthogonal axis (runtime binding vs. deployment surface).
- [ADR-0023](0023-standard-operating-substrate.md) — standard operating substrate; the universal floor these class obligations sit above.
- `architect-learnings.md` — the outcome-D harvest; `hard-financial-kill-switch` and `hard-cost-ceiling-as-design-constraint` parked for this ADR.
