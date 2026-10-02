# ADR-0114: The land gate is serialized — one land at a time, the rest queue

**Status:** Proposed
**Date:** 2026-09-04
**Deciders:** the operator (named the symptom while many concurrent lanes fought over the trunk, session ~185: the lanes were colliding on landing, and he asked whether that was burning tokens; set the scheduling — file it now, work it once the fan-out has closed). Federation Architect (the feedback-loop diagnosis, the critical-section boundary, the acquire primitive, the queue, the fail-open set, and the decision that a lost CAS still re-gates).
**Builds on:** [ADR-0058](0058-land-per-session-with-an-isolated-gate.md) (the gate runs in an isolated scratch worktree), ADR-0060 (withheld) (a lane lands by CAS on the local trunk ref), [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) (the coordination store), [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) C3 (loser rebases) and C5 (leases, and the warning against leasing broadly)
**Bears on:** ADR-0102 (withheld) (what a rebase after a lost CAS actually does), ADR-0097 (withheld) (the nested re-gate this has to survive)
**Related:** WI-0215 (running the suite during an in-progress merge destroys `MERGE_HEAD` in the live checkout — the same collision surface), WI-0150 (a land that reported exit 0 having landed nothing), WI-0261, WI-0249, WI-0166 (a coordination kind absent from `COORD_KINDS` is invisible to five surfaces)
**Reality:** Built — `land_gate_lock` wraps all three land paths; 20 tests, four mutations, and a two-process race against the real store.
**Work item:** WI-0267

## Context

**The shape of the cost.** Under many concurrent lanes, several `session.py merge` processes and several full `python3 -m unittest discover -s tests` runs contend for one machine's cores at once. A full suite takes minutes alone; contended, a land can take several times the uncontended cost of the single thing it is waiting for.

**The part that matters is that it feeds itself, and that is why "make the gate faster" is not the fix.** Every land attempt re-runs the entire suite in a scratch worktree ([ADR-0058](0058-land-per-session-with-an-isolated-gate.md) D4). N suites competing for the same cores make each one slower; a slower gate widens the window in which a sibling can CAS the trunk first; losing the CAS forces a rebase and a retry; the retry starts *another* full suite. Contention therefore **multiplies** latency rather than adding to it — the more lanes are landing, the longer each land takes, which makes collisions *more* likely rather than less. A positive feedback loop has no stable operating point. Halving the suite's runtime would move where it settles and not whether it settles.

The lanes reported this from the inside. WI-0261's lane: *"land is looping on CAS as siblings keep landing."* WI-0150's lost the race three times in a row.

**On tokens, stated plainly because the item was filed on that suspicion.** The retry loop lives inside `session.py merge`, one shell invocation, so while it spins the agent is blocked on the shell and generating nothing. The retries are close to free in tokens. The real cost is wall-clock and CPU. The second-order token cost is real but small — each failed land costs the agent a turn to re-read and re-reason about, and WI-0150 hit a worse variant where a land reported exit 0 having landed nothing, burning turns verifying against a pre-rebase SHA. Correcting the premise is part of the decision: this was fixed for latency and for the correctness surface WI-0215 names, not to save tokens.

## Decision

### D1 — One land at a time, per repo. The rest queue.

A lane takes an exclusive **land gate** record in the coordination store before landing, and holds it for the duration. A lane that cannot take it **waits and says so**, rather than starting a suite it will throw away.

### D2 — The critical section is rebase → gate → CAS, not the gate alone.

> **Superseded by [ADR-0124](0124-the-land-lock-holds-the-merge-not-the-validation.md) D1 (2026-09-10).** The argument below — that a shortened section buys nothing because lane B loses the CAS to A anyway — holds only if B *attempts* the CAS. A lane that first checks whether the trunk is still the commit it validated against writes nothing when the trunk has moved, releases at once, and re-gates outside the queue. Measured on the WI-0325 land: derive 271.5 s + gate 58.7 s inside the lock, against a merge of 0.060 s and a push of 1.7 s. The critical section is now *check the trunk, merge, publish*; **D7 below is kept in full**, relocated rather than narrowed.


Serializing only the expensive part does nothing. Lane B would rebase onto the pre-A trunk, wait its turn, gate, and then lose the CAS to A anyway — the same multiplication, one step further down. The lock has to span the whole window in which the trunk tip the lane built on must stay put. In practice it also spans the trunk sync, the view recompile and the push, because those mutate the shared main checkout and are cheap next to the gate. It is released **before** `_dispatch_after_land`, which spawns lanes and must never run under it.

