# ADR-0116: The trunk has two writers, and now one door — but the door is not the fix

**Status:** Proposed
**Date:** 2026-09-05
**Deciders:** the operator (set this session's goal — a two-process test proving a store write cannot steal a land's swap, with FL5 still green; ruled 2026-09-05 that a lane decides in-lane and records the reasoning). Federation Architect (the asymmetry diagnosis, the two-lock split, the decision to remove the tip re-read as inert, and the finding below that decision 1 alone does not deliver its own goal sentence).
**Builds on:** [ADR-0114](0114-the-land-gate-is-serialized-one-at-a-time.md) (the serialized land gate; this amends nothing in it), [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) (the coordination store), ADR-0073 (withheld) (store writes commit straight to the main checkout, by design)
**Bears on:** [ADR-0058](0058-land-per-session-with-an-isolated-gate.md) (the gate runs in a scratch worktree), ADR-0097 (withheld) (the nested re-gate)
**Related:** WI-0272 (observed the theft live), WI-0263 (CAS starvation), WI-0276, WI-0277, WI-0281 (the rest of WI-0293, not taken here)
**Reality:** Built — `trunk_lock` on the store commit and the land swap; 4 two-process tests, 5 mutations each caught by exactly one test, full suite 3135 green.
**Work item:** WI-0293 (decision 1 of three)

## Context

**The asymmetry, which is the whole of it.** Two things advance `refs/heads/<trunk>` in this repo and until now they played by different rules. A **land** advances it with `git update-ref <ref> <new> <old>` (`session.py`, `_land_worktree_lane`) — a compare-and-swap, which is honest and can *fail*. A **store write** — `poga work` / `poga ops` reaching `_store_autocommit` — advances it with a plain `git commit`, which takes no lock, does no CAS, and *cannot* fail.

In a race the writer that cannot fail always wins. So the land is the only one that ever loses, and it loses **after** paying for a full gate run.

**This was observed live, not theorised.** WI-0272 was filed while landing ADR-0114 itself: the gate held perfectly, `land-gate trunk <- worktree-poga-1` was in the coord dump for the whole land, no sibling land ran beside it — and the CAS lost anyway, to a `poga work` commit. The code already says so; `_cas_loser_note`'s docstring reads *"the commits a gate-holding lane loses to are mostly not lanes at all."*

**ADR-0114 bought "only one suite runs at a time." It did not buy "the trunk is stable for the duration of a land."** Those read as the same sentence and are not.

## Decision

### D1 — A second lock on the same ref, held for a ref update and nothing else.

`TRUNK_LOCK_KIND = "trunk-lock"`, one record, `O_EXCL` create-or-lose, modelled on `_land_gate_reserve`. Taken by `_store_autocommit` around its `git commit`, and by `_land_worktree_lane` around its `update-ref`. Both holds are milliseconds.

### D2 — Store writes take the trunk lock, never the land gate.

WI-0272 says why in its own text before recommending anything: putting store writes behind the land gate *"turns the cheapest verb in the system into the most expensive, and serializes a write that cannot break anything against one that can."* A `poga work` write waits for a land's **swap**, never for a land's **gate**. This is pinned by a test that is green today and must stay green — without it, a future "fix" that bought exclusion by widening the land gate would look like progress.

### D3 — Not a queue. Not `_coord_try_acquire`.

No FIFO tickets: the land gate takes dated tickets because its holds run to minutes and a silent multi-minute wait is indistinguishable from a hang; here every hold is one commit, so the fair-ordering machinery would cost more than the thing it orders. And not `_coord_try_acquire`, for ADR-0114 D3's reason plus a sharper one — a lane running `session.py wi-new` directly shares its session id **and** its journal with its own land, so reclaim-if-mine would let a process walk straight through its own hold.

Re-entrancy is a process-local depth counter, never identity, for that same reason.

### D4 — Fail open on three paths, all of them printing.

No coordination dir → unlocked, saying so. A dead holder → a **120-second** TTL, short because every legitimate hold is milliseconds. Waited past a **30-second** cap → unlocked, naming the holder. The cap is 30 seconds rather than the land gate's 90 minutes, and that difference is the point of having two locks: nothing legitimate holds this longer than a commit, so a 30-second wait already means something is wrong and the honest move is to proceed and say so rather than leave a `poga work` write hanging.

### D5 — The tip re-read is NOT taken, because it is inert.

