# ADR-0015: Work-package briefs as the federation parallel-work mechanism

**Status:** Accepted
**Date:** 2026-05-26
**Deciders:** the user, Federation Architect

## Context

Through session 6 the federation's production pattern was strictly foreground-and-serial: the user and one Federation Architect session would work through one artifact at a time. Session 7 surfaced the real bottleneck — *the user waits on me, not the other way around* — and adopted a parallel-work pattern: split federation work into self-contained **work-package briefs** that parallel Federation Architect sessions (or background subagents) can pick up and execute independently. See feedback memory [`feedback-user-waits-parallelize`](../.claude/projects/-Users-user-principles-of-good-architects/memory/feedback_user_waits_parallelize.md).

The pattern has now been used four times across sessions 7–9:

- ADR-0011 (data-root config form) — drafted by background subagent in session 7, folded back and Accepted in session 8.
- ADR-0012 (per-Architect repo conventions), ADR-0013 (receipt ritual), ADR-0014 (existing-Architect retrofit) — drafted by three parallel background subagents in session 9.

Two findings from those uses are load-bearing:

1. **Sandbox-block on background-agent git ops.** Background-subagent contexts cannot run `git commit` even with `dangerouslyDisableSandbox`. Discovered session 7. Resolution: **stage-and-stop semantics** — the agent stages, the foreground session commits and pushes. Briefs 0012 and 0013 were patched session 8 to bake this in; brief 0014 used it from the start.
2. **"Open for the user" items must not be decided solo.** The whole point of the brief structure is that the agent renders the locked direction into form and surfaces the genuinely-open architectural choices with recommendations — *the user* resolves them at Accept-time, not the agent. ADR-0011's five Open-for-the user items, all resolved per recommendation in session 8, validated this.

The pattern is in use and working. This ADR pins what it is, in canonical form, so future Architects (and future Federation Architect sessions on cold restart) inherit it rather than re-deriving it.

This ADR does not specify *what* gets briefed — that's a per-work-unit judgment. It specifies the brief format, the dispatch mechanics, the stage-and-stop discipline, the acceptance flow, and the lifecycle of the brief artifact itself.

## Decision

### What a work-package brief is

A **work-package brief** is a self-contained dispatch spec for one parallel Federation Architect session (live or background-agent). It contains everything that session needs to produce a single named artifact end-to-end without consulting the dispatcher.

Briefs are **system** per [ADR-0007](0007-github-as-system-storage-data-excluded.md) §"Classification rule" — they define how the federation operates, they don't carry user-data.

### Where briefs live

All federation briefs live in `briefs/` at the federation repo root. Filename matches the artifact being produced (e.g., `briefs/adr-0014-existing-architect-retrofit.md` produces `adr/0014-existing-architect-retrofit.md`). Briefs are committed to the federation repo at the moment they become dispatch-ready.

### Brief structure

Every brief contains, in this order:

1. **Title and metadata** — title, type ("Work-package brief for a parallel Federation Architect session"), created date and source session, dispatch-readiness status.
2. **Who you are** — identity statement asserting the picking-up session inherits the Federation Architect role doc and all adopted principles + habits. Names `session-vision-anchoring` explicitly. Forbids widening scope past the brief.
3. **Vision** — one paragraph: what artifact gets produced and why it matters.
4. **Time budget** — explicit ("½ session ~30–45 min", "Full session ~60 min"). If the work stretches past budget, the picking-up session is told to pull back to vision rather than power through.
5. **Required reading** — ordered list of files the picking-up session must read before drafting. Includes the role doc, relevant prior ADRs, relevant session-handoff entries, the ADR template (or other output-shape reference).
6. **What's already decided** — the locked direction. Sub-decisions the dispatcher session pre-resolved with the user. The picking-up session does not reopen these.
7. **What's open — surface, do not decide** — the architectural choices the dispatcher session deliberately left for the user. The picking-up session drafts each as a recommendation with reasoning and lists them in the resulting artifact's "Open for the user" section. **Never decided solo.**
8. **Output spec** — the artifact(s) to produce, their paths, their status on completion.
9. **In/out of scope** — explicit boundaries.
10. **Acceptance criteria** — checklist the dispatcher session uses at fold-back to confirm the work is complete.
11. **Merge / handoff procedure** — the stage-and-stop steps (see below).
12. **Status on completion stub** — a stub block the picking-up session stamps when the work is done (branch name, staged files, summary, concerns).

### Dispatch modes

Two dispatch modes are supported. The brief is identical in both; only the launching mechanism differs.

