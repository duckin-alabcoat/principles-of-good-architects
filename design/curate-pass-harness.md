# Curate Pass — Harness Design Proposal

> **STATUS: SHELVED (session 21, 2026-06-04) — superseded by [ADR-0019](../adr/0019-curate-gather-and-staging-boundary.md).**
> the operator judged this over-engineered for the current single-producer corpus. The curate pass is
> instead a deterministic code gather (`curate/gather.py`) plus human review; see ADR-0019. This
> document is retained as the reference design for the deferred multi-agent harness. Revisit only
> when a second domain producer file exists *and* the corpus has outgrown single-context review.
> The one piece carried forward into ADR-0019 is the §4.2 gate hardening (quote the operator's approval
> verbatim into the sidecar). Everything below is reference, not canon.
>
> _Original header:_ DESIGN PROPOSAL — authored by the Federation Architect, 2026-06-04. No file
> outside this proposal had been written at authoring time.

---

## 1. Status & executive summary

The **curate pass** is the federation step that reads the Architects' producer learnings and
role-doc mirrors, finds claims that recur or generalize, and decides — per ADR-0008 — whether
each is a **principle**, a **universal habit**, a **situation-specific habit**, or a **preference**,
then stages the survivors for promotion into the shared registries (`principles/master.md`,
`habits/master.md`, `users/operator/profile.md`).

A **harness** — a multi-agent pipeline rather than a single FA session — is proposed because the
hard part of curation is *not* reading; it is **resisting the universality-by-frequency trap**. A
single agent that both proposes and judges a generalization will rationalize its own first guess.
The harness exists to force an **adversarial argument** about every universality claim, to keep an
**unbroken provenance chain** from a verbatim source span to a registry entry, and to make the
human Accept gate **structural** (a directory boundary) rather than a matter of the FA's discipline.

**Headline recommendation: do NOT build the full pipeline. Build a walking skeleton now, and grow
the adversarial machinery only when the corpus can feed it.** The adversarial stress-test was
decisive on this: today's corpus has **exactly one diverse producer file** (one member's), plus the FA's
own root file, plus three thin role-doc mirrors. There is **zero cross-system corroboration possible
today** — no second domain Architect has produced anything. A research-grade pipeline of nine-plus
frozen agent contracts pointed at this corpus costs more to build and run than the FA reading it
in-session, for as long as the corpus stays this sparse. So: ship the **staging boundary** (the
gate-critical, corpus-independent part) and the **EXTRACT→artifact-boundary→cluster→stage** spine
now; **defer** the proponent/challenger/referee panel, the judge panel, the gold-set, and most
contracts behind a `distinct_system_count >= 2` trigger that the corpus does not yet satisfy.

The honest expected yield of an early run is **a short "surface to the operator" list with near-zero
auto-keeps**. That is the harness working correctly, not underperforming. This proposal states that
up front so a thin `REVIEW.md` is read as rigor, not failure.

---

## 2. The recommended architecture

### 2.1 Phases

The pipeline is **autonomous up to a hard stop**; it touches no canon. Two boundaries are
load-bearing: the **artifact boundary** (P1→P2) and the **human Accept gate** (P7→FA write).

```
pipeline curate_pass:                          [autonomous — touches NO canon]
  P0  DISCOVER + SCOPE   agent             → manifest (files, classes, base-version SHAs, delta cursor)
  P1  EXTRACT            parallel(agent)   → verbatim observation records, per file        ── PHASE A
  ──────────────  HARD ARTIFACT BOUNDARY: curate-runs/<run>/extraction.json  ──────────────
  P2  NORMALIZE+ROUTE    agent             → tag-normalized records, pooled                ── PHASE B
  P3  CLUSTER            agent             → cross-system clusters, full source-set retained
  P4  CLASSIFY           parallel(agent)   → per-cluster bucket (decomposed ADR-0008 rubric)
  P5  VERIFY             parallel(pipeline)→ per-cluster adversarial kill/keep/narrow (depth = reach)
  P6  PROPOSE            pipeline(agent×N) → generator → judge panel → assembler → DRAFT
  P7  STAGE             agent → writes REVIEW.md + drafts to curate-runs/ ONLY
  ─────────────────────  HARD STOP — the operator's ACCEPT GATE  ─────────────────────
  [FA, human-gated]      FA writes Proposed→registry on accept; flips status; writes sidecar line
```

