# ADR-0125: An unattended run closes on its own receipt, and says that no human confirmed it

**Status:** Proposed
**Date:** 2026-09-11
**Deciders:** Federation Architect (lane, WI-0331 — the second-authorization reframe, the honest label, and the choice to grow a standard capability rather than fix the runner against the fleet as it stands).
**Extends:** [ADR-0113](0113-a-dispatched-lane-closes-on-its-dispatch.md) — same guard, same resolved-not-asserted receipt, same fail-open asymmetry, on a second source. Nothing in ADR-0113 is reversed.
**Builds on:** [ADR-0104](0104-a-session-closes-on-the-user-s-word-never-the-agent-s-judgment.md) (the `--confirm` gate and the `close-confirm` receipt, both kept), [ADR-0112](0112-a-relayed-approval-cites-a-grant-not-a-peer.md) (assertion substitution; D7 — an environment variable is a marker, not a control), [ADR-0050](0050-headless-background-adoption-runner.md) (the headless adoption runner this unblocks), [ADR-0036](0036-session-liveness-sidecar-and-orphan-auto-triage.md) (the gitignored, machine-local state dir the run record lives in)
**Related:** WI-0331 (the charter), WI-0249, WI-0144, OPS-0007 (the weekly triage run that found it)
**Reality:** Built — the resolver, the guard wiring, the runner's marker and run record, the standard-section wording and the v1.18.0 conformance detector all ship together.
**Work item:** WI-0331

## Context

**ADR-0113 found one non-human close authorization. There were two.**

That ADR redrew ADR-0104's close guard on *what authorized the close* rather than *where `end` runs*, and named the case it was solving: a dispatched lane, working an item, with nobody attached to agree. The reframe was right. The enumeration was short by one.

`curate/adopt-runner.py` is the fleet's **only** unattended adoption path ([ADR-0050](0050-headless-background-adoption-runner.md)). It sweeps the members, and for each one holding a runner-eligible brief it spawns a headless `claude -p` session *in that repo, as that Architect*, and instructs it — in the prompt, verbatim — to close through `session.py end --title "..." --commit`.

That session is not dispatched. It is not in a lane. Nobody is attached to it. So `end` refused it, every time, on every member, on every sweep. `adopt_one`'s verify-or-reset then saw a session that produced no commit, `git reset --hard`'d the applied brief away, and wrote a `blocked` comms note. **The path could not complete by construction.** Found 2026-09-11 on OPS-0007's first run, by reading the whole path end to end.

The mechanism is three files deep and each layer looks correct on its own:

| | |
|---|---|
| `curate/adopt-runner.py:570` | tells the session to close with `session.py end --commit` |
| `sessionlib/hooks.py:647` | `end` raises `SystemExit` unless `--confirm` is passed **or** a dispatch resolves |
| `sessionlib/coord.py:2556` | the dispatch exemption requires `POGA_DISPATCH` in the environment |
| `curate/adopt-runner.py:728` | `subprocess.run(cmd, cwd=launch_dir)` — **no `env=`**, so nothing ever sets it |

### Why this was worse than a stuck verb, which is the whole argument for fixing it

A blocked path is an outage. This was an outage **with a trapdoor**.

The refusal message names exactly one escape: `--confirm "<what the user said>"`. For a session with no user in it, taking that escape means **inventing a sentence nobody uttered** and writing it into the audited field whose entire purpose is to record that a human agreed. The substrate did not merely block the adoption path; it presented, as the single sanctioned way forward, the fabrication of its own audit trail — to an agent whose instructions told it to close, in a repo where failing to close would discard the work it had just done correctly.

ADR-0104 built `close-confirm` knowing it was not tamper-proof (*"an agent could fabricate the quote"*), on the reasoning that a fabrication sits in an auditable field the [ADR-0053](0053-mined-ritual-conformance-and-spot-audit.md) miner is built to catch. That reasoning holds for an agent that *chooses* to fabricate. It does not survive a substrate that **manufactures the reason**: a field the harness structurally pressures every unattended session to falsify is not evidence about anything.

### What could not be determined

Whether this has bitten in production. The newest applied adoption receipt predates the `--confirm` gate, so there is no run on either side of the change to compare. The defect is established by reading the path, not by a failed sweep; the tests shipped with this ADR are what establish it by running.

## Decision

**D1 — `end` recognises a second non-human authorization: an unattended run.** The guard stays `not confirm`. `confirm` now falls back, in order, to a dispatch authorization and then an unattended-run authorization. A hand-opened session is unaffected, in a lane or out of one.

**D2 — The two authorizations stay separate, with separate citations.** The cheap fix was available and is rejected: the runner could have set `POGA_DISPATCH` and let ADR-0113's fail-open path write `dispatch D-xxxxxx — UNRESOLVED` into each member's record. It closes the verb and corrupts the vocabulary. Every unattended adoption would be recorded, permanently and fleet-wide, as a dispatch that could not be read — naming a lane in another repo that had nothing to do with it, and making the degraded state the normal one. **Three authorizations, three readings, tellable apart:**

| what happened | `close-confirm` |
|---|---|
| a human agreed | their sentence, verbatim |
| a dispatch authorized it | `dispatch D-xxxxxx (item WI-NNNN) — resolved against the dispatch record` |
| a machine ran it unattended | `unattended run A-xxxxxx (curate/adopt-runner.py, brief <id>) — resolved against the run record. NO HUMAN CONFIRMED THIS CLOSE` |

