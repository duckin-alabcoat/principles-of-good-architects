# ADR-0095: Shared data lives outside the repo, under a declared data root

**Status:** Accepted
**Date:** 2026-08-16
**Deciders:** the operator (approved taking the design + ADR this session and leaving the fleet migration to its own pass), Federation Architect (the measurement and the design).
**Implements:** [ADR-0009](0009-data-storage-mechanics.md) (the data root abstraction, decided 2026-05-23 and never built).
**Extends:** [ADR-0007](0007-github-as-system-storage-data-excluded.md) (data excluded from the system repo), [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) (worktree lanes).
**Work item:** WI-0004, deferred as needing its own session in sessions 90, 99/~100 and 154.

## Context

**[ADR-0009](0009-data-storage-mechanics.md) already decided this, and the opposite shipped.**
It defined a **data root** — "a configured path… opaque to the Architect: it knows the path,
it does not assume the substrate" — and explicitly rejected the alternative:

> **Bake the profile location into the federation working directory permanently.** Rejected
> — collapses the test surface and the architectural envelope. A cloud-hosted Architect or a
> second-machine Architect has no federation working directory; the data root is the
> indirection that makes the architecture portable.

What exists today is the rejected option. There is **no `data_root` key anywhere** in
`session.py`, `poga`, `poga_cli.py` or any `session.config.json` — verified by search. Every
data path is a repo-relative literal (`inbox`, `user_profile`) resolved against `ROOT`, and
the data itself (`proposed-edits/`, `users/`, `inputs/`, `architect-learnings.md`,
`retrofit-reports/`, the `*.local` configs) sits *inside* the working tree behind
`.gitignore`.

That was harmless while one checkout existed. [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md)
ended that: **a git worktree materializes tracked files only**, so gitignored data is simply
**absent** in every lane. Session 90 found it the bad way — a lane opened announcing
`inbox: (empty)` while two briefs were pending, one of them time-boxed. The harm was the
silence, not the absence.

### The session-90 patch, and what measuring it shows

The patch symlinks a hardcoded list from the main checkout into each lane
(`SHARED_LANE_PATHS` in `session.py`, `_link_shared_data`). Probed in this lane against the
main checkout:

| Path | Main checkout | This lane |
|---|---|---|
| `proposed-edits` | real dir, 141 files | symlink → main |
| `users` | real dir, 2 files | symlink → main |
| `inputs` | real dir, 5 files | symlink → main |
| `architect-learnings.md` | real file | symlink → main |
| `reconcile-roots.local` | real file | symlink → main |
| `repo-paths.local` | real file | symlink → main |
| **`retrofit-reports`** | **real dir, 3 files** | **ABSENT** |
| **`poga.local`** | **real file** | **ABSENT** |

**Four defects, three of them measured rather than reasoned:**

