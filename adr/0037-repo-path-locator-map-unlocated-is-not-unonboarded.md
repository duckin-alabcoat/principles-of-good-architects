# ADR-0037: A repo-path locator map for reconcile; UNLOCATED is not "not onboarded"

**Status:** Accepted
**Date:** 2026-07-06
**Deciders:** the operator (directed the fix, session 47); Federation Architect (drafted + built, session 49)

## Context

[`curate/reconcile.py`](../curate/reconcile.py) ([ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md) upstream half) locates each managed system's live `STATUS.md` by **walking** a set of machine-local search roots (`reconcile-roots.local`, P3) and matching by the STATUS `id` field. A mirror with no located STATUS was labelled **UNRECONCILED**.

That label was ambiguous, and the ambiguity bit three times (federation sessions 35, 37, and — most expensively — 47). In the motivating case a member's live repo lived *outside* the walked roots, and a **dead old clone** of it (far behind its origin, no STATUS.md) was itself a walk root. So walk found nothing for that member's id, reported it UNRECONCILED, and the Architect read that as **"never onboarded"** — and in session 47 wrote a full onboarding plan for an already-converged system with real session history. An outside consultant caught it by checking the live repo. This is a [P18](../principles/master.md#p18--verify-everything) failure on the federation's own records, and the third instance is the trigger for [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence) / [P15](../principles/master.md#p15--code-for-mechanism-not-judgment): a promise won't do, code will.

## Decision

**Add a machine-local locator map, read the mapped path directly, and reframe the "no STATUS found" outcome so it can never be misread as "not onboarded."** Concretely, in `reconcile.py` + `curate/common.py`:

1. **`repo-paths.local`** (gitignored, P3; tracked template `repo-paths.example`): a `<system-id> = <absolute repo path>` map, split on the first `=` so paths with spaces work. Only systems whose repo is not under a walk root need an entry.
2. **The mapped path is read directly and wins** over a walked hit for the same id — the map is the authoritative locator when present. A system outside every walk root now reconciles.
3. **Reframed outcomes.** A mirror with no STATUS located by walk *or* map is **UNLOCATED** — "not reachable from this machine; add it to `repo-paths.local`" — explicitly **not** "not onboarded." A map entry that points somewhere without a valid STATUS.md (missing dir, no STATUS, or STATUS `id` disagreeing with the key) is **BROKEN MAP** — "fix the map, not the system."
4. **Stale-clone tell.** Any located checkout is compared to its own already-known `origin` (read-only, no fetch): far-behind or diverged is flagged as a possible **STALE-CLONE** — the signal that would have named the dead clone a dead copy on sight.

Federation-only tooling (like the rest of `curate/`); not shipped to fresh Architects. Covered by `tests/test_reconcile.py`.

## Alternatives Considered

- **Just add the off-root path as a walk root.** Rejected: it fixes that one member on one machine but leaves the *semantics* broken — the next off-root system repeats the misread, and the dead-clone walk root would still shadow the live repo. The defect is the word "UNRECONCILED meaning ambiguous," not one missing path.
- **Auto-discover repos across the whole disk.** Rejected: slow, and re-introduces exactly the dead-clone hazard (it would find the dead clone too). An explicit map is the P3-clean, unambiguous locator.
- **A tracked (committed) map.** Rejected: machine-specific filesystem paths must stay out of tracked files (P3), like `reconcile-roots.local`.

## Consequences

- UNLOCATED now means only "not reachable from here." The federation cannot again conflate an unreachable live system with an un-onboarded one from this signal.
- The off-root member reconciles via the map and correctly shows drift (mirror stale vs live) rather than vanishing as UNRECONCILED — surfacing the mirror-refresh work instead of hiding it.
- The stale-clone tell gives the dead clone a name the moment it is ever located, backstopping its pending retirement.
- One more machine-local config to seed per machine; absent, reconcile degrades to walk-only (its prior behavior), never errors.

## References

- [ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md) — delivery + the reconcile upstream half.
- Session 47 (2026-07-05) — the mis-diagnosis of an already-onboarded member; the operator's directive to build the structural guard. Session 49 (2026-07-06) — built.
- Memory: `feedback-check-records-and-live-repo`.
