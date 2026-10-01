# Basic acceptance drill

**Date:** 2026-09-28. **Result: PASS**, 50 of 50 checks. **Work item:** WI-0455.
**Driver:** [`basic-acceptance.sh`](basic-acceptance.sh). Run it on macOS, from your own checkout of this cut, to get your own transcript: `drills/basic-acceptance.sh`. It tests the checkout it lives in, as downloaded.

## What it proves

One person, on one machine, with only a fresh export of the public cut, Git, Python 3 and one runtime, can:

1. install poga and start a local-only project with `poga init`;
2. take one small task through claim, checkpoint, resume, verify and land;
3. record a lesson, add a second project, and see the lesson arrive there.

There was no GitHub, no second host, no scheduler, no board, no broker and no existing profile. The drill proves the machinery, not an agent. The runtime could not sign in, so the drill operator typed the commands the agent would type. See **Needs a person** below.

Paths below are written `$DRILL/...`. The drill ran in a neutral scratch folder, `/private/tmp/poga-drill-basic`, so this record carries no user name.

## How it was isolated

The recorded run was started from a fresh export of the public cut, not from the source repository. By default the script takes the checkout it lives in as the candidate and copies it, without its git history, to `$DRILL/cut`. That copy is the only step that reads outside `$DRILL`. (A maintainer with the source repository can instead pass `--from-source <repo>`, which makes a fresh export with `curate/public_cut.py` first. A tree with no `PUBLIC-CUT-RECEIPT.md` is refused without that flag.) Everything after the copy ran inside a macOS sandbox, with an empty environment:

```
sandbox-exec -f $DRILL/drill.sb /usr/bin/env -i HOME=$DRILL/home \
  PATH=$DRILL/home/.local/bin:$DRILL/bin:/usr/bin:/bin:/usr/sbin:/sbin \
  TMPDIR=$DRILL/tmp TZ=UTC LANG=C.UTF-8 TERM=xterm-256color bash $DRILL/steps.sh
```

The sandbox profile:

```
(version 1)
(allow default)
(deny file-write*)
(allow file-write* (subpath "$DRILL") (subpath "/dev") (subpath "<system temp dir>"))
(deny file-read* file-write* (subpath "/Users"))
(deny file-read* (subpath "/opt/homebrew") (subpath "/usr/local"))
(deny network*)
```

- **/Users is unreadable.** That covers every real home folder: the operator's federation, profile, git config and runtime login.
- **No network.** No GitHub, no second host, no broker, no board. Nothing could be reached or served.
- **Writes only under `$DRILL`.** Two exceptions: `/dev`, and this user's system temp folder. macOS `mktemp` ignores `TMPDIR`, and poga's shell calls it.
- **Only Git, Python and one runtime.** The package-manager trees are unreadable, so `gh` and anything else installed there is out of reach. The runtime is a copy of the `claude` binary in `$DRILL/bin`.
- The export was published as a one-commit repository with a neutral identity (`drill <drill@example.invalid>`), in its own empty HOME, inside the same sandbox. The newcomer cloned that.

The proofs, from the transcript:

```
$ ls -An $DRILL/home
total 0
CHECK PASS: HOME is empty: no .gitconfig, no .config/poga, no .claude
$ git config --global --list
fatal: unable to read config file '$DRILL/home/.gitconfig': No such file or directory
$ command -v gh
[exit 1]
$ ls /opt/homebrew/bin
ls: /opt/homebrew/bin: Operation not permitted
$ ls /Users
ls: /Users: Operation not permitted
$ touch /private/tmp/poga-drill-outside
touch: /private/tmp/poga-drill-outside: Operation not permitted
$ git ls-remote https://github.com/git/git
fatal: unable to access 'https://github.com/git/git/': Could not resolve host: github.com
$ python3 --version        -> Python 3.9.6 (the system Python)
$ git --version            -> git version 2.54.0 (Apple Git-157)
$ claude --version         -> 2.1.284 (Claude Code)
```

The environment was exactly `HOME`, `PATH`, `TMPDIR`, `TZ`, `LANG`, `TERM` and `DRILL`. The first `poga init` was passed `--user-name "Drill User" --timezone UTC --machine-label laptop`. Those flags only keep this published record neutral; without them, `init` asks, and offers this machine's values as defaults. The second `init` asked for none of them, because the federation already existed.

## The steps

Each step lists the command and the key output. Commit ids and times differ on every run.

### 0. Isolation: PASS

Nine checks, listed above.

### a. Clone and install: PASS

```
$ git clone -q $DRILL/cut $DRILL/home/poga
$ ./poga install
poga install: linked $DRILL/home/.local/bin/poga -> $DRILL/home/poga/poga
$ command -v poga
$DRILL/home/.local/bin/poga
```

### b. Project alpha, `poga init`: PASS