All three land paths are covered — `_land_worktree_lane` (the live one), `_land_branch` and `_land_candidate`. Leaving the two legacy paths unserialized would be shipping half a guard to a fleet in which non-Claude members ([ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md)) still use them.

### D3 — The acquire is exclusive-create-or-lose, NOT the existing `lease()`.

`lease()` was the obvious reuse and would have been silently useless for exactly the population that causes the storm. `_lease_try` delegates to `_coord_try_acquire`, which reclaims a record `_coord_mine` judges as already *ours* — and `_coord_mine` matches on the ambient **journal** when the session id differs. Dispatched lanes are precisely that case: `tmux new-session` hands each child the dispatcher's environment, so siblings inherit one `CLAUDE_CODE_SESSION_ID` and one journal, both are judged "mine", and **both would take the gate**. This is not hypothetical — ten racers were once measured all drawing lane `poga-2` through the same hole, which is why `_lane_reserve` exists.

So `_land_gate_reserve` is modelled on `_lane_reserve`: `O_EXCL` create, or lose; the only reclaim permitted is of an **expired** record, and because that write is not itself exclusive it is re-read and arbitrated so only one of two racing reclaimers proceeds. The refresh is likewise strict on `session_id` and never matches on the journal — a sibling silently extending a wedged holder's TTL would hide it from the one mechanism that frees it.

A test pins the hazard itself (`test_the_lease_primitive_does_not_which_is_why_this_lock_exists`), so that if `_coord_try_acquire` is ever fixed, the reason for the extra code surfaces as a failing test rather than decaying into an unexplained duplicate.

### D4 — FIFO, and the wait is reported.

Waiters take a dated ticket. Position is enqueue time, ties broken by identity, so every lane computes the **same** order from the same records — a queue whose members disagree about who is first is a race with extra steps. Only the head of the line touches the gate; a free-for-all retry lets the lane that arrived last win repeatedly, and the lane it starves is the one whose wait is already being reported to a human.

Every 60 seconds a waiting lane prints its **position, the queue length, how long it has waited, and who holds the gate**:

```
land-gate: queued — position 2 of 5, waiting 7m; gate held by poga-4.
           One land at a time (WI-0267) — this is a queue, not a stall.
```

This is not decoration. An unexplained wait is indistinguishable from a stall on every surface we have, and this one can legitimately run to tens of minutes; a lane that says "3rd in line, 6m" is visibly working, while the same lane silent is a hang somebody kills. The holder is read off the gate record rather than taken from a failed acquire, because a lane that is *not* at the head never attempts one — and that is exactly the lane whose wait needs explaining. That gap was found by racing two real processes, not by the unit tests, all of which happened to sit at position 1 where a failed acquire hands you the holder for free.

### D5 — Three independent fail-open paths. It never fails closed.

A gate nobody can enter is worse than a gate two lanes enter: a stuck lock would strand every land in the repo, which is a far worse failure than the slow one it replaces.

1. **No coordination store** (no resolvable git common dir) → land unserialized, saying so.
2. **A holder that died holding it** → its record expires (45 min) and the next waiter reclaims it. Lane teardown and the dead-lane sweep also release it, because both new kinds are registered in `COORD_KINDS` — the tuple that drives the reaper, the dead-lane sweep, teardown, `release --kind` and the coord dump. A kind absent from it writes records nothing can ever free, which is WI-0166's bug, twice.
3. **A holder that is alive but wedged** → nothing above can detect that, so a **90-minute cap** is the backstop: the starved lane lands unserialized, says so, and points at `python3 session.py coord`. It does not steal the gate.

The cap is deliberately generous. Many lanes at a few minutes of serialized land each is on the order of an hour; a cap much below that would fire during exactly the storm the lock exists for and put every lane straight back into it.

Nothing else in `session.py` fails closed on coordination, and this is not the place to start.

### D6 — Re-entrant within one process.

`_land_worktree_lane`'s push-rejected path calls `_integrate_trunk_with_remote` (ADR-0097 (withheld)), which runs its own gate and CAS — from inside the lock. A non-reentrant exclusive-create would have that lane queue behind **itself** and sit out the full 90-minute cap, turning a successful land into an hour-and-a-half hang. Re-entrancy is keyed on a process-local counter, never on holder identity, so it cannot be confused with a sibling the way an identity check can.

