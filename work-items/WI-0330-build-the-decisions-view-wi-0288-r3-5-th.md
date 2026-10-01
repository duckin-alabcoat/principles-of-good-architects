# WI-0330: Build the decisions view -- WI-0288 R3 (5), the one half of the ruling still unbuilt

- status: done
- section: next
- blocked-by: 
- group: 
- source: proposed-edits/federation-arch/pending/2026-09-04-consultant-finish-line-tests-and-scoreboard.md
- impact: feature
- version: 7.1.0

Split from WI-0288 on 2026-09-10 by the consultant so it can be dispatched: WI-0288 (1)-(4) are built (merge closes by default, dispatched-lane turn budget, wave cap, readiness predicate -- journal 20260907-c4ce note 3), and WI-0288 itself is refused by the readiness gate for quoting its own predicate. This item is WI-0288 R3 (5) only. Build a read-only decisions view over sessions/journal/*.md: the decisions recorded across sessions since a date, reviewable as one batch. Design and measurement are in WI-0274 item (2); curate/metrics.py already has _ledger_section and DECISION_HEADINGS to reuse. Join on the SESSION, not the grant id (WI-0274 measured why: most recorded decisions carry no grant). Flags: --since DATE (default: the last OPS-0007 run, else 7 days) and --json. Output: one row per decision -- journal id, date, item ids cited, the decision line, and who decided (architect or the operator). Acceptance: (a) the view exists as a session.py verb (decisions) with a poga alias, reads only and writes nothing; (b) run against the journals since 2026-09-06 it lists every decision recorded under a DECISION_HEADINGS section, proven by a test over a fixture journal carrying three decisions and one distractor heading; (c) --json emits the same rows and the test asserts it; (d) the OPS-0007 item body gains one line naming this verb as the pre-sort input for bin B. Out of scope: any gate, any write, any change to what counts as a decision heading. When this lands, WI-0288 closes as done with a note pointing here.
