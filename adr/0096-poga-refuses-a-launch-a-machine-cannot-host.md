# ADR-0096: poga refuses a launch the machine provably cannot host, and says how to fix it

**Status:** Accepted
**Date:** 2026-08-22
**Deciders:** the operator (the standing ruling — the operator should never see these when dealing with poga, and POGA never asks the operator to perform a git or POGA operation, 2026-08-19; and the direction for this build: gate the instant local checks, leave auth and the land gate behind the explicit verb), Federation Architect (the subset choice, the UNKNOWN policy, and the fail-open branch).
**Extends:** [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) (poga is the deterministic outer shell), [ADR-0082](0082-lifecycle-inversion-and-per-session-runtime.md) (D4 — resolve before allocating), and the standing rule that session start makes no network probes (ADR-0092, withheld).
**Work items:** WI-0149 (this job). Bears on WI-0105 (a failed launch strands the lane it already created) and on the 2026-08-19 consultant brief's thesis.

## Context

Standing up a new development machine hit four preconditions in quick succession. Each was found by
the operator hitting it and the Architect diagnosing it afterward, and **three of the four ended
with a command in his terminal**:

1. **terminfo** — no entry for the terminal emulator's `$TERM` value. Missing `kbs` meant backspace did nothing;
   absent width data made pastes *render* truncated while the buffer was intact. Nothing
   errored. The terminal simply lied.
2. **workspace trust** — poga delegates lane creation to `claude --worktree`, which refuses
   with "Workspace trust not yet accepted". Nothing checked it, so it surfaced as a failed
   poga launch rather than as the dialog it actually is.
3. **onboarding** — `hasCompletedOnboarding` absent ran the first-run wizard, whose login
   step opens a browser on a headless host, *even though* `claude auth status` already
   reported `loggedIn: true`.
4. **a stranded auth flow** — a probe `claude auth login` outlived its TaskStop by 30
   minutes, because killing a local ssh client does not kill the remote process.

Every one of these is knowable *before* the launch, from a file read or a process list.
None of them was checked. That is the pattern the 2026-08-19 consultant brief says must
become structurally impossible rather than individually regrettable, and it is the operator's
2026-08-04 ruling restated a third time — which per `add-structural-guard-on-recurrence`
means the discipline-level answer is spent.

WI-0149 asked for two things: an on-demand verb, and the strong version where poga refuses
a launch itself. The verb is uncontroversial. The gate needs a decision, because a check in
front of every launch is a cost every launch pays.

## Decision

**D1 — `poga preflight` is the on-demand verb, and it reports every check separately.**
Seven checks against *this* machine and the checkout it is standing in: terminfo, workspace
trust, onboarding, live auth, data root, stranded auth flows, land gate. It runs against the
tree you are standing in — deliberately not the main-checkout anchor `work` and `ops` use —
for the same reason `poga test` does: the question is whether *this* checkout can be worked
and landed in.

**D2 — three verdicts, never two, and the exit code carries all three.** Every check
returns `OK` / `FAIL` / `UNKNOWN`, and an UNKNOWN prints its own sentence naming what could
not be determined (`declare-what-a-check-assumes`). `--quick` reports the land gate
`SKIPPED`, never OK. Exit 0 = ready, 1 = something failed, 2 = nothing failed but something
could not be determined. Collapsing UNKNOWN into either neighbour is what turns "we did not
look" into "it is fine" — and a not-applicable branch that reads as a pass does not merely
fail to detect a gap, it certifies one.

**D3 — the launch gate is the instant local subset: terminfo, workspace trust, onboarding.**
The test for membership is: local, instant, and its failure stops a lane from being usable.
All three are file reads. **Auth and the land gate are deliberately excluded** — auth reaches
the network and the gate is ~90 seconds, and putting either in front of every launch is
exactly the startup cost the no-network-probes rule forbids. They stay behind the explicit verb, where someone
standing a machine up pays for them once. This is a launch gate, not a health check: it
refuses only what it can prove broken for free.

**D4 — the gate refuses on `FAIL` only. `UNKNOWN` warns and lets you through.** A gate that
blocked on "could not determine" would refuse every launch where `$TERM` is unset (all
non-interactive invocation) or `~/.claude.json` is briefly unreadable — turning an
unanswered question into a locked door. This is *not* D2's compromise in disguise: the
warning is printed by name, the operator sees exactly what went unverified, and `poga
preflight` answers in full on demand.

**D5 — it runs before `alloc_lane`.** Identical to ADR-0082 D4's argument for resolving the
runtime first: a refusal that arrives after the worktree and branch already exist is
confusing to read and leaves litter to clean up — the failure WI-0105 is open about. It runs
on `poga resume` too: resume creates no lane, but the three checks are about whether a
runtime can usefully start on this machine, which is equally true entering an existing lane.

**D6 — the gate fails OPEN when it cannot run, and says so.** The exit contract is
three-way: 0 launch, 1 refuse, *anything else* launch with a note on stderr. `poga` is
pushed byte-identical to every member, and a member whose `session.py` predates `preflight`
answers exit 2 from argparse. A gate that blocked there would brick every launch on every
un-migrated member the moment poga shipped ahead of session.py — turning a missing *feature*
into a missing *capability*. The note is what keeps this from being a silent assumption of
health.

**D7 — the bypass is named in the refusal.** `POGA_SKIP_PREFLIGHT=1` skips the gate and
announces that it did. A gate with no escape hatch eventually blocks legitimate work with no
recourse; an unnamed hatch is one only its author can find. Naming it in the refusal itself
costs nothing — someone reading a refusal is exactly the person who may need it — and the
announcement means a bypassed launch is never mistaken for a verified one.

## Consequences

A fresh machine now fails **once, in one place, with the remedy attached**, instead of once
per precondition in the operator's terminal. The three walls that produced a failed
launch or a browser on a headless host are refused before a lane exists.

The gate is not a health check and must not grow into one. Every check added to D3's subset
is paid by every launch on every member, forever; the pressure to add "just one more" is why
the membership test is written down rather than left to judgment. New checks belong in the
verb (D1), and only move to the gate if they are local, instant, and launch-fatal.

`preflight` is not on the session-start path and must not be put there. Its two most
valuable checks are the two that cost the most, and the no-network-probes rule is what keeps them
behind an explicit verb.

**A known gap this ADR does not close.** The verb reports the state of the machine it runs
on; nothing yet *fixes* what it finds. Every remedy is still a human action, and two of them
(installing a terminfo entry, accepting a trust dialog) are the kind of thing the consultant
brief argues should not reach the operator at all. Making the remedies executable is the next step
and is deliberately out of scope here — refusing with an accurate remedy is strictly better
than the current silence, and shipping it should not wait on the harder half.
