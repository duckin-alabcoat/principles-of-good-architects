# Principles history

Append-only change log for [`master.md`](master.md). Newest-first. Per [ADR-0010](../adr/0010-registries-canon-only-sidecar-history.md).

Each entry records: date, change type (add / edit / status-flip / remove / rename), affected slug(s), prior value for edits or removes, citation that justified the change, author. Cold-readable — someone reading this file alone can reconstruct what happened.

---

## 2026-09-28 — provenance prose generalized, round 2 (WI-0448)

- **Author:** Federation Architect (WI-0448 fix lane)
- **Change type:** edit (provenance / argument prose only; **no Statement changed**)

Provenance and argument prose were generalized (WI-0447/WI-0448); no Statement changed. `CANON.md` is unaffected.

---

## 2026-09-28 — provenance prose generalized, round 1 (WI-0448)

- **Author:** Federation Architect (WI-0448 fix lane)
- **Change type:** edit (provenance / argument prose only; **no Statement changed**)

Provenance and argument prose were generalized (WI-0447/WI-0448); no Statement changed. `CANON.md` is unaffected.

---

## 2026-09-27 — provenance prose generalized (WI-0448)

- **Author:** Federation Architect (WI-0448 sweep lane)
- **Change type:** edit (provenance / argument prose only; **no Statement changed**)

Provenance and argument prose were generalized (WI-0447/WI-0448); no Statement changed. `CANON.md` is unaffected.

---

## 2026-07-27 — P21 added + Accepted — `state-survives-failure`

