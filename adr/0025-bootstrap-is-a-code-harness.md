# ADR-0025: Fresh-Architect bootstrap is a deterministic code harness

**Status:** Accepted
**Date:** 2026-06-18
**Deciders:** the operator, Federation Architect

## Context

Bootstrapping a new Architect (PROCESS.md fresh-Architect path) has been **agent-driven by default** since v1.1.2 ([ADR-0017](0017-bootstrapping-is-a-federation-responsibility.md) made Bootstrap a mission responsibility; PROCESS.md §"How to run this" told the Architect to gather inputs and hand-do the file copies + token substitution). Session 38 ran that path live to stand up a new member's Architect, and it produced a string of mechanical errors that had nothing to do with judgment:

- **Wrong clone location.** The repo was cloned into a transient local path instead of the established projects-directory convention that every earlier bootstrapped Architect followed. Had to be relocated after the fact.
- **Absolute inbox path.** `session.config.json`'s `inbox` was written as an absolute path rather than the repo-root-relative form the harness expects, breaking machine portability. (The federation's own config uses a relative path — the convention existed; it just wasn't followed.)
- **Path-view confusion.** The same data can sit at different absolute paths on different machines; which view to declare had to be reasoned out by hand and got the declared `Data root:` wrong on the first pass.
- **Session-counter / orphan confusion** in the new Architect's first real session.

None of these required a language model. They are file copies, token substitution, convention-following, and path resolution — pure mechanism. This is the same problem [ADR-0020](0020-session-rituals-are-a-code-harness.md) solved for the *session rituals* (which became `session.py`) and [ADR-0019](0019-curate-gather-and-staging-boundary.md) solved for the *curate gather*: the deterministic half belongs in code, the LLM keeps only the judgment. It is mandated by the canon principle **P15 `code-for-mechanism-not-judgment`** and the `add-structural-guard-on-recurrence` habit. Bootstrap was the last conspicuous federation operation still executed by hand.

"Agent-driven" was conflated with "the LLM hand-performs each step." The correct reading — consistent with the no-toil intent — is that the agent *invokes deterministic tooling* and supplies only the content that needs a brain.

## Decision

**The mechanical half of fresh-Architect bootstrap moves into a deterministic, federation-only script: `bootstrap.py` at the federation repo root.** It is the sibling of `session.py` (shared harness), `curate/distill.py`, and `curate/standardize.py` (federation-only generators).

**Split of responsibility (P15):**

- **Code does the mechanics, deterministically.** Given a spec, `bootstrap.py`:
  1. **Derives** the 4-slot identity from the system name per [ADR-0006](0006-naming-convention-corrected.md) — System ID (kebab), Architect ID (`<system-id>-arch`), canonical Architect name (`<Function> Architect`), session-title prefix.
  2. **Detects** machine (`scutil`/`hostname`), timezone, today's date/time, and the GitHub account (`gh auth status`) — never asks for what the system can report.
  3. `gh repo create <owner>/<repo>` (private), then **clones into the convention path** `<projects_dir>/<FriendlyName>`.
  4. Copies the kit files (per the PROCESS.md §4 table) with the correct renames, copies `session.py` verbatim from the federation root, and copies the generated `CANON.md` + `STANDARD.md` verbatim.
  5. **Substitutes all `<<TOKENS>>`** across the copied files and **strips the template guidance blocks**.
  6. Writes the `inbox` path **relative** in `session.config.json`; declares `Data root:` as the canonical machine's view of the path.
  7. Makes the receipt-ritual inbox dirs and seeds the user profile under the data root.
  8. Commits + pushes the new repo's initial commit.
  9. Edits `portfolio.md` to register the Architect (leaves it staged for the Federation Architect to commit at session-end — the script does **not** commit the federation repo, to avoid racing concurrent federation sessions).

- **The LLM supplies only judgment, in a spec file.** The genuine-judgment inputs — mission/scope/voice prose, system-specific principles and session steps, orchestrator-agent presence, the friendly folder name, and the intake answers — are written into a `bootstrap spec` (JSON) that the Architect authors from the intake conversation. The Architect then runs the script and **reviews the generated repo**.

**PROCESS.md flips** from "agent-driven by default" (hand-do the steps) to "code-driven: the Architect authors the spec and runs `bootstrap.py`; the mechanical steps are the script's, not the operator's." The step-by-step runbook is retained as the script's documented behavior and the manual fallback.

`bootstrap.py` is **federation-only** — like `distill.py`/`standardize.py`, it is **not** copied into a fresh Architect's repo and is not in the PROCESS.md §4 copy list.

## Alternatives Considered

- **Keep it agent-driven, add a checklist.** Rejected — session 38 *had* PROCESS.md's checklist and still erred on every mechanical axis. Checklists don't make an LLM deterministic; code does. This is the exact lesson of ADR-0020.
- **A single mega-harness combining session + bootstrap.** Rejected — different lifecycles and triggers (bootstrap is a one-time federation action; `session.py` runs every session in every repo, and ships to every Architect). Keeping `bootstrap.py` federation-only mirrors the established `session.py`-shared / `distill.py`-federation-only split.
- **Make the spec a flag-soup CLI instead of a file.** Rejected — mission/scope/voice are multi-paragraph prose; a spec file is the honest carrier and leaves an auditable record of what was fed in.
- **Have the script also commit the portfolio registration.** Rejected — the federation repo is shared and often has a concurrent session open; committing it from the script would race. The script stages the edit; the Federation Architect commits it within its own session boundary ([P13](../principles/master.md#p13--single-writer-per-state)).

## Consequences

- Bootstrap becomes reproducible: same spec → same repo, every time. The session-38 error classes (wrong path, absolute inbox, path-view confusion) become structurally impossible — they are encoded once, correctly.
- PROCESS.md §"Fresh-Architect onboarding steps" must be rewritten to the code-driven framing (the manual runbook survives as fallback + as the script's spec). **No kit version bump** — `bootstrap.py` changes how the kit is *applied*, not any template, and the kit's versioning rule is templates-only; `bootstrap.py` reads the kit version from the kit README rather than hardcoding it, so it can't drift. The kit README's "How PROCESS.md drives the kit" section gains the `bootstrap.py` driver note.
- A new artifact (`bootstrap.py`) joins the federation-only tool set; the federation-arch.md §7 artifacts table gains a row and §2 Bootstrap responsibility should note the harness. (Follow-up role-doc bump, same pattern as ADR-0020 → v1.4.0.)
- The script's correctness is now load-bearing for onboarding. It should be exercised against the next real bootstrap and (ideally) a `--dry-run` mode so the Architect can preview before any repo is created.
- Does **not** change *what* a fresh Architect receives (the kit contents, the inherited canon, the standard section) — only *how reliably* it gets assembled. The judgment inputs and the operator's gates are unchanged.

## References

- [ADR-0017](0017-bootstrapping-is-a-federation-responsibility.md) — Bootstrap as a mission responsibility (the actor, not just the toolmaker).
- [ADR-0020](0020-session-rituals-are-a-code-harness.md) — the direct precedent: session rituals → `session.py`.
- [ADR-0019](0019-curate-gather-and-staging-boundary.md) — code-does-mechanism / human-keeps-judgment for the curate gather.
- [ADR-0006](0006-naming-convention-corrected.md) — the 4-slot naming convention the script derives from.
- [ADR-0011](0011-data-root-config.md) — `Data root:` declaration and the per-machine-clone path model.
- P15 `code-for-mechanism-not-judgment`, `add-structural-guard-on-recurrence` (registries) — the governing principle/habit.
- Session 38 — the live bootstrap whose mechanical failures triggered this ADR.
