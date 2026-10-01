# ADR-0129: A lane is named by its session and addressed by its slot — the two are never the same string

**Status:** Accepted
**Date:** 2026-09-12
**Deciders:** the operator (raised it twice — on 2026-08-22, that slot names like `poga1` and `poga2` confuse because they are recycled through the day, and in the scope extension asking whether a lane's label can follow its work). Federation Architect (the three-tier rule, the join key, the decision not to convert every site at once).
**Builds on:** [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) (lanes are worktrees, slots are allocated lowest-free), [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) C1 (the durable session id), [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) (the coordination store), [ADR-0073](0073-work-item-store.md) (the claim front door anchors on main)
**Extends:** [ADR-0069](0069-shared-numbers-are-compiled-or-drawn-never-declared-or-picked.md) D1 — which already forbids showing a session ORDINAL on a lane, for the identical reason — from the ordinal to the slot.
**Reality:** Partial by design — the rule and its helper are built and pinned by tests that were mutation-checked; **six** render sites are converted (D5 lists them; the acceptance criterion named four, and the two extra came free off shared helpers); the remaining sites are named in D5 as the debt this ADR exists to make countable.
**Work item:** WI-0157

## Context

A lane slot is an **address**. `poga-N` is drawn lowest-free, freed on teardown, reissued
to the next lane, and numbered per machine. `_lane_label`'s own docstring records that the
recycling is deliberate, and that the stranded-process detector keys on a directory's
birth time rather than its name precisely because it cannot trust the name.

Every lane-facing surface nonetheless printed that address as though it were a name.
Measured: `poga-2` referred to at least three distinct sessions across two machines in one
day. A person reading `poga lanes` and a startup `stranded:` line an hour apart could not
tell whether they were looking at the same work — and `poga lanes` in particular printed
the slot, a restatement of the slot (the branch ref), and two facts about the lane's git
state. Not one field in the row said which SESSION, so there was no second fact to
disambiguate two lanes that had held the same slot.

**Two durable identifiers already existed and neither was displayed.** The durable session
id (ADR-0051 C1) is unique forever and is already how every journal is named; its own shape
carries the machine and a 4-char random tail. And the **claimed work item** is what a human
actually cares about — it is the answer to "what is that lane doing".

Three facts made this wiring rather than design:

- `_lane_label` — the project-qualified label the operator asked for in session ~127 — was **built
  and had exactly one caller**, an OSC-0 terminal title. It reached no stdout surface at all.
- `_coord_short_holder` **already** rendered `<machine>-<tail>` for the work-item STATE
  cell. One surface had the convention; the rest did not.
- The claim record already carries `machine`, `journal` and the item title, and the lane's
  own liveness sidecar already carries its durable session id. Nothing needed to be
  collected that was not already being written.

## Decision

### D1 — Three tiers, each the most durable thing actually on hand

`lane_identity(lane)` answers what a human calls a lane:

| Tier | Condition | Renders |
|---|---|---|
| 1 | a session, and it holds a claim | `devbox·WI-0157` |
| 2 | a session, nothing claimed | `devbox·d5b9` — the 4-char tail of the durable id |
| 3 | no session ever ran here | `_lane_label` — `federation lane 15` |

Tier 3 is the honest floor, not a fallback to the old behaviour: a dormant slot has **no
session to name**, and inventing one would be worse than admitting it. What tier 3 must not
do is present the raw slot as a name, and the project qualifier is ADR-0069's lesson applied
to lanes.

**The ordinal is excluded deliberately.** It is assigned at land, so two live lanes
transiently show the same one — the failure ADR-0069 D1 exists to prevent. This is not an
identifier to import.

### D2 — The slot keeps its job and loses the other one

The slot is still printed, in its own column or in brackets, **wherever the reader must type
it** — `poga resume <lane>`, `recover-lanes --lane <name>`, a worktree path, a branch ref.
What it stops doing is standing in the reading position as a name. A surface never collapses
the two into one string: that collapse is the defect.

### D3 — The join key is the journal, never the branch

`lane_claim_index()` maps durable-session-id → item id. It matches on the claim record's
`journal` field because `poga work claim` anchors on the main checkout by design (ADR-0073),
so `rec["branch"]` reads `main` for every front-door claim and a branch-keyed join silently
matches nothing. This is the WI-0130 defect that `_coord_short_holder` was built for and the
same reasoning that put a journal arm on `_coord_mine`. Verified on the live store while
building: all five held claims carried `branch: "main"` and five distinct journal ids.

A lane that has run more than one session keeps one sidecar per session, so **the last beat
wins** — the label must name the session a reader could still attach to.

### D4 — A surface that cannot resolve the identity says so, loudly

`poga lanes` is bash and must shell out to the harness for the label. When that call fails
it prints a NOTE **to stderr** and the column falls back to `poga`'s own `lane_label` —
tier 3, a project label — never to the raw slot. A silent degrade here would reproduce the
exact defect this ADR removes
([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)).

**Three limits, stated rather than left to be found.** (1) The NOTE distinguishes *"no such
verb"* from *"the harness named no lanes"*, because reporting the second as the first is a
loud WRONG diagnosis, which is not an improvement on a quiet one. (2) A PER-ROW miss — an
orphaned lane branch with a ref but no worktree, which the harness's scan never sees — gets
the tier-3 label and **no** NOTE; the label is honest, the silence is a gap. (3) The NOTE is
on stderr while the table is on stdout, so `poga lanes > file` keeps the fallback and loses
the warning. That split is deliberate — a NOTE interleaved into a table corrupts it for
every reader that pipes — but it means D4's guarantee is *"never a bare slot"*, which holds
absolutely, and *"always a visible warning"*, which holds only for a terminal.

### D4b — The session TITLE stops resting its uniqueness on the ordinal

Carried here because it is the same doctrine one field over, and because WI-0157's body
asked for it "with or ahead of the rest" (it is WI-0152's acceptance half; that item stays
open for its other half).

`structural_name`'s docstring asserted *"Unique (S<n> alone guarantees it)"* over the one
field that collides: the ordinal is DERIVED from the landed record at start (ADR-0069 D1),
so every lane cut from one base derives the same one. Measured — two sessions on one machine an
hour apart both stamped 162; six journals share 191. The title now renders `S<n>-<tail>`,
appending the durable id's tail **to the ordinal** rather than to the end of the name,
because the tail's job is to say WHICH n; they are one fact, not two. The `did` argument is
optional, so a caller without one still gets a name — glanceable but not unique, which is
the old behaviour, now admitted in the docstring instead of contradicted by it.

Nothing about how the ordinal is DERIVED changes here. That is still ADR-0069 D1's.

### D5 — Converted now; the rest is named debt

Converted: `poga lanes`, `poga work claims` / `session.py coord` (via `_coord_who`), the
startup `claims:` line, the startup `active:` / `idle:` / `stranded:` / `empty:` lines,
`recover-lanes`, and `reap-lanes` (via the shared `_idle_live_lane_note`). The **dispatch
plan needed no change** — it already renders work items and no slot at all.

Not converted, and deliberately: roughly thirty further sites render a bare `poga-N` or
`worktree-poga-N`, mostly land/end messages that name a lane by its BRANCH, where the branch
is genuinely the address being acted on. Converting them in one lane would collide with
every concurrent lane touching those files for no gain the reader can see. They adopt the
helper as they are next edited. **The launch-time echoes in `poga` are exempt by
construction**: at launch no claim and no journal exist yet, so tier 3 is the only truthful
answer there and `lane_label` already gives it.

## Consequences

- A reader holding two lane surfaces an hour apart can tell whether it is the same work,
  because both name the session rather than the slot it happens to occupy.
- `_lane_label` stops being a one-caller function and becomes tier 3 of a rule.
- One parse of the durable session id, `_durable_id_parts`, is now shared by the narrow
  STATE cell and the wide identity column. The separator still differs between them — that
  is presentation; what must not be duplicated is knowledge of how the id is built.
- A member running a `poga` newer than its `session.py` gets a loud NOTE rather than a
  quietly wrong column. The two ship byte-identical, so the window is a push, not a state.
- The work-item feed **already** carried `machine`, `journal` and the item; whatever a
  consumer of that feed displays is a rendering choice in its own repo, and this ADR gives
  it a convention to render toward.

## Verification

- `tests/test_lane_identity.py` — the three tiers; the machine read from the FRONT of the
  id so the documented `…-<person>-<random>` extension cannot rename it; a lane dict with
  no `path` naming nothing rather than the running session; one session holding two claims
  saying so; last-beat-wins across two sidecars whose filenames are adversarial to glob
  order; the journal join surviving the `branch: "main"` every front-door claim carries;
  and the acceptance drill over the converted renders.
- **The drill RUNS `poga lanes`; it does not grep the script.** Its first cut was four
  `assertIn`s over `poga`'s source text, and an adversarial pass proved what that buys:
  emptying the identity lookup, changing the awk field separator, and deleting the
  empty-map check were all green under it. It pinned strings while the conversion beneath
  it could be removed entirely. It now builds a real repo with a real lane worktree and a
  real liveness sidecar, runs the real script, and reads the real table — and a second
  case does the same for the D4 fallback with no harness present.
- Every converted line was **mutation-checked** by reverting it and confirming a test goes
  red; the ones that did not were the reason for three of the tests above.
- `tests/test_lane_recovery.py`, `tests/test_claims.py`, `tests/test_worktree_lane.py` —
  updated to the new row shapes; the three-surface consistency test still holds. Three of
  them had been keyed on the slot's whitespace POSITION (`l.split()[1]`, a `stranded:
  poga-1` prefix) and now key on its bracketed address, which is the stronger parse.
- `tests/test_poga_fleet.py` — and this one is a finding, not a rename. `poga lanes` no
  longer prints the branch ref, which left `test_federation_lanes_do_not_leak_into_a_member_view`
  asserting the absence of a string the surface can no longer emit **under any
  circumstances**: a green test certifying the property it was written to defend. It now
  asserts both spellings, the ref and the slot. A display change silently disarming a
  leak check one file away is the cost this ADR's D5 scope discipline is paying down.
