# Dual-runtime governance — converging a non-Claude system

The install reference for onboarding a system built on a **non-Claude runtime**
(for example, Gemini/Antigravity). It realizes the
[ADR-0041](../adr/0041-runtime-agnostic-substrate-floor-contract-vs-binding.md)
binding: the runtime keeps building; the federation **governs in Claude Code**;
and the obligations a git hook can enforce are enforced, not honour-systemed.

This is a Phase-4 (substrate-convergence) checklist, run in a Claude Code
governance session over the target repo after its retrofit report is resolved.
Nothing here requires the native runtime to take any action.

> **Two shapes.** This guide describes the **asymmetric** dual-runtime binding
> (ADR-0041 §6): the native runtime *builds*, Claude Code *governs*, and obligations
> may lean on the governance session. If instead the operator moves **back and forth
> between models with no impact** — every session, in *either* runtime, both builds
> and governs — use the **symmetric multi-runtime** binding: declare
> `"runtimes": [...]` (co-equal), author `binding.md` from the **matrix** form in
> [`binding-template.md`](binding-template.md) (one disposition per obligation × runtime,
> every obligation met in every runtime), and hang C1/C2 on `session.py` invoked from
> each runtime's native session-start ritual so they hold symmetrically. Parts 2–3
> below (git hooks, per-clone install) apply to both shapes; part 1's `runtime` field
> becomes the `runtimes` array.

## The three parts

1. **Governance substrate (the Claude binding, present-but-dormant).**
   Install the standard substrate at the repo root: `session.py`, a
   `session.config.json`, `CANON.md`, `STANDARD.md`, and a governance
   `.claude/settings.json`. Antigravity/Gemini ignore `.claude/` entirely, so
   these are inert during building and active whenever Claude Code opens the repo
   — that governance session satisfies C1 (canon injection), C2 (handoff / stamp
   / orphan), and C4's receipt/auto-adopt.
   - **Set `"runtime": "<runtime-id>"` in `session.config.json`** (e.g.
     `gemini-antigravity`). This is the ADR-0041 §5 signal: `push-substrate`
     skips a non-Claude target from the fleet push (its governance substrate is
     serviced here, at retrofit, not by the blanket push — which would also fail
     on a repo with no git remote).

2. **The binding manifest (`binding.md` at repo root).**
   Author it from [`binding-template.md`](binding-template.md); it records, per
   obligation C1–C5, `satisfied` / `satisfied-differently` /
   `waived-with-compensation`. Validate before committing:
   `python3 curate/check-binding.py <repo>/binding.md`. A waiver with no
   compensating control fails the check (no silent subtraction — ADR-0041 §3).

3. **Enforced git hooks (agent-agnostic, this folder).**
   Install [`git-hooks/pre-push`](git-hooks/) and
   [`git-hooks/pre-commit`](git-hooks/) into the target's `.git/hooks/`:

   ```sh
   cp bootstrap-kit/git-hooks/pre-push   <repo>/.git/hooks/pre-push
   cp bootstrap-kit/git-hooks/pre-commit <repo>/.git/hooks/pre-commit
   chmod +x <repo>/.git/hooks/pre-push <repo>/.git/hooks/pre-commit
   ```

   `pre-push` blocks force-push / history-rewrite / branch-deletion — the C3
   destructive-git guard, firing regardless of which agent runs `git` (this is
   the enforcement point a non-Claude runtime otherwise lacks). `pre-commit` is a
   non-blocking version/changelog-drift reminder (C4). `.git/hooks/` is not
   tracked, so this is a per-clone install step — note it in the target's
   onboarding record. (A future refinement is `core.hooksPath` pointed at a
   tracked, version-controlled hooks dir so a fresh clone is covered automatically.)

## What stays waived

`check-bash` (arbitrary/compound-bash interception) and `check-question` (the
multiple-choice picker) gate **Claude Code tool calls** and have no git-hook or
native-Antigravity analog — a git hook fires at commit/push, not before an
arbitrary shell command or a UI picker. These are `waived-with-compensation` in
`binding.md`: they are largely moot on the Gemini tool surface, and Architect-
substrate work runs in the Claude Code governance session where both fire natively.