**The hard artifact boundary** is the central guarantee: Phase B reasons only over the frozen
`extraction.json`, never re-reading source. If Phase B wants evidence Phase A did not capture, that
is an extraction-contract bug — surfaced and re-extracted, never papered over by re-reading.

> **Adopted from stress-test (H1): content-addressed `obs_id`.** The original design assigned
> `obs_id` as a run-local sequence at the merge step. That breaks the boundary's own escape hatch:
> a re-extraction mints new ids, and every cluster/verdict from the prior run points at dead
> identifiers, so a Proposed entry's provenance can no longer be re-resolved. **Fix: `obs_id` is a
> stable content hash of `(source_file, locator, evidence_span, base_version_sha)`.** Unchanged
> spans regenerate identical ids across runs; provenance survives re-extraction.

### 2.2 Agent decomposition

Every agent is a **worker inside the Federation Architect's authority** — never another Architect,
never a canon writer. Workers return artifacts to `curate-runs/`. Only the human-gated FA step
touches canon (§4).

| Agent role | Reads | Returns | Key schema fields |
|---|---|---|---|
| **P0 discover** (1) | `inputs/` listing; root `architect-learnings.md`; registries (+ ingested set, base-version SHAs); prev run's `ingested.json`; `portfolio.md` (only to list *absent* systems) | `manifest.json` | `run_id`, `base_versions{file→sha+last_history_date}`, `inputs[]{file,system_id,class,since_cursor}`, `absent_registered_systems[]` |
| **P1 producer reader** (parallel, 1/file) | one `*-learnings.md` OR root file | observation records | `obs_id`(content-hash), `source_class:producer`, `source_system`, `confidence_floor:high`, `evidence_span`(verbatim), `locator`, `declared_scope_tag`(+`_raw`, may be `null`), `producer_flag`, `stated_observation`, `stated_conclusion`, `context_conditions` |
| **P1 mirror reader** (parallel, 1/file) | one `*-arch.md` + kit baseline | proto-learning records | same schema; `source_class:role-doc-mirror`, `confidence_floor:low`, `declared_scope_tag:null`, `delta_kind`, `role_doc_section`+`version` |
| **merge** (deterministic) | reader outputs | `extraction.json` + base-version manifest | content-hash `obs_id`s; `delta:new\|seen` per `(system,locator)` |
| **P2 normalize+route** (1) | `extraction.json` | `routed.json` | `scope_tag_canonical`, `pool`, `tag_inferred`, `confidence` |
| **P3 cluster** (1) | `routed.json` | `clusters.json` | `cluster_id`, `members[]`, `source_systems[]`, `distinct_system_count`, `intra_system_recurrence`, `source_class_mix`, `cross_source_corroboration`, `near_pairs[]`, `pool` |
| **P4 classifier** (parallel, 1/cluster) | one cluster + spans + ADR-0008 rubric + current registries | `classification` | `bucket`, `parent_principle_slug\|null`, `deciding_span`, `proposed_universality_claim`, `adjudicated_reach`, `default_narrow_applied`, `is_edit_of\|null`, `paired_with\|null`, `classifier_confidence` |
| **P5 proponent** (per universality-claiming cluster) | cluster + members + claim | affirmative case | `mechanism_argument` (frequency forbidden as support) |
| **P5 challenger** (per cluster) | cluster + proponent case + absent-systems + role-class roster — but **NOT** the count | counterexample attempt | `counterexample{context,strength,in_corpus:bool}` |
| **P5 auditor-challenger** (per *principle*/cross-role-class claim) | same, framed for cold/single-pass Auditor | Auditor-context counterexample | same |
| **P5 referee** (per cluster) | proponent + challenger(s) | verdict | `verdict`, `surviving_bucket`, `applicability_constraint`, `mechanism_argument_final`, `counterexample_considered`, `frequency_used_as:prior-only`, `single_source_capped`, `minority_veto_fired` |
| **P6 generator** (1/survivor) | cluster + verdict + members + schema | draft entry | per-bucket canon schema + `provenance[]{obs_id,class,strength}` + `redistribution{shallow A/B/C hint}` |
| **P6 judge panel** (×N) | draft + cited members | keep/revise/reject | support / universality / citation-accuracy / P4-label-hygiene |
| **P6 assembler** (1) | all drafts + deferred/blocked items | `REVIEW.md` + `proposed/*.md` | the single human surface |

