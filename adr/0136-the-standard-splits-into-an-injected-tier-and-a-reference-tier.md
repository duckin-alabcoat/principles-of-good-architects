# ADR-0136 — The standard splits into an injected tier and a reference tier

**Status:** Accepted
**Date:** 2026-09-13
**Session:** ~330
**Item:** WI-0208
**Amends:** [ADR-0024](0024-standard-role-doc-section-is-generated-and-injected.md)
**Related:** [ADR-0052](0052-canon-budget-and-consolidation-ritual.md), [ADR-0137](0137-the-canon-budget-is-a-land-gate-not-a-report.md)

## Context

The doctrine set — `CANON.md` + `STANDARD.md`, the two files `sessionlib/coord.py`
injects whole into every member's session at start — was measured at **67,928 bytes
against a 48,000 ceiling**, over by 19,928.

The item that carries this had named the wrong lever for four weeks. It was read as a
canon-trimming problem, pointing at the 51 habit statements: the part of the set that is
hardest to cut and most costly to get wrong, because each statement is a rule every
Architect is bound by. The measurement says otherwise, and one number settles it:

```
delete 100% of CANON.md          →  45,558   UNDER ceiling by 2,442
```

The entire overage is smaller than CANON. The bytes are in `STANDARD.md`, which grew
from 31,964 to 45,558 in a fortnight.

**Subtraction could not clear the ceiling.** The shortlist prepared in lane `poga-6`
(a withheld proposal) totalled every
meaning-preserving cut available — all duplicated prose, all stale narration, all
historical rationale, both safe CANON merges — at 15,907 bytes, landing at **52,021,
still over by 4,021**. Deleting *both* protocol sections entirely, judgment steps
included, lands at 48,671 — still over by 671. The premise the item's own title shared
with every prior attempt, that the job was to find the right thing to cut, was false.

## Decision

**The standard section splits by what a session must CARRY versus what it must be able
to LOOK UP.**

1. **Rules stay injected and unchanged.** Every Accepted principle and universal habit
   keeps reaching every session at start, byte for byte. This decision trims no habit
   and weakens no canon — a rule a session cannot see is a rule it cannot follow.
2. **Lookup material moves to a delivered-not-injected reference file**,
   `STANDARD-REFERENCE.md`. *Delivered* means every member still has it on disk and can
   read it on demand. *Not injected* means it stops being paid for at every session
   start, by every Architect, forever.
3. **Protocol prose that narrates what the harness executes leaves the Standard.**
   `session.py start` and `session.py end` perform those steps and print their own
   description; the prose described them a second time. That is duplication across a
   code/prose boundary ([P16](../principles/master.md#p16--avoid-duplication)), and it
   is the cheapest bytes in the set precisely because deleting it removes no rule.

**The line, stated once so it can be applied the same way next time:**

> **Injected** — conduct you could get wrong *without knowing you needed to look it up*:
> the session protocols, what reaches the user versus what you decide, how a close is
> authorized, `W`/`C`, the relay rule (a relay arrives unbidden), checkpointing.
>
> **Reference** — material you consult at the moment you *deliberately use a named
> surface*: the stores and their commands, the roadmap shape, the `layout` block, the
> substrate table, remote invocations, the journal template, and the evidence behind a
> rule whose statement stays injected.

### Mechanism

One source, two delivered files. A `##` section in `standard-source.md` whose next line
is the marker `<!-- tier: reference -->` is emitted into `STANDARD-REFERENCE.md`;
everything else, including the preamble above the first heading, is emitted into
`STANDARD.md`. `curate/standardize.py` grows a `split_tiers()` parser and a fourth
output target; nothing else in the harness changes, because
`sessionlib/coord.py:2454-2471` injects exactly two files **by name**, so a third file is
delivered-but-never-injected with no harness change at all.

### Delivery is part of this decision, not a follow-up

The reference file enters `curate/push-substrate.py`'s `BYTE_IDENTICAL` manifest **in
the same change**, and deliberately *not* into `REFRESH_ONLY`. If it did not, this would
reproduce the exact defect it was written to avoid: `CANON.md`'s own preamble tells every
reader that the registries are the source of truth, and for nearly every member repo that
sentence names a file that is not there.

Three doors, because delivery has three:

| Surface | Change | Why |
|---|---|---|
| `curate/push-substrate.py` | manifest entry, introduced where absent | the running fleet gets it on the next `--go` |
| `bootstrap.py` | both copy plans | a freshly seeded member gets it at seed, not at the next push |
| `standard_check.py` | `standard-reference` capability, release `1.20.0` | A member **missing** it is reportable rather than silently absent |

`standard-reference` is a new release, **not** a floor addition: `required_set` walks
`RELEASES` cumulatively and the `1.0.0` floor is what makes a member *below-floor* rather
than merely *behind*. Adding the key to the floor would flip every existing member to
below-floor the instant this landed, for a file none of them can have yet. *Behind* is
the honest reading until the push runs.

## Consequences

**Measured result.** `CANON.md` 22,370 + `STANDARD.md` 22,560 = **44,930 against 48,000
— under by 3,070**, with `STANDARD-REFERENCE.md` at 18,370 bytes delivered beside it. No
principle or habit statement was touched.

**Authority is unchanged, only delivery.** The reference tier is exactly as binding as
the injected tier, and its generated banner says so. What changed is that a tmux
invocation and a journal template stop being paid for in every session of every member
forever, in order to be read once.

**The risk this takes on** is that a rule in the reference tier is a rule a session must
choose to read. That is why the line above is drawn where it is: nothing moved that fires
*unbidden*. The relay rule stays injected because a relay arrives without warning; the
work-item store moves because you only ever touch it deliberately. The injected tier's
`## Reference material` section names every reference section by subject, so a session
knows what exists and where — and is told that a missing reference file is a delivery
defect to report, never an instruction to guess.

**Sequencing, and it is not optional.** WI-0010's still-owed fleet delivery follows this
pass; it does not precede it. Every reachable member is on pre-consolidation
substrate in two generations (fleet total 732,997 bytes). Pushing before
the consolidation takes the fleet to 815,136 — worse. After it, to roughly 576,000.

**What this does not solve.** Route 1's headroom is finite, and nothing in this ADR
resists growth at the point of addition. [ADR-0137](0137-the-canon-budget-is-a-land-gate-not-a-report.md)
is the half that does; the two were decided together and neither is sufficient alone.

## Alternatives rejected

- **Trim the habit statements** (the item's own named lever, and WI-0010's prediction).
  Rejected on measurement: the whole realistic CANON shortlist — 2 merges, 1 retire,
  3 re-tiers — is 1,331 bytes, 6.7% of the overage, and the part takeable without the operator is
  250 bytes, 1.3%. It is the most expensive place to cut and the least effective.
- **Ship the registries and compress the digest lines** (Route 2, 41,442 — 2,000 better
  than this). Rejected: it touches all 51 rules for a marginal gain, and it rests on the
  behavioural assumption that an Architect will go and read the registry. This route's
  saving is structural and assumes nothing. *(Shipping the registries is still owed on
  its own terms — `push-substrate` ships `CANON.md` but not `habits/master.md` or
  `principles/master.md`, so CANON's "full statements live in the registries" names a
  file most members do not have. That is a separate defect, not a lever for this.)*
- **Raise the ceiling.** Rejected by the operator by name. The ceiling is a statement about what
  a session can afford to carry, not a scoreboard to be adjusted when it reads badly.
- **Per-file budgets.** Already rejected by name in ADR-0052:129-132 and not revisited.
