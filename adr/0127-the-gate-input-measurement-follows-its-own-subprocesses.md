# ADR-0127: The gate-input measurement follows its own subprocesses

**Status:** Proposed
**Date:** 2026-09-11
**Deciders:** Federation Architect (WI-0337, dispatched lane)

## Context

[ADR-0117](0117-a-land-classifies-its-own-diff-before-it-gates.md) D1 made the land gate's
input set **measured, never listed**: `curate/gate_inputs.py --derive` installs a
`sys.addaudithook`, runs the suite, and records every repo-relative path it opened.
`_classify_gate_neutral` then skips the whole gate for a lane whose diff touches no
measured prefix.

ADR-0117 named the gap in its own Consequences, in as many words:

> **The hook sees only the probe's own process.** `sys.addaudithook` is per-interpreter,
> so reads performed by processes the suite *spawns* … do not appear in the record. …
> this is an under-measurement, not a neutral simplification, and under-measuring is the
> direction that produces a wrong skip.

It was named and then not closed, and nothing in `curate/gate_inputs.py` carried a note
of it — `NON_SUITE_READ_PREFIXES`, the file's only mitigation, was added for two
*different* blind spots (a gate command the probe does not run; a read in the import-time
window before the hook exists), and its presence read as coverage. OPS-0007's first run
found this in September 2026 and filed **WI-0337**.

The numbers: **67 of 112 test modules spawn subprocesses**, several with `cwd` at the repo
root — `bootstrap.py --adopt`, `./poga work`, `session.py preflight`. Every repo file
those children opened was read by the suite and recorded by nothing.

**The direction is the whole point.** The classifier skips when NO changed path is in the
measured set, so a missing read does not make the land cautious — it makes it skip. The
end state is a suite that should have run and did not, on a record that looks well-formed
and confident either way. This is `declare-what-a-check-assumes` turned on the deriver.

## Decision

**D1 — Measure the children, do not declare the gap.** The probe puts a `sitecustomize`
shim (`curate/childaudit/`) on its children's `PYTHONPATH`. `site` imports the first
`sitecustomize` on `sys.path` in every Python process that starts, and `PYTHONPATH` is
inherited across `exec`, so every Python *descendant* of the probe — grandchildren
included, with no bookkeeping — installs the same audit hook and writes what it read into
a collection directory the probe unions in. The read-classification rule lives in one
module used by both halves, so a single measurement cannot classify its own halves by two
different rules.

