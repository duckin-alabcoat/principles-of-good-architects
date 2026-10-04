# ADR-0122: Validation reuse and resumable completion

**Status:** Accepted
**Date:** 2026-09-10
**Deciders:** the operator; Federation Architect
**Acceptance:** the operator accepted the ADR on 2026-09-10. Acceptance includes the revised D2: keep normal sessions open through confirmed push and completed cleanup, then close.

## Context

the operator requested fast closing and authorized creation of this ADR, two implementation
items and an amendment to WI-0312 after reviewing an outside reviewer's September 9 proposal.
the operator subsequently accepted the detailed contract below, including the revised close ordering.

Inspection of `sessionlib/land.py::_gate_lane_candidate` shows that a candidate
changing tests or `curate/gate_inputs.py` runs serial input derivation, commits its
generated record, then calls `_run_gate`, whose configured commands include the
sharded suite. The serial pass measures reads; the second pass supplies the normal
gate verdict. Removing the second execution requires proof that the first supplies
the same required validation, including the effect of publishing the generated record.

`suite_tree` fingerprints tests, not all candidate inputs. It cannot independently
certify validation reuse across attempts. The audit hook observes one interpreter;
declared dependencies still cover known reads outside that observation boundary.

Ordinary `merge` already routes through `cmd_end`. The latter closes the journal
before landing and refuses a second close. This records completion too early for
a normal session that can still finish its push. the operator approved the correction in
this session: stay open through delivery and cleanup, and close afterward. A crash
or explicit stop is an interruption, not successful completion. The proposal changes
that ordering within the existing path rather than creating another lifecycle.

Historical timing and token figures in that reviewer's brief motivate measurement; they
were not independently reproduced for this ADR and are not performance guarantees.

## Decision

### D1 — One equivalent validation per unchanged final candidate

> **Retired as moot by [ADR-0124](0124-the-land-lock-holds-the-merge-not-the-validation.md) D3 (2026-09-10), the day after it was accepted.** This decision exists because a land that changed `tests/` ran the suite twice — once under the serial derive, once under the gate — and asked whether the second run could trust the first. ADR-0124 takes the serial derive out of the land path entirely (it was 271.5 s inside the one-at-a-time lock), so there is no first run to trust: the gate runs the suite exactly once, over the tree that lands. D1's requirement is then met by construction rather than by evidence, which is the stronger of the two. `_gate_validation_reuse`, `_gate_discovered_ids` and `_run_gate`'s `serial_derive_exit` parameter are removed with the second run they existed to elide. **Not superseded on its merits** — the standard it set for reuse evidence (bind the verdict to the candidate, the interpreter, the discovery set and the environment; never a hash, a name or an exit code) is the standard any future reuse must meet. **D2, D3 and D4 below are untouched**, and D4's stage measurements are extended by ADR-0124 D4's land receipt.


A complete successful serial derivation may supply the suite verdict within its
land only after equivalence with the required gate is demonstrated: discovery and
executed tests, interpreter, gate environment, skips, failure semantics, candidate
inputs and execution conditions. All configured non-suite checks still run.

Evidence for reuse binds the candidate to relevant code, configuration, test runner,
measurement implementation, declared dependencies and execution conditions. A
tests-only hash, matching lane name, timestamp or successful process exit alone is
insufficient. Missing, failed, incomplete, mismatched or uncertain evidence refuses
reuse and requires validation. Relevant changes after rebase invalidate evidence.
Unchanged work alone is insufficient if its execution conditions no longer match.

The generated gate-record publication is part of this proof. Do not test one tree,
commit a new record, and assert that the resulting candidate was tested without
establishing that the publication preserves the verdict. Failed or partial evidence
must never become authority for later skips. If safe equivalence cannot be proved,
retain the existing validation and report the precise gap.

This does not change ADR-0117's neutral-diff policy or claim that runtime observation
discovers every possible dependency. Preserve conservative declared inputs and
unknown-input handling. Sharding the current parent-only audit probe is excluded;
worker instrumentation is a separate follow-up if measured serial cost warrants it.

### D2 — Stay open through delivery and cleanup; resume failed stages

Use the existing completion front door for journal/baseline preparation, candidate
validation, local land, confirmed push, cleanup and finally successful session close.
Prepare authored content before validation, but do not stamp `ended`, emit the
successful close banner or terminate the active runtime at preparation time. Record
delivery outcomes mechanically without requiring another model-authored edit and
land cycle. Preserve existing close-authorization rules.

The active session owns normal retries. A push failure leaves the session open with
delivery pending; a cleanup failure leaves it open with delivery confirmed and
cleanup pending. Retry the failed stage, retaining valid evidence for completed
stages. Bound retry attempts and delays; when a blocker persists, keep the session
open and surface the specific blocker instead of silently ending or spinning forever.
No recovery daemon is needed to finish an ordinary still-running session.

Extend existing lifecycle state where possible with atomic, durable stage receipts.
They identify the durable session, candidate, intended destination and completed
stages; a reusable lane name is not identity. Normal successful close requires
confirmed delivery and completed cleanup, and writes its final durable close receipt
before acknowledgement or runtime shutdown. Final stamp persistence must not depend
on a worktree that cleanup already removed: preserve the required durable state and
runtime through finalization. Define and test how the final journal/compiled status
reflects this receipt without another model-authored validation/land cycle; never
pre-stamp success merely to put a closing journal into the candidate being pushed.

