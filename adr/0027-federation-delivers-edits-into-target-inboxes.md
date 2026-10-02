# ADR-0027: Federation delivers proposed-edit briefs into target inboxes over the shared volume

**Status:** Accepted
**Date:** 2026-06-21
**Deciders:** the operator, Federation Architect

> **Superseding note, 2026-09-26 (WI-0012): one inbox model.** The model table below had a third row, *read-federation-direct*: one member read its briefs out of the federation's own `proposed-edits/<arch-id>/pending/`, and Decision §2 made writing there count as delivery. [ADR-0028](0028-converge-inbox-model-onto-repo-local.md) chose repo-local as the one model. That member now reads its own repo-local inbox (seeded, probed, and since verified by use), so that row and §2 are removed here. They are not rewritten; the ADR-0028 downstream step names this note as the way to record the collapse. The model is now **one row**: every member, the custom-path one included, reads `<its repo>/<its declared mailbox>`, and the federation delivers by copy. The **transport** this ADR described, copying over the shared volume, was itself superseded by [ADR-0107](0107-mail-transits-origin-and-every-machine-drains-its-own-share.md): a brief is queued in `outbox/to-<id>/`, travels by `origin`, and each machine's poller delivers the share it can reach. The **delivery semantics** here still stand: copy into the configured inbox, never write outside it, and never guess a path. The old location now refuses by name, not silently. `deliver.resolve` raises `RetiredInbox` for a member whose mailbox resolves into the federation's own tree, and the mail poller names any brief left there, in a `retired` bucket with the fix.

## Context

The Redistribute mission ships a proposed-edit brief to each affected Architect; the brief lands in that Architect's receipt-ritual inbox, and the Architect's session-start sweep surfaces it for application ([ADR-0013](0013-receipt-ritual.md)). ADR-0013 deliberately left the **cross-machine transport** unspecified — it called the artifacts "substrate-opaque" and assumed "whatever sync mechanism is configured at the substrate level (mounted volume, git push/pull, rsync) carries the receipt-ritual artifacts the same way it carries everything else under the data root."

In practice that assumption never resolved to a concrete, working transport, and session 42 surfaced why:

- **`proposed-edits/` is gitignored data** ([ADR-0007](0007-github-as-system-storage-data-excluded.md), `.gitignore`). So git — the configured sync substrate for everything else — explicitly does **not** carry inbox artifacts.
- **Each Architect has its own data root** (the federation's own repo, and each member's own project directory). A brief the federation drafts in its *own* data root does not appear in a target's data root by any automatic means.
- The result: briefs drafted federation-side were stranded. The session-37 convergence briefs sat unapplied; some were hand-copied into a target repo ad hoc, others never reached their target at all.
- ADR-0013's "Risks" section even flagged the tension: a "direct write to another Architect's repo when the same machine hosts both" was named a *substrate-coupling regression* — leaving it ambiguous whether the federation may write into a target's inbox at all.

[ADR-0026](0026-cross-machine-execution-is-standard-substrate.md) made cross-machine reach **standard substrate**. The design supports an always-on shared volume mounted at the same path on each machine, and the runner is reached by an SSH host alias the operator configures. With that setup, every Architect's repo is visible at a stable path under one mounted volume. That makes a concrete transport available, and forces the ambiguity to a decision.

A survey of the live targets (session 42) found **two inbox models already in use**, which this ADR must accommodate:

| Model | Who | Inbox location read at session start |
|---|---|---|
| **Repo-local** | the kit-based Architects | `<target-repo>/proposed-edits/<arch-id>/pending/` (the `inbox:` value in `session.config.json`) |
| **Repo-local (custom path)** | a pre-kit Architect with a custom inbox path | `<target-repo>/architect/proposed-edits/pending/` |

*(A third row, the read-federation-direct model, was removed 2026-09-26; see the superseding note above.)*

## Decision

**The federation delivers a proposed-edit brief by copying it into the target Architect's configured inbox, over the shared volume — and this is sanctioned, not a regression.** Dropping a file into the inbox a target *designed to receive federation deliveries* is delivery, not a bypass; the ADR-0013 caution is hereby scoped to mean the federation must never edit a target's **role doc or other working files** directly — only its inbox.

Concretely, per inbox model:

