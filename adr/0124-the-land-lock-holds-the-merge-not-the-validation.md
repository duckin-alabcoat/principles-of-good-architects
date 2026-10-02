# ADR-0124: The land lock holds the merge, not the validation — and the derive leaves the land path

**Status:** Proposed
**Date:** 2026-09-10
**Deciders:** the operator (the ruling, in session with the consultant, replacing a wait for the weekly review: the lock holds only merge and push; each lane validates before it queues; the derive runs nightly as an obligation or runner and staleness warns rather than refuses; a finish-line test goes red whenever any land holds the lock over 30 seconds, and lands still complete). Federation Architect (the parent-equals-trunk check as the thing that makes it safe, the retirement of ADR-0122 D1's reuse proof as moot rather than wrong, the decision that a stale record disqualifies the skip rather than the land, the land receipt, and the two refusals named at the end).
**Supersedes:** [ADR-0114](0114-the-land-gate-is-serialized-one-at-a-time.md) **D2 only** — what the serialized section contains. D1, D3, D4, D5, D6, D7, D8 and D9 stand unchanged, and D7 in particular is kept in full rather than narrowed.
**Amends:** [ADR-0117](0117-a-land-classifies-its-own-diff-before-it-gates.md) (the gate-input record's source is a nightly obligation, not the land; staleness warns and disqualifies the skip instead of refusing); [ADR-0122](0122-validation-reuse-and-resumable-completion.md) D1 (the validation-reuse proof is retired as moot — there is no second suite run left to elide)
**Builds on:** [ADR-0058](0058-land-per-session-with-an-isolated-gate.md) (the gate runs on a candidate in a scratch worktree), ADR-0060 (withheld) (a lane lands by CAS on the local trunk ref), [ADR-0119](0119-the-gates-three-side-doors-are-shut.md) D2/D3/D5 (number release at the CAS, the live-holder lease, trunk-only views)
**Bears on:** [ADR-0116](0116-the-trunk-has-two-writers-and-now-one-door.md) (the trunk lock still covers the ref write), ADR-0097 (withheld) (the nested re-gate, named below as the one path that can still blow the budget)
**Reality:** Built — all three landers restructured onto one serialized-advance helper; land receipt + FL7 + scoreboard row; 22 new tests, and the WI-0310 freshness tests moved to warn semantics.
**Work item:** WI-0329; amended by WI-0357 (D1 — why the view recompile stays in the section, and what actually made it expensive)

## Context

**The measurement, not the impression.** On 2026-09-10 the WI-0325 land (dispatch D-069e3d) recorded its own stages for the first time:

| stage | seconds | inside the one-at-a-time land lock? |
|---|---|---|
| serial derive (`curate/gate_inputs.py --derive`) | 271.5 | yes |
| remaining gate checks (sharded suite + three `--check`s) | 58.7 | yes |
| merge (`git update-ref`, the compare-and-swap) | 0.060 | yes |
| push | 1.7 | yes |

So **99.5% of the hold was work that never touched the trunk.** Five lanes queued behind one another that day took about an hour to land roughly ten minutes of work, and four of the commits that reached main in a thirty-minute window were `chore(gate): derive suite input record` — the derive publishing its own output, once per land.

**Why this is not a criticism of ADR-0114.** That ADR fixed a real and worse problem, and its diagnosis was right: lands were thrashing, each losing its compare-and-swap to the last and starting another suite, and contention *multiplied* latency rather than adding to it. Serializing removed the feedback loop. Its D2 then made the deliberate choice to put the whole `rebase → gate → CAS` window under the lock, on the argument that serializing only the expensive part buys nothing — *"Lane B would rebase onto the pre-A trunk, wait its turn, gate, and then lose the CAS to A anyway."*

**That argument has exactly one hole, and closing it is this ADR.** Lane B loses the CAS to A only if it *attempts* the CAS. If, on taking the lock, B first checks whether the trunk is still the commit it validated against, then a moved trunk costs it nothing at all: it writes nothing, releases the lock immediately, and goes back to rebase and re-gate **outside the queue**, where the lane behind it is not paying for its suite. What D2 treated as an unavoidable consequence of shortening the section is avoided by one comparison.

**And that comparison buys a second property, which is the one that actually matters for safety.** Because the advance is refused unless the trunk is still exactly `parent`, the tree that lands is byte-for-byte the tree the gate ran on. Under D2 that was true too, but it was true *because nothing else could move the trunk while the lock was held*. Now it is true because it is checked. Checked is stronger: it survives every future change to what else can write the trunk.

**the operator's ruling, 2026-09-10**, made in session rather than waiting for the weekly findings review: (1) the land lock holds only merge and push, each lane validates its candidate before it queues, and inside the lock it checks whether the trunk moved — if not, merge and push; if it did, leave the lock, rebase, re-validate outside, queue again, bounded attempts. (2) The serial derive leaves the land path and runs nightly on main as an obligation or runner; a stale record warns and never refuses. (3) A finish-line test goes red whenever any land holds the lock longer than 30 seconds, and lands still complete.

## Decision

### D1 — The serialized section is: check the trunk, merge, publish. Nothing else.

One helper, `_serialized_advance(trunk, parent, tip, publish)`, is the entire critical section, and all three landers (`_land_worktree_lane`, `_land_candidate`, `_land_branch`) reach the trunk only through it. Inside the held gate, in order:

1. Re-read `refs/heads/<trunk>`. If it is not exactly `parent` — the commit this candidate was validated against — **write nothing**, release, and return `moved`.
2. Compare-and-swap the trunk ref forward to `tip`, under the trunk lock ([ADR-0116](0116-the-trunk-has-two-writers-and-now-one-door.md), unchanged: it is there for `index.lock` mutual exclusion with the store's own writer, not to make the CAS atomic — `update-ref` already is).
3. Publish: release the numbers the diff carries ([ADR-0119](0119-the-gates-three-side-doors-are-shut.md) D2), sync the main checkout, recompile the trunk's views, push.

Everything else — the fetch, the trunk-view restore, the rebase, the renumber, the counter gate, and the whole gate — runs **outside**, once per attempt, in the lane.

**On step 3, because the ruling says "merge and push" and this is four things.** Each of those four mutates state every lane shares: the reservation store, the main checkout's working tree and index, the trunk's generated views, and origin. Two lands doing them concurrently collide on the main checkout's `index.lock`, which is a collision no compare-and-swap refuses cleanly (WI-0293). They are the publication of the merge, not a second validation, and that is the distinction the ruling is drawing. They are also *timed separately into the receipt*, so if one of them ever becomes the expensive thing, the evidence says which — rather than leaving the next reader to re-measure what this one already knew. The alternative — publishing outside the lock under a second, narrower lock — was rejected: it invents a new concurrency design to satisfy the ruling's wording while defeating its purpose, and the 30-second budget polices the outcome either way.

**The view recompile stays in, and the thing that got expensive was not its scope (WI-0357).** That item read step 3 as scoping the section to "merge and publish" with the recompile outside both, and proposed either moving it out or recording why it cannot move. It is inside deliberately — it is the third of the four publication steps named above — so this is the record, with the measurement it asked for.

*The diagnosis the item reached, and why it is wrong.* WI-0357 compared a 30.1-second recompile on a loaded machine against a 2.9-second one on a quiet one, concluded the stage *"has been inside the lock all along, costs about 3 seconds there"*, and wrote that **"a fix that hunts for what changed today will find nothing."** Hunting finds it in one query. Split this repo's own land receipts at `3c0fd1ac` — the commit that wired the finish-line board into the generated region under `## Now` (WI-0285), landed 2026-09-13, a few hours before the loaded land:

| | recompiles that did work | median | max |
|---|---|---|---|
| before `3c0fd1ac` | 12 | **0.552 s** | 2.010 s |
| after `3c0fd1ac` | 24 | **3.050 s** | 30.109 s |

A 5.5x step in the median at that commit. Both of the item's samples were taken *after* it, which is why comparing them showed only load. The recompile had indeed been inside the lock all along; what it was a recompile *of* changed that day.

*What the cost actually is.* `_recompile_main_checkout` spawns the trunk's own `session.py compile`, which runs `cmd_wi_render`, whose `## Now` producer spawns `curate/finish_line.py --status` — a walk over every fleet member's git. Measured under heavy load: 7.9 s / 10.4 s / 22.7 s over five runs. Its cap was `FINISH_LINE_TIMEOUT_S = 30`, **exactly the hold budget**, so one region of one generated view was permitted to spend the entire allowance of the section before the land had merged anything. The land was running the scoreboard that contains FL7 from inside the lock FL7 measures. The other four regions are ~0.06 s combined and are derived from the tree being committed; this one is not.

*The fix is to the content, not the scope.* The cap is now derived — a third of the budget — and a board that does not answer inside it returns `None`, which the renderer honours by keeping the verdict the region already carries. That is sound here and nowhere else in `run_compile`: this region's content is a cached fleet observation, already stale by one land when written, whereas every other region is a fact about the commit being made. Keeping the last verdict also removes a flap the old placeholder would have authored — one `docs(roadmap)` commit per land on a busy machine, as placeholder and verdict took turns.

*Why the stage does not leave the section anyway.* It writes the main checkout's working tree and index, and so does `_sync_main_checkout` beside it. Moving one out without the other does not remove a collision, it creates one the single lock prevents today: a lane recompiling outside while a sibling syncs inside. The coherent version is to move **both** under a named main-checkout mutex taken outside the land gate — which is the "publish outside the lock under a narrower lock" alternative rejected below, and this measurement does not overturn that rejection. Over 89 landed receipts the recompile is 17.1% of all hold time and 1 of the 4 budget breaches; `integrate` is 51.4% from three events and the other 3 breaches. The expensive path is the one already named as a finding at the end of this ADR, not this one. **Deferred, with the number that would reopen it:** when recompile plus sync exceed integrate's share of hold in a 14-day window, or when any land breaches the budget on the recompile alone twice in one window, the mutex is worth its risk.

**A `moved` trunk is not a lost CAS, and the land says so differently.** Before, a trunk that moved was discovered by a swap git refused, so "lost the swap" was literally true. Now the move is caught before anything is written and the swap is never attempted. Reporting it as a refused swap would spend the reader's attention on an anomaly — a CAS refused under the lock, with the tip verified a line earlier, which nothing in this design produces — when what happened was routine.

**[ADR-0114](0114-the-land-gate-is-serialized-one-at-a-time.md) D7 is kept in full, and the reason it is worth saying so is that this ADR looks like the place someone would finally narrow it.** After a rebase onto whatever landed first, the tree contains this lane's changes *plus* a sibling's — a combination no gate run has tested — and D7's refusal to find a predicate for "this rebase cannot have changed the answer" still holds, measurement and all. Every re-validation this ADR causes is a full one. What changes is only *where* it is paid: outside the queue, by the lane that caused it, instead of by everyone behind it.

**The retry budget is unchanged** (`_CasBudget`, WI-0266) and now covers a cheaper event, so it is if anything more generous than before. A lane that exhausts it still parks with its work committed on its branch and reports `contended`, exit 2.

### D2 — A stale gate-input record WARNS, and the warning disqualifies the skip.

`_gate_record_freshness` returns a warning instead of a refusal, and `_gate_unless_neutral` treats a stale record as **no usable measurement**: nothing is classified neutral and the gate runs in full.

**This reverses WI-0310's acceptance in as many words**, which said *"gate-inputs.json is re-derived whenever the suite hash moves, or the record FAILS rather than being consulted stale — never a third option where a stale record is used with a warning."* Recording why, because a future reader will find that sentence:

- WI-0310's subject was the **direction** of the failure. A stale record fails permissively: the newest tests are the ones most likely to be missing from the measurement, so a stale record can let a land skip the gate those tests were written for. That hazard is real and it is closed here — just at the classifier rather than at the door. A land under a stale record gates **more**, never less.
- What made refusal tenable in WI-0310 was that the land carried its own deriver, so a refusal was always one command away from being cleared *by the land itself*. D3 removes that. A refusal with no deriver behind it is not a stricter check; it is a repo-wide outage triggered by the most ordinary event there is — a lane adding a test file.
- So the two designs are identical on the question WI-0310 cared about (neither lands untested code), and differ only on what happens to a land that cannot be classified: it used to stop, and now it gates. One of those can wedge a fleet.

The warning names the command **and the obligation that is supposed to be running it**, because a reader who cannot tell a lapsed obligation from a normal day will learn to skip the line.

### D3 — The derive leaves the land path entirely; a nightly obligation owns the record.

`_gate_lane_candidate` is deleted. No land spawns `curate/gate_inputs.py --derive`, no land commits `gate-inputs.json`, and a test asserts it structurally — over the *argv* built in the landers' source, with comments, docstrings and the operator-facing WARN line exempt by parsing rather than by pattern, so the guard cannot be satisfied by deleting the explanation.

The record is now produced by **OPS-0009 (withheld)**, a daily obligation (`daily` is new to `OPS_CADENCES`, in the day map beside `weekly` for the same reason). An obligation rather than a launchd runner, and the choice is argued in the item itself: the runner is the more automatic answer and the right next step, but it belongs on the Runner, where it cannot be installed or verified from devbox — and a scheduled job nobody has watched run is a capability claimed rather than shipped. Obligations surface at every session start ahead of every dev item, which is a scheduler with a human in it. Promoting it has a real acceptance test ("it ran last night and the record moved") and deserves to be done that way rather than asserted here.

**[ADR-0122](0122-validation-reuse-and-resumable-completion.md) D1 goes with it, as moot rather than wrong.** That decision built a proof binding a serial derive's verdict to a candidate, an interpreter, a discovery set and an environment, so that a land which ran the suite twice could skip the second run. With the derive gone there is no first run to trust: the gate runs the suite exactly once, over the tree that lands. D1's requirement — *"establish that the publication preserves the verdict"* — is met by construction rather than by evidence, which is the stronger of the two. `_gate_validation_reuse`, `_gate_discovered_ids`, `_run_gate`'s `serial_derive_exit` parameter and `tests/test_gate_validation_reuse.py` are removed with the second run they existed to elide. Leaving them unreachable was considered and rejected: a parameter left behind is an invitation to supply it again, and D1's own standard — evidence, never a plausible-looking proxy — is not served by keeping a proof nothing calls. ADR-0122 D2, D3 and D4 are untouched; D4's stage measurements are extended here rather than replaced.

### D4 — Every land writes a receipt, and FL7 reads the window.

A land appends one JSON line to `land-receipts.jsonl` in the git common dir: the outcome, the lane, the parent and tip, the attempt number, and the three durations that matter — time queueing, time validating, and **time holding the gate** — plus the per-stage breakdown inside the hold.

**Why a file and not the existing tombstone.** `poga-coord/land-gate/released/trunk.json` already records `claimed_at` → `released_at`, but it is one file per *name* and the name is `trunk`, so every land overwrites the last one's times. "No land in the window held the lock over 30 seconds" is a claim about a **population**, and a surface that keeps one sample cannot answer it.

**Local and never tracked**, which is not a shortcut. A tracked receipt would be rewritten by every land — a merge hotspot on the one file every concurrent lane touches, which is the shape [ADR-0119](0119-the-gates-three-side-doors-are-shut.md) D5 exists to keep off the trunk. Lands are per-machine events and the git common dir is per-machine state. Appended under `O_APPEND` one short line at a time so two lands cannot corrupt each other, and trimmed to a 45-day window on every write (`retention-enforced-by-code`) so it cannot grow unbounded on a path nobody watches.

**FL7** is the seventh finish-line capability — *concurrent lanes queue behind each other's merge, not each other's test suite* — with a scoreboard row (`probe_land_lock_hold`) that reads the receipts over the board's 14-day window. It is **red if ANY land in the window held the gate over 30 seconds**, not if the median did: one land holding for four minutes is four minutes every other lane spends queueing, and it does not matter that ninety-nine others were quick. This is a budget on a shared resource, not a performance average. An empty window reports **UNKNOWN**, never PASS — no lands is not evidence that lands are fast. Nothing here blocks a land; the board reports, and the land's own output carries an `OVER BUDGET` line for the operator watching it.

## Alternatives considered

**Make the gate faster instead.** [ADR-0114](0114-the-land-gate-is-serialized-one-at-a-time.md) already answered this for its own problem and the answer holds here: halving the suite moves where the queue settles, not whether it settles. It is also already half-done — WI-0296 sharded the suite from 290 s to 60 s — and the wave that produced this item happened anyway, because the derive is unshardable by construction.

**Keep the derive in the land but only on the first attempt.** Cheaper than today, and it keeps the record fresh automatically. Rejected: it leaves a 271-second serial suite inside a section whose whole purpose is now to be short, and it leaves it there on exactly the lands that change tests — the ones most likely to be retried.

**Refuse on a stale record, and have the nightly obligation be the only fix.** The literal reading of WI-0310's acceptance carried forward. Rejected under D2: with no deriver in the land, one lapsed night stops every land in the repo, for a condition that is the normal state of a repo whose lanes write tests.

**Hold the lock across validation but let waiters run their gates speculatively.** Closer to the original D2 and it would shorten the wall clock, but it re-creates precisely the feedback loop [ADR-0114](0114-the-land-gate-is-serialized-one-at-a-time.md) measured: N suites competing for the same cores, each one slower for the others' presence. The gain here comes from *not running the suite N times*, not from running them at once.

**Publish outside the lock under a narrower lock.** Satisfies acceptance (b) word-for-word. Rejected under D1: it invents a second concurrency design to match the ruling's phrasing while re-opening the `index.lock` collision the trunk lock exists to prevent, and the budget measures the outcome either way.

## Consequences

- **A land's cost to everyone else drops from its whole suite to its merge.** Measured on this item's own disposable land: see the journal. Individual lands are not faster — a lane still pays its own validation — but the queue drains at merge speed rather than suite speed, so a fan-out of N lanes costs roughly one suite each instead of one suite each *serialized*.
- **The trunk stops carrying `chore(gate): derive suite input record` commits.** Four of them reached main in thirty minutes on 2026-09-10. The record now moves once a night, on purpose.
- **A stale record makes lands slower, not riskier**, and each affected land says so. If OPS-0009 lapses for a week, the symptom is that gate-neutral close-outs stop being quick — visible, bounded, and self-announcing.
- **`_land_branch` gained a retry loop it never had.** It gated under the lock, so the trunk could not move under it and it needed none. Moving the gate out gives the trunk a window, so the loop comes with it. It is the legacy session-branch path and no lane uses it; it is changed anyway, because [ADR-0117](0117-a-land-classifies-its-own-diff-before-it-gates.md) D4's rule — a land-path change made in one lander and not the others ships as a known gap — applies to this ADR as much as to that one. A structural test now asserts that no lander opens the land gate itself and that all three advance through the one helper.
- **ADR-0122's WI-0326 loses a dependency it was counting on.** Resumable completion was specified on top of WI-0325's reuse evidence; that evidence is gone, and what replaces it is simpler — a stage receipt per land, already durable, already reconciled against the trunk ref by the parent-equals-trunk check. WI-0326 should be read against this ADR before it is worked. **Finding for review**, not a decision taken here.

### What this does not do — two refusals, named rather than left to be discovered

- **The push-rejected integrate path still runs a full gate inside the held section.** When `git push` is refused, the lane calls `_integrate_trunk_with_remote` (WI-0143, ADR-0097 (withheld)), which merges origin in, **re-gates**, CASes and pushes — re-entrantly, inside the lock this ADR just emptied ([ADR-0114](0114-the-land-gate-is-serialized-one-at-a-time.md) D6 makes the re-entry legal). It is the one remaining path that can blow the 30-second budget, and it is rare: it needs a peer to have pushed to origin during this land. It is left alone because the alternative — releasing the lock mid-publication, with the local trunk already advanced and origin not — is a worse shape than a slow hold, and because FL7 will now name it the moment it happens instead of it being invisible. **Finding for review.**
- **`session.py integrate`, invoked as a verb, still takes the lock across its own gate.** Same mechanism, same reason, and unlike the path above it is operator-invoked and one-at-a-time in practice. Its receipts are written under the `integrate` verb so the board can tell the two apart, but the budget is not enforced differently for it. **Finding for review.**

## References

- [ADR-0114: The land gate is serialized](0114-the-land-gate-is-serialized-one-at-a-time.md) — D2 superseded here; D7 kept in full.
- [ADR-0117: A land classifies its own diff before it gates](0117-a-land-classifies-its-own-diff-before-it-gates.md) — the measured input set, and the staleness hazard it named and left open.
- [ADR-0119: The gate's three side doors are shut](0119-the-gates-three-side-doors-are-shut.md) — number release at the CAS, the live-holder lease, trunk-only views.
- [ADR-0122: Validation reuse and resumable completion](0122-validation-reuse-and-resumable-completion.md) — D1 retired here; D2–D4 stand.
- OPS-0009 (withheld) — the nightly derive.
- ADR-0105: The roadmap's generated regions (withheld) — the region contract WI-0357's `None` sentinel extends.
- Work items: WI-0329 (this), WI-0357 (the D1 amendment above), WI-0285 (the `## Now` board), WI-0310, WI-0325, WI-0326, WI-0267, WI-0294.
