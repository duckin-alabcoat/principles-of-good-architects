# ADR-0030: `ROADMAP.md` is a standard deliverable; its format is federation-owned and rolls out on change

**Status:** Accepted
**Date:** 2026-06-21
**Deciders:** the operator, Federation Architect

## Context

the operator (session 43) asked for a per-system deliverable, because day-to-day detail was hiding the real status: a Markdown file showing where the system stands, what is done, what is next, and the full backlog of everything planned.

Each system already has two state surfaces, and neither answers this:
- `STATUS.md` (withheld) ([ADR-0021](0021-cross-system-status-surface.md)) — a terse YAML frontmatter signal that a status consumer in another system reads. Machine-facing; deliberately *not* a human narrative. It *is* the minutia-hiding-status problem.
- `session-handoff.md` — the per-session blow-by-blow. All minutia, by design.

A federation `ROADMAP.md` was drafted and dogfooded this session and the operator approved the format. He then set the load-bearing requirement: format and formatting changes must be able to roll out to every system via POGA whenever they are needed. So the decision isn't just "add a roadmap" — it's "add a roadmap **whose format the federation controls centrally**, so a future format change propagates to every system without hand-editing each one."

That requirement rules out the obvious-but-wrong implementation: a per-system hand-authored doc, or a kit template frozen at bootstrap time. Both drift, and neither rolls out a later format change ([P16](../principles/master.md#p16--avoid-duplication)).

## Decision

**Every system emits a `ROADMAP.md` at its repo root. Its format is standard and federation-owned; its content is per-system.** The split is the whole point:

- **Format (shape) — federation-owned, injected, roll-out-able.** The required sections (Now / Recently shipped — outcomes / Next / Backlog), the outcome-framing rule, the as-of line, and the maintenance footer are defined **once** in [`standard-source.md`](../standard-source.md) → generated into the injected [`STANDARD.md`](../STANDARD.md) ("Roadmap deliverable" section). This is exactly the [ADR-0024](0024-standard-role-doc-section-is-generated-and-injected.md) channel: change the format in `standard-source.md`, regenerate, and **every system inherits the new format by injection at its next session and reshapes its `ROADMAP.md` to match** at session-end. This is the [`single-source-and-deliver`](../habits/master.md#single-source-and-deliver) habit — the shape is single-sourced and delivered, never copied per system.
- **Content — per-system, Architect-authored.** What goes *in* each section is the system's own status/outcomes/backlog — judgment, single-writer (the Architect), not generatable. The federation never writes another system's roadmap content.

A standard **session-end step** ("Refresh `ROADMAP.md`") drives maintenance, sibling of the existing `STATUS.md` refresh — both land in the same close commit.

**The roll-out guarantee (the operator's requirement).** A format/formatting change is made in one place (`standard-source.md`), regenerated (`curate/standardize.py` → `STANDARD.md` + `bootstrap-kit/STANDARD.md`), and reaches every system through the injected standard section. No per-system edit, no per-system brief — the injected-standard channel *is* the rollout mechanism. With [ADR-0029](0029-receiving-architects-auto-adopt-at-startup.md) (auto-adopt) this is fully hands-off: systems pick up the new format and conform at their next session.

The kit ships a thin `roadmap-template.md` **seed** so a fresh repo has the file from day one — but it is explicitly *non-authoritative on format* (it points at `STANDARD.md` and carries no format rules to drift); the injected spec is the single source.

## Alternatives Considered

- **Per-system hand-authored ROADMAP, no central format.** Rejected — every copy's format drifts, and a later format change can't be rolled out ([P16](../principles/master.md#p16--avoid-duplication)); fails the operator's explicit requirement.
- **Kit template as the format source.** Rejected as the *authority* — a kit template is a bootstrap-time snapshot; editing it later does not reach already-onboarded systems. Kept only as a non-authoritative seed file; the living format is the injected `STANDARD.md` spec.
- **Generate `ROADMAP.md` itself (like `CANON.md`).** Rejected — `CANON.md` is generated because its content is deterministic (transcribed from registries). Roadmap content is per-system judgment (outcomes, plans), not generatable. Only the *format* is single-sourced; the content is authored.
- **Fold the human view into `STATUS.md`.** Rejected — `STATUS.md` is a machine contract ([ADR-0021](0021-cross-system-status-surface.md)); mixing a human narrative into it muddies the contract and the duplication. Separate files, distinct audiences.

## Consequences

- **the operator gets one outcome-framed status doc per system**, and can change its format portfolio-wide from a single federation edit — the requirement that motivated this ADR.
- **Format changes are cheap and safe to make often** — edit `standard-source.md`, regenerate, done; the change reaches every system by injection.
- **A standard session-end step is added** (`ROADMAP.md` refresh). Existing Architects inherit it via the injected `STANDARD.md` and begin emitting the file at their next session-end — no retrofit brief needed (the injected standard is the delivery).
- **Kit gains `roadmap-template.md`** (seed only) + a PROCESS.md copy row; **kit version bumps** (new template file + new standard step).
- **Federation dogfoods first**: its own `ROADMAP.md` is the reference copy and was built before standardizing.
- **One more standard artifact to keep current at session-end** — mitigated by it being a quick refresh beside `STATUS.md`, and by the format being injected (no per-system format upkeep).

## References

- [ADR-0024](0024-standard-role-doc-section-is-generated-and-injected.md) — the generated-and-injected standard-section channel that carries the ROADMAP *format* and gives it the roll-out property.
- [ADR-0021](0021-cross-system-status-surface.md) — `STATUS.md`, the machine signal `ROADMAP.md` complements.
- [ADR-0029](0029-receiving-architects-auto-adopt-at-startup.md) — auto-adopt, which makes format roll-out hands-off.
- [`single-source-and-deliver`](../habits/master.md#single-source-and-deliver) / [P16](../principles/master.md#p16--avoid-duplication) — the duplication-avoidance the format-vs-content split implements.
- Federation session 43 (2026-06-21) — the operator approved the format and required portfolio-wide roll-out of format changes via POGA.
