# ADR-0113: A dispatched lane closes on its dispatch, and the exemption follows authorization rather than location

**Status:** Proposed
**Date:** 2026-09-04
**Deciders:** the operator (named the need, session ~185: many lanes did not close on their own, and those that did left their Claude session running; and ruled that lanes must close automatically from then on). Federation Architect (the location-to-authorization reframe, the resolved receipt, the fail-open inversion, and the supersession of ADR-0104 D5).
**Supersedes:** [ADR-0104](0104-a-session-closes-on-the-user-s-word-never-the-agent-s-judgment.md) **D5** — the lane exemption, which that ADR declared open on purpose and tracked as WI-0144.
**Builds on:** [ADR-0104](0104-a-session-closes-on-the-user-s-word-never-the-agent-s-judgment.md) (the `--confirm` gate and the `close-confirm` receipt, both kept), [ADR-0112](0112-a-relayed-approval-cites-a-grant-not-a-peer.md) (assertion substitution: cite a record you read, D9's receipt-is-derived), [ADR-0100](0100-dispatch-spawns-headless-the-operator-s-machine-attaches.md) / [ADR-0101](0101-a-lane-may-block-on-a-question-never-invisibly.md) (dispatched lanes and their blocking contract), [ADR-0058](0058-land-per-session-with-an-isolated-gate.md) / [ADR-0061](0061-poga-unified-lifecycle-cli-and-state-manifest.md) (the land)
**Related:** WI-0249 (the charter), WI-0144 (the open hole this closes), WI-0238, WI-0248, WI-0172, WI-0166
**Reality:** Built — the guard, the resolved receipt, the standard-section wording and the conformance detector all ship together. Parts (b) and (c) of WI-0249 shipped earlier, in session ~199.
**Work item:** WI-0249

## Context

**A dispatched lane had no sanctioned way to stop existing.** WI-0249 found both halves under many concurrent lanes: journals carrying an `ended` stamp while `tmux ls` showed their sessions still alive. Two defects, one root.

The second half — *ending the record does not end the process* — was fixed in session ~199 and is not relitigated here. A dispatched lane now terminates its own runtime after a successful land, and a closed journal releases the lane reservation that was holding dispatch capacity.

**This ADR is about the first half: the lane would not close at all.** The session-end protocol is explicit and correct — *"You never close a session on your own judgment — you ask, and the user agrees."* That is right for a session a human opened. It is wrong for a dispatched one, because **there is nobody attached to agree**. The lane finishes its item, reports, and waits forever. An instruction to work an item that can never be completed is not an instruction.

The wait is not free. A lane sitting in it holds a lane slot, a coordination record carrying `dispatch_id`, and a live tmux session, and it burns tokens whenever anything wakes it. WI-0238's cap defect is fed by exactly this: capacity consumed by lanes that finished hours ago.

### The doctrine and the enforcement already disagreed

`cmd_end` exempted `worktree-` branches from ADR-0104's `--confirm` receipt, so the guard the doctrine leaned on **was never armed in a lane at all**. Every lane could already close itself; the protocol asked it not to. The gap ran in both directions — dispatched lanes waited for an agreement the substrate would not have required of them anyway, and hand-opened lanes were free to close on the session-98 inference with nothing to stop them.

### What changed since ADR-0104 D5, which is what licenses superseding it

D5 was not an oversight. It named the hole in its own text — *"A lane can still close itself at work-package completion on exactly the session-98 inference, because the exemption is drawn on **where** `end` runs rather than on **what it is doing**"* — and then kept it, on one stated reason: *"a lane that cannot land strands its work — a considerably worse outcome than the one being prevented."*

**That reason has expired.** When D5 was written, `end` was a lane's only route to the trunk, so gating it did strand work. It is not any more: `session.py merge` commits, gates and CAS-advances the trunk and **leaves the journal open** (WI-0144's own finding — the injected standard had never named the verb). A lane refused a close today lands with `merge` and carries on. The refusal costs one question, not a strand.

So D5's line can finally be drawn where D5 itself said it belonged: on **what authorized the close**.

## Decision

**D1 — `end` refuses without `--confirm` unless the session was dispatched. The lane term leaves the condition entirely.**

