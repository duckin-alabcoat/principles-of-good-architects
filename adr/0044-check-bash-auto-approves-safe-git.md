# ADR-0044: check-bash auto-approves the safe git surface (the `git -C` prompt fix)

**Status:** Accepted
**Date:** 2026-07-13
**Deciders:** Federation Architect, the operator (session 60)

## Context

the operator reported that `git -C` operations were still raising approval prompts, and ruled that they are acceptable and must stop asking for permission.

`git -C <path> <verb>` is how the federation operates on other repos it can reach from the machine it runs on — the fleet substrate push, upstream reconcile reads, delivering briefs into target inboxes, inspecting sibling repos. Those commands were prompting on every invocation, session after session.

The floor `.claude/settings.json` already carried `Bash(git -C:*)` and `Bash(git -C *)` allow entries (added session 46, [ADR-0034](0034-settings-are-federation-pushed-generated-substrate.md)), yet the prompts kept coming. The tell was in the gitignored `settings.local.json`: **~30 accumulated exact-command `git -C …` allows**, one per distinct command the operator had click-approved across sessions — the unmistakable signature of an allow-string that never matches.

Confirmed against the Claude Code permission docs (session 60): **CC's Bash permission matcher cannot match a `git -C <path>` command against any allow-string.** The matcher parses the git command, and the `-C /path` global option defeats the prefix match — neither `Bash(git -C:*)` nor `Bash(git status:*)` fires for `git -C /path status`. There is *no* settings-string form that reliably allows arbitrary-path, arbitrary-safe-subcommand `git -C`. The session-46 string-fix was structurally incapable of working; the accumulation of per-session exact allows is exactly the recurrence signal for [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence) ([P15](../principles/master.md#p15--code-for-mechanism-not-judgment)): discipline (and a dead config string) isn't enough — move the enforcement into code.

The federation already owns the right piece of code: `check-bash`, the `PreToolUse(Bash)` hook, which parses git `-C`-aware and *denies* destructive git regardless of the `-C` prefix ([ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md)-era guard, confirm-destructive-ops / [P9](../principles/master.md#p9--destructive-ops-confirmed)). A `PreToolUse` hook can also **auto-approve** a command by emitting `hookSpecificOutput.permissionDecision: "allow"`, which skips the interactive prompt.

## Decision

**`check-bash` becomes the single arbiter of the safe-git fast-path.** After its compound and destructive-git checks clear, it auto-approves (`permissionDecision: "allow"`) any command whose every top-level segment is a git invocation with a subcommand on an explicit safe-list (`_AUTO_ALLOW_GIT_SUB` in `session.py`). Everything else emits nothing and falls through to the normal permission flow.

Order is load-bearing: **deny checks run first, allow second, prompt (no output) last.**

1. compound → `deny`
2. destructive git (`-C`-aware) → `deny`
3. safe git surface → `allow`
4. anything else → no output → normal prompt

The safe-list **mirrors the floor's already-blessed plain-git allow surface** (read-only/inspection verbs + forward-only mutating verbs: `status log diff show fetch pull remote branch add commit push merge restore checkout switch stash …`). This is **parity, not new trust** — every verb on it is one plain `git <verb>` is already blanket-allowed for in `standard-settings.json`; the change only extends that same allowance to the `-C` form the string-matcher can't express. Nuanced or unlisted git (config writes, `tag -d`, `reset`, `cherry-pick`, unknown subcommands) and every non-git command are left to prompt exactly as before.

Two independent safety invariants make the auto-approve unable to unlock a destructive op:

- **Deny-first precedence (CC platform guarantee):** a `PreToolUse` hook `"allow"` can *never* override a `permissions.deny` entry. The floor's destructive deny-list still wins.
- **Ordering (this hook):** destructive git is classified and denied at step 2, *before* the step-3 allow — so even a destructive form whose *subcommand* is on the safe-list (`git -C /x push --force` — `push` is listed) is denied, never approved. This is the property the new `CheckBashEmissionTest` pins.

The dead `Bash(git -C:*)` / `Bash(git -C *)` settings entries are **retained** as an inert, documented fallback (they would begin working if CC ever fixes the matcher) but are no longer the mechanism; the `//git-c` note now states the truth.

## Alternatives Considered

- **Another allow-string.** No string form matches `git -C <path> <verb>` (confirmed against the CC docs). This is the approach that already failed at session 46; repeating it is the recurrence, not the fix.
- **Keep click-approving exact commands.** The status quo: ~30 entries and counting in `settings.local.json`, one per path/verb, forever. Never converges; pure toil ([P10](../principles/master.md#p10--architect-owns-operational-substrate)).
- **A `deny`-only broadening (allow all git except a blocklist).** Rejected as unsafe: it would silently auto-approve any destructive git form *not yet enumerated* in the classifier. A positive safe-list fails toward the prompt (safe direction) for anything unforeseen.
- **Auto-approve arbitrary non-git commands too.** Out of scope and a far larger trust surface. This ADR touches only git, and only the verbs already trusted for the plain form.

## Consequences

- `git -C <path> <verb>` for the safe surface no longer prompts, on the federation and on every Claude Architect once the `session.py` push lands. The `settings.local.json` accumulation stops.
- `check-bash` is now a **grant** point, not only a deny point. Its safe-list and the deny-first/ordering invariants are the security-relevant surface; both are covered by tests (`SafeGitAutoAllowTest`, `CheckBashEmissionTest`), and `push-substrate.py` refuses to propagate `session.py` unless the suite is green.
- Fleet substrate change: ships via `curate/push-substrate.py` (`session.py` + regenerated `.claude/settings.json` carrying the corrected `//git-c` / `//pretooluse` notes); non-Claude targets skipped ([ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md) §5). Fresh Architects inherit it via the kit template.
- **Follow-up (not built, recurrence-gated):** the safe-list includes `checkout`/`restore`/`stash`, whose working-tree-discarding forms (`checkout .`, `restore .`, `stash clear`) are not caught by the destructive classifier and so are auto-approved in parity with the existing plain-git allowance. If silent working-tree discard ever bites, the fix is to add those forms to `_destructive_git_hit` (deny) or split them into a prompt tier — build on recurrence, not anticipation.

## References

- [ADR-0034](0034-settings-are-federation-pushed-generated-substrate.md) — settings are federation-pushed generated substrate (where the dead `git -C` strings were added).
- [ADR-0020](0020-session-rituals-are-a-code-harness.md) — `session.py` is the mechanical harness that hosts `check-bash`.
- [P15 `code-for-mechanism-not-judgment`](../principles/master.md#p15--code-for-mechanism-not-judgment) / [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence) — the recurrence → make-it-structural rule this applies.
- [P9 `destructive-ops-confirmed`](../principles/master.md#p9--destructive-ops-confirmed) — the deny half the allow half is layered on top of.
- Session 60 conversation (the operator's report); claude-code-guide verification of the CC permission-matcher and hook-decision semantics.
