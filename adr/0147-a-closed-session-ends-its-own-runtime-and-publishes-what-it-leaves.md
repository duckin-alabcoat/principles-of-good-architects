# ADR-0147: A closed session ends its own runtime, and publishes what it leaves behind

**Status:** Accepted
**Date:** 2026-09-19
**Deciders:** the operator (the direction, twice, 2026-09-18: that a closed session exits its runtime — the `poga` wrapper waits on the runtime process and, when the session's journal carries `ended:`, prints the close summary, pauses a few seconds so it can be read, and terminates the process; and that the close summary is read off a status board rather than the terminal — *"Board = where it is read, notification = that there is something to read"*; folded into one item on his direction 2026-09-19). Federation Architect (the mechanism choices below).
**Extends:** [ADR-0082](0082-lifecycle-inversion-and-per-session-runtime.md) D7 — the same `exec`-keeps-the-pid property, written down instead of handed to a watcher. ADR-0092 D2 (withheld) — POGA publishes a contract surface and any optional consumer renders it; nothing here departs from that.
**Builds on:** [ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md) (why this is in the wrapper and not in a Claude hook), [ADR-0119](0119-the-gates-three-side-doors-are-shut.md) D4 (the gate may not read the machine), [ADR-0104](0104-a-session-closes-on-the-user-s-word-never-the-agent-s-judgment.md) (unchanged — this acts *after* an authorized close, never on one)
**Supersedes nothing.** WI-0249's dispatched-lane exit is kept whole and is now one of two arms.
**Reality:** Built — the wrapper receipt, the ancestry authorization, the readable pause, the published close record, and 44 tests, demonstrated end to end on a real process tree.
**Work item:** WI-0399

## Context

### Half one — the machine handed the operator a chore it could do itself

Every closed session sat at its runtime's exit prompt until the operator closed the terminal.
The close was complete in every other sense: journal stamped, work landed, claims freed,
records released. The only thing left was a window, and the only actor able to close it
was a person.

WI-0249 had already solved this — **for a dispatched lane**. `session.py lane-exit`
knows how to end a runtime: SIGTERM, then SIGKILL after a grace, so the mechanism does
not depend on how a given runtime treats the signal. What it could not do was *name* a
runtime outside a dispatch. Its only handle, `_dispatch_runtime_pid`, finds the process
by matching `POGA_DISPATCH=<id>` in an ancestor's command line. A session a person opened
carries no such variable, so the one case that actually costs a human an action was the
one case with no handle at all.

**The handle existed the whole time and nobody wrote it down.** `poga` hands the terminal
to the agent with `exec`, which replaces the process image while **keeping the pid** —
which is why `prep_and_supervise` can start a liveness watcher on `$$` and be watching
the agent. The wrapper knows the runtime's pid at launch, on every path and every
runtime. It simply never recorded it.

### Half two — a summary printed to a window that then closes itself

The terminal was already the wrong surface for a close summary: it is visible only on the
machine that ran the session, and it is gone when the window goes. Half one makes that
sharper rather than introducing it — the window now closes *itself*.

A board that reads the coordination store can already render "this lane needs
you" from an `attention` coordination record. "This lane finished, and here is what it
left" is the same shape of question and deserves the same answer, not a second channel.

## Decision

**D1 — the wrapper records the pid it is about to become.** Immediately before every
`exec`, `poga` calls `session.py lane-launch --lane <slot> --pid $$`, writing one record
per lane slot to the shared coordination store (`lane-launch`). One record per slot,
overwritten by the next launch into it, because the question is *what is running in
poga-7 right now* and a slot holds one session at a time.

It goes in the coordination store rather than the lane's own state dir for two reasons
that both bite: on the native Claude launch path the worktree does not exist until after
the exec, so there is nowhere local to write; and the worktree is *removed* at teardown,
which is exactly when a close record is still being read.

**D2 — this lives in the wrapper, and that is a requirement, not a preference.** A
Claude-only exit path — a `SessionEnd` hook, a `Stop` hook — would have to be rebuilt for
Codex and for Antigravity, and the substrate floor is expressed as runtime-agnostic
obligations with per-runtime bindings (ADR-0041). `poga` is the one actor present at
every launch of every runtime. The record carries the runtime id so a close can *say*
what it ended; nothing branches on it.

**D3 — the authorization is ancestry, not the file.** A launch receipt is a number in a
file, and a number in a file can be stale: a slot relaunched, a wrapper that died before
its exec, a pid the OS has recycled onto something unrelated. Signalling on the strength
of the file alone means a close can kill a stranger.

So `_attended_close_runtime` requires **three** facts, and each rules out a distinct way
of killing the wrong thing:

1. a launch receipt exists for this lane slot;
2. its pid is an **ancestor of this process** — which answers *am I running inside it*,
   a question a stale file cannot fake, because a stale file names a process this session
   is not descended from;
3. the journal that just closed is the one this lane holds — `end --session-id <other>`
   closes a *sibling's* record, and a sibling's close is no reason to end this runtime.

(2) is the runtime-agnostic form of the proof the dispatched arm gets from matching
`POGA_DISPATCH` in an ancestor's command line. It needs no environment variable, so it
holds for a Codex or Antigravity lane exactly as for Claude.

**D3a — the ancestry read refuses under the gate.** It spawns `ps`, a machine-scoped
program, and under the land gate the suite is running inside somebody's real runtime: a
`True` there would identify a live session as killable, and the land's verdict would
become a function of what else is running on the operator's box (ADR-0119 D4). `False`
is already the function's answer for *cannot tell*. **This clause exists because the
completeness detector produced it, not because a reviewer thought of it** — the first
cut had no guard and `test_gate_snapshot.CompletenessTest` failed the land.

**D4 — only after a successful land, the same condition as the dispatched arm.** A lane
whose gate failed, whose CAS lost, or whose renumber refused still has real work
committed on its branch and needs a session alive to finish it. Killing it converts a
recoverable failure into stranded work with no owner. A lane that did not land stays up
and says why.

**D5 — the pause is part of the feature.** An attended session prints its close summary
to a terminal someone is looking at, and a window that closes the instant the summary
appears has communicated nothing. `lane-exit --read-pause` holds for
`CLOSE_READ_PAUSE_SECONDS` (8s) before signalling, and zero for a detached pane — holding
a pane open for a reader who does not exist is what WI-0249 removed. The pause happens
*after* the detach, so `end` returns immediately and the wait is the operator's to watch
rather than the harness's to block on.

A runtime that exits on its own during the pause is not signalled — the number may by
then belong to something else — but the pane teardown still runs, because a session that
left on its own can still leave a wrapper shell at a prompt holding a slot.

**D6 — the close publishes one record; any consumer may render it.** Every close writes a
`session-close` coordination record keyed by the same lane key `attention` uses, carrying
the session's whole `### State at close` section (`summary`), its first line
(`summary_line`), the close stamp, the items held, and a three-valued `landed`.

`landed` is three-valued on purpose: `null` means *the land has not been attempted yet*,
which is what the first write can honestly say. A boolean would have to guess, and
guessing `false` renders every pre-land close as a failure.

**Board = where it is read, notification = that there is something to read.** That split
is the design, not an implementation detail: a notification carrying the whole summary
makes the board redundant, and a board with no notification is a page nobody opens. One
record, two fields, two consumers.

**D6a — rendering belongs to the consumer.** The panel, its placement, how it relates to
what the consumer already shows, and the notification's form are the consumer's. POGA ships
the record and its contract; it does not write the consumer's code.

**D7 — the record is published BEFORE the land and BEFORE anything is signalled, and
this is the race the pairing can lose.** A record written on the way out would be lost to
a fast exit or to the SIGKILL escalation, and a board would show a session that closed
with nothing to say. So it is written the instant the journal is stamped, and rewritten
once with the land's answer. Demonstrated rather than asserted: on a real close the
process was signalled and gone, and the record was still readable afterwards with its
summary intact.

**D8 — the `session-close` kind is deliberately not in `COORD_KINDS`.** Those kinds are
**holds**, and the lane exit releases every one of them the moment a session closes. A
close record put there would be deleted by the very event it exists to record. It expires
on its own `expires_at` (24h, matching `attention`) with expiry derived on read and no
sweep anywhere — so a board polling a store nobody has written to for a day shows an
empty panel rather than yesterday's.

## Consequences

- **A closed session no longer needs a person.** The chore the operator named is gone for
  hand-opened sessions; dispatched lanes are unchanged, since WI-0249 already closed them.
- **Lanes vanish from any board faster**, and `.gone` markers become more common while
  meaning less — a supervised runtime signalled by its own close is a clean end, but a pid
  watch cannot tell. The `session-close` record is the discriminator, and the record's
  contract says so.
- **One section, one reader.** `close_summary` is what the terminal prints and what a
  board renders, so the two cannot describe different closes. It was lifted out of
  `_journal_outcome`'s hardcoded scan (P16); the scoping rule is same-or-shallower, so a
  `### State at close` carrying a `#### Caveat` keeps it instead of silently truncating.
- **Nothing in the standard section changes.** The session-end protocol governs what the
  *Architect* does, and that is untouched — the close is still authorized by the user's
  word or a dispatch (ADR-0104, ADR-0113, ADR-0125). What changes is what the harness does
  *after* an already-authorized close. Members receive it as an ordinary substrate push.
- **The `--no-merge` close does not auto-exit.** That path returns before the close arms
  and is left alone deliberately: the operator who asked to land later is the one who
  decides when that session is finished with.

## What could not be determined

Whether a SIGTERM'd Claude runs its `SessionEnd` binding and writes a `.ended` marker
before going. The signal escalation makes the *exit* independent of the answer, but the
clean-vs-crash discriminator may read as a crash for an auto-exited session. The
`session-close` record is the honest discriminator either way and is why the record's
contract names it. Worth a measurement on the first real auto-exit.
