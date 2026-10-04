# ADR-0067: Adopt-in-place is the third onboarding path

**Status:** Accepted
**Date:** 2026-07-23 (session 90)
**Builds on:** [ADR-0025](0025-bootstrap-is-a-code-harness.md) (bootstrap is a code harness), [ADR-0014](0014-existing-architect-retrofit.md) (retrofit), [ADR-0023](0023-standard-operating-substrate.md) (same pipes, different houses), [ADR-0017](0017-bootstrapping-is-a-federation-responsibility.md)
**Deciders:** the operator (directed the first adoption and approved building the path rather than hand-adopting); Federation Architect (design + build)
**Reality:** Built — first exercised on a real member, session 90

> *Public edition.* The examples below use a hypothetical system, `example-app`, in place of
> the members this path was first run on.

## Context

[PROCESS.md](../PROCESS.md) has offered exactly two onboarding paths, and its own
tie-breaker says: *"if the candidate Architect has any committed role-doc content beyond
template skeleton, go retrofit. If it's a cold start, go fresh."*

A system like `example-app` breaks that rule by satisfying neither branch:

- **Not fresh.** Fresh means "repo doesn't exist yet, or exists empty," and
  `bootstrap.py` implements it literally — `gh repo create`, then `git clone` into
  a new directory under the configured projects root, with a hard `die()` if the target directory already exists.
  `example-app` is a working git repo with running code, ADRs of its own, and perhaps a
  `STATUS.md` it already writes against our ADR-0021 schema.
- **Not retrofit.** [ADR-0014](0014-existing-architect-retrofit.md) reconciles an *evolved
  role doc and ongoing practice* against federation conventions. `example-app` has no role
  doc and no Architect at all. There is nothing to reconcile.

The tie-breaker routes it to *fresh* (no role-doc content ⇒ cold start), and fresh is
exactly the path that cannot run. The gap is not specific to one system: other backlog
members had the same shape (working material, no repo-level Architect; in one case not yet
a git repo at all).

The distinguishing property is not repo-emptiness and not role-doc maturity. It is:
**the system exists and works; nobody has ever been its Architect.**

## Decision

### D1 — Name the third path

**Adopt-in-place**: install the standard operating substrate into an existing repo that
has real content and no Architect. Three paths now, selected by two independent questions
rather than one:

| | Repo has content? | Has an Architect? | Path |
|---|---|---|---|
| Fresh | no | no | `bootstrap.py --spec` |
| **Adopt-in-place** | **yes** | **no** | **`bootstrap.py --spec --adopt <path>`** |
| Retrofit | yes | yes | [ADR-0014](0014-existing-architect-retrofit.md) |

The fourth cell (no content, has an Architect) is incoherent and needs no path.

### D2 — Adopt installs pipes, never houses

The adopt copy plan is a strict **subset** of the fresh plan — a test pins the subset
relation, so adopt can never install something fresh doesn't. It omits `README.md`,
`STATUS.md`, `.gitignore`, `adr/README.md` and `adr/template.md`, because each is a surface
the adopted system already owns. This is [ADR-0023](0023-standard-operating-substrate.md)
applied at onboarding: standardization is mandatory on the operating substrate and stops
at the domain.

