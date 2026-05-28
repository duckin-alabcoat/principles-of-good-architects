# ADR-0011: Data-root configuration form — declared in role-doc top metadata

**Status:** Accepted
**Date:** 2026-05-26
**Deciders:** the user, Federation Architect

## Context

[ADR-0009](0009-data-storage-mechanics.md) introduced the **data root** as the indirection that decouples *where data lives on a given machine* from *how data syncs across machines*. Every federation-participating Architect reads and writes its data through this path; the substrate behind the path (local directory, mounted volume, git working tree, network filesystem) is below the abstraction and can change without affecting the Architect's code or prose.

ADR-0009 named the data root and pinned what lives under it (`principles/`, `habits/`, `users/<user-id>/`, plus their sidecars per [ADR-0010](0010-registries-canon-only-sidecar-history.md)). It explicitly left one slot empty: *where the `<data-root>` value itself is declared*. The phrase ADR-0009 used was "declared in that Architect's local config" — a placeholder for a real mechanism.

That gap has surfaced in two places:

1. **Bootstrap.** The forthcoming bootstrap kit (next-step #1 in the session-6 handoff) needs to tell a new Architect's first session how to declare its data root. The kit can't render that section of the role-doc template without a pinned form.
2. **Federation Architect itself.** [ADR-0003](0003-federation-architect-is-a-participant.md) makes Federation Architect a federation participant on equal footing with every other Architect. It reads from the data root. Its current role doc ([federation-arch.md](../federation-arch.md), v0.5.0) has no `Data root:` declaration — the path is implicit in the working directory. That is a gap, not a feature.

The session-6 handoff records the locked direction (§"Pending decisions / drafted but not yet ADR'd"): *"Data-root config form — recommended (declared in role-doc top metadata)."* This ADR renders that direction into form.

Two constraints from prior ADRs shape the design:

- **[ADR-0007](0007-github-as-system-storage-data-excluded.md) classification rule.** The `Data root:` *declaration* (a path string in a role doc) is system, not data — the role doc is system, and the field is a configuration knob on the system, not content drawn from elsewhere. Different from what the path *points at*, which is data.
- **[ADR-0010](0010-registries-canon-only-sidecar-history.md) role-doc-as-canon convention.** The role doc is the canonical record of each Architect's adopted principles, habits, and overrides. Putting the data-root declaration in the role doc is consistent with that locus: the role doc already carries the per-Architect configuration that defines the Architect's binding to the federation.

This ADR pins the **declaration form** only. It does not redefine the data-root abstraction (ADR-0009 owns that), does not pick a substrate (substrate-opacity is preserved), and does not specify how Architects organize their own GitHub repos (that's ADR-0012's territory).

## Decision

### Declaration site

Each federation-participating Architect declares its data root in the **top metadata block of its role doc**. Same block that already carries `Version:`, `Status:`, `Last updated:`, `System ID:`, `Architect ID:`.

### Field name and shape

The field name is **`Data root:`** — matches the `<data-root>` token used throughout ADR-0009 and ADR-0010, so the prose and the configuration use the same word.

The value is a single path string. Example shape:

```markdown
# <Architect Name> — role doc

**Version:** X.Y.Z
**Status:** Active
**Last updated:** YYYY-MM-DD
**System ID:** `<system-id>`
**Architect ID:** `<architect-id>`
**Data root:** `<path>`
```

The field sits immediately after `Architect ID:`. It is the last entry in the top metadata block.

### Resolution

At session start, the Architect reads its own role doc (already part of the session-start ritual per [federation-arch.md §11](../federation-arch.md#11-session-rituals) and the `session-start-git-ritual` universal habit), takes the `Data root:` value verbatim, and treats it as the path to the data root for the remainder of the session. The value is opaque: the Architect does not inspect what kind of substrate it resolves to.

Session-start consumption of this field is upstream of the override-resolution step (federation-arch.md §11 step 6 / ADR-0009 §"Overrides") — the data root must be resolved before the user profile can be read.

### Scope of applicability

Every federation-participating Architect carries this field. By [ADR-0003](0003-federation-architect-is-a-participant.md) (recursive participation) extended via [ADR-0008](0008-three-bucket-taxonomy.md), this includes Federation Architect itself.

The field is required for any Architect that reads from or writes to the data root — which, by ADR-0009, is every Architect bound to a user. There is no exempt class.

### Format rules

- **Absolute path.** The value must be an absolute path string. No `~`-relative paths, no environment-variable interpolation, no shell expansion. (See §"Open for the user" item 1 — this is the recommended default; the user resolves.)
- **No trailing slash.** Canonicalized without trailing `/`. The Architect always joins sub-paths (`<data-root>/users/...`) by appending; a trailing slash on the declared value would produce `//` joins. Cosmetic but worth pinning.
- **One line, in backticks.** Rendered as inline code (`` `<path>` ``) so the path is visually distinct from prose and doesn't tempt the reader into reflowing it.
- **No comments inline.** If the path needs explanation (e.g., "this is the mounted volume"), the explanation goes in a footnote or in the role doc's CHANGELOG for the version that introduced it — not on the same line as the value.

### Substrate-opacity is preserved

The path string is opaque to the Architect. The Architect knows the path; it does not know, and does not branch on, whether the path resolves to a local filesystem, a mounted volume, a git working tree, a synced directory, or something else. That choice is below the abstraction, per ADR-0009 §"The data root abstraction." This ADR does not narrow that envelope.

The format rules above are about the **declaration**, not about the substrate. An absolute-path declaration can resolve to any of the substrates ADR-0009 contemplates; the rules constrain the form of the declared string, not what it points at.

### Interaction with ADR-0010

The role doc is the canonical record of adoption per ADR-0010. Adding the data-root declaration to the role doc's top metadata extends that locus — the same document that records *what* the Architect has adopted now also records *where* its data lives. Both are per-Architect configuration; both belong with the Architect.

The data-root field is **not** a registry entry. It does not get a sidecar history file. Its change history rides on the role doc's own CHANGELOG, the same way any other top-metadata edit (version bump, status flip, ID rename) does.

## Resolutions at Accept

Five questions the brief surfaced that the foreground session did not pre-resolve. Each was drafted with a recommendation and reasoning. **All five resolved per recommendations on 2026-05-26 (session 8).** The Decision section above is the canonical text; this section retains the reasoning for provenance. Question 5 in particular was confirmed empirically in-session — see note inline.

### 1. Absolute vs. relative paths

**Question.** Should `Data root:` require an absolute path, or permit `~`-relative paths and environment-variable interpolation (e.g., `$HOME/...`)?

**Recommendation.** **Absolute only.**

**Reasoning.** Absolute paths are unambiguous, cold-readable, and substrate-agnostic in the sense ADR-0009 cares about — any substrate the Architect might be pointed at can be expressed as an absolute path. `~` and env-var interpolation introduce a resolution step that depends on the shell environment of whatever process the Architect runs under; that is one more failure mode to debug when the data root is misconfigured. The argument *for* interpolation is portability across machines with different home-directory layouts, but the architectural envelope already addresses that differently: each machine has its own role doc instance (see item 5 below), and per-machine paths are expected to differ. Interpolation would smuggle a multi-machine pattern into a per-machine field.

**Flagged alternative.** Permit `~`-relative paths only (no env-var interpolation). Mildly more portable for the same-machine case, modest added complexity. Reject if simplicity is the priority; accept if the user notices the field will frequently be `~/some-dir` and wants to write it that way.

### 2. Default if missing

**Question.** What happens if an Architect's role doc has no `Data root:` field at session start?

**Recommendation.** **(a) Refuse to start. Loud failure.**

**Reasoning.** The data root is required for resolving the user profile, the registries, and all data-root-resident artifacts. An Architect that runs without it would either crash later on the first data read (silently mis-orienting until then) or substitute an inferred default that may be wrong (silently writing data to the wrong place). Loud failure at session start surfaces the misconfiguration at the right moment — when the user can fix it before any work depends on the answer. Consistent with ADR-0009's "dangling override is surfaced, not silently dropped" stance (R7.3).

**Flagged alternatives.**
- **(b) Infer from the role doc's location** (assume `<role-doc-dir>/`). Convenient for the same-machine case where the role doc and the data root often co-locate; dangerous in the multi-machine case where they may not. Reject as the default; could be a fallback flagged with a warning.
- **(c) Prompt at session start.** Interactive prompting violates the architectural envelope — a cloud-hosted Architect has no interactive session. Reject.

### 3. Update process — version bump grade for `Data root:` edits

**Question.** When the `Data root:` value changes (machine swap, repo move, substrate migration), what version-bump grade does the role-doc edit get? ADR-0010 doesn't cover config-only edits explicitly.

**Recommendation.** **Patch bump.**

**Reasoning.** The data-root edit changes *where* the Architect reads its data, not *what* it does, not *what* it has adopted, not *what authority* it carries. Mission, scope, behavioral surface, and adoption set are all unchanged. Under each role doc's own versioning policy (see federation-arch.md §10 as the prototype: MAJOR for mission, MINOR for new behavioral surface or formal adoption, PATCH for clarification or correction without behavioral change), a config-only edit is patch-grade. The CHANGELOG entry records the old and new paths and the reason for the change (e.g., "data root moved from `/Volumes/.../foo` to `/Users/.../foo` per machine migration on YYYY-MM-DD").

**Flagged alternative.** **Minor bump** on the argument that the data-root change can break the Architect (if the new path is wrong, the next session fails to start per item 2). Reject — the *risk* of a misconfigured edit doesn't change the *grade* of a correct one. Other patch-grade edits (e.g., correcting a typo in a slug reference) carry similar risk. Grade is about scope of change, not about blast radius if done wrong.

### 4. Federation Architect's own data root declaration

**Question.** Federation-arch.md currently has no `Data root:` field. Does adopting this ADR trigger a version bump on federation-arch.md to add the field, and what does the CHANGELOG entry say?

**Recommendation.** **Yes — trigger a minor bump on federation-arch.md (next version `0.6.0`) in a separate, post-Accept commit.**

**Reasoning.** Federation Architect is a federation participant per ADR-0003 and is bound by this ADR once accepted. Adding the field is *not* a config-only edit in the sense of item 3 — it is the first-time addition of a required field, which is a structural change to the top metadata block. Under federation-arch.md §10, "structural change to artifacts maintained" is minor; "wording, clarification, or correction without behavioral change" is patch. Adding a required field is structural.

Per the brief's acceptance criterion #4, this ADR does **not** edit federation-arch.md. The bump lands in the same session that accepts this ADR (or a follow-up), not here.

**Suggested CHANGELOG entry shape (for the foreground session to refine when it lands the bump):**

> **0.6.0** (YYYY-MM-DD) — Added `Data root:` field to the top metadata block per [ADR-0011](adr/0011-data-root-config.md). Value pinned to the federation's existing working-directory path. Minor — structural addition to the top metadata block; no change to mission, scope, or authority.

**Flagged alternative.** Patch bump on the argument that the field's *value* (the federation working directory) is the current implicit reality and the edit just makes it explicit. Reject — the *form* changes, not just the value, and other Architects landing this ADR will be doing the same structural addition. Treating Federation Architect's adoption identically to every other Architect's adoption is the consistent move.

### 5. Multi-machine identity

**Question.** If the same logical Architect runs on two machines (laptop + workstation), each has its own role doc clone with potentially different data-root paths. Is the role doc a per-machine artifact, or is the `Data root:` field machine-conditional (e.g., a table keyed by hostname)?

**Recommendation.** **Per-machine role doc instance, single-valued `Data root:` field.** Each machine carries its own clone of the role doc; each clone's `Data root:` is the absolute path on that machine. Machine-conditional syntax is rejected.

**Session-8 empirical note.** Confirmed during Accept: the user works from two machines (a workstation, `workstation.local`, plus another machine). Both clone the federation repo from GitHub onto local disk under `/Users/user/principles-of-good-architects` — same username, same path layout. Implication: the `Data root:` field is single-valued *and* identical across their current machines, so the per-machine-clone model is friction-free in practice today. The drift problem the recommendation flagged still exists architecturally (clones can diverge on non-`Data root:` fields between pulls), but is not amplified by needing different `Data root:` values.

**Reasoning.**

- Machine-conditional syntax (a table, a switch, hostname-keyed values) introduces a parser the Architect would need, plus a hostname-resolution step, plus the question of what to do when running on a hostname not listed. All of that lives in the role doc, which is supposed to be cold-readable prose.
- The per-machine-clone model matches how ADR-0009 already thinks about multi-machine federations (§"Architectural envelope" — "Each machine has its own clone; the read-freshness window is enforced by pulling at session start"). The data root is per-machine; the role doc carrying its declaration is correspondingly per-machine in its instance, even when the *content* (mission, scope, adopted principles, etc.) is identical across instances.
- This does open a real coordination problem: two role-doc clones can drift on fields *other than* `Data root:`. That is the "data-root configuration drift" risk ADR-0009 flagged, generalized one level. **It is not solved here.** ADR-0009 left it deferred; this ADR does not pre-solve it. When the federation actually spans machines (currently it does not), a follow-up ADR can pin a sync discipline. Until then, per-machine clones are the working model.

**Flagged alternative.** Machine-conditional table inside the field, e.g.:

```markdown
**Data root:**
- `laptop.local`: `/Users/.../foo`
- `workstation.local`: `/Volumes/.../foo`
```

Reject for the parser/cold-readability reasons above. Re-open if the user has a use case where two machines must share a single role-doc clone (e.g., the clone is itself in cloud storage and is read from both machines).

## Alternatives Considered

- **Declare the data root in a separate config file** (e.g., `.federation-config` or similar at the repo root). Rejected — adds a second per-Architect configuration artifact when the role doc is already the canonical per-Architect config locus (per ADR-0010). Splitting the configuration surface invites the question of which file is authoritative when they disagree. One file, the role doc, keeps the locus singular.

- **Declare the data root via an environment variable** (e.g., `FEDERATION_DATA_ROOT`). Rejected — pushes the configuration outside the role doc and into the shell environment. Not cold-readable (the role doc no longer tells you where the data is), not portable to substrates where there's no shell to set environment variables, and depends on whatever process spawns the Architect.

- **Infer the data root from the role doc's filesystem location.** Rejected as the default (see §"Open for the user" item 2). Convenient when the role doc and the data root co-locate; brittle when they don't. Could be a fallback behind an explicit opt-in flag, but not the primary mechanism.

- **Make the data root field optional with a built-in default.** Rejected — see §"Open for the user" item 2. Optional fields with silent defaults are the failure mode this ADR is specifically trying to avoid.

- **Bury the field deeper in the role doc** (e.g., in §"User overrides" or a new dedicated §"Configuration" section). Rejected — the data root is read at session start before any other section is processed, including the override-resolution step. Putting it in the top metadata block matches the order of consumption and matches where every other per-Architect identifier (`System ID:`, `Architect ID:`) already lives.

- **Permit `~`-relative or env-var-interpolated paths** (§"Open for the user" item 1's alternative). Recommendation is to reject; flagged for the user.

- **Machine-conditional `Data root:` syntax** (§"Open for the user" item 5's alternative). Recommendation is to reject; flagged for the user.

- **Prompt the user for the data root at session start when missing** (§"Open for the user" item 2 option c). Rejected on architectural-envelope grounds — cloud-hosted Architects have no interactive surface.

## Consequences

### Immediate

- **Forthcoming bootstrap kit unblocks on this field.** The role-doc template the bootstrap kit produces can render the `Data root:` slot per the form pinned above. First Architect bootstrapped via the kit will declare its data root from session 1.

- **Federation Architect's own role doc needs a structural edit.** Per §"Open for the user" item 4 (recommended), a minor bump (`0.5.0` → `0.6.0`) lands the `Data root:` field in federation-arch.md's top metadata block. This is **not** done by this ADR — acceptance criterion #4 explicitly forbids it. A follow-up session executes the bump.

- **No participating Architects exist yet to retrofit.** Federation Architect is currently the only federation-participating Architect (per the session-6 handoff). Retrofit cost is one role-doc edit, in a follow-up session.

- **No change to ADR-0009's data-root abstraction or layout.** This ADR is strictly about the declaration form. The abstraction, the override mechanism, the failure-mode behavior, the sidecar pattern — all unchanged.

### On future Architect onboarding

- The bootstrap kit's role-doc template carries the `Data root:` slot as a required field, with placeholder text indicating the absolute-path requirement. A new Architect's first session fills the slot in before its first data read.

- The "refuse to start" behavior (per §"Open for the user" item 2) means a missing or malformed `Data root:` field surfaces at session start. The bootstrap kit should make this hard to miss — e.g., the template's placeholder is obviously invalid as a path, so leaving it unmodified produces an immediate, legible failure.

### On the architectural envelope

- The form generalizes cleanly to multi-machine, cloud-hosted, and multi-user federations (§"Open for the user" item 5's per-machine-clone model). The path string is opaque; the resolution rule is uniform; the failure mode is consistent.

- No change to ADR-0009's envelope coverage. This ADR sits inside that envelope; it does not narrow or widen it.

### Risks

- **Data-root drift across role-doc clones (deferred).** When the same logical Architect runs on multiple machines, each clone's `Data root:` is correctly different by design; other fields are supposed to be identical and can drift. Not solved here per §"Open for the user" item 5. Surface for a follow-up ADR when the federation actually spans machines.

- **Misconfigured field silently breaking next session.** Mitigated by the loud-failure default (§"Open for the user" item 2): an absent or malformed field refuses to start, with a legible error. A field that points at a valid-but-wrong path is harder to detect — that is the data-root configuration drift risk ADR-0009 already flagged. The override-hygiene check from ADR-0009 catches some of it (dangling overrides are surfaced); a wholesale wrong path produces an empty profile and empty registries, which surface as the absent-profile failure mode (R7.1) — non-fatal, logged at session start.

- **CHANGELOG-grade ambiguity for `Data root:` edits.** Mitigated by §"Open for the user" item 3 (recommend patch). If the user chooses minor, the rule is equally clear; the risk is just unresolved-question, not architectural.

### Deferred

- **Multi-machine sync discipline** for the non-`Data root:` fields of a role doc (general drift problem). Out of scope; ADR-0009 already deferred. Reopen when the federation spans machines.

- **Field validation tooling.** This ADR specifies form rules (absolute path, no trailing slash, backtick-rendered); whether to enforce them mechanically (e.g., a lint check at session start) is deferred. The session-start "refuse to start on missing field" is the minimum; richer validation can be added if drift becomes a real problem.

- **Retrofit ADR for any later-found participating Architects.** Currently moot (Federation Architect is the only participant). If a participating Architect predates this ADR, its retrofit is a minor bump per §"Open for the user" item 4's reasoning. Out of scope for this ADR per the brief.

## References

- [ADR-0003: The Federation Architect is a federation participant](0003-federation-architect-is-a-participant.md) — recursive participation; the reason Federation Architect itself carries the `Data root:` field.
- [ADR-0007: GitHub as system storage; data excluded by policy and gitignore](0007-github-as-system-storage-data-excluded.md) — classification rule; the `Data root:` field is system (a configuration knob on the role doc), the path it points at is data.
- [ADR-0009: Data storage mechanics — data root, habit registry, user profile](0009-data-storage-mechanics.md) — the data-root abstraction this ADR implements the declaration slot for. Substrate-opacity argument originates here.
- [ADR-0010: Registries are canon-only; sidecar history pattern is uniform across all three](0010-registries-canon-only-sidecar-history.md) — role-doc-as-canon convention; the locus argument for putting the declaration in the role doc.
- [federation-arch.md](../federation-arch.md) — current top metadata block (lines 3–7 as of v0.5.0) is the live example the form extends. Will gain a `Data root:` field in a follow-up bump per §"Open for the user" item 4.
- [specs/user-profile-storage.md](../specs/user-profile-storage.md) — the user-profile storage contract; references `<data-root>/users/<user-id>/` as the read path, depending on the declaration this ADR pins.
- [session-handoff.md](../session-handoff.md) — session-6 entry §"Pending decisions / drafted but not yet ADR'd" records the locked direction this ADR renders.
