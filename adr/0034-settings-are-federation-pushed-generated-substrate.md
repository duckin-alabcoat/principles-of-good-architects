# ADR-0034: `.claude/settings.json` is federation-pushed generated substrate (floor + per-system extras)

**Status:** Accepted
**Date:** 2026-07-05
**Deciders:** the operator, Federation Architect

## Context

Every Architect's `.claude/settings.json` carries a large, near-identical shared baseline: the permission allow/deny lists, the two `PreToolUse` structural guards (`check-bash`, `check-question`), and the `startup` `SessionStart` hook. This is exactly the "standard operating substrate" [ADR-0023](0023-standard-operating-substrate.md) names as a mandatory floor — the same plumbing every Architect runs on.

But unlike the other three substrate files — `CANON.md`, `STANDARD.md`, `session.py`, which are generated once and **pushed** to every converged repo ([ADR-0031](0031-delivery-integrity-self-contained-briefs.md)) — `settings.json` was never wired into that channel. It shipped **once** at bootstrap (the kit's `claude-settings-template.json`) and was hand-updated forever after. `curate/push-substrate.py` pushed `CANON.md` / `STANDARD.md` / `session.py`, and *not* `settings.json`.

The cost surfaced in session 46. A one-line permission fix (allow `git -C`, so out-of-tree git stops prompting) was live in the federation in seconds, but reaching the other systems meant editing every one of their files, or writing a brief for each. The shared thing was not shared machinery.

The obstacle to just pushing it: `settings.json` is not byte-identical across repos the way `session.py` is. A system may legitimately **add** to the floor — one member's Architect carries its own extra `PreToolUse` guard hook; the federation itself runs two extra `SessionStart` status probes (`gather --status`, `reconcile --status`) that reference federation-only scripts. A naive "push the federation's settings.json verbatim" would **clobber** those local additions. (The session-46 dry-run caught exactly this: an unguarded overwrite would have deleted that member's guard hook.)

## Decision

**`.claude/settings.json` is federation-pushed substrate, GENERATED per-target from a shared floor plus that target's own declared extras.**

- **The floor** is [`standard-settings.json`](../standard-settings.json) at the federation root — the single source of the shared permission/hook baseline (this is the same authoring model as `standard-source.md` → `STANDARD.md`). It is federation-owned; a system never edits it.
- **Per-system extras** live in a `settings_extras` block in that system's own [`session.config.json`](../session.config.json) — `allow` / `deny` lists that extend the floor's, and a `hooks` map (keyed by event: `SessionStart`, `PreToolUse`, …) whose entries are appended after the floor's hooks for that event. A system **adds, never subtracts** ([ADR-0023](0023-standard-operating-substrate.md)'s floor rule). A freshly-bootstrapped Architect has no `settings_extras` and gets the pure floor.
- **The generator**, [`curate/gen_settings.py`](../curate/gen_settings.py), composes floor + extras into the target's `.claude/settings.json` — a **generated, do-not-hand-edit** file carrying a `//generated` banner. `--check` fails if a copy is stale. `curate/push-substrate.py` calls the generator per-target on push.