Only a crash, runtime loss or explicit user stop can end execution with completion
unfinished. Record that as interrupted/stopped, with its last verified delivery and
cleanup stages; do not emit the successful-close receipt. Existing liveness/recovery
mechanisms own discovery of interrupted work, and an explicit resume/recovery uses
the same stage receipts. It must surface any pending delivery and its recovery path,
not label it complete or silently strand it. Session termination and delivery status
remain separate facts for these exceptional cases; no new unattended retry service
is introduced by this decision.

Recovery reconciles recorded stages against actual repository and remote state;
receipts cannot merely assert that an external effect happened. Cover crashes both
before and after each effect, including a successful push before its receipt is saved.

- After validated work lands locally, an unchanged push retry does not rerun its
  successful validation. Confirm the intended destination and remote result.
- Remote advancement is reconciled safely. A newly composed candidate requires
  validation unless equivalence is positively established. Never force-push to
  preserve a fast path.
- After confirmed push, the still-open session retries only unfinished cleanup and
  finalization. It closes successfully only after these complete.
- After successful completion, repetition returns the existing durable receipt
  without validation, another close stamp or release of another session's claims.

### D3 — One cleanup implementation with durable ownership

WI-0312 continues to own convergence of land, reap and recovery through the shared
exit implementation. It adds partial-failure retry, repeated cleanup and reused-lane
identity coverage. WI-0326 owns stage persistence and recovery routing and calls that
shared implementation. It does not implement another release path.

A late cleanup from an old session cannot release a new holder's claims or
reservations even when that holder reuses the same lane slot. Cleanup cannot
fabricate delivery success, close a still-retrying session or relabel an interruption
as successful completion. Runtime shutdown follows durable finalization.

### D4 — Report stage progress and measure the actual cost

Flush start/end receipts with elapsed times for queueing, derivation/validation,
remaining checks, local land, push and cleanup on the paths changed by these items.
Persist the final delivery receipt. Report reused validation explicitly with its
evidence and explain any additional full-suite execution or invalidation.

Measure comparable before/after suite counts and stage wall times. Benchmark repeated
disposable completions and report median and tail latency. The proposed target is
at most five seconds of post-push bookkeeping on the dev machine, separately measured from
queueing, validation and network time; it is not a promise that validation is instant.

The CLI already blocks. Empty model polling originates at the tool boundary and
remains an existing weekly-review finding. Do not promise one tool call, change the host's
communication limits, or translate input-token counts into guaranteed allowance savings.

## Alternatives Considered

- **Keep both full-suite runs:** preserves current safety but repeats costly work.
  Retain it as the fallback whenever equivalence is unproved.
- **Unconditionally omit the sharded suite or trust `suite_tree`:** cheaper, but
  cannot prove that the final candidate satisfied its configured validation.
- **Instrument sharded workers immediately:** could remove the serial bottleneck,
  but expands scope into worker aggregation and incomplete-observation handling.
  Defer until stage measurements justify it; a parent hook cannot observe workers.
- **Restart completion from the beginning:** simple but repeats successful work
  after unrelated push/cleanup failures.
- **Close before delivery and delegate every failed push to recovery:** prematurely
  ends a session that can do its own retry and adds a recovery-owner dependency to
  ordinary completion. Rejected by the operator in favor of staying open through push and
  cleanup. Crash/stop recovery remains necessary for interrupted execution.
- **Add another completion command and cleanup implementation:** duplicates an
  existing lifecycle and recreates the divergence WI-0312 exists to remove.

## Consequences

- WI-0325 establishes safe validation reuse and stage measurements first.
- WI-0326 depends on WI-0325 and adds resumable completion, integrating WI-0312's
  shared cleanup. Existing WI-0312 cleanup work need not wait for validation reuse.
- ADR acceptance is satisfied. WI-0325 and WI-0326 remain implementation work,
  with WI-0326 depending on WI-0325. The WI-0312 amendment preserves its existing
  authorized convergence work.
- Receipts become recovery-critical state: their persistence, identity checks and
  reconciliation require failure-injection tests, not success-path tests alone.
- Acceptance includes failed/incomplete/mismatched validation refusal, relevant
  changes after rebase, crash windows around stage effects, push-only retry,
  cleanup-only retry, repeated completion and reused-lane safety. Assert that push
  and cleanup failures leave the active session open, successful close occurs only
  after both finish, and crash/explicit-stop records never claim successful close.
- Implementation must update the current early `cmd_end` stamp/banner and associated
  reservation/runtime teardown ordering, tests and completion instructions together.
  Preparation is not termination; capacity must not be advertised as free while the
  active session still owns retries. Preserve recovery evidence through cleanup and
  verify final close-state persistence without a recursive completion cycle.
- Focused implementation tests may precede the final candidate gate; extra full
  suites need changed inputs, actual failure or a distinct verification purpose.
- No implementation, canon change, runtime-instruction rollout or new polling item
  is performed by creating these planning records.

## References

- The outside reviewer's self-contained September 9 proposal (withheld working record) in the shared data root (gitignored), including its relationship to the September 7 gate-measurement proposal.
- [ADR-0056: Merge gate](0056-session-branch-gated-trunk.md).
- [ADR-0058: Isolated candidate gate](0058-land-per-session-with-an-isolated-gate.md).
- [ADR-0114: Serialized land gate](0114-the-land-gate-is-serialized-one-at-a-time.md).
- [ADR-0117: Measured diff classification](0117-a-land-classifies-its-own-diff-before-it-gates.md).
- [ADR-0119: Gate isolation](0119-the-gates-three-side-doors-are-shut.md).
- Shared work-item store: WI-0310, WI-0312, WI-0325 and WI-0326 (`poga work show <id>`).
