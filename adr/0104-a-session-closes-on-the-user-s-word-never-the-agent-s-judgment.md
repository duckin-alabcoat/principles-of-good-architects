# ADR-0104: A session closes on the user's word, never on the agent's judgment

**Status:** Accepted
**Date:** 2026-08-30
**Deciders:** the operator (the mid-flight redirect that cut both halves of the first design — no permission dialog; the conversation is enough). Federation Architect (the guard, the receipt field, the lane exemption, the detector).
**Builds on:** [ADR-0013](0013-receipt-ritual.md) (receipts over self-report), [ADR-0020](0020-session-rituals-are-a-code-harness.md) (the mechanical half of the rituals is code), [ADR-0053](0053-mined-ritual-conformance-and-spot-audit.md) (the record is what gets mined), [ADR-0058](0058-land-per-session-with-an-isolated-gate.md) / [ADR-0061](0061-poga-unified-lifecycle-cli-and-state-manifest.md) (in a lane, `end` is the land step)
**Reality:** Built — the refusal, the `close-confirm` receipt and the v1.6.0 detector all ship in the byte-identical harness. One named consequence is open: the lane exemption (WI-0144).
**Work item:** WI-0036
**Shipped in:** `1a06acc` (guard + receipt), `81c32ec` (the operator's redirect), `779601b` (detector at standard v1.6.0), all 2026-07-28, session ~102.

## Context

**Session 98 closed itself.** It ran the full session-end ritual — handoff written, `ended`
stamped, banner printed — off the back of the operator approving a piece of work. That is a task
instruction. It is not a close signal, and nobody had asked for a close.

The harm is specific and it is not "an extra command ran". `end` writes the session's
terminal record: the `ended` stamp, the duration, the compiled handoff, the ordinal. Once
written, that record asserts a close happened at a moment the user chose. When the agent
closed on its own reading of "my task list looks finished", the record made a true-shaped
claim about a decision the user never made — and every downstream view compiled from it
inherited the claim without any way to tell.

This is the same failure class as
[`a-close-is-the-banner-not-the-sentence`](../habits/master.md#a-close-is-the-banner-not-the-sentence),
approached from the opposite side. That habit exists because a session can *report* a close
its closing code never performed. Here the closing code ran perfectly and the *trigger* was
counterfeit. Both produce a record that is wrong about the ritual, and in both cases the
record is what everyone downstream trusts.

The recurrence test is what makes this structural rather than a reminder. "Don't close unless
the user asks" was already the plain reading of the session-end protocol, and the protocol had
been in the standard section for dozens of sessions when session 98 closed itself anyway. A
rule enforced only by an agent's judgment fails precisely where the agent's judgment is the
thing at fault, which is
[`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)
([P15](../principles/master.md#p15--code-for-mechanism-not-judgment)) exactly on its stated
trigger.

## Decision

**Outside a worktree lane, `session.py end` refuses unless it is given the user's own words
agreeing to the close, and it records them.**

### D1 — The gate: `--confirm "<what the user said>"`

`cmd_end` refuses outside a lane when `--confirm` is absent or whitespace-only. The refusal
names the correct procedure rather than merely denying: ask the user in one line (*"Close out
the session?"*), run `end` only once they agree, and pass their reply verbatim. It states the
common false trigger explicitly — *finishing your task list, a brief, or the board is NOT a
close* — because that is the exact inference session 98 made.

### D2 — The receipt: a `close-confirm` frontmatter field

The value is written into the journal's frontmatter as `close-confirm`. This is the load-bearing
half, not the flag. A gate leaves no evidence; a receipt does. The
[ADR-0053](0053-mined-ritual-conformance-and-spot-audit.md) miner reads the field, so a close
resting on nothing sits visibly in the permanent record instead of resting on the Architect's
own later account of it.

**The guard is not tamper-proof and does not claim to be.** An agent can pass a fabricated
quote, and no code in this repo can detect that. What the design buys is that fabricating one
requires writing a specific false statement into an audited field — a different and much
louder act than silently exercising bad judgment, and one the miner can be pointed at. Naming
this limit is the point ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)):
a guard whose real coverage is unstated gets trusted for more than it does.

### D3 — No list of accepted phrases

The first implementation fired the close on a fixed list — *"wrap up"*, *"we're done"*, *"close
out"*, *"goodnight"*, *"let's stop"*. the operator cut it one commit later, and the argument generalises
past this feature: **a fixed trigger list is wrong the moment the user says something not on
it.** The agent then either refuses a real close, or decides for itself that an unlisted phrase
counted — which is the judgment call the guard exists to remove, smuggled back in one level
down.

So the rule is a procedure, not a vocabulary: never close on your own judgment; ask; close when
the user agrees. Any reply counts, because the field is a receipt rather than a password. A bare
`W` needs no ask — it is already the instruction
([`emergency-write-and-checkpoint-commands`](../habits/master.md#emergency-write-and-checkpoint-commands)).

### D4 — Enforcement lives in the conversation, not in a permission dialog

The first implementation also added a `PreToolUse` hook returning `ask` for `session.py end`
outside a lane. the operator cut it in the same redirect, ruling that the conversation is enough.

Two reasons it was right to go. It was a **second ask for the same act**
— the user had already been asked in the conversation, so the dialog bought no new consent. And
it moved the safety story onto the surface
[P7](../principles/master.md#p7--transparency-at-conversation-layer) explicitly calls the weak
one: a harness dialog people click through by reflex, precisely because they are frequent and
small. The removal was pinned by a test asserting `check-bash` does *not* intercept
`session.py end`, so the absence is a property under test rather than a thing that quietly
drifted back.

### D5 — In a lane, `end` is the land step and is exempt

> **SUPERSEDED 2026-09-04 by [ADR-0113](0113-a-dispatched-lane-closes-on-its-dispatch.md).**
> The hole this decision declared open is now closed, on the ground D5 itself named: the
> exemption follows **what authorized the close**, not where `end` runs. D5's reason for
> keeping the old line — that a gated lane strands its work — expired when `merge` became
> the land verb and left the session open. A hand-opened lane now asks (WI-0144 closed); a
> *dispatched* one is exempt, because nobody is attached to it to agree. The rest of this
> ADR — D1's gate, D2's receipt, D3, D4 and D6 — stands unchanged.

A worktree lane's `end` is not the session-end ritual — it is the land
([ADR-0058](0058-land-per-session-with-an-isolated-gate.md) /
[ADR-0061](0061-poga-unified-lifecycle-cli-and-state-manifest.md)), the mechanical act of merging a lane's work to the
trunk. Gating a land behind a conversational close signal would refuse the substrate's own
routine plumbing, and a lane that cannot land strands its work — a considerably worse outcome
than the one being prevented. The boundary is WI-0030's, recorded here as it was implemented.

**This exemption is a known open hole, and naming it is part of the decision.** A lane can still
close itself at work-package completion on exactly the session-98 inference, because the
exemption is drawn on *where* `end` runs rather than on *what it is doing*. Tracked as WI-0144;
the exemption stands until that is resolved, because the strand risk outranks it today.

### D6 — The detector ships in the same pass, at standard v1.6.0

`d_interactive_close_guard` requires **both** halves in a member's `session.py`: the `--confirm`
flag and the `close-confirm` receipt field. Either alone is a half-state worth seeing — a flag
without a field is a gate that leaves no evidence, a field without a flag is a receipt nothing
enforces.

Scope is `all`, not `claude-hook`: enforcement is in the harness, so a non-Claude member runs
the same `end` and reads **behind** rather than `n/a`. There is no hook half by construction
(D4).

Shipping the detector alongside the capability rather than a release later is
[`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability),
and it was applied here because the preceding rung had failed it: `worktree-lanes` spent a whole
release invisible to this very surface, with every member reading clean on a question nothing had
taught the surface to ask. Fleet effect on landing, measured: **nearly every member read `behind`**, where
the day before the same gap read as fine.

## Alternatives considered

**Leave it to doctrine.** Sharpen the session-end protocol's wording and rely on the Architect
reading it. Rejected on evidence: the doctrine already said this, and session 98 closed itself
anyway. A rule that depends on the judgment it is trying to constrain is not a rule, and
`add-structural-guard-on-recurrence` names the second violation as the moment to stop
re-stating and start enforcing.

**Trigger-phrase matching.** The first cut. Rejected by the operator mid-flight — see D3. Worth keeping
in the record because it is an attractive design that fails in a way that is invisible until the
user phrases something normally.

**A permission prompt.** The other half of the first cut. Rejected — see D4. A dialog is
consent theatre when the conversation has already asked.

**Make `end` non-destructive so a wrong close is cheap.** Rejected as a category error: `end`'s
whole job is to write the terminal record, and a close you can quietly take back is exactly the
record-integrity harm `a-close-is-the-banner-not-the-sentence` exists to prevent. WI-0100
addresses the legitimate need underneath it — post-close work having somewhere honest to live —
without weakening the close.

## Consequences

- Every member's `end` outside a lane now costs one conversational question. That is the price,
  it is paid once per session, and it is the smallest surface that actually binds.
- Journals carry `close-confirm`, so **why** a session closed is auditable after the fact rather
  than reconstructible only from the agent's narration.
- The fleet's conformance surface gained a real question and immediately answered it honestly —
  nearly every member behind. The amber was the detector working.
- **Open:** D5's lane exemption (WI-0144). A lane's `end` is unguarded by design, and the
  session-98 inference is still reachable from inside one.
- **Open by construction:** the guard cannot witness whether the Architect actually asked. It
  witnesses that a value was recorded. Behavioural conformance is mined from the record
  (ADR-0053), not enforced here.

## References

- Source brief: `proposed-edits/federation-arch/accepted/2026-07-26-consultant-unprompted-mid-session-closeouts.md` (withheld)
- Commits: `1a06acc` (guard + receipt + doctrine), `81c32ec` (the operator's redirect: no keyword list, no permission prompt), `779601b` (detector, standard v1.6.0)
- Work items: WI-0036 (this record), WI-0030 (the lane boundary), WI-0144 (the open exemption), WI-0100 (post-close work)
- Habits: [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence), [`a-close-is-the-banner-not-the-sentence`](../habits/master.md#a-close-is-the-banner-not-the-sentence), [`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability), [`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)