---

## 3. The adversarial verify stage (P5) — the heart of it

This stage enforces **universal-by-argument, not by frequency**. It runs only on clusters
classified `principle` or `universal-habit`; `situation-specific` and `preference` make no
universality claim and skip to P6.

### 3.1 Information-asymmetric debate

**Proponent** argues universality *from mechanism*, frequency forbidden as support: *"You may note
how many systems surfaced this, but your argument must stand if only one did. A count is not an
argument. Why must any Architect — regardless of domain — encounter this failure mode?"*

**Challenger** is the counterexample hunter — the load-bearing defense. It reads the proponent case,
the **absent-systems list**, and the
**role-class roster**, but **not the frequency count** (the asymmetry: it must not be swayed by
"appeared in 3 systems"). It must (1) name a concrete context where the claim fails/inverts/is
merely local; (2) argue it is `*-specific` masquerading as general — an artifact of one member's domain;
(3) test whether support is *several systems copying one source* vs independent discovery. The
empty-producer reality is the challenger's hunting ground: the silent majority of the portfolio is
exactly where universality goes to die.

**Auditor-context challenger** asks: *"Does this hold for a cold-context, single-engagement reviewer
with no session continuity, no runtime, no producer file?"* Many `architect-general` habits (session
rituals, handoff) **fail** the Auditor — exactly the boundary a broad claim must survive.

**Referee** issues the kill/keep/narrow verdict:

```
IF a challenger produced a WELL-EVIDENCED, IN-CORPUS counterexample where the claim fails/inverts:
    → MINORITY VETO fires. DOWNGRADE (one substantiated dissent beats any numeric majority).
ELSE IF an ABSENT-SYSTEM (label-only, no data) counterexample is found:
    → NARROW or DEFER to the operator — NEVER kill. (Killing on a hypothetical over-trusts the challenger.)
ELSE IF the claim does not APPLY everywhere but does not INVERT (terse-internal vs verbose-user-facing):
    → NARROW. Keep as universal habit WITH an explicit applicability_constraint. (Tag, don't collapse.)
ELSE IF the proponent's case rests on frequency with no mechanism argument:
    → DOWNGRADE (universality unsupported; absent argument = classification error, not an entry).
ELSE IF a parent principle is required but none is nameable:
    → HOLD "needs-parent-principle": promote habit + new principle together, or downgrade.
ELSE IF proponent and challenger are both well-evidenced and referee confidence < threshold:
    → DEFER to the operator as an explicit open question. NEVER auto-resolve a genuine conflict.
ELSE (mechanism argument survived, no counterexample):
    → KEEP. Universality confirmed by SURVIVAL OF CHALLENGE; corroboration is a PRIOR, never the reason.

OVERRIDE 1 — TAG-INFERRED PENALTY: a cluster whose universality rests ONLY on tag-inferred mirror
    deltas (no producer entry, no cited exchange) CANNOT be KEPT this pass — capped at
    "surface to the operator, low-confidence." Never federate without provenance.

OVERRIDE 2 — SINGLE-SOURCE SATURATION GATE: a surviving cluster whose ENTIRE provenance set comes
    from one system (today: the one domain producer, or it plus the FA — both authored under the same Architect-centric capture
    defaults) CANNOT be KEPT as principle/universal-habit this pass — capped at "surface to the operator,
    single-source proto-candidate."  [adopted from stress-test FM-3]
```

`mechanism_argument_final` lands **verbatim** in the registry's **Generalization argument**
(principle) / **Failure-mode breadth** (habit) section — the surviving adversarial argument *is* the
on-page provenance-of-universality ADR-0008 demands. `counterexample_considered` is retained so the operator
sees the adversary's best shot.

