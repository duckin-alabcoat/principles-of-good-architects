# Habits history

Append-only change log for [`master.md`](master.md). Newest-first. Per [ADR-0010](../adr/0010-registries-canon-only-sidecar-history.md).

Each entry records: date, change type (add / edit / status-flip / remove / rename), affected slug(s), prior value for edits or removes, citation that justified the change, author. Cold-readable — someone reading this file alone can reconstruct what happened.

Per-Architect adoption events are **not** logged here — they are logged in the adopting Architect's role doc (per [ADR-0010](../adr/0010-registries-canon-only-sidecar-history.md)).

---

## 2026-09-28 — one Statement generalized, round 3 (WI-0448)

- **Author:** Federation Architect (WI-0448 fix lane)
- **Change type:** edit (Statement wording on one entry; meaning, status, parent and slug untouched)
- **Slug:** `journaled-reversible-writes` — examples in the Statement were generalized.

Provenance and argument prose were generalized (WI-0447/WI-0448); the one Statement example above is the only Statement change. `CANON.md` regenerated (habit count unchanged; one digest line changed).

---

## 2026-09-28 — provenance prose generalized, round 2 (WI-0448)

- **Author:** Federation Architect (WI-0448 fix lane)
- **Change type:** edit (citation and sidecar prose only)

Provenance and argument prose were generalized (WI-0447/WI-0448); no Statement changed. `CANON.md` is unaffected.

---

## 2026-09-28 — provenance prose generalized, round 1 (WI-0448)

- **Author:** Federation Architect (WI-0448 fix lane)
- **Change type:** edit (citation and sidecar prose only)

Provenance and argument prose were generalized (WI-0447/WI-0448); no Statement changed. `CANON.md` regenerated; the digest is unchanged.

---

## 2026-09-27 — two Statements generalized (WI-0448)

- **Author:** Federation Architect (WI-0448 sweep lane)
- **Change type:** edit (Statement wording on two entries; citation and sidecar prose elsewhere; meaning, status, parent and slug untouched)
- **Slugs:** `single-source-and-deliver`, `no-server-outlives-its-session` — examples in the Statements were generalized.

Provenance and argument prose were generalized (WI-0447/WI-0448); the two Statement examples above are the only Statement changes. `CANON.md` regenerated (habit count unchanged; two digest lines changed).

---

## 2026-09-26 — two Statements generalized (WI-0447)

- **Author:** Federation Architect (session ~426, lane)
- **Change type:** edit (Statement wording only; meaning, status, parent and slug untouched)
- **Slugs:** `no-server-outlives-its-session`, `journaled-reversible-writes` — examples in each Statement were generalized.
- **Citation:** the operator's ruling, 2026-09-26. The rule each Statement states is unchanged.

---

## 2026-09-18 — habit amended — `never-route-your-own-work-through-the-user` (OPS-0008 sitting, WI-0184 + WI-0232)

- **Author:** Federation Architect (session ~371, lane)
- **Change type:** edit (Statement + Last edited + Citation; status, parent and slug untouched)
- **Slug:** `never-route-your-own-work-through-the-user`
- **Prior value (the two replaced spans):**
  - *"…a list of steps with exact terminal commands is the deliverable…"*
  - *"…(c) file or update the work item for the missing verb. Handing over the command is on none of these lists. Genuinely out-of-sandbox actions — GUI dialogs, OS permission panes, browser admin consoles — are not this habit's subject and stay governed by [`batch-user-asks`](#batch-user-asks)."*
- **Citation:** WI-0184; WI-0232; consultant brief of 2026-09-03 on machine-bound development §6.2 item 5; a draft (a withheld proposal); producer entries of 2026-09-14 (*"Ask who is ACTING on a host before asking what they can do there"*) and 2026-08-25 (session ~172). the operator's ruling of 2026-09-13: a session does not reach into a machine it was not started on.
- **Approval:** the operator, in-session 2026-09-18, directing this sitting to *"close WI-0184 by accepting or declining its research-then-confirm-once habit"* and to *"settle WI-0232's hardware clause wording in CANON so a machine-bound handoff reads as a defect."* Both calls were delegated explicitly; no other canon change in this sitting was taken on that authority.

**One gap, found twice, closed once.** The sanctioned-moves list (a)/(b)/(c) was written for the case
where *a guard refuses you*, and it reads as exhaustive. Two walls that are not refusals fell straight
through it: being **unequipped** (WI-0184 — the tool is not on a box you control) and being **bound to
a machine you cannot reach** (WI-0232). Both produced the same defect, the user's keyboard as the
resolution, and both were invisible to the habit that exists to forbid exactly that. The list gains
(d) and (e) rather than the federation gaining two more habits.