**D2 — The channel proves itself before the measurement is trusted.** A canary child with
known reads (one absolute, one relative after a `chdir`) runs *before* the suite. If its
reads do not come back, the derive refuses and writes nothing. A child-audit that silently
fails to arm does not merely stop measuring children — it certifies the parent's half as
the complete input set, which is the original defect wearing a receipt
([`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)).

**D3 — What could not be reached is named, derived from the spawn event.** The probe
watches `subprocess.Popen` and classifies each spawn: non-Python, `-I`/`-E`/`-S`, or an
explicit `env=` without the shim. These are facts readable off the call, not suspicions
about it, and they land in the record as `child_audit.unaudited`. A repo script's *exec* is
recorded as the read it is — for `./poga` or a `drills/*.sh` it is the only read we ever
get. **The only accounting that refuses is silence**: expected reports > 0 and reports == 0
means the shim did not arm. An equality is not available (a grandchild reports without the
parent seeing its spawn) and a shortfall has innocent causes (a child a test kills).

**D4 — The refusal moves to the WRITE side.** Every prior guard sat on the reading side and
took the record's own word for it, so a probe that discovered no tests wrote a record with
a schema, a timestamp, a commit and a handful of prefixes — and passed every "is this
usable?" check in the repo, while meaning that nearly every diff is gate-neutral. `refusal()`
refuses to write below a floor: the child-audit receipt, a test-count floor, a measured-prefix
floor, and a **sentinel** — `tests/` must be among the *measured* prefixes, because
`unittest discover -s tests` cannot complete without reading it, so a measurement without it
did not observe the run it claims to have measured. A declaration cannot vouch for a hook
that never fired, so the sentinel is checked against measured prefixes, never declared ones.

**D5 — Schema 2, and the reader's check is structural.** `sessionlib/land.py` refuses any
record with no `child_audit` receipt. Not a threshold — that file ships to every member and
a floor calibrated to the federation's 3,500-test suite is a federation-shaped number in
fleet code, inert (no member has a deriver) and therefore rotting unnoticed. The numeric
floors live only in the deriver. The schema bump retires every schema-1 record at once:
each one was derived blind to every subprocess, so each under-reports, and under-reporting
is what licenses a skip. Until a fresh derive lands, every land gates.

**D6 — A bare directory read is promoted to its tracked prefix.** `top_prefix` renders
`tests/x.py` as `tests/` and a bare `tests` — what `os.scandir` of a directory produces — as
`tests`. `tracked` only ever holds `dir/` forms and the land only ever classifies *file*
paths out of a diff, so **every top-level directory listing the suite has ever done was
worth nothing**. ADR-0117 D2 says membership is decided at the directory, and the deriver's
own docstring says a glob makes a whole directory an input; the code dropped exactly that
evidence. The promotion can only ever *add* a prefix, and only one git already tracks.

## Alternatives Considered

- **Declare the gap and refuse to classify anything gate-neutral.** WI-0337's acceptance
  allowed this. Rejected: it retires the ADR-0117 speedup entirely — every land back to a
  five-minute suite — to fix a gap that is measurable. It survives as the *failure* path
  (D2), which is where it belongs: costing everything when the mechanism is broken and
  nothing when it works.
- **Hand-declare the subprocess-heavy modules' fixture roots**, the other follow-on ADR-0117
  named. Rejected: the set to declare is "whatever 67 modules happen to open", which nobody
  can assert, and a hand-list is the failure D1 exists to prevent.
- **A strict spawn/report equality.** Rejected: grandchildren report without the parent
  seeing their spawn, so reports legitimately exceed expectations; the check would be noise
  on every run and would wedge the derive.
- **Leave the schema at 1 and let OPS-0009's nightly derive age the old records out.**
  Rejected: that leaves a known-incomplete record licensing skips for as long as the nightly
  takes, and the nightly obligation can lapse.
- **Shadow `sitecustomize` without chaining.** Rejected: some Python distributions ship one inside
  the stdlib that reshuffles `sys.path`, rewrites `sys.executable` and fixes up
  `site.PREFIXES`. Swallowing it changes what a measured child *is*, and a measurement that
  alters its subject is not a measurement.

## Consequences

- **The measured input set went 27 prefixes → 38** at the deriving commit. Twelve the old
  record never carried: `adr/`, `audits/`, `comms/`, `habits/`, `principles/`, `outbox/`,
  `releases/`, `ROADMAP.md`, `federation-arch.md`, `session-handoff.md`,
  `dev-environments.json`, `POGA-OVERVIEW-AND-SCALABILITY.md`. **This answers WI-0337's
  COULD-NOT-DETERMINE with a yes**: a comms-only, role-doc-only or ADR-only land classified
  gate-neutral and skipped the entire suite, and those are diff shapes this repo produces
  constantly.
- **The widening came from D6, not from D1.** `child_audit.child_only_prefixes` came back
  **empty**: every prefix the children read, the parent had already read. Said plainly rather
  than smoothed over — the child channel's value today is that `sessionlib/` and
  `interpreter.py` are read by construction rather than incidentally, and that a future
  child-only read is caught. It did not find new inputs on this suite. D6 was found *by* D1's
  acceptance test failing, which is the part of this that generalises.
- **Materially fewer lands are gate-neutral, and they pay the suite.** That is the
  fail-closed direction ADR-0117 argues for, but it is a visible slowdown and it is the cost
  of this ADR.
- **This removes the reason WI-0338's guard was built on, and does not remove the guard.**
  `tests/test_gate_input_deriver_is_serial.py` landed on the trunk while this work was in a
  lane, and it enforces — structurally, after the rule had lived in three prose places and been
  enforced in none — that the deriver names the serial command and spawns nothing after the hook
  is armed. Its stated premise is *"an audit hook does not follow subprocesses, so a sharded
  derive would measure almost nothing"*, and that premise is now false. **The guard still stands
  on its own merits and this ADR does not weaken it**: sharding moves essentially the whole
  measurement out of the probe's process into N children, which changes the shape of the evidence
  the land classifies on — and WI-0338's negative control, which proves an unassisted hook does
  not follow a child, remains true and remains the thing the shim exists to work around. Sharding
  the derive is now a live *option* rather than an impossibility, and taking it means retiring
  that guard deliberately, in its own item. The three prose copies WI-0338 named have been
  corrected here rather than left asserting a reason that no longer holds
  ([`retire-the-class-not-the-instance`](../habits/master.md#retire-the-class-not-the-instance)).
- **Not closed, and not hidden:** 19,056 of 39,937 spawns in a full derive are unreachable by
  construction (`/bin/sh`, `git`, `tmux`). Their execs are recorded; anything they open is
  not. The honest fix remains per-command input sets, one layer up.
- **Not closed:** the measured set is not stable across derives. `STATUS.md` was in the
  previous record's read paths and not in this one; whether a path is measured depends on test
  ordering and fixture state, so a path measured yesterday can silently vanish today and a
  land becomes permissive for it. The mechanism has no notion of "this was an input last time".

## References

- [ADR-0117](0117-a-land-classifies-its-own-diff-before-it-gates.md) — the decision this
  extends; its Consequences name this gap and ask for exactly this follow-on.
- [ADR-0124](0124-the-land-lock-holds-the-merge-not-the-validation.md) — the derive is out of
  the land path and runs on the OPS-0009 nightly cadence; a stale record warns and
  disqualifies the skip.
- WI-0337 (found by OPS-0007's first run), WI-0294, WI-0295, WI-0307, WI-0328, WI-0342.
- [`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes),
  [`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability),
  [`verify-in-the-created-configuration`](../habits/master.md#verify-in-the-created-configuration).
