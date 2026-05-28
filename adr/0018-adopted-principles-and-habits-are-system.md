# ADR-0018: Adopted principles and habits are system, not data

**Status:** Accepted
**Date:** 2026-05-28
**Deciders:** the user, Federation Architect

## Context

[ADR-0007](0007-github-as-system-storage-data-excluded.md) classified the registries as **data** and excluded them from git: `.gitignore` lists `principles/`, `habits/`, `inputs/`, `users/`, and `architect-learnings.md`. The stated rationale was that registry content is "synthesized from other systems' producer files and role docs" — i.e., derived from data, therefore data.

That rationale conflates two different things:

- **Raw inputs** (`inputs/` — mirrored copies of other Architects' producer files and role docs) and **producer observations** (`architect-learnings.md`) genuinely *are* data: external or pre-distillation material.
- **The registries** (`principles/master.md`, `habits/master.md`) are the *output* of distillation — the federation's canonical statement of what a good Architect is. Once an entry is **adopted** (status `Accepted`), it is not synthesized data sitting in a cache; it is the system's substance. It is the thing every participating Architect's role doc is edited *from*. The name of the whole project is *Principles of Good Architects* — and the principles were excluded from the repo.

The user, 2026-05-28: *"I consider the principles and habits once adopted to be part of the system, not part of the data. They need to make it into git."* This surfaced after it was noted that the public version — and the private repo's own git history — contained the role doc's adoption *tables* but never the actual principle/habit *text*.

Consequences of the current (data) classification:
- The canonical principles and habits have **no version history in git** — their evolution is invisible to `git log`, defeating [P2](../principles/master.md#p2--system-artifacts-evolve-auditably) (system-artifacts-evolve-auditably) for the federation's most important artifacts.
- A fresh clone of the system repo on a new machine has the role doc but **not the principles it references** — the registry links (`principles/master.md#...`) dangle.
- The sidecar history files ([ADR-0010](0010-registries-canon-only-sidecar-history.md)) — explicitly built as the audit trail for registry changes — are themselves untracked, so the audit trail isn't backed up or shared.

Current registry state at decision time: 13 principles, 21 habits, **all `Accepted`** — no `Proposed` or `Deprecated` entries.

## Decision

**The principles and habits registries are system artifacts and are tracked in git.** Reclassify `principles/` and `habits/` (each containing `master.md` and `master-history.md`) from data to system. Remove both directories from `.gitignore`; commit them to the system repo.

**Whole-file granularity, not per-entry.** The registry *files* are system, regardless of any individual entry's status. The user's "once adopted" describes the nature of the registries' purpose (they hold adopted canon) rather than mandating per-entry git-gating. A `Proposed` entry riding in a committed `master.md` is acceptable and even desirable — it is a not-yet-ratified *system proposal*, not secret data, and its presence in git history is audit signal, not leakage. (Were a large body of speculative drafts ever to accumulate, a separate gitignored staging file could be revisited; not needed now — zero `Proposed` entries exist.)

**What stays data** (remains gitignored, unchanged):
- `inputs/` — mirrored copies of other Architects' role docs and producer files. Genuinely external; not federation canon.
- `architect-learnings.md` — the federation's own producer file. Pre-distillation observations; the raw material *for* the registries, not the canon itself.
- `users/` — the bound user's preference profile and its history. User-preference data, not principle/habit canon. (The user's directive named principles and habits specifically. If adopted *preferences* should also be tracked, that is a separate, later decision.)

**Amends:**
- [ADR-0007](0007-github-as-system-storage-data-excluded.md) — its classification rule stands, but the registries move from the data side of the line to the system side. The `.gitignore` block loses `principles/` and `habits/`.
- [ADR-0010](0010-registries-canon-only-sidecar-history.md) — its "gitignored as data" notes on the registries and sidecars are superseded by this ADR.

The role doc §7 artifacts table (which currently marks the registries "Gitignored as data per ADR-0007") is updated in the same change.

## Alternatives Considered

- **Leave the classification as-is (registries stay data/gitignored).** Rejected — it is the status quo the user just overruled, and it defeats P2 for the federation's most important artifacts (no git history for the principles themselves).
- **Per-entry granularity: only `Accepted` entries are system; `Proposed` entries live in a gitignored staging area until promotion.** Rejected for now — adds machinery (a staging file, a promotion-moves-content step) to solve a problem that doesn't exist (zero `Proposed` entries). Whole-file is simpler and `Proposed`-in-git is harmless. Revisitable if speculative drafts ever accumulate.
- **Also reclassify `users/` and `architect-learnings.md` as system.** Rejected — out of scope of the user's directive (principles and habits), and both are genuinely different in kind: `users/` is preference data, `architect-learnings.md` is pre-distillation producer observation. Keeping them data preserves the clean "inputs and observations are data; distilled canon is system" line.

## Consequences

- **Private repo gains 4 files** (`principles/master.md`, `principles/master-history.md`, `habits/master.md`, `habits/master-history.md`) and their full future history. The private repo already retains incidental detail by design ([§13 open-question](../federation-arch.md#13-open-questions)), so the provenance citations in the registries introduce no new exposure category there.
- **P2 satisfied for the registries** — their evolution becomes visible in `git log`, and the sidecar audit trail is finally backed up and shared across machines.
- **Public version carries sanitized registries.** The registries are dense in their provenance (system names, Architect names, direct user quotes). To keep this public version faithful ("everything except data is the real framework"), the registries are present here, sanitized with the established generic mapping, and join the per-sync scrub going forward.
- **No effect on other Architects.** This is the federation's own storage classification; nothing ships into any participating Architect's repo.
- **Dangling registry links resolve** — a fresh clone now has the principle/habit text the role doc links to.

## References

- [ADR-0007: GitHub as system storage; data excluded](0007-github-as-system-storage-data-excluded.md) — the classification this ADR amends.
- [ADR-0010: Registries are canon-only; sidecar history uniform](0010-registries-canon-only-sidecar-history.md) — registry/sidecar structure; its "gitignored as data" notes are superseded here.
- [ADR-0008: Three-bucket taxonomy](0008-three-bucket-taxonomy.md) — principle/habit/preference distinctions.
- [federation-arch.md §7](../federation-arch.md) — artifacts table updated by this ADR.
- Source conversation: Federation Architect session 15 (2026-05-28).
</content>
