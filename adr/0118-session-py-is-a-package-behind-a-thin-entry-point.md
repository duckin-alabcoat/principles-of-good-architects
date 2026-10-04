# ADR-0118 — `session.py` is a package behind a thin entry point

- **Status:** Accepted
- **Reality:** Built
- **Date:** 2026-09-06
- **Supersedes:** —
- **Related:** [ADR-0020](0020-session-rituals-are-a-code-harness.md) (the harness is code),
  [ADR-0022](0022-architect-ingest-distillation-and-code-channel.md) (the code channel),
  ADR-0047 (withheld) (member self-check),
  [ADR-0070](0070-worktree-lanes-are-fleet-substrate.md) (byte-identical fleet substrate)

## Context

`session.py` had reached **25,620 lines** in one file: 668 top-level functions, 9 classes,
245 module-level constants. It is the shared harness — byte-identical in every converged
member repo, pushed by `curate/push-substrate.py`, and the subject of ~20 `standard_check.py`
capability detectors. Every session of every Architect in the fleet loads it.

One file that size is a working-surface problem, not an aesthetic one: no reader can hold it,
every edit re-reads it, and concerns that have nothing to do with each other (the land gate,
the work-item store, the coordination store) sit interleaved with no boundary a tool can see.

The obvious move — split it into a package — has one hard constraint that shapes the whole
decision, and it is not obvious until measured.

## The constraint that decides the design

Two measurements, both taken before any code moved:

1. **The call graph has a 123-symbol strongly-connected component.** Land, coordination,
   journal, lanes and start/end call each other mutually. There is no layering; a package of
   modules importing each other with `from .land import _run_gate` cannot be ordered.

2. **The test suite patches module globals 380+ times.** `session.ROOT = tmpdir` appears 185
   times, `session.CFG = ...` 58 times, plus `JOURNAL_DIR`, `SESSION_STATE_DIR`, `ARCHIVE`,
   `CFG_RAW` and others. Every one of those assignments expects *every* function to observe
   the new value.

Real submodules each carry their own `__dict__`. Under them, `session.ROOT = tmpdir` would
rebind the name in one namespace while functions living in `sessionlib/land.py` continued to
read the old one — and the failure is silent, because the assignment still succeeds. Making
that work would mean rewriting call sites (`ROOT` → `config.ROOT`, ~thousands of sites) or
rewriting the tests. Both are forbidden: the item is *move code, do not rewrite*, and no test
may be deleted or weakened.

So the requirement is a split that changes **file layout only** and leaves **name resolution
exactly as it was**.

## Decision

`session.py` stays at the repo root as a ~120-line entry point and remains the fleet's
byte-identical file. It carries the module docstring, resolves its own directory, and does:

```python
import sessionlib
sessionlib.load(globals())
```

`sessionlib/` holds the code in seven parts, grouped by concern:

| Part | Concern |
|---|---|
| `config.py` | config and paths — the imports, every module-level constant, path/config resolution |
| `coord.py` | the coordination store — claims, leases, allocations, locks, authorization |
| `store.py` | the work-item and ops store |
| `journal.py` | journals and the compiled views (handoff, ROADMAP, STATUS) |
| `land.py` | land and gate — CAS trunk advance, checkout sync, main materialization |
| `lanes.py` | dispatch and lanes — worktrees, reap, attach, notify, supervise |
| `hooks.py` | hooks and start/end — the PreToolUse guards, preflight, the CLI surface |

`sessionlib.load(ns)` compiles each part and `exec`s it into the namespace it is handed —
the `session` module's own globals. **The parts share one namespace.** That is the load-bearing
property: cross-part calls resolve at call time through the same dict they always did, and
`session.<GLOBAL> = x` reaches every part, because there is only one place for a global to live.
Semantically this is identical to the monolith — it *is* the monolith, stored in seven files.

**Ordering.** Because the parts execute in sequence, anything evaluated at exec time must
already be bound: module-level constant values, `def` default arguments, decorators, class
bases. Two rules keep that true, and a generated check enforces both:

- Module-level constants live in `config.py` (loaded first), except the handful whose value
  *calls* their own concern's functions (`_ROADMAP_REGIONS`, `PREFLIGHT_GATE_CHECKS`,
  `PREFLIGHT_REPAIRS`), which sit with those functions.
- Function *bodies* are unconstrained — they resolve at call time — so the concern grouping is
  free to be what a reader would want.

Each part carries `from __future__ import annotations` explicitly. That directive is
per-compilation-unit; the monolith's single import could not carry across a split, and without
it `Path | None` annotations fail on the Python 3.9 floor that sessions actually run.

## Consequences

**The substrate is now a directory, and two things had to be taught that.**

`curate/push-substrate.py` derives the `sessionlib/*.py` list from the federation's own tree
rather than hardcoding names, so a future module reaches the fleet with no edit here. The
package files join the byte-identical set: compared, written, committed by pathspec, and
(unlike `poga`) not executable.

`standard_check.py` was the sharper risk. Roughly 20 detectors read `session.py` and tested for
a substring — `"def run_compile" in src`, `"def _complete_lazy_start" in src`. After the split
those all read a 121-line stub, find nothing, and report the capability **absent** — every
member of the fleet reading below floor, from a check that still looked well-formed and
confident. That is `declare-what-a-check-assumes` exactly: the detector's assumption about its
subject's shape silently became false. One helper now returns `session.py` plus every
`sessionlib/*.py` concatenated; no detector's substring changed. It returns just `session.py`
when the directory is absent, so a member that has not yet received the package reads exactly
as before.

The same defect class lived in the test helpers — fixtures that copy `session.py` into a
synthetic member repo, and linters that parse it for verbs and advice strings. Both are routed
through one shared helper for the same reason.

**Costs, stated plainly.** The parts are not independently importable modules; `import
sessionlib.land` is not the interface, and a reader has to know that `sessionlib/__init__.py`
is an assembler rather than a package root. In exchange, the split needed no call-site edit, no
test change of meaning, and carries a mechanical proof that it moved code rather than rewriting
it. Should the fleet ever want true modules, the boundaries this ADR draws are where the seams
already are — that migration would be a rewrite of call sites, and a different decision.

`config.py` is the largest part (~7,000 lines) because it absorbs every module-level constant
plus the shared path/IO helpers. It is a genuine catch-all and the next natural thing to split.

**Verification.** The generator asserts a partition: every body line of the original lands in
exactly one part, none twice, none dropped — the proof that this was a move. It also verifies
that every exec-time dependency resolves under the load order, and reports violations rather
than discovering them at import. Both interpreters the fleet runs on are exercised: the
system `python3` at the 3.9 floor that sessions use, and a current Python 3 release.

**Not in scope.** Selecting tests by changed module is the obvious follow-on now that the
harness has module boundaries — it is a separate item, deliberately not taken here.