```
$ mkdir -p ~/projects/alpha && cd ~/projects/alpha
$ poga init --yes --purpose "A small ledger of household expenses." \
    --user-name "Drill User" --timezone UTC --machine-label laptop
  No local federation on this machine yet — making one. Three questions.
  Create a private GitHub remote and push to it? (...): no
  created the local federation at $DRILL/home/.config/poga/federation
  remote:     none -> LEFT AS-IS, local only (pass --create-remote to create one)
  committed locally — no remote, nothing pushed
  git identity: none was configured — set Drill User <drill-user@localhost> for this repo only
  registered alpha in the local federation (1 project)
$ git remote -v
(nothing)
$ find ~/.config/poga -type f
.../federation/federation.json
.../federation/users/drill-user/profile.md
.../federation/inputs/alpha-arch-learnings.md
```

With no git identity on the machine, `init` set one for this repository only.

### c1. Open a lane with `poga`: PASS (a correct refusal)

```
$ poga
poga: REFUSING to create a lane — 1 precondition(s) failed on laptop and could not be repaired here.
  onboarding: the first-run wizard will run — ~/.claude.json does not exist (a machine where `claude` has never run).
    -> Run `claude` once interactively on this machine and complete onboarding BEFORE launching a lane.
[exit 1]
```

This is the right answer. The runtime has never been signed in, and only a person can do that.

### c2. The same launch past the preflight: PASS

`POGA_SKIP_PREFLIGHT=1` is poga's documented override. It shows what happens next without a person:

```
$ POGA_SKIP_PREFLIGHT=1 poga
poga: launching claude-code session in lane 'poga-1' (worktree .claude/worktrees/poga-1, branch worktree-poga-1)
Not logged in · Please run /login
[exit 1]
CHECK PASS: the lane worktree exists
CHECK PASS: the lane branch exists
```

poga and the real runtime opened the lane. The runtime then stopped at its login.

### c3. In the lane: start, draw, claim, begin: PASS

From here, the drill operator plays the agent in the lane the runtime opened. `session.py start` is what the runtime runs as its start hook.

```
$ python3 session.py start
CHECK PASS: start's stdout is one JSON object (what the runtime's hook reads)
surface: poga lane · worktree-poga-1 (isolated worktree; lands by CAS)
sync:    no upstream tracking — pull/push skipped
journal: sessions/journal/<id>.md — write your narrative here
$ poga work new "Add a tiny expense ledger" --scope product
WI-0001: Add a tiny expense ledger  [scope: product]
$ poga work claim 1
claimed 'WI-0001' as worktree-poga-1 — Add a tiny expense ledger
```

Then `ledger.py` was written: one function, `total(entries)`.

### c4. Checkpoint and leave: PASS

```
(a line added to the journal: "WI-0001: ledger.total written. Tests not written yet. Checkpointed.")
$ git add ledger.py sessions
$ git commit -q -m "wip(WI-0001): ledger total; tests next (checkpoint)"
$ git status --short
(nothing)
$ cd ~/projects/alpha && poga lanes
  laptop·<id>              poga-1     worktree present           UNMERGED
```

The unfinished work is on the lane's branch, not on `main`.

### c5. Resume: PASS

A lane that is only seconds old answers `WAIT`, because its liveness evidence can take up to 30 seconds to appear. The drill waited, as a person coming back later would.

```
$ poga resume
poga-1     yes  — unmerged, 1 commit(s) not on main
$ poga resume 1
poga: REFUSING to create a lane — 2 precondition(s) failed ...   (workspace-trust, onboarding)
$ POGA_SKIP_PREFLIGHT=1 poga resume 1
poga: resuming claude-code in EXISTING lane 'poga-1' (worktree .claude/worktrees/poga-1, branch worktree-poga-1)
Not logged in · Please run /login
$ cd .claude/worktrees/poga-1 && python3 session.py start
=== SESSION START (already open) ===
session: ~1  (journal <same id as c3>)
$ git log --oneline -3
<sha> wip(WI-0001): ledger total; tests next (checkpoint)
```

The same session, the same journal and the checkpoint commit were all there.

### c6. Verify with `poga test`: PASS

Two tests were added in `tests/test_ledger.py`.

```
$ poga test
test: PASSED [serial] — 2 test(s), OK (exit 0).
```

### c7. Land: PASS

```
$ python3 session.py merge --title "Tiny expense ledger" --confirm "land it" \
    --commit "feat(WI-0001): tiny expense ledger with tests"
verdict: GREEN, filed for tree <tree>
landed:  lane 'worktree-poga-1' → main
$ git log --oneline
<sha> docs(handoff): recompile views and stamp STATUS after lane land
<sha> feat(WI-0001): tiny expense ledger with tests
<sha> wip(WI-0001): ledger total; tests next (checkpoint)
<sha> chore(work-items): add WI-0001 — Add a tiny expense ledger
<sha> feat: install alpha-arch (bootstrap) (bootstrap kit v0.24.0)
$ poga work status WI-0001 --status done
$ poga work show 1
  status:     done
```

`main` has `ledger.py` and `tests/test_ledger.py`. With no remote, the land did not try to push. The `--confirm` text is what the drill operator, standing in for the user, said.

### d. Record a lesson and share it: PASS

An entry was appended to alpha's `architect-learnings.md`:

```
## 2026-09-28 · scope: architect-general · Read the verdict line, not the tail
```