**`research-then-confirm-once` was DECLINED as a standalone universal habit.** Its own central defence
— that the parent habit *"explicitly carves out the case this defect lives in"* — was measured against
the text and is false. The carve-out was a closed list of three GUI surfaces and never reached an
in-sandbox read of a file inside an app bundle, which the draft itself concedes those reads were. So
roughly 70% of the draft restated the parent, and the residue that was genuinely absent is one clause:
*a missing tool on a box you control is a gap to close, not a reason to use the operator as a terminal.*
The draft's sharpest contribution — **one confirmation, never a sequence of probes each announced as
the last** — is kept verbatim in (d), because the denominator argument against
[`batch-user-asks`](master.md#batch-user-asks) does survive: eight probes batched into one message
satisfy that habit in full while six of them were never needed, and its counter-case (*"ask as you
learn"*) positively licenses the serial shape. The habit count is unchanged at 51.

**The exemption changed KIND, which is the part that closes WI-0232's loophole.** It exempted
*membership in a category* — "GUI dialogs, OS permission panes, browser admin consoles" — which is
precisely what sheltered the asks the 09-03 brief wanted counted as defects. It now exempts a
*property*: **an observation no session you can start could make**. A GUI-shaped ask must earn its
exemption by being structurally unreachable rather than by looking unreachable.

**What was deliberately NOT promised away.** A session whose `launchctl managername` is `Background`
— a dispatched lane, any SSH login — cannot reach a GUI application over Apple Events at all
([ADR-0077](../adr/0077-spawn-surface-is-detected-and-tmux-is-first-class.md), surface detection still
standing under [ADR-0100](../adr/0100-dispatch-spawns-headless-the-operator-s-machine-attaches.md)).
So *does a window actually open* remains a legitimate ask. WI-0184's own line is preserved intact:
**static inspection moves; behavioural verification does not.**

**Rejected wording, recorded because it was a decision.** A drafted variant named the 09-03 brief's
three exits literally, at +207 bytes. Rejected: those are federation-specific infrastructure, and this line ships byte-identical to
every member, most of which have none of them. (e) names the general move the operator actually ruled
— *ask who is acting on that host and dispatch it to them* — and the three exits stay in the brief and
in WI-0232 where they belong.

**Measured, not asserted.** CANON.md went 46,400 → 46,912 of the 48,000 ceiling (+512), leaving 1,088.
`curate/check_canon_budget.py` exit 0. `habits/master.md` is not in `CANON_FILES`
(`curate/metrics.py:104`), so the registry prose above and this sidecar entry cost nothing against the
ceiling — only the Statement line is emitted into the digest.

**Not delivered here.** The 09-03 brief §6.2 item 5 also asks that the substrate's own voice be linted
for this clause (WI-0158) — *any tool that prints "run this from another machine" is printing a defect*.
That is code, not canon. **WI-0232 does not close on this edit alone.**

---

## 2026-09-13 — Reality re-graded — `evidence-is-separated-from-state-by-construction` (WI-0286)

- **Author:** Federation Architect (session ~314, dispatched lane)
- **Change type:** edit (Reality field only — the statement, parent and status are untouched)
- **Slug:** `evidence-is-separated-from-state-by-construction`
- **Prior value:** *"Discipline-only — the coordination store and the liveness sidecar are still reachable from a test, and the environment neutraliser is an opt-in `setUp` call a module can forget. The structural fix is scoped and owed."*
- **Citation:** [ADR-0133](../adr/0133-evidence-is-separated-from-state-by-an-immovable-anchor.md), WI-0286.

The structural fix this field called "scoped and owed" is built for the liveness-sidecar
axis, so the field is now false as written. It is re-graded to **Partial**, not Built, and
the remaining gaps are named with the numbers that measured them rather than left to the
reader's optimism.

**What the entry now records that it could not on 2026-09-11.** All three shipped detectors
were green with zero holes while the boundary was open — the rules were satisfied and 52
writes a run still landed in a real `.session-state/`. Thirty-five came from a module that
pins its anchor *correctly*; the escape was one level down, in a path computed FROM that
anchor at import. That is the shape this field should be read for from now on: "a
neutraliser exists and modules call it" is not the same fact as "a write cannot land in
live state", and the registry had been recording the first as though it were the second.

`CANON.md` unchanged — the digest carries the one-line statement, not the Reality field.

---

## 2026-09-11 — habit added (Accepted) — `evidence-is-separated-from-state-by-construction` (OPS-0007 first run)

- **Author:** Federation Architect (session ~268)
- **Change type:** add
- **Slug:** `evidence-is-separated-from-state-by-construction` (Parent [P13](../principles/master.md#p13--single-writer-per-state), universal)
- **Status on landing:** Accepted (the operator's registry-Accept in the sitting)
- **Citation:** WI-0286's six instances plus four independent producer entries (`0b910b53ffe4`, `bb905f64ddf8`, `8a6a81780885`, `e1dea2b8bdb7`). the operator, 2026-09-11: *"a"*.

**This is a reversal, and the reversal is the interesting part.** Session ~241 considered exactly this clause and declined it, in writing: *"the injected set is 8,731 chars over ceiling, and a rule that has never been enforced anywhere is the weakest candidate in the queue for the space."* That was a sound decision on the premises available. Both premises were overturned earlier the same day — item 1 of the OPS-0008 sitting ruled promote-on-merit with the ceiling split separately, and item 9 established that "never enforced anywhere" is a property to **record** in the new Reality field rather than a reason to refuse. The findings review then caught the stale decision, which is the function it exists to serve: a lane decided correctly, the ground moved, and nothing but this review would have noticed.

**Why P13 and not P18.** The instances look like test-quality failures and are not. Their damage was to *records*: authored outcome history deleted by an auto-commit, four live lanes held unrecoverable by a fake heartbeat, the real work-item store written to mid-run. That is a single-writer violation — two writers to one state, one of them wearing the costume of an observer — so it belongs under P13.

**Why a rule and not only the code fix.** The structural fix is separately scoped and unaffected by this decision. It was considered as the *alternative* and declined on reach: the fix protects this repository and travels nowhere, while the exposure is universal — every member's suite runs as the same user, in the same tree, with production's write authority. Canon is the only mechanism by which a lesson learned here becomes a rule in every other system, which is this Architect's entire remit.

**The Reality line is honest on arrival.** Discipline-only: the coordination store and liveness sidecar remain reachable from a test, and `neutralize_dispatch_env` is an opt-in `setUp` call a module can forget. The rule states the bar; the code does not yet enforce it, and the entry says so rather than implying protection it does not provide.

`CANON.md` regenerated (50 → 51 universal habits).

---

## 2026-09-11 — `declare-what-a-check-assumes` edited — it binds your own ad-hoc probes, not only shipped checks

- **Author:** Federation Architect (session ~268)
- **Change type:** edit
- **Slug:** `declare-what-a-check-assumes` (Parent [P18](../principles/master.md#p18--verify-everything), universal)
- **Prior value:** statement ended at *"never fold 'couldn't tell' into the same output as 'checked and it's fine.'"*
- **New value:** the same, plus: *"**This binds your own throwaway diagnostic commands, not only the checks you ship**: before reporting a negative from a one-off probe, ask whether the probe asked the negative question — an exact-name lookup, an `ls` that printed nothing, a single grep answers only about the key you named, and reporting it as a searched negative is the same collapse in a costume the rule's readers keep missing."*
- **Citation:** Five producer entries in the 2026-09-11 curate pass applied this habit to something that is *not* a shipped detector — `029416b4036c` (*"checking one exact name is not searching"* — a package lookup of one exact name reported as "not available", costing the user an option they actually had), `bb905f64ddf8` (which asked for this clause by name: *"worth asking in the next curate pass whether it needs an explicit 'applies to your own diagnostic commands, not just shipped detectors' clause"*), `0e30165ec85c`, `a7c41db7560a`, `22d1f6b6b6db` (the same habit read from the *consuming* side). the operator, 2026-09-11: *"a"*.

**The narrow reading was the natural one, which is the finding.** The habit's own supporting text is written entirely about detectors, parity views and status surfaces — permanent, shipped instruments. Five separate entries reached for it to describe a one-off command run while diagnosing, and each had to argue the extension rather than cite it. When five readers independently stretch a rule the same way, the rule is under-stated, not the readers over-reaching.

**Why a clause and not a new habit.** Considered in the sitting and declined: the mistake is identical — "I could not see it" printed as "there is nothing there" — and only the surface differs. A second entry would have split one idea across two lines that a reader must remember to connect, and cost a full entry against a budget already over ceiling, to say something the existing rule almost says.

**Note on the sibling promotion.** [`derive-a-checks-subjects-from-the-authority`](master.md#derive-a-checks-subjects-from-the-authority), Accepted earlier in this same sitting, is deliberately *not* this: it governs where a check gets its roster, which is upstream of anything this habit can declare. The two were separated on argument, and the separation is recorded in that entry's history note.

`CANON.md` regenerated (50 habits, statement text changed).

---

## 2026-09-11 — habit added (Accepted) — `a-timing-claim-is-measured-never-read` (OPS-0008 first run)

- **Author:** Federation Architect (session ~268)
- **Change type:** add
- **Slug:** `a-timing-claim-is-measured-never-read` (Parent [P18](../principles/master.md#p18--verify-everything), universal)
- **Status on landing:** Accepted (the operator's registry-Accept in the sitting)
- **Citation:** Producer entries `1c0107405007`, `77ccdb18693f`, `fc73225068ef`, `d5ec44feaf2d`, `f2ffec4011aa`, `d73c8ffdb6ca`, `3c299490ed94`, `07eaf1d5d39f` — **eight entries, the largest cluster in the 64-entry queue**, reached independently by three of the five reader slices. the operator, 2026-09-11: *"a"*.

**The worked example that carried it in the sitting.** A work item asserted one event happened *"seconds after"* another, with a provenance line reading `VERIFIED by reading the code`. Measured later: 0.07–0.22s across 13 lanes, and the tool everyone assumed was responsible was not on that path at all. A fix had already been designed on top of the wrong picture. Nobody was careless — the check was performed and recorded; the method simply could not reach the claim's class, and nothing flags that mismatch.

**Why it is not a restatement of `no-fabricated-data`.** Considered and declined on the argument. That habit demands a value come from a tool call rather than from the Architect's head — and reading the code *is* a tool call, so it reads as satisfied. The failure survives the existing rule intact, which is precisely why it kept recurring. [`capture-the-probe`](master.md#capture-the-probe) is the other near neighbour and has the same hole: it asks you to record the probe, never whether the probe could answer the question.

**The second surface is the Architect's own prose.** `77ccdb18693f` extends the rule from inherited claims to self-authored ones: comparative vocabulary in a comment — *backstop*, *fast path*, *in practice never* — names a frequency or a precedence and therefore carries a truth value. In the source incident the ranking was inverted: the TTL was load-bearing and the sweep never saw the case. A correct value with a wrong story attached is what the next reader relies on when deciding whether the value may be changed.

**Prior art in local memory, now promoted.** *"Reading code cannot verify a timing claim"* had been carried as a local operating memory on this machine for some time. Local memory reaches one machine and one Architect; canon reaches every member. This is the promotion that closes that gap.

`CANON.md` regenerated (49 → 50 universal habits).

---

## 2026-09-11 — habit added (Accepted) — `derive-a-checks-subjects-from-the-authority` (OPS-0008 first run)

- **Author:** Federation Architect (session ~268)
- **Change type:** add
- **Slug:** `derive-a-checks-subjects-from-the-authority` (Parent [P18](../principles/master.md#p18--verify-everything), universal)
- **Status on landing:** Accepted (the operator's registry-Accept in the sitting)
- **Citation:** Producer entries `727a6d5535f8`, `b5dd99fdcf48`, `802a8d2c8fe7`, `47d75ebfb102`, `8a6a81780885`. the operator, 2026-09-11: *"a"*.

**Second convergence promotion of the sitting.** Five entries, four disjoint slices, and three independent readers who drafted the same rule under three different slugs without seeing each other's output.

**Why not folded into `declare-what-a-check-assumes`.** Considered in the sitting and declined on the argument, not on taste. That habit binds a check to print a distinct answer *when its assumption is known to have failed*. Here the failure is unobservable by construction: a check whose own enumeration defines the universe cannot detect that a subject is missing, so there is no unknown for it to declare — the total silently shrinks and the verdict stays green. The existing rule governs what a check **says**; this one governs what it **looks at**, and sits upstream. Folding them would have buried the actual remedy (where the roster comes from) inside a rule about output wording.

**The second clause earns its place.** `47d75ebfb102` supplies the variant that is not about rosters at all but about the same closure: probing a *count of instances produced so far* instead of the *rule that produces them*. A count returned 0 for most members — identically for a member that was genuinely safe and for one carrying the same broken rule that had not yet filed a brief. Re-probing the rule turned "everyone else is fine" from a hope into a fact. Same defect shape, one step earlier than the output.

**The verification clause is deliberate.** *"Watch the widened check go red once before trusting it"* — a widened check that stays green is a finding about the widening, not reassurance about the fleet.

`CANON.md` regenerated (48 → 49 universal habits).

---

## 2026-09-11 — habit added (Accepted) — `retire-the-class-not-the-instance` (OPS-0008 first run)

- **Author:** Federation Architect (session ~268)
- **Change type:** add
- **Slug:** `retire-the-class-not-the-instance` (Parent [P18](../principles/master.md#p18--verify-everything), universal)
- **Status on landing:** Accepted (the operator's registry-Accept in the sitting)
- **Citation:** Producer entries `aa1453a0f226`, `0e30165ec85c`, `0b910b53ffe4`, `9c41b16b3095`, `802a8d2c8fe7`. the operator, 2026-09-11: *"a"*.

**The evidence is the convergence, not the count.** The 64-entry queue was triaged in five disjoint slices by readers who could not see each other's output. Two of them drafted this rule independently, from non-overlapping entry sets, in near-identical words. Five instances across five sessions is past the bar; two independent derivations of the same wording is a stronger argument than any single reader of all 64 could have made, and it is the specific product this ritual exists to generate.

**Why it is not covered by what we already have.** [`single-source-and-deliver`](master.md#single-source-and-deliver) forbids the hand-maintained duplicate but presupposes you know where the copies are. [`add-structural-guard-on-recurrence`](master.md#add-structural-guard-on-recurrence) fires on a repeat violation of an *adopted rule* and prescribes a *code guard*; this fires on the first fix of an ordinary *defect* and prescribes a *sweep*. A grep of `CANON.md` for sibling/call-site/sweep language returned one unrelated hit (`choose-shared-host-resource-defaults-for-siblings`, about shared host resources). The gap was real.

**The sharp half, preserved in the statement.** Fixing the instance in hand removes the symptom that would have led you to the others — so peak confidence and minimum evidence arrive at the same moment. That inversion is why the habit has to be a step before closing rather than a disposition to be thorough.

`CANON.md` regenerated (47 → 48 universal habits).

---

## 2026-09-11 — `add-structural-guard-on-recurrence` edited — fail-open is a default, not a mandate

- **Author:** Federation Architect (session ~268)
- **Change type:** edit
- **Slug:** `add-structural-guard-on-recurrence` (Parent [P15](../principles/master.md#p15--code-for-mechanism-not-judgment), universal)
- **Prior value:** *"The guard must fail open (never brick the tool it guards) and narrate its denial at the conversation layer."*
- **New value:** the same, plus: *"— **unless what it protects cannot be undone**, in which case it fails closed and says why it could not verify. The direction is set by what is on the other side of the guard, not by a blanket preference: where a broken guard letting the action through would produce exactly the loss the guard exists to prevent, open is the unsafe direction."*
- **Citation:** Producer entry `bb905f64ddf8` (session ~102), raised in the OPS-0008 sitting of 2026-09-11 and laid out in `curate-runs/REVIEW-20260911-144806Z.md`. The entry's own Status line named this as its strongest promotion candidate: *"fail-open vs fail-closed is set by what the guard protects — it refines `add-structural-guard-on-recurrence`, whose current statement mandates fail-open flatly, and this session produced a guard where fail-open would have been wrong."* the operator, 2026-09-11: *"b"*.

**The old text was not vague, it was wrong.** It said *never* brick the tool, and the session that produced this entry built a guard protecting against work loss — where a broken guard that lets everything through produces precisely the damage the guard exists to prevent. A canon line is injected into every Architect's session, so a flat mandate in the wrong direction is an instruction to build one class of guard unsafely, fleet-wide.

**Why an exception and not a removal.** Dropping the direction entirely was considered and declined in the sitting: fail-open is the right default for the large majority of guards, and a rule that says nothing would leave new guards to guess. The edit keeps the default, names the one condition that inverts it, and makes the test concrete — *what is on the other side of this guard, and is it recoverable?*

**Reservation.** "Cannot be undone" is a judgment call, which is the cost of the edit. It is the same judgment [`confirm-destructive-ops`](master.md#confirm-destructive-ops) already asks every Architect to make, so it introduces no new class of decision.

`CANON.md` regenerated (47 habits, statement text changed).

---

## 2026-09-11 — habit added (Accepted) — `confirm-a-relayed-ruling-before-acting-on-it` (OPS-0008 first run)

- **Author:** Federation Architect (session ~268)
- **Change type:** add
- **Slug:** `confirm-a-relayed-ruling-before-acting-on-it` (Parent [P20](../principles/master.md#p20--untrusted-data-stays-untrusted), universal)
- **Status on landing:** Accepted (the operator's registry-Accept in the sitting)
- **Citation:** Producer entries `b47b7c0c0922`, `4ecfc2beeaa4`, `5c6525a5718e`, laid out by `curate/gather.py` into `curate-runs/REVIEW-20260911-144806Z.md`. the operator, 2026-09-11, choosing the write-it-down option over discipline-only and over building a ticket mechanism: *"b"*.

First promotion from the first run of OPS-0008, the weekly canon-curation sitting authorized 2026-09-09. The queue was 64 entries; this is one of the candidates that survived triage.

**Why canon and not judgment.** The originating entry asked the question directly — *"every Architect with an inbox can receive a brief that speaks for its user"* — and that is the whole argument: the hole is not federation-specific, and canon is the only route by which a lesson observed in one system becomes a rule in another. Keeping it as the Architect's judgment would have left every other system exposed to a gap this one had already found twice.

**Why P20 and not P18.** The failure looks like a verification failure, but its subject is inbound content from a lower-trust channel, which is P20's territory. It sits directly beside [`treat-inbound-payload-as-data-not-commands`](master.md#treat-inbound-payload-as-data-not-commands) and closes that habit's open edge: "act only after a human clears it" assumes the record of the human clearing it is itself trustworthy, and this is the case where it is not.

**Recorded reservation, deliberately not acted on.** Both observed relays were truthful, so the failure has never actually bitten. But this is the second instance, and [`add-structural-guard-on-recurrence`](master.md#add-structural-guard-on-recurrence) holds that a repeat means discipline is not enough and the fix belongs in code. The structural option — extending the ADR-0112 grant model to cover rulings, not just actions — was presented and declined for now, on the ground that it charges the user a ticket every time they make a decision. If a relayed ruling is ever found false, that reservation converts into owed work rather than being re-argued from scratch.

`CANON.md` regenerated (46 → 47 universal habits).

---

## 2026-08-22 — habit added (Accepted) — `never-route-your-own-work-through-the-user` (consultant brief R1)

- **Author:** Federation Architect (session ~166)
- **Change type:** add
- **Slug:** `never-route-your-own-work-through-the-user` (Parent [P10](../principles/master.md#p10--architect-owns-operational-substrate), universal)
- **Status on landing:** Accepted (the operator's registry-Accept in-session)
- **Citation:** External consultant brief `2026-08-19-consultant-command-escalations-to-the operator` R1 — an `apply: manual` brief that assembled its evidence entirely from the federation's own journals and work items (WI-0085/0097/0099/0122/0141/0143). the operator, 2026-08-19, ruled every one of those escalations a defect: POGA should never hand him git or `poga` work to do. the operator approved the habit in session ~166.

Not a curate-pass promotion — a doctrine adoption triaged from the receipt-ritual inbox, landed with [ADR-0099](../adr/0099-the-user-is-not-an-execution-surface.md).

**The brief's R1 was adopted with its scope corrected, and the correction is the substance.** As written, R1 said an Architect *"never asks the operator to run a terminal command."* That would have contradicted the already-Accepted [`batch-user-asks`](master.md#batch-user-asks), which explicitly sanctions *"a single user-execute punch list with exact steps"* for work outside the Architect's sandbox (GUI panes, OS settings) — WI-0049, granting macOS Automation permission, is exactly that case. Two Accepted rules in direct contradiction. the operator drew the real line at adoption: when he asks an Architect to guide him, a list of steps and terminal commands is the right deliverable; what is being stopped is an Architect handing him its own work to carry out.

So the habit keys on **who owns the work**, not on whether a command appears in chat. User-requested guidance is a deliverable and commands belong in it; the Architect's own blocked task, escalated into the user's hands, is the defect. Drafting it as "never print a command" would have been a rule the fleet had to violate on its first guidance request — and a canon rule routinely violated is worse than none.

Parented to P10 rather than promoted to a new principle: P10 (`architect-owns-operational-substrate` — *"Routine work is the Architect's job, not the user's"*) already carries the principle; this is the sharp practice under it, which is what the habit registry is for. `CANON.md` regenerated (45 → 46 universal habits).

---

## 2026-08-13 — `session-start-git-ritual` edited — harness-written dirt is the harness's job, not a stop-and-ask

- **Author:** Federation Architect (session ~138)
- **Change type:** edit (Statement)
- **Slug:** `session-start-git-ritual` (Parent [P5](../principles/master.md#p5--session-continuity-survives-cold-restart))
- **Prior value:** the Statement's dirty-tree clause read, in full: *"If `git status` is dirty, stop and ask."*
- **Citation:** the operator, 2026-08-13: every Architect kept surfacing the main checkout's harness-written dirt to the user as a question, every session. Mechanism and evidence in [ADR-0091](../adr/0091-the-harness-commits-what-the-harness-writes.md); the operator approved the full plan including this scoping in the same session.
- **Reasoning:** the unqualified clause made every Architect surface the substrate's own uncommitted writes (journals, compiled handoff, `STATUS.md`, `ROADMAP.md` — measured live: dozens of dirty paths on one member, more on others, and standing dirt on the federation's own main checkout) as a user-facing question, in every session, with no path to ever resolving it — the user was the fleet's garbage collector. The amended clause scopes stop-and-ask to **authored** work, which is the genuine signal, and directs the harness's own records to a pathspec'd commit per ADR-0091. The stop-and-ask protection for real work is unchanged.

**Rollout.** Statement edit only — no status change. Reaches every member through the regenerated `CANON.md` at their next session start; the mechanical half (the janitor's commit step, `main-sync`) ships in the same substrate push.

---

## 2026-08-06 — `verify-in-the-created-configuration` Accepted — the three-trap verification habit, 16 sessions after it was proposed

- **Author:** Federation Architect (session ~121)
- **Change type:** status-flip (Proposed → Accepted)
- **Slug:** `verify-in-the-created-configuration` (Parent [P18](../principles/master.md#p18--verify-everything))
- **Prior value:** Proposed since 2026-07-21 (session-80 curate pass). Statement unchanged by this flip — only the status, the `Last edited` date, and the citation's closing line changed.
- **Citation:** **the operator's registry-Accept given directly in-session**, 2026-08-06. The entry surfaced in the session-start orientation as the one Proposed item in either registry; the three traps were restated in plain terms without registry vocabulary, and the answer was *"accept."* The originating evidence is unchanged: three independent federation instances across sessions 78–79 (`6f5ad99435a0`, `f4d10e5bab4a`, `fbc5f2587fa3`).
- **Reasoning:** Nothing about the argument changed between Proposal and Accept — what changed is that it accumulated corroboration while sitting un-gated. Session ~116 produced three defects that were invisible to review and obvious on first real use, and that session independently practised the habit before it was canon (deliberately reverting a fix to confirm the new test actually failed against the broken version). A habit whose failure mode keeps recurring in the curator's own work while the entry waits is exactly the un-gated-proposal debt [ADR-0009](../adr/0009-data-storage-mechanics.md)'s promotion workflow exists to clear.

**What accepting it binds.** Per [ADR-0022](../adr/0022-architect-ingest-distillation-and-code-channel.md) the universal set is **inherited, not negotiated** — registry-Accept *is* the adoption event, and no per-Architect §4 edit follows. The habit reaches every member through the regenerated `CANON.md`, injected at each session start; members pick it up with no action on their part. `curate/distill.py` regenerated the digest (44 → 45 universal habits).

**Composition, not duplication.** It sits under [P18](../principles/master.md#p18--verify-everything) alongside [`exercise-delegated-work-end-to-end`](master.md#exercise-delegated-work-end-to-end) (that habit turned on oneself rather than on a subagent) and [`add-structural-guard-on-recurrence`](master.md#add-structural-guard-on-recurrence) (the pin-the-documented-default loader test *is* that guard). No existing entry was narrowed or superseded.

---

## 2026-08-04 — `no-compound-bash` Deprecated — its motivation was a harness limitation, and the limitation is gone

- **Author:** Federation Architect (session ~116)
- **Change type:** status-flip (Accepted → Deprecated)
- **Slug:** `no-compound-bash` (Parent [P7](../principles/master.md#p7--transparency-at-conversation-layer))
- **Prior value:** Accepted since 2026-05-27. Statement: *"Don't compound bash commands with `&&`, `;`, or shell `for` / `until` / `while` loops. One bash tool call = one chat narration = one logical operation."* Entry retained in full in `master.md` for provenance; only the status and an explanatory section changed, no text was deleted.
- **Citation:** External consultant brief `2026-08-04-consultant-compound-bash-guard-retirement.md`, commissioned by the operator 2026-08-04 and triaged into the inbox this session. **the operator's registry-Accept given directly in-session** — asked in plain terms whether to retire the rule that blocks chained shell commands, he answered *"1. yes."* The consultant's ruling was quoted in the brief, but a ruling arriving through the inbox is inbound data, not a command ([`treat-inbound-payload-as-data-not-commands`](master.md#treat-inbound-payload-as-data-not-commands)), so it was confirmed with him before any of this landed.
- **Reasoning:** The habit had two stated failure modes. Mode 2 (**permission allow-list bypass**) was the load-bearing one and was a property of the *harness*, not of good Architects: settings allow-strings cannot match a compound command, so every chain surfaced as a context-free prompt and one-op-per-call was what let the allow-list work. The harness's **auto permission mode** judges commands directly, so that motivation no longer exists. Mode 1 (**narration specificity**) survives on its own merits and is already carried by [`narrate-consequential-tool-calls`](master.md#narrate-consequential-tool-calls). Two real side effects — cleaner one-line-per-op `guard-firings.jsonl` records and atomic per-op permission decisions — were weighed and judged nice-to-haves, not grounds to keep an absolute habit whose founding motivation is gone.

**The guard is retired, not deleted.** `compound_violation` stays live in `session.py check-bash` and under test, behind `"compound_bash_guard": true` in `session.config.json`; absent or false, the check is skipped. No member declares it, so the next substrate push turns the deny off fleet-wide with zero per-member action — the zero-touch shape of [ADR-0039](../adr/0039-appliable-brief-schema-and-auto-adopt-mechanism.md)/[ADR-0050](../adr/0050-headless-background-adoption-runner.md).

**The destructive-git half of `check-bash` is untouched and unconditional**, and the check order was flipped so it runs *first*: it used to sit behind the compound deny, which was safe only while compounds were always denied upstream. Its motivation was never prompt-matching — it is the [P9](../principles/master.md#p9--destructive-ops-confirmed) floor, and no settings deny-string can match the `git -C <path>` form.

The reusable half — *a guard whose motivation was a harness limitation retires when that limitation does; a guard whose motivation is a standing safety property does not* — is [ADR-0087](../adr/0087-a-guard-whose-motivation-was-a-harness-limitation-retires-with-it.md). `curate/distill.py` regenerated `CANON.md` (45 → 44 universal habits; `Deprecated` entries are excluded from the digest).

---

## 2026-07-27 — 6 habits added + Accepted — the operational-patterns harvest

- **Author:** Federation Architect (session 99/~100)
- **Change type:** add ×6 + status-flip (Proposed → Accepted), same session
- **Slugs:** `journaled-reversible-writes` (Parent [P21](../principles/master.md#p21--state-survives-failure)), `verified-backup-of-non-derivable-state` (Parent [P21](../principles/master.md#p21--state-survives-failure)), `retention-enforced-by-code` (Parent [P15](../principles/master.md#p15--code-for-mechanism-not-judgment)), `scheduled-liveness-smoke-test` (Parent [P18](../principles/master.md#p18--verify-everything)), `security-drift-check` (Parent [P18](../principles/master.md#p18--verify-everything)), `denial-audit-log` (Parent [P7](../principles/master.md#p7--transparency-at-conversation-layer)) — all universal
- **Status on landing:** Accepted, all six
- **Citation:** a withheld proposal (session 47, 2026-07-05), from a member system review, which found durability/liveness doctrine implemented as running self-verifying code, not just written policy. Sat un-gated for 22 sessions. the operator, session 99/~100, gave a single blanket accept over the principle (P21, same pass), all six habit candidates, and a green-light to draft the `scheduled-liveness-smoke-test` + `retention-enforced-by-code` pattern-briefs first (the harvest doc's own recommended sequencing). **Note:** the harvest doc's own decision-line said "the five habit candidates" but its table lists six; all six are promoted here as the table's actual content, not the miscounted prose line.

`curate/distill.py` regenerated `CANON.md` (39 → 45 universal habits; 20 → 21 principles, via the paired P21 add). The two pattern-briefs (smoke-test, retention-enforce skeletons) are queued as delivery work, not registry content — they ship as per-system pattern-briefs like the rest of substrate convergence, not a kit file.

---

## 2026-07-27 — 4 habits added (Proposed) — the session-99 recurrence-bar pass (curate)

- **Author:** Federation Architect (session 99/~100)
- **Change type:** add ×4
- **Slugs:** `declare-what-a-check-assumes` (Parent [P18](../principles/master.md#p18--verify-everything), universal), `ship-the-detector-with-the-capability` (Parent [P18](../principles/master.md#p18--verify-everything), universal), `numbers-are-drawn-never-picked` (Parent [P15](../principles/master.md#p15--code-for-mechanism-not-judgment), universal), `a-close-is-the-banner-not-the-sentence` (Parent [P18](../principles/master.md#p18--verify-everything), universal)
- **Status on landing:** Proposed (awaiting the operator's registry-Accept), all four
- **Citation:** `python3 curate/gather.py` run against the 13-entry review queue (`curate-runs/REVIEW-20260727-193439Z.md`). Four candidates already named in-entry by the producer file cleared the recurrence bar on their own evidence: `declare-what-a-check-assumes` (5 instances + 1 corroborating, ids `ccb3fc8bbee1` + `7836703c0b08`), `ship-the-detector-with-the-capability` (2 instances in one entry, id `1090c1e499d1`), `numbers-are-drawn-never-picked` (2 manifestations in one incident, id `59357d8fa046`), `a-close-is-the-banner-not-the-sentence` (1 instance in the producer file plus 2 more surfaced live in this session's own start orientation, id `59357d8fa046`). The remaining 9 queued entries stay `Surfaced`/`Folded`/`Fixed` per their own author-judgment ("one instance, not proposed" / "watch for a second instance") — not promoted this pass.

Cursor not yet advanced (`gather.py --accept` pending user review of the four drafts). `CANON.md` unaffected until Accept — distill transcribes Accepted entries only, so no digest regen or version bump this pass.

---

## 2026-07-27 — 4 habits Accepted (status-flip) — the session-99 recurrence-bar pass (curate)

- **Author:** Federation Architect (session 99/~100)
- **Change type:** status-flip ×4 (Proposed → Accepted), same session as the add above
- **Slugs:** `declare-what-a-check-assumes`, `ship-the-detector-with-the-capability`, `numbers-are-drawn-never-picked`, `a-close-is-the-banner-not-the-sentence`
- **Status:** Accepted, all four
- **Citation:** the operator, in-session: *"accept."* — a single blanket accept over the four drafts presented with recommendation.

`curate/distill.py` regenerated `CANON.md` (35 → 39 universal habits; 20 principles unchanged); `curate/gather.py --accept` advances the review cursor past the 13-entry queue in the same pass. Source `architect-learnings.md` Status fields updated to `Promoted (→ habits/master.md#<slug>)`.

---

## 2026-07-23 — habit added (Accepted) — `no-server-outlives-its-session`

- **Author:** Federation Architect (session 90)
- **Change type:** add
- **Slug:** `no-server-outlives-its-session` (Parent [P10](../principles/master.md#p10--architect-owns-operational-substrate), universal)
- **Status on landing:** Accepted (the operator's registry-Accept in-session)
- **Citation:** A member proposal asked for this rule as a one-line append to every Architect's `CLAUDE.md`. A federation decision (ADR-0064, withheld from this copy) rerouted it to canon: a maintained sentence copied into N repos is the hand-maintained duplicate [`single-source-and-deliver`](master.md#single-source-and-deliver) prohibits, and `CANON.md` already injects into every member — including members not yet bootstrapped. the operator agreed with the recommendation, session 90.

Not a curate-pass promotion — a redistribution decision taken while answering a member's proposal. `CANON.md` regenerated (34 → 35 universal habits); the digest line is the Statement verbatim. Per-target detail stays out of canon.

---

## 2026-07-22 — habit edit (corroboration) — `probe-live-state-before-acting-on-a-report` (curate)

- **Author:** Federation Architect (session 84)
- **Change type:** edit (citation — added a corroborating instance; no statement change, no status change)
- **Slug:** `probe-live-state-before-acting-on-a-report` (Parent [P18](../principles/master.md#p18--verify-everything), universal)
- **Status:** unchanged (Accepted)
- **Citation added:** Federation `architect-learnings.md` id `1a08069a7ba1`, 2026-07-21 (session 82) — relayed two concurrent sessions' journal *"landed cleanly"* self-reports to the operator as fact; `git show` proved one session had blended the other's files into a single commit while reporting clean isolation. The prior-session journal is a status-describing report; git-verify load-bearing *"landed"* claims before relaying.

Session-84 curate pass, entry 1 of 2. Recorded as a new **instance** of an already-Accepted P18 habit (prior-session journal, git-verified) — not a new promotion. Statement and one-line digest unchanged, so **no `CANON.md` regen and no version bump** this pass. The compiled-handoff structural guard the entry floats is deferred per `add-structural-guard-on-recurrence` (the entry judged it not-yet-recurrent / over-engineering risk). Cursor advanced via `gather.py --accept`.

---

## 2026-07-21 — habit added (Proposed) — `verify-in-the-created-configuration` (curate)

- **Author:** Federation Architect (session 80)
- **Change type:** add
- **Slug:** `verify-in-the-created-configuration` (Parent [P18](../principles/master.md#p18--verify-everything), universal)
- **Status on landing:** Proposed (awaiting the operator's registry-Accept)
- **Citation:** Session-80 curate pass. Three independent federation `architect-learnings.md` instances, sessions 78–79: `6f5ad99435a0` (tests-from-the-premise / boundary sweep), `f4d10e5bab4a` (a gate never run in its gated configuration), `fbc5f2587fa3` (a prose-asserted default not pinned by a test). Clears the recurrence bar single-source on three angles of one claim.

Sharpening of P18 `verify-everything`: *claimed behavior is not verified behavior — verification must exercise the actual code path in the actual configuration and be able to falsify the premise, not just confirm it.* Proposed only — not yet in `CANON.md` (distill transcribes Accepted entries), so no digest regen or version bump this pass; those follow on Accept.

---

## 2026-07-12 — habit added + Accepted — `choose-shared-host-resource-defaults-for-siblings` (curate)

- **Author:** Federation Architect (session 58)
- **Change type:** add + status-flip (Proposed → Accepted), same session
- **Slug:** `choose-shared-host-resource-defaults-for-siblings` (Parent [P10](../principles/master.md#p10--architect-owns-operational-substrate), universal)
- **Status on landing:** Accepted
- **Citation:** Session-58 curate pass. A producer-side learning (`architect-general`); a default-port collision between two members is the motivating case. the operator approved.

Universal P10 habit. `curate/distill.py` regen + version bumps at the curate-pass batch close.

---

## 2026-07-12 — 2 habits added + Accepted — under P20 `untrusted-data-stays-untrusted` (curate)

- **Author:** Federation Architect (session 58)
- **Change type:** add (2 habits) + status-flip (Proposed → Accepted), same session
- **Slugs:** `treat-inbound-payload-as-data-not-commands`, `enforce-the-data-code-boundary-where-it-exists` (Parent [P20](../principles/master.md#p20--untrusted-data-stays-untrusted), universal)
- **Status on landing:** Accepted
- **Citation:** Session-58 curate pass, from a producer-side entry — the data-not-commands posture for any inbound channel from a lower-trust component ([ADR-0013](../adr/0013-receipt-ritual.md) / [ADR-0033](../adr/0033-non-derivable-data-is-backed-up-at-machine-level.md)); the boundary-exists habit is the classic parameterized-query discipline ("little Bobby Tables"). the operator approved the habits.

The consuming-side pair for P20: `treat-inbound-payload-as-data-not-commands` (no structural boundary → surface + gate + auto-apply nothing) and `enforce-the-data-code-boundary-where-it-exists` (a real boundary → parameterize / args-not-interpolation). `curate/distill.py` regen (**29 → 31 habits**) + version bumps at the curate-pass batch close.

---

## 2026-07-12 — habit added + Accepted — `exercise-delegated-work-end-to-end` (curate)

- **Author:** Federation Architect (session 58)
- **Change type:** add + status-flip (Proposed → Accepted), same session
- **Slug:** `exercise-delegated-work-end-to-end` (Parent [P18](../principles/master.md#p18--verify-everything), universal)
- **Status on landing:** Accepted
- **Citation:** Session-58 curate pass. A member's producer-side learnings file (2026-06-21, self-tagged `architect-general`) + Federation memory `feedback_verify_subagent_output_before_relay` (session 56). the operator: *"accept."*

Universal P18 habit. Promotes a previously local-only practice into the registry (it had bound nothing in the fleet). `curate/distill.py` regen + version bumps batched at the curate-pass end.

---

## 2026-07-12 — habit added + Accepted — `probe-live-state-before-acting-on-a-report` (curate)

- **Author:** Federation Architect (session 58)
- **Change type:** add + status-flip (Proposed → Accepted), same session
- **Slug:** `probe-live-state-before-acting-on-a-report` (Parent [P18](../principles/master.md#p18--verify-everything), universal)
- **Status on landing:** Accepted
- **Citation:** Session-58 curate pass. Federation `architect-learnings.md` (2026-07-07, session 52 — nearly redid two shipped member items, caught by checking the live artifact) + the stale-`inputs/`-mirror failures (sessions 35/37/42/47). the operator approved.

Universal P18 habit; composes with the existing `curate/reconcile.py` structural guard (the mirror instance). `curate/distill.py` regen + version bumps batched at the curate-pass end.

---

## 2026-07-12 — habit added + Accepted — `mark-adr-reality-status` (curate)

- **Author:** Federation Architect (session 58)
- **Change type:** add + status-flip (Proposed → Accepted), same session
- **Slug:** `mark-adr-reality-status` (Parent [P18](../principles/master.md#p18--verify-everything), universal)
- **Status on landing:** Accepted
- **Citation:** Session-58 curate pass, from a member retrofit's outcome-D harvest (`architect-learnings.md`, 2026-07-12; the operator prioritized it session 55). Shaped with the operator, session 58: ADRs stay immutable, a single status board carries reality, and the existing **ADR index** (`adr/README.md`) is that board — a `Reality` column (`Built` / `Partial` / `Not-built` / `—`) beside `Status`, not a new artifact.

Universal P18 habit. Self-application: the federation's own ADR index gains the `Reality` column this session. `curate/distill.py` regen (**28 → 29 habits**) + federation/kit version bumps are batched at the curate-pass end (session-42 precedent — one bump for the whole batch).

---

## 2026-07-12 — 3 habits added + Accepted — cloud-deployed class-bound (P19)

- **Author:** Federation Architect (session 58)
- **Change type:** add (3 habits) + status-flip (Proposed → Accepted), same session; registry-schema change (new `Binds-to` field)
- **Slugs:** `hard-cost-ceiling-as-design-constraint`, `spend-is-budget-reported`, `hard-financial-kill-switch` (all Parent [P19](../principles/master.md#p19--cap-what-can-run-away), `Binds-to: cloud-deployed`)
- **Status on landing:** Accepted
- **Citation:** the operator, session 58 (2026-07-12) — content + explicit registry-Accept (*"accept"*); from [ADR-0042](../adr/0042-deployment-classes-and-promotion-discipline.md) §3 (cloud-deployed obligations) + the parked producer candidates (federation `architect-learnings.md`, 2026-07-12 outcome-D harvest).

First **class-bound habits** ([ADR-0042](../adr/0042-deployment-classes-and-promotion-discipline.md)): they live in the registry but are federation-managed for the cloud-deployed roster, not universal. Registry schema gained an optional **`Binds-to`** field (default `all` = universal; the preamble + entry-format note document it). `curate/distill.py` excludes any non-`all` binding from the generated `CANON.md`, so these three do **not** enter the universal digest (habit count stays **28**); +6 tests in `tests/test_distill.py` (suite 193 green). Granularity note: per-place spend authorization was folded into `hard-cost-ceiling-as-design-constraint` rather than minted as a 4th habit (Architect's call, surfaced to the operator, accepted as-is); budget reporting is its own habit.

---

## 2026-06-21 — Batch Accept (session-42 curate pass)

- **Author:** Federation Architect (session 42)
- **Change type:** status-flips + add + re-home + 2 statement edits
- **Citation:** the operator confirmed the Accept of the whole staged set in the session-42 curate pass.

Applied together as one Accept pass (canon regenerated once; federation + kit bumped once):

- **Status-flips Proposed → Accepted:** `no-question-without-a-tradeoff` (P17), `frame-work-in-outcome-terms` (P11), `batch-user-asks` (P10) — see each habit's "added" entry below for statements/provenance.
- **Added (Accepted on landing):** `capture-the-probe` (P18) — *"When you record something as verified, capture how you verified it — the probe, not just the verdict."* Child habit of the newly-Accepted P18; source: a member Architect's producer entry.
- **Re-home:** `no-fabricated-data` moved P7 → **P18** (don't-invent-values is a special case of verify-everything; composes-with P7 retained). Statement unchanged.
- **Statement edit — `exhaust-questions-in-planning`:** added foundational-first sequencing (*"sequence foundational-first … 'exhaust' means nothing unresolved at transition, not 'ask everything at once'"*). Prior statement: *"When planning, ask everything you need in one round. Before moving on, ask 'anything else on this?' …"* Source: a member Architect's producer entry.
- **Statement edit — `routine-ops-autonomy`:** appended the delegate-the-close clause (*"A delegate-the-close directive … is a full delegation … don't re-ask."*). Source: a member Architect's producer entry.

Net: canon habits **24 → 28** (3 flips + 1 add; the re-home and 2 statement edits don't change the count). Plus the already-applied (non-flip) session-42 edits below: the `add-structural-guard-on-recurrence` provenance/clarification and the `single-source-and-deliver` maintenance corollary.

---

## 2026-06-21 — Habit added — `batch-user-asks`

- **Author:** Federation Architect (session 42)
- **Change type:** add (new habit)
- **Slug:** `batch-user-asks`
- **Parent:** P10 — architect-owns-operational-substrate
- **Status on landing:** Proposed
- **Citation:** Session-42 curate review. the operator approved. Statement: *"Don't dribble your asks — batch what you genuinely need from the user into one up-front pass."* Source: a member Architect's producer entries (pre-flight permission batching; GUI punch list). Complement of `routine-ops-autonomy`. Theme-5 entries 5c (commit-first) and 5d (pull-forward) stay surfaced; 5e folded into `routine-ops-autonomy` (see its own history entry, batched).

**Status flip to Accepted requires the operator's approval per [ADR-0009](../adr/0009-data-storage-mechanics.md);** batched with the session-42 Accept pass. On Accept: regenerate `CANON.md`.

---

## 2026-06-21 — Habit added — `frame-work-in-outcome-terms`

- **Author:** Federation Architect (session 42)
- **Change type:** add (new habit)
- **Slug:** `frame-work-in-outcome-terms`
- **Parent:** P11 — ship-working-system-on-time
- **Status on landing:** Proposed
- **Citation:** Session-42 curate review. the operator approved. Statement: *"Process docs aren't the work — say what changes for the system, not just what artifacts you shipped."* Source: a member Architect's producer entry (process artifacts masquerade as outcome work), previously tagged `process-artifacts-arent-outcome-work` but never promoted. Related federation instance `aa6f3dcc52fb` (park upstream-blocked cosmetic work) kept surfaced, not folded.

**Status flip to Accepted requires the operator's approval per [ADR-0009](../adr/0009-data-storage-mechanics.md);** batched with the session-42 Accept pass. On Accept: regenerate `CANON.md` (habit count → reflects this + `no-question-without-a-tradeoff`).

---

## 2026-06-21 — Edit (provenance + clarification) — `add-structural-guard-on-recurrence`

- **Author:** Federation Architect (session 42)
- **Change type:** edit (failure-mode + citation; **Statement unchanged → canon one-liner unchanged, no regen**)
- **Slug:** `add-structural-guard-on-recurrence`
- **Citation:** Session-42 curate review. Added cross-system corroboration from a member Architect's producer entries (self-correction-promises-are-anti-evidence; structural-fixes-stick) and a clarifying line — *"a self-correction promise is anti-evidence; treat it as the trigger to add a guard, not as the fix."* No status or Statement change; the habit was already Accepted. Strengthens provenance to federation-wide (a member and the federation arrived at it independently). the operator approved.

---

## 2026-06-21 — Habit added — `no-question-without-a-tradeoff`

- **Author:** Federation Architect (session 42)
- **Change type:** add (new habit)
- **Slug:** `no-question-without-a-tradeoff`
- **Parent:** P17 — ask-in-prose-not-pickers (composes with P10)
- **Status on landing:** Proposed
- **Citation:** Session-42 curate review (`REVIEW-20260621-131951Z.md`). the operator agreed. Statement: *"If a choice has no real trade-off, don't ask — just make the call."* Cross-system: a member Architect's producer entry (convention questions aren't questions) + Federation bootstrap-intake `072cca146bde` (asked operator to name the Architect; asked git-setup questions — P10 violation).

Complement of `structured-decision-presentation` (how to present a real trade-off) — this governs the *non*-trade-off case (don't surface it as a question). **Status flip to Accepted requires the operator's approval per [ADR-0009](../adr/0009-data-storage-mechanics.md);** batched with the session-42 Accept pass. On Accept: regenerate `CANON.md` (habit count 24 → 25).

---

## 2026-06-20 — Statement sharpened — `routine-ops-autonomy` leads with the no-ask-to-commit imperative

- **Author:** Federation Architect (session 41)
- **Change type:** edit (statement wording — prepended a lead imperative; no semantic change, the prior statement is retained verbatim after it)
- **Slug:** `routine-ops-autonomy`
- **Parent:** [P10 — architect-owns-operational-substrate](../principles/master.md#p10--architect-owns-operational-substrate)
- **Prior lead:** *"Routine ops (git, toolchain, formatting, dependency tidying) — the Architect decides and acts. …"* (now the second sentence onward, unchanged).
- **New lead:** *"Never ask whether to commit or push — the answer is always yes; just do it."*
- **Citation:** the operator, session 41 (2026-06-20), ruled that Architects were still asking whether to commit or push, and that the answer is always yes: they should do it without asking. He approved the sharpened wording. The autonomy rule was already canon (this habit, under P10), but Architects kept asking — the rule lived as a multi-sentence statement that didn't *lead* with the prohibition. Unlike the picker (session 40), this can't be a `PreToolUse` guard — it's a chat-layer question with no tool call to intercept — so the fix is to make the canon one-liner itself the imperative. `distill.py` transcribes the whole Statement line, so `CANON.md` (regenerated + injected every session) now leads with "Never ask whether to commit or push."
- **Status:** Accepted (unchanged). `Last edited` bumped to 2026-06-20.

On regeneration, `curate/distill.py` rewrites this habit's `CANON.md` line (no count change — same habit). Injects to every Architect via the [ADR-0022](../adr/0022-architect-ingest-distillation-and-code-channel.md) channel.

---

## 2026-06-16 — habit status-flip — `single-source-and-deliver` Proposed → Accepted

- **Author:** Federation Architect (session 33)
- **Change type:** status-flip (Proposed → Accepted)
- **Slug:** `single-source-and-deliver`
- **Parent:** [P16 — avoid-duplication](../principles/master.md#p16--avoid-duplication)
- **Status on landing:** Accepted
- **Citation:** the operator's explicit approval, session 33 (2026-06-16): on P16 + this habit, *"Accept both?"* → *"accept."* Flipped in the same federation event as the parent principle P16 (see principles sidecar).

Promotes the habit to Accepted. On regeneration, `curate/distill.py` adds it to `CANON.md` (habit count 23 → 24); it injects to every Architect via the [ADR-0022](../adr/0022-architect-ingest-distillation-and-code-channel.md) channel.

---

## 2026-06-16 — habit added — `single-source-and-deliver`

- **Author:** Federation Architect (session 33)
- **Change type:** add
- **Slug:** `single-source-and-deliver`
- **Parent:** [P16 — avoid-duplication](../principles/master.md#p16--avoid-duplication)
- **Status on landing:** Proposed
- **Citation:** Federation Architect session 33 (2026-06-16). Demoted from the first-draft P16 statement: the operator cut P16 to the bare property *"avoid duplication"* and named this one-liner *"a habit underneath"* (see principles sidecar, same date). The concrete practice for *shared content specifically* — give it one authoritative source and deliver it (inject / point / build-assemble), never a hand-maintained copy. Names the practice already exercised by the canon channel ([ADR-0022](../adr/0022-architect-ingest-distillation-and-code-channel.md)) and extended to the standard role-doc section by [ADR-0024](../adr/0024-standard-role-doc-section-is-generated-and-injected.md); the inject-where-a-runtime-exists choice is the ADR-0024 decision. Composes with `add-structural-guard-on-recurrence` (a `--check` on a stale generated copy is the delivery guard — e.g. `distill.py --check`).

**Status flip to Accepted is NOT in this change** — pending the operator's explicit approval alongside the parent principle P16. On Accept, `curate/distill.py` regenerates `CANON.md` (habit count 23 → 24) and the line injects to every Architect via the [ADR-0022](../adr/0022-architect-ingest-distillation-and-code-channel.md) channel.

---

## 2026-06-14 — Statement plain-English rewrite — 4 P5 session habits

- **Author:** Federation Architect (session 30)
- **Change type:** edit (statement wording → one-line plain English; no semantic change; prior wording demoted to a following paragraph, not removed)
- **Affected slug(s):** `session-stamp-and-counter`, `session-orphan-detection`, `mid-session-checkpointing`, `emergency-write-and-checkpoint-commands`
- **Citation:** Federation Architect session 30 (2026-06-14). Surfaced by dogfooding the [ADR-0022](../adr/0022-architect-ingest-distillation-and-code-channel.md) distillation generator (`curate/distill.py`): these four habits' `**Statement.**` lines were multi-line lead-ins (ending in `:`), so the generated `CANON.md` digest truncated them to dangling fragments. Applied the one-line-plain-statement convention (same as the P13/P14 principle pass this session); the operator approved the four one-liners, asking for `mid-session-checkpointing` to be put in simpler English, which produced *"Save your work as you go. Don't wait until the end of the session to commit."*

The structured detail (stamp format + counter rules; the orphan question + outcomes; the commit-as-you-go bullets + wip-commit note; the write/checkpoint command specs) is retained verbatim in each entry's body below the new one-line statement. `Last edited` bumped to 2026-06-14 on all four. No change to status (all remain Accepted), parent, or meaning. **Verbose-but-complete habit statements (e.g. `session-end-handoff-before-signoff`, `session-start-git-ritual`, `stay-in-role`) are NOT touched here** — flagged for a separate consistency-tightening pass.

---

## 2026-06-14 — `add-structural-guard-on-recurrence` status-flip — Proposed → Accepted

- **Author:** Federation Architect (session 30)
- **Change type:** status-flip (Proposed → Accepted)
- **Slug:** `add-structural-guard-on-recurrence`
- **Parent:** [P15 — code-for-mechanism-not-judgment](../principles/master.md#p15--code-for-mechanism-not-judgment)
- **Status on landing:** Accepted
- **Citation:** the operator's explicit approval, session 30 (2026-06-14): *"Accept both as `Proposed` → `Accepted`?"* → **the operator: *"yes."*** Flipped alongside its parent P15 (see principles sidecar).

Promotes the child habit to Accepted in the same federation event as P15. **Federation Architect §4.2 self-adoption is NOT in this change** — routes through the [`federation-arch.md`](../federation-arch.md) §11 self-promotion-guard inbox unless the operator approves same-session adoption; redistribution to other Architects is separate per-edit work.

---

## 2026-06-14 — Add `add-structural-guard-on-recurrence`

- **Author:** Federation Architect (session 30)
- **Change type:** add
- **Slug:** `add-structural-guard-on-recurrence`
- **Parent:** [P15 — code-for-mechanism-not-judgment](../principles/master.md#p15--code-for-mechanism-not-judgment)
- **Status on landing:** Proposed
- **Citation:** Federation Architect session 30 (2026-06-14). Drafted alongside P15 (see principles sidecar). Was tagged as the `make-the-fix-structural` principle / `add-structural-guard-on-recurrence` habit in the [`no-compound-bash`](master.md#no-compound-bash) registry note (session 27, 2026-06-06) on the first structural guard; promoted here under P15, with the tagged parent folding into P15.

Statement: *"When a behavioral rule … is violated again after it was already adopted, stop relying on self-discipline and make the enforcement structural — build deterministic code (a hook, a guard, a check) that prevents or denies the violation at the substrate level. … The guard must fail open … and narrate its denial at the conversation layer."*

The concrete practice implementing the make-the-fix-structural facet of P15. Federation instance: `no-compound-bash` (adopted habit) violated twice in session 26, then enforced by the `session.py check-bash` PreToolUse hook in session 27. **Status `Proposed`** — Accept and any Federation Architect §4.2 self-adoption are separate gated events.

---

## 2026-06-05 — Add `reference-secrets-dont-transmit`

- **Author:** Federation Architect (session 24)
- **Change type:** add
- **Slug:** `reference-secrets-dont-transmit`
- **Parent:** [P14 — no-confidential-data-in-chats](../principles/master.md#p14--no-confidential-data-in-chats)
- **Status on landing:** Accepted
- **Citation:** Federation Architect session 24 (2026-06-05). Drafted as the child habit of P14 on the same incident — an Architect asked the user to paste a secret into chat. the operator approved the suggested one-line form unchanged.

Statement: *"When a system needs a credential, instruct the user where to place it (keychain, environment variable, gitignored `.env`, secret manager) and reference it by name; never request the literal value in chat. If a secret is ever exposed, prompt the user to rotate it."*

The concrete practice implementing P14: P14 names the property (no confidential data in chats); this habit names what to do instead when a credential is genuinely needed. Federation Architect §4.2 self-adoption routes through the self-promotion guard alongside P14 (see principles sidecar) — not landed in this change.

---

## 2026-06-04 — Header correction — registry classification + provenance wording

- **Author:** Federation Architect (session 19)
- **Change type:** edit (file-header wording only — no entry statement, status, parent, or slug change)
- **Affected slug(s):** none — registry preamble line only
- **Citation:** session-19 curate-pass harness design ([`design/curate-pass-harness.md`](../design/curate-pass-harness.md) §6 item 11), confirmed by the operator (2026-06-04): *"fix the bugs."*

The preamble line read *"Gitignored per [ADR-0007] — synthesized from input data,"* stale on two counts: (1) the registries were reclassified data→system and are git-tracked per [ADR-0018](../adr/0018-adopted-principles-and-habits-are-system.md) (federation-arch v1.1.0), so "gitignored" was wrong; (2) "synthesized" implied machine generation, contradicting the single-writer ([P13](../principles/master.md#p13--single-writer-per-state)), human-gated curation discipline ([ADR-0009](../adr/0009-data-storage-mechanics.md) promotion workflow). Replaced with *"System artifact, git-tracked per ADR-0018 — curated by the Federation Architect from input data … not machine-generated."* Recorded in [`federation-arch.md`](../federation-arch.md) v1.2.3 CHANGELOG. Same correction applied to [`principles/master.md`](../principles/master.md) + its sidecar in the same commit.

---

## 2026-06-03 — Provenance label scrub — a coordinator agent's name removed from a member Architect label

- **Author:** Federation Architect (session 18)
- **Change type:** edit (citation wording only — no statement, status, parent, or slug change)
- **Affected slug(s):** `session-end-handoff-before-signoff`, `session-start-git-ritual`, `session-stamp-and-counter`, `session-orphan-detection`, `mid-session-checkpointing`, `emergency-write-and-checkpoint-commands` (all carried the conflated label in their Citation field)
- **Citation:** the operator, session 18 (2026-06-03), ruled that the coordinator agent is not the Architect and directed the label fixed.

The citations labelled a member Architect with the name of its system's coordinator *agent*, conflating the two roles. Per [ADR-0006](../adr/0006-naming-convention-corrected.md) and [P4](../principles/master.md#p4--identity-boundaries-non-collapsing), these are distinct roles, and the Architect is unnamed. Scrubbed the agent's name from the citation label here and in [`master.md`](master.md) (6 occurrences). The source itself is unchanged — only the wrong name label was removed. Same scrub applied across the principles registry + sidecar, [`federation-arch.md`](../federation-arch.md), and [`bootstrap-kit/role-doc-template.md`](../bootstrap-kit/role-doc-template.md) in the same commit. Surfaced during a session-18 retrofit walk.

---

## 2026-05-27 — Add `no-fabricated-data`

- **Author:** Federation Architect (session 12)
- **Change type:** add
- **Slug:** `no-fabricated-data`
- **Parent:** [P7 — transparency-at-conversation-layer](../principles/master.md#p7--transparency-at-conversation-layer)
- **Status on landing:** Accepted
- **Citation:** In-session incident, session 12 (2026-05-27): produced a summary table entry — an end time and a duration — without running `date`. The actual time at production was more than two hours earlier. the operator caught the wrong stamp.

Habit drafted in response to the incident. Two structural options surfaced: (1) sharpen the existing `session-stamp-and-counter` habit to cover summary-table references, (2) draft a broader anti-fabrication habit covering timestamps, counts, paths, versions, hashes, and existence claims. the operator chose option 2: *"2."*

Repair-side note: I also re-encountered the prior session's habit-edit pattern problem — the previous v0.10.0 edit's *new_string* for `no-compound-bash` had inadvertently dropped the "## Status legend" header from the bottom of habits/master.md. Restored in this same commit.

Federation Architect adopts via federation-arch.md v0.11.0 (§4.2 row added). Bootstrap kit `<<HABITS_TABLE>>` guidance updated (20 → 21). Kit bumped to v0.5.0.

---

## 2026-05-27 — Add `no-compound-bash`; edit `narrate-consequential-tool-calls`; edit `mid-session-checkpointing`

- **Author:** Federation Architect (session 12)
- **Change type:** add (×1) + edit (×2)
- **Affected slugs:**
  - `no-compound-bash` (add — parent P7)
  - `narrate-consequential-tool-calls` (edit — statement expanded with specificity, no-bundled-preamble, velocity-no-excuse refinements)
  - `mid-session-checkpointing` (edit — statement sharpened with WIP-commit framing)
- **Status on landing:** all Accepted
- **Citation:** A member Architect's role doc: its narration rules (no-compound-bash, narration-specificity refinements) and its mid-session commit rules (WIP-commit framing).

Third batch of cross-Architect lifts. Source: a member Architect's role doc, mirrored under federation `inputs/`. Per the operator's session-12 review, three of its five candidate patterns passed the universal-failure-mode-breadth test; the fourth was deemed specific to that member; the fifth (cold-start reading list) was dropped as unnecessary for federation's smaller doc tree.

**Bounded-sharing open question reaffirmed:** that fourth, member-specific candidate is a concrete instance of the cross-Architect-but-bounded habit case noted in federation-arch.md §13 open questions. Not pre-solving the taxonomy amendment until a second instance appears.

Prior `narrate-consequential-tool-calls` statement: *"Before running anything that changes the machine, the repo, or outside state, say in chat what you're about to do and why. One sentence. Then run it. One narration per call — don't bundle. Missed it? One-line ack, narrate the next one."*

Prior `mid-session-checkpointing` statement bullets (unchanged); WIP-commit framing added as a new paragraph after the bullets.

Federation Architect adopts all three in the same change via federation-arch.md v0.10.0 (§4.2 row added / two rows updated). Bootstrap kit role-doc-template's `<<HABITS_TABLE>>` guidance updated (19 → 20).

---

## 2026-05-27 — Add `mid-session-checkpointing` and `emergency-write-and-checkpoint-commands`; edit `session-end-handoff-before-signoff`

- **Author:** Federation Architect (session 12)
- **Change type:** add (×2) + edit (×1)
- **Affected slugs:**
  - `mid-session-checkpointing` (add)
  - `emergency-write-and-checkpoint-commands` (add)
  - `session-end-handoff-before-signoff` (edit — statement sharpened with write-before-the-social-close framing)
- **Parent:** all three under [P5 — session-continuity-survives-cold-restart](../principles/master.md#p5--session-continuity-survives-cold-restart)
- **Status on landing:** Accepted
- **Citation:** A member's role doc: its write protocol (topic-break writes, a single-key write-and-close command) and its design rules (write before the social close; sessions end without warning). The checkpoint command was added in a later session of that member (2026-05-26).

Second batch of session-rituals adoptions from that member. Completes the session-durability surface end-to-end:

| Failure mode | Mitigation habit |
|---|---|
| Session crashes after sign-off | `session-end-handoff-before-signoff` (in-place, sharpened today) |
| Session crashes mid-flight, work lost since session start | `mid-session-checkpointing` (new) |
| Session crashed and never reopened cleanly | `session-orphan-detection` (added earlier on 2026-05-27) |
| User needs to bail fast or harden state without ending | `emergency-write-and-checkpoint-commands` (new) |

Prior `session-end-handoff-before-signoff` statement: *"Before signing off (commit, push, 'we're done,' 'goodnight'), write a handoff entry. Date, what happened, what's next, open questions. Newest first. Never sign off without it."*

Federation Architect adopts all three in the same change via federation-arch.md v0.9.0 (§4.2 rows added / row updated). Bootstrap kit role-doc-template's §4.2 guidance list updated to v0.9.0 set (17 → 19 habits).

---

## 2026-05-27 — Add `session-stamp-and-counter` and `session-orphan-detection`; edit `session-start-git-ritual`

- **Author:** Federation Architect (session 12)
- **Change type:** add (×2) + edit (×1)
- **Affected slugs:**
  - `session-stamp-and-counter` (add)
  - `session-orphan-detection` (add)
  - `session-start-git-ritual` (edit — statement amended with silent-resync framing)
- **Parent:** all three under [P5 — session-continuity-survives-cold-restart](../principles/master.md#p5--session-continuity-survives-cold-restart)
- **Status on landing:** Accepted
- **Citation:** A member's session-start and session-end protocols (2026-05-21) for stamp format and orphan detection; a later revision of its session-start protocol (2026-05-22) for silent-resync framing on the existing `session-start-git-ritual` habit.

The Federation Architect's session-rituals surface lifted three behaviors out of that member's protocol — explicit stamp lines with version + machine + timestamp, a monotonic session counter with corruption tagging, and orphan-session detection on cold restart. The first two are fresh habits; the third is an amendment to the existing `session-start-git-ritual` habit (statement clarified to require *silent* refresh — never raise sync state as a user-visible launch-folder issue).

Prior `session-start-git-ritual` statement: *"At session start: `git status`. Clean? Pull. Dirty? Stop and ask. Then read the handoff and summarize where we are. Don't make the user type 'what's next?'"*

All three pair with the existing `session-end-handoff-before-signoff` and `session-end-git-ritual` to make session boundaries auditable across crashes and machines.

Federation Architect adopts all three in the same change via federation-arch.md v0.8.0 (§4.2 rows added / row updated). Per ADR-0010, the role doc is the canonical adoption record. Bootstrap kit role-doc template's §4.2 guidance list updated in the same commit to keep new-Architect onboardings aligned with the current registry.

---

## 2026-05-26 — Add `parallel-work-decomposition`

- **Author:** Federation Architect (session 11)
- **Change type:** add
- **Slug:** `parallel-work-decomposition`
- **Parent:** [P12 — manage-scope-for-schedule](../principles/master.md#p12--manage-scope-for-schedule)
- **Status on landing:** Accepted (companion habit registered on [ADR-0015](../adr/0015-work-package-briefs.md) Accept)
- **Citation:** Federation Architect sessions 7–9 (2026-05-25 through 2026-05-26). Adopted-in-fact since session 7 (4 brief dispatches across sessions 7–9). Formal registration on ADR-0015 Accept (session 11, 2026-05-26).

Companion habit to [`session-vision-anchoring`](master.md#session-vision-anchoring) under the same parent (P12). The habit is the planning-time decomposition question (*can this work be split and dispatched in parallel?*); the mechanism (work-package briefs, stage-and-stop semantics, Open-for-the operator discipline, fold-back flow) is specified in ADR-0015 and not duplicated here.

Adoption by Federation Architect lands in the same change via federation-arch.md v0.7.0 (§4.2 row added). Per ADR-0010, the role doc is the canonical adoption record.

---

## 2026-05-24 — Add `session-vision-anchoring`

- **Author:** Federation Architect (session 6)
- **Change type:** add
- **Slug:** `session-vision-anchoring`
- **Parent:** [P12 — manage-scope-for-schedule](../principles/master.md#p12--manage-scope-for-schedule)
- **Status on landing:** Accepted (same-session draft → accept, the operator-approved; precedent: P11/P12 in session 5)
- **Citation:** Federation Architect session 5 (2026-05-23) — the operator observed that the output was good but the vision was hard to hold on to in the middle of the details. Deferred from session 5 as the concrete practice implementing the P11/P12 shipping cluster. Drafted and accepted in session 6 (2026-05-24).

Companion habit to the shipping cluster (P11 names the goal, P12 the lever, this habit pulls the lever in-session). Parent is P12 alone, not P11+P12: every move in the habit (time-budget, vision pull-back, decision-forcing, scope surfacing) is a scope-management move; P11 anchors the failure-mode argument but doesn't directly govern in-session behavior.

Adoption by Federation Architect lands in the same change via federation-arch.md v0.5.0 (§4.2 row added). Per ADR-0010, the role doc is the canonical adoption record.

---

## 2026-05-23 — Plain-English statement rewrites

- **Author:** Federation Architect (session 5)
- **Change type:** edit (all 13 entries; statement field only)
- **Citation:** the operator, session 5 (2026-05-23). Same pass as the principles statement rewrites. Fancy/corporate phrasing replaced with plain English. Slugs, parent principles, failure-mode arguments, and citations unchanged.

---

## 2026-05-23 — Adoption fields stripped from all entries

- **Author:** Federation Architect (session 5)
- **Change type:** edit (schema change applied to all 13 entries)
- **Citation:** [ADR-0010](../adr/0010-registries-canon-only-sidecar-history.md) — adoption is not registry-resident; it lives in the adopting Architect's role doc.

Each habit entry previously carried an "Adoption" subsection enumerating source-member / Federation Architect / Other-Architects status. Per ADR-0010, that information is duplicative of the role-doc record and was hand-maintained, making it drift-prone. All "Adoption" subsections and the "Adoption-status legend" at the bottom of the file were removed in the same change.

The prior adoption state (as of 2026-05-23 init) was, for the record:

| Habit slug | Source member | Federation Architect | Other |
|---|---|---|---|
| `exhaust-questions-in-planning` | adopted | de-facto practiced; pending v0.4.0 formalization | pending |
| `versioned-role-doc-and-changelog` | adopted | adopted v0.1.0+ | pending |
| `gitignore-enforces-data-system-boundary` | adopted | adopted (ADR-0007) | pending |
| `stay-in-role` | adopted | adopted in policy (federation-arch.md §3); pending explicit habit-level framing | pending |
| `session-end-handoff-before-signoff` | adopted | adopted (federation-arch.md §10) | pending |
| `producer-side-learnings` | adopted | pending (no `architect-learnings.md` stood up yet) | pending |
| `narrate-consequential-tool-calls` | adopted | partial; practiced but not formalized | pending |
| `structured-decision-presentation` | adopted | partial; reinforced by a local memory | pending |
| `conventional-commits` | adopted | pending (forward-only) | pending |
| `session-start-git-ritual` | adopted | pending | pending |
| `session-end-git-ritual` | adopted | adopted in practice (federation-arch.md §10) | pending |
| `confirm-destructive-ops` | adopted | adopted (ADR-0007) | pending |
| `routine-ops-autonomy` | adopted (de-facto) | adopted (ADR-0007) | pending |

Going forward, "pending v0.4.0 formalization" entries for Federation Architect are addressed by the v0.4.0 bump landing the habit in federation-arch.md. The role doc becomes the canonical record per ADR-0010.

---

## 2026-05-23 — Initialization

- **Author:** Federation Architect (session 4, retroactively logged in session 5 per [ADR-0010](../adr/0010-registries-canon-only-sidecar-history.md))
- **Change type:** add

Universal habit registry created with 13 entries, all status `Accepted`, derived from the session 4 re-classification pass over candidates from a member Architect's role doc (see session-handoff.md session 4 entry). Each carries a named parent principle per the [ADR-0008](../adr/0008-three-bucket-taxonomy.md) requirement that universal habits implement an underlying principle.

| Slug | Parent | Source |
|---|---|---|
| `exhaust-questions-in-planning` | P1 | A member's role doc |
| `versioned-role-doc-and-changelog` | P2 | A member's role doc; a member-side ADR |
| `gitignore-enforces-data-system-boundary` | P3 | A member's role doc; [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md) |
| `stay-in-role` | P4 | A member's role doc; federation-arch.md §3 |
| `session-end-handoff-before-signoff` | P5 | A member's role doc |
| `producer-side-learnings` | P6 | A member's role doc |
| `narrate-consequential-tool-calls` | P7 | A member's role doc; a member-side ADR |
| `structured-decision-presentation` | P8 | A member's role doc |
| `conventional-commits` | P2 | A member's role doc |
| `session-start-git-ritual` | P5 | A member's role doc |
| `session-end-git-ritual` | P5 | A member's role doc |
| `confirm-destructive-ops` | P9 | A member's role doc; [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md) |
| `routine-ops-autonomy` | P10 | A member's role doc; [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md) |

**Scope decisions captured in session 4 (matter for future edits):**

- The `stay-in-role` habit was initially demoted to member-specific, then re-promoted to universal after the operator's session-4 challenge — see the parent-principle entry's narrower-vs-broader framing in `master.md`.
- "Recovery from correction = one-line ack" was folded into `narrate-consequential-tool-calls` rather than retained as a standalone preference.
- The handoff doc lists several Federation Architect adoption rows as "pending v0.4.0 bump" — those are not action items for this registry; they are action items for federation-arch.md, which is the canonical adoption record per [ADR-0010](../adr/0010-registries-canon-only-sidecar-history.md).