The guard was `not on_lane and not confirm`. It is now `not confirm`, where `confirm` falls back to a dispatch authorization when one exists. Four cases, and the second is the behaviour change:

| | Hand-opened | Dispatched |
|---|---|---|
| **Outside a lane** | asks (unchanged, ADR-0104 D1) | exempt |
| **In a lane** | **now asks** (closes WI-0144) | exempt (closes WI-0249 (a)) |

**D2 — The dispatch is the authorization, for the close and nothing else.** A dispatch is an instruction to work an item; carrying it to completion includes closing. It authorizes no other act — it is not a grant, does not widen to canon, standard or fleet writes, and does not license a close before the item is done. What it removes is a wait that had no possible end.

**D3 — The receipt is resolved, never asserted.** `POGA_DISPATCH` is an environment variable, so it is a marker and not a control (ADR-0112 D7 says so of the same variable). The lane therefore does not write *"the environment said I was dispatched."* It reads the dispatch record and writes what it found:

```
close-confirm: dispatch D-e83efa (item WI-0249) — resolved against the dispatch record
```

This is ADR-0112's assertion substitution applied to a close. The lane cites an artifact it read itself rather than vouching for a claim it cannot check, and per ADR-0112 D9 the field is where a close's authorization already lives — so *"what authorized this?"* stays answerable from tracked git history after the ephemeral store is gone.

**D4 — It fails OPEN, and says which — deliberately inverting ADR-0112 D8.** A dispatch id that does not resolve still closes, and the receipt records it:

```
close-confirm: dispatch D-ghost0 (item WI-0249) — UNRESOLVED: no dispatch record by that id is readable from here
```

D8 made the grant resolver fail closed because an unreadable store must not authorize. The costs are not symmetric here. A wrong refusal rebuilds the exact forever-wait this ADR exists to delete — on a store that is ephemeral by design, outside `COORD_KINDS`, and expected to be missing after a reap. A wrong allow leaves a labelled row in an audited field. So it allows, and the **label is the load-bearing half**: an unresolved close is visible to the ADR-0053 miner instead of indistinguishable from a resolved one ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)).

**D5 — An explicit `--confirm` always wins.** A human's own sentence is better evidence than a record of the instruction that produced it, so the synthesized citation never overwrites a value the Architect was given. A dispatched lane that *was* told to close records what it was told.

**D6 — The field is never empty on a close that happened.** Before this, a lane's close wrote no `close-confirm` at all, which is why the conformance surface could not tell a lane's close from a missing one. Every close now carries its authorization — the user's words, or the dispatch, resolved or unresolved.

