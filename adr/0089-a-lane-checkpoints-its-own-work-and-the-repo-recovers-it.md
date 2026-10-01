# ADR-0089: A lane checkpoints its own work, and the repo that owns it recovers it

**Status:** Accepted
**Date:** 2026-08-06
**Deciders:** the operator (the ruling that prompted the work: the Architects do all git work, and the operator should never have to; **accepted this ADR in session ~121 after reading it in full** — *"accept"*), external consultant (the requirements and the acceptance criteria), Federation Architect (the two delegated design calls, D1 and D2).
**Extends:** [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) (worktree lanes), [ADR-0054](0054-heartbeat-on-tool-use-and-crash-reap-grace.md) (heartbeat + clean-exit evidence), [ADR-0055](0055-lazy-session-start.md) (first-heartbeat materialization).
**Depends on:** WI-0082 (landed `c8e5270`) — a dirty lane is no longer force-removed. Every decision below assumes stranded work still *exists*; before that fix it could be deleted before anyone recovered it.

## Context

"Architects do all git" is enforced only while an Architect is **awake in the repo**.
`session.py end` in a lane is the closing move of a session, and when a session stops
without it, nothing touches git in that repo again: `record-end` deliberately does no git
(fail-open, ~1.5 s SessionEnd budget), milestone commits strand on the lane branch, and
mid-flight edits strand as bare uncommitted files.

Third-plus occurrence of the same interface failing:

- **2026-07-23 stress run** — poga-5 stranded silently; that round of fixes addressed
  the loud/silent split, not the stranding.
- **The federation's own killed-sessions incident** — ~35 min of unlanded work
  in a dead lane, recovered by hand-crafted patch replay.
- **2026-07-27, one member** — poga-1 and poga-3 both wrote clean `.ended` markers and never
  landed: 6 commits stranded 8 days, plus uncommitted edits sitting as bare files,
  invisible to every surface until an outside observer happened to look on 08-03.

Per [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)
this is well past the threshold where discipline — the close ritual, role-doc text, a
brief — is an acceptable enforcement mechanism.

**One premise in the requirements needed correcting before designing against it.** The
brief states that ended-but-unlanded detection lives only in an outside observer. It does not:
`reap-lanes` already classifies unmerged lanes and surfaces them loudly by name, and
`_auto_reap_lanes` runs at every session's first heartbeat. The real gap is narrower —
the **automatic** path is silent and handles only the provably-safe subset, so an
ended-but-unlanded lane is surfaced only if a human runs the standalone command; and
until WI-0082 neither path knew anything about uncommitted files. So this is not "build
detection." It is "surface the detection that already exists, and extend it to the
working tree." That is a materially smaller and better-shaped job.

## Decision

### D1 — The crash case is covered by preservation and surfacing, not by snapshotting

The requirements ask that clean stops checkpoint, and explicitly delegate the *depth* of
crash coverage — *"Your call on depth; requirement is only that clean stops checkpoint,
with the crash case explicitly decided rather than defaulted."* This is that explicit
decision.

**A lane checkpoints on clean stop (`record-end`). It does not checkpoint on heartbeat.**

