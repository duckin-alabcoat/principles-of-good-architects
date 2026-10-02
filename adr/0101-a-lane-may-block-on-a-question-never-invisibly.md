# ADR-0101: A lane may block on a question, never invisibly

**Status:** Accepted
**Date:** 2026-08-22
**Deciders:** the operator (the ruling that a POGA session which cannot reach the operator is no better than an unattended agent; the pre-approval; the ruling that finding and answering questions is the operator's job, as it already is). Federation Architect (the record-not-delivery split, the three-state rule, deferring attendance detection).
**Builds on:** [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) (the coordination store), [ADR-0075](0075-dispatch-is-a-launcher-and-the-wave-trigger-is-the-landing-session.md) (dispatched lanes self-claim and ask), [ADR-0099](0099-the-user-is-not-an-execution-surface.md) (P10 ownership), [ADR-0100](0100-dispatch-spawns-headless-the-operator-s-machine-attaches.md) (the window the question appears in)
**Reality:** Partial — hook, record, and surfacing built; the `Notification` payload shape is verified against one installed Claude Code release only.
**Work item:** WI-0162

## Context

A dispatched lane is told, in the prompt dispatch itself composes: *"Ask me questions exactly
as you would in a session I had opened by hand."* So it will ask. The question is where the
asking goes.

Under the pre-[ADR-0100](0100-dispatch-spawns-headless-the-operator-s-machine-attaches.md)
arrangement the answer was nowhere: lanes spawned into **detached** tmux sessions, no window
appeared, and a lane that asked something sat blocked in a pane nobody had been told to look
at. ADR-0100 fixes the common case — every lane becomes a window the operator opened. It does
not fix the residual one, and the residual one is the dangerous shape: **a window you have
scrolled past is indistinguishable from a window with nothing in it.**

The substrate cannot currently tell the two apart either, and this was demonstrated rather
than argued. On 2026-08-22 two abandoned lanes — poga-1 and poga-2, hours past their last human
contact — were reported by the session roster as `interactive · idle` and by `reap-lanes` as
`live session — protected`, for hours. An attended session waiting on the operator and an abandoned
session waiting on nobody produced byte-identical status. Every liveness signal we keep
answers *"did the process move"*, and none answers *"is a human there."*

the operator's own ruling scopes the fix: finding and answering questions is the operator's job. So this is not a notification system. It is a floor under one: the guarantee that
a lane which is waiting is **countable**, so that a question missed in the moment is not a
question lost.

## Decision

**A lane may block on a question. It may not block invisibly. The blocking is allowed; the
silence is not.**

### D1 — A `Notification` hook writes an attention record

Claude Code fires `Notification` when a session needs the user. Verified present in the
installed runtime alongside `PreToolUse`, `SessionStart`, `Stop`, `SubagentStop`,
`UserPromptSubmit`, and `WorktreeRemove`; the settings floor wires five of those and not this
one.

On that event the lane writes an attention record into the shared coordination store
([ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md)), kind `attention`, keyed by
the lane's coordination identity: lane, claimed item if any, the message, and the timestamp.
It is cleared when the session next does work, and on session end.

Structural, not disciplinary. A rule that a blocked lane *should* announce itself is a rule
that fails exactly when the lane is stuck, which is the moment it matters —
`add-structural-guard-on-recurrence` applied before the recurrence rather than after.

### D2 — The record is the truth; delivery is best-effort

Any interrupt, relay, or notification built on top of this is a **delivery optimisation**. The
record is what makes the state true. No design may make the interrupt the only trace, because
an interrupt that fails is silent by construction and reproduces the failure it was built to
prevent.

### D3 — Three states, never two

Every surface that reports attention distinguishes:

- **waiting** — a fresh attention record exists;
- **not waiting** — the store is readable and holds no record for that lane;
- **cannot tell** — the store is unreadable, absent, or on another machine.

"Cannot tell" must never render as "not waiting". This is `declare-what-a-check-assumes`, and
the fleet has now produced six instances of the collapse it names; the cheapest time to refuse
it is while writing the check.

### D4 — Surfaced where the operator already looks

At session start (the banner already carries lanes and claims), and in `poga work claims`. Not
a new place to check — a new column in the places already checked. Any optional consumer of the
feed gets the same data and can show it, on its own cycle.

### D5 — Attendance detection is deliberately not built

An earlier draft of this design routed a blocked lane's question to *the session the operator
is currently attending*, identified by evidence of human input — a `UserPromptSubmit` hook
stamping a human-presence record, distinct from the tool-use heartbeat, with a freshness
window and a not-a-dispatched-lane filter.

That is a sound design and it is not needed. Under
[ADR-0100](0100-dispatch-spawns-headless-the-operator-s-machine-attaches.md) every lane is a
window the operator opened, so "which session is the operator in" stops being load-bearing: he is in
all of them. Building attendance detection now would be machinery in service of a premise the
architecture removed.

It is recorded here rather than discarded because the premise can come back — an unattended
overnight fan-out, or a lane on a machine with no operator — and when it does, this is the
shape to reach for. What survives from it into this ADR is the distinction it was built on:
**process liveness is not human presence**, and no surface may conflate them.

### D6 — Federation first, floor after one cycle

The hook wires into the federation's own `settings_extras`, not the standard floor. The floor
binds every member including ones not yet bootstrapped, and a hook event no member has ever
run is not a thing to ship fleet-wide on first draft. Promotion follows the established
pattern — `apply-briefs` and the lane-teardown hook were both proven federation-side, then
floor-promoted.

## Alternatives Considered

- **Attendance detection now.** Deferred with its design recorded — see D5.
- **Broadcast to every session.** With two ghosts in the roster when this was
  designed, most recipients would have been nobody. Noise that trains the operator to ignore
  the channel is worse than no channel.
- **Declare the attended session by hand (`poga attend`).** An ask, forgettable, and precisely
  the class of chore [ADR-0099](0099-the-user-is-not-an-execution-surface.md) exists to stop.
- **A comms agent relaying to a channel the operator reads away from the machine.** Mechanically real
  — `tmux send-keys` was confirmed on 2026-08-22 to drive a live Claude TUI, so an outside
  process genuinely can answer a blocked lane. Deferred because it is the largest build, it
  aims at the rarest case, and it is the option most at risk of quietly becoming the
  coordinator [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) retired.
  If it is ever built it must carry words only, never decide what runs next.
- **Park instead of blocking — the lane never asks, it writes a decision note and ends.**
  Right for headless and overnight dispatches, wrong as a universal rule: it trades the
  operator's latency for the lane's whole context, and the ruling is that an operator at
  the keyboard answers questions in the moment. Retained as the unattended-dispatch
  doctrine, not adopted as the floor.

## Consequences

**A missed question surfaces late instead of never.** A lane blocked in a window scrolled past
appears in the next session's startup and in `claims`, with an age. That is the entire claim
being made — not that the operator is notified, but that the state is recoverable.

**Blocked lanes become countable, which makes a metric possible.**
[ADR-0099](0099-the-user-is-not-an-execution-surface.md) D4 filed WI-0159 for a paired
escalation/parked-queue metric whose whole value is in the pairing. A blocked-lane count with
an age distribution is the other half of that pair and now has a source.

**One more surface must learn the three-state rule.** Every renderer of attention — banner,
claims, feed, and any consumer of the feed — has to carry "cannot tell" through rather than
flattening it at the edge. This is where the rule usually gets lost.

**The federation runs a hook event no other member runs.** Deliberate, and it means a member
reading the federation's settings as a reference will see an extra. The floor-promotion brief
is downstream work, not part of this ADR.

## References

- [ADR-0100](0100-dispatch-spawns-headless-the-operator-s-machine-attaches.md) — the window the question appears in
- [ADR-0099](0099-the-user-is-not-an-execution-surface.md) — the user is not an execution surface
- [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) — the coordination store this record lives in
- [`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes) — the three-state rule
- Source conversation: Federation Architect session ~166, 2026-08-22
