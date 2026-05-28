# ADR-0016: Memory is local; cross-machine continuity is the session-handoff + registries job

**Status:** Accepted
**Date:** 2026-05-26
**Deciders:** the user, Federation Architect

## Context

Claude Code's memory system stores files under `~/.claude/projects/<project-key>/memory/`, where `<project-key>` is derived from the absolute working-directory path. The user's laptop accesses this project at `/Volumes/user/principles-of-good-architects` (via a network mount of the workstation's filesystem); their other machine accesses the same physical files at `/Users/user/principles-of-good-architects`. The derived project-keys differ, so the two machines have two parallel memory directories that do not sync.

This was discovered empirically in session 9 (`feedback_no_multiple_choice.md` was rewritten on the studio, invisible on the laptop) and pinned in session 10's handoff as an open architectural question.

The federation already has cross-machine continuity for authority-bearing artifacts:

- **ADRs, role docs, session-handoff.md, briefs, PROCESS.md, portfolio.md** — committed to GitHub per [ADR-0007](0007-github-as-system-storage-data-excluded.md).
- **Data-layer registries** (`principles/`, `habits/`, `users/`, `architect-learnings.md`) — gitignored but co-located with the working tree, so in the user's specific laptop+studio shared-mount setup they're shared by virtue of the shared filesystem.

The question this ADR resolves: does memory need its own cross-machine sync mechanism, or is the existing continuity surface sufficient?

The federation-arch.md §5 operating principle already states the implicit position: *"Memory is for behavioral continuity, not authority. Authority lives in ADRs and role docs. When they disagree, the ADR/role doc wins and memory gets corrected."* This ADR pins that into an architectural decision and closes the open question.

## Decision

**Memory is local to each machine's Claude Code project-key path. The federation does not attempt to synchronize memory across machines.**

Cross-machine continuity is the job of:

1. **session-handoff.md** — newest-first state-transfer log, committed to GitHub. Read at session start. The canonical current-state doc per federation-arch.md §11.
2. **ADRs** — committed to GitHub. Architectural authority.
3. **Role doc** ([federation-arch.md](../federation-arch.md)) — committed to GitHub. Adopted principles, habits, scope, rituals.
4. **Registries** (`principles/master.md`, `habits/master.md`, `users/<user-id>/profile.md`) — co-located with the working tree under the [data root](0011-data-root-config.md). Sync mechanism is whatever the user's data-root setup provides (in the user's case, the shared mount; for future multi-machine Architects without shared FS, this becomes its own concern under ADR-0011's territory).

Memory drift between machines is **acceptable** because:

- Memory is behavioral continuity (preferences, recent context, recurring corrections), not authority.
- If a memory is important enough that losing it cross-machine is a real cost, that's a signal to promote it: codify in the role doc, register a habit, write an ADR. Drift becomes a useful gradient that lifts load-bearing content into authoritative storage.
- The architectural target ([feedback-architecture-target-vs-test-surface](../../../Users/user/.claude/projects/-Volumes-user-principles-of-good-architects/memory/feedback_architecture_target_vs_test_surface.md)) includes multi-machine, multi-user, possibly cloud-hosted Architects. Any sync mechanism would need to work across all those deployment shapes; "memory rides a shared filesystem" doesn't generalize.

**Promotion guidance.** When Federation Architect (or any other Architect, recursively) notices that a memory file would be missed if the current machine were lost, that's the trigger to promote the content into role-doc / habit / ADR storage. The decision criterion is *load-bearing-ness*, not *content type*.

**Local substrate fixes are out of scope.** If a particular machine setup has a shared filesystem (network mount, cloud-sync folder), an operator may symlink their Claude Code memory directories to a shared location. This is a machine-local convenience, not a federation mechanism, and is not endorsed or specified by this ADR. Such a setup remains brittle to Claude Code harness changes in how memory paths are resolved.

## Alternatives Considered

- **Sync memory via GitHub (treat as system).** Rejected — memory contains user-specific feedback content; classifying it as system would force data into committed system files in a more concentrated form than the incidental leakage currently flagged in federation-arch.md §13. Also: memory paths are harness-controlled; we can't redirect them to a git-managed location without symlinking, which the next alternative covers.
- **Bake symlinks-to-shared-location into the federation convention.** Rejected — works only for setups with a shared filesystem layer (the user's current laptop+studio mount, or Dropbox/iCloud-style sync). Doesn't generalize to truly independent machines or cloud-hosted Architects. Routes around Claude Code's chosen project-key derivation rather than working with it; fragile to harness changes.
- **Move memory under the data root (`<data-root>/memory/`).** Rejected — Claude Code harness controls the memory directory location; we cannot reconfigure it from the federation side. A symlink workaround would inherit all the problems of the previous alternative.
- **Treat cross-machine memory continuity as a Claude Code feature request.** Out of scope for the federation. Memory keyed on a portable identifier (git remote URL, project-id config) would solve this cleanly, but that's a harness-level change we can't make. If it ever lands, we revisit; until then, the federation does not architect around the missing capability.

## Consequences

### Immediate

- The "cross-machine memory drift" open question (federation-arch.md §13, session-handoff.md session-10 entry) is resolved. Removable from the open-questions list.
- Federation-arch.md §5's existing operating principle — *memory is for behavioral continuity, not authority* — gains a citing ADR.
- Bootstrap-kit work proceeds with no memory-sync component; the kit specifies how an Architect's authority-bearing artifacts sync, not how their memory does.

### On behavior

- **Architects (including Federation Architect) should treat per-machine memory as ephemeral-ish.** Important durable guidance gets promoted; everything else is fine to drift.
- **Session-start ritual remains correct as written** (federation-arch.md §11): read session-handoff + role doc + registries; memory loads automatically but supplements rather than replaces. Sessions on a "cold" machine (one without recent memory) should still be fully oriented.
- **Promotion is now an architectural move with a name.** When a memory feels load-bearing, the right response is to draft a role-doc edit, habit registration, or ADR — not to wish for a memory-sync mechanism.

### Risks

- **Useful-but-not-quite-load-bearing content stays local and can be lost.** Acceptable cost; the alternative is sync infrastructure that doesn't generalize across the architectural target.
- **First-time-on-this-machine sessions miss recent feedback corrections.** Mitigated by the promotion guidance — recurring corrections should already be promoted. If a particular correction keeps reappearing on machine A but not machine B, that's the signal to promote it.
- **Per-Architect bootstrap kits need to communicate this stance explicitly** so receiving Architects don't try to build memory-sync into their own infrastructure. Captured here for the kit-drafting session to pick up.

### Deferred

- **Authority-bearing artifact sync for multi-machine setups without a shared filesystem.** Not a memory problem; a data-root problem. Reopens under [ADR-0011](0011-data-root-config.md)'s territory when a real case appears.

## References

- [ADR-0007: GitHub as system storage; data excluded by policy and gitignore](0007-github-as-system-storage-data-excluded.md) — sync mechanism for system artifacts.
- [ADR-0011: Data-root configuration form](0011-data-root-config.md) — per-machine data-layer abstraction; the natural future home for any cross-machine data-sync questions.
- [federation-arch.md §5](../federation-arch.md#5-federation-specific-operating-principles) — *"Memory is for behavioral continuity, not authority."*
- [federation-arch.md §11](../federation-arch.md#11-session-rituals) — session rituals; describes memory as supplementing rather than replacing the handoff.
- [session-handoff.md](../session-handoff.md) — session 10 entry pins the drift observation that triggered this ADR.
