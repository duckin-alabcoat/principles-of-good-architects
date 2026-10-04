# WI-0380: poga work status refuses done-to-open and prints the mint-fresh remedy (ADR-0139 enforcement); ships in the next substrate push

- status: done
- section: next
- blocked-by: 
- group: 
- source: sessions/agenda-2026-09-18-findings-review.md
- impact: fix
- migration: yes
- scope: 
- version: 7.4.0

Bin B item B14 from the OPS-0007 pack for 2026-09-18 (revision 2), section 5.
From WI-0343's close-out. This one is a DECISION STATED, not a finding: the sitting
confirms or overrides it.

ADR-0139 says done stays done. It has NO ENFORCEMENT: poga work status --status open
silently reopens a done item today.

DECISION: build the refusal in sessionlib/store.py now and ship it in the next substrate
push. It prints the mint-fresh remedy rather than just refusing.

REJECTED: waiting for the first violation. The store already holds one live example of
the shape -- WI-0264 to WI-0250 -- and a refusal that costs one print is cheaper than the
audit that has to find a silent reopen after the fact.

ACCEPTANCE: a done-to-open transition is refused; the refusal names ADR-0139 and prints
the mint-fresh remedy; a test asserts both the refusal AND the remedy text, since a
refusal without the remedy just moves the confusion.

MIGRATION: YES. This changes behaviour every converged member depends on, so it rides the
next substrate push rather than landing silently here. Note the interaction the pack
records under B18: standard-source.md's work-item section never says done is terminal, so
ADR-0139 binds the federation ONLY until that sentence ships. Shipping this refusal
without that sentence enforces a rule the members were never told.