- **Author:** Federation Architect (session 99/~100)
- **Change type:** add (P21) + status-flip (Proposed → Accepted), same session
- **Slug:** `state-survives-failure`
- **Status on landing:** Accepted
- **Citation:** Classified in the 2026-07-05 operational-patterns harvest (a withheld proposal) (session 47, 2026-07-05) from system reviews, drafted as "P19" (that slot taken by [P19](master.md#p19--cap-what-can-run-away) at session 58, hence renumbered P21 here — the statement is unchanged from the 2026-07-05 draft). Sat unaccepted for 22 sessions. the operator, session 99/~100, accepted the principle, all six harvested habit candidates, and green-lit drafting the two pattern-briefs, in one pass.

Universal principle (durability). Parents three new habits Accepted this session: [`journaled-reversible-writes`](../habits/master.md#journaled-reversible-writes), [`verified-backup-of-non-derivable-state`](../habits/master.md#verified-backup-of-non-derivable-state) (composes P18), and [`retention-enforced-by-code`](../habits/master.md#retention-enforced-by-code) (parented P15 instead, per the harvest doc's own re-homing recommendation). Three sibling habits from the same harvest — `scheduled-liveness-smoke-test`, `security-drift-check`, `denial-audit-log` — are liveness/vigilance patterns, deliberately parented under P18/P7 instead, not this principle (the harvest doc's explicit scoping call, to keep P21 to durability specifically rather than one over-broad principle). `curate/distill.py` regen (**20 → 21 principles**) at the curate-pass batch close.

---

## 2026-07-12 — P20 added + Accepted — `untrusted-data-stays-untrusted`

- **Author:** Federation Architect (session 58)
- **Change type:** add (P20) + status-flip (Proposed → Accepted), same session
- **Slug:** `untrusted-data-stays-untrusted`
- **Status on landing:** Accepted
- **Citation:** Session-58 curate pass, from a producer-side learning (self-tagged `domain-general` — the injection-laundering-channel learning; cf. [ADR-0033](../adr/0033-non-derivable-data-is-backed-up-at-machine-level.md)). Named and shaped with the operator, session 58 — he named it *"Untrusted Data Stays Untrusted"* and gave the explicit registry-Accept.

Universal principle (taint / confused-deputy / Bobby-Tables). Two child habits, both Accepted this session: [`treat-inbound-payload-as-data-not-commands`](../habits/master.md#treat-inbound-payload-as-data-not-commands) (no-boundary case) and [`enforce-the-data-code-boundary-where-it-exists`](../habits/master.md#enforce-the-data-code-boundary-where-it-exists) (boundary case). Enforcement is judgment + recurrence-triggered sink guards (further sink guards wait for a recurrence; none is built on anticipation). `curate/distill.py` regen (**19 → 20 principles**) + version bumps at the curate-pass batch close.

---

## 2026-07-12 — P19 added + Accepted — `cap-what-can-run-away`

- **Author:** Federation Architect (session 58)
- **Change type:** add (P19) + status-flip (Proposed → Accepted), same session
- **Slug:** `cap-what-can-run-away`
- **Status on landing:** Accepted
- **Citation:** the operator, session 58 (2026-07-12) — directed the new principle, supplied its content (per-place spend authorization, budget reporting, graduated emergency cutoff; no-authorization / hard-cutoff posture), approved the name *"Cap what can run away,"* and gave the explicit registry-Accept. From [ADR-0042](../adr/0042-deployment-classes-and-promotion-discipline.md) §3 + the parked producer candidates (federation `architect-learnings.md`, 2026-07-12 outcome-D harvest).

Universal principle (binds every deployment class). Its concrete practices are **class-bound habits** (`binds-to: cloud-deployed`): [`hard-cost-ceiling-as-design-constraint`](../habits/master.md#hard-cost-ceiling-as-design-constraint), [`spend-is-budget-reported`](../habits/master.md#spend-is-budget-reported), [`hard-financial-kill-switch`](../habits/master.md#hard-financial-kill-switch) — see the habits sidecar. `curate/distill.py` regenerated `CANON.md` (**18 → 19 principles**; the three class-bound habits are excluded from the universal digest, so the habit count stays 28). Statement wording is the Architect's draft; the operator wordsmiths principle statements (cf. P16/P17/P18) and approved this one.

---

## 2026-07-02 — P17 edit (generalization argument) + cleanup (stale status prose, P16/P17/P18)

- **Author:** Federation Architect (session 45)
- **Change type:** edit (generalization argument, P17; statement unchanged) + cleanup (stale pending-approval prose, P16/P17/P18 provenance)
- **Slugs:** `ask-in-prose-not-pickers` (argument), `avoid-duplication` / `verify-everything` (prose cleanup only)
- **Status on landing:** all three remain Accepted (no flips)
- **Citation:** the operator, 2026-07-02, during the external consultant review (Auditor engagement); brief `2026-07-02-p17-generalization-argument-hardening` drafted at his direction, landing approved the same day.

**P17 argument rewrite.** The consultant flagged P17's generalization argument as the thinnest in the registry — "degrades by construction" asserted, not shown, and no answer to "some questions *are* multiple choice." the operator supplied the real argument; the new paragraph installs his two mechanisms: **(1) enumeration failure** — a picker structurally asserts the answer space is known, fully enumerated, mutually exclusive, and premise-accepting, and design/intake questions violate all four (the ice-cream example: off-menu, combination, "what if I didn't want ice cream?"); **(2) false-detail capture** (load-bearing) — a picker leads the LLM to believe it has gathered detail: a picker answer arrives as a clean validated datum, so the Architect records requirements-gathering when the user was constrained, not consulted — a counterfeit of P1 and a P18 failure (the click verifies the click, not intent). Explicitly covers the closed-enumerable case (no carve-out: the false-detail mechanism operates regardless, and prose-with-recommendation handles it at trivial cost). Prior paragraph (superseded): the "tooling-layer surface / pre-baked option space / degrades by construction" argument recorded in the 2026-06-19 add entry below. Statement unchanged → no canon event; `curate/distill.py --check` verified `CANON.md` unaffected.

**Stale-prose cleanup.** Struck three Proposed-era sentences that survived their own status-flips (all three flips properly approved per this sidecar: P16 session 33, P17 + P18 session 42): P16 "…flip pending his explicit approval", P17/P18 "…the Proposed → Accepted flip awaits his explicit approval per §9." Status fields were already correct; prose now matches.

---

## 2026-06-21 — P18 status-flip — `verify-everything` Proposed → Accepted

- **Author:** Federation Architect (session 42)
- **Change type:** status-flip (Proposed → Accepted)
- **Slug:** `verify-everything`
- **Status on landing:** Accepted
- **Citation:** the operator confirmed the Accept in the session-42 curate pass (statement *"Verify everything."*).

Accepted as part of the session-42 batch Accept. Implementing habits set this session: [`no-fabricated-data`](../habits/master.md#no-fabricated-data) re-homed from P7 → P18 (don't-invent-values is a special case of verify-everything; composes-with P7 retained), and new child habit [`capture-the-probe`](../habits/master.md#capture-the-probe) added (Accepted). The `feedback-validate-against-real-target` memory stays situational (covered by P18, not minted). `curate/distill.py` regenerated `CANON.md` (**17 → 18 principles**); federation + kit versions bumped once for the whole batch.

---

## 2026-06-21 — P18 added — `verify-everything`

- **Author:** Federation Architect (session 42)
- **Change type:** add (P18)
- **Slug:** `verify-everything`
- **Status on landing:** Proposed
- **Citation:** Federation Architect session 42 (2026-06-21), curate review of `REVIEW-20260621-131951Z.md`. the operator ruled it a new principle and named it "verify everything".

Statement (the operator's wording, bare property): *"Verify everything."* Promoted from a cross-system, multi-instance cluster surfaced in the curate pass: the property "check the real thing against its source before relying on it or asserting it." A member Architect's instances — read code before drafting a security doc, capture the probe not just the verdict, use the capabilities ledger not the roadmap narrative; Federation instances — validate against the real target before shipping (`e1d76234637a`), re-verify a prior "confirmation" (`61756d76f003`), ship-then-verify / emit ≠ apply (`9e3297cd1030`). Distinct from but composing with [`no-fabricated-data`](../habits/master.md#no-fabricated-data) (P7 — don't invent values) and [P1](master.md#p1--ambiguity-paid-down-before-commitment) (resolve unknowns before committing): broader than fabrication-avoidance, it is the standing duty to verify.

**Status flip to Accepted is NOT in this change** — it requires the operator's explicit approval per [ADR-0009](../adr/0009-data-storage-mechanics.md). Statement wording is the Architect's draft; the operator wordsmiths principle statements (cf. P16, P17). On Accept: `curate/distill.py` regenerates `CANON.md` (17 → 18 principles); taxonomy cleanup re-homes/dual-cites `no-fabricated-data` under P18 and adds child habits for "capture the probe" and "validate against the real target."

---

## 2026-06-21 — P17 status-flip + edit — `ask-in-prose-not-pickers` Proposed → Accepted

- **Author:** Federation Architect (session 42)
- **Change type:** status-flip (Proposed → Accepted) + edit (statement)
- **Slug:** `ask-in-prose-not-pickers`
- **Status on landing:** Accepted
- **Citation:** Federation Architect session 42 (2026-06-21). the operator approved the Accept and directed the wording change: the Architect lists the pros and cons of each option.

Prior statement (Proposed draft): *"Ask the user in prose, never a picker — recommend with reasoning, then confirm."* New statement: *"Ask the user in prose, never a picker — lay out each option with its pros and cons, give a reasoned recommendation, then confirm."* The edit pulls the pros-and-cons-of-each-option requirement up from the implementing habit ([`structured-decision-presentation`](../habits/master.md#structured-decision-presentation), which already carried it) into the principle statement itself; the following paragraph gained a scoping line (*"Simple yes/no confirmations don't need an option table"*) so the requirement binds only genuine multi-option decisions.

Accept cascade run this session: `curate/distill.py` regenerated `CANON.md` (16 → 17 principles) + `bootstrap-kit/CANON.md`, injected to every Architect via the [ADR-0022](../adr/0022-architect-ingest-distillation-and-code-channel.md) channel; taxonomy cleanup re-homed `structured-decision-presentation` to dual-cite P17, re-pointed the `check-question` guard reason string at P17, and folded the universal core of the `recommend-then-confirm` preference into a child habit.

---

## 2026-06-19 — P17 added — `ask-in-prose-not-pickers`

- **Author:** Federation Architect (session 40)
- **Change type:** add (P17)
- **Slug:** `ask-in-prose-not-pickers`
- **Status on landing:** Proposed
- **Citation:** Federation Architect session 40 (2026-06-19). the operator directed that the practice be raised to a principle.

Statement (Architect draft): *"Ask the user in prose, never a picker — recommend with reasoning, then confirm."* Promotes the universal core of an existing operator-profile practice, `recommend-then-confirm`, to an Architect-universal principle. Trigger: a freshly-bootstrapped member Architect opened its first session with an `AskUserQuestion` picker despite the rule being in its profile (the rule lived only as text, so it bound nothing); session 40 added the `check-question` `PreToolUse(AskUserQuestion)` structural guard, and the operator judged the property principle-grade — *"the picker degrades the interaction by construction, not as a quirk of one user's taste."*

The argument names the picker as a tooling-layer surface that hides reasoning and pre-bakes the option space (composes with P7 conversation-layer, P8 decisions-auditable; distinct in governing the *delivery mechanism* for every question + the never-proceed-on-an-unanswered floor). Implementing practices: existing habit [`structured-decision-presentation`](../habits/master.md#structured-decision-presentation) (P8) and the new `check-question` guard ([`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence) / P15).

**Status flip to Accepted is NOT in this change** — it requires the operator's explicit approval per [ADR-0009](../adr/0009-data-storage-mechanics.md). The statement wording is the Architect's draft and the operator wordsmiths principle statements (cf. P16); the Accept flip awaits his explicit go and likely a wording pass. On Accept: `curate/distill.py` regenerates `CANON.md` (16 → 17 principles), the line injects to every Architect via the [ADR-0022](../adr/0022-architect-ingest-distillation-and-code-channel.md) channel, and a taxonomy-cleanup pass re-homes the child habits + folds the universal core of the `recommend-then-confirm` preference into a child habit.

---

## 2026-06-16 — P16 status-flip — `avoid-duplication` Proposed → Accepted

- **Author:** Federation Architect (session 33)
- **Change type:** status-flip (Proposed → Accepted)
- **Slug:** `avoid-duplication`
- **Status on landing:** Accepted
- **Citation:** the operator's explicit approval, session 33 (2026-06-16). On the recut P16 (*"Avoid duplication."*) + child habit, the operator accepted both. Statement is the operator's own wording.

Promotes P16 to Accepted (same session as the add). Downstream propagation now in scope per [ADR-0008](../adr/0008-three-bucket-taxonomy.md). Child habit [`single-source-and-deliver`](../habits/master.md#single-source-and-deliver) flipped to Accepted in the same federation event (see habits sidecar). Per [ADR-0022](../adr/0022-architect-ingest-distillation-and-code-channel.md) the universal set is inherited via the injected `CANON.md` — no separate federation-arch §4 adoption edit; `curate/distill.py` regenerates `CANON.md` (15 → 16 principles) and the new line reaches every Architect on next session start. Redistribution as explicit role-doc edits to other Architects is separate per-edit promotion work.

---

## 2026-06-16 — P16 added — `avoid-duplication` (+ child habit `single-source-and-deliver`)

- **Author:** Federation Architect (session 33)
- **Change type:** add (P16) + child-habit add (see habits sidecar)
- **Slug:** `avoid-duplication`
- **Status on landing:** Proposed
- **Citation:** Federation Architect session 33 (2026-06-16). Surfaced while drafting [ADR-0024](../adr/0024-standard-role-doc-section-is-generated-and-injected.md) (the standard role-doc section is generated and injected); flagged in that ADR's Consequences as a possible generalization of the canon-channel pattern beyond the single case. the operator directed its promotion to a principle.

Statement: *"Avoid duplication."* (the operator's wording.) **Drafting note — slug + statement changed during this same session:** the first draft was slugged `shared-content-delivered-from-one-source` with statement *"If many roles need the same thing, keep it in one place and hand it out — never copy it into each"* (committed `4743007`). the operator cut it to the bare property, "avoid duplication", with the one-liner as a habit underneath — on the reasoning that the first statement fused the property with one of its mechanisms (and didn't even cover non-content duplication like logic). The principle is now the property *"avoid duplication"*; the mechanism one-liner demoted to the new child habit [`single-source-and-deliver`](../habits/master.md#single-source-and-deliver). Git holds the intermediate draft; this entry records the landed-draft shape.

The harm the argument names is **silent drift** (copies diverge, with no announcement) and the bounded-vs-unbounded asymmetry (de-dup is one-time; drift recurs at every copy site). Generalizes the property already exercised by `CANON.md` ([ADR-0022](../adr/0022-architect-ingest-distillation-and-code-channel.md)), `STANDARD.md` ([ADR-0024](../adr/0024-standard-role-doc-section-is-generated-and-injected.md)), and one member's agent-layer prior art (facts kept as data in one file; prompts treated as build artifacts from one source). Composes with P13 (single-writer — content exists once, written by one), sibling of P15 (structure over discipline — content vs. computation), refines P3 (facts-are-data = P3 + P16).

**Status flip to Accepted is NOT in this change** — it requires the operator's explicit approval per [ADR-0009](../adr/0009-data-storage-mechanics.md). The statement wording is now the operator's; the Accept flip (principle + child habit together) is pending his explicit go. On Accept, `curate/distill.py` regenerates `CANON.md` (15 → 16 principles, 23 → 24 habits) and the new lines inject to every Architect via the [ADR-0022](../adr/0022-architect-ingest-distillation-and-code-channel.md) channel.

---

## 2026-06-14 — Statement plain-English rewrite — P13, P14

- **Author:** Federation Architect (session 30)
- **Change type:** edit (statement wording → plain English; no semantic change; prior wording demoted to a following paragraph, not removed)
- **Affected slug(s):** `single-writer-per-state` (P13), `no-confidential-data-in-chats` (P14)
- **Citation:** the operator, session 30 (2026-06-14), ruled that P13 and P14 had the same statement issue — bring both into line with the one-line plain-English statement convention (as P1–P10 were rewritten in session 5, and P15 this session). Final wording is the operator's own: P13 *"One writer per file."* and P14 *"LLMs don't handle secrets."*

P13 statement compressed to *"One writer per file."*; the prior two-sentence version (unique authoritative writer + controlled-mechanism detail) is retained verbatim as the paragraph below it. P14 statement compressed to *"LLMs don't handle secrets."*; the prior wording — the session-24 version the operator had approved verbatim — is retained verbatim as the paragraph below it, so the approved text is preserved, just no longer the lead. No change to either principle's meaning, generalization argument, provenance, or status (both remain Accepted).

---

## 2026-06-14 — P15 status-flip — `code-for-mechanism-not-judgment` Proposed → Accepted

- **Author:** Federation Architect (session 30)
- **Change type:** status-flip (Proposed → Accepted)
- **Slug:** `code-for-mechanism-not-judgment`
- **Status on landing:** Accepted
- **Citation:** the operator's explicit approval, session 30 (2026-06-14). On the drafted P15 + child habit, *"Accept both as `Proposed` → `Accepted`?"* → **the operator: *"yes."*** Statement is the operator's own wording (*"Use code for deterministic things. Use the LLM when code won't work."*).

Promotes P15 to Accepted (same session as the add). Downstream propagation is now in scope per [ADR-0008](../adr/0008-three-bucket-taxonomy.md). Child habit [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence) flipped to Accepted in the same federation event (see habits sidecar). **Federation Architect §4.1 self-adoption is NOT in this change** — adding P15 to [`federation-arch.md`](../federation-arch.md) §4 is an adoption event that routes through the §11 self-promotion-guard inbox unless the operator approves same-session adoption. Redistribution to other Architects is separate per-edit promotion work.

---

## 2026-06-14 — P15 added — `code-for-mechanism-not-judgment`

- **Author:** Federation Architect (session 30)
- **Change type:** add (P15)
- **Slug:** `code-for-mechanism-not-judgment`
- **Status on landing:** Proposed
- **Citation:** Federation Architect session 30 (2026-06-14). the operator, on a member Architect's missing session-start stamp, asked how much of the LLM work could be code and whether a code-versus-LLM principle already existed, then directed that it be drafted. Review found the federation applies the code/judgment split to itself ([`session.py`](../session.py), [`curate/gather.py`](../curate/gather.py), `check-bash`) and states it in [ADR-0019](../adr/0019-curate-gather-and-staging-boundary.md) / [ADR-0020](../adr/0020-session-rituals-are-a-code-harness.md) (*"code does the mechanics; the Architect keeps the judgment"*), but never promoted it to a principle — so it lived only in a local memory note and reached no other Architect (same failure shape as `recommend-then-confirm`).

Statement: *"Use code for deterministic things. Use the LLM when code won't work."* (the operator's exact wording, session 30.) The statement is followed in the registry by the kept elaboration — the deterministic-work examples, the cuts-both-ways framing (mechanism out of the LLM, judgment out of code), and the *"is the output determined by the inputs?"* test. (Statement compressed to plain English on the operator's direction that a statement be one line in simple English, then set to his exact wording; the detail was demoted to a following paragraph, not removed.)

The tagged-but-unpromoted `make-the-fix-structural` (named in the `no-compound-bash` habit note, session 27) **folds into P15** as a facet (enforcement is deterministic work) rather than landing as a separate principle. Its concrete practice promotes as the child habit [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence) (see habits sidecar). Placed at the end of display order (after P14), before the Status legend.

**Status is `Proposed`, not Accepted** — drafted under the §9 autonomous-Proposed authority on the operator's "yes." Accept requires the operator's explicit approval. Federation Architect §4.1 self-adoption is a separate adoption event that routes through the [`federation-arch.md`](../federation-arch.md) §11 self-promotion-guard inbox unless the operator approves same-session; redistribution to other Architects is separate per-edit promotion work.

---

## 2026-06-05 — P14 added — `no-confidential-data-in-chats`

- **Author:** Federation Architect (session 24)
- **Change type:** add (P14)
- **Slug:** `no-confidential-data-in-chats`
- **Status on landing:** Accepted
- **Citation:** Federation Architect session 24 (2026-06-05). An Architect had asked the user to paste a secret into chat; the operator directed a principle for handling confidential data. Approved as P14, which the operator named *"No confidential data in chats"*; the statement landed unchanged from the chat draft, which the operator approved as written.

Statement: *"Never ask the user to hand you a secret. Passwords, API keys, tokens, anything confidential — the user puts it in a secret store (keychain, environment variable, gitignored `.env`, secret manager) and you reference it by name. You never request the literal value in chat, never write it to a file, never echo it back. If a secret does get exposed, tell the user to rotate it."*

Safety-floor principle in the family of P3 / P9: composes with P3 (data stays out of system files — secrets are the most sensitive case) and P9 (irreversibility of a leak — can only be rotated, not undone) while naming a distinct property — the *handling discipline* for confidential credentials (never solicit, never transmit, never store). Placed at the end of display order (after P13), before the Status legend. Child habit [`reference-secrets-dont-transmit`](../habits/master.md#reference-secrets-dont-transmit) added in the same federation event (see habits sidecar).

**Federation Architect §4.1 self-adoption is NOT in this change.** Per the [`federation-arch.md`](../federation-arch.md) §11 self-promotion guard, adding P14 to §4.1 is an adoption event and routes through `proposed-edits/federation-arch/pending/` for a separate session unless the operator approves same-session adoption. Registry promotion to Accepted (this entry) is independent of that self-adoption. Redistribution to other Architects is separate promotion-pass work, gated per-edit.

---

## 2026-06-04 — Header correction — registry classification + provenance wording

- **Author:** Federation Architect (session 19)
- **Change type:** edit (file-header wording only — no entry statement, status, slug, or parent change)
- **Affected slug(s):** none — registry preamble line only
- **Citation:** session-19 curate-pass harness design ([`design/curate-pass-harness.md`](../design/curate-pass-harness.md) §6 item 11), confirmed by the operator (2026-06-04), who directed the fixes.

The preamble line read *"Gitignored per [ADR-0007] — synthesized from input data,"* stale on two counts: (1) the registries were reclassified data→system and are git-tracked per [ADR-0018](../adr/0018-adopted-principles-and-habits-are-system.md) (federation-arch v1.1.0), so "gitignored" was wrong; (2) "synthesized" implied machine generation, contradicting the single-writer ([P13](master.md#p13--single-writer-per-state)), human-gated curation discipline ([ADR-0009](../adr/0009-data-storage-mechanics.md) promotion workflow). Replaced with *"System artifact, git-tracked per ADR-0018 — curated by the Federation Architect from input data … not machine-generated."* Recorded in [`federation-arch.md`](../federation-arch.md) v1.2.3 CHANGELOG. Same correction applied to [`habits/master.md`](../habits/master.md) + its sidecar in the same commit.

---

## 2026-06-03 — Provenance label scrub — an orchestrator agent's name removed from a member Architect label

- **Author:** Federation Architect (session 18)
- **Change type:** edit (provenance/citation wording only — no statement, status, or slug change)
- **Affected slug(s):** P13 (`single-writer-per-state`)
- **Citation:** the operator, session 18 (2026-06-03), ruled that the coordinator agent is not the Architect and directed the label fix.

The provenance labelled a member Architect with the name of that member's coordinator *agent*, conflating the two roles. Per [ADR-0006](../adr/0006-naming-convention-corrected.md) and [P4](master.md#p4--identity-boundaries-non-collapsing), the Architect and the orchestrator/coordinator agent are distinct roles, and the Architect is unnamed. Scrubbed the agent's name from the label in P13's provenance here and in [`master.md`](master.md). A reference to the coordinator agent inside the cited material (as a sole writer in that member's design) was correct and was left intact. Same scrub applied across [`habits/master.md`](../habits/master.md) + sidecar, [`federation-arch.md`](../federation-arch.md), and [`bootstrap-kit/role-doc-template.md`](../bootstrap-kit/role-doc-template.md) in the same commit. Surfaced during a session-18 member retrofit walk.

---

## 2026-05-27 — P13 added — `single-writer-per-state`

- **Author:** Federation Architect (session 12)
- **Change type:** add (P13)
- **Slug:** `single-writer-per-state`
- **Status on landing:** Accepted
- **Citation:** A member Architect's role doc, which lists single-writer-per-state among its design principles. Federation-side implicit practice since session 4 (Federation Architect writes registries; per-Architect role docs written by their own Architects; receipt ritual is the controlled cross-writer mechanism). Promoted to explicit principle in session 12 per the operator's adopt-as-exists direction following a pattern-recognition pass over that member.

Statement: *"Every piece of authoritative state has a unique authoritative writer. Cross-writer influence flows through controlled mechanisms (proposals, receipt rituals, flag queues) — never through direct concurrent writes to shared state."*

P13 composes with P3 (data-system separation — what goes where) and P4 (identity boundaries — one role doesn't write as another) by naming *who* writes each artifact. Cross-writer mechanisms in the federation that already satisfy P13: receipt ritual ([ADR-0013](../adr/0013-receipt-ritual.md)), federation-edit proposals ([ADR-0014](../adr/0014-existing-architect-retrofit.md)), per-Architect role-doc ownership ([ADR-0010](../adr/0010-registries-canon-only-sidecar-history.md)). The principle is therefore *retroactively named*, not newly imposed — current practice already conforms.

Adoption by Federation Architect lands in the same change via federation-arch.md v0.9.0 (§4.1 row added — also includes the P11/P12 cleanup that was lagging from sessions 5–6). Per ADR-0010, the role doc is the canonical adoption record. Bootstrap kit role-doc-template §4.1 guidance updated to v0.9.0 set.

---

## 2026-05-23 — P12 added

- **Author:** Federation Architect (session 5)
- **Change type:** add (P12)
- **Citation:** the operator, session 5 (2026-05-23), framed it as managing feature requests to keep the project on schedule — a positive, action-oriented framing replacing an earlier defensive "drift-resistance" draft. Broadened from "feature requests" to "scope" to catch Architect-side over-delivery, the dominant failure mode in session 5.

P12 (`manage-scope-for-schedule`) statement: *"Manage scope to keep the project on schedule."* Placed in display order directly after P11 — together they form the shipping cluster (P11 = goal, P12 = lever).

---

## 2026-05-23 — Plain-English statement rewrites + P11 added

- **Author:** Federation Architect (session 5)
- **Change type:** edit (all 10 entries' statement field), add (P11)
- **Citation:** the operator, session 5 (2026-05-23): the operator ruled the existing statements too ornate. Plain English reads faster without losing precision. Bulk rewrite applied to all 10 statement fields; generalization arguments and provenance unchanged.

**P11 — `ship-working-system-on-time`** added with statement *"The goal is always to ship a working system on time"* (the operator's own phrasing). Provenance: session 5's own behavior — multiple instances of polish-past-usefulness in this session and prior (ADR-0010 scope creep, architect-learnings.md backfill, a role-doc patch bump for a one-line correction). The federation has 10 ADRs and zero real consumers — exactly the failure mode this principle prevents.

**Display order updated.** P11 inserted as second in doc (after P1) per the operator's direction that important principles should not sit at the bottom. Stable IDs (P1, P2, …) are unchanged; the registry preamble now explicitly says stable IDs don't imply display order.

---

## 2026-05-23 — Initialization

- **Author:** Federation Architect (session 4, retroactively logged in session 5 per [ADR-0010](../adr/0010-registries-canon-only-sidecar-history.md))
- **Change type:** add

Principles registry created with 10 entries, all status `Accepted`, derived from the session 4 re-classification pass over candidates from a member Architect's role doc (see session-handoff.md session 4 entry).

| Slug | Source | Notes |
|---|---|---|
| `P1 — ambiguity-paid-down-before-commitment` | A member Architect's role doc (planning guidance) | |
| `P2 — system-artifacts-evolve-auditably` | A member Architect's role doc (working conventions); federation-arch.md §"References" | Broadened in session 4 from session-2's narrower form ("role-doc evolution must be auditable over time") to cover the git-habit cluster (Conventional Commits rides P2). |
| `P3 — data-system-separation` | A member Architect's role doc (data/system boundary); [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md) | |
| `P4 — identity-boundaries-non-collapsing` | A member Architect's role doc (identity); [ADR-0004](../adr/0004-auditor-as-separate-role-class.md); [ADR-0006](../adr/0006-naming-convention-corrected.md) | |
| `P5 — session-continuity-survives-cold-restart` | A member Architect's role doc (session handoff); federation-arch.md §10 | |
| `P6 — observations-captured-at-source` | A member Architect's role doc (producer-side learnings); federation-arch.md §2 | |
| `P7 — transparency-at-conversation-layer` | A member Architect's role doc (narration); that member's own ADR | |
| `P8 — decisions-auditable` | A member Architect's role doc (planning guidance) | Merges session-2's two principles ("don't pre-bake the decision space" + "show pros/cons with recommendation") into one. |
| `P9 — destructive-ops-confirmed` | A member Architect's role doc (confirm-first operations); [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md) §"Carve-out for destructive or irreversible operations" | Named in session 4. Both prior artifacts encoded local versions of an unnamed principle. |
| `P10 — architect-owns-operational-substrate` | A member Architect's role doc (git work off the user's plate); [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md) | Named in session 4. the operator's articulation: the user's time goes on direction and occasional audits, never on routine git work. |

**Scope decisions captured in session 4 (matter for future edits):**

- Principles are universal by [ADR-0008](../adr/0008-three-bucket-taxonomy.md); the principle bucket has no scope axis. Anything narrower demotes to habit.
- The `stay-in-role` habit was initially classified member-specific, then re-promoted universal under P4 after the operator ruled that impersonation comes naturally to Claude agents, so it must be baked into a principle and a habit. The narrower framing ("don't impersonate runtime via runtime channels") is that member's situation-specific instantiation; the broader pattern (don't speak/act as any role you're not) is what P4 covers.