**D3 — The receipt is resolved, never asserted.** `POGA_UNATTENDED_RUN` is an environment variable and therefore a marker, not a control ([ADR-0112](0112-a-relayed-approval-cites-a-grant-not-a-peer.md) D7 says exactly this of its sibling). So the runner writes a **run record** into the member's gitignored `.session-state/` before spawning, and `end` cites what it read there. Like ADR-0113 D3 this proves **intent and time, not identity**, and it must never be cited as an access control: anything running as the user can write the file. Raising the cost of a forgery is not the point — substituting a readable artifact for a self-assertion is.

**D4 — The label is load-bearing.** `NO HUMAN CONFIRMED THIS CLOSE` is in the citation and is checked by the conformance detector. An exemption that closes the session and leaves the receipt looking like any other close is the *same corruption as the fabricated quote*, reached by omission instead of by invention — and it is worse in one respect, because nobody had to decide to do it. The detector treats a resolved, wired, unlabelled citation as **absent**.

**D5 — It fails open, and says which.** A run id whose record cannot be read still closes, labelled `UNRESOLVED`. This is ADR-0113 D5's inversion of [ADR-0112](0112-a-relayed-approval-cites-a-grant-not-a-peer.md) D8, for the same reason plus one: a wrong refusal here does not only rebuild the wait, it triggers `verify-or-reset` and **destroys real work that was applied and verified correctly**. A wrong allow leaves a labelled row the miner can see.

**D6 — The authorization is scoped to the run, and the writer retires it.** The runner removes the record as soon as the spawn returns, in a `finally`. An authorization artifact that outlives its run is a standing licence to close a session nobody started — the next interactive session in that repo would find one sitting in its state dir. Enforced by the code that owns the file rather than deferred to the janitor ([`retention-enforced-by-code`](../habits/master.md#retention-enforced-by-code)).

**D7 — The runner strips its own dispatch handles at the spawn.** The sweep is routinely run from inside a dispatched lane and `subprocess.run` passes the ambient environment through wholesale — the same inheritance the gate's suite hit (WI-0242 / WI-0250). Unstripped, a member's `end` would resolve the *federation's* dispatch id against its own store and write a citation about a lane in another repo. Removed at the spawn rather than trusted not to be set.

**D7b — The receipt is one frontmatter line, whatever reaches it — a hole found while building D3 and fixed as a class.** `close-confirm` is rendered as a `key: value` line, so a newline in the value does not wrap, it **forges keys**: measured, a run id carrying `\nordinal: 999` renders a second `ordinal` into the closed journal, and `ordinal` is the field the handoff compiles sessions by. **Three sources reach that argument and all three could carry a newline** — `--confirm` (composed by an agent), the ADR-0113 dispatch citation (interpolates `POGA_DISPATCH`), and this one. Fixing only the source this lane introduced would have left the older two running the old answer ([`retire-the-class-not-the-instance`](../habits/master.md#retire-the-class-not-the-instance)). So the flattening lives at the choke point where the value becomes frontmatter, and each id also gets a shape check at its own read. **Flatten, never reject:** a close must not fail because its receipt was oddly shaped, and the whole value survives — on one line.

**D8 — Standard v1.18.0, `scope "all"`, cut as its own release.** New capability `unattended-close-authorization`, three probes (computed **and** consulted; resolved inside the function's own body; labelled). A separate release rather than a fold into 1.15.0 on the 1.13.0 / 1.14.0 / 1.16.0 precedent: folding is free only when the prior release rolled out nowhere, and 1.15.0 is already satisfied by the federation. New code, so members read **behind** until the harness is pushed. That amber is the detector working.

## Consequences

**The adoption path can finish.** A sweep applies the brief, verifies it, closes, commits, and files the receipt — with no `--confirm` value that no human supplied, which is WI-0331's acceptance in its own words.

**The record gains a third legible state.** `close-confirm` could previously say "a human agreed" or "a dispatch authorized it." It can now say "a machine ran this, and no human agreed" — and the ADR-0053 miner can count those separately instead of reading them as human confirmations.

**Members read behind until the harness is pushed.** Most members carry the ADR-0113 half and not this one; their unattended adoptions keep failing exactly as they do today until the substrate push reaches them. The standard-version surface says so, per member, which is the whole reason the row is cut rather than deferred.

**The runner is a writer in the member's state dir.** One ephemeral, gitignored file, inside the writer relationship ADR-0050 already established (the runner authors that member's comms notes, files its brief, and resets its tree). No tracked state and no single-writer boundary ([P13](../principles/master.md#p13--single-writer-per-state)) is touched.

**A forged `POGA_UNATTENDED_RUN` plus a written record closes a session.** ADR-0113's honesty, unchanged and restated because it is easy to forget: this proves intent and time, not identity. The label limits the damage — a forged unattended close still announces itself as unattended.

### What this does not fix, stated rather than glossed

**Nothing verifies that an unattended session adopted the brief it was started for.** A session that gives up, or works the wrong brief, produces a citation as well-formed as one that shipped. That is behavioural, not structural; it is mined from the record (ADR-0053), and the resolvable citation is what makes the mining possible at all. The runner's own verify-or-reset is the operational backstop.

**The `end`-side half is tested against a fixture, not against a live member.** No member carries v1.18.0 yet, so the end-to-end claim — *a real sweep of a real member adopts and closes* — is unproven until the harness is pushed and a sweep runs. What is proven here is each half, separately, on the real code paths: the close guard driven with `POGA_DISPATCH` absent, and the spawn's actual environment and artifacts read from inside the spawned process.
