# ADR-0036: Session liveness sidecar and evidence-based orphan auto-triage

**Status:** Accepted
**Date:** 2026-07-05
**Deciders:** the operator, Federation Architect

## Context

[ADR-0020](0020-session-rituals-are-a-code-harness.md) detects an orphan from a
single bit: the topmost handoff entry has a `Start:` but no `End:`. **Three
different realities collapse to that one signature**, and the code cannot tell
them apart:

1. **Session still live** — possibly on the *other* machine. Where both
   machines run against one shared working tree, a session humming
   on the Runner is indistinguishable from an abandoned one when a session opens
   on the Laptop.
2. **Cleanly finished, close ritual skipped** (terminal closed, user walked
   away). The common case; the bad/keep answer is essentially always "keep."
3. **Crashed mid-flight.** Rare, and already made non-catastrophic by
   mid-session checkpointing.

Because the three are indistinguishable, every occurrence became a blocked start
+ a bad/keep round-trip to the operator + hand surgery + a re-run of `start`, with the
false-alarm class dominating. **Session 46 is the proof of harm:** a real
session that did v2.14.0→v2.15.1 of committed work was nearly marked `NC` from
an empty-body misread — the session-46 verify-git-before-ruling-orphan lesson.

Two adjacent defects sit in the same seam:

- **Stale orphans present as mystery dirt.** The start stamp is uncommitted
  until session end (by design), so an abandoned session leaves the handoff
  dirty. The idempotent re-run guard only recognizes that state when the topmost
  entry is dated **today**; an abandoned session from any earlier day falls
  through to the generic `BLOCKED: working tree is dirty` — the worst failure
  mode, a symptom with no cause. The same guard also false-blocks a legitimate
  session that crosses midnight.
- **A concurrent second session is silently misclassified as a re-run.** A
  second startup on the other machine, same day, sees handoff-only-dirty +
  topmost-in-progress-today → already-started → it believes it *is* the first.
  Two live sessions share one stamp; whichever runs `end` first closes it and
  the other's `end` fails on double-close.

An external consultant engagement (Auditor role class,
[ADR-0004](0004-auditor-as-separate-role-class.md)), commissioned by the operator and
delivered 2026-07-05, verified the relevant platform facts against the Claude
Code hooks docs:

- A **`SessionEnd`** hook fires once on clean termination
  (`clear`/`resume`/`logout`/`prompt_input_exit`/`other`) but **not** on
  crash/force-kill — which makes it the clean-vs-crash discriminator. Default
  timeout 1.5s; cannot block termination; cannot set a title.
- A **`Stop`** hook fires after every assistant turn; its stdin carries
  `session_id`; a file-touch heartbeat is well within budget.
- **`SessionStart`** stdin carries `session_id` — which `session.py start`
  currently reads none of.

## Decision

**A gitignored liveness sidecar plus git evidence turns orphan handling from a
guess into a decision table, and orphan surgery from hand-edited LLM text into
one subcommand.**

### 1. `.session-state/` liveness sidecar

Gitignored, ephemeral, all-derivable — this **does not extend** the
[ADR-0033](0033-non-derivable-data-is-backed-up-at-machine-level.md)
unbacked-gitignored-data surface (nothing here is non-derivable):

- **`SessionStart`** (`session.py start`) reads stdin and writes
  `.session-state/<session_id>.live` → `{session_n, machine, started}`.
- A **new `Stop` hook** heartbeats that file with a fresh timestamp — the crash
  detector **and** the live-elsewhere detector; a shared working tree makes one
  machine's heartbeat visible to the other's start.
- A **new `SessionEnd` hook** writes `.session-state/<session_id>.ended` with a
  timestamp **only** — the ~1.5s budget forbids git work here; the *next* start
  does any handoff surgery.

### 2. Idempotency keyed on `session_id`

Not same-day + handoff-only-dirty:

- Same `session_id` re-running → already-open, **regardless of date** (fixes the
  midnight false-block).
- A **different** `session_id` against a live entry → the concurrent-session
  case, **announced** ("session N live on `<machine>`, heartbeat Xm ago")
  instead of silently piggybacked. Surfacing beats guessing; the resolution
  (wait / proceed anyway) stays with the user, and should be rare.

### 3. Evidence-based orphan auto-triage — the amendment to ADR-0020

With sidecar + git evidence, orphan handling at start is a decision table:

| Evidence | Verdict | Action |
|---|---|---|
| Fresh heartbeat, no `.ended` | Live elsewhere | Announce; do **not** touch the entry; concurrent-case flow (item 2) |
| `.ended` marker present | Clean exit, ritual skipped | **Auto-close** at marker time; one info line in the orientation block |
| Stale heartbeat, no marker | Crash/sleep | **Auto-close** at last-heartbeat time; flag for the summary |
| No sidecar (legacy/pre-rollout) | Unknown | Git-evidence fallback, then the residual bad/keep question (now rare) |

The **git-evidence fallback** is the [P15](../principles/master.md#p15--code-for-mechanism-not-judgment)
orphan-detector the session-46 lesson asked for: `git log` since the orphan's
`Start` stamp — commits present → "likely a real unclosed session, reconstruct,
don't `NC`" — before the residual bad/keep question. **"Bad" becomes a post-hoc
reclassification, not a startup gate.**

### 4. `session.py resolve-orphan --keep|--bad [--end-at STAMP]`

Mechanizes the surgery that was hand-edited LLM text-surgery on the exact
machine-parsed handoff format the harness exists to protect — append `CORRUPT`,
tag the SESSION LOG row `NC`, synthesize a zero-duration end stamp — performed
at the worst moment, pre-orientation. It operates on the topmost orphan (the one
blocking a start). `--keep` takes an optional `--end-at` so triage evidence can
supply the real end time; the command does the surgery only and prints "re-run
`session.py start`" (it is a manual command with no hook stdin, so it must not
try to open a session itself — `start`'s own auto-triage handles the inline
decidable cases). **Ships regardless of item 3** — even if the bad/keep question
stayed, the resolution becomes one command instead of a multi-turn edit session.