The `adr/` omission is the clearest case. Suppose `example-app` keeps its decision record
at `docs/adr/`. Installing our repo-root `adr/` would produce two ADR homes in one repo — a
duplication ([P16](../principles/master.md#p16--avoid-duplication)) created by the very act
of onboarding, and an imposition of our layout on a system whose practice was already
sound. The Architect's role doc points at `docs/adr/` instead.

**Any planned file that already exists is skipped and reported, never overwritten.**

### D3 — `.gitignore` is merged, never replaced

This is the sharp edge, and it cuts both ways:

- Replacing the target's `.gitignore` is a **data-loss bug**. An adopted system's own
  ignore rules may be the only thing keeping an unrecoverable data directory out of git.
- *Not* merging ours is a **P3 breach**. Without the kit's rules, `users/` (the operator's
  preference profile), `proposed-edits/` (the receipt-ritual inbox), `architect-learnings.md`
  and `.session-state/` would be committed into the adopted repo the first time the
  Architect ran a session.

So the merge appends only the rules the target lacks, comparing stripped non-comment lines
so a re-adopt adds nothing twice, under a labelled block explaining why the rules are
there. A commented-out rule does not count as coverage.

### D4 — Remote creation is opt-in

An adopted system may have no git remote at all, having lived its whole life as a local
repo. Creating a GitHub repo is **outward-facing**, so it never happens as a side effect of
onboarding: `--create-remote` is an explicit flag, and without it adopt commits locally and
reports that nothing was pushed.

### D5 — Preflight refuses every other shape

Adopt refuses, changing nothing, when the target: is not a git repo; already carries
Architect substrate (`session.py` / `CANON.md` / `STANDARD.md`) — that is a retrofit, and
adopting would write a template role doc over evolved practice; or has a dirty working
tree — in-flight work must not be swept into the adoption commit, which has to stand alone
as a reviewable change. Target-shape checks run *before* the user-profile check, so when
both are wrong the more actionable error wins.

## Consequences

- Systems of this shape have a defined path instead of an ad-hoc one. A system that is not
  yet a git repo needs `git init` first (adopt installs into history; it does not create
  it) — later relaxed, see the first update note.
- The onboarding decision is now two questions, not one. PROCESS.md's single tie-breaker
  ("any role-doc content ⇒ retrofit") was the thing that mis-routed the first adoption and is
  replaced by the D1 table.
- Adopt is deliberately *less* opinionated than fresh. A fresh repo gets our whole shape;
  an adopted one gets only the pipes. Some adopted members will therefore look less
  uniform — a system keeping ADRs in `docs/adr/` while the rest use `adr/`. That is the
  intended reading of ADR-0023, not drift.
- The unattended, never-overwrite rule means a *partial* adopt is possible in principle
  (a repo that already has, say, a `CLAUDE.md`): the pre-existing file is preserved and
  reported, and reconciling it is the Architect's judgment, not the harness's.

## Notes — two kit defects surfaced by the first run

Both pre-existed this ADR and affect every bootstrapped member, not just adopted ones:

1. **`gitignore-template` never excluded `.session-state/`**, so a freshly bootstrapped
   Architect would commit its own liveness sidecar. Fixed in the kit.
2. **`session.config.json` had no `standard_version` key**, so every bootstrapped member
   self-reported *"unstamped"* under [ADR-0047](0047-versioned-standard-substrate-and-rollout-reconciliation.md)
   — the declared-version half of declared-vs-detected was simply absent. `bootstrap.py`
   now stamps it from `standard_check.LATEST` rather than hardcoding it in the template,
   so the template cannot go stale against what the federation actually ships (P16).
   One existing member still carried the gap and needed the one-line stamp; it was left
   alone because another session held that system.

## Verification

- `tests/test_bootstrap_adopt.py` — 16 tests: gitignore merge (existing rules survive,
  federation rules added, a data directory never dropped, idempotent, comments don't count
  as coverage), preflight refusals (non-git, already-has-substrate ⇒ RETROFIT, dirty tree,
  missing target), dry-run writes nothing, remote left alone without the flag, and the
  copy-plan subset/omission properties.
- Suite 564 → 580 green.
- **Live, on the first adopted member:** the adoption commit added substrate only; a
  `git diff` over every pre-existing path was **empty** (each file byte-identical), and the
  system's own ignored directories were confirmed still ignored. `standard_check.py
  --status` in the adopted repo reported *"v1.3.0 — self-check clean."* Repo created
  PRIVATE.

## Update note — non-git targets are auto-inited, not refused (2026-07-24, session ~96)

D5's non-git refusal (*"is not a git repo ... `git init` it first"*) was found live while
onboarding a later member: a well-behaved refusal that still left a manual step in the
middle of an otherwise automated onboarding path. the operator overruled it directly, ruling that
adopt must end in a working session with working git, with no manual step left over.

**Changed:** a target directory with no `.git` is no longer refused. Preflight now
defers the actual mutation past the `--plan`/`--dry-run` gate (same discipline as every
other write this function makes) and, on a real run, `git init -b main`s the directory
and — if it holds any content — commits it as the repo's root commit
(`init_and_commit_existing_content()` in [`bootstrap.py`](../bootstrap.py)) before
adopt's own substrate commit lands on top. An empty directory is still initialized (a
valid, if history-less, repo) with nothing to commit. The dirty-tree refusal is
unchanged and now applies only to a target that already had git history walking in —
there is no "dirty" state to protect against for content that adopt itself is about to
capture as the first commit.

**Unchanged:** the RETROFIT refusal (existing Architect substrate) and the missing-target
refusal both still apply exactly as D5 specified; the marker check now runs before the
git-shape check so a directory that carries substrate files without ever having been a
git repo is still correctly refused as a RETROFIT, not silently adopted.

**Verification:** `tests/test_bootstrap_adopt.py` — `test_non_git_directory_is_auto_inited_not_refused`
replaces the old refusal test; `InitAndCommitExistingContentTest` (2 tests) exercises the
extracted helper directly, hermetically (patched git identity), so proving the commit
behavior doesn't require running the whole pipeline against the real federation repo.
Suite 665 → 671 green (shared with the `poga bootstrap` parity fix landing the same
session).

## Update note — `--adopt` with no arguments (2026-07-24, session ~97)

the operator ruled, immediately after the first update note landed, that `poga` with no `--spec`
and no path after `--adopt` should run in the current directory, find its files there,
and report if they are missing or if the project is already adopted.

**Changed — two independent defaults, both opt-out by passing the value explicitly:**

1. **Target defaults to the directory the user is standing in.** `--adopt` takes an
   optional value (`nargs="?", const="."`); bare `--adopt` means "here." The refusal
   cases from D5 and the first update note (already-adopted ⇒ RETROFIT, missing target)
   still fire exactly as before — the default only supplies *which* directory to check
   them against.
2. **Spec defaults to `bootstrap-spec.json` found inside the adopt target.** `--spec` is
   no longer required at the argparse level; omitted under `--adopt`, `bootstrap.py`'s new
   `load_adopt_spec()` looks for that file in the target and dies with a named, actionable
   message (which keys are required) if it isn't there — never a guess. An explicit
   `--spec` still wins even when the target also carries its own `bootstrap-spec.json`.
   (Fresh bootstrap, no `--adopt`, still requires `--spec` explicitly — there is no
   target directory yet for it to default from.)

**A real latent bug surfaced building this and got fixed alongside it, not separately:**
the `poga` wrapper `cd`s into the federation checkout before exec'ing `poga_cli.py` (so
`session`/`lanes` always operate on the right tree regardless of caller cwd — see the
wrapper's own header comment on the session-90 lane-resolution bug). That means a
relative `--adopt`/`--into` was ALREADY silently resolving against the federation repo,
not wherever the user actually typed `poga` from, before this change ever added a
default — the default just made the bug reachable with zero arguments instead of one
easy-to-miss relative path. Fixed at the root: the wrapper now exports
`POGA_INVOKED_FROM="$PWD"` as its first line, before any `cd`; `bootstrap.py`'s new
`resolve_from_invocation()` resolves any relative `--adopt`/`--into` against that env var
when present, falling back to plain cwd for a direct `python3 bootstrap.py`/`poga_cli.py`
call (no wrapper, no `cd` to correct for). `poga_cli.py`'s `resolve_target()` (the
`--into` path, both bootstrap and restore) now goes through the same helper.

**Verification:** `tests/test_bootstrap_adopt.py` `AdoptDefaultsTest` (4 tests, direct
invocation — cwd already is the invocation dir with no wrapper involved) and
`tests/test_poga_cli.py` `BootstrapAdoptDefaultsTest` (3 tests) — the latter includes
`test_poga_invoked_from_wins_over_process_cwd`, which runs with the subprocess `cwd` set
to the federation ROOT (simulating the wrapper's post-`cd` state) and `POGA_INVOKED_FROM`
set to a scratch target, proving the env var — not process cwd — wins. Also verified live,
end to end, through `poga_cli.py` directly (not just tests): a real `--adopt --plan` run
from inside a scratch project with only a `bootstrap-spec.json` sitting in it correctly
found both the target and the spec with zero other arguments. Suite 673 → 680 green.
