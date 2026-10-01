# ADR-0137 — The canon budget is a land gate, not a report

**Status:** Accepted
**Date:** 2026-09-13
**Session:** ~330
**Item:** WI-0208
**Amends:** [ADR-0052](0052-canon-budget-and-consolidation-ritual.md) §2
**Related:** [ADR-0136](0136-the-standard-splits-into-an-injected-tier-and-a-reference-tier.md)

## Context

[ADR-0052](0052-canon-budget-and-consolidation-ritual.md) §2 made the injected-set budget
**soft on purpose**, rejecting a hard block by name, on the reasoning that a reported
ceiling would *"create real back-pressure within a few sessions."*

That is a prediction, and it has now been measured. Over the 28.4 days to 2026-09-13:

```
standard-source.md    16 commits   +298 / -76 lines    2 net-negative
habits/master.md       4 commits   +129 /  -6 lines    0 net-negative
principles/master.md   0 commits

set 46,444 -> 67,928  (+757 bytes/day, +46.3%)
16 first-parent size steps: 14 UP, 2 DOWN
the two down-steps total 264 bytes against 21,748 bytes of growth — 1.2% of all movement
budget re-crossed 2026-08-19: THREE DAYS after the pass that cleared it
over ceiling 25 of the last 28 days
```

Fourteen up, two down. The predicted back-pressure did not happen, and the reason is
structural rather than anyone's carelessness. **There is no back-pressure anywhere**, and
this was searched rather than assumed: `standardize.py` and `distill.py` have no size
logic; `standard_check.py` has none; `.claude/settings.json` has no `PreToolUse` matcher
on `Write`/`Edit` at all; the commit hooks are `exit 0`; no test asserts a ceiling on the
real files; and **the land gate's seven commands do not include `curate/metrics.py`** —
the only code in the repo that knows the ceiling exists never runs at land time. It runs
at `SessionStart`, fail-open, one line among thirty-three, *before* anything is written.

A number nobody is stopped by is a number that only ever goes one way.

## Decision

**A change that would cross the ceiling does not land unless it carries its own
consolidation.** Not a warning, not a nightly report, not an item filed for later: the
land refuses, in the lane that wrote the change, naming the bytes it added and the ceiling
it crossed.

`curate/check_canon_budget.py --check` joins the gate command list in
`session.config.json`. It measures the injected set **after** the change and refuses on
one condition, with both halves load-bearing:

```
the set AFTER this change is OVER the ceiling   AND   this change GREW it
```

Everything else passes, and passes silently.

- **Under the ceiling — silent, whatever the change did.** the operator named this constraint
  explicitly: a guard that fires on a change made under the ceiling becomes the thing
  lanes route around and then delete. The ceiling is the subject; the diff is not. This
  also preserves what ADR-0052 §2 was actually protecting — the *first* crossing is never
  blocked, because before it the set is under.
- **Over, but this change shrank the set or did not touch it — silent.** This is the
  "unless it carries its own consolidation" clause made mechanical: a land that reduces
  the set *is* carrying its consolidation. Without this half the guard freezes the whole
  repository the moment the set is in debt — including the consolidation pass that would
  clear it, which is the one land that must always be able to get through. A land that
  never touched `CANON.md` or `STANDARD.md` did not grow the set and is not blamed for
  bytes it did not add.

**"I could not measure the set" is its own answer**, distinct from "it fits": exit 2,
`COULD NOT CHECK`, *"The injected set was not measured. This is not a pass."* — the
established three-outcome discipline of `check_citations.py` and
`check_substrate_docs.py`. The check deliberately does **not** reuse
`metrics.mine_canon_size`, which is fail-soft by design: an unreadable `CANON.md`
contributes 0 there, so a missing file would read as a very comfortable pass. It reads
the constants from `metrics.py` and does its own raising reads.

The before-side is read **only when the after-side is already over**, so an unresolvable
trunk can never block a land that fits.

## Consequences

**The author of the growth pays for it**, rather than whoever happens to open the item
months later — which is exactly what this item was, today, for the third time.

**It bites only in debt.** In the healthy state the check is one line of output and no
behaviour. It becomes a refusal only while the set is over its ceiling and someone is
making that worse.

**It runs on every land.** The command is absent from `gate-inputs.json`'s `commands`
mapping, and absent means *runs unconditionally* — fail-safe, not fail-open. That is the
right default here and costs two file reads and one `git show` per land. The nightly
`OPS-0009` derive (ADR-0124 D3) will give it a measured input set on its own schedule; it
is not this change's job to derive one, and a mid-run edit to `tests/` would burn the
whole derive anyway.

**ADR-0052 §2 is amended, not overturned.** The budget, the two-file definition of the
set, the 48,000 ceiling, the mined trend and the exceptions line all stand unchanged. What
changes is that the number now has teeth, on the narrowest condition that gives it any.

**This is the half ADR-0136 does not solve.** That decision cleared 23,000 bytes and left
3,070 of headroom — six days at the measured rate of growth, if nothing resists it. With
this gate, headroom stops being a countdown.

## Alternatives rejected

- **Leave it soft and file another consolidation item.** Rejected on the record: that is
  what happened after the last two passes, and the ceiling was re-crossed within three
  days of one of them. WI-0010 predicted this recurrence in writing and was right.
- **Refuse any land while the set is over.** Rejected — it freezes the repository on
  unrelated work and blocks the consolidation pass itself. It is also the guard shape
  most likely to be deleted by the third lane it inconveniences.
- **A `PreToolUse` hook on `Write`/`Edit`.** Rejected: it fires per-edit rather than
  per-change, so a two-step edit that nets negative would be refused mid-flight, and it
  cannot see what the land will actually contain.
- **A warning in the land banner.** Rejected by the operator by name — *"not a warning, not a
  nightly report"*. A warning is what we already had, one line among thirty-three, and
  the measurement above is what it achieved.
- **Raise the ceiling to match reality.** Rejected by the operator. Explicitly not at issue in
  either direction.