```
$ poga share
  projects: alpha
  lessons: 1 shareable (...), 0 kept home; 0 new deliveries
```

With one project there is nowhere to deliver yet. That is the expected answer.

### e. Project beta, and the lesson arrives: PASS

```
$ mkdir -p ~/projects/beta && cd ~/projects/beta && poga init --yes
  registered beta in the local federation (2 projects)
  shared: alpha -> beta  Read the verdict line, not the tail
$ ls proposed-edits/beta-arch/pending
2026-09-28-lesson-from-alpha-<hash>.md
$ python3 session.py start
CHECK PASS: start's stdout is one JSON object (what the runtime's hook reads)
inbox:   2026-09-28-lesson-from-alpha-<hash>.md
CHECK PASS: beta's first session is told the picker ban (the default)
```

The start payload states the effective picker policy (`pickers: denied, the P17 default`), and the canon it carries says "never a picker". With `interaction.pickers: allow`, the payload says pickers are allowed and the canon's two P17 lines permit one, while keeping the rest of P17. `tests/test_local_init.py` checks a fresh project's first payload in both states.

### f. The picker guard in a fresh install: PASS

```
$ echo '{"tool_name":"AskUserQuestion", ...}' | python3 session.py check-question
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", ...}}
(set "interaction": {"pickers": "allow"} in session.config.json)
$ echo '{"tool_name":"AskUserQuestion", ...}' | python3 session.py check-question
(no output: allowed)
$ git checkout -- session.config.json
```

### g. What was not used: PASS

```
$ git -C ~/projects/alpha remote -v     (nothing)
$ git -C ~/projects/beta remote -v      (nothing)
$ find ~/.config/poga -type f
.../federation/federation.json
.../federation/users/drill-user/profile.md
.../federation/inputs/alpha-arch-learnings.md
.../federation/inputs/beta-arch-learnings.md
$ diff jobs.before jobs.after           (no new launchd or cron job)
```

The sandbox made "nothing written outside `$DRILL`" structural, apart from the system temp folder. A final check confirmed the outside probe file was never created.

## Defects found and fixed

Earlier runs of the drill found three defects in the landed code. Each was fixed with a test, and the drill was re-run from a fresh export of the fixed tree. The recorded run is that re-run.

1. **`poga work claim 1` refused.** `poga work --help` says ids take either form (`WI-0028` or `28`). `claim` and `release` did not. Fixed in `sessionlib/coord.py`. Test: `tests/test_claims.py`.
2. **Every `poga work` verb warned "CANNOT TELL whether ... is current" in a local-only project.** With no remote, there is nothing to be behind. It now stays quiet when the repository has no remote. A repository with a remote but no upstream still warns. Fixed in `sessionlib/land.py`. Test: `tests/test_checkout_staleness.py`.
3. **A fresh project's first `session.py start` printed a line before its JSON.** The ROADMAP render reported `wi-render: 0 section(s) already current in ROADMAP.md.` on stdout. The runtime reads that stdout as the start hook's JSON, so it could not parse it. The report now goes to stderr. Fixed in `sessionlib/journal.py`. Test: `tests/test_journal_frontmatter_guard.py`.

## Needs a person

- **Signing the runtime in.** Running `claude` once, completing its first-run steps, signing in and trusting the project folder. poga refuses to open a lane until this is done, and says so. The drill could not do it: it had no network and no person.
- **The agent's work.** A live agent decides what to build and writes it. In the drill, the operator typed the commands the agent would type.
- **Agreeing to the close.** `session.py merge` closes the session, and a hand-opened session needs `--confirm` with what the user actually said. The drill operator gave that answer.
- **Writing the lesson.** What was learned is judgment. The operator wrote it here.

## What the drill did not show

- **A live agent session.** No model ran. The runtime stopped at its login every time.
- **The runtime's own close.** After a land, the lane's worktree stays on disk until the runtime's session ends and removes it. `poga lanes` shows it as `merged`. `poga lanes --clean` deletes merged branches and prunes stale entries; it does not remove a live worktree.
- **The intake conversation.** Running `poga` in a folder with no repository launches an agent interview. The drill used `poga init --yes` only, and did not answer `init`'s questions by hand.
- **Checkpoint on exit.** The runtime's exit hook can commit unfinished work by itself. This run checkpointed by an explicit commit instead.
- **The picker guard inside a live runtime.** The drill fed the guard a payload directly. It did not watch a runtime call it.
- **Other runtimes and other systems.** Only Claude Code, only macOS.
- **Auto-detection.** The first `init` was given its name, time zone and machine label.
- **Anything optional beyond sharing.** No remote, no dispatch, no deploy, no schedule. That was the point.

## Smaller findings, not fixed

- On `poga resume`, the preflight refusal says "REFUSING to create a lane", though resume creates none.
- A project's start banner points at `python3 curate/gate_inputs.py --derive` and OPS-0009 when its suite is unmeasured. A project made by `poga init` has neither.
- The land does not mark the item done. That takes `poga work status WI-0001 --status done`, as the README's "Try it" says.
- In the sandbox, the preflight cannot read memory pressure (`ps` is denied). That is the sandbox, not poga.
