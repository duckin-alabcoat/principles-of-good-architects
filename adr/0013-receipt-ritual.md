# ADR-0013: Receipt ritual — federation edit distribution mechanism

**Status:** Accepted
**Date:** 2026-05-26
**Deciders:** the user, Federation Architect

## Context

The federation's third responsibility — **redistribute** ([federation-arch.md §2](../federation-arch.md)) — is the moment a principle or universal habit, once promoted in the registries, becomes a behavioral rule inside another Architect's role doc. By [ADR-0008](0008-three-bucket-taxonomy.md), this is the *only* path by which a learning from one system becomes binding on another. The propagation rule is per-Architect role-doc edits, drafted federation-wide and applied per-Architect; the audit trail is preserved by each adoption being its own traceable role-doc event.

ADR-0008 fixed the propagation rule but did not specify the **distribution mechanism** — *how* the proposed role-doc edit physically reaches the receiving Architect, who gates its application, and what artifact trail it leaves. Three options were weighed in session 5 (2026-05-23, [session-handoff.md](../session-handoff.md)):

1. **Direct write** — Federation Architect edits the receiving Architect's role doc itself. Rejected: violates the `stay-in-role` universal habit ([habits/master.md#stay-in-role](../habits/master.md#stay-in-role)) — Federation Architect would be authoring content as another Architect, collapsing the identity boundary [P4](../principles/master.md) is built to protect.
2. **user-as-bridge** — Federation Architect emails / pastes the edit to the user, the user walks it over to the receiving Architect's session, applies it himself. Rejected: works but burdens the user in the per-edit loop. Doesn't scale to bulk federation passes; turns the user into a manual transport layer for what is structurally an Architect-to-Architect handoff.
3. **Receipt ritual** — Federation Architect drops the proposed edit at a known location in the data root; each receiving Architect's session-start ritual checks for pending edits, surfaces them to the user, applies on the user's approval. **Chosen.** Satisfies both `stay-in-role` (receiving Architect makes the final edit to its own role doc) and the architectural envelope from [ADR-0009](0009-data-storage-mechanics.md) (substrate-opaque — works whether data root is local filesystem, mounted volume, git working tree, or synced directory). Async, cross-machine-friendly, no per-edit the user involvement until the receiving session.

Session 5 locked the *mechanism choice*. This ADR pins the **detailed spec** — paths, edit format, lifecycle, acknowledgment, conflict handling, discovery, role-doc trail. Ten architectural sub-decisions are surfaced as recommendations to the user in §"Open for the user" below rather than collapsed unilaterally; the federation is two Architects (Federation Architect, Recipe Box Architect-in-prospect) shy of running this end-to-end, and getting the spec right before the first real receipt is worth more than closing every sub-decision in one pass.

Three existing decisions constrain the design:

- **[ADR-0003](0003-federation-architect-is-a-participant.md)** — Federation Architect is itself a federation participant. The receipt ritual is bidirectional: Federation Architect *sends* edits to other Architects' inboxes, and *receives* edits at its own inbox under the same data root. The recursive case is not an afterthought.
- **[ADR-0009](0009-data-storage-mechanics.md)** — the receipt ritual's artifacts live *under the data root*, so substrate-opacity must be preserved. The ADR talks about paths and files, never about whether sync between machines is filesystem, git, or mounted volume.
- **[ADR-0010](0010-registries-canon-only-sidecar-history.md)** — the receiving Architect's role doc is the canon of adoption. Application of a proposed edit ends in a role-doc version bump; the relationship between the proposed-edit artifact and the role-doc record must be made explicit.

## Decision

### Mechanism

Federation Architect distributes proposed role-doc edits via a **receipt ritual**: edits land as files under each Architect's inbox in the data root; the receiving Architect's session-start ritual reads its inbox, surfaces pending edits to the user, and applies them — gated by `stay-in-role` — on the user's approval. Federation Architect proposes; receiving Architect + the user dispose.

### Inbox layout under the data root

