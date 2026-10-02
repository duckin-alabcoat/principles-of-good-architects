# ADR-0130: A start-time ordinal is provisional on every surface — the trunk is a concurrent writer too

**Status:** Accepted
**Date:** 2026-09-12
**Deciders:** Federation Architect (dispatched lane, WI-0152 — the item's acceptance names this outcome directly: *"a start-time ordinal renders as provisional off a lane as well as on one"*).
**Amends:** [ADR-0069](0069-shared-numbers-are-compiled-or-drawn-never-declared-or-picked.md) D1, first bullet — *"Only a trunk session shows a bare `N`."* That sentence is withdrawn. Everything else in D1 stands unchanged, including the compile being the assigning authority, the lane close-commit shape, and the frontmatter field's advisory status.
**Builds on:** [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) C1 (the durable session id is the identity), [ADR-0129](0129-a-lane-is-named-by-its-session-and-addressed-by-its-slot.md) D4b (the session title stops resting its uniqueness on the ordinal — WI-0152's other half)
**Reality:** Built — `_display_ordinal` inverted and pinned; five existing call sites converted and two surfaces that bypassed the renderer entirely brought into it; 7 tests, each verified red against the code it guards.
**Work item:** WI-0152

## Context

ADR-0069 D1 made the session ordinal a **compiled presentation label**: identity is the
session-id, and the number is assigned by the trunk compile — `frozen_max + rank by
started` over the union of journals actually landed. It then drew the display rule on the
wrong axis:

> A lane's banners, orientation block, and `announce:` line show **`~N`** with *"lane —
> final number assigned at land"*. Only a trunk session shows a bare `N`.

`_display_ordinal` implemented exactly that, in one line:

```python
return f"~{n}" if _on_worktree_lane() else str(n)
```

**Nothing in the reasoning behind the tilde is about worktrees.** The number is an
estimate because it was derived *before the compile had seen the final set of journals*.
That is a fact about **when** it was derived, not **where**. `_on_worktree_lane()` was
standing in for the question *"am I a concurrent writer?"* — and it answers **false for a
concurrent writer**.

**Why the trunk is not exempt.** `_load_journals` orders ascending by `started`, and
`render_handoff` assigns `frozen_max + 1 + rank` over that order. An in-flight lane's
journal lives on the lane's own branch, so a session on the trunk **cannot see it**. When
that lane lands, a journal with an *earlier* `started` is inserted **ahead**, and every
number behind it shifts up. A trunk session's start-time ordinal is therefore an estimate
by precisely the mechanism a lane's is. At the moment this was written the machine had
**eight** live lanes; the off-lane case is not a corner.

**Measured, not argued.** WI-0152 observed two sessions on one machine an hour apart both
stamped `ordinal: 162`. The item's later sweep found **six journals sharing 191, six
sharing 174, and six sharing 129**. A bare `N` at start was asserting those away, on the
one field a human navigates the record by.

**Two surfaces never reached the rule at all.** Independently of the axis question,
`_stamp_start` — the session-start ritual for a **non-Claude runtime** — built both its
`session:` line and its start banner from the raw integer, bypassing `_display_ordinal`.
So an external runtime printed a bare `N` **even on a lane**, where the pre-existing rule
already forbade it. `_emit_already_open` (the idempotent re-run block) did the same, and
its fallback value is the frontmatter's own admittedly-advisory estimate.

## Decision

### D1 — Provisionality is a property of the moment, not the surface

A displayed ordinal is rendered `~N` unless the caller can show the number is **settled**
— that the trunk compile has already run over the landed union and produced it. Lane-ness
does not enter the question and `_display_ordinal` no longer consults it.

### D2 — The default is provisional; `settled=True` is opt-in and narrow

What sits on the other side of this guard is a false claim of uniqueness on a human-facing
identifier, so a caller who forgets the flag must land on the honest render rather than
the asserting one. This is [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)'s
direction rule: the safe direction is set by what the guard protects, not by a blanket
preference.

Exactly one caller passes `settled=True` — the **off-lane close banner**, where the
compile has run and the number is the one going into `docs(handoff): close session N`.
A lane never passes it, and still never asserts an ordinal in a close-commit message at
all (ADR-0069 D1, unchanged).

### D3 — An unknown ordinal passes through undecorated

`compiled_ordinal` returns `None` when no journal ranks, and two callers render that as
the literal `"?"`. `~?` is worse than either honest form: it decorates a non-answer as
though it were an estimate *of something*. `?` already says the only true thing.

### D4 — What deliberately keeps a bare number, and why

- **The compiled handoff** — the `## … Session N — …` heading and the SESSION LOG row.
  This *is* the settling event; a tilde here would mean the record never settles.
- **The journal frontmatter `ordinal:`** stays a bare integer. It is a machine field, not
  a display: `_guard_close_ordinal` reads it with `int()` as the close floor, and
  `find_open_journal` matches on it. Its advisory status is ADR-0069 D1's and is
  documented at the write site. Tilde-ing the value would break three readers; renaming
  the key would invalidate the close floor for every journal already on disk. The
  ambiguity a human reading the raw file might take from it is real but small, and the
  cost of the two available fixes is not.
- **`structural_name`** (the picker title) keeps `S<n>-<tail>`. WI-0157 / ADR-0129 D4b
  already solved the same problem there by a better means: the durable tail makes two
  same-ordinal sessions **distinguishable**, which is what the ambiguity actually cost.
  A tilde would add noise without adding information.

## Alternatives considered

- **Leave the axis and widen the note.** Keep the bare `N` off a lane, but say somewhere
  that it may move. Rejected: the defect is the assertion, not the absence of a footnote,
  and a disclosure a reader has to go find is not one.
- **Make the trunk ordinal genuinely settled at start** by having the trunk read in-flight
  lane journals off their branches before numbering. Rejected: it re-creates the allocator
  ADR-0069 deliberately retired, it cannot see a lane that has not started yet, and it
  makes every start pay a git read per live lane to buy a label.
- **Tilde the frontmatter value too.** Rejected in D4 on cost: three readers break and the
  close floor is invalidated for every existing journal, to fix an ambiguity in a field
  already documented as advisory.
- **Give the ordinal a `settled` state field** written when the compile assigns it.
  Rejected as the wrong shape for the same reason ADR-0069 gave: the number is derived, so
  a stored flag about it is a second thing to keep true. The call site knows whether the
  compile has run; that is where the answer belongs.

## Consequences

- Every start-time surface now shows `~N` with a note naming what it waits on —
  `LANE_ORDINAL_NOTE` on a lane (*"final number assigned at land"*), `START_ORDINAL_NOTE`
  off one (*"provisional — final number assigned by the trunk compile"*). Both say the
  same thing about the same number; the events differ, the fact does not.
- The trunk's start banner is one glyph and one parenthetical longer. That is the cost of
  the banner no longer claiming something it cannot know.
- A non-Claude runtime's stamp output gains the marking it should always have had.
- ADR-0069 D1's first bullet is withdrawn, not reinterpreted. A reader who arrives at D1
  from the index must be sent here, so the index row and D1 itself both carry the pointer.
