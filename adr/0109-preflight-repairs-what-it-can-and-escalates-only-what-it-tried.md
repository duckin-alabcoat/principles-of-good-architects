# ADR-0109: preflight repairs what it can, and escalates only what it tried

**Status:** Accepted
**Date:** 2026-09-04
**Deciders:** the operator (two rulings — that preflight, git and lane problems are fixed by the substrate and not reported to him, and, on the gate, that it repairs and carries on, announcing what it did, with refusal still available via a flag; and take the Architect's own recommendation on the guided step), Federation Architect (the repair-class split, the never-self-source rule, and the stranded-flow interlock)
**Extends:** [ADR-0096](0096-poga-refuses-a-launch-a-machine-cannot-host.md) (the verb and the launch gate), and **amends** its "the gate is not a health check" consequence
**Work items:** WI-0150 (this job). Bears on WI-0004 (the data-root remedy this ADR declines to automate) and WI-0245 (the conformance detector deliberately not shipped here).

> **Extended by [ADR-0135](0135-a-trunk-clone-is-never-a-process-root.md) (2026-09-13).** the operator's ruling here — preflight, git and lane problems are fixed and not reported — now carries a standing form for the deploy substrate: **no git sentence reaches the operator unless the design is being changed or something was lost.** A stranded commit, a refused fast-forward or a clone that will not push is the substrate's own housekeeping ([P10](../principles/master.md#p10--architect-owns-operational-substrate)); each hand touch that does happen is counted under WI-0159 rather than narrated. (The 2026-09-13 brief filed this against ADR-0120, which is about renumbering an unlanded ADR; corrected to this ADR, which carries the ruling it extends.)

## Context

ADR-0096 shipped the report and named this gap in its own Consequences rather than
leaving it implied: *"The verb reports the state of the machine it runs on; nothing yet
fixes what it finds. Every remedy is still a human action."* Eight checks, eight remedies,
and at least two of them — installing a terminfo entry, accepting a trust dialog — are
exactly the shape the 2026-08-19 consultant brief argues must never reach the operator. Deferring
it was right: refusing with an accurate remedy is strictly better than the silence it
replaced, and should not have waited on the harder half.

The ruling that closes it is broader than this verb. the operator ruled on 2026-09-04 that preflight
problems are fixed and not reported to him, and that the same holds for git and lane
issues. So preflight, repository state and lane state are the substrate's own housekeeping
([P10](../principles/master.md#p10--architect-owns-operational-substrate)) — repaired
automatically as the **default**, with the cautious path as an opt-out, never an opt-in.
This ADR builds the first instance and is meant to be the pattern the rest of that class
copies, so the shape matters more than the single check it currently repairs.

**One premise in WI-0150 is wrong, and correcting it is what makes the repair cheap.**
The item says terminfo is *"fully automatable where a peer machine is reachable (the fleet
already knows its machines)."* A peer is not needed at all. The entry that actually bit it in session ~161 ships inside the terminal emulator's
own app bundle under `/Applications`. **The remedy
ADR-0096 prints sends the operator to another machine for a file already present in
`/Applications` on the one they are standing at.** Verified end-to-end into a throwaway
database before any of this was designed: extract with `infocmp -x -A`, install with
`tic -x -o`, and the re-check resolves the entry and confirms it declares `kbs`.

## Decision

**D1 — repair is the DEFAULT, and the flag turns it off.** WI-0150 proposed `poga
preflight --fix`. An opt-in fix leaves the errand exactly where it was for everyone who
does not know to type it, which is the defect restated rather than closed. So the flags
run the other way: `--no-repair` on the verb, `POGA_NO_PREFLIGHT_REPAIR=1` on the gate.
The verdict the verb prints is the **post-repair** state, because "can this machine be
worked in now" is the question it is actually asked.

**D2 — every check declares a repair class, and the split is 1 auto / 3 guided / 4 none.**

| class | checks | why |
|---|---|---|
| `AUTO` | terminfo | local, non-destructive, and verifiable by re-running the check |
| `GUIDED` | workspace-trust, onboarding, auth | `claude`'s own first-run state; we can launch the step, never forge it |
| `NONE` | data-root, residency, stranded-runtimes, gate | no repair exists at this layer |

The class is **declared per check rather than inferred**, so a check added later must say
which class it is in. The `NONE` reasons are part of the contract, not commentary:
data-root's remedy would entrench the two-writer problem session ~161 recorded and
WI-0004 is the real fix; residency needs a *declaration*, and a value invented here is a
fabricated one; killing a live auth flow is destructive and this check cannot tell whose
it is ([P9](../principles/master.md#p9--destructive-ops-confirmed)); a red land gate is
failing code, and fixing it is the work rather than housekeeping.

**D3 — a repair is done only when the check RE-RUNS and returns OK.** The repair's own
exit code is never the receipt. `tic` exits 0 having installed a wrong entry, which is
precisely the wrong-answer-shaped-like-a-right-one this whole verb exists to stop
([`a-close-is-the-banner-not-the-sentence`](../habits/master.md#a-close-is-the-banner-not-the-sentence)).
A repair whose command succeeded and whose check still fails is reported as a **failed
repair**, which is both the honest answer and the only one that cannot certify a gap.

**D4 — the destination is never a source.** `~/.terminfo` is where the repair writes, and
the check only failed because what is there is absent or broken; reading the entry back
out of it would reinstall the broken entry and then confirm success. Sources are the
system database, a package manager's ncurses, and terminal-emulator app bundles, with the
destination excluded **by resolved path** so a `$TERMINFO_DIRS` naming it under another
spelling is excluded too. A candidate is rejected unless the entry it yields actually
declares the capabilities the check requires: a source that has the entry but not
`kbs` is no better than what is already installed, and swapping one silently-broken
terminal for another is not a repair.

**D5 — the launch gate repairs AUTO-class failures and carries on. This amends ADR-0096.**
That ADR says the gate *"is not a health check and must not grow into one,"* and this is a
real departure rather than a consistent reading of it — say so plainly. The sentence was
written against a gate that could only observe. A local `tic -x` is milliseconds and
passes ADR-0096 D3's own membership test unchanged (local, instant, launch-fatal), and
under the ruling a refusal carrying a correct command is the defect, not the fix. **What
does not change is the cost on a healthy machine, which is still zero**: a repair only
ever runs for a check that already FAILED. The gate runs AUTO always and GUIDED only when
a terminal exists to answer — a guided step is not a startup cost either, because on a
healthy machine it never fires.

**D6 — the repairing gate announces what it repaired,** on stderr. ADR-0096 made the gate
silent on a clean pass and that stays true for a genuinely clean pass. But silence is for
observation: a gate that **mutates** the machine and says nothing is one nobody can audit,
and a repaired launch that reads identically to a clean one is how a machine's real state
goes unnoticed.

**D7 — the refusal is still available.** `POGA_NO_PREFLIGHT_REPAIR=1` restores ADR-0096's
refuse-and-print behaviour, and skips the repair itself rather than merely its report.
Same shape as the WI-0169 auto-renumber landed in session 189: the automatic path is the
default and the escape is an opt-out, not the reverse.

**D8 — the guided step is skipped, with a reason, when it cannot be run.** Two blocks: no
TTY, and `stranded-runtimes` FAIL. The second is not a detail. Wall 4 of ADR-0096 was a
second auth flow opened while one was already live, so a repair that starts another auth
flow on a machine that already has one is **the original defect wearing a fix's clothes**.
Trust, onboarding and auth are settled by **one** interactive `claude` in the main
checkout — the main checkout, not the lane, for the same reason the *check* reads it
there: poga asks the runtime to make the worktree from the repo root, so that is the path
whose trust state decides whether a lane can be created.

**D9 — an undeclared check is a substrate defect, named, and non-fatal.** A check with no
entry in the repair table is reported as *our* omission rather than skipped, because
"nobody wired this up" must never read as "nothing can be done" — the
absent-reads-as-answered failure the OK/FAIL/UNKNOWN split already exists to prevent. It
does **not** raise: the completeness of the table is enforced by the suite, which is where
a structural guard belongs, and crashing a diagnostic verb on the machine whose diagnosis
was requested is a worse failure than the one it would guard.

## Consequences

**What a fresh machine now costs.** The terminfo wall disappears: the gate repairs it in
front of the launch that would have hit it, from a database already on the box. Trust,
onboarding and auth collapse from three commands across two machines into one dialog the
substrate opens itself. Four checks still hand back a remedy — and now each says **why**
the substrate could not do it, so what reaches the operator is *"I tried and could not,"* never
*"here is what I found."*

**The gate now mutates the machine, and that is a real widening of its contract.** The
pressure to widen it further is higher, not lower, which is exactly why the AUTO
membership test is written down: local, instant, non-destructive, and verifiable by
re-running the check. A repair that cannot be verified by its own check does not belong in
the gate at any speed.

**The peer-machine repair path is deliberately not built.** No peer route was
available to test against, so it would be untestable code asserting a capability nothing
could exercise — [`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)
inverted. If a source is ever genuinely reachable, that is a new decision with a live
machine to test it against.

**No conformance detector ships in this pass,** following the precedent set for WI-0245 in
session 189: adding one bumps the standard's `LATEST` and immediately marks already-stale
members newly non-conformant, which is a fleet-rollout decision rather than a side effect
of building a verb.

**This is the first build of a pattern with three more instances waiting.** The ruling
covers git state and lane state too. What generalises is not the terminfo repair but the
five properties around it: repair by default, a declared class per condition, the re-run
as the receipt, the destination never a source, and an escalation that carries the attempt
that failed. A later repair class that keeps four of those and drops the third will look
like this one and will not be.
