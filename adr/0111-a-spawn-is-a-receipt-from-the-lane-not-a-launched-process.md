# ADR-0111: A spawn is a receipt from the lane, not a launched process

**Status:** Accepted (D1(1) and D3 superseded)
**Superseded in part:** 2026-09-18 by [ADR-0145](0145-a-dispatch-brief-rides-the-orientation-not-the-argv.md) — **the witness, not the shape.** The brief no longer travels in argv at all: passing it as a bare positional is what made `poga` classify it as the operator's own prompt and withhold the session-start context, so every dispatched lane ran without its canon. D1's *first* signal is now the orientation block the lane is handed rather than the runtime's process line, and D3's runtime-process targeting goes with it. **Everything else here stands and was re-read before that change was made:** two independent signals kept apart (D1), three states never two (D2), `spawned` and `briefed` both printed because the gap is the finding (D4), per-wave confirmation (D5), the two-signal re-brief gate and its separate `send-keys` Enter (D6), the cap's asymmetry (D7), the honest non-tmux degradation (D8), and D9's warning that every mechanism here is activity-driven while its subject is a lane with no activity.
**Date:** 2026-09-04
**Deciders:** Federation Architect (the two-signal design, the runtime-process targeting, the re-brief gate). Dispatched on WI-0248.
**Builds on:** [ADR-0055](0055-lazy-session-start.md) (the lazy-start marker consumed by the first heartbeat — reused here as the *is it working* signal), [ADR-0054](0054-heartbeat-on-tool-use-and-crash-reap-grace.md) (heartbeats are driven by tool calls), [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) (the coordination store the receipt is written onto), [ADR-0082](0082-lifecycle-inversion-and-per-session-runtime.md) D4 (the runtime registry that names the launch command)
**Extends:** [`a-close-is-the-banner-not-the-sentence`](../habits/master.md#a-close-is-the-banner-not-the-sentence) from *a ritual's close* to *a spawn* — the same rule, applied to the other end of a session's life
**Reality:** Built — detector, receipt, confirm pass, re-brief, cap exclusion and the reporting surfaces all land this session. Exercised in a live dispatched lane (the detector's three states, the slot write, the lane mapping and the working signal were all read off real state, not fixtures), and pinned by 29 new tests; the suite is 2658 passing. D9 and the wave-counter fix each carry a mutation probe: reverting them makes their tests fail with the measured symptom.
**Work item:** WI-0248

## Context

Measured 2026-09-03 (session ~185, dispatch D-d9202c): a five-item retry reported
*"5 lane(s) spawned this pass, 0 still queued, state complete."* **Three of the five had
received no opening prompt at all.** They sat at a virgin prompt with the placeholder
hint, no brief, no claim. Of ten live lanes only one held a claim.

This is the worst shape a dispatch failure can take, and every check we had said fine:

- the lane is genuinely **alive**, so no liveness check fires;
- the dispatch record counts it as **spawned**, so the queue reads `complete`;
- the tmux session **exists**, so `attach` works and shows a healthy prompt.

A lane that never got its instruction is indistinguishable from one thinking hard, and it
will sit there forever — while holding a lane slot and a dispatch-id allocation record.

**The item's stated cause was wrong, and probing first is what found that.** WI-0248
nominated a race in `_dispatch_spawn_command` / `cmd_session`'s `has_operator_prompt` /
`name_the_context`, sharing the prep marker and `POGA_SESSION_CONTEXT` across five
same-second spawns. Measured in a live dispatched lane:

- A Claude lane takes poga's **native `--worktree` path** (`poga:625`), which never calls
  `prep_and_supervise` or `name_the_context`. The prep marker and session context are not
  on this path at all, so they cannot be the race.
- The brief travels in **argv** — `claude --worktree poga-4 "You have been dispatched on
  WI-0248 — …"`, read off this lane's own process line. Each spawn's brief is its own argv
  element with no state shared between spawns.
- Lane names are **drawn atomically** (`session.py lane-alloc`, WI-0185), so five
  same-second spawns cannot collide on a lane either.

What is left is the runtime's own handling of `--worktree` plus a trailing prompt. **We do
not own that component**, and the lesson of WI-0216 one session earlier is exactly this:
a failure arriving from an unexpected direction is not automatically ours to fix, and
building the fix it asks for can mean building the thing a standing decision forbids.

So this ADR does not try to make argv delivery reliable. It makes the failure **visible,
correctly accounted, and self-healing** — which is what the work item actually asked for.

## Decision

**D1 — Two independent signals, deliberately not collapsed into one.**

1. **The receipt (argv).** Immediate, and says *why*. The lane measures, at SessionStart,
   whether the runtime process it runs under actually received the brief, and writes the
   verdict onto the dispatch's own spawn slot. The lane is the only party that can see
   this; the spawner cannot.
2. **`.pending-start` persisting.** Ground truth that the lane is *not working*, and it
   needs no argv at all — so it still answers when the hook never ran to write a receipt,
   which is how the D-d9202c lanes actually looked. ADR-0055's marker is written by the
   hook and **consumed by the first heartbeat**; heartbeats ride on tool calls; therefore
   a lane nobody told to do anything keeps its marker forever. *"A phantom preload never
   beats"* was written about a different failure and describes this one exactly.

Kept separate because they fail apart. The receipt can be `unknown`; the marker can be
unreadable. Collapsing them would produce one answer with two ways of being wrong.

**D2 — Three states, never two.** `briefed` / `unbriefed` / `unknown:<reason>`, the
[`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes) floor.
*Could not tell* must not fold into either verdict: reporting a healthy lane as unbriefed
would strand it worse than the bug, and reporting an unreadable one as briefed restores
the bug. `no-receipt` — a slot that never reported within the grace — is a fourth
observation, and is **not** evidence of health.

**D3 — The detector targets the runtime process, not the ancestry, and this is
load-bearing.** *(SUPERSEDED 2026-09-18 by [ADR-0145](0145-a-dispatch-brief-rides-the-orientation-not-the-argv.md) D5. The reasoning below was correct for as long as its premise held — the brief travelled in argv, and the runtime process was the only witness that could answer. WI-0390 removed that premise deliberately: the positional brief was itself the thing suppressing canon delivery. Left in full rather than rewritten, because the trap it names — a scan that reports every lane briefed and certifies the failure it exists to detect — is the trap the replacement was audited against.)* Measured: **two** ancestors carry the brief — the runtime itself, and the
`zsh -c` wrapper `_dispatch_spawn_command` built. The wrapper's argv holds the brief
whether or not the runtime ever saw it, so a whole-chain scan reports *every* lane briefed
and **certifies the exact failure it was written to detect**. Pinned by a test whose chain
has the brief only in the wrapper.

**D4 — `spawned` keeps its meaning; `briefed` is a new number, and both are always
printed.** `spawned` gates budget and carries the WI-0156 deadlock message; redefining a
load-bearing counter to fix a reporting problem would trade one silent failure for
another. Instead every surface prints both, because **the gap between them is the
finding** — `5 spawned` alone is the sentence three unbriefed lanes hid behind.

**D5 — Confirmation is per wave, not per lane.** The pass polls all of a wave's slots
together and returns the moment they resolve, so a healthy five-lane wave costs about one
poll and only a genuinely broken one pays the full 45-second ceiling. Per-lane waiting
would have multiplied a diagnostic cost by the fan-out it exists to protect.

**D6 — Re-brief requires BOTH signals, and confirms by the lane's own artifact.** Where a
lane is not-briefed *and* not-working, the brief is typed in
(`tmux send-keys -l <text>`, then `send-keys Enter` as a **separate call** — combined, the
TUI swallows the Enter and the text sits unsent, measured session ~185). Success is
`.pending-start` being consumed — the artifact the lane's own machinery produces, never
our say-so. Not-briefed *alone* is never sufficient: injecting text into a session that is
merely thinking hard is the one thing this must not do.

**D7 — The cap excludes only a MEASURED `unbriefed`.** `pending`, `unknown` and a missing
slot all still count. Under-spawning costs a wave that drains slower; over-spawning costs
money and a machine full of sessions nobody asked for. The asymmetry picks the direction.

**D8 — Non-tmux surfaces detect but cannot re-brief, and say so.** Terminal and iTerm2
lanes get the receipt, the honest count and the cap exclusion; what they do not get is the
automatic repair. Declared in the output rather than hidden behind a uniform-looking
success line — an operator who cannot be helped must at least be told.

**D9 — The lane lookup reads EXPIRED reservations, and the bug that forces this hides in
the failure case.** The lane-alloc record carries a 5-minute TTL refreshed by heartbeats,
and heartbeats ride on tool calls — so **an unbriefed lane never refreshes it**. Five
minutes after a lost brief the reservation expires, a live-only listing stops returning
it, and the lane becomes unfindable exactly when something wants to name or repair it:
the working signal degrades from `False` (idle) to `None` (cannot tell), and the unbriefed
report falls silent about the one lane it exists for. Measured on this lane, which sat
nine hours without a heartbeat and went from resolving `poga-4` to resolving nothing. The
TTL governs whether the *slot* may be reissued, never whether the lane exists — that is
answered by the worktree and the branch, which outlive any reservation.

This is the general trap in the design and worth stating plainly: **every mechanism this
ADR leans on is driven by activity, and the subject is a lane with no activity.** Any
future check added here must be audited against that, or it will work perfectly on healthy
lanes and go blind on the broken ones.

## Consequences

- `poga dispatch --go` now takes a few seconds longer per wave, spent confirming. That is
  the price of the count meaning something.
- A dispatch record grows `briefed` and `brief_states`. Older records lack both and render
  as `briefed 0`, which is honest: nothing confirmed them.
- **Two declared limits, kept rather than papered over.** (1) The working signal reads
  `True` for any lane whose `.session-state` exists with no unconsumed marker — that is
  *no evidence of a stalled start*, not proof of activity, and it is the conservative
  direction because it withholds a re-brief rather than forcing one. (2) `no-receipt`
  cannot distinguish *the hook never ran* from *the slot was reaped*; the reporting gate
  therefore requires idle corroboration before naming a `no-receipt` lane, and only a
  measured `unbriefed` stands alone.
- The re-brief is a write into a live session's input. It is gated on two signals and a
  bounded confirmation, and it is the only place in the substrate that types into a TUI.
  If that gate is ever loosened, this is the paragraph to re-read first.

## Rejected alternatives

**Replace argv delivery with `send-keys` for every lane.** One uniform, observable path —
and it trades a rare race for a common one (typing into a TUI that may not be ready),
breaks the non-tmux surfaces, and leaves no fallback: a promptless launch *is* the failure
state, so a failed send reproduces today's bug with the safety net removed.

**Detect and report only.** Cheapest, and it ends in *"and then the operator re-sends the
prompt."* A fix whose last step is an errand for the operator has relocated the defect,
not closed it.

**Reconcile receipts lazily, when someone next reads the dispatch.** Non-blocking and
therefore attractive — but an all-unbriefed wave lands nothing, so nothing ever reads the
dispatch, and the queue sits forever. That is WI-0156's deadlock rebuilt from new parts.

**Fix the runtime's argv handling.** Not ours. See Context.
