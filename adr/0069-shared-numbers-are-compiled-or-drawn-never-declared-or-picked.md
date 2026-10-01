# ADR-0069: Shared numbers are compiled or drawn, never declared or picked

**Status:** Accepted
**Date:** 2026-07-24 (session 93)
**Builds on:** [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) (journal model, derived ordinals), [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) (worktree lanes), [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) (coordination store, `adr-next`), [ADR-0068](0068-retire-the-declared-standard-version.md) (the same doctrine for `standard_version`)
**Deciders:** the operator (commissioned both fixes, 2026-07-24 rulings, via the consultant's landing brief); Federation Architect (design + build)
**Reality:** Built — session 93, suite 657 green

## Context

The first run of several concurrent lanes broke both of
the federation's shared number-spaces at once:

1. **Session ordinals.** Every lane computes its ordinal from the *lane-local* journal
   set (`frozen_max + rank over _load_journals()`), and every lane branched from the
   same base sees exactly one new journal — its own. Every lane announced
   the same **"session 90"** and every close commit said `close session 90`. The session-83
   guard (`_guard_close_ordinal`) floors the number but cannot deduplicate across
   lanes, because no lane can see its siblings' journals.

2. **ADR numbers.** Two different ADR-0067s were created by concurrent lanes. The
   coordination mechanism did not fail — **it was bypassed**: one lane drew 0067 through
   `adr-next` and held the reservation record; another created its 0067 file
   with a hand-picked number and *no reservation at all*, then landed first. Drawing
   was optional, so the discipline was the only enforcement — and one lane's lapse
   collided the namespace. A second latent defect compounded it: a reservation's
   release condition assumed "once the ADR file exists, the file scan accounts for
   it" — **false under lanes**, where the file is lane-local until land, so a lapsed
   TTL could re-issue a number whose file was committed but unlanded.

Both are instances of one failure class, the same one the operator ruled on for
`standard_version` the same day ([ADR-0068](0068-retire-the-declared-standard-version.md)):
**a value the system can compute must not be hand-carried where it can drift or
collide.**

## Decision

### D1 — The session ordinal is a compiled presentation label; a lane never asserts one

Identity is the **session-id** (already unique, already the filename). The ordinal is
assigned by the **trunk compile** — `frozen_max + rank by started` over the union of
journals actually landed (`compiled_ordinal` already computed exactly this; the defect
was lanes asserting the lane-local guess as final). Concretely:

- A lane's banners, orientation block, and `announce:` line show **`~N`** with *"lane —
  final number assigned at land"*. ~~Only a trunk session shows a bare `N`.~~
  **AMENDED by [ADR-0130](0130-a-start-time-ordinal-is-provisional-on-every-surface.md):
  that last sentence is withdrawn.** It drew the line on the wrong axis. The tilde is
  earned by *when* the number was derived, not *where* — and a trunk session cannot see
  an in-flight lane's journal either, so when that lane lands with an earlier `started`
  it is ranked ahead and the trunk's own start-time number shifts. Several journals can
  share one ordinal. A start-time ordinal is now provisional on **every** surface, with
  `START_ORDINAL_NOTE` off a lane. The rest of this D1 stands.
- A lane's derived close-commit message is **`docs(handoff): close <session-id> — <title>`**,
  never `close session N`. (Several identical `close session 90` messages in one
  history was the visible symptom.)
- The journal frontmatter `ordinal:` is **advisory** — a start-time estimate kept only
  as `_guard_close_ordinal`'s floor during the transition; nothing else may treat it as
  final.

After the stranded lanes landed, the concurrent sessions took consecutive ordinals
automatically — no hand renumbering.

### D2 — Drawing an ADR number is enforced at the land gate, not left to discipline

Layered, strongest first:

- **R5a — land gate.** Any *new* `adr/NNNN-*.md` entering the trunk must satisfy:
  NNNN is not already used on the trunk, AND (NNNN is reserved by the landing lane in
  `adr-alloc`, OR no live sibling holds NNNN). A hand-picked colliding number now
  blocks loudly at the land — the exact point the collision becomes real. Enforced on
  all three land paths (worktree-lane, candidate, legacy branch). Fail-open on git
  plumbing errors; the trunk-duplicate check is the hard invariant.
- **R5b — reservation lifetime is tied to trunk visibility, not wall-clock.** A
  TTL-expired `adr-alloc` reservation whose holder lane branch still exists is **not
  reapable** — its file may be committed-but-unlanded and invisible to every file
  scan. The TTL remains solely the orphan backstop: it frees a number once the lane
  branch is gone (or the number is already visible on the trunk's `adr/`).
- **R5c — defense in depth in the allocator.** `_adr_reserve_next` (and the
  no-coord-store fallback) includes **sibling lane tips** (`refs/heads/worktree-*` →
  `ls-tree adr/`) in the existing-max scan, so even a fully lapsed reservation with a
  committed-but-unlanded file can never be re-issued.
- **R5d — habit candidate** routed through curate: *numbers are drawn, never picked*,
  with the acceptance drill below as its conformance probe.

## Consequences

- Five lanes cut from one base now close with five distinct, non-colliding commit
  messages, and the compiled handoff numbers them consecutively at land — no
  duplicate "Session N" is constructible from the lane surface.
- A lane that hand-picks an ADR number now fails its land with the fix named
  (`session.py adr-next`, renumber, `session.py merge`) instead of landing a
  namespace collision for the next session to unwind.
- A long-running lane's ADR reservation survives TTL while the lane lives; abandoned
  lanes still free their numbers once reaped (branch deleted).
- The transition keeps `ordinal:` frontmatter and its close-floor guard; dropping
  them entirely is deferred until nothing reads them.

## Verification

- `tests/test_worktree_lane.py` — `LaneOrdinalTest`: the §4 acceptance drill (two
  lanes cut from one base, both close and land → distinct consecutive compiled
  ordinals; no ordinal claim in either close commit; lane banner shows `~N`).
- `tests/test_worktree_lane.py` — `AdrAllocationGateTest`: the R5d acceptance drill
  (hand-picked number against a sibling's reservation → land BLOCKED; trunk-duplicate
  number → BLOCKED; drawn number → lands; sibling-tip scan pushes the allocator past a
  committed-but-unlanded number; expired-reservation-with-living-lane holds, reaps
  once the branch is gone).
- Full suite 657 green.