```
<data-root>/
  proposed-edits/
    <architect-id>/
      pending/
        <edit-id>.md
      applied/
        <edit-id>.md
      rejected/
        <edit-id>.md
      withdrawn/
        <edit-id>.md
      index.md
```

- `<architect-id>` matches the convention from [ADR-0006](0006-naming-convention-corrected.md) (`federation-arch`, `recipe-box-arch`, etc.). One subdirectory per receiving Architect.
- `pending/` is the inbox the receiving Architect reads at session start.
- `applied/`, `rejected/`, `withdrawn/` are terminal states. The file moves between directories on state transition; the file *is* the state record (see §"Lifecycle and state transitions").
- `index.md` is a per-Architect roll-up Federation Architect maintains for human readability. Authoritative state is the directory layout, not the index — the index is a derived view, kept current as a courtesy to readers.

The receipt ritual is **bidirectional**. Federation Architect's own inbox lives at `<data-root>/proposed-edits/federation-arch/` and is read by Federation Architect at its session start per the recursive principle ([ADR-0003](0003-federation-architect-is-a-participant.md)). In practice, edits to Federation Architect's role doc may be drafted by Federation Architect itself (self-edits within the maintaining-its-own-role-doc authority granted in [federation-arch.md §9](../federation-arch.md)) — but where another federation pass or an Auditor engagement proposes an edit to Federation Architect, it arrives via this inbox like any other.

### Edit-file shape

Each proposed edit is a single Markdown file with a structured header and a body. The schema:

```markdown
# Proposed edit: <one-line description>

**Edit ID:** <edit-id>
**Target Architect:** <architect-id>
**Target file:** <path relative to target Architect's repo root>
**Expected base version:** <semver of the target role doc at draft time>
**Proposed new version:** <semver after the edit lands>
**Drafted by:** federation-arch
**Drafted on:** YYYY-MM-DD
**Batch ID:** <optional — links sibling edits drafted in the same federation pass>
**State:** Pending

## What changes

<Plain-English summary of the edit's substance. One paragraph.>

## Before

```<lang>
<exact text being replaced, or null for additions>
```

## After

```<lang>
<exact replacement text, or null for removals>
```

## Insertion site

<For additions or moves: where in the target file the new text lands. Section reference, anchor, or "after the line containing X.">

## Rationale

<Why this edit. Cite the principle, habit, or federation event that drove it. Link to the registry entry or ADR.>

## Adoption note for the role-doc CHANGELOG

<One-line CHANGELOG entry the receiving Architect can paste into its role doc's CHANGELOG when the edit lands. Pre-drafted to reduce friction at application time.>
```

The shape is **structured prose**, not a unified diff. Diffs are brittle against role-doc edits that drift between draft and application (whitespace, neighboring-section renumbering, etc.); structured before/after with an insertion-site description is robust to small drift and remains cold-readable.

### Lifecycle and state transitions

```
[draft]
  │
  ▼
Pending ──────► Applied
  │
  ├────► Rejected
  │
  └────► Withdrawn
```

- **Pending** — Federation Architect has dropped the file in `pending/`. The receiving Architect has not yet acted.
- **Applied** — The receiving Architect's role doc has been edited per the proposal, version bumped, CHANGELOG entry added. The proposed-edit file is moved from `pending/` to `applied/`. The user's approval gate is upstream of this transition.
- **Rejected** — the user or the receiving Architect declined the edit. The file is moved from `pending/` to `rejected/`. A short rejection note is appended to the file under a new `## Rejection note` section (date, reason).
- **Withdrawn** — Federation Architect retracts the proposal before the receiving Architect acts (e.g., the federation pass that produced it was reversed, or a conflict was discovered post-drop). The file is moved from `pending/` to `withdrawn/`. Federation Architect can transition `Pending → Withdrawn` autonomously; it cannot transition `Pending → Applied` or `Pending → Rejected` (those belong to the receiving Architect + the user).