**D7 — It ships with its detector, at standard version 1.15.0.** ADR-0104 D6 set this precedent and gave the reason: `worktree-lanes` spent a whole release invisible to the conformance surface, every member reading clean on a question nothing had taught the surface to ask ([`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)). The detector requires both halves — the guard keyed on dispatch, and the citation written into `close-confirm` — because either alone is a half-state worth seeing. Scope `all`: enforcement is in the harness, so every member inherits it. **Members will read `behind` until they pick it up.** That is the detector working, not a defect to hide.

**D8 — What the detector cannot witness, stated so nobody trusts it for more.** It cannot see whether a dispatched lane actually *finished its item* before closing, and it cannot see whether a hand-opened session really asked. Both are behavioural and are mined from the record (ADR-0053), not enforced here. This is the same limit ADR-0104 D2 declared of `close-confirm` and it is not narrowed by anything above.

## Alternatives considered

**Leave D5's exemption alone and special-case only dispatched lanes.** Add the dispatch exemption without removing the lane exemption. Rejected: it fixes WI-0249 and leaves WI-0144 open, keeping a line the earlier ADR had already diagnosed as drawn in the wrong place. Two exemptions where one suffices, and the surviving one is the one known to be wrong.

**Have the operator close each finished lane by hand.** The status quo: under many concurrent lanes, the operator kills each finished pane by hand. Rejected on [`never-route-your-own-work-through-the-user`](../habits/master.md#never-route-your-own-work-through-the-user): a fan-out of N lanes that ends in N manual closes has moved the cost rather than paid it, and the errand grows with exactly the parallelism dispatch exists to buy.

**A `poga authorize` grant per dispatched lane.** ADR-0112's machinery is right there. Rejected as the wrong instrument: a grant substitutes for an approval the operator gave and could not transport, and there is no such approval here — the dispatch itself is the instruction, already durable and already scoped to one item. Minting a grant per lane would also reintroduce the per-lane cost the grant model was built to remove.

**Make the close conditional on the item being marked done.** Refuse unless the dispatched item's store record says `done`. Rejected, though it is the most attractive alternative: a lane that determines its item is *not doable*, or is superseded, must still be able to close honestly, and forcing a false `done` to escape the wait would corrupt the store to satisfy a guard. The completion question is behavioural and belongs to the miner (D8), not to the gate.

**Keep the doctrine and drop the guard.** Say a dispatched lane may close, and rely on the Architect reading it. Rejected on [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence) and on measurement: the doctrine already said the opposite of the enforcement, and the lanes obeyed the doctrine — which is how they came to wait forever. Prose that disagrees with code is how this defect was built.

## Consequences

**A dispatched lane can finish.** With (b) and (c) already landed, a lane that completes its item now closes its record, releases its slot, ends its runtime, and closes its pane — a full end of life where there was none. The three parts only work as one: closing without releasing still holds the cap, and releasing without ending still holds the process.

**A hand-opened lane costs one question it did not cost before.** This is a real regression in convenience and the price of closing WI-0144. The refusal names `merge` explicitly so a lane meeting it is not left believing its work is trapped — the fear that kept D5 alive.

**Every other member reads `behind` on a new question.** Expected under D7.

**The `close-confirm` field gains a second grammar.** It used to hold only the user's words. It now also holds a machine-resolved citation, and a miner must distinguish `dispatch D-… — resolved` from `dispatch D-… — UNRESOLVED` from free prose. That is a small parsing burden accepted in exchange for the field never being empty.

**A forged `POGA_DISPATCH` closes a session.** Anything running as the user can set it, and D3's resolution raises the cost without removing it: forging the variable alone yields an UNRESOLVED receipt, and forging a resolvable one means writing a dispatch record. This is ADR-0112 D10's honesty, unchanged — **it proves intent and time, not identity**, and it must never be cited as an access control.

**Still not verified end-to-end: the runtime kill.** WI-0249 (b) has unit coverage for pid resolution and every refusal path, but `_dispatch_close_runtime`, `cmd_lane_exit`, `_dispatch_runtime_pid` and the marker ordering have **no behavioural tests** — the SIGTERM→SIGKILL escalation, the fork/setsid detach, and the `closing_csid != live` refusal are all unexercised. The signal probe that would have measured a runtime's SIGTERM handling was refused by the sandbox, which is why the escalation exists at all. The only real proof is a dispatched lane closing itself; on 2026-09-03 the panes were killed by hand, so that test did not happen. **This session's own close is it** ([`exercise-delegated-work-end-to-end`](../habits/master.md#exercise-delegated-work-end-to-end)).

## References

- WI-0249 — the charter and its measurement; parts (b) and (c) shipped session ~199.
- WI-0144 — the open exemption, closed here from the other side.
- [ADR-0104](0104-a-session-closes-on-the-user-s-word-never-the-agent-s-judgment.md) — D1's gate and D2's receipt, both kept; **D5 superseded**; D6's ship-the-detector precedent followed.
- [ADR-0112](0112-a-relayed-approval-cites-a-grant-not-a-peer.md) — D7 (the marker-not-a-control caveat on this same variable), D8 (the fail-closed contract this deliberately inverts), D9 (the receipt is derived), D10 (proves intent, not identity).
- [ADR-0101](0101-a-lane-may-block-on-a-question-never-invisibly.md) — a lane may block on a question; a lane still waiting on one has not finished, and D2 does not authorize closing over it.
- WI-0238 / WI-0248 — the cap defects fed by lanes that never let go.
- [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence), [`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes), [`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability), [`never-route-your-own-work-through-the-user`](../habits/master.md#never-route-your-own-work-through-the-user), [`exercise-delegated-work-end-to-end`](../habits/master.md#exercise-delegated-work-end-to-end)