**Why this dissolves the clobber risk instead of managing it.** The generator rebuilds `settings.json` from **known, declared inputs** (the floor + the target's `settings_extras`). It never has to read the file it overwrites and guess which lines are floor vs. local — so it cannot mis-preserve. A system's additions survive a push because they live in `settings_extras` (tracked in that system's own git), not because the push tried to detect and keep them. Genuinely per-machine values still go in the gitignored `settings.local.json`, which the push never touches.

**Boundary (refines [ADR-0031](0031-delivery-integrity-self-contained-briefs.md) / [ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md)).** `settings.json` is now federation-owned generated substrate — the federation may write+commit it into any converged repo, like the other three files. The federation still **never writes a target's authored files**, and `session.config.json` (which now hosts `settings_extras`) **is** such a file: the federation *reads* it to render, never writes it. So a system's local additions are declared **by that system** (or via a brief it applies), never by the federation reaching in. `settings.json` is also **refresh-only** in the push — refreshed where present, never *introduced* — so a repo with a non-standard layout (for example, one that keeps its settings in a subdirectory, not the repo root) is not handed a root settings file it didn't have.

## Alternatives Considered

- **Push the federation's own `settings.json` byte-identical (like `session.py`).** Simplest, but wrong: it clobbers every system's local additions (that member's guard hook) and carries the federation's own extra hooks into systems that shouldn't have them. Rejected — `settings.json` is genuinely per-system, not identical.
- **Merge the floor into each target's existing file (preserve the non-floor lines).** Requires the push to parse each settings.json and classify floor vs. local — fragile, and "how do you tell a floor line from a local one" is itself unsolved. The generate-from-declared-inputs model deletes this problem. Rejected.
- **Put the shared floor in the user-level `~/.claude/settings.json` (one file per machine, native layering).** The only way to have a literally single file, but it lives outside version control, is per-machine (one copy on every machine), and isn't federation-pushable. The generated-and-committed per-repo model keeps everything tracked ([ADR-0018](0018-adopted-principles-and-habits-are-system.md)) and owned in one place. Rejected as the primary channel; `settings.local.json` remains the per-machine escape hatch.
- **Keep hand-updating / briefing each system per change.** The status quo that made a one-line `git -C` fix a per-system chore. Rejected — it is the exact "shared thing isn't shared machinery" gap this ADR closes.

## Consequences

- **A permission or shared-hook change is now one edit + one push.** Edit `standard-settings.json`, run `push-substrate.py`; every converged repo with a standard settings file gets it. The session-46 `git -C` fix ships this way.
- **`settings.json` becomes a generated file everywhere.** The federation's own was regenerated (dogfood; behavior-identical, minus 4 redundant `branch -d` allow lines the general `branch` allow already covers). The kit's `claude-settings-template.json` is regenerated as the pure floor, so fresh Architects bootstrap onto it.
- **Migration is required for a system with pre-existing local additions.** Because the federation can't write a target's `session.config.json`, a system that already hand-added settings (such as that guard hook) needs those declared in its own `settings_extras` **before** its first generated push — otherwise the push would drop them. Until migrated, that repo is excluded from the settings push (its `session.py` still refreshes). This is delivered as a normal brief the target applies ([ADR-0013](0013-receipt-ritual.md)).
- **The floor made two deliberate normalizations:** it adds `git -C` (session 46), and it drops the plain `branch -d` / `--delete` **deny** entries (a non-destructive op — git refuses to delete unmerged branches — already covered by the general `branch` allow; force-delete `-D` / `--delete --force` stays denied, backed by the `check-bash` destructive-git guard).
- **`standard-settings.json` and `gen_settings.py` are federation-only** (like `standard-source.md` / `standardize.py` / `push-substrate.py`) — the floor source and generator live in the federation; targets receive only the generated output.
- **Pairs with the session-46 `check-bash` destructive-git guard.** The floor blanket-allows `git -C`; that is safe only because the guard hard-denies destructive git regardless of the `-C` prefix. The floor and the guard ship together.

## References

- [ADR-0023](0023-standard-operating-substrate.md) — the standard operating substrate as a mandatory floor + additive local; `settings.json`/hook wiring named there.
- [ADR-0031](0031-delivery-integrity-self-contained-briefs.md) — generated substrate is auto-pushed, single-writer; this ADR adds `settings.json` (generated per-target) to that set.
- [ADR-0024](0024-standard-role-doc-section-is-generated-and-injected.md) — the `standard-source.md` → `STANDARD.md` generate-from-one-source model this mirrors.
- [ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md) — the never-write-authored-files boundary; `session.config.json` (hosting `settings_extras`) is read, never written.
- [ADR-0018](0018-adopted-principles-and-habits-are-system.md) — why the generated `settings.json` and the floor are tracked in git.
- Federation session 46 (2026-07-05) — the `git -C` fix that exposed the shared-thing-not-shared-machinery gap.
