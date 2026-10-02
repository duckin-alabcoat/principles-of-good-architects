# ADR-0141 — Diagnosis is a read-only collector, and it publishes on a state change rather than answering a request

**Status:** Accepted
**Date:** 2026-09-17
**Session:** ~355 (lane, dispatch D-f408ec)
**Deciders:** Federation Architect (all of it — the separate module, the executor, the publish-don't-request model, the redaction disposal, and the two scrub-gate corrections). Under the operator's standing ruling of 2026-09-13: *"Plumbing, mechanics, schema details, ordering, retries, which of two equivalent fixes: make the decision yourself, record it under Decisions I made without you."* Nothing here changes what the system is for.
**Work item:** WI-0229
**Implements:** Exit C of the 2026-09-03 consultant brief on machine-bound development — *"Ask for diagnostics and receive a bundle (logs, launchd state, ledger) delivered by the mail path. Never open a Runner session to look."*
**Builds on:** [ADR-0103](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md) D1 (the git remote is the only bridge), D4 (the runner is plain code), D5 (the contract), D7 (the ledger is machine-local), D8 (two triggers, one path); [ADR-0132](0132-a-deploy-result-is-published-by-the-machine-that-produced-it.md) (the return path this rides); [ADR-0107](0107-mail-transits-origin-and-every-machine-drains-its-own-share.md) (mail transits origin)
**Supersedes in part:** WI-0229's own in-item decision of session ~241, *"piggyback the mail-poller"* — the premise it rested on expired before it was built (see D2)
**Corrects:** `curate/scrub.py`'s private-ip pattern, which never matched any address in 10.0.0.0/8, and its allowlist test, which could not match three of its own entries
**Reality:** Built — `deploy/diagnose.py`, the sweep hook, the `poga diagnose` verb, 32 tests, 8 of 8 mutations caught

---

## Context

Every question about a deployed system was answered by a person on the Runner. Not because
the facts were hidden — the ledger, `launchctl print`, the unit's log and the contract's
`verify` are four commands to anyone sitting there — but because *sitting there* was the
only way to run them. The 2026-09-03 brief rules that shape a substrate defect and counts
it as one, and WI-0229 is the item that closes it.

The two halves of the problem are not equally hard. Gathering the evidence is ordinary
code. Getting it to devbox is the part the architecture constrains: ADR-0103 D1 forbids
any bridge but `origin`, so the answer has to travel as a git object, and the only
machinery that does that is the mail channel WI-0230 finished in session ~309.

### The premise that had expired

WI-0229 carried a decision made in session ~241: *piggyback the mail-poller*, reasoned
from *"the poller already runs on the Runner under launchd every 600s"* and the observation
that it was the only such executor. Re-probed this session, that is no longer true.
`com.federation.deploy-sweep` is installed and running on the Runner — kickstarted
2026-09-13, recorded on WI-0224's note — so there are now two scheduled executors, and the
one the old decision ruled out on availability grounds is the one that already knows what a
deployed system is.

## Decision

**D1 — The collector is a separate program from the runner, and it cannot write.**
`deploy/diagnose.py`, not a flag on `deploy/runner.py`. Every other function in that file
exists to change this machine — check out a tag, seed state, restart units, roll back — and
a read-only collector living among them is one careless edit away from not being one. The
property that makes this safe to run unattended against production is exactly that it
cannot write, and a separate module is how that stays a property rather than an intention.

It reads the runner rather than re-deriving anything: registry, deploy tree, contract,
ledger and launchd domain all come from the runner's own functions, so there is never a
second, disagreeing answer to *what is deployed here* ([P16](../principles/master.md#p16--avoid-duplication)).

**One function is exempt and says so: the contract's `verify`, which is executed.** It
earns the exception because it is the only probe that answers the question an operator
actually has — everything else reports what is *installed* and what is *loaded*, and a
diagnosis without `verify` can report a running unit whose service is dead. ADR-0103 D5
calls `verify` a post-restart aliveness check, which is a contract about intent and not a
guarantee about side effects, so the bundle reports it under its own heading marked
`executed`, and `--no-verify` produces `skipped` rather than a silent pass.

**D2 — The deploy sweep invokes it, not the mail poller.** This reverses WI-0229's
in-item decision on the ground its reasoning named. Three arguments, in order of weight:

1. **Dependency direction.** Nothing in `curate/` imports `deploy/`; `deploy/runner.py`
   imports `outbox`. Having the poller call a diagnose would create the first edge the
   other way and put deploy knowledge — registries, contracts, trees, launchd domains — in
   the program whose job is moving files.
2. **The sweep already holds everything it needs.** It loads the registry and resolves each
   system's contract and tree on every pass. The poller knows none of that and would have
   to acquire it.
3. **The poller's own refresh would fight it.** `mail-poller.refresh` fast-forwards the
   checkout and declines when the tree is dirty. A writer inside the poller's pass dirties
   the tree the poller is about to refresh.

Cadence is not among the arguments: both run at `StartInterval 600`, so this costs no
latency either way. Rejected: the mail-poller, on the reasoning above; and a third
standalone LaunchAgent, which needs installing on a machine this lane cannot reach and adds
a unit for work an existing unit is already awake to do.

**D3 — It publishes on a state change; it does not answer a request.** The bundle is
posted when a DIGEST of the stable facts moves — deployed tag, per-unit liveness and last
exit code, contract validity, verify exit, ledger status — and not otherwise.

**The digest deliberately excludes the log text**, which is the whole subtlety. Logs gain
lines continuously; a digest containing them would report a change on every 600s pass and
produce 144 tracked files per system per day, burying the record in itself — the argument
[ADR-0132](0132-a-deploy-result-is-published-by-the-machine-that-produced-it.md) already made and won for receipts.
The logs still travel: they are the evidence *in* the bundle, cut at the moment the stable
state moved, which is the moment worth having them from.

**Rejected: a request/response channel from devbox.** It is the shape the brief's wording
suggests — *"ask for diagnostics"* — and it does not serve the session that asks. The round
trip is a push, a ≤600s poll on the Runner, a collection, a push back and a ≤600s poll on
devbox: ten to twenty-five minutes, during which a dispatched lane would be holding a slot
to wait. What actually serves a live session is the answer already being in its mailbox
when it starts, which is what publishing gives and requesting does not. The request path is
recorded as a finding rather than dropped silently; it becomes worth building only if a
question arises that a state change cannot anticipate.

**Rejected: publish every sweep.** Simplest, and useless within a week, for the reason
above.

**D4 — Machine output is redacted before it travels, and the gate behind it is not
bypassed.** The outbox is tracked and pushed, so a bundle is permanent.
`launchctl print` can carry the loaded job's whole environment — `curate/mailacceptance.py`
already refuses to store its raw output for that reason — and a log tail can carry
anything.

So `curate/scrub.py` gains `redact`, and the collector redacts before posting while
`outbox.post`'s scrub gate stays in front of it **unchanged**. That ordering is the point:
the gate becomes an independent check that the redaction worked, rather than a door the
collector argued past. Nothing in `diagnose.py` ever passes `force`, and a scrub refusal
means the bundle is not sent — which is the correct outcome, because it means a class was
missed.

**Redaction has two disposals, because the patterns are two kinds of thing.** A
value-class hit (`10.1.2.3`) is masked in place, keeping the line readable. A
marker-class hit (`api_key =`, `psk:`, `date of birth`) takes the **whole line**, because
the pattern matches a LABEL and the sensitive value follows it. This is not a theoretical
distinction: the first implementation masked in place throughout, and
`api_key = hunter2hunter2` came back as `[[xpi_kxy =]] hunter2hunter2` — the secret intact
one character to the right, and `findings` then reported it clean. **A redactor that
converts a catchable secret into an uncatchable one is worse than none**, because the gate
behind it stops firing.

Rejected: refusing to post a bundle with findings, the way `gate` and `post` do for
authored content. That is right where an author is standing there to fix it and wrong here:
the moment machine output is most likely to carry a stray token is the moment the report is
most worth having, and refusing then deletes the evidence rather than protecting anything.

**D5 — Every probe that could not answer is named.** A `gaps` list, in the bundle and
rendered at the end of every report, and a collection that stopped early says where it
stopped. A bundle that silently omitted the unit it could not read would state *"looked and
found nothing"* in the same shape as *"never looked"* — the collapse
[`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes) exists to
prevent. The first live run of the renderer produced exactly that failure: with no deploy
tree it printed *"Units: none"* and *"Verify: not declared"*, which read as findings about
the system and were findings about nothing having been asked.

**D6 — A failed diagnosis never fails a deploy.** The sweep's job is to deploy. Both the
per-system collection and the collector as a whole are wrapped, logged and swallowed, and
the sweep's exit code is untouched — a launchd job that exits non-zero for a condition no
re-run clears is an alarm channel that gets muted, which is the argument
`curate/mail-poller.py` already makes about its own unroutable bucket.

**D7 — Two corrections to the scrub gate, found while testing D4 and fixed here.**
Both pre-existing, and the second is why the first survived.

- **No address in 10.0.0.0/8 was ever caught.** The alternative read `10\.` where its two
  siblings read `192\.168` and `172\.(…)`; since a `\.` follows the whole group it could
  only match a doubled dot, so `10..1.2` was a hit and `10.1.2.3` was not. It was also one
  octet short, so correcting only the dot masked `10.4.5` out of `10.4.5.6` and left the
  last octet standing. The largest private range there is passed a gate that then reported
  the file clean.
- **Three allowlist entries could never match.** `_ALLOW` was tested against the captured
  span, and the span for `192.168.0.0/16` is `192.168.0.0` — the `/16` is not in it. So
  `the 192.168.0.0/16 range` was REFUSED as a LAN address, and the entries for all three
  documentation ranges were dead. The fix extends the candidate by a trailing CIDR suffix
  before testing, and deliberately does **not** test the whole line: allowlisting per line
  would let one occurrence of the word "example" excuse a real address beside it, a larger
  hole than the one being closed.

**The suite was green over both.** `test_documentation_ranges_and_placeholders_are_allowed`
exercised exactly one range — `10.0.0.0/8` — which the broken pattern never matched, so the
dead allowlist entry was never reached. One defect was hiding the other, and neither was
found by reading the pattern; both surfaced from a redaction test asserting the *count* of
masked spans.

Blast radius measured before changing the rule, over all 1,675 tracked files: **two spans
newly refused and two no longer refused, all four inside `curate/scrub.py`'s own pattern
source and comments.** No real content anywhere in the repository changes verdict.

## Alternatives Considered

- **A flag on `runner.py`.** Smaller diff, and it puts a no-mutation guarantee inside the
  program whose every other function mutates. It also collides with WI-0228, which owns
  that file's canary path.
- **Reuse `runner.read_contract` to read the contract.** The obvious simplification, and
  wrong: it writes `status=contract-invalid` to the ledger on a schema failure. Correct for
  a deploy, fatal for an observer — an observer whose act of observing rewrites the record
  is not an observer. The validator is called directly instead, which keeps the verdict
  identical without the side effect.
- **A tracked snapshot file per system, overwritten in place**, instead of mail. Bounded at
  one file per system with git carrying the history, and genuinely tidier. Rejected because the item
  and the brief both name the mail path, because a second machine becoming a production
  host would make it a two-writer tracked file (WI-0132's defect), and because the mailbox
  is where a session already looks.
- **Include a periodic heartbeat bundle** so freshness is legible even with no change.
  Rejected for now: it reintroduces unbounded growth at a slower rate, and the same
  question is better answered by the sweep's own status than by mailing a report that says
  nothing happened. Named as a finding.

## Consequences

- A session on devbox can answer *what is that system doing* from its own mailbox, with no
  Runner session and no request. The 09-03 brief's Exit C is closed for launchd-hosted
  systems.
- **A system whose contract declares an `apply` command and no units gets the least from
  this, and it is visible rather than hidden.** There is no local process to ask about and
  its `verify` is the only liveness answer; the bundle says so in `gaps` instead of reporting a
  clean unit table.
- The scrub gate now refuses a range it always claimed to. Anything bound for the outbox
  carrying a 10.x address will be refused where it previously passed — which is the point,
  and the measurement says nothing in the tree is affected today.
- **The request path is not built.** A question a state change cannot anticipate — *pull
  me a log tail from a system whose deploy state has not moved* — still has no answer, and
  is recorded as a finding for review rather than closed by implication.

## References

- WI-0229; the 2026-09-03 consultant brief, §5 Exit C
- [ADR-0103](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md),
  [ADR-0132](0132-a-deploy-result-is-published-by-the-machine-that-produced-it.md),
  [ADR-0107](0107-mail-transits-origin-and-every-machine-drains-its-own-share.md)
- `deploy/diagnose.py`, `curate/scrub.py`, `tests/test_diagnose.py`, `tests/test_scrub.py`
