# Principles history

Append-only change log for [`master.md`](master.md). Newest-first. Per [ADR-0010](../adr/0010-registries-canon-only-sidecar-history.md).

Each entry records: date, change type (add / edit / status-flip / remove / rename), affected slug(s), prior value for edits or removes, citation that justified the change, author. Cold-readable — someone reading this file alone can reconstruct what happened.

---

## 2026-05-27 — P13 added — `single-writer-per-state`

- **Author:** Federation Architect (session 12)
- **Change type:** add (P13)
- **Slug:** `single-writer-per-state`
- **Status on landing:** Accepted
- **Citation:** Home Hub Architect (Hearth) architect.md §"SYSTEM DESIGN PRINCIPLES" line *"Single Writer Per State."* Federation-side implicit practice since session 4 (Federation Architect writes registries; per-Architect role docs written by their own Architects; receipt ritual is the controlled cross-writer mechanism). Promoted to explicit principle in session 12 per the user's adopt-as-exists direction following the home-hub-arch pattern-recognition pass.

Statement: *"Every piece of authoritative state has a unique authoritative writer. Cross-writer influence flows through controlled mechanisms (proposals, receipt rituals, flag queues) — never through direct concurrent writes to shared state."*

P13 composes with P3 (data-system separation — what goes where) and P4 (identity boundaries — one role doesn't write as another) by naming *who* writes each artifact. Cross-writer mechanisms in the federation that already satisfy P13: receipt ritual ([ADR-0013](../adr/0013-receipt-ritual.md)), federation-edit proposals ([ADR-0014](../adr/0014-existing-architect-retrofit.md)), per-Architect role-doc ownership ([ADR-0010](../adr/0010-registries-canon-only-sidecar-history.md)). The principle is therefore *retroactively named*, not newly imposed — current practice already conforms.

Adoption by Federation Architect lands in the same change via federation-arch.md v0.9.0 (§4.1 row added — also includes the P11/P12 cleanup that was lagging from sessions 5–6). Per ADR-0010, the role doc is the canonical adoption record. Bootstrap kit role-doc-template §4.1 guidance updated to v0.9.0 set.

---

## 2026-05-23 — P12 added

- **Author:** Federation Architect (session 5)
- **Change type:** add (P12)
- **Citation:** the user, session 5 (2026-05-23): *"manage feature requests to keep the project on schedule"* — positive, action-oriented framing replacing an earlier defensive "drift-resistance" draft. Broadened from "feature requests" to "scope" to catch Architect-side over-delivery, the dominant failure mode in session 5.

P12 (`manage-scope-for-schedule`) statement: *"Manage scope to keep the project on schedule."* Placed in display order directly after P11 — together they form the shipping cluster (P11 = goal, P12 = lever).

---

## 2026-05-23 — Plain-English statement rewrites + P11 added

- **Author:** Federation Architect (session 5)
- **Change type:** edit (all 10 entries' statement field), add (P11)
- **Citation:** the user, session 5 (2026-05-23): existing statements identified as fancy/corporate. Plain English reads faster without losing precision. Bulk rewrite applied to all 10 statement fields; generalization arguments and provenance unchanged.

**P11 — `ship-working-system-on-time`** added with statement *"The goal is always to ship a working system on time"* (the user's own phrasing). Provenance: session 5's own behavior — multiple instances of polish-past-usefulness in this session and prior (ADR-0010 scope creep, architect-learnings.md backfill, v0.4.1 patch for one-line correction). The federation has 10 ADRs and zero real consumers — exactly the failure mode this principle prevents.

**Display order updated.** P11 inserted as second in doc (after P1) per the user's direction that important principles should not sit at the bottom. Stable IDs (P1, P2, …) are unchanged; the registry preamble now explicitly says stable IDs don't imply display order.

---

## 2026-05-23 — Initialization

- **Author:** Federation Architect (session 4, retroactively logged in session 5 per [ADR-0010](../adr/0010-registries-canon-only-sidecar-history.md))
- **Change type:** add

Principles registry created with 10 entries, all status `Accepted`, derived from the session 4 Recipe Box candidate re-classification pass (see [session-handoff.md](../session-handoff.md) session 4 entry).

| Slug | Source | Notes |
|---|---|---|
| `P1 — ambiguity-paid-down-before-commitment` | Recipe Box Architect v0.4.1 §"Communication and planning discipline" | |
| `P2 — system-artifacts-evolve-auditably` | Recipe Box Architect v0.4.1 §"Working directory and conventions"; federation-arch.md §"References" | Broadened in session 4 from session-2's narrower form ("role-doc evolution must be auditable over time") to cover the git-habit cluster (Conventional Commits rides P2). |
| `P3 — data-system-separation` | Recipe Box Architect v0.4.1 §"Authority"; [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md) | |
| `P4 — identity-boundaries-non-collapsing` | Recipe Box Architect v0.4.1 §"Identity"; [ADR-0004](../adr/0004-auditor-as-separate-role-class.md); [ADR-0006](../adr/0006-naming-convention-corrected.md) | |
| `P5 — session-continuity-survives-cold-restart` | Recipe Box Architect v0.4.1 §"Git workflow"; federation-arch.md §10 | |
| `P6 — observations-captured-at-source` | Recipe Box Architect v0.4.1 §"Architect learnings — producer-side"; federation-arch.md §2 | |
| `P7 — transparency-at-conversation-layer` | Recipe Box Architect v0.4.1 §"Narration discipline"; Recipe-Box-side ADR-0020 | |
| `P8 — decisions-auditable` | Recipe Box Architect v0.4.1 §"Communication and planning discipline" | Merges session-2's two principles ("don't pre-bake the decision space" + "show pros/cons with recommendation") into one. |
| `P9 — destructive-ops-confirmed` | Recipe Box Architect v0.4.1 §"Things you should NOT do without checking with the user first"; [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md) §"Carve-out for destructive or irreversible operations" | Named in session 4. Both prior artifacts encoded local versions of an unnamed principle. |
| `P10 — architect-owns-operational-substrate` | Recipe Box Architect v0.4.1 §"Git workflow"; [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md) | Named in session 4. the user's articulation: *"I want to spend zero time on git, other than doing occasional audits."* |

**Scope decisions captured in session 4 (matter for future edits):**

- Principles are universal by [ADR-0008](../adr/0008-three-bucket-taxonomy.md); the principle bucket has no scope axis. Anything narrower demotes to habit.
- The `stay-in-role` habit was initially classified Recipe Box-specific, then re-promoted universal under P4 after the user's challenge: *"impersonation is natural to claude agents. It needs to be baked into a principle and habit."* The narrower framing ("don't impersonate runtime via runtime channels") is Recipe Box Architect's situation-specific instantiation; the broader pattern (don't speak/act as any role you're not) is what P4 covers.