### 3.2 Universality is enforced by REACH, not by the producer's tag

This is the single most important correction the stress-test forced. The original design routed
verify depth by the producer's scope tag (`domain-general`→deepest lane, etc.). **The corpus
destroys that design:** all 22 entries in the one domain producer file are tagged `architect-general`; the FA's 7 entries carry
**no scope tag at all**; `domain-general` appears only in that file's tag-vocabulary preamble (zero
entries); `auditor-general` appears nowhere. So tag-driven lanes mean the deepest scrutiny **never
fires**, and a **capture-time default silently sets scrutiny depth to a flat constant**. That is the
universality-by-frequency trap one level up: the tag is a frequency artifact of what the author was
told to reach for, not an adjudicated breadth.

**Adopted fix (FM-1/FM-2): decouple verify depth from the producer tag; drive it from the
federation's own adjudicated reach.** Phase 4 already proposes a universality claim and names a
parent principle — use *that*. Concretely:

- The Auditor-context challenger runs on **every principle candidate and every claim whose proposed
  reach binds a role class the corpus cannot speak for** (Auditors, non-Architect AI collaborators)
  — **regardless of tag**. This makes the Auditor Checker actually fire.
- The scope tag is retained as a *weak prior and a clustering pool key only* (R1 firewall): it never
  sets the bucket and now never sets the depth either. A `null` tag (the FA's own file) no longer
  falls through routing.

> **Considered and rejected — the tag→lane routing graft.** The original unified design kept "tag
> sets lane depth" as a benefit ("concentrate scrutiny where the claim is boldest"). Rejected: on
> the real corpus the boldness signal is a flat constant, so the graft is dead code that also leaves
> the FA's tagless file unrouted. Reach-driven depth recovers the same intent from a signal the
> corpus actually carries.

### 3.3 Authority asymmetry on counterexamples

An **in-corpus** counterexample may **kill**; an **absent-system** counterexample (reasoned from a
domain label only, no data) may **narrow or defer** but **not kill**. Killing on a label-only
hypothetical over-trusts the challenger's imagination; narrowing/deferring is the conservative,
reversible move and routes the real decision to the operator. (Verified strong by all three stress-tests.)

---

## 4. Provenance, the human Accept gate, and P13 single-writer

### 4.1 Provenance threading — unbroken, never re-derived

```
reader.evidence_span + locator + source_file + class + confidence_floor   (P1, verbatim)
   │ carried by content-addressed obs_id (stable across re-extraction)
cluster.members[] + source_systems[]   (P3 — FULL set retained, never reduced to a representative)
   │
verify.mechanism_argument_final + counterexample_considered   (P5 — the universality argument BECOMES provenance)
   │
generator entry: Provenance section cites each obs_id → resolves to (file, locator, version, class, strength)
   │
judge: every pointer resolves to a real span at the stamped locator
   │
emit: entry carries full chain + base_versions manifest + shallow A/B/C hint
```

**Emit invariant (hard):** no entry ships if any claim lacks a resolving `obs_id` pointer — the
harness encoding of "never federate without provenance." A mirror-only candidate carries
`class:role-doc-mirror, strength:role-doc-delta, confidence:low` explicitly and earns an automatic
**`low-provenance` banner** in `REVIEW.md`.

**P4 label hygiene:** provenance labels are Architect-level, never agent-level — "Example Service Architect,"
never "Example Service Architect (its agent's name)" (the v1.2.2 scrub precedent). A judge criterion checks this.

> **Adopted from stress-test (C2): correct the filename-keying rule, grounded in ADR-0006.**
> The join key for `cross_source_corroboration` — the strongest promote signal the harness has — is
> **system-id**, derived by stripping the *known* suffix per family, never by a single naive strip:
> - producer file = `<system-id>-learnings.md` (e.g. `example-service-learnings.md`)
> - mirror file = `<system-id>-arch.md`, which **equals** the Architect ID `<architect-id>.md`
>   (e.g. `example-service-arch.md`)
>
> Deriving the source Architect by a naive `-arch`/`-learnings` strip keys a producer and its mirror
> to *different* identifiers, and the corroboration join silently fails on exactly the signal we
> most want. The discovery rule keys **purely on filename suffix**, and `inputs/` may contain
> **both** producer copies and mirrors — see §6 item 8 and the rejected note below.

> **Adopted from stress-test (H2): non-transitive corroboration across provenance-asymmetric
> near_pairs.** When a `near_pair` straddles the provenance-strength boundary (one side has producer
> provenance, the other is tag-inferred-only), `REVIEW.md` labels it
> `provenance-asymmetric near_pair — corroboration NOT transitive`, and the tag-inferred side keeps
> its `low-provenance` banner *even when shown next to its producer neighbor*. This closes the seam
> where a human, seeing two adjacent clusters, "accepts both" and lets the weak one inherit
> credibility from a claim it was deliberately not merged with. Stated as a **gate rule**, not just
> a clustering rule.

### 4.2 The Accept gate and P13 single-writer — and the one big correction

The original design asserted that the harness writes drafts only to `curate-runs/`, hard-coded
`Status: Proposed`, and that **the registries never contain a Proposed entry** — the FA copies an
approved draft *into* the registry, so canon only ever sees Accepted entries. It marketed this as
P13 fidelity.

> **Considered and corrected (stress-test C1) — that invariant CONTRADICTS canon.** ADR-0009
> §"Promotion / demotion workflow" has the FA *"draft the case in the appropriate registry file …
> with status `proposed`,"* and approval is *"recorded by status flip from `proposed` to
> `accepted`."* `federation-arch.md` §9 grants explicit standing authority to *"mark an entry
> `Proposed` in the registry autonomously."* PROCESS.md (lines ~234–245) says promote to
> `Proposed` **in the registries**, then flip to `Accepted` **in the registries**. So
> `Proposed`-in-registry is the *defined writer behavior*, not a P13 violation. The invented
> "canon holds only Accepted" invariant also (a) **strands the sidecar `status-flip` change-type**
> (ADR-0010) — if Proposed never enters the registry, there is no flip event to log, and that
> change-type goes dead; and (b) **moves in-flight canon provenance into gitignored `curate-runs/`
> scratch** that does not yet even have an ADR defining it as data — a net provenance *regression*.

**Corrected gate model (this is the recommended design):**

1. **The harness writes only to `curate-runs/<run-id>/`.** Its emit schema has **no path to
   `Accepted`** and **no preference fast-path** (see below). This directory split is the structural
   gate — enforced by location, not policy. *(All three stress-tests confirmed this primitive is
   sound.)*

2. **Two states, both FA-written, P13-clean.** The harness is "a tool the FA runs"; a harness-written
   entry is an FA write. On a normal pass the FA promotes a staged draft to a **`Status: Proposed`
   entry in the registry** (ADR-0009 artifact 1; federation-arch §9 authorizes this autonomously),
   carrying its full provenance into canon where it is cold-readable and git-tracked.

3. **the operator's Accept gate = the in-place `Proposed → Accepted` flip**, done by the FA *only on the operator's
   explicit approval*, accompanied by the matching `*-history.md` sidecar line (date, change-type
   `status-flip`, slug, prior value if edit, **citation**, author `federation-arch`). An entry in a
   base file without a sidecar line is a bug (ADR-0009/0010).

> **Adopted from stress-test (A2): convert the last-mile gate from discipline to a checkable
> artifact.** The weak link is that the FA-writes-canon step is an LLM action gated only by the FA's
> own adherence to "wait for the operator." **Fix: the FA must quote the operator's approval verbatim into the
> sidecar `status-flip` line as the authorization record. No quoted human approval → no flip.** This
> makes the gate auditable in git rather than trusting in-session discipline.

> **Adopted from stress-test (A1): the harness NEVER exercises the preference self-approval path.**
> ADR-0009 lets a preference be self-approved by the operator's own quote. But P4 classification is an agent
> judgment, and a cluster *mislabeled* `preference` with a plausible quote would auto-ship a
> generalization the operator never approved (a quote "be terse with this one system" does not authorize a generalized
> "be terse"). **Fix: every preference is staged as `Proposed` like everything else; the FA decides
> at the gate whether the quote self-approves *that exact generalization*.** Extend the emit-schema
> bar: no `Accepted`, and no preference fast path.

4. **`Accepted` opens only *eligibility* for Redistribute** — a separate, later, per-role-doc-edit
   pass (ADR-0013 receipt ritual, ADR-0014). Curate stops at the flip.

5. **Recursive self-promotion guard.** Any survivor that would amend *federation-arch's own* role
   doc routes to `proposed-edits/federation-arch/pending/<edit-id>.md` for a fresh cold session
   (federation-arch §11), never an in-session write — unless the operator says "adopt this now."

> **Adopted from stress-test (A4): widen the self-adoption flag.** The recursive principle
> (ADR-0003) binds the FA to *every* universal habit and principle, not only literal
> `federation-arch.md` edits. **Fix: flag `self_adoption:true` for any surviving `principle` or
> `universal-habit`; let the FA downgrade the flag, not the harness set it too narrowly.** And the
> FA post-accept write step is **explicitly excluded** for `federation-arch.md` §4.1/§4.2 — those
> always go through `proposed-edits/federation-arch/pending/`, never an in-session flip.

> **Adopted from stress-test (A3): a hard, ADR-level invariant against unattended canon writes.**
> A scheduled/cron-triggered pass runs with no FA session and no the operator present; it is a gate-
> architecture decision, not a cadence preference. **Fix, stated as an invariant in the new
> `curate-runs/` ADR: a pass may *stage* unattended, but the `Proposed → Accepted` flip and any
> canon write are forbidden outside a live, the operator-present session — forever, regardless of what
> scheduling substrate exists.**

---

## 5. Degradation under the empty-producer reality

The harness is a **mixed-confidence aggregator**, not a producer-file parser. Today's
one-real-producer corpus is its **normal operating point**, not a degenerate case.

- **Corpus, verified on disk:** root `architect-learnings.md` (FA's own producer surface, ~151
  lines, **no scope tags** — entries are `## DATE — title`); one member's producer file
  under `inputs/` (the one rich domain-adjacent producer, ~22 entries, all tagged
  `architect-general`); three role-doc mirrors under `inputs/`.
  **Producer entry count is ~29 (22 member + 7 FA), not the "~18" the unified design quoted** — corrected
  per stress-test FM-4. Verify the exact count before quoting it to the operator again.
- **P0/P1:** fan-out is over files-on-disk, keyed by suffix; absent systems spawn no reader (not an
  error). Backfill is structurally prohibited — no agent writes to `inputs/` or synthesizes a
  producer entry; readers only quote; an empty producer or no-delta mirror returns `[]`.
- **Two of the three mirror'd systems have *only* a mirror, no producer file.** So
  any signal from those two is *definitionally* `role-doc-delta` provenance, capped at "surface,
  low-confidence." Combined with the single-source gate (§3.1 OVERRIDE 2), **the low-provenance
  banner is the common case, not the edge case.** REVIEW.md states this in its header.
- **P5:** mirror-only / tag-inferred / single-source clusters are capped at "surface to the operator."
- **Homogeneous-input danger (FM-3):** the risky case is not the empty run — it is 22 real-but-
  uniform `architect-general` entries from one member with a defanged adversary (the silent systems have no
  producer files, so the challenger reasons from labels only and, per §3.3, cannot kill). The
  **single-source saturation gate** is the structural answer: single-system claims are capped at
  surfacing, so the pipeline cannot manufacture universal habits to look productive.
- **Empty run is success:** no new producer entries and no mirror changes since the cursor →
  `REVIEW.md` says "nothing new to curate" and exits. Over-producing to look busy violates P11/P12.
- **Residual reality to state to the operator, every run:** the only two producer voices (that member and the FA) are *both*
  Architects sharing authorship lineage and capture conventions —
  **not independent domain discovery**. Until a second *domain* Architect has a
  real producer file, the rigorous, honest output is: **surface candidates, promote almost nothing.**

---

## 6. Substrate the federation must build (ordered by dependency)

Build order is dependency-ordered. Items 1–4 are the **walking skeleton** (build now); items 5–9
are the **deferred adversarial machinery** (build when `distinct_system_count >= 2` first occurs,
which the corpus does not yet satisfy); items 10–13 are **fixes to existing files** to do before the
FA ever writes them.

**Walking skeleton (build now):**

1. **New ADR defining `curate-runs/` as gitignored data** + a `.gitignore` line. The keystone:
   without a staging area distinct from canon, the propose/accept boundary cannot be enforced
   structurally. The ADR must also carry the §4.2 invariants (no `Accepted` in the emit schema; no
   preference fast path; **no unattended canon write, ever**).
2. **`extraction.json` schema** — the Phase-A/B boundary contract, frozen and versioned — *with
   content-addressed `obs_id` = hash of `(source_file, locator, evidence_span, base_version_sha)`*
   (H1). The whole two-phase guarantee rests on this.
3. **`ingested.json` cursor** — per-producer newest-ingested date; per-mirror **content SHA**; base-
   registry SHAs. Used at **P0 as a pre-flight skip**: an unchanged mirror SHA means its reader is
   never spawned (B2) — the common "nothing changed" pass becomes nearly free.
4. **FA post-accept runbook + `REVIEW.md` template** — the human-gated write procedure (promote to
   Proposed-in-registry; on the operator's quoted approval, flip to Accepted + sidecar `status-flip` line;
   advance cursor). The template header states the expected-yield reality from §5.

**Deferred adversarial machinery (build on first `distinct_system_count >= 2`):**

5. **`curate-config.yaml`** — the scope-tag→pool map (routing only, NOT depth — §3.2), the strict
   merge threshold, the legacy tag-normalization map, and the mirror-delta baseline pointer. Config,
   not prompt-baked, so the gold-set check can detect spec drift.
6. **Role-class roster data file** (Architect vs Auditor, with the Auditor's cold/single-pass
   properties) — the challenger reads it; exists only as prose in MEMORY/ADR-0004 today.
7. **Agent-contract prompt files** (system, git-tracked) under `curate/contracts/`. The skeleton
   needs only ~2 (`producer-reader`, `mirror-reader`, plus a reasoned-clustering prompt); the full
   roster (`classifier`, `verify-{proponent,challenger,auditor-challenger,referee}`, `generator`,
   `judge-*`, `assembler`) is part of this deferred item.
8. **`curate-runs/gold-set.json`** — ~8 hand-labeled clusters for the rubric-reproducibility alarm,
   seedable from already-validated registry entries. **Run only when `curate-config.yaml` or the
   model version changes** — not every pass (B3).
9. **Auditor-tier registry write target** — `auditor-general` is canonical (ADR-0004) but no slot
   exists. The Auditor *challenger* is part of the skeleton's verify intent (§3.2) and hardens
   principle candidates immediately; the Auditor *registry section* is a separate canon decision —
   surviving Auditor-tier candidates are held at "surface to the operator" until OQ-A is resolved.

**Fixes to existing files (do before the harness writes them):**

10. **PROCESS.md placeholder fix** — line ~171 uses `inputs/<architect-id>-arch.md`, which expands
    under ADR-0006 to a doubled `example-service-arch-arch.md`. Change the placeholder to `<system-id>-arch.md`
    to match the on-disk convention. *(Corrected diagnosis per stress-tests C2/C3: the literal text
    is not doubled; the doubling is a placeholder-expansion bug.)* Also fix `federation-arch.md`
    lines ~151–152, which carry the same placeholder bug — the unified design named only PROCESS.md.
    Publish the canonical scope-tag set in PROCESS.md as the single source of truth.
11. **Stale-header scrub** — `principles/master.md:9` and `habits/master.md:13` still say *"Gitignored
    per ADR-0007 — synthesized from input data,"* superseded by ADR-0018 (registries are git-tracked
    system). Worse, "synthesized" implies machine generation — the opposite of the P13 single-writer,
    human-gated discipline this harness depends on. Fix before the harness touches these files.