**Stale** is not a stored state. Pending edits that have aged past a threshold are *surfaced* by the receiving Architect's session-start ritual ("this edit has been pending 30 days — re-confirm or withdraw?"), but the file remains in `pending/` until acted on. Staleness is a derived property, not a transition.

The file's location in the directory tree (`pending/` vs `applied/` vs `rejected/` vs `withdrawn/`) is the authoritative state record. The `**State:**` header field inside the file should match the directory, but if they diverge, **directory wins** — it is the harder-to-fake signal.

### Discovery at session start

The receiving Architect's session-start ritual includes a step:

> *"Read `<data-root>/proposed-edits/<my-architect-id>/pending/`. For each file, surface to the user in order: 'Pending edit \<edit-id\>: \<one-line description\>, drafted \<date\>. Apply, reject, or defer?'"*

Order is **FIFO by edit-id** (which is timestamp-prefixed; see §"Open for the user" item 1). One edit is presented at a time, not in bulk — this preserves the user's per-edit gate and matches how Architects already handle decision presentation (`structured-decision-presentation` universal habit). If the user chooses to batch-approve ("apply all pending"), the receiving Architect walks the list and applies each, but the surface is per-edit by default.

### Conflict handling

Each proposed edit carries an **`Expected base version:`** field naming the target role doc's version at draft time. At application time, the receiving Architect compares the expected base to the role doc's actual current version:

- **Match** — apply normally.
- **Mismatch** — surface to the user: *"This edit was drafted against v0.5.0 but the role doc is now v0.5.2. Two interim version bumps may or may not interact with this edit. Re-confirm or send back to Federation Architect for redraft?"* The edit is **not** silently applied. The receiving Architect cannot judge whether the drift is harmless renumbering or a substantive collision; the user decides.

If the user opts to send it back, the receiving Architect marks the edit `Rejected` with reason `"version drift; needs redraft against v0.5.2"`, and Federation Architect picks it up on next federation pass.

### Acknowledgment back to Federation Architect

When the receiving Architect transitions an edit to `Applied`, `Rejected`, or `Withdrawn`, the file moves to the corresponding subdirectory under `<data-root>/proposed-edits/<architect-id>/`. Federation Architect re-derives outcomes by walking these directories on its next pass:

- File still in `pending/` → not yet acted on.
- File in `applied/` → adopted; receiving Architect's role doc has the version bump.
- File in `rejected/` → declined; rejection note inside the file gives reason.
- File in `withdrawn/` → Federation Architect's own withdrawal, recorded for history.

The receiving Architect's role doc CHANGELOG is the **canonical record of adoption** per [ADR-0010](0010-registries-canon-only-sidecar-history.md); the `applied/` directory is corroborating evidence and a discoverability surface for Federation Architect, not a parallel record. If they ever disagree, the role doc wins.

### Role-doc trail (the application event)

When an edit is applied:

1. The receiving Architect makes the edit to its own role doc.
2. The role doc's version bumps per its versioning policy.
3. A CHANGELOG entry is added to the role doc. The entry **cites the proposed-edit file by edit-id and path** (e.g., *"Per [proposed-edit 2026-05-28-stay-in-role-clarification](../proposed-edits/federation-arch/applied/2026-05-28-stay-in-role-clarification.md), …"*). The pre-drafted `## Adoption note for the role-doc CHANGELOG` section of the proposed edit is the suggested text.
4. The proposed-edit file is moved from `pending/` to `applied/`. Its `**State:**` header is updated to `Applied`. A `**Applied on:**` field with the date and `**Applied at role-doc version:**` field with the bumped version are added.

The role doc cites the proposed edit; the proposed edit records the role-doc version it became. The two-way pointer makes the audit trail cold-readable from either end.

### Substrate opacity

