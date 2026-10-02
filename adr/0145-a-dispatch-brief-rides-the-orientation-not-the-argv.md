# ADR-0145: A dispatch brief rides the orientation, not the argv

**Status:** Accepted
**Date:** 2026-09-18
**Deciders:** Federation Architect (the channel move, the joined runtime spelling, the detector's new witness). Dispatched on WI-0390.
**Supersedes:** [ADR-0111](0111-a-spawn-is-a-receipt-from-the-lane-not-a-launched-process.md) D1(1) and D3 — not the ADR, and not its shape. The two-signal design, the three states, the per-wave confirmation, the re-brief gate and the cap asymmetry all stand exactly as written. What is superseded is the *witness*: the receipt no longer measures argv, because the brief is no longer in argv.
**Builds on:** [ADR-0082](0082-lifecycle-inversion-and-per-session-runtime.md) D2 (the runtime registry and its `-r`/`--runtime` flag), D3 (the reserved argv-shape column), D5 (context delivery by splice, degrading inject → point, never → nothing)
**Reality:** Built this session — the runtime flag, the channel move, the new detector and the executed-classifier tests all land together. Measured rather than reasoned about: the classifier was lifted out of `poga` and run on the argv shapes below, and the finished work was probed by re-introducing the original defect, which fails six of the new tests.
**Work item:** WI-0390

## Context

WI-0324 asked for a reading *"at the first Codex dispatch after this lands."* That named a
path which structurally could not exist, for two independent reasons — and probing the
first turned up a third.

**Layer 1 — dispatch could not produce a Codex lane at all.** `_dispatch_spawn_command`
built its spawn line with no `-r`, no `--runtime` and no `POGA_RUNTIME_ID`, and the
dispatch argparse declared no runtime flag. Every dispatched lane fell through
`parse_runtime_flag` with an empty request and resolved `RUNTIME_DEFAULT`. A dispatched
Codex lane was not expressible.

**Layer 2 — the brief was classified as the operator's own prompt, which suppressed
delivery.** `deliver_the_context` rewrites the opening prompt only when
`POGA_PROMPT_IS_OURS=1`, which is set only when `has_operator_prompt` returns false. That
function keys on argv **position** and never on authorship: any bare token that is not a
listed value-flag's value is the operator's. Dispatch passed its brief as exactly such a
token, so the splice was skipped and the else-arm printed a stderr note the model never
sees. **Every dispatched lane in the fleet ran without its canon, its principles, its
universal habits and the standard session rituals** — while looking identical to one that
had them.

Probed, not reasoned about — the function lifted out of `poga` and run on four argv shapes:

    DELIVERED   []                                          <- bare hand launch
    DELIVERED   [--model sonnet]                            <- value flag only
    SUPPRESSED  [You are a dispatched lane. Claim WI-....]  <- what dispatch spawns
    SUPPRESSED  [run your startup and tell me who you are]  <- poga's OWN default prompt

The last line is the negative control and it is the one that proves the mechanism: poga's
own prompt, passed positionally, is classified as an operator's. This is ADR-0082 D3's
reserved argv-shape column presenting as a live defect rather than as a reserved column.

**Layer 3 — found while building the fix, and it would have re-broken it.** `-r` and
`--runtime` are absent from `POGA_VALUE_FLAGS`, and `cmd_session` calls
`has_operator_prompt` on the **raw** argv (poga:719), before `parse_runtime_flag` has
produced anything. So the separated spelling leaves the runtime's *name* sitting as a bare
token:

    SUPPRESSED  [-r codex]
    SUPPRESSED  [--runtime codex]
    DELIVERED   [--runtime=codex]

Selecting a runtime would have suppressed the canon that selecting it was for — and this
is true of a **hand** launch too, not only a dispatched one. `cmd_resume` (poga:1623) does
not have it: it passes the post-parse `args`.

## Decision

**D1 — The brief travels in the session-start orientation, and nothing travels in argv.**
`_dispatch_brief_block` renders it into the block `session.py start` already builds, which
both delivery routes carry: the SessionStart hook's `additionalContext` on the native path,
and the `--prep` session-context payload on every other runtime. The spawn line passes no
positional at all, so `has_operator_prompt` is false, `poga` appends its own opening prompt
**in the shape the runtime takes** (`argv_shape`: positional for claude-code and codex,
`-i` for antigravity) and the splice proceeds.

The launcher already owns that shape knowledge. Dispatch competing for the prompt slot
meant a second copy of it, and the copy was wrong for antigravity before it was wrong for
anyone.

**D2 — Derived, not transmitted.** Nothing new rides the spawn line. `POGA_DISPATCH` and
`POGA_DISPATCH_ITEM` are already exported there, the dispatch record and the spawn slot
already exist in the coordination store, and the brief is a pure function of them. A brief
copied into the environment or into a sidecar file would be a second copy to keep in step
with `_dispatch_prompt` and a second way to arrive stale. The item's *title* is the one
thing that is recorded rather than re-read, on the slot the spawner already writes —
because a lane reads a work-item store frozen at its own base commit, and re-reading it in
the lane would be a second, differently-stale answer to a question the spawner had already
answered.

**D3 — The runtime is selected with the JOINED spelling, and that is a decision about
`poga` as it stands, not a style preference.** `--runtime=<id>` is a single `-*` token and
cannot be mistaken for a prompt; the separated form can and does (layer 3 above). The real
repair belongs in `poga` — add the flag to `POGA_VALUE_FLAGS`, or classify the post-parse
argv as `cmd_resume` already does — and `poga` belongs to the sibling item's file set, so
this is **raised as a finding and not reached across for**. A test pins the joined form
*and* the reason, and inverts to a named skip when the finding is fixed.

The id is resolved and refused **once, at dispatch time, in front of the operator**, and
the canonical id (never an operator's alias) is what goes on the record and into the spawn
line. Refusing in each of N tabs nobody is attached to is WI-0105's litter shape.

**D4 — Absence stays its own state.** No runtime requested emits no flag at all, so the
default spawn line is byte-identical to what it was; the record stores the empty request
rather than filling in `RUNTIME_DEFAULT`. *Nobody asked* and *someone asked for the
default* must remain distinguishable, on the record and in `ps`.

**D5 — The receipt's witness moves with the channel; its shape does not.** ADR-0111 D3
targeted the runtime process's argv, and that was the right witness while the brief *was*
the runtime's trailing argument. It is not any more — and a detector still asking the old
question would answer `unbriefed` for every correctly briefed lane in the fleet **and send
the re-brief path to type a duplicate brief into each one.** A guard that fires on correct
code gets deleted; one that fires on correct code *and acts on it* does damage first.

The witness is now the orientation block itself. `session.py start` builds it once and uses
it twice: the receipt is written *about* that string, and that same string is appended to
what the lane is handed — so the receipt is a claim about the delivery that happened, not
an inference from a process tree. The three states survive unchanged: a resolvable brief is
`briefed`, a brief that resolves to nothing is a **measured** `unbriefed`, and a store that
cannot be read is `unknown`.

**D6 — The re-brief keeps typing `_dispatch_prompt` verbatim.** The recovery path is a
different channel by design — it types into a live TUI as a user turn, which is the
argv-equivalent — and it reaches a lane whose session start has already happened, so an
orientation it will never rebuild is no use to it. Both routes render the same function, so
they cannot drift apart.

## Consequences

- A dispatched lane now reads its brief *after* the orientation and *before* the canon
  digest, which is the order it needs: orient, read the assignment, read the set it must
  apply to it. The brief says in one line that it outranks the generic opening prompt the
  lane was launched with, because both are now present.
- `poga dispatch -r cx 1-10` is expressible, and the plan — the one approval surface —
  names the runtime and its guard posture. "10 Claude lanes" and "10 Codex lanes" are
  materially different things to agree to, and only `claude-code` declares `native` guards
  (ADR-0082 D6).
- **A hand launch is untouched in every respect**, and this is pinned by an executed
  negative control rather than by reading source: the operator's own prompt is still classified as
  his, `poga --model sonnet` still gets its context, and a session with no dispatch in its
  environment gains not a heading, not a line, not a trailing blank.
- Dispatches whose lanes were spawned before this ships carry receipts measured by the old
  witness. They are not re-measured and are not wrong — they describe a delivery that
  genuinely did happen by argv.
- **The layer-3 finding is still open in `poga`.** Until it is fixed, `poga -r codex 'do
  the thing'` and `poga -r codex` both launch a lane whose canon was never delivered, by a
  route that has nothing to do with dispatch. That is a hand-launch defect and it is named
  here so the next reader does not mistake this ADR for having closed it.

## Rejected alternatives

**Change `has_operator_prompt` to key on authorship.** The correct long-term fix and the
smallest-looking diff — and it is an edit to `poga`, which this item's brief assigns to the
sibling lane minted alongside it. Reaching across would have put two lanes in the same file
on the same afternoon for a change neither could see the other making. Raised as a finding
instead, which is what the brief asked for.

**Pass the brief in the environment (`POGA_DISPATCH_BRIEF=…`).** Works, needs no derivation,
and survives a runtime with no orientation channel at all. Rejected because it is a second
copy of a string `_dispatch_prompt` already owns, it puts ~2KB of prose in every `ps` line
in the fleet, and the records it would be copied from are already reachable from the lane.

**Keep the positional brief and teach `deliver_the_context` to splice into it anyway.**
Delivers both, changes no channel — and it means `poga` amending a prompt it cannot prove
it wrote. The classifier's whole job is that distinction; defeating it for the one caller
that happens to be us defeats it for the operator too.

**Leave the detector on argv and accept `unbriefed` everywhere.** The cheapest possible
diff, and it ends with a fan-out of fifteen lanes each having a duplicate brief typed into
it by the confirm pass. A detector that has stopped measuring the thing it is named for is
worse than no detector: it still acts.