1. **Repo-local inbox (the default, and the kit standard).** The federation locates the target repo under the shared volume and copies the brief into the target's configured inbox path (`proposed-edits/<arch-id>/pending/`, or the target's declared variant). It creates the `pending/` directory if absent. If a brief with the same edit-id already sits there (a refresh), it **overwrites** — the edit-id is the identity, the newest draft wins.
2. *(Retired 2026-09-26. This clause sanctioned the second model; see the superseding note above. The numbering is kept so citations of §3 still resolve.)*
3. **Unreachable target.** If the target repo is not present on the shared volume (e.g., a checkout that lives outside the shared volume, on another machine), the federation does **not** guess a path. It leaves the brief staged federation-side and surfaces the target as undelivered to the operator. Wrong-path delivery (a phantom no one sweeps) is worse than non-delivery.

**Path resolution stays out of committed system files.** Target repo paths are machine-specific data ([P3](../principles/master.md#p3--data-system-separation)) — the federation discovers them at delivery time (locate the repo under the shared volume's project area) rather than hard-coding them in any tracked artifact. This ADR records the *mechanism*, never the literal paths.

**The federation's `proposed-edits/<arch-id>/` remains the authoritative draft + record.** Delivery is a copy *out* of it; it is not emptied on delivery. Where a target keeps a repo-local copy, the two can diverge — the federation-side copy is the canonical draft, and a refresh re-delivers (overwrites the stale repo-local copy).

## Alternatives Considered

- **the operator hand-carries every brief.** Rejected — it is the manual toil the agent-driven-no-toil preference exists to eliminate, and it is exactly the path that left the session-37 briefs stranded.
- **Make every Architect read the federation data root directly (the read-federation-direct model, universally).** Tempting — no copies, no federation-side/repo-side drift. Rejected *for now* because it couples every session to the shared volume being mounted (an Architect can't see its inbox offline), and it requires re-pointing the kit's `session.config.json` inbox at a discovered federation path on every Architect. Recorded as a candidate convergence (see Consequences) rather than forced here.
- **Un-gitignore `proposed-edits/` and sync via git.** Rejected — proposed edits are per-machine transient artifacts about role-doc changes, not public system canon ([ADR-0007](0007-github-as-system-storage-data-excluded.md)); committing them pollutes the system repo and still wouldn't deliver across separate repos.
- **A network/cloud sync daemon for inboxes.** Rejected as over-engineered for a handful of co-mounted repos; the shared volume already provides the transport.

## Consequences

- **Redistribution actually lands.** A drafted brief reaches the target's sweep instead of stranding federation-side. This is what makes the Redistribute mission real cross-machine.
- **ADR-0013's transport gap is closed** with a concrete mechanism, and its line-326 caution is disambiguated (inbox delivery = sanctioned; role-doc writes = still forbidden).
- **Two inbox models persist** (repo-local, read-direct). The federation must be model-aware on delivery. **Follow-up:** converge the inbox model federation-wide — likely onto read-federation-direct once the shared volume is dependable everywhere — so delivery becomes a no-op for all Architects. Tracked, not done here. *(Resolved the other way: ADR-0028 converged on repo-local, and the collapse to one model is recorded in the 2026-09-26 superseding note above.)*
- **A member whose checkout lives outside the shared volume is undeliverable from it.** Its brief stays staged federation-side until its repo is reachable or it moves onto the volume. This makes that migration item load-bearing for redistribution, not just for git hygiene.
- **Drift risk between the federation-side draft and a delivered repo-local copy.** Mitigated by edit-id-as-identity + overwrite-on-refresh, and by the federation-side copy remaining canonical.
- **A new structural-guard candidate:** the federation should never write outside a target's inbox directory. If delivery is ever code-harnessed, that guard belongs in the harness ([`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence) / [P15](../principles/master.md#p15--code-for-mechanism-not-judgment)).

## References

- [ADR-0013](0013-receipt-ritual.md) — receipt ritual; the transport this ADR concretizes (and whose line-326 caution it disambiguates).
- [ADR-0026](0026-cross-machine-execution-is-standard-substrate.md) — the shared volume + `ssh runner` substrate that makes shared-volume delivery possible.
- [ADR-0007](0007-github-as-system-storage-data-excluded.md) — why `proposed-edits/` is gitignored data (the reason git can't be the transport).
- [ADR-0009](0009-data-storage-mechanics.md) — data-root abstraction / substrate-opacity envelope.
- Federation session 42 (2026-06-21) — P17 redistribution surfaced the stranded-brief gap; the operator chose shared-volume delivery (option B) and directed this ADR.
