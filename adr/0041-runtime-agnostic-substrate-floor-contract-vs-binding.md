# ADR-0041: Runtime-agnostic substrate floor — contract vs. binding

**Status:** Accepted
**Date:** 2026-07-11
**Deciders:** the operator, Federation Architect
**Accepted:** 2026-07-11 (session 55) — the operator's explicit Accept.

## Context

[ADR-0023](0023-standard-operating-substrate.md) defined the mandatory operating-substrate floor and expressed it as a list of **Claude Code mechanisms** — the `session.py` harness, `.claude/settings.json` hooks (`SessionStart`, `PreToolUse` guards), `CLAUDE.md` auto-load, the `check-bash` / `check-question` tool-call interception, the canon channel injected by `session.py start`. §3 then made that floor non-subtractable: a system MAY add on top, MAY NOT drop any component.

Every Architect to date runs on **Claude Code**, so a mechanism and the behavior it produces were the same thing — the distinction never had to be drawn. ADR-0023 could name files and mean behaviors and no one noticed the conflation.

A federation candidate built on a **non-Claude runtime** (for example Gemini in the Antigravity IDE) breaks that. That runtime exposes none of Claude Code's primitives: no `.claude/settings.json` hook contract, no `CLAUDE.md` auto-load, no `PreToolUse` interception. A strict reading of ADR-0023 §3 says a Gemini-runtime Architect **cannot converge**: the retrofit report has no legal outcome for its substrate rows (an outside consultant's finding). And Antigravity will not be the last non-Claude runtime a federation meets, so the answer must be **general**, not a special case for one candidate.

The error is not in ADR-0023's intent — it is that the floor was **specified by its mechanism (the pipe material) instead of by the behavior it carries.** The floor we actually require is a set of behaviors; the Claude Code files are one implementation of them. This is "same pipes, different houses" ([ADR-0023](0023-standard-operating-substrate.md) §4) discovering that we defined the pipes too concretely.

## Decision

Refactor the substrate floor into a **two-layer model**: a runtime-agnostic **contract** (obligations stated as behaviors) and a per-runtime **binding** (how a given runtime satisfies each obligation). ADR-0023's mechanism list becomes the **Claude Code binding** — the reference implementation — of the contract, not the contract itself.

### 1. The contract — five floor obligations, stated as behaviors

The [ADR-0023](0023-standard-operating-substrate.md) §1 list, re-expressed as *what* every Architect must guarantee, not *how*:

- **C1 — Canon delivery.** The Accepted principles and universal habits are present in the Architect's working context at session start.
- **C2 — State continuity.** Session boundaries transfer state: a durable handoff, a monotonic session counter/stamp, and orphan detection.
- **C3 — Operational guards.** Destructive and ambiguous operations are gated **before they execute** (the `check-bash`, `check-question`, and destructive-git behaviors).
- **C4 — Auditable evolution.** The role doc is 3-part-semver-versioned and changelogged; incoming edits arrive through a receipt / auto-adopt channel; structural decisions are ADR-recorded.
- **C5 — Identity & provenance.** 4-slot identity ([ADR-0006](0006-naming-convention-corrected.md)), portfolio registration, data/system separation ([P3](../principles/master.md#p3--data-system-separation)).

### 2. The binding — per runtime, declared, satisfy-or-waive

Each system ships a **binding manifest** that, for every contract obligation, records exactly one disposition:

- **satisfied** — by the reference Claude Code mechanism (the default; the ADR-0023 substrate);
- **satisfied-differently** — by a native mechanism of the runtime, named with the file/config that provides it;
- **waived-with-compensation** — the runtime structurally cannot satisfy it; a named **compensating control** covers the gap, and the waiver is recorded.

A binding manifest with an **unrecorded** gap on any obligation is non-conformant. The Claude Code binding is the reference: every obligation *satisfied*, zero waivers.

### 3. No-subtraction is preserved — at the obligation layer, not the mechanism layer

[ADR-0023](0023-standard-operating-substrate.md) §3's "may not subtract" now binds the **contract**. A system may not drop a floor **obligation**. It MAY satisfy an obligation with a different mechanism, and it MAY waive a specific **mechanism** it cannot run — but only by naming a compensating control and recording the waiver. A silent hole is a subtraction; a recorded waiver-with-compensation is not. Additions-allowed ([ADR-0023](0023-standard-operating-substrate.md) §3, first bullet) is unchanged.

This is why the reframe is stronger than exempting non-Claude systems from the floor wholesale (the rejected alternative below): every obligation stays **live** for every runtime, and only the specific mechanisms a runtime genuinely cannot run are waived — each with a compensating control on the record.

### 4. Enforce where the runtime allows; honor-system only where it doesn't

Prefer a binding that **enforces** an obligation structurally over one that relies on the agent's goodwill. Runtime-agnostic enforcement points — **git hooks, which fire regardless of which agent invokes git**, and CI gates — satisfy an obligation for *any* runtime and are preferred over a prompt-level instruction. A `waived-with-compensation` disposition on an *enforceable* obligation is justified only when no structural enforcement point exists on that runtime; where one does (e.g. a `pre-push` hook for destructive-git, a `pre-commit` hook for the guard behaviors), the binding must use it rather than waive.

### 5. Federation delivery tooling becomes binding-aware

`push-substrate.py`, `gen_settings.py`, and the pushed `session.py` are the **Claude binding's** delivery mechanism. They MUST **skip — never clobber** — a target whose binding is non-Claude, the same presence-gate reflex that the [ADR-0034](0034-settings-are-federation-pushed-generated-substrate.md) `settings_path` gate applies to a system's runtime settings. The **management** surface — `reconcile.py`, `repo-paths.local` ([ADR-0037](0037-repo-path-locator-map-unlocated-is-not-unonboarded.md)), `portfolio.md`, `STATUS.md` — is already runtime-agnostic (it reads declared state) and covers every binding unchanged.

### 6. The dual-runtime binding (govern in Claude Code, build native)

A system MAY satisfy the Claude-specific obligations by running its **governance** sessions in Claude Code over the same repo while **building** in the native runtime (the consultant's dual-runtime model). Under this binding the Claude substrate is present-but-dormant, active in governance sessions where C1/C2/C4 are satisfied by the reference mechanism. C3 for the native building sessions is then either **enforced by a runtime-agnostic control** (git hooks, per §4) or **waived-with-compensation**. This is **one binding under the contract**, not a separate doctrine — which is how it differs from scoping the floor per-runtime absolutely (rejected below).

### 7. A non-Claude binding (worked example)

The dispositions below are a worked example for a candidate repo built in a runtime without a hook contract (the Antigravity IDE is one such runtime); the shape applies to any runtime of that kind. **Headline finding: the runtime exposes no native enforcement surface at all**: no runtime config directory with a hook or rule contract, no `PreToolUse` analog, no command allow/deny. Every obligation exists only as prose doctrine (a prompt doc and the repo's ADRs) that the agent is *asked* to self-honor. The only reusable *enforced* control is one the retrofit **introduces**: **git hooks**, which fire regardless of which agent invokes git.

- **C1 Canon delivery** — `satisfied-differently`, weakly: the canon is embedded in the repo's prompt doc, but delivery is convention-only (no auto-load guarantees a session reads it). The federation refreshes the embedded canon on push; **guaranteed** per-session delivery is met in governance sessions via the reference injection (§6).
- **C2 State continuity** — split. A native orientation script plus prose handoffs can give orientation but **no session stamp, counter, or orphan detection**. Stamped continuity is `satisfied` in governance sessions by the reference `session.py` handoff; the native building side keeps its own orientation script as its ritual.
- **C3 Operational guards** — the load-bearing split, per rule 4:
  - *destructive-git subset* → `satisfied-differently`: a **`pre-push` (and `pre-commit`) git hook the retrofit installs**, enforcing the destructive-git guard agent-agnostically. This is a real enforcement point, not honor-system.
  - *arbitrary-bash interception (`check-bash`) and the picker guard (`check-question`)* → `waived-with-compensation`: these gate **Claude Code tool calls** and have **no git-hook or native Antigravity analog** (a git hook fires at commit/push, not before an arbitrary shell command or a UI picker). Compensation: (a) they are largely moot on the Gemini tool surface — `check-question` guards Claude's `AskUserQuestion`, which Gemini has no equivalent of, and `check-bash`'s compound-chain rule is a Claude-bash-tool convention; (b) Architect-substrate work runs in Claude Code (§6) where both guards fire natively.
- **C4 Auditable evolution** — often `satisfied`-in-fact (ADRs, a changelog, 3-part SemVer, release tags) but **unenforced, so it can drift** (commits pile up past the last tag with no bump). Harden to `satisfied-differently`-enforced with a **`pre-commit` hook** checking the version-file / `CHANGELOG.md` bump — the same installed-git-hook vehicle as C3.
- **C5 Identity & provenance** — the [ADR-0006](0006-naming-convention-corrected.md) function-based Architect ID and `repo-paths.local` registration, set at onboarding. Self-ID and a per-message version stamp can already be present by doctrine.

**Net:** nothing native is reusable; the reusable enforcement is a small set of **federation/retrofit-installed git hooks** (agent-agnostic) covering the git-boundary of C3 and the C4 bump-check, plus **dual-runtime governance** (§6) for everything that only a Claude-Code tool layer can gate.

## Alternatives Considered

- **Full conversion to Claude Code.** Cleanest doctrinally; defeats the project's purpose (building natively on a second runtime). Rejected.
- **Content-only federation, no substrate** — adopt canon, skip the floor entirely. Rejected: skipping the floor **is** a subtraction, and it discards obligations such a candidate may already meet (semver, changelog, ADRs, a session ritual). Strictly worse than a declared binding with waivers.
- **Scope the no-subtraction floor per-runtime absolutely** ("the floor applies only to Claude systems"). Rejected as too blunt: it exempts a non-Claude system from obligations it *can* satisfy, collapsing the floor to nothing for that runtime. Contract/binding keeps every obligation live and waives only the specific mechanisms a runtime cannot run — a smaller, honest exemption with a compensating control attached.
- **Keep ADR-0023 as-is; treat a non-Claude candidate as un-onboardable.** Rejected — forecloses the entire non-Claude portfolio direction over a specification artifact, not a real incompatibility.
- **Teach `session.py` to run on the foreign runtime (a portable harness).** Rejected for the same reason ADR-0023 §2 rejected a pluggable multi-model harness — it re-grows per-runtime complexity in the shared tool. The binding lives with the *system*, not in the federation's harness.

## Consequences

- **[ADR-0023](0023-standard-operating-substrate.md) is re-framed, not superseded.** §1's mechanism list is retro-named the *Claude Code binding* (the reference); §3's no-subtraction re-anchors to the **obligation** layer. Per the [ADR immutability convention](README.md), ADR-0023's file is left untouched; the linkage is carried by this ADR and annotated on ADR-0023's index row ("re-framed by ADR-0041").
- **[ADR-0034](0034-settings-are-federation-pushed-generated-substrate.md)'s settings-push is explicitly a Claude-binding detail,** not a floor obligation — consistent with §5's skip-non-Claude rule.
- **New artifact: the binding manifest**, one per system. Location TBD in the build — likely a declared block in `session.config.json` or a sibling `binding.md`; the reference (Claude) binding is implicit/empty (all-satisfied) so existing systems need no change.
- **The retrofit report gets a legal outcome for every substrate row** — the gap the consultant named. Each row resolves to satisfied / satisfied-differently / waived-with-compensation.
- **`reconcile.py` / status surface** should learn to read a binding manifest and report a system's binding + any open (uncompensated) waivers, so a non-conformant binding is visible like any other drift. Downstream, not built here.
- **Downstream, gated on Accept:** (1) define the binding-manifest format; (2) make `push-substrate.py` binding-aware (skip non-Claude targets); (3) run the first non-Claude retrofit as the first exercise of the contract, completing §7. None built here — this ADR fixes the *decision*.

## References

- [ADR-0023](0023-standard-operating-substrate.md) — the standard operating substrate; this ADR re-frames its floor from mechanism to contract.
- [ADR-0034](0034-settings-are-federation-pushed-generated-substrate.md) — settings-push as generated substrate; the presence-gate reflex §5 generalizes.
- [ADR-0014](0014-existing-architect-retrofit.md) — the retrofit path a non-Claude onboarding runs through.
- [ADR-0006](0006-naming-convention-corrected.md) — function-based naming.
- [ADR-0037](0037-repo-path-locator-map-unlocated-is-not-unonboarded.md) — `repo-paths.local` locator; registers the candidate on the status surface.
- [P4 `identity-boundaries-non-collapsing`](../principles/master.md#p4--identity-boundaries-non-collapsing) — the dual-runtime governance model keeps persona and Architect identities separate across runtimes.
