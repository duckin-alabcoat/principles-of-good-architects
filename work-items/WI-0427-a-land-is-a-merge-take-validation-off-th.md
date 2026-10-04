# WI-0427: A land is a merge — take validation off the land path (ADR-0148)

- status: done
- section: next
- blocked-by: 
- group: 
- source: proposed-edits/federation-arch/pending/2026-09-25-consultant-a-land-is-a-merge.md
- impact: fix
- scope: harness
- version: 7.8.0

Source: proposed-edits/federation-arch/pending/2026-09-25-consultant-a-land-is-a-merge.md (D1-D7). the operator, 2026-09-25, in-session: "go — start now"; fix landing before any fan-out. ADR-0148.

A land is a merge: the land verb checks, merges, pushes and never runs the suite. Validation is the lane's, bound to its tree (verdict keyed on the tree with bookkeeping paths removed). Bookkeeping-only diffs are gate-neutral by a static allowlist (prerequisite: the suite is hermetic against the live store, with a guard). A moved land rebases and lands without re-validating. A background trunk check, owned by the land verb, one at a time and coalescing, runs the suite on the new trunk after each land; red refuses the next code land with the failing test names and proposes the revert. The nightly derive no longer disables anything on the land path. FL7 measures total_seconds p90 over 7 days, split code/bookkeeping.

ACCEPTANCE: measured on devbox from .git/land-receipts.jsonl, not in-process — (1) a bookkeeping-only land totals <= 30 s; (2) a code land whose lane has a green verdict for its tree totals <= 60 s; (3) a moved land runs no suite (receipt shows validate 0 and the reason); (4) planted semantic conflict — two lanes each green alone, red together — both land, the trunk check goes red within one suite run, names the test, refuses the next land and proposes reverting the second; (5) with the nightly derive red/stale, lands 1 and 2 still meet their times; (6) the guard test fails when a test is made to read the live work-items/ directory; (7) acceptance is proven by a disposable bookkeeping land and a disposable code land AFTER this item's own land, timed by their receipts.

FILES: sessionlib/land.py, sessionlib/lanes.py, sessionlib/config.py, sessionlib/hooks.py, curate/finish_line.py, curate/run_suite.py, tests/, adr/0148-a-land-is-a-merge.md, adr/README.md, ops-items/OPS-0009-derive-the-gate-input-record-on-main-nig.md

Note 2026-09-25 (acceptance 1, disposable bookkeeping land): the item landed at 49d3cea under its own code in 8.4 s end-to-end (suite runs at land: 0; the lane verdict came from a full `poga test`). This note is the bookkeeping-only land that times acceptance 1.
