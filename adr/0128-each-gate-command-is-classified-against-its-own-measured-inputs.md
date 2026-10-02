# ADR-0128: Each gate command is classified against its own measured inputs

**Status:** Accepted
**Date:** 2026-09-11
**Deciders:** Federation Architect (WI-0347, approved as an item by the operator at the OPS-0007 first run, 2026-09-11)

## Context

[ADR-0117](0117-a-land-classifies-its-own-diff-before-it-gates.md) D1 established that
the land gate's input set is **measured**, never listed: `curate/gate_inputs.py` runs the
suite under `sys.addaudithook` and records every repo-relative path it opened, and
`_classify_gate_neutral` skips the gate when a lane's diff touches none of them.

That design measured **one** command and applied the result to **all** of them.
`session.config.json`'s `gate` list holds seven:

    python3 session.py wi-check
    python3 curate/run_suite.py
    python3 curate/distill.py --check
    python3 curate/standardize.py --check
    python3 curate/gen_settings.py --check
    python3 curate/check_substrate_docs.py --check
    python3 curate/check_citations.py --check

and the probe measured none of them — it measured `python3 -m unittest discover -s tests`,
which is the same *test set* the second command runs but is not a command the gate
invokes. The record therefore held a single `read_prefixes` list that the land used to
decide whether to skip the whole gate.

**Why that is a defect and not an inaccuracy.** The classification is a skip when a diff
touches NO measured prefix. So an input that nobody measured does not make the gate
cautious — it makes it skip. The failure mode is a check that should have run and did
not, reported as success.

**It held by coincidence, and the coincidence failed twice.** The suite happens to read
most of what the other six read, so the one shared set was usually a superset. Twice it
was not, and both times a person had to notice:

| found | command | prefix the suite does not read |
|---|---|---|
| WI-0307 | `session.py wi-check` | `work-items/`, `ops-items/` |
| WI-0342 | `curate/check_citations.py --check` | `adr/` |

