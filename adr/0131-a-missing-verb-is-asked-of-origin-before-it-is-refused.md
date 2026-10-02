# ADR-0131: A missing verb is asked of origin before it is refused, and the substrate refreshes itself

**Status:** Accepted
**Date:** 2026-09-13
**Deciders:** Federation Architect (lane, dispatched on WI-0151)

## Context

`poga integrate` ([ADR-0097](0097-a-lane-integrates-the-trunk-with-its-remote.md) / WI-0143) exists for one situation: a trunk whose push was rejected because origin moved on. Session ~161 tried it on a genuinely stuck Runner and got

```
integrate is not a poga verb
```

`~/.local/bin/poga` is a symlink into the MAIN checkout, and that checkout still held pre-fix code. **The fix for the blockage was sitting at origin, unreachable by exactly the blockage it fixes.** The verb was only exercised at all because a lane can merge origin into its own branch and run its own copy of the harness directly — a route a stuck operator would have to invent, and one that needs a live lane, which the machine that most needs recovering is the least likely to have.

WI-0151 records a second half with the same root. A lane runs the MAIN checkout's wrapper through the PATH symlink while running its OWN `session.py`, so a lane can hold newer code than the dispatcher fronting it. The two disagree silently, and did.

Both halves are the same shape: **two copies of the substrate disagree, and the operator's PATH reaches the wrong one.** Reading either copy cannot see it — each file is individually correct. This is the failure family [`never-route-your-own-work-through-the-user`](../habits/master.md#never-route-your-own-work-through-the-user) names, arriving through the substrate rather than through a session: the tool's own refusal is what converts the Architect's problem into the operator's errand.

The asymmetry that makes a fix possible at all: **a diverged checkout cannot PUSH and cannot FAST-FORWARD, but it can always FETCH**, and it can always write one blob out of a fetched tree. "My substrate is behind origin" is the one failure in this family recoverable from inside the stale substrate.

## Decision

**A verb-shaped token the wrapper does not have is a question, not a verdict, until origin has been asked.**

Before refusing, `poga` consults — in this order, each step cheaper and closer to hand than the next, so the network is reached only when nothing local can answer:

1. **The tree under your feet.** A lane carries its own wrapper while the one on PATH is the main checkout's. If the standing tree's verb table has the token and the running copy does not, re-exec into that copy. Free, offline, and the whole of WI-0151's second half.
2. **Origin.** Fetch the trunk with an explicit refspec, lay `origin/<trunk>`'s executable harness in a scratch directory, and ask *that* copy whether it has the verb — **by running it** (`<verb> --help` is answered inside `parse_args`, before any command function or config check), never by grepping for a parser call that usually wraps onto a second line.
3. **Refresh and retry.** If origin has it, write origin's harness over the checkout's and re-exec the command the operator actually typed.

**D1 — It refreshes rather than printing the command that would.** A recovery ending in *"…and now type this"* has moved the defect, not removed it. The automatic path is the DEFAULT; `POGA_NO_SELF_REFRESH=1` is the opt-out, never the reverse.

