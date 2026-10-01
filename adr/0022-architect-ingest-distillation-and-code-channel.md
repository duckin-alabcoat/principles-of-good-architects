# ADR-0022: How Architects ingest principles & habits — generated distillation + code channel, inherited not negotiated

**Status:** Accepted
**Date:** 2026-06-14
**Deciders:** the operator, Federation Architect

## Context

Session 30 surfaced that the canon never reaches the Architects bound by it.

- **Participating Architects do not read the registries at runtime.** `habits/master.md` says so outright (*"Not read by participating Architects at runtime"*). Their session-start ritual reads their own role doc; the principles/habits there live in a **§4 one-line-gloss table** (slug + gloss) — not the statement, reasoning, or provenance. So the full canon (and the plain-English statements polished this session) reaches no Architect.
- **The §4 gloss is a second, hand-maintained string per item, and it drifts.** We simplified P13/P14 statements in the registry this morning; the §4 glosses an Architect actually reads still carry the old wording, and P15 is in no §4 table. Hand-maintaining a parallel summary guarantees rot.
- **Procedural habits left as text silently no-op.** The session rituals, stamping, etc. are deterministic procedures the LLM is *supposed to perform from memory* each session. One member Architect's missing session-start stamp is exactly this — the habit is "adopted" (text is present) but nothing *executes* it. That is [P15](../principles/master.md#p15--code-for-mechanism-not-judgment)'s failure mode.

the operator's direction: the registry canon is good and stays as-is; Architects need a **separate version they can ingest — effective for them, cheap on tokens**; they **inherit** the principles, they don't get a say; and **anything that can be adopted deterministically should be adopted as code**, not as text the LLM is trusted to re-execute.

## Decision

**Two tiers, two channels, inherited — and the Architect-facing tier is generated from canon, never hand-authored.**

1. **Tier 1 — Canon (unchanged).** The registries (`principles/master.md`, `habits/master.md` + sidecars) remain the single source of truth: full statements, generalization arguments, provenance. For the Federation Architect and for audit. Read in full only by federation.

2. **Tier 2 — Distillation (new, generated).** A lean, Architect-facing manifest carrying the **one-line statement** of each item in the **universal set** (all Accepted principles + all Accepted universal habits). ~one line per item (~40 lines total today; trivial token cost at session start). It is **generated from the canon by a deterministic script** — never written by hand. This is the artifact an Architect ingests; it replaces the §4 gloss table.

3. **Inherit, not negotiate.** The universal set is inherited by every Architect. There is **no per-Architect say and no per-item gate** for universal principles/habits — they are universal by definition ([ADR-0008](0008-three-bucket-taxonomy.md)). The **only** gate is registry-Accept, which the operator already performs. **Situation-specific habits stay local** to their originating Architect and are not inherited.

4. **Two channels, split by mode ([P15](../principles/master.md#p15--code-for-mechanism-not-judgment)).**
   - **Text channel** — judgment items go into the distillation manifest; the LLM reads and internalizes them.
   - **Code channel** — deterministic items ship as **code**: a shared harness (`session.py` + hooks + structural guards) installed in the Architect's repo and wired into its `.claude/settings.json`. The manifest *names* these as "enforced by the harness"; it does not restate them for the LLM to re-execute.
   - **Classification rule** (per-step, not per-habit): time/count trigger → code; format/syntax → guard; destructive/permission → deny-list; semantic meaning / trade-off / what-to-write → text. Build a guard **on recurrence**, except the **irreversible-leak class** (data, secrets) which is guarded pre-emptively.

5. **Generation is deterministic (P15-native).** A federation script reads the registries and emits (a) the text manifest and (b) the code-channel list (which items are code-enforced and by what). Regenerated whenever canon changes; the Architect-facing tier therefore cannot drift from canon.

6. **Federation dogfoods first** ([ADR-0003](0003-federation-architect-is-a-participant.md)) before redistribution.

## Alternatives Considered

- **Keep the §4 gloss table (status quo).** Rejected — it is hand-maintained, already drifting, and the canon never reaches runtime. This is the problem, not a baseline.
- **Have Architects read the full registry at session start.** Rejected — burns tokens on reasoning/provenance they don't need; the registry is also federation-internal. The one-line statements are the only runtime-relevant content.
- **Hand-author a separate lean doc per Architect.** Rejected — that *is* the §4 gloss with extra steps; a parallel hand-maintained summary drifts by construction. Generation is the whole point.
- **Keep per-Architect receipt-ritual gating for principles** ([ADR-0013](0013-receipt-ritual.md)). Rejected for the universal set — principles are universal by definition, so per-Architect "approval" was always fiction; the real gate is registry-Accept. Receipt-ritual is retained for situation-specific edits and as the transport for code-channel installs.
- **Ship procedural habits as text and trust the LLM** (status quo for non-federation Architects). Rejected — this is precisely the missing-stamp bug above (P15).

## Consequences

- **Amends [ADR-0013](0013-receipt-ritual.md)'s adoption model** for the universal set: inheritance via generated manifest replaces per-item per-Architect gating. ADR-0013's receipt ritual continues to govern situation-specific edits and code-channel install/updates.
- **New federation artifacts:** the generator script + the generated manifest format + the code-channel harness package (the `session.py`/hooks/guards bundle, templatized).
- **Role-doc §4 changes shape:** from a hand-maintained gloss table to a pointer at the generated, inherited manifest ("do not hand-edit"). A downstream edit to `federation-arch.md` and the kit role-doc template.
- **Initial code-channel payload:** the P5 session-ritual cluster + `no-compound-bash` + `conventional-commits`, plus the irreversible-leak guards (P3 data-leak, P14 secret-scan) and the P2 version/CHANGELOG-consistency check. Built and dogfooded in federation, then shipped.
- **The code-delivery gap must be closed.** Today the receipt ritual and bootstrap kit carry role-doc *text* and `settings.json` permissions, but not scripts/hooks. Shipping the harness requires the kit + retrofit to carry code. (The `settings.json` deny-list already ships, so the channel is being *extended*, not invented.)
- **Open — manifest location & loading.** Candidate: a generated file at the Architect's repo root, referenced by the role doc and read at session-start; or injected via a SessionStart hook's `additionalContext` so it's always current. Decide at build time.
- **Rollout:** kit (fresh Architects) + retrofit (existing). **The member with the missing stamp goes first** — it is actively exhibiting the failure this ADR fixes.

## References

- [P15 — code-for-mechanism-not-judgment](../principles/master.md#p15--code-for-mechanism-not-judgment) and child habit [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence) — the principle this implements.
- [ADR-0008](0008-three-bucket-taxonomy.md) — principles universal by definition; situation-specific habits stay local.
- [ADR-0013](0013-receipt-ritual.md) — the adoption model this amends for the universal set.
- [ADR-0019](0019-curate-gather-and-staging-boundary.md), [ADR-0020](0020-session-rituals-are-a-code-harness.md) — prior code/judgment-split precedents (`gather.py`, `session.py`).
- [ADR-0003](0003-federation-architect-is-a-participant.md) — federation dogfoods first.
- session 30 (2026-06-14) — the review + design conversation that produced this.