| Mode | Launcher | Use when |
|---|---|---|
| **Parallel session** | the user opens a new Claude Code session pointed at the brief. | the user wants to drive the dispatch himself; the work needs a fully interactive surface (rare for briefs — they're written to be hands-off). |
| **Background subagent** | Foreground session spawns via the `Agent` tool with `subagent_type: "general-purpose"`, `isolation: "worktree"`, `run_in_background: true`. | Foreground is dispatching multiple briefs at once; foreground can keep working while the agent runs. |

The Agent-tool path is the default for foreground-driven dispatch. `general-purpose` is the correct subagent type: the work involves reading, drafting, and git staging, none of which are specialized enough to warrant `Plan` or `Explore`.

### Worktree isolation

Background-subagent dispatches **must** use `isolation: "worktree"`. Each agent gets its own git worktree off `main`, branches as `worktree-agent-<hash>`. This:

- Prevents agents from stomping on each other or on the foreground working tree.
- Gives each agent a clean, branch-isolated commit surface.
- Makes fold-back a normal merge instead of a working-tree reconciliation.

### Stage-and-stop semantics

**Background-subagent contexts cannot commit.** Even with `dangerouslyDisableSandbox`, the harness blocks `git commit` from agent contexts. The brief therefore instructs the agent to:

1. `git add` every file it created or modified, including the brief itself (stamped with completion).
2. **Stop.** Do not run `git commit`. Do not run `git push`.
3. Report back: branch name, staged file list, work summary, any concerns.

The dispatching foreground session then commits and pushes from the agent's branch.

For the user-driven parallel-session dispatches (no sandbox block), the same semantics still apply by convention — the brief is a single document and the same form runs in both modes. The parallel session stages, reports to the user, the user commits. (This is the conservative default; if a future parallel-session pattern proves that direct commit + push is fine in that context, the brief can carry a mode-flag.)

### Acceptance flow (fold-back)

When the picking-up session reports completion, the foreground session does the following on a single working session:

1. `git fetch` and check out the agent's branch locally (or pull into a clone).
2. Read the produced artifact end-to-end. Treat the agent's work as a first draft, not the final answer.
3. Walk the user through the artifact's "Open for the user" section. Each item gets a decision — accept-recommendation, accept-alternative, or re-ask.
4. Edit the artifact in place to reflect the user's resolutions. For ADRs: flip Status `Proposed → Accepted`; add a brief "Resolutions at Accept" section (or equivalent) recording which open items resolved how and the date.
5. Resolve any merge conflicts on `adr/README.md` (each parallel agent updates the index for *its* ADR; concurrent dispatches mean multi-way conflicts at merge — take the union). Resolve any brief-file conflicts in favor of the agent's stamped version (the superset).
6. Merge to `main` with `--no-ff` to preserve the parallel-work trail.
7. Delete the agent's branch local + remote. Clean up the worktree on disk (`git worktree remove`).

### Brief lifecycle (post-fold)

The brief file **stays on disk** with its completion stamp. It is **not** deleted after the artifact lands. Rationale:

- The brief is the historical record of what direction was locked at dispatch and what was left open for the user. That record outlives the ADR itself for understanding *how the decision was assembled*.
- Storage cost is trivial; discoverability cost of deleting it is high (future cold-restarts wouldn't see how the pattern was actually used).
- The stamp encodes the round-trip (when dispatched, when stamped, by which branch).

### Open-for-the user discipline

The single most important convention is this: **the picking-up session never decides "Open for the user" items solo.** It drafts a recommendation, lists alternatives, names tradeoffs, and routes the decision back to the user via the artifact's "Open for the user" section. The user resolves at Accept-time.

This convention is what makes the pattern safe to parallelize. If a picking-up session decides solo, the dispatcher loses control of the architectural decisions; if instead it leaves them open with recommendations, the dispatcher (foreground + the user) holds the architectural surface even when production is parallelized.

The brief enforces this convention in two places: §"Who you are" forbids widening scope, and §"What's open — surface, do not decide" lists the specific items the picking-up session must not decide.

### Companion habit

This ADR's adoption triggers registration of a small companion habit in federation-arch.md:

> **parallel-work-decomposition** — At session planning (paired with `session-vision-anchoring`), ask: *does this session's work split into one or more units that can be briefed and dispatched in parallel?* If yes, prefer dispatch over foreground execution unless the work is short enough that briefing costs more than executing.

Adopted-in-fact since session 7; this ADR's acceptance lands the formal registration. Bump federation-arch.md minor on the same Accept event.

## Open for the user

Five items the foreground did not pre-resolve. Recommendations and reasoning below; the user resolves at Accept.

### 1. Brief lifecycle post-fold

**Question.** Briefs stay on disk post-fold (current emergent practice; 0011's brief is still there, stamped). Confirm, or archive into `briefs/done/`, or delete?

**Recommendation.** **Keep in place, stamped.** Briefs are historical records of how a decision was assembled; they're system; storage cost is trivial. Archiving into `done/` introduces a sort-order question and a "when do you move it" trigger that isn't worth the complexity. Deleting loses the audit trail.

**Flagged alternative.** Move to `briefs/done/` once the ADR lands. Minor cosmetic improvement; not worth the rule.

### 2. Briefs for non-ADR artifacts

**Question.** All four briefs to date produce ADRs. Should the pattern explicitly generalize to non-ADR artifacts (habit registration, principle adoption, bootstrap kit, retrofit reports, etc.)?

**Recommendation.** **Yes, explicitly generalize.** The brief structure is artifact-agnostic; the 1:1 brief↔ADR mapping is incidental. The bootstrap-kit session (already on the priority list) is a natural first non-ADR brief. The brief's §"Output spec" already accommodates this — it says "the artifact(s) to produce" without constraining the type.

**Flagged alternative.** Restrict briefs to ADRs only. Reject — narrows the pattern arbitrarily.

### 3. Brief authorship

**Question.** Currently only Federation Architect writes briefs. Should this be pinned (briefs are federation-side only), or should receiving Architects be able to write their own briefs for their own parallelism?

**Recommendation.** **Federation Architect only, for federation-side work.** Each Architect's internal parallelism (if any) is its own concern under its own repo conventions (per ADR-0012's territory). Federation-side briefs are the federation's mechanism. Mixing the two creates a discovery problem (which `briefs/` directory does a session pick from?).

**Flagged alternative.** Per-Architect briefs at the per-Architect repo level. Doesn't conflict with this ADR — but isn't this ADR's call to make. Defer to ADR-0012 if the user wants it scoped there.

### 4. Multi-agent `adr/README.md` conflict pattern

**Question.** When N parallel agents each update `adr/README.md` to index their respective ADRs, the foreground sees a multi-way conflict at fold-time (N agents = N versions). Should the ADR prescribe a resolution policy?

**Recommendation.** **Document the pattern explicitly: agents update the README, foreground resolves at merge by taking the union of new rows.** This is the current emergent practice. The cost is one merge resolution per agent (small); the benefit is that each agent's work-package is complete and self-contained (it produces *both* the ADR and the index entry).

**Flagged alternative.** Have agents *not* touch `adr/README.md`; foreground updates the index after all folds complete. Cleaner merges, but the agent's work-package is no longer self-contained — foreground has to remember to do the README update separately. Net cost similar; reject for the self-containment argument.

### 5. Subagent type pinning

**Question.** All four background-agent dispatches used `subagent_type: "general-purpose"`. Worth pinning this in the ADR, or leave as a per-dispatch choice?

**Recommendation.** **Pin `general-purpose` as the default.** Brief work involves reading, drafting, and git staging; specialized agent types (`Plan`, `Explore`) don't fit. Naming the default in the ADR saves future-foreground from rethinking it each dispatch.

**Flagged alternative.** Leave open. Reject — defaults are useful when the choice is uniform across uses.

## Resolutions at Accept

All five Open-for-the user items resolved per recommendation in session 11 (2026-05-26 late).

1. **Brief lifecycle post-fold.** Resolved per recommendation: briefs stay in `briefs/` with completion stamp; no `done/` archive, no deletion. Audit trail preserved; no move-trigger rule to maintain.
2. **Briefs for non-ADR artifacts.** Resolved per recommendation: the pattern generalizes explicitly to any federation artifact. Bootstrap kit is the natural next non-ADR brief. The §"Output spec" wording already accommodates this.
3. **Brief authorship.** Resolved per recommendation: Federation Architect only, for federation-side work. Per-Architect internal parallelism (if any) is its own concern under ADR-0012's territory and does not share the `briefs/` directory.
4. **Multi-agent README conflict policy.** Resolved per recommendation: agents update `adr/README.md` for their own ADR; foreground resolves at merge by taking the union of new rows. Self-containment of each work-package preserved.
5. **Subagent-type pinning.** Resolved per recommendation: `general-purpose` is the default for background-subagent dispatches.

## Alternatives Considered

- **Foreground-only execution (status quo through session 6).** Rejected — session 7 surfaced that the user-waiting-on-Federation-Architect is the bottleneck. Single-threaded production capped throughput at one artifact per foreground session.
- **the user-as-router for dispatched work.** the user opens new sessions, hand-types the spec each time. Rejected — works for the first one or two, but the spec is non-trivial (vision, time budget, required reading, locked direction, open items, scope, acceptance criteria, merge procedure) and re-typing it is the actual bottleneck.
- **Free-form prompt dispatch (no brief structure).** Spawn a subagent with an ad-hoc prompt. Rejected — agents need locked direction or they will widen scope; without an "Open for the user" discipline, agents make architectural decisions solo; without an acceptance-criteria checklist, fold-back is murky. The brief structure is the minimum that makes the pattern safe.
- **Agent commits and pushes directly.** Rejected — sandbox-blocked in background-subagent contexts (session 7 finding). Stage-and-stop is the workaround that holds for both the user-driven and foreground-driven dispatches.
- **Briefs as a directory of templates, not committed artifacts.** Briefs live as templates and get instantiated each dispatch. Rejected — committed briefs are themselves the audit trail of *what was dispatched when*, which is load-bearing for cold-restart understanding of how decisions were assembled.
- **Agents decide "Open for the user" items solo.** Rejected — see §"Open-for-the user discipline" in the Decision. The whole pattern hinges on this convention.

## Consequences

### Immediate

- **The pattern is canonical.** Future Federation Architect sessions know to brief-and-dispatch rather than re-derive the form.
- **Federation Architect role-doc gains the companion habit** (`parallel-work-decomposition`) in a follow-up minor bump on the Accept event.
- **`briefs/` is a permanent top-level directory** of the federation repo, alongside `adr/`, `habits/`, `principles/`, `specs/`, `users/`, `inputs/`.

### On throughput

- **Multiple ADRs can be in flight per session.** Session 9 has three concurrent dispatches at draft time. Hard cap is the user's review bandwidth at fold-time, not Federation Architect's production capacity.
- **Cold-restart cost goes up slightly.** A returning session reads `briefs/` to understand in-flight work, in addition to the handoff and the role doc. Acceptable cost.

### On safety

- **Architectural decisions remain the user's.** Open-for-the user discipline (Decision §) is the mechanism. Picking-up sessions render direction into form; they do not extend the architectural surface unilaterally.
- **Fold-back conflicts are expected and handled.** README conflicts (per Open-for-the user item 4) and brief-file conflicts (foreground's brief commit vs. agent's stamped version) are part of the routine. Each has a documented resolution.

### Risks

- **Brief drift.** If a brief is written and dispatched, but the federation evolves between dispatch and pickup (e.g., a new ADR lands that changes the required reading), the picking-up session may operate on stale direction. Mitigated by short dispatch-to-pickup windows (briefs are usually picked up the same session or next) and by the brief's `Created:` date + required-reading anchor versions.
- **Open-for-the user fatigue.** If every brief surfaces 5–10 open items, the user's fold-back load grows linearly with parallelism. Mitigated by the dispatcher session being explicit about *what's truly open vs. what can be foreground-decided before dispatch*. ADR-0011 had 5 open items (manageable); 0013 has 10 (heavy by design — that's the nature of the receipt-ritual ADR). Self-regulating: dispatcher learns to pre-resolve more for heavier briefs.
- **Three-agent README.md conflict (per Open-for-the user item 4).** Already documented; resolution policy is "take union at merge." Cost scales with dispatch count.

### Deferred

- **Briefs from non-Federation-Architect parties.** the user, an Auditor, an Architect — could any of them write a federation brief? Out of scope; ADR-0003 makes Federation Architect the sole producer of federation work, so the question reduces to *whether anyone else proposes a brief that Federation Architect picks up and sponsors*. Surface if it ever becomes a real flow.
- **Briefs for cross-Architect work.** Multiple Architects collaborating on one artifact. No mechanism for this yet; federation doesn't currently have multiple participating Architects beyond Federation Architect itself. Defer to when it actually arises.

## References

- [ADR-0003: The Federation Architect is a federation participant](0003-federation-architect-is-a-participant.md) — establishes that Federation Architect's authority covers federation mechanisms. Briefs are one such mechanism.
- [ADR-0007: GitHub as system storage; data excluded by policy and gitignore](0007-github-as-system-storage-data-excluded.md) — classification rule; briefs are system.
- [ADR-0008: Three-bucket taxonomy](0008-three-bucket-taxonomy.md) — `parallel-work-decomposition` is a universal habit (applies to all federation-participating Architects' planning, not Federation-Architect-only). Adopted-in-fact since session 7.
- [`briefs/adr-0011-data-root-config.md`](../briefs/adr-0011-data-root-config.md) — the first brief; the live test of the pattern; carries the canonical "Status on completion" stamp shape.
- [`briefs/adr-0012-per-architect-repo-conventions.md`](../briefs/adr-0012-per-architect-repo-conventions.md), [`briefs/adr-0013-receipt-ritual.md`](../briefs/adr-0013-receipt-ritual.md), [`briefs/adr-0014-existing-architect-retrofit.md`](../briefs/adr-0014-existing-architect-retrofit.md) — the three briefs dispatched in session 9 in parallel.
- [session-handoff.md](../session-handoff.md) — sessions 7 and 8 entries record the pattern's introduction, the sandbox-block finding, and the brief-patch for stage-and-stop semantics.
- Feedback memory [`feedback-user-waits-parallelize`](../.claude/projects/-Users-user-principles-of-good-architects/memory/feedback_user_waits_parallelize.md) — the origin direction.