12. **R2 mental-model correction** — discovery keys **purely on filename suffix**: `*-learnings.md`
    → producer (regardless of directory), `*-arch.md` → mirror; `inputs/` may contain **both**
    classes (a member's `*-learnings.md` copy is a *producer* file living in `inputs/`).
    *(Per stress-test C2: the "inputs = mirrors only" prose was wrong and would mis-class the richest
    producer file as a low-confidence mirror.)*
13. **System-id join-key spec** for `cross_source_corroboration` — derive system-id by stripping the
    known per-family suffix, never a naive strip (§4.1, C2).

---

## 7. Open questions for the operator

- **OQ-A — Auditor registry shape:** `## Auditor-tier` section per registry vs. a separate
  `master-auditor.md` (sidecar-count and cross-link complexity differ). *I recommend a section.*
- **OQ-B — Cadence trigger:** the operator-triggered + an opportunistic session-start suggestion vs. a
  scheduled run-to-gate. *I recommend the operator-triggered + suggestion at bootstrap; a sparse cron mostly
  produces empty runs. Note: per §4.2 (A3), even if we ever schedule, the canon flip stays the operator-
  present-only forever.* Revisit once ≥3 producer files exist.
- **OQ-C — Mirror-delta provenance ceiling:** can a candidate with *only* role-doc-delta provenance
  ever reach `Accepted`? *I recommend: Accept allowed, but only on explicit the operator override of the
  `low-provenance` banner.*
- **OQ-D — Embedding crossover:** reasoned clustering holds at ~29 entries; some dozens-of-files
  scale forces embed→ANN (re-introducing cross-shard dedup and provenance-loss risk). Crossover
  threshold unknown; flagged, not guessed. The cluster schema is kept embedding-swap-ready.
- **OQ-E — Judge-panel model diversity:** PoLL de-biasing depends on *different model families*; a
  single-model harness recovers criteria-decomposition but not family-diversity de-biasing. Because
  the `mechanism_argument_final` lands verbatim in canon, a single-family blind spot can produce a
  self-consistent-but-wrong universality argument that passes its own judges. *I recommend marking
  such arguments with a provenance-confidence qualifier — not presenting them as fully adversarially-
  survived — and leaning on the operator as the backstop.* (Per stress-test M2.)
- **OQ-F — Shallow A/B/C hint scope:** curate emits a shallow A/B/C hint only for Architects whose
  mirror it read this pass; whether redistribute trusts, deepens, or discards it is redistribute's
  call. *Confirm the hint is wanted at all, or whether curate should emit zero per-Architect data
  and leave all A/B/C to retrofit.*
- **OQ-G (new, from the headline) — Skeleton vs full build:** do you accept building the walking
  skeleton (§6 items 1–4) now and deferring the adversarial panel behind a real cross-system signal?
  *I recommend yes.* The alternative — full pipeline now — costs more than FA in-session curation for
  as long as the corpus stays single-producer.

---

## 8. How this design was produced

This proposal was itself generated by a **multi-agent design harness** — a small-scale instance of
the very pattern it proposes. The process: **N grounding agents** read the actual corpus, ADRs, and
role docs and returned briefs; **M design candidates** were drafted independently and scored by
**judge panels**; the top-ranked candidate became the spine and the others were grafted at named
seams during **synthesis** (the unified design). The synthesis was then put through an **adversarial
stress-test** — three reviewers, each on a distinct lens (provenance & single-writer; universality
rigor & empty-producer reality; human gate, safety & cost realism) — instructed to read ground truth
and try to break it.

That stress-test materially changed the design. It caught a **canon-contradicting invariant** (the
"Proposed never lives in the registry" claim — C1), a **filename-keying error** that would have
silently dropped the strongest promote signal (C2), a **dead-code rigor mechanism** (tag-driven
lanes with no fuel in the corpus — FM-1/FM-2), an **un-fueled cost profile** (FM-3/B1), and the
**buildability mismatch** that produced the headline skeleton recommendation (C1/buildability). The
fixes that held are adopted inline above; the one graft that did not survive contact with the corpus
(tag→lane routing) is marked "considered and rejected" in §3.2. The harness improved its own
proposal — which is the recursive-participant principle (ADR-0003) doing exactly what it should.
