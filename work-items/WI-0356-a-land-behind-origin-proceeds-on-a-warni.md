# WI-0356: A land behind origin proceeds on a warning, and one-behind becomes a diverged trunk

- status: done
- section: next
- blocked-by: 
- group: Fast landing and completion
- source: 
- impact: fix
- version: 7.3.0

MINTED 2026-09-13 by session ~302 under the operator's standing decide-and-record rule. He set the
requirement — "a land that is behind origin must not proceed on a warning; either it
integrates first or it refuses" — and left the shape to me. THE SHAPE IS DECIDED HERE AND
IS NOT AN OPEN QUESTION.

WHAT HAPPENED, MEASURED. `sessionlib/land.py:129` prints
`IS N COMMIT(S) BEHIND origin/main ... Pull there, then re-run. (Reporting only; nothing
pulls under you.)` and then lands anyway. On 2026-09-13 the shared trunk was 1 behind
origin (the Runner's own deploy-receipt push). Lands kept going on top of the stale trunk.
Within the hour the trunk was 19 AHEAD and 1 BEHIND — diverged — and the push was rejected
non-fast-forward. Recovery cost an `integrate` (merge + full re-gate) and reached the user,
which is the failure this item exists to remove.

A warning that the tool itself then ignores is not a safeguard; it is a record that the
tool knew. One-behind is trivially recoverable and diverged is not, so the warning fires in
exactly the window where acting is cheap and declines to act.

THE RULE, DECIDED. Before a land merges the trunk into the lane, it compares the SHARED
trunk against origin and branches on three states, never two:

1. **BEHIND ONLY** (ahead == 0, behind > 0) — FAST-FORWARD THE TRUNK AUTOMATICALLY, then
   land. Nothing is rewritten and nothing can be lost; this is the state where acting is
   free. Shipping this as the DEFAULT rather than an instruction is the point: a fix that
   ends in "and then someone runs a pull" leaves the defect in place with a manual step
   bolted on.
2. **DIVERGED** (ahead > 0 AND behind > 0) — REFUSE, naming `python3 session.py integrate`.
   Reconciling a diverged trunk means a merge plus a full re-gate; that is integrate's job
   and must not happen implicitly inside someone else's land, where a conflict would
   surface as a mysterious land failure.
3. **CANNOT TELL** (the refresh failed; `behind is None`, or a zero that no successful
   refresh dates) — REFUSE. The existing staleness reporter already distinguishes these
   honestly and must not be collapsed here: "I could not look" rendering as "fine" is the
   precise failure `declare-what-a-check-assumes` names, and the whole point of this item
   is that proceeding on an unverified trunk is what costs.

WHY REFUSE RATHER THAN AUTO-INTEGRATE in case 2: an auto-integrate inside a land would run
the suite twice and could leave a half-merged trunk if the re-gate went red, with the lane
holding the land lock throughout. Refusing costs one command; guessing costs the trunk.

ACCEPTANCE: with the shared trunk 1 behind origin and not diverged, a land fast-forwards it
and proceeds, with no human action and no warning left unacted on. With the trunk diverged,
the land REFUSES before taking the land lock and names integrate. With the staleness check
unable to answer, the land refuses and says it could not verify rather than reporting
current. Each of the three states is pinned by a test that fails if the branch is removed,
and the behind-only case is watched going red against today's warn-and-proceed code.

SEQUENCING: edits sessionlib/land.py. Check against anything else touching the land path
before running it in parallel.
