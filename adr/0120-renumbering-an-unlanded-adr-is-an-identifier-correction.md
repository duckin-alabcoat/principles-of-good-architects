# ADR-0120: Renumbering a not-yet-landed ADR is an identifier correction, not a revision of a decision

**Status:** Accepted
**Date:** 2026-09-06
**Deciders:** Federation Architect (delegated, session ~241 — the operator delegated this class with *"use your best judgement"*; the two outcomes are indistinguishable from outside the repo, which is this session's own test for what does not reach him).
**Builds on:** [ADR-0069](0069-shared-numbers-are-compiled-or-drawn-never-declared-or-picked.md) (shared numbers are compiled or drawn, never declared or picked)
**Reality:** Not-built — this ADR settles the doctrine only. The renumberer it unblocks is WI-0290.
**Work item:** WI-0290

## Context

The ADR counter has no renumberer. `wi-renumber` and `ops-renumber` exist and are
described in the substrate as the by-hand path for moving colliding items onto freshly
drawn numbers; the ADR counter's registry entry carries no `renumberer` at all, and
`_cmd_store_renumber` deliberately prints a sentence explaining the absence rather than a
traceback.

That absence was left in place because of a genuine ambiguity, not an oversight. ADR-0069
established that a shared identifier is drawn or compiled, never picked — and its founding
incident was two ADR-0067s landing the same night. But `adr/README.md` also states that
**Accepted** ADRs are not edited in place, and that a revised decision gets a new ADR. If
changing an ADR's number counts as editing an Accepted record, a renumberer is a tool for
violating immutability, and building one would be a mistake.

So the question had to be answered before the code was worth writing: **is renumbering a
revision of a decision, or a correction of an identifier?**

## Decision

**D1. Renumbering an ADR that has not yet landed on the trunk is an identifier
correction.** It is the same act `wi-renumber` and `ops-renumber` already perform for the
other two counters, and it carries the same justification: a number drawn in a lane can
collide with a number drawn concurrently in a sibling lane, and the collision must be
resolved before the trunk sees either. Nothing about the decision the ADR records changes.

**D2. Immutability attaches at the land, not at the draft.** `adr/README.md`'s rule governs
**Accepted** records that are on the trunk — records another artifact may already cite. An
unlanded file has no citations outside its own lane by construction, because no other lane
can see it. The immutability rule and the renumberer therefore do not overlap; they are
scoped to different sides of the same boundary.

**D3. A landed ADR is never renumbered.** Where two ADRs have both landed under one number,
the remedy is the one ADR-0069 already built for — the land gate refusing an undrawn or
contested number — plus a superseding record if one is genuinely owed. This ADR does not
create a path to renumber history, and a renumberer must refuse a number that is already
on the trunk.

**D4. The renumberer rewrites self-references.** This is why the ADR counter is a different
job from the other two and not merely a missing `renumberer=` kwarg: an ADR cites its own
number in its title, its filename, and frequently its body. A rename that leaves the body
saying ADR-0067 while the file says ADR-0120 produces a record that misidentifies itself,
which is worse than the collision it fixed.

## Consequences

The renumberer becomes buildable, mirroring the existing batch renumberer with the added
self-reference rewrite from D4 and the landed-number refusal from D3.

The doctrine boundary generalizes past ADRs: for any drawn identifier, immutability is a
property of the landed record, not of the draft that will become it. That is worth stating
once here rather than re-deriving it the next time a counter gains a renumberer.

**What this does not settle.** Whether a *landed* ADR ever warrants renumbering under any
circumstance is deliberately closed by D3 rather than explored. If a case for it appears,
it needs its own record superseding this one.