**D2 — The subject is the EXECUTABLE harness only:** `poga`, `session.py`, `interpreter.py`, `standard_check.py`, `sessionlib/`. That is `push-substrate.py`'s `BYTE_IDENTICAL` minus `CANON.md` and `STANDARD.md`, which are deliberately out of scope — they are content, and no amount of content makes a verb dispatchable. `sessionlib`'s module list is read out of the tree being installed *from*, never written down ([`derive-a-checks-subjects-from-the-authority`](../habits/master.md#derive-a-checks-subjects-from-the-authority)).

**D3 — It refuses outright on a locally MODIFIED harness path and names it.** That is unlanded work, and a recovery path is not a reason to lose it. Everything the refresh can then replace is committed content, so nothing unlanded can be destroyed and no undo instruction is owed.

**D4 — It anchors on the MAIN checkout**, like every verb whose subject is shared state, so recovering from inside a lane does not write substrate onto that lane's branch.

**D5 — Every forwarding site goes through one door.** `harness_exec` replaces a bare `exec` at all 21 `session.py` forwards. What WI-0151 records is a property of the SUBSTRATE, not of `integrate` — the verb it happened to be found on — and teaching only that site would leave every sibling answering the old way ([`retire-the-class-not-the-instance`](../habits/master.md#retire-the-class-not-the-instance)). The cost on the ordinary path is one extra process, not one extra python start: only argparse's exit 2 buys the probe, so the 0.109 s per-verb interpreter tax [WI-0222 measured and refused](0082-lifecycle-inversion-and-per-session-runtime.md) is not reintroduced by the back door. A bad FLAG also exits 2, so the probe asks which of the two it was before spending a fetch.

**D6 — A refusal that survives the consult says so.** "Not a verb HERE" and "not a verb" are different answers, and so is "could not reach origin" ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)). All three are distinct lines. `harness_has_verb` likewise returns three values, not two: has it, does not, could not tell.

**D7 — The loop guard is handed forward, not exported.** `POGA_SUBSTRATE_REFRESHED` must reach exactly one process — the retry. Exported it would mark the launcher's whole descendant tree, handing every lane a retry spawns a flag that silently DISABLES its own recovery. It is set with `env` on the exec, and `main` consumes it into a plain shell variable and unsets it on its first line.

## Alternatives Considered

- **Print the runnable command instead of acting** (the item allowed either). Rejected under D1: the operator remains the transport, which is the original finding one level up.
- **Run origin's harness from a throwaway worktree and leave the checkout alone.** Safer-looking, and rejected because it fixes one invocation rather than the machine: the next verb is missing too. It also needs a scratch checkout of the whole repo where three files suffice.
- **Grep the harness source for the subcommand.** Rejected: most `add_parser` calls wrap onto a second line, so the grep answers wrong for the majority of verbs — the exact "check whose assumption about its subject's shape quietly stopped holding" that `harness_fixture` exists to record.
- **Pre-check staleness before forwarding, rather than after an exit 2.** Rejected on WI-0222's measurement: a second python start costs 0.109 s against a `poga work list` that costs 0.15 s. The failure path is the only place that tax is affordable.
- **Fix `integrate` alone.** Rejected under D5.
- **Hardcode the substrate file list.** Rejected under D2 — that list is the transcribed copy the wrapper's own `poga_verbs()` exists to delete.

## Consequences

- A typo now costs a fetch (~1.5 s measured) before its refusal. That is the price of the refusal being a *searched* negative.
- Tests that drive the refusal must run outside a repo, or the suite reaches the live remote. `tests/test_preflight.py` and `tests/test_substrate_voice.py` were moved to a temp non-repo cwd for exactly this reason — structurally out of reach rather than trusted not to reach ([`evidence-is-separated-from-state-by-construction`](../habits/master.md#evidence-is-separated-from-state-by-construction)).
- **This is forward-looking by construction.** A machine stuck *today* with a wrapper from before this change still has no route — the copy that would consult origin is the copy it does not have. Nothing decided here can change that; what changes is that the next stuck machine recovers itself, for any verb. Members get it at the next `curate/push-substrate.py` run.
- Pinned by `tests/test_stale_substrate_recovery.py` (16 tests), which builds a bare origin carrying the current harness and a checkout cloned before it did, and drives the checkout's OWN wrapper as a subprocess — the only shape in which a disagreement between two copies of the substrate can fail honestly.
- **Not fixed here, and measured:** `integrate` run from the MAIN checkout advances `refs/heads/<trunk>` by `update-ref` while `_sync_main_checkout` early-returns on `main == ROOT.resolve()`, so that checkout's tree is left behind its own ref — a `D` staged deletion of everything the advance brought, the session-89 phantom (WI-0040, WI-0067). It is pre-existing, independent of this decision, and lives in `sessionlib/land.py`, which WI-0151's own INDEPENDENT-OF list separates from this work. Recorded on WI-0151.

## References

- WI-0151 — the item, and the session-~161 finding.
- [ADR-0097](0097-a-lane-integrates-the-trunk-with-its-remote.md) / [ADR-0102](0102-integrate-merges-a-diverged-trunk-never-replays-it.md) — the `integrate` verb this was found on.
- [ADR-0070](0070-worktree-lanes-are-fleet-substrate.md) — poga resolves the repo from the caller, the decision whose symmetric defect this is.
- [ADR-0099](0099-the-user-is-not-an-execution-surface.md) / WI-0158 — every command we print must be runnable by the reader we print it to.
