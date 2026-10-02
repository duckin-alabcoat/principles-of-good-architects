# ADR-0046: Per-system `comms/` surface (agent→the operator communication; federation publishes the contract, does not aggregate)

**Status:** Accepted
**Date:** 2026-07-14
**Deciders:** the operator, Federation Architect

## Context

Three rails already carry cross-system information, and each has a clear owner:

- **`STATUS.md`** ([ADR-0021](0021-cross-system-status-surface.md)) — a system's *current state*, authored at-source at session-end, readable by any consumer.
- **`session-handoff.md`** — *agent→agent continuity* across a system's own sessions.
- **`proposed-edits/`** ([ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md)) — *agent→agent edits* between Architects.

There is no rail for the fourth traffic type: **an agent has something *the operator* needs to see** — "I'm blocked and need you," "this needs a decision," "this shipped," "I recommend X." Today that has no at-source home a consumer may sweep. It surfaces only if the operator happens to open that system's handoff or STATUS, or if the agent is a foreground chat agent that can tell him directly. A member that runs in the background or as a resident daemon has no channel at all — its "the operator, look at this" evaporates into a handoff nobody is reading.

the operator's ask (2026-07-14): a **single at-source home** for agent→the operator communication, so these items stop scattering across project folders and land somewhere a consumer MAY mirror into one place.

The design constraints are the same ones that shaped ADR-0021, and for the same reasons:

- **Start as light as possible.** Not an aggregation engine (the session-21 over-engineering lesson, [ADR-0019](0019-curate-gather-and-staging-boundary.md)). Federation owns a schema and a roster, nothing more.
- **At-source, single-writer.** The authoring system owns its own channel ([P13](../principles/master.md#p13--single-writer-per-state), [P6](../principles/master.md#p6--observations-captured-at-source)).
- **Consumer composes at read-time.** The consumer reads and surfaces; federation does not read, mirror, or aggregate — the [ADR-0021](0021-cross-system-status-surface.md) / [ADR-0011](0011-data-root-config.md)-era boundary holds.

This ADR is the **event-stream sibling of ADR-0021's state-snapshot.** `STATUS.md` answers *"where does this system stand right now?"*; `comms/` answers *"what has this system needed to tell the operator, and when?"* Same authoring discipline, same publish-not-aggregate boundary, different shape (an append-only stream of dated notes vs. a single overwritten state block).

## Decision

**Communication-to-the operator is authored at-source as dated notes under a per-system `comms/` directory; federation publishes the schema and the roster; a consumer reads and composes. Federation does not aggregate.**

1. **At-source authoring.** A system's Architect (or its orchestrator agent) with something *the operator* needs to see writes a note `comms/YYYY-MM-DD-<slug>.md` in its own repo. Single-writer per system ([P13](../principles/master.md#p13--single-writer-per-state)); captured at source ([P6](../principles/master.md#p6--observations-captured-at-source)). The directory is an append-only stream — one file per item, not one overwritten file.

2. **The schema (the contract).** YAML frontmatter, four fields, then a short free-form body:

   ```yaml
   ---
   date: 2026-07-14
   system: example-system      # kebab system id, per ADR-0006
   type: decision-needed       # blocked | decision-needed | milestone | recommendation
   summary: One line — what the operator needs to know.
   ---

   Body: short, free-form. Enough context to act, no more.
   ```

   The four `type` values *are* the definition of "significant" — see the quiet-channel norm below. No status/read/ack field: this channel is write-by-author, read-by-consumer; tracking what the operator has seen is the **consumer's** concern (however a consumer chooses to track it), not a field the author maintains.

3. **Quiet-channel norm.** Routine session narration stays in `session-handoff.md`; current state stays in `STATUS.md`. `comms/` exists **only** for items warranting the operator's direct attention, and it works only if it stays quiet — a channel that fills with routine noise is one the operator learns to ignore. The author's judgment governs; the four types are the bar. When in doubt, it belongs in the handoff, not here.

4. **Consumer side is the consumer's build.** A consumer MAY locate each system's repo, read its `comms/`, and compose a reading surface that raises the two urgent types (`blocked`, `decision-needed`) above the rest. *Locating* each repo is the consumer's job — repo paths are machine-specific **data** ([P3](../principles/master.md#p3--data-system-separation)) and stay out of the roster, exactly as for `STATUS.md`. Federation does not read, mirror, or aggregate. This is the same publish/consume split ADR-0021 drew; whoever builds a consumer owns that build.

5. **Optional adoption, absence tolerated.** Same soft rollout as ADR-0021: a system that has nothing to tell the operator simply has no `comms/`, and that is not a defect. Ships via the **redistribute** rail — one proposed-edit per Architect in `proposed-edits/<arch>/pending/`, gated per-edit by the operator.

6. **Federation dogfoods first.** Federation ships its own `comms/` as the reference implementation ([ADR-0003](0003-federation-architect-is-a-participant.md) recursive participation) before the authoring step is redistributed.

### Classification: `comms/` is **system** (git-tracked)

The brief handed this call to the federation ("communications are point-in-time records, arguably data; but their value is being readable by a sweeping consumer, which favors tracked"). **Decision: `comms/` is system — committed to each system's own repo, not gitignored.** Reasoning:

- **It parallels `STATUS.md` exactly.** ADR-0021 classifies `STATUS.md` as system and tracked, even though it is a point-in-time state record carrying a prose `focus` line — because a consumer must read it from the repo. `comms/` is the same class of artifact (author's own output, in the author's own repo, whose entire value is being readable by that consumer). The event-stream sibling inherits the state-snapshot's classification.
- **It is not *data* under the ADR-0007/0012 rule.** "Data" is content **drawn from or synthesized from *other* systems** (mirrored producer files, ingested cross-system artifacts). A `comms/` note is a system's *own* authored output about its *own* state — like `STATUS.md`'s `focus`, or a handoff entry. It is not mirrored cross-system content, so the classification rule places it on the **system** side.
- **Tracked is what makes the consumer path work.** A gitignored `comms/` could not be read by the consumer from a clone or a synced volume without a side channel — defeating the one thing the channel is for.

Two guardrails ride this classification:

- **No confidential data in the note body.** Per [P14](../principles/master.md#p14--no-confidential-data-in-chats) / `reference-secrets-dont-transmit`: a `comms/` note *references* a secret or sensitive artifact ("blocked on the API key in vault entry X"), it never embeds one. The note is committed to a repo, so it carries only what a `STATUS.md` `focus` line could.
- **Per-system override permitted.** A system whose the operator-facing communications would be inherently sensitive may gitignore its own `comms/` as a recorded per-system divergence (the same override mechanism ADR-0012 grants for repo visibility). The **default is tracked**; the override is deliberate and logged in that system's own ADR/role doc, and it makes that system's `comms/` unreadable by the consumer's normal read path (the consumer must then reach it another way — the system's choice).

## Alternatives Considered

- **Reuse `STATUS.md`'s `focus`/`blocked` fields for the operator-facing comms.** Rejected — `STATUS.md` is a single overwritten *state* block; it cannot carry a dated *stream* of distinct items, and overwriting loses the milestone/recommendation history. `blocked` already carries one urgent bit; it does not carry "here are the four things I've flagged for you this week." State-snapshot and event-stream are different shapes.
- **A single shared comms file all systems append to.** Rejected — violates single-writer ([P13](../principles/master.md#p13--single-writer-per-state)); write contention and muddy provenance. Per-repo `comms/` keeps one writer per directory, same as per-repo `STATUS.md`.
- **Federation aggregates comms into a central Inbox.** Rejected — federation only runs in federation sessions, so a federation-kept mirror goes stale the instant any other system authors a note. Reintroduces the ADR-0021 freshness problem and makes federation the aggregation engine the operator ruled out. Aggregation is read-time, consumer-side.
- **Classify `comms/` as data (gitignored).** Rejected — communications are the most data-*shaped* artifact, but the ADR-0007/0012 rule keys on *provenance* (drawn from other systems), not on tone; a system's own authored output is system. And gitignoring it breaks the consumer read path the channel exists to enable. See the classification section.
- **Route everything through a foreground chat agent.** Rejected — only a member in a foreground chat can reach the operator that way; a member that runs in the background or as a resident daemon has no live chat surface. The at-source file channel is exactly what gives a headless system a voice.

## Consequences

- **New optional capability for every Architect:** author a `comms/YYYY-MM-DD-<slug>.md` when something warrants the operator's attention. Ships via a redistribute pass (one proposed-edit per Architect), gated per-edit by the operator. This cross-Architect convention is why this is an ADR. Because adoption is optional and the schema is four fields, the per-Architect edit is small.
- **portfolio.md** gains a "Communication surface" section documenting the convention and pointing here, mirroring its "Status surface" section.
- **Federation's session-end ritual** gains a light **comms check** step: at close, consider whether the session produced a `blocked` / `decision-needed` / `milestone` / `recommendation` item for the operator and, if so, author the note. Judgment-driven, not mechanical (unlike the `STATUS.md` stamp, which `session.py end` can automate) — the whole point is that most sessions produce nothing here.
- **A consumer's read/compose side is its builder's work:** repo-locating and the reading surface that raises `blocked`/`decision-needed`. Federation owns only the contract.
- **Runtime-agnostic.** Authoring a dated markdown file is a plain filesystem write; it satisfies the [ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md) floor in any runtime with no Claude-specific binding. A member that runs on more than one runtime can author `comms/` from any of them identically.
- **Interaction with F2 (the ADR-0021 redistribute completion).** Some members still publish no `STATUS.md`; their per-Architect edit folds `comms/` adoption together with the owed `STATUS.md` adoption, so those systems take one pass, not two.
- **Framing:** an extension of federation's existing **Redistribute** rail and the ADR-0021 publish/consume boundary, not a new mission responsibility.

## References

- [ADR-0021: Cross-system status surface](0021-cross-system-status-surface.md) — the state-snapshot sibling this ADR mirrors in structure and boundary; `comms/` is its event-stream counterpart.
- [ADR-0006](0006-naming-convention-corrected.md) — system ids used as the `system` field.
- [ADR-0007](0007-github-as-system-storage-data-excluded.md) / [ADR-0012](0012-per-architect-repo-conventions.md) — system-vs-data classification; the rule that places `comms/` on the system side and permits the per-system gitignore override.
- [ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md) — the redistribute delivery rail this ships on.
- [ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md) — runtime-agnostic floor; `comms/` authoring is binding-free.
- [P13](../principles/master.md#p13--single-writer-per-state), [P6](../principles/master.md#p6--observations-captured-at-source), [P3](../principles/master.md#p3--data-system-separation), [P14](../principles/master.md#p14--no-confidential-data-in-chats).
- Consultant brief `proposed-edits/federation-arch/pending/2026-07-14-comms-convention-and-status-redistribute.md` (F1) — the source engagement.
