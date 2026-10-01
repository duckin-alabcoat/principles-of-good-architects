# ADR-0063: `poga` peer-lanes and orchestrated subagents (Workflow / ultracode) are complementary, not competing

**Status:** Accepted
**Date:** 2026-07-22 (session 89)
**Relates to:** [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) (the `poga` lane model), [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) (C4/C5 coordination), [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) (the session model this rests on)
**Deciders:** the operator (asked, on seeing the ultracode announcement, whether it made the `poga` work redundant); Federation Architect (analysis)

## Context

Immediately after the `poga` C4/C5 coordination work landed (session 89), Claude Code surfaced
**ultracode** — an opt-in mode that activates the **Workflow** tool: one session runs a
deterministic script that orchestrates many *headless subagents* (fan-out, pipeline, loop-until,
parallel, adversarial verify), synthesizing their results back into that one session. The natural
question: does this platform capability make the federation's `poga` concurrency substrate
redundant?

It does not — but the reason is worth recording, because the question will recur every time the
platform grows a parallelism feature, and the answer determines whether we keep investing in `poga`.

## Decision

**`poga` lanes and Workflow/ultracode occupy different axes; the federation needs both, and they
compose. We keep `poga` as the session-parallelism substrate and treat Workflow as an in-lane tool.**

The distinguishing insight is the same one that motivated C4/C5: **coordination is only needed
where there is no orchestrator.** A Workflow script *is* the orchestrator — it assigns each subagent
its work, so it inherently knows who is doing what and needs no claims or leases. `poga` lanes are
**leaderless peers**; the claim/lease store exists precisely to coordinate them. The two models sit
on opposite sides of a single divide:

| | Workflow / ultracode | `poga` lanes |
|---|---|---|
| Structure | one orchestrator + subordinate agents | independent peers, no boss |
| Agents | ephemeral, headless, within one turn | durable interactive sessions |
| Identity | none (no Architect, no journal, no continuity) | full federation sessions (journal, cold-restart survival) |
| Git | feed the orchestrating session's result | each lands its own commits to trunk via CAS |
| Human role | fire-and-synthesize | steer each session live |
| Coordination | implicit (the script assigns) | explicit (claims/leases — leaderless) |

The federation's entire model ([ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md))
is **independent Architect sessions** with durable journals landing to a shared trunk and surviving
cold restart. A Workflow subagent is not that and cannot become that within one turn. So Workflow
cannot deliver "open a window, claim an item, steer it to a landed commit" — the exact shape of the
roadmap and the thing `poga` provides.

**They compose, and nesting is the intended pattern:** a single `poga` lane, working its claimed
item, may itself run a Workflow to parallelize that item's decomposable sub-tasks. Peer sessions on
the outside; orchestrated fan-out on the inside.

## Consequences

- **`poga` investment continues** — fleet rollout of the coordination verbs proceeds as planned; it
  is not superseded.
- **Workflow is adopted as the in-lane fan-out tool**, not a replacement for lanes. When a task
  decomposes into sub-tasks one session should synthesize (read-heavy sweeps, multi-lens review,
  broad migrations), reach for Workflow *inside* a lane rather than opening more lanes. Opening more
  lanes is for *independent* work streams, each worth a full steerable session.
- **A selection heuristic** for "I have parallel work": if the pieces are sub-tasks of one goal to be
  synthesized by one context → Workflow (cheaper on human attention, autonomous). If the pieces are
  independent streams each deserving its own interactive session and its own landed commits → `poga`
  lanes (more human attention, true independence). The two are not ranked; they answer different
  questions.
- **This positioning is re-evaluated if the platform ships durable, interactive, independently-
  landing peer sessions** — at which point `poga` would genuinely overlap and we would reassess.
  Today no platform feature provides that; `poga` is not redundant.

## Alternatives considered

- **Abandon `poga`, adopt Workflow/ultracode as the concurrency story.** Rejected: Workflow provides
  orchestrated ephemeral fan-out, not durable peer sessions. It would drop Architect identity,
  journals, continuity, and independent trunk-landing — the load-bearing properties of the federation
  session model. It answers a different question.
- **Build `poga` coordination *on top of* Workflow's orchestration.** Rejected as a category error:
  that reintroduces a single orchestrator (the master session ADR-0051 deliberately retired) and the
  contention the peer model avoids.
- **Do nothing / leave the distinction implicit.** Rejected: the "is `poga` now redundant?" question
  will recur with every platform parallelism feature; recording the axis once makes each future
  answer a lookup instead of a re-derivation.

## References

- [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) — the peer-session model
  (retired master session) that makes lanes leaderless and coordination necessary.
- [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) — `poga` as a git-lifecycle
  wrapper around an interactive LLM session.
- [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) — C4/C5 coordination; the
  "coordination is only needed without an orchestrator" insight this ADR generalizes.
