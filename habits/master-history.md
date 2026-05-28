# Habits history

Append-only change log for [`master.md`](master.md). Newest-first. Per [ADR-0010](../adr/0010-registries-canon-only-sidecar-history.md).

Each entry records: date, change type (add / edit / status-flip / remove / rename), affected slug(s), prior value for edits or removes, citation that justified the change, author. Cold-readable — someone reading this file alone can reconstruct what happened.

Per-Architect adoption events are **not** logged here — they are logged in the adopting Architect's role doc (per [ADR-0010](../adr/0010-registries-canon-only-sidecar-history.md)).

---

## 2026-05-27 — Add `no-fabricated-data`

- **Author:** Federation Architect (session 12)
- **Change type:** add
- **Slug:** `no-fabricated-data`
- **Parent:** [P7 — transparency-at-conversation-layer](../principles/master.md#p7--transparency-at-conversation-layer)
- **Status on landing:** Accepted
- **Citation:** In-session incident, session 12 (2026-05-27): produced a summary table entry *"End (so far): v0.10.0 · Laptop · ~11:30 CDT · ~3h 15m"* without running `date`. Actual time at production was 09:08 CDT. the user caught it: *"whoa, your stamp had the wrong time."*

Habit drafted in response to the incident. Two structural options surfaced: (1) sharpen the existing `session-stamp-and-counter` habit to cover summary-table references, (2) draft a broader anti-fabrication habit covering timestamps, counts, paths, versions, hashes, and existence claims. the user chose option 2: *"2."*

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
- **Citation:** Recipe Box Architect role doc v0.4.1 §"Narration discipline" (no-compound-bash, narration-specificity refinements); §"Git workflow" / mid-session commits language (WIP-commit framing).

Third batch of cross-Architect lifts. Source: Recipe Box Architect (Basil) role doc v0.4.1, mirrored at federation `inputs/personal-assistant-arch.md`. Per the user's session-12 review, three of Recipe Box Architect's five candidate patterns passed the universal-failure-mode-breadth test; the fourth (abandoned-session detection via transcript scan) was deemed Recipe Box-specific given Recipe Box Architect's deployment shape (long-lived paired runtime + multi-machine + frequent mid-session interruptions); fifth (cold-start reading list) was dropped as unnecessary for federation's smaller doc tree.

**Bounded-sharing open question reaffirmed:** Recipe Box Architect's `abandoned-session-detection` mechanism is a concrete instance of the cross-Architect-but-bounded habit case noted in federation-arch.md §13 open questions. Not pre-solving the taxonomy amendment until a second instance appears.

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
  - `session-end-handoff-before-signoff` (edit — statement sharpened with "Write Before Goodbye" framing)
- **Parent:** all three under [P5 — session-continuity-survives-cold-restart](../principles/master.md#p5--session-continuity-survives-cold-restart)
- **Status on landing:** Accepted
- **Citation:** Home Hub Architect (Hearth) architect.md §AGENT WRITE PROTOCOL (topic-break writes, W command) and §"SYSTEM DESIGN PRINCIPLES" (Write Before Goodbye, Sessions Have No Reliable Endings). C-command addition from home-hub-arch session 35 (2026-05-26).

Second batch of session-rituals adoptions from home-hub-arch. Completes the session-durability surface end-to-end:

| Failure mode | Mitigation habit |
|---|---|
| Session crashes after sign-off | `session-end-handoff-before-signoff` (in-place, sharpened today) |
| Session crashes mid-flight, work lost since session start | `mid-session-checkpointing` (new) |
| Session crashed and never reopened cleanly | `session-orphan-detection` (added 2026-05-27 morning) |
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
- **Citation:** Home Hub Architect (Hearth) architect.md v3.1 §SESSION START PROTOCOL + §SESSION END PROTOCOL (2026-05-21) for stamp format and orphan detection; v3.2 §SESSION START PROTOCOL step 1 (2026-05-22) for silent-resync framing on the existing `session-start-git-ritual` habit.

The Federation Architect's session-rituals surface lifted three behaviors out of home-hub-arch's protocol — explicit stamp lines with version + machine + timestamp, a monotonic session counter with corruption tagging, and orphan-session detection on cold restart. The first two are fresh habits; the third is an amendment to the existing `session-start-git-ritual` habit (statement clarified to require *silent* refresh — never raise sync state as a user-visible launch-folder issue).

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

Companion habit to [`session-vision-anchoring`](master.md#session-vision-anchoring) under the same parent (P12). The habit is the planning-time decomposition question (*can this work be split and dispatched in parallel?*); the mechanism (work-package briefs, stage-and-stop semantics, Open-for-the user discipline, fold-back flow) is specified in ADR-0015 and not duplicated here.

Adoption by Federation Architect lands in the same change via federation-arch.md v0.7.0 (§4.2 row added). Per ADR-0010, the role doc is the canonical adoption record.

---

## 2026-05-24 — Add `session-vision-anchoring`

- **Author:** Federation Architect (session 6)
- **Change type:** add
- **Slug:** `session-vision-anchoring`
- **Parent:** [P12 — manage-scope-for-schedule](../principles/master.md#p12--manage-scope-for-schedule)
- **Status on landing:** Accepted (same-session draft → accept, the user-approved; precedent: P11/P12 in session 5)
- **Citation:** Federation Architect session 5 (2026-05-23) — the user: *"Making good stuff, but hard to remember the vision in the middle of details."* Deferred from session 5 as the concrete practice implementing the P11/P12 shipping cluster. Drafted and accepted in session 6 (2026-05-24).

Companion habit to the shipping cluster (P11 names the goal, P12 the lever, this habit pulls the lever in-session). Parent is P12 alone, not P11+P12: every move in the habit (time-budget, vision pull-back, decision-forcing, scope surfacing) is a scope-management move; P11 anchors the failure-mode argument but doesn't directly govern in-session behavior.

Adoption by Federation Architect lands in the same change via federation-arch.md v0.5.0 (§4.2 row added). Per ADR-0010, the role doc is the canonical adoption record.

---

## 2026-05-23 — Plain-English statement rewrites

- **Author:** Federation Architect (session 5)
- **Change type:** edit (all 13 entries; statement field only)
- **Citation:** the user, session 5 (2026-05-23). Same pass as the principles statement rewrites. Fancy/corporate phrasing replaced with plain English. Slugs, parent principles, failure-mode arguments, and citations unchanged.

---

## 2026-05-23 — Adoption fields stripped from all entries

- **Author:** Federation Architect (session 5)
- **Change type:** edit (schema change applied to all 13 entries)
- **Citation:** [ADR-0010](../adr/0010-registries-canon-only-sidecar-history.md) — adoption is not registry-resident; it lives in the adopting Architect's role doc.

Each habit entry previously carried an "Adoption" subsection enumerating Recipe Box / Federation Architect / Other-Architects status. Per ADR-0010, that information is duplicative of the role-doc record and was hand-maintained, making it drift-prone. All "Adoption" subsections and the "Adoption-status legend" at the bottom of the file were removed in the same change.

The prior adoption state (as of 2026-05-23 init) was, for the record:

| Habit slug | Recipe Box | Federation Architect | Other |
|---|---|---|---|
| `exhaust-questions-in-planning` | adopted v0.4.1 | de-facto practiced; pending v0.4.0 formalization | pending |
| `versioned-role-doc-and-changelog` | adopted v0.1.0+ | adopted v0.1.0+ | pending |
| `gitignore-enforces-data-system-boundary` | adopted | adopted (ADR-0007) | pending |
| `stay-in-role` | adopted | adopted in policy (federation-arch.md §3); pending explicit habit-level framing | pending |
| `session-end-handoff-before-signoff` | adopted | adopted (federation-arch.md §10) | pending |
| `producer-side-learnings` | adopted | pending (no `architect-learnings.md` stood up yet) | pending |
| `narrate-consequential-tool-calls` | adopted | partial; practiced but not formalized | pending |
| `structured-decision-presentation` | adopted | partial; reinforced by memory `feedback-recommend-dont-optionate` | pending |
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

Universal habit registry created with 13 entries, all status `Accepted`, derived from the session 4 Recipe Box candidate re-classification pass (see [session-handoff.md](../session-handoff.md) session 4 entry). Each carries a named parent principle per the [ADR-0008](../adr/0008-three-bucket-taxonomy.md) requirement that universal habits implement an underlying principle.

| Slug | Parent | Source |
|---|---|---|
| `exhaust-questions-in-planning` | P1 | Recipe Box v0.4.1 §"Communication and planning discipline" |
| `versioned-role-doc-and-changelog` | P2 | Recipe Box v0.4.1 §"CHANGELOG"; Recipe-Box-side ADR-0019 |
| `gitignore-enforces-data-system-boundary` | P3 | Recipe Box v0.4.1 §"Authority"; [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md) |
| `stay-in-role` | P4 | Recipe Box v0.4.1 §"Identity" + §"Authority"; federation-arch.md §3 |
| `session-end-handoff-before-signoff` | P5 | Recipe Box v0.4.1 §"At session end" step 1 |
| `producer-side-learnings` | P6 | Recipe Box v0.4.1 §"Architect learnings — producer-side" |
| `narrate-consequential-tool-calls` | P7 | Recipe Box v0.4.1 §"Narration discipline"; Recipe-Box-side ADR-0020 |
| `structured-decision-presentation` | P8 | Recipe Box v0.4.1 §"Communication and planning discipline" |
| `conventional-commits` | P2 | Recipe Box v0.4.1 §"Working directory and conventions" |
| `session-start-git-ritual` | P5 | Recipe Box v0.4.1 §"At session start" |
| `session-end-git-ritual` | P5 | Recipe Box v0.4.1 §"At session end" |
| `confirm-destructive-ops` | P9 | Recipe Box v0.4.1 §"Things you should NOT do without checking with the user first"; [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md) |
| `routine-ops-autonomy` | P10 | Recipe Box v0.4.1 §"Authority" / §"Git workflow"; [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md) |

**Scope decisions captured in session 4 (matter for future edits):**

- The `stay-in-role` habit was initially demoted to Recipe Box-specific, then re-promoted to universal after the user's session-4 challenge — see the parent-principle entry's narrower-vs-broader framing in `master.md`.
- "Recovery from correction = one-line ack" was folded into `narrate-consequential-tool-calls` rather than retained as a standalone preference.
- The handoff doc lists several Federation Architect adoption rows as "pending v0.4.0 bump" — those are not action items for this registry; they are action items for federation-arch.md, which is the canonical adoption record per [ADR-0010](../adr/0010-registries-canon-only-sidecar-history.md).