**1. The list is hand-maintained and has already drifted.** `retrofit-reports/` and
`poga.local` are gitignored data in the main checkout and are on no list, so no lane can see
them. `poga.local` is *sourced by the `poga` wrapper*, so a lane's tooling behaves
differently from main's and nothing says so. The `.gitignore` and `SHARED_LANE_PATHS` are two
hand-kept copies of one fact — the exact [P16](../principles/master.md#p16--avoid-duplication)
drift, in the code that exists to prevent a drift.

**2. The list lives in byte-identical fleet substrate but enumerates the federation's own
data paths.** `session.py` is pushed unchanged to every member, so every member inherits
*our* idea of which data exists. The code handles the mismatch by linking "only a path that
EXISTS upstream" — which means a member whose data path is not on the federation's list gets
no link, no warning, and an empty view that reads exactly like having nothing to say. That is
the [`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)
collapse, in the substrate that ships to everyone.

**3. Symlinks carry reads but not writes — verified in the created configuration.** Editing a
symlinked path from inside a lane is **refused**, and the refusal misdirects:

> `This session is isolated in the worktree …/poga-5. Edit the worktree copy of this file
> instead of the shared-checkout path.`

The path passed *was* the worktree path; the tool resolved the link, saw the shared checkout,
and told the caller to edit "the worktree copy" — a second copy that must not exist, because a
copy is precisely the drift the symlink was chosen to avoid. So every shared-data **write**
(appending to `architect-learnings.md`, filing a brief `pending/` → `applied/`) needs an
out-of-band workaround, and that workaround is tribal knowledge held in one Architect's
memory rather than in the substrate. A mechanism whose correct use is undocumented is not a
mechanism.

**4. Absolute paths break across machine views.** Where one disk is visible from
more than one machine, it can appear under a different absolute prefix on each: the same
bytes, two prefixes. Any fix that writes an absolute
data-root literal into a shared or tracked file is wrong on one of the two machines, and the [ADR-0025](0025-bootstrap-is-a-code-harness.md) guard *dies* on an
absolute inbox for this reason. The indirection is not optional decoration; it is the whole
problem.

## Decision

**D1 — Shared data lives OUTSIDE the repository, in a real directory.** Not inside the working
tree behind `.gitignore`. One copy per machine, seen by the main checkout, every worktree
lane, and any additional clone, with no materialization step and no symlinks. This makes
defects 1 and 3 structurally impossible rather than better-maintained: there is no per-lane
view to keep in sync, and a real directory has no write restriction.

**D2 — The location is reached through a declared data root, never an absolute literal.**
Resolution order, first hit wins:

1. `POGA_DATA_ROOT` environment variable, if set — the escape hatch for a cloud-hosted or
   oddly-mounted member, and the seam tests drive.
2. `${XDG_DATA_HOME:-$HOME/.local/share}/poga/<system-id>/` — the default.

**`$HOME` is what solves the two-machine problem**, and it is the reason this is a default
rather than a config field: it resolves to each machine's own home directory, without
either machine storing the other's prefix anywhere. Per [ADR-0009](0009-data-storage-mechanics.md), each machine has exactly one data
root; per [ADR-0016](0016-memory-is-local.md), per-machine divergence in *data* is the model,
not a bug.

**The pointer must not itself be gitignored data inside the repo.** The obvious shape — a
`data-root.local` beside `reconcile-roots.local` — is circular: it would be invisible in a
lane for the very reason this ADR exists, so the mechanism that locates the data would need
the data-locating mechanism to find it. A convention plus an env var has no bootstrap step.

**D3 — Which paths are data is declared by the member, not hardcoded in shared substrate.**
`session.config.json` gains a `data` block naming the member's own data paths, resolved
against the data root:

```json
"data": ["proposed-edits", "users", "inputs", "architect-learnings.md",
         "retrofit-reports", "reconcile-roots.local", "repo-paths.local", "poga.local"]
```

This is the same move [STANDARD.md](../STANDARD.md)'s `layout` block already makes for
non-standard paths, for the same reason: shared code naming a member's path as a literal
returns a wrong answer shaped like a right one. `SHARED_LANE_PATHS` and `_link_shared_data`
are **deleted**, not extended — the federation's list stops being every member's list, and
the `.gitignore`/list duplication ends because there is one declaration.

**D4 — What does NOT move**, carried forward unchanged from the current code's reasoning:

- **`.session-state/`** — per-tree *by design*. It is what makes a lane invisible to its
  siblings, and [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) had to put
  coordination in the git common dir precisely because of it. Sharing it would break
  isolation.
- **Derived or ephemeral output** — `curate-runs/`, `EVIDENCE.md`, `__pycache__/`. Correct to
  regenerate per tree; a shared copy would be a cache with two writers.

**D5 — Migration is each member's own, and the federation ships the verb.** Moving a member's
gitignored data is a local filesystem operation on files the federation must never touch
([ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md)), and it is not expressible
as a brief's text edit. So the federation ships a `poga data migrate` verb — move the declared
paths to the data root, leave nothing behind, refuse rather than merge if the destination is
non-empty — and each member runs it in its own repo. This mirrors
[ADR-0088](0088-a-mailbox-is-gitignored-inbound-data-fleet-wide.md) D4: federation-owned
convention, member-applied migration.

**The migration is a separate pass from this ADR, deliberately.** WI-0004 carries
`migration: YES` and touches every member repo; sequencing the design ahead of it is what lets the
mechanical half run against a settled decision rather than a moving one.

**D6 — Ship the detector with the capability.** `standard_check.py` gains a data-root
detector that reports three distinct outcomes — resolved / **declared-but-unreadable** /
undeclared — never folding "cannot see it" into "nothing there". That collapse is the
originating defect of this whole neighbourhood (session 90's `inbox: (empty)` over two live
briefs), and shipping the fix without the detector would leave it invisible to the
conformance surface that should show it
([`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)).

## Consequences

**The write problem disappears rather than being worked around.** A real directory outside the
repo is not a symlink, so no tool refuses it and no python-instead-of-Edit workaround is
needed. This is the single strongest argument for doing it properly, and it is now measured
rather than asserted.

**A lane stops being a degraded view.** Today a lane sees six of eight data paths and cannot
write to any of them. After this it sees exactly what main sees, because there is only one
thing to see.

**`.gitignore` gets shorter and stops being load-bearing for lanes.** The five no-trailing-slash
entries exist because those paths are *symlinks* in a lane and a trailing slash matches
directories only — a subtlety documented at length in the file. Once the data is not in the
tree, the entries and their explanation go.

**Two facts become one.** `.gitignore` and `SHARED_LANE_PATHS` are today two hand-kept lists
of "what is data here." D3 leaves one declaration.

**Bootstrap and restore both change, and restore benefits most.**
[ADR-0061](0061-poga-unified-lifecycle-cli-and-state-manifest.md)'s hydrate phase places
non-derivable data; with a data root it places it *outside* the rebuilt repo, so a restored
system's data survives a subsequent re-clone instead of living inside the thing being
replaced.

**Risk: a member that migrates while a lane is open.** The lane's symlinks would dangle. The
migrate verb must refuse while any lane holds a live session — the same liveness evidence
`_tree_has_live_session` already reads — rather than discovering it afterwards.

**Risk: `$HOME` is not stable under every execution context.** A LaunchAgent or a headless
`claude -p` run may carry a different environment than an interactive shell. The
[ADR-0050](0050-headless-background-adoption-runner.md) runner and the
[deploy/](../deploy/) LaunchAgent are the two known cases and both must be checked in the
migration pass — an unset `$HOME` resolving the data root somewhere plausible-but-wrong is
exactly the silent class this ADR is about.

**Not addressed here:** whether the data root should itself be version-controlled in a
separate private data repo. [ADR-0009](0009-data-storage-mechanics.md) sketched that for the
multi-machine case and it stays open; this ADR only moves the data out of the system repo and
gives it an address.