Each was repaired by hand-declaring the missing prefix into the one shared set
(`NON_SUITE_READ_PREFIXES`). That fixes the instance and leaves the class where it was
([`retire-the-class-not-the-instance`](../habits/master.md#retire-the-class-not-the-instance)):
the next command added to the gate is the next near-miss, and it is only a *near* miss if
somebody happens to think of it in the same session. `curate/gate_inputs.py` said so
itself — "the honest fix one layer up is per-command input sets … left as a finding
rather than smuggled in here."

**What changed to make the fix available.** [ADR-0127](0127-the-gate-input-measurement-follows-its-own-subprocesses.md)
(WI-0337) shipped `curate/childaudit/` — a `sitecustomize` shim carried to descendants on
`PYTHONPATH`, so a spawned process measures itself. It was built to close the
subprocess blind spot inside the suite. It is also, unchanged, the mechanism for
measuring a gate command that is not the suite at all.

**Measured before deciding**, on this repo at `51c7d38b`, each command spawned under the
shim with `POGA_GATE=1`:

| command | wall | reports | torn | prefixes |
|---|---|---|---|---|
| `session.py wi-check` | 1.5s | 1 | 0 | 5 |
| `curate/distill.py --check` | <0.1s | 1 | 0 | 5 |
| `curate/standardize.py --check` | <0.1s | 1 | 0 | 4 |
| `curate/gen_settings.py --check` | <0.1s | 1 | 0 | 4 |
| `curate/check_substrate_docs.py --check` | 0.2s | 1 | 0 | 6 |
| `curate/check_citations.py --check` | 0.2s | 1 | 0 | 20 |
| `curate/run_suite.py` | 67.2s | 814 | 0 | 38 |

The last row is the one that settles the design question. `curate/run_suite.py` is the
command the gate actually runs, and measuring it under the shim returned **exactly** the
38 prefixes the serial in-process probe returns — no prefix in either direction — in 67.2s
against roughly 300s. It also measured all five prefixes that were hand-declared, so the
declarations are now redundant with a measurement rather than standing in for one.

## Decision

**D1 — The record carries one measured input set per configured gate command.**
`gate-inputs.json` gains a `commands` block: one entry per command in the `gate` list,
each with the prefixes that command was measured to read, and a `measured` flag.

**D2 — The subject list comes from `session.config.json`, never from the record.**
The record is a *claim about* the gate list; the gate list is the authority
([`derive-a-checks-subjects-from-the-authority`](../habits/master.md#derive-a-checks-subjects-from-the-authority)).
A command added between two derives is in the next nightly gate and in no record, and the only
safe reading of that gap is that it runs.

**D3 — A command runs unless the record measured it and the diff misses its own set.**
Every other shape runs the command: no usable record, no `commands` block, an entry with
`measured: false`, an entry with an empty prefix list, a command the record does not
mention, an unreadable diff, an empty diff, a stale record. `_gate_skip_plan` returns the
set of skippable commands; an empty plan runs the gate whole, which is exactly ADR-0117
D3's fail-closed rule applied per command instead of once.

**D4 — A failed measurement is `measured: false`, never an empty set.** A check that
exits nonzero has read a *prefix* of its inputs — the ones it reached before failing — and
recording those as its input set would license skips on everything it never got to.
`measured: false` and `read_prefixes: []` must not classify alike, because the land reads
a file rather than this deriver, and a future derive may record a partial read beside the
flag.

**D5 — The serial probe stays, and the suite command inherits its measurement.**
The in-process probe still produces the record's `verdict` (test ids, counts, skips) and
still carries the `refusal()` floors that answer "is this a measurement at all". Its set
is unioned into the suite command's own. Measured today the union adds nothing — the two
sets are identical — so this is insurance against a future divergence, and it can only
ever make that command run more often. [ADR-0124](0124-the-land-lock-holds-the-merge-not-the-validation.md)
D3 keeps the derive out of the land path; this adds ~70s to a nightly nobody waits on.

**D6 — The declared prefixes go on every command, and shrink in meaning not in size.**
`NON_SUITE_READ_PREFIXES` keeps all five members but its justification narrows to the two
that still need one: `sessionlib/` and `interpreter.py` are read by the parent in the
import-time window before any hook exists, a window *every* measured command has, so they
attach to all of them. `work-items/`, `ops-items/` and `adr/` are now measured by the
commands that actually read them; they are kept declared because removing a prefix from
the set is a permissive change and this item is a safety item.

**D7 — Schema 3, and a schema-2 record classifies nothing.** A schema-2 record cannot
answer the question the new classifier asks — it has no per-command statement to read
conservatively — so it is not read at all and every land gates in full until the next
derive. Same reasoning as ADR-0127 D5's bump, for the same reason: retire the generation
at once rather than age it out one nightly at a time.

**D8 — A skip is a receipt.** ADR-0117 D5 per command: `_run_gate` prints
`gate:    skipped — <command> (diff touches none of its measured inputs)` in the same list
as the commands that ran, so anything counting `gate:    ok` lines still sees them.

## Alternatives Considered

**Point the deriver at `curate/run_suite.py` and retire the serial probe.** This is the
literal reading of WI-0347's acceptance and it is now technically sound — measured above,
same 38 prefixes, 4.5× faster. Rejected for this pass because it requires deleting
[WI-0338's guard](../tests/test_gate_input_deriver_is_serial.py), which landed hours
earlier with eight tests and a careful rationale, and the saving is on a nightly nobody
waits for. The premise that guard rests on — "an audit hook does not follow children" —
is still true of the *hook*; ADR-0127's shim is a second mechanism beside it, not a
refutation. **The finding is recorded rather than acted on:** the serial probe is now
provably redundant *for input measurement*, and retiring it deliberately is its own item.

**Keep one set and keep hand-declaring.** What we have been doing. It is a chore that
silently lapses, and its lapse is permissive. Two instances already.

**Let each gate command declare its own inputs in `session.config.json`.** Cheaper, and
wrong in the way ADR-0117 D1 already settled: a hand-listed set is a person's belief about
what a program reads, and the belief is what keeps being wrong.

**Compute the neutral set instead of the read set.** Rejected by ADR-0117 D1 and rejected
again here, one level down: a path nobody has measured would default to safe per command
instead of per gate, which multiplies the error rather than fixing it.

## Consequences

- The class closes. A gate command added to `session.config.json` gets its inputs
  measured by the next derive with no edit to any deriver, and until then it runs on
  every land. Neither outcome needs anybody to remember anything.
- **Almost nothing changes operationally today.** The suite's measured set is currently a
  strict superset of all six other commands' sets, so the commands that become skippable
  are the narrow checks on diffs that were already gating. This is a correctness change,
  not a speed one, and claiming otherwise would be claiming a benefit that was measured
  not to exist.
- More skips are handed out in aggregate, each licensed by a measurement of the command
  being skipped rather than of a different one. The evidence per skip is strictly better;
  the count is higher. That trade is the decision.
- Until the next OPS-0009 run publishes a schema-3 record, every land gates in full.
  Safe, slower, and self-healing overnight.
- `_run_gate` gains a `skip` parameter. It defaults to empty, so preflight — which asks
  "is the gate green in this checkout", a question no diff narrows — is unchanged.
- Downstream: retire the serial probe (see Alternatives); and `child_audit.unaudited`
  now has a per-command analogue worth surfacing on the board.

## References

- [ADR-0117](0117-a-land-classifies-its-own-diff-before-it-gates.md) — the classifier and
  the measured input set this refines.
- [ADR-0127](0127-the-gate-input-measurement-follows-its-own-subprocesses.md) — the child
  audit that made per-command measurement possible.
- [ADR-0124](0124-the-land-lock-holds-the-merge-not-the-validation.md) D2/D3 — the derive
  is outside the land path; a stale record warns and disqualifies a skip.
- [ADR-0119](0119-the-gates-three-side-doors-are-shut.md) D4 — `POGA_GATE=1`, the
  environment each command must be measured in.
- WI-0307, WI-0342 — the two instances hand-declared; WI-0337, WI-0338 — the child audit
  and the serial guard; WI-0347 — this item.
