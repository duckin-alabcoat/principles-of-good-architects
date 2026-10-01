# ADR-0024: The standard role-doc section is generated and injected, not hand-authored per Architect

**Status:** Accepted
**Date:** 2026-06-15 (accepted 2026-06-16, session 33)
**Deciders:** the operator, Federation Architect

## Context

[ADR-0023](0023-standard-operating-substrate.md) declared one standard operating substrate that every Architect runs on, with a governing constraint: **additions allowed, subtractions not.** It standardized the mechanical layer — `session.py`, the session-state model, semver, the `CANON.md` canon channel, the `check-bash` guard, the receipt-ritual inbox.

It did **not** standardize one thing: the **prose protocol** in each role doc — the session rituals (§11), the artifact tables, the harness narration. That text is still hand-authored separately in every Architect's role doc. [ADR-0022](0022-architect-ingest-distillation-and-code-channel.md) had already split the role doc three ways — **code** (`session.py`, run by the harness), **reference data** (`CANON.md`, generated and injected), and left this third leg, the **standard prose**, as hand-maintained source.

That leg is where a subtraction slipped through. Session 32 diagnosed one member Architect's missing session-start stamp: the code channel was installed and working, but during its ADR-0022/0023 convergence that member **re-authored its §11 prose** and, in doing so, dropped the step that announces the stamp. The mechanical half (the hook writing the stamp) was fine; the re-authored prose protocol had lost a standard step. This is exactly the failure ADR-0023 forbids — a subtraction of a standard component — and it slipped through because the standard prose is the one part of the substrate still copied and re-authored by hand per Architect.

The producer-learning from session 32 stated the structural gap directly: **convergence verifies that the *code* installed and runs, but not that the *re-authored prose protocol* kept every standard step.** The code half is standardized by verbatim copy (you cannot drop a step from a file you copy byte-for-byte); the prose half is re-authored per Architect (you can). As long as the standard prose lives *inside* each Architect's role doc as editable text, an Architect can mangle or drop it, and the floor has a hole.

A structurally identical problem has a well-known answer one level down, in agent-prompt layers. There, a common pattern treats **prompts as build artifacts**: a deterministic assembler composes each agent's prompt from a shared protocols file plus a slim per-agent role source, instead of hand-maintained files that drift. A companion rule names the writer: **the architect is the sole author of prompts**. Agents never edit their own prompts; they recommend changes to the architect through a channel read at session start. The federation problem is the same shape pointed one level up: **the standard role-doc section is to an Architect what the shared protocols file is to one agent.**

## Decision

**The standard section of every role doc is a federation-owned, generated artifact, injected into each Architect's session at startup — not hand-authored prose inside the Architect's own role-doc file.** Each Architect authors only its *custom* section; the Federation Architect is the sole author of the *standard* section; an Architect that wants the standard changed recommends it through the existing receipt ritual.

This closes ADR-0022's third leg: **code → run; reference data → generated + injected; standard prose → now also generated + injected; custom prose → Architect-authored.**

### 1. Two sections, two writers

A role doc is split into:

- **Standard section** — the session rituals (§11), the canon pointer (§4), the substrate artifact rows (§7 harness/CANON.md/config), the harness narration. Same for every Architect. **Federation Architect is the sole author** (the analog of the sole-author-of-prompts rule: the architect of the *prompts* is the author; here the Federation Architect is the author of the *standard role-doc section*).
- **Custom section** — identity, mission, scope, system-specific operating principles, voice. Unique per Architect. **Each Architect is the sole author of its own custom section** ([P13](../principles/master.md#p13--single-writer-per-state) single-writer falls out cleanly: the role-doc *file* in each repo carries only custom, so it has exactly one writer).

### 2. Delivery is injection, not on-disk merge

The standard section lives in a federation-owned source, generated like `CANON.md` (one source of truth, regenerated — never hand-typed). It is shipped to each repo as a generated file (`STANDARD.md`, the sibling of `CANON.md`) and **read + injected by `session.py start` into `additionalContext`**, alongside the canon digest that already rides that channel ([ADR-0022](0022-architect-ingest-distillation-and-code-channel.md); `session.py` `_emit_start`). The Architect's own role-doc file carries only its custom section plus a **pointer** for the standard section — exactly as §4 already points at `CANON.md` today.

Standard and custom therefore merge **in context, per session** (the hook combines the injected standard with the Architect's read of its custom file), **not on disk.** The standard section never becomes editable text inside a file the Architect maintains.

### 3. Why injection prevents subtraction by construction

Because no Architect ever *holds* the standard section as editable text, no Architect can drop a step from it. The missing-stamp bug — re-author the prose, lose a step — is structurally impossible: there is no per-Architect copy of the standard prose to re-author. Regeneration from one source overwrites; injection by the hook removes even the reading from per-Architect discipline. This is **prevention by construction**, the standard ADR-0023 asks for ("a floor with opt-out holes is not a floor"), and it is stronger than the detection-after-the-fact that an on-disk assembler's regenerate-and-diff provides.

### 4. Additive-only composition — one extension slot, not a slot framework

A system that genuinely needs an extra session-start step expresses it as an **addition** in its custom section. The injected standard's final step is the single extension point: *"now execute any system-specific session-start steps declared in your custom section."* This satisfies ADR-0023's additions-allowed/subtractions-not rule directly — a system may *add* a step, never *remove or reorder* a standard one.

We deliberately do **not** build an N-named-slot framework for weaving custom steps among standard ones. The federation's standard/custom split is block-separable at the role-doc level (unlike agent prompts, where shared and per-agent text can be woven sentence by sentence), so one appendix slot suffices. A richer slot model waits for a second real weave case (the same restraint ADR-0008 applies to the bounded-habit taxonomy: don't pre-solve in the abstract).

### 5. Deterministic standard steps belong in code, not injected prose ([P15](../principles/master.md#p15--code-for-mechanism-not-judgment))

The step that was actually dropped — *announce the stamp* — is deterministic: format an already-written stamp and print one line. By P15 it should not be prose at all. The standard section therefore lands in three tiers, not one:

- **Deterministic standard steps** (git refresh, stamp write, counter, **announce**) → **code** (`session.py`, already most of the way there; `session.py start` should emit the announce line itself so the Architect only relays it).
- **Judgment standard steps** (orphan keep/bad decision, the orientation summary, inbox triage) → **injected standard prose** (generated, un-droppable because not per-Architect-authored).
- **Custom steps** → **Architect-authored appendix** (additive-only, per §4).

### 6. Changes to the standard flow through the receipt ritual

An Architect that believes a standard step is wrong does not edit it — it **recommends** the change to the Federation Architect through the existing receipt-ritual inbox ([ADR-0013](0013-receipt-ritual.md)). This is the recommendation flow of the sole-author-of-prompts rule, which the federation already has built; no new channel is needed. The Federation Architect, as sole author of the standard section, makes the edit at the source; regeneration propagates it to every Architect on their next session.

## Alternatives Considered

- **Build-time assembly into self-contained role-doc files** (the prompts-as-build-artifacts choice: an assembler composes standard + custom into one deployed file per Architect, drift caught by regenerate-and-diff). Rejected for the federation, though correct for prompt layers, because the substrates differ. Prompts pasted into an agent with **no runtime** have build time as their only assembly point. Federation Architects each run `session.py` under a `SessionStart` hook — they *have* a runtime injection point that such agents lack, and already use it for `CANON.md`. Injection is the consistent, minimal extension of the channel we already run (one more payload, vs. standing up an assembler plus a "deployed file is read-only output" workflow), it keeps the custom file single-writer, and it prevents subtraction by construction rather than detecting it after the next regeneration. *(The build-assemble model stays the right reference for any future build artifact that has no runtime to inject at.)*
- **Status quo: keep the standard prose hand-authored, catch drops in review.** Rejected — it is the status quo that produced the missing-stamp bug. Relying on a human reading every converged §11 against the kit template is exactly the discipline-not-structure failure mode P15 exists to remove.
- **Move the whole standard section into code (`session.py` prints all of §11).** Rejected — the judgment steps (orphan bad/keep, the summary, inbox triage) are not deterministic; coding them up is the inverse P15 error (judgment does not belong in code). The three-tier split in §5 routes each step to its correct substrate.
- **An N-named-slot composition framework** for interleaving custom steps among standard ones. Rejected as premature (§4) — block-separable today; one appendix slot meets every current need.

## Consequences

- **The standard role-doc section becomes a generated artifact.** The federation needs a single standard-section source and a generator for it — most naturally an extension of [`curate/distill.py`](../curate/distill.py) (which already generates `CANON.md` and `bootstrap-kit/CANON.md` from canon and `--check`s them), or a sibling generator, emitting `STANDARD.md` to the federation root and the bootstrap kit. **This is the build, and it is a later session** — this ADR locks the principle (consistent with the Accepted-before-build precedent).
- **`session.py start` gains a payload and an emit.** It reads `STANDARD.md` and appends it to `additionalContext` (the `CANON.md` path, duplicated for one more file), and it emits the deterministic **announce** line itself (§5). Config-driven so the identical script runs everywhere ([ADR-0022](0022-architect-ingest-distillation-and-code-channel.md)).
- **Every role doc loses its standard prose and gains pointers.** §11 (and the other standard blocks) collapse to a pointer at `STANDARD.md`, mirroring the §4→`CANON.md` move ADR-0022 already made. The role-doc file is no longer self-contained — opening it shows custom + pointers, not the standard rituals. This is the same tradeoff already taken for the universal set; an Auditor reads `STANDARD.md` directly.
- **Federation dogfoods first**, then each system converges via the receipt ritual — folding into the ADR-0023 convergence retrofit rather than standing as separate work. The convergence-verification gap from session 32 closes here: once the standard prose is injected, there is no per-Architect §11 to diff against the template, because there is no per-Architect §11.
- **The bootstrap kit changes shape.** The role-doc template's standard sections become the generated `STANDARD.md`; the template keeps only the custom scaffold plus the standard-section pointer. Fresh Architects are born with the standard injected, never hand-authoring it.
- **A new single-point-of-failure: a broken `STANDARD.md` or generator corrupts every Architect's standard section at once.** Mitigated as `CANON.md` already is — generated from one reviewed source, `--check`ed for staleness, git-tracked with history as rollback, and best-effort at injection (a read failure never blocks the start ritual; `session.py` `_read_canon` is the precedent).
- **Sharpens [ADR-0023](0023-standard-operating-substrate.md).** The standard prose protocol is now named as part of the non-subtractable substrate and given an enforcement mechanism (generation + injection) rather than relying on convergence discipline. It also retires, for the standard section, the assumption that each Architect hand-maintains its own §11.
- **Possible new principle.** "A standard shared across roles is delivered by the substrate, not re-authored per role" may generalize beyond this case (it already rhymes with P15 and the ADR-0022 canon channel). Flagged as a curate/promotion candidate, not promoted here.

## References

- [ADR-0022](0022-architect-ingest-distillation-and-code-channel.md) — the code channel this ADR extends; established generation + injection for `CANON.md` and the three-way role-doc split whose third leg this closes.
- [ADR-0023](0023-standard-operating-substrate.md) — the standard operating substrate and the additions-allowed/subtractions-not floor rule this operationalizes for the prose leg.
- [ADR-0013](0013-receipt-ritual.md) — the recommendation channel an Architect uses to propose standard-section changes.
- [ADR-0020](0020-session-rituals-are-a-code-harness.md) — the `session.py` harness that does the injection and the deterministic steps.
- [ADR-0008](0008-three-bucket-taxonomy.md) — additive-divergence discipline the one-extension-slot decision follows.
- The agent-layer prior art this lifts one level up (prompts are build artifacts; the architect is the sole author of prompts; facts are data, prompts are roles) — build-assemble is the right choice for a runtime-less substrate, injection is ours.
- Federation sessions 32 (the missing-stamp bug + prompt-writer scoping) and 33 (2026-06-15, this decision).