WI-0272's candidate fix (a) is *"have the land re-read the tip immediately before the CAS rather than trusting the one it gated on (it already retries, so this only saves a re-gate)."* It was built, then removed. **Measured:** `git update-ref <ref> <new> <old>` is already an atomic CAS — a stale old-value is refused with exit 128, `cannot lock ref … but expected`. Re-reading first therefore changes no outcome, and a mutation replacing the re-read with `parent` was caught by **no test in the suite**, which is the correct verdict on dead code rather than a gap in coverage. WI-0272 says the fix "only saves a re-gate"; it does not save even that.

## What this does NOT do — the finding, stated here rather than in a footnote

**Decision 1 does not measurably reduce how often a land loses its swap to a store write, and WI-0293's goal sentence ("a land stops losing its swap to a work-item commit") should not be read as delivered.**

The land's exposure window runs from where it reads `parent` — *before* the gate — to its `update-ref` *after* it, which is the whole multi-minute suite run. This lock covers only the last microseconds of that window. A store write arriving at any point during the gate still moves the trunk and still costs that land a full re-gate, exactly as before.

What the lock actually buys is three things, all real and none of them the headline:

1. **The two trunk writers become mutually exclusive.** `_store_autocommit` commits in the **main checkout**, which is the same working tree `_sync_main_checkout` and `_recompile_main_checkout` operate on a few lines after the swap. Two processes committing in one checkout collide on `index.lock`, and that collision is *not* refused cleanly the way a CAS is.
2. **It is the primitive the next half needs.** The gate-neutral re-gate skip — the half that genuinely closes the theft — requires rebasing over the intervening commits and CAS'ing under a single hold. There is no such hold without this.
3. **A guard against the wrong fix** (D2 above), which nothing else in the suite carried.

**Why the two halves are not as separable as WI-0293 assumed.** The item puts the trunk lock in scope and the gate-neutral classification out of it, on the reading that the first is useful alone. It is not: a millisecond lock around an operation that was already atomic protects a window that was never the problem. The scoping is not *wrong* — the lock is a correct, cheap prerequisite and shipping it first is fine — but the two decisions are one mechanism, and the item's goal is met only when both have landed. **This is a finding for review with the operator, per the standing ruling that sessions record findings rather than file items.**

## Alternatives Considered

- **Store writes take the land gate.** Rejected — WI-0272's own argument, and now a test.
- **One lock for everything.** Rejected: a lock whose hold time ranges from a commit to a suite run has to be costed for its worst case, which is the option above wearing different clothes.
- **A tiny CAS on the store side instead of a lock** (WI-0272's candidate (b)). Equivalent in exclusion but strictly worse in failure mode: a store write that loses a CAS must retry, and a retry loop in the cheapest verb in the system is a new starvation surface. A lock waits once, deterministically, and fails open.
- **Widening the land's hold to cover `_sync_main_checkout` + `_recompile_main_checkout`.** Deferred, not rejected — it is where the residual `index.lock` hazard actually lives, but the recompile shells out to the member's own `session.py compile` and is seconds, not milliseconds, which would break D2's promise. Recorded as a finding.

## Consequences

- `poga work` / `poga ops` gain a bounded wait (typically microseconds; at most 30s before failing open) on a verb that previously never waited.
- `trunk-lock` is registered in `COORD_KINDS` **and** in `_coord_refresh_identity`'s per-kind TTL map. The suite's own structural guard (`test_attention.test_every_hold_kind_has_its_own_refresh_ttl`) caught the second omission — the WI-0166 failure class for the third time, exactly as ADR-0114 D5 predicted. Missing that row would have let a heartbeat re-stamp the shortest-lived record in the store (120s) with the claim default (8h), blocking every land *and* every store write in the repo until the next day.
- The land-gate rollout hold (WI-0273) still stands; this ships to the trunk only. `session.py` is byte-identical fleet-wide, so this is a fleet change whose push is a separate decision.
- WI-0293's decisions 2 (live-holder lease) and 3 (one lane-exit path) are not built. **Both are now built** — by WI-0298 at `b1f5857`, which drew no ADR of its own; they are recorded as D3 and D6 of [ADR-0119](0119-the-gates-three-side-doors-are-shut.md), which also closes the three remaining side doors this ADR's own §"What this does not do" pointed at.

## References

- [ADR-0114](0114-the-land-gate-is-serialized-one-at-a-time.md) — the land gate; D5 (fail-open set), D6 (re-entrancy), D7 (a lost CAS re-gates) are all unchanged by this.
- WI-0272 — the live observation, and both candidate fixes.
- `proposed-edits/federation-arch/pending/2026-09-05-consultant-one-writer-for-the-trunk.md` §3 D1 — the two-lock design this implements the first half of.
- `tests/test_trunk_lock.py` — the two-process race, and why it is processes rather than threads.