The rejected option is a throttled dirty-tree commit on the existing heartbeat path,
which fires on every tool call. Its cost is not merely "ugly commits nobody sees": it
would commit **mid-edit** states on a branch the operator is also using, churning the
index under a session that is still working, and it would make the substrate a
second writer of the lane's git state while the Architect is live in it —
[P13](../principles/master.md#p13--single-writer-per-state) pointed at the working tree.

What makes that acceptable is that the crash case is now covered by two other properties
that did not exist when the requirement was written:

1. **The work cannot be destroyed.** WI-0082: a lane holding uncommitted work classifies
   `dirty`, the silent sweep never touches it, and `_reap_lane` no longer passes
   `--force`. That member's failure mode was work sitting unreachable; it is now work sitting
   *preserved*.
2. **The work cannot stay invisible.** D3 below surfaces it at the next session start in
   the owning repo, with no external observer involved.

Preserved plus surfaced is the property the invariant actually needs. Snapshotting buys
"the bytes are in a commit" at the cost of fighting the live session for its own index,
and it does not improve visibility at all — an unnoticed `wip` commit is exactly as
stranded as an unnoticed dirty file.

**Named as a future option rather than closed off:** if surfacing proves insufficient in
practice — a lane that is surfaced and then ignored for weeks — heartbeat checkpointing
becomes the next escalation, and this ADR should be amended rather than reinterpreted.

### D2 — Land absorbs a checkpoint, and says that it did

*"Absorb-silently vs refuse-loudly is a design choice for the ADR; 'ff them onto trunk'
is the one wrong answer."*

**Land absorbs, and reports the absorption.** Refuse-loudly is rejected on the founding
ruling: it hands the operator a git chore at exactly the moment the substrate exists to
spare them one. Absorb-*silently* is rejected because a commit that vanishes without a
line in the record is the kind of quiet history edit
[P2](../principles/master.md#p2--system-artifacts-evolve-auditably) exists to prevent.

The absorption has two halves, and the first is what makes the second rare:

- **On resume, the checkpoint dissolves.** If a session opens a lane whose `HEAD` is a
  checkpoint commit this mechanism made, the first heartbeat runs `git reset --soft
  HEAD^` — restoring the working tree exactly as the operator left it, uncommitted, with
  the checkpoint gone. The safety net is not meant to be a permanent artifact; it exists
  to cover the interval between stop and resume. (`--soft` only: the working tree is never
  touched, and `reset --hard`/`--keep` remain denied by the destructive-git guard.)
- **At land, a surviving checkpoint is squashed into the close commit** and named in the
  land output. This is reachable when a lane is landed without ever being resumed.

### D3 — Recovery is reported by the repo that owns the lane, at session start

An ended-but-unlanded lane — an `.ended` sidecar with an unmerged branch, or a checkpoint
tip, or (post-WI-0082) a `dirty` classification — is **named in the startup banner** of
the next session in that repo, with the one command that resolves it. An outside
observer is the backstop, never the primary; an observer finding that member's stranded
work on day 8 was a fortunate accident of one existing and being pointed at the right
repo.

This is deliberately *reporting*, not acting. The automatic path stays limited to what is
provably safe; resuming or discarding stranded work remains a human call
([`confirm-destructive-ops`](../habits/master.md#confirm-destructive-ops)/[P9](../principles/master.md#p9--destructive-ops-confirmed)).

## Consequences

**The operator-visible invariant**, which is the acceptance criterion worth restating as
doctrine: *no keystroke of Architect work is ever outside git for longer than one session
stop* — and the operator never runs a git command to make that true.

- A lane branch may carry a `wip(checkpoint)` commit between a stop and the next resume.
  It is private scratch; it never reaches the trunk.
- The close ritual keeps its meaning. A checkpoint is explicitly **not** a close: it
  stamps no `ended`, writes no journal body, and a session that stopped without landing
  still reads as flown-not-landed. This ADR makes stranded work *safe and visible*; it
  does not make an abandoned session look finished.
- Main-checkout sessions are unaffected. A dirty main checkout at stop remains the
  interactive-close guard's territory.
- Every mechanism here fails open. A checkpoint that cannot be written, a resume-dissolve
  that cannot run, or a banner line that cannot be composed must never block a session
  from opening or closing.
- This is shipped substrate (`session.py` is byte-identical fleet-wide), so it reaches
  every member at the next push. It adds no member-side action: no config key is required
  and absent state degrades to today's behaviour.

## Alternatives considered

- **Heartbeat checkpointing** — D1; rejected as a second writer of a live session's index,
  and named as the escalation if D3 proves insufficient.
- **Refuse-to-land until absorbed** — D2; rejected as handing the operator the git chore
  the substrate exists to remove.
- **Auto-resume or auto-discard a stranded lane at startup** — rejected under P9. The
  substrate preserves and reports; a human decides.
- **Leave recovery to an outside observer** — rejected as the status quo that took 8 days, and as
  an inversion: an observer is a backstop, and cannot be a dependency of every
  member's own safety.