This ADR specifies paths and files. It does **not** specify how those files move between data roots on different machines. Whatever sync mechanism is configured at the substrate level (mounted volume, git push/pull, rsync, network filesystem) carries the receipt-ritual artifacts the same way it carries everything else under the data root. The receipt ritual works *within* whatever sync is configured; the choice of sync is deferred per [ADR-0009](0009-data-storage-mechanics.md) §"Architectural envelope."

### Recursive case — Federation Architect as recipient

Federation Architect reads its own inbox at `<data-root>/proposed-edits/federation-arch/pending/` at session start per the recursive principle ([ADR-0003](0003-federation-architect-is-a-participant.md)). In normal operation, most edits to federation-arch.md are self-authored (the role doc's §9 grants Federation Architect blanket authority over its own role doc). Edits arriving via this inbox come from:

- **Another federation pass** that produced a self-edit and routed it through the ritual for trail-consistency reasons.
- **An Auditor engagement** (per [ADR-0004](0004-auditor-as-separate-role-class.md)) that produced a proposed edit as its output.
- **the user, directly,** in cases where it is structurally cleaner to drop a proposed edit than to negotiate the change inline.

The same rules apply: the user's approval gate, version drift check, role-doc CHANGELOG citation, file moves to `applied/`.

## Resolutions at Accept

Ten architectural sub-decisions surfaced by the brief. Each was drafted as a recommendation with reasoning. **All ten resolved per recommendations on 2026-05-26 (session 9).** The Decision section above is the canonical text; this section retains the reasoning for provenance.

### Open-1: Path schema specifics

**Recommendation:** `<edit-id>` = `YYYY-MM-DD-<short-slug>`. One file per edit (not one file per Architect). Top-level `index.md` per Architect for human readability, with a row per edit; authoritative state remains the directory layout.

**Reasoning:** Timestamp prefix gives free FIFO ordering by filename sort, and `YYYY-MM-DD` is unambiguous across locales. A short slug (`stay-in-role-clarification`) keeps the filename meaningful at a glance — pure timestamp is opaque, pure slug needs separate ordering metadata. One file per edit is the prerequisite for the granular accept/reject in Open-9; bundling N edits into one file would force atomic-batch-or-nothing semantics. The index.md is a courtesy view, not the source of truth — readers who land on the directory cold get oriented without parsing N files.

**Alternative considered:** Monotonic integer ID (`edit-0001`, `edit-0002`). Rejected: requires Federation Architect to track a per-Architect counter, more state to maintain than the timestamp-prefix scheme buys.

### Open-2: Edit format

**Recommendation:** Structured doc with `Before`, `After`, `Insertion site`, and `Rationale` sections (the schema in §"Edit-file shape" above). Not a unified diff; not a pure natural-language description.

**Reasoning:** Diffs are precise but brittle against role-doc drift — whitespace, neighboring renumbering, or even a section move between draft and application invalidates a patch. Natural-language descriptions are robust but require the receiving Architect to interpret and synthesize edit content, which is exactly the `stay-in-role` boundary the ritual exists to protect (Federation Architect should *give* the exact words, not hand-wave). Structured before/after with insertion-site as prose threads the needle: exact text where exactness matters (the actual edit content), prose where flexibility matters (where it goes).

**Risk:** If the role doc has drifted such that the `Before` text no longer matches exactly, the receiving Architect surfaces the mismatch (parallel to the version-drift check) and escalates to the user. This is a feature — silent fuzzy-matching would defeat the purpose.

### Open-3: Lifecycle states

**Recommendation:** `Pending → Applied | Rejected | Withdrawn`. `Stale` is derived (surfaced by the session-start ritual), not stored.

**Reasoning:** Four states is the minimum that captures real outcomes: applied means it landed, rejected means the user declined, withdrawn means Federation Architect pulled it back. `Stale` would be a fifth state, but a pending-and-old edit is not categorically different from a pending-and-fresh one — both still need a decision. Treating staleness as a property of `Pending` (surface a flag at session start) avoids state-set bloat while still preventing pending edits from rotting unseen.

**Alternative considered:** Add `Deferred` as an explicit state for edits the user wants to revisit later. Rejected: identical semantics to `Pending` from the file's point of view; the user's "come back to this" can be a separate journaling concern, not a transition.

### Open-4: Acknowledgment mechanism

**Recommendation:** Option (b) from the brief — the receiving Architect moves the file into a parallel directory (`applied/`, `rejected/`, `withdrawn/`). Federation Architect re-derives outcomes by walking the directories.

**Reasoning:** Three sub-options were on the table:

- **(a) Sidecar file** in `applied/`. Adds writes but doesn't reduce the `pending/` directory, so Federation Architect would have to cross-reference two directories to know what's outstanding. More writes, no clarity gain.
- **(b) File moves** between directories. *Chosen.* The directory listing of `pending/` is exactly the "what's outstanding" answer — no cross-reference needed. The file's content remains the rationale and trail; only its location changes.
- **(c) Role-doc CHANGELOG is the source of truth**, Federation Architect re-derives by reading role docs. Conceptually purest (matches ADR-0010's adoption-locus rule) but expensive — Federation Architect has to read every role doc on every pass just to know which proposed edits landed. The applied/ directory is a low-cost local cache of the same fact.

The role-doc CHANGELOG remains the *canonical* record of adoption per ADR-0010; the `applied/` directory is corroborating evidence. They should always agree; on disagreement, role doc wins.

### Open-5: Conflict handling — version drift

**Recommendation:** Edits carry an `**Expected base version:**` field. Mismatch at application time surfaces to the user with the choice: apply anyway, reject as "needs redraft," or have Federation Architect produce a redraft. The receiving Architect does **not** silently apply on mismatch.

**Reasoning:** Three failure modes are possible when versions drift:

- **Harmless** — interim version bumps were unrelated (e.g., a typo fix in a different section). The edit applies cleanly.
- **Subtly affected** — interim bumps changed the surrounding context (renumbered sections, renamed slugs) but the edit's substance is fine; the receiving Architect needs to re-anchor the insertion site.
- **Conflicting** — interim bumps already addressed the same concern, or moved in an opposite direction. The edit is wrong to apply.

The receiving Architect cannot distinguish these algorithmically. The user can, with the diff in hand. Surfacing the mismatch and asking is the only path that doesn't risk silent wrong-application.

### Open-6: Discovery semantics

**Recommendation:** FIFO by edit-id (timestamp-sorted), one at a time, surfaced inline during session-start. The user can opt into batch-mode ("just apply all pending") but the default is per-edit.

**Reasoning:** Per-edit matches the existing `structured-decision-presentation` universal habit and preserves the user's per-edit gate. FIFO matches the temporal ordering of when Federation Architect drafted the edits — usually the most relevant order, and the cheapest one (free from the filename sort given Open-1's recommendation). Bulk-mode-by-default would lump unrelated edits together and lose the surface where the user notices a problematic one; bulk-mode-by-opt-in preserves the speed-when-trust-is-high path without making it the default.

**Alternative considered:** Most-recent-first. Rejected: most-recent isn't categorically more important than older-but-pending, and reverses the temporal narrative of "here's what the federation produced in order."

### Open-7: Multi-machine sync responsibility

**Recommendation:** **Out of scope of this ADR.** The receipt ritual operates within whatever sync mechanism is configured at the substrate level. ADR-0009 §"Architectural envelope" already defers substrate choice; this ADR inherits that deferral. ADR-0013 states the deferral explicitly so a reader doesn't ask the question.

**Reasoning:** The substrate question is genuinely orthogonal — the same path-and-file mechanics work for local filesystem (single machine), git push/pull (multi-machine), mounted volume (shared workstation), or cloud-hosted sync (eventually). Picking a substrate inside this ADR would over-commit. The deferral is principled, not a punt.

This is the most uncontroversial of the ten — flagged here for completeness per the brief, but probably the cheapest to confirm.

### Open-8: user-approval mechanics

**Recommendation:** Explicit in the ADR text (§"Mechanism" above): **Federation Architect's session does not approve.** the user's approval gate happens inside the **receiving Architect's session**, surfaced by that Architect's session-start ritual, gated by that Architect's `stay-in-role` discipline. Federation Architect proposes; receiving Architect + the user dispose.

**Reasoning:** This is the load-bearing claim that makes the receipt ritual satisfy `stay-in-role`. Without it, "Federation Architect drops the edit" could be read as "Federation Architect ships the edit, the user confirms post-hoc" — which is the direct-write failure mode in disguise. The approval lives where the role doc lives: in the session of the Architect whose role doc it is. Stating this explicitly removes any ambiguity.

This is more "make explicit" than "recommend" — the substance is locked by session 5's mechanism choice. The recommendation is the prose framing.

### Open-9: Bulk vs individual

**Recommendation:** **One file per edit.** Granular accept/reject per edit. When a federation pass produces N edits for one Architect, drop N files. Optionally, link sibling edits via a shared `**Batch ID:**` header field, so the receiving Architect's session-start ritual can present them as "5 edits in batch 2026-05-28-shipping-cluster — review individually or apply as set?"

**Reasoning:** Bundled-file (atomic accept of the whole batch) sounds efficient but loses the failure-recovery mode the per-Architect role-doc-edit rule from [ADR-0008](0008-three-bucket-taxonomy.md) was designed to preserve: if one edit in a batch turns out to be wrong, the whole batch fails together, and reversing one means reversing all. Granular files preserve per-edit adoption events (each is a separate version bump on the role doc, each is a separate CHANGELOG entry, each is independently reversible). The `Batch ID` field is the lightweight affordance for "these belong together" without forcing atomic semantics.

**Tradeoff acknowledged:** Five files for five edits is more directory entries than one file for five edits. The clarity gain (granular state, granular rollback, granular CHANGELOG) is worth the directory noise.

### Open-10: Role-doc trail — what cites what

**Recommendation:**

1. The bumped role doc **cites the proposed-edit file** by edit-id and path in its CHANGELOG entry.
2. The proposed-edit file **records the resulting role-doc version** in a `**Applied at role-doc version:**` field added at application time.
3. The proposed-edit file is **moved to `applied/`** after the role-doc bump.
4. **The role-doc CHANGELOG is the canonical record of adoption** per [ADR-0010](0010-registries-canon-only-sidecar-history.md). The `applied/` file is corroborating evidence and discoverability aid. On disagreement, role doc wins.

**Reasoning:** Two-way pointers (role doc → edit file, edit file → role doc version) make the audit trail cold-readable from either end: a reader landing on the role doc sees what drove a bump; a reader landing on the proposed-edit file sees what it became. ADR-0010 already settled the canonical-record question — adoption is recorded in the role doc, the registries are canon-only. The receipt-ritual artifact is *not* a third source of truth; it's the artifact that *carried* the change, retained for trail.

Archiving via move (`pending/ → applied/`) keeps `pending/` clean — the directory listing of `pending/` is the "what's outstanding" view at all times.

## Alternatives Considered

The three options session 5 weighed, captured faithfully for the historical record:

### Alternative 1: Direct write

Federation Architect edits the receiving Architect's role doc directly, then notifies the user.

**Rejected.** Violates the `stay-in-role` universal habit ([habits/master.md#stay-in-role](../habits/master.md#stay-in-role)) — Federation Architect would be authoring content as another Architect, collapsing the identity boundary [P4](../principles/master.md) is built to protect. The recursive principle ([ADR-0003](0003-federation-architect-is-a-participant.md)) extends the boundary to Federation Architect itself: just as no Architect speaks as another, Federation Architect does not act as one. Direct write would also remove the user's per-edit gate in the receiving Architect's session — approvals would either pile up post-hoc or get skipped.

### Alternative 2: user-as-bridge

Federation Architect produces the proposed edit; the user receives it (email, message, hand-off note), walks it into the receiving Architect's session, applies it himself.

**Rejected.** Works structurally — `stay-in-role` is preserved, the user's gate is enforced — but burdens the user in the per-edit loop. Doesn't scale to bulk federation passes (a five-edit pass becomes five hand-offs). Turns the user into a manual transport layer for what is architecturally an Architect-to-Architect handoff. The architecture should make the user's per-edit *decision* explicit, not the user's per-edit *transport* manual.

### Alternative 3: Receipt ritual (chosen)

Federation Architect drops the proposed edit at a known inbox location in the data root; each Architect's session-start ritual checks for pending edits, surfaces them to the user, applies on approval.

**Chosen.** Satisfies both `stay-in-role` (receiving Architect makes the final edit to its own role doc with the user's consent) and the architectural envelope from [ADR-0009](0009-data-storage-mechanics.md) (substrate-opaque — works whether data root is local filesystem, git working tree, mounted volume). Async — no real-time coordination required between Federation Architect's drafting session and the receiving Architect's application session. Cross-machine-friendly via the data-root abstraction. The user's per-edit gate is preserved where it belongs: in the receiving Architect's session.

### Sub-mechanism alternatives (within the chosen mechanism)

Each of the ten open items in §"Open for the user" carries its own alternatives-considered analysis. Not duplicated here; see the individual items for the rejected paths.

## Consequences

### Immediate

- `<data-root>/proposed-edits/` becomes a recognized directory in the data root. Added to `.gitignore` per [ADR-0007](0007-github-as-system-storage-data-excluded.md)'s rule that path-pattern presence is a data declaration. (Proposed edits are *transient artifacts about role-doc edits* — they live in the data root because they're per-Architect and per-machine, and they're gitignored because they don't belong in the public system repo.)
- The role-doc template (forthcoming bootstrap kit, next-step #1 in session-5/-6 handoff) gains a `## Federation edit receipts` placeholder section, citing this ADR. The receiving Architect's session-start ritual gains a step: "Read your inbox; surface pending edits."
- Federation Architect's own role doc ([federation-arch.md](../federation-arch.md)) needs the same receipt-ritual section added in a subsequent role-doc bump; the recursive case is part of the architecture, not optional.

### On Federation Architect's session rituals

Federation Architect's §11 session-start ritual gains a step: *"Read `<data-root>/proposed-edits/federation-arch/pending/`; surface any pending edits before responding to the user's first substantive request."* This lands in the federation-arch.md v0.X.0 bump that absorbs this ADR.

Federation Architect's session-end ritual is unchanged. Producing proposed edits (drops into other Architects' inboxes) is part of normal federation-pass work, gated by the standing edit-drafting authority in [federation-arch.md §9](../federation-arch.md) — no special ritual hook needed beyond writing the file.

### On the bootstrap kit

The bootstrap kit (next-step #1 in the session-5 handoff) needs a placeholder for the receipt ritual in the role-doc template even before the kit is built. With this ADR drafted, the placeholder can render against a real spec — the section will say "read your inbox at `<data-root>/proposed-edits/<my-architect-id>/pending/`" rather than `<TBD>`. The the user-approved compromise in session 5 ("include the receipt-ritual slot in the role-doc template even before its full ADR is drafted") is satisfied with the ADR in hand.

### On future federation passes

The first real federation pass that produces a role-doc edit will exercise the ritual end-to-end. Expect rough edges — at minimum, version-drift handling, conflict resolution, and the user's preferred discovery surface are likely to need refinement based on what surfaces. ADR-0013 is the first cut; same-session same-day refinement (per [ADR-0010](0010-registries-canon-only-sidecar-history.md)'s precedent) is the recovery path if the first pass surfaces a real gap.

### Risks

- **Inbox bit-rot.** Pending edits that pile up unreviewed lose context over time. Mitigation: staleness surfaced at session start (§"Lifecycle and state transitions"); Federation Architect can withdraw stale edits autonomously per the `Pending → Withdrawn` transition. Not a state explosion, just a soft prompt.
- **Substrate-coupling regression.** Future temptation to read or write inbox files via non-data-root paths (e.g., a direct write to another Architect's repo when the same machine hosts both) would re-introduce the substrate coupling ADR-0009 eliminated. Mitigation: the ritual operates *only* through the data root abstraction; any cross-Architect write that bypasses it is a regression flag.
- **Recursive-case underuse.** Federation Architect's own inbox may sit empty in practice if most self-edits go through the standing self-edit authority. The inbox is still required by the recursive principle. Mitigation: this is fine — empty is a valid state; the inbox exists for the Auditor and external-pass cases, which will arrive as the federation matures.
- **Index drift.** The `index.md` per-Architect roll-up can drift from the directory state if not refreshed. Mitigation: authoritative state is the directory layout, not the index. The index is a courtesy view; if it lies, readers fall back to `ls pending/`.

### Deferred

- **Sync substrate.** Per ADR-0009 — out of scope here, locked when federation actually spans machines.
- **Retrofit for existing Architects.** Recipe Box Architect (the only other Architect with an existing role doc) needs the receipt-ritual section added to come into compliance. The retrofit path is a separate conversation; ADR-0013 specifies the ritual for *participating* Architects, not the migration mechanism for existing ones.
- **Auditor → receipt-ritual path.** Auditor engagements ([ADR-0004](0004-auditor-as-separate-role-class.md)) producing proposed role-doc edits as output is mentioned in §"Recursive case" but not specified mechanically. When the first Auditor engagement actually produces an edit, the path can be pinned. Probably trivial: the Auditor's deliverable lands in the relevant Architect's inbox.
- **Concurrent-write semantics.** If two federation passes (or a federation pass and an Auditor) both drop edits for the same Architect at the same time, with overlapping content, no coordination mechanism is specified. Mitigation by deferral: at current scale (one Federation Architect, zero Auditor engagements) this cannot happen; revisit when it can.

## References

- [ADR-0003: The Federation Architect is a federation participant](0003-federation-architect-is-a-participant.md) — recursive principle. The receipt ritual is bidirectional; Federation Architect itself receives via this mechanism.
- [ADR-0008: Three-bucket taxonomy — principle, habit, preference](0008-three-bucket-taxonomy.md) — propagation rules. Universal habit and principle propagation = per-Architect role-doc edit; this ADR specifies the *mechanism* that propagation uses.
- [ADR-0009: Data storage mechanics — data root, habit registry, user profile](0009-data-storage-mechanics.md) — the receipt ritual's artifacts live under the data root; substrate-opacity is inherited.
- [ADR-0010: Registries are canon-only; sidecar history pattern is uniform across all three](0010-registries-canon-only-sidecar-history.md) — role doc is canon of adoption. The receipt ritual ends in a role-doc bump; the proposed-edit file is corroborating evidence, not a parallel record.
- [federation-arch.md](../federation-arch.md) — role doc; §2 mission item 3 (Redistribute), §3 What I do NOT do (the `stay-in-role` constraints the receipt ritual exists to satisfy), §9 Approval gates.
- [habits/master.md#stay-in-role](../habits/master.md#stay-in-role) — the universal habit the receipt ritual must not violate.
- [habits/master.md#session-vision-anchoring](../habits/master.md#session-vision-anchoring) — applied during the drafting of this ADR; informs the "Open for the user" pattern (surface decisions, don't collapse unilaterally).
- [habits/master.md#session-end-handoff-before-signoff](../habits/master.md#session-end-handoff-before-signoff) — universal session-end habit; receipt-ritual checks slot inside each Architect's session-start ritual (the symmetric session-start habits).
- [session-handoff.md](../session-handoff.md) — session 5 entry, §"Redistribute mechanism locked: receipt ritual": locked mechanism choice and the three options weighed. Faithfully captured in §"Alternatives Considered."
- [adr/template.md](template.md) — the ADR shape this draft conforms to.