### D7 — A lost CAS still re-runs the whole gate. This is deliberate; do not re-open it.

WI-0267 asked whether the re-run is redundant, since the suite passed on the same tree moments earlier and only the trunk moved. **Measured, then decided: it must re-run.**

After a lost CAS the lane rebases onto the winner's tip, so the resulting tree contains this lane's changes *plus* a sibling's — a combination **no gate run has ever tested**. That is the ordinary semantic-conflict case, and this repo has already written down what it costs to assume otherwise: a green suite proves nothing about a code path that never ran ([`verify-in-the-created-configuration`](../habits/master.md#verify-in-the-created-configuration)).

The tempting narrowing — re-gate only when the rebase brought in code — does not survive measurement either. Over the last 300 trunk commits, 25% of pairs landing within 20 minutes of each other involve a `.py` change, so three quarters of rebases fold in only docs, journals and work items. But **66 of 82 test modules read `.md` files**: this suite tests documentation invariants (canon staleness, the ADR index, the roadmap shape, store soundness), so a docs-only rebase can genuinely flip the verdict. And the gate command list is member-declared config, arbitrary by design — nothing can bound what a member's gate reads. There is no honest predicate for "this rebase cannot have changed the answer."

The cost of keeping it is small and shrinking, which is the other half of the argument: **with D1 in force a CAS is rarely lost at all**, because the only lane rebasing and CASing is the one holding the gate. The re-run stops being the common case and becomes the rare one, which is where a conservative choice belongs.

### D8 — Scope is the repo, not the machine, and the difference is deliberate.

The coordination store lives in the git **common** dir — shared by every worktree of one repo and by nothing else — so this serializes every lane of one Architect. The storm this addresses is many lanes of **one** repo, so that is the storm covered. A genuinely machine-global lock (a path under `~/.local/state`) would let one wedged lane in one Architect's repo block every other Architect on the box: trading a latency problem for an availability one, on a machine where the repos do not in fact compete at this scale. Recorded here so it reads as a decision rather than an oversight.

### D9 — A relative git-common-dir is no answer at all.

Serializing the land made the land paths write coordination records **for the first time**, and that immediately surfaced a latent hole one layer down. `_git_common_dir` asks git for an *absolute* path and then called `.resolve()` on whatever came back — which turns a relative reply into an absolute one by anchoring on the process CWD. In this repo that CWD is the checkout, so the "shared coordination store" becomes a directory **inside the working tree**. A fixture that stubs `sh` so every `rev-parse` returns a fixed short sha created `beef9999/poga-coord/` in the real repo, and a `git add -A` committed it.

A non-absolute answer is now `None`. Absence of a common dir is a case every caller already handles — coordination degrades to a no-op and the land proceeds unserialized. A **plausible wrong path** is one none of them can detect, which is the difference that makes this worth a structural guard rather than a fixture fix.

## Consequences

- **Total CPU spent on gates drops from N² to N** under a fan-out. Individual lands get *slower* in wall-clock when the queue is deep — a lane may wait half an hour — but the queue drains at a predictable rate instead of thrashing, and the wait is visible rather than mysterious.
- **WI-0215's correctness surface improves as a side effect**: two lands can no longer be mid-flight against the shared main checkout simultaneously. **Amended by [ADR-0119](0119-the-gates-three-side-doors-are-shut.md) D4:** a side effect was all this was, and it did not cover the case WI-0215 actually recorded — an operator running the suite by hand in a checkout holding a `git merge --no-commit`. `session.py test` / `poga test` now refuse while `MERGE_HEAD` exists, which closes it directly rather than narrowing the window.
- **A wedged holder now has a blast radius**: up to 45 minutes of stalled lands before its record expires, or 90 before every waiter gives up and proceeds. Both are bounded, both are reported, and `python3 session.py coord` shows the holder. This is the price of the lock and it is stated rather than hidden.
- **Not covered, named rather than left to be discovered:** `poga preflight`'s gate (`_pf_gate`) runs the full suite and lands nothing, so it is not serialized and can still contend with a land for CPU — an operator-invoked diagnostic, one at a time in practice, and serializing it would make preflight queue behind every land. `poga lanes` and the other read-only verbs never land. `session.py integrate` **does** gate and CAS, so it takes the lock at the verb.
- **The rollout to other members is not decided here.** This ships in the federation's `session.py`, which is byte-identical substrate, so it reaches members with the next substrate push — but the decision to push is a separate one.