### Amendment rationale

[ADR-0020](0020-session-rituals-are-a-code-harness.md) reserved bad/keep for the
user because **code had no evidence** — the flag was all it could produce. With
evidence, the common case is decidable, and the cost-asymmetry spine applies: a
cheap, reversible auto-action (auto-close, fully reversible via
`resolve-orphan --bad` after the fact) should not cost a startup interruption
**every time** to guard against a rare, still-correctable mistake. the operator reviewed
the argument, commissioned the brief, and approved landing the doctrine change
this session (session 47).

## Alternatives Considered

- **Keep inferring liveness from the one Start-no-End bit (status quo).**
  Rejected — it is the false-alarm engine: three realities, one signature, a
  blocked start every time. The sidecar adds the missing bits (heartbeat,
  `.ended` marker) that separate them.
- **Do the handoff surgery inside the `SessionEnd` hook.** Rejected — the ~1.5s
  `SessionEnd` budget forbids git work, and the hook cannot block termination.
  It writes a timestamp-only marker; the next start does the surgery.
- **Do git work in the `Stop` heartbeat.** Rejected — it fires after *every*
  turn; a git touch per turn is wasteful. A file-touch is well within budget and
  is all the crash/live-elsewhere detector needs.
- **Keep reserving bad/keep for the user unconditionally
  ([ADR-0020](0020-session-rituals-are-a-code-harness.md)).** Rejected *given
  evidence* — that reservation was correct when code had no evidence, but with
  the sidecar the common case (clean exit, ritual skipped) is decidable, and the
  reversible auto-close is cheaper than an every-startup interruption. The
  reservation survives only for the genuinely ambiguous residual (no sidecar, no
  git evidence).
- **Track liveness in a committed file.** Rejected — it is ephemeral,
  per-machine, fully derivable state; committing it adds churn and a merge
  surface for zero durability gain. The durable record is still the tracked
  start stamp; the sidecar only *adds* evidence.

## Consequences

- **Amends [ADR-0020](0020-session-rituals-are-a-code-harness.md).** The orphan
  doctrine moves from detect-and-ask to evidence-based auto-triage; the flag
  becomes a post-hoc reclassification. Everything else in ADR-0020 stands.
- **The `Stop`/`SessionEnd` hooks are `settings.json` floor additions**,
  delivered fleet-wide via the [ADR-0034](0034-settings-are-federation-pushed-generated-substrate.md)
  settings path + push-substrate — added to `standard-settings.json`, generated
  into every converged repo.
- **The durability model is unchanged.** The start stamp is still written at
  start into the tracked handoff; the sidecar only **adds** evidence, never
  replaces the record ([P13](../principles/master.md#p13--single-writer-per-state)
  single-writer on the handoff is untouched).
- **`session.py` ships byte-identical everywhere**
  ([ADR-0031](0031-delivery-integrity-self-contained-briefs.md)), so the **R1
  test gate is a precondition** — the triage decision table, the `session_id`
  idempotency cases, and the `resolve-orphan` surgery go in the pytest file
  before this rides a substrate push.
- **Item 5 of the liveness brief is part of this decision:** route
  handoff-only-dirty + unclosed-topmost into the orphan flow on **any** day,
  reserving the generic dirty-tree block for genuinely unexplained dirt. This
  fixes both the stale-orphan-as-mystery-dirt failure and the midnight-crossing
  false-block.
- **Session 46 could not recur:** a stale earlier-day orphan with committed work
  hits the git-evidence fallback ("reconstruct, don't `NC`") instead of the
  generic dirty block, and a concurrent second session is announced instead of
  silently piggybacked.

## References

- [ADR-0020](0020-session-rituals-are-a-code-harness.md) — the orphan doctrine
  this amends; detect-and-flag becomes evidence-based auto-triage.
- [ADR-0034](0034-settings-are-federation-pushed-generated-substrate.md) — the
  `standard-settings.json` + generator path the new `Stop`/`SessionEnd` hooks
  ship through.
- [ADR-0033](0033-non-derivable-data-is-backed-up-at-machine-level.md) — why the
  all-derivable `.session-state/` sidecar does not extend the
  unbacked-gitignored-data surface.
- [ADR-0031](0031-delivery-integrity-self-contained-briefs.md) —
  byte-identical `session.py` push; why the R1 test gate precedes it.
- [ADR-0004](0004-auditor-as-separate-role-class.md) — the Auditor role class of
  the commissioning consultant engagement.
- [P13](../principles/master.md#p13--single-writer-per-state),
  [P15](../principles/master.md#p15--code-for-mechanism-not-judgment) — the
  single-writer floor the sidecar preserves and the code-for-mechanism rule the
  git-evidence detector instantiates.
- The session-46 *verify-git-before-ruling-orphan* lesson (feedback memory) —
  the failure the git-evidence fallback hardens in code.
- Source brief: `proposed-edits/federation-arch/pending/2026-07-05-session-liveness-and-orphan-autotriage.md` (withheld)
  — the consultant findings this decision records.
- Companion: [ADR-0035](0035-startup-turn-budget-inject-and-git-diagnosis.md) —
  same engagement; the startup turn budget, separable landing.
- Federation sessions 46 (the proof of harm) and 47 (2026-07-05, the decision).
