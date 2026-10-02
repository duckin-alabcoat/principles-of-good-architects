# ADR-0146: Rehearsal evidence and host evidence are produced separately, and neither substitutes for the other

**Status:** Proposed
**Date:** 2026-09-18
**Deciders:** Federation Architect (drafted); the operator (acceptance owed)

## Context

[WI-0366](../work-items/) closes the federation's production wave. Its acceptance has two
halves that look alike on paper and are not alike at all:

- a **local rehearsal** — old installation → migration release → second release, with both
  mail directions, concurrent producers, a lost publication confirmation, a worker restart,
  duplicate delivery and rollback — which any machine can run against fixtures; and
- **host evidence** — the loaded `WorkingDirectory` and `ProgramArguments` of the three
  federation jobs, the running release, the resolved `poga` target, member lookup, worker
  liveness, delivered probe messages with acknowledgments, and a second approved release
  that nobody touched the Runner to get.

[`curate/mailacceptance.py`](../curate/mailacceptance.py) already encodes that split and
refuses to conflate the two: `check()` reports `local_rehearsal` separately from
`live_gaps`, and returns `accepted: false` and `ops_observation_started_at: null`
unconditionally, whatever it is handed.

What was missing was a **producer for the local half**. The ledger requires a rehearsal
record carrying a `command`, an `artifact_sha256` naming retained output, and all ten names
in `REHEARSAL_CHECKS` marked `passed`. Nothing emitted one. The only way to supply it was to
write it by hand, which makes the artifact a *claim that a test run happened* rather than a
*result of one* — and it would have passed the checker exactly as well as a real run.

The retirement half had the same shape. `legacy_dependencies_inspected` must be an empty
list of dependencies remaining after launchd/cron/`poga` inspection. Nothing in the
repository enumerated any of those: every probe was by known label — `unit_target`,
`diagnose._launchctl`, `mailacceptance.snapshot` all ask `launchctl print <domain>/<label>`
and none asks what else is loaded. A requirement whose only possible answer is typed in by
its reviewer is not a check.

## Decision

**Each half of WI-0366's evidence has exactly one producer, and each producer refuses to
answer the other half's question.**

| Half | Producer | Verdict | Cannot |
|---|---|---|---|
| Local rehearsal | `curate/mailrehearsal.py run` | per-test exit codes → `checks` map + transcript digest | run on, or say anything about, a live host |
| Host observation | `curate/mailacceptance.py snapshot --live-observation` | read-only probes on the machine itself | be produced anywhere but that machine |
| Retirement inspection | `deploy/legacydeps.py --old-root <path>` | `clean` / `remaining` / `cannot-tell` | retire, disable or delete anything |
| Adjudication | `curate/mailacceptance.py check` | `blocked` / `ready-for-review` | ever emit `accepted` or start OPS-0010 |

Three rules bind all of them.

1. **A verdict is derived from a run, never asserted about one.** `mailrehearsal` maps each
   declared check to the tests that already exercise it and takes their exit status; it does
   not re-implement the scenarios beside the real ones. Its subject list is read from
   `mailacceptance.REHEARSAL_CHECKS`, so a check added there without a mapping is a refusal
   rather than a silently unasked question.

2. **"Could not tell" is never folded into "fine".** A test id that no longer resolves, a
   run reporting a number of tests other than one, and a non-zero exit are three distinct
   failures and each is recorded as itself. `legacydeps` exits 2 when any source could not
   be read, and an incomplete `remaining` list is not offered as a clean one.

3. **A local pass is not a live answer, and the artifacts say so in themselves.** The
   rehearsal record carries a note to that effect; `mailacceptance.check` repeats it in
   `review_required`. Neither program can be handed the other's artifact and produce
   acceptance from it.

**A lane cannot close WI-0366.** The unavailable half is not unavailable by accident — it
requires a promotion, two releases and a host this Architect may not reach (the standing
boundary that a development-machine session never opens a path to the Runner — see
[ADR-0135](0135-a-trunk-clone-is-never-a-process-root.md)). The item stays open on that basis, which is what its own
acceptance clause prescribes: *"Unavailable production evidence keeps this item open/held."*

## Alternatives Considered

- **Let the rehearsal write fresh scenarios of its own.** Rejected. The mechanisms already
  have tests in the modules that own them; a parallel set would be a rehearsal proving its
  own fixtures ([P16](../principles/master.md#p16--avoid-duplication)), and a regression
  would land in the real test while the rehearsal stayed green.
- **One subprocess for the whole rehearsal, parsing `unittest -v`.** Rejected twice over.
  These modules mutate `os.environ` and patch module globals in `setUp`, so a shared
  interpreter makes one test's isolation another's starting state — measured at eighty
  errors in a serial run of `tests/test_federation_migration.py`. And a verdict parsed out
  of a combined transcript is a verdict read off the wrong thing, which is the failure
  [WI-0344](../work-items/)'s guard exists to deny.
- **Add a new key to `deploy/deploy.json` for the evidence step.** Refused by the installed
  version before it runs: `read_contract` validates an incoming tag's contract against the
  schema in the *runner's own* checkout, and that schema sets
  `additionalProperties: false`. A new key invented by a new tag is rejected whatever the
  tag carries.
- **Have `legacydeps` disable what it finds.** Rejected. An inspector that can act is one
  whose refusals a future caller will be tempted to route around, and the retirement clause
  requires the old directory to survive as a recovery archive. Retirement stays a separate,
  deliberate act with the inspection as its precondition.
- **Extend `deploy/retire_clone.py` to cover the federation's own process root.** Rejected.
  It compares a member dev clone against `~/deploy/<system>` and knows nothing about
  launchd, cron or `poga`; `deploy/clone-retirement.json` is a closed list requiring a
  cutover receipt per row, and `tests/test_retire_clone.py` pins it to its two ruled
  systems. Different question, different tool.

## Consequences

- The one acceptance input a machine that cannot reach production *can* supply is now
  produced by a command, with a transcript whose digest is of the bytes actually written.
- `mailacceptance.check`'s `local_gaps` branch is exercised for the first time. Before this,
  every fixture supplied all ten checks as `passed`, so `local_rehearsal: "incomplete"` was
  unreachable and the ten check names were inert strings as far as the suite was concerned.
- The repository gains its first enumeration of *unknown* scheduled jobs. Every probe before
  it asked about a label it already knew, which cannot answer "what else still points at the
  old checkout".
- **A reading is about the machine that took it.** `legacydeps` run on DevBox reports
  DevBox's `poga` link resolving into the working checkout — correct there, and not
  evidence about the Runner. The `--old-root` that matters for acceptance is the Runner's
  retiring root, and only a run on that host answers it.
- Downstream, still owed and not closed by this ADR: the two approved releases, the four
  live observations with return-channel provenance, the operational artifact, the retirement
  act, and OPS-0010's activation from the real timestamp of the second release.

## References

- [ADR-0103](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md) — the promotion path these releases travel.
- [ADR-0132](0132-a-deploy-result-is-published-by-the-machine-that-produced-it.md) — how host evidence travels back.
- [ADR-0135](0135-a-trunk-clone-is-never-a-process-root.md) — the one-writer channel and the sealed process root.
- [`deploy/mail-acceptance.md`](../deploy/mail-acceptance.md) — the artifact schemas.
- [`habits/master.md#declare-what-a-check-assumes`](../habits/master.md) and
  [`#a-close-is-the-banner-not-the-sentence`](../habits/master.md) — the two habits this
  decision is an instance of.
