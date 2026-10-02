# First task, live agent

**Date:** 2026-10-01. **Result: RUN.** A signed-in Claude Code agent did the [first task](../docs/first-task.md) by itself, from a fresh install. It drew and claimed the item, wrote the code and its test, ran `poga test` to PASSED, asked before landing, and landed on `land it`. The person then marked the item done. `main` ended up as the walkthrough says it should.

This is the live counterpart of [`basic-acceptance.md`](basic-acceptance.md). That drill proves the machinery with a person typing the agent's commands. This one shows the agent typing them.

Paths are written `$S/...`. `$S` is a fresh scratch folder made with `mktemp -d`. Machine labels, user names, accounts and session ids are replaced with `<...>`. The runtime's own banner and notices are left out.

## The plan

1. Export the public cut to `$S/cut` with `curate/public_cut.py`. Install poga into a scratch home where Claude Code is already signed in, and nothing else is set up: no `~/.gitconfig`, no poga config, no federation.
2. Make the project: `mkdir $S/hello-poga && cd $S/hello-poga && poga init --yes`.
3. Run `poga` inside a detached tmux session of our own, so a person's typing can be sent with `tmux send-keys` and read back with `tmux capture-pane`. That is the closest thing to a person at a terminal.
4. Send the person's prompt, word for word:

   > Add a file greet.py with a function greet(name) that returns "Hello, <name>!", and a unittest for it in tests/test_greet.py. First draw and claim a work item for this. Run poga test until it prints PASSED. Then ask me before you land.

5. When the agent asks, send the reply: `land it`. Then the person marks the item done from their own terminal: `poga work status WI-0001 --status done`.

The plan skipped the walkthrough's step 3 (run `claude` once in the project and trust it) on purpose, to see what `poga` does when a person forgets it.

## What ran

Clock times are from the run. The whole agent part, from `poga` to the land, took about five minutes.

### Export, install, init: done

```
$ python3 curate/public_cut.py --out $S/cut
public_cut: wrote 491 file(s) to $S/cut (edition showcase)
public_cut: clean — no known shape matched. ...
$ cd $S/cut && ./poga install
poga install: linked <home>/.local/bin/poga -> $S/cut/poga
poga install: done — bare `poga` now works in this and future terminals.
$ claude auth status
{ "loggedIn": true, "authMethod": "claude.ai", ... }
$ mkdir $S/hello-poga && cd $S/hello-poga && poga init --yes
  No local federation on this machine yet — making one. Three questions.
  ...
  Create a private GitHub remote and push to it? (Local only is complete; add a remote any time): no
  created the local federation at <home>/.config/poga/federation
  ...
  committed locally — no remote, nothing pushed (re-run with --create-remote, or add an origin by hand).
  git identity: none was configured — set <user> <<user>@localhost> for this repo only (`git config user.email ...` to change it)
  registered hello-poga in the local federation (1 project)
```

The scratch home had no git identity, so `init` set one for this repo only and said so.

### `poga`: the trust dialog first, then the lane

The folder was not trusted yet, because step 3 was skipped. `poga` did not refuse. Its preflight opened Claude Code in the project for the person to answer:

```
$ poga
poga: preflight could not verify residency — no `residency` block in session.config.json — which is NOT the same as declaring no production mode (ADR-0106 D1). ...
preflight: workspace-trust needs the first-run/trust dialog; opening it in $S/hello-poga now. Answer it and quit `claude` to continue.
```

Claude Code asked whether to trust the folder. It said the folder pre-approves 67 tool permissions in `.claude/settings.json` (`Bash(git status)`, `Bash(git log)` and so on). The person said yes, declined the runtime's offer of a new full-screen renderer, and typed `/exit`. Then:

```
poga: preflight workspace-trust: repaired — ran `claude` in $S/hello-poga
poga: launching claude-code session in lane 'poga-1' (worktree .claude/worktrees/poga-1, branch worktree-poga-1)
```

### The agent's startup

`poga` gave the agent its first message, `run your startup and tell me who you are`. It read its role doc and reported (shortened):

```
⏺ Session ~1 start: Hello Poga Architect v0.1.0 · <machine> · 2026-10-01 CDT · lane worktree-poga-1
  Who I am: the Hello Poga Architect, the architect for the hello-poga system. ...
  My mission and scope aren't written yet. §2 of the role doc says the first working session writes them with you, and this is that session.
  ...
  What is Hello Poga meant to do?
```

It ended on a question about the project's mission. The person did not answer it and typed the task instead, as the walkthrough says.

### The task: claim, code, test

```
$ ./poga work new "Add greet(name) function with unittest" --scope product --impact feature --section next
committed 3371a24 in $S/hello-poga — the store is SAVED. ...
WI-0001: Add greet(name) function with unittest  [scope: product]  [impact: feature]
$ ./poga work claim WI-0001
claimed 'WI-0001' as <session id> — Add greet(name) function with unittest
```

It wrote `greet.py` (`return f"Hello, {name}!"`) and `tests/test_greet.py` with two cases, then:

```
$ ./poga test
test: PASSED [serial] — 2 test(s), OK (exit 0).
```

Without being asked, it then checked that the tests can fail. It changed `Hello, ` to `Hi, `, ran `poga test` (`FAILED (failures=2) (exit 1)`), put the original back and ran it again (`PASSED`).

Then it stopped and asked:

```
⏺ WI-0001 is done and ready to land.
  ...
  Landing closes this session by default. If you want to keep working here afterwards, I'll land with --continue instead.
  Land WI-0001 now?
```

### The land

The person typed `land it`. The agent first tried `poga work status WI-0001 --status done` and was refused, correctly, because nothing on `main` cited the item yet. It added a lesson to `architect-learnings.md`, filled in its journal and landed:

```
$ python3 session.py merge --title "greet function (WI-0001)" --confirm "land it" --commit "feat: add greet(name) with unittest (WI-0001)" --focus "..." --blocked false
verdict: none — running the suite once for this lane's tree <tree> in this lane before landing ...
verdict: GREEN, filed for tree <tree> (0.0s).
committed: feat: add greet(name) with unittest (WI-0001)  (on worktree-poga-1)
gate:    skipped — ... (the suite never runs at land — ADR-0148 D1; the lane's verdict and the trunk check cover it)
landed:  lane 'worktree-poga-1' → main
         lane is clean + merged; close it to free the worktree.
close: this session is complete. The window closes itself in 8s — the full summary is on the board.
```

In those eight seconds the agent marked the item done itself (`WI-0001: status=done ...`). Then Claude Code closed and the person was back at the shell.

### The person marks it done

```
$ poga work status WI-0001 --status done
WI-0001: status=done section=next impact=feature migration=no scope=product waiting-on=(nobody) blocked-by=(none)
$ git log --oneline
6a599d5 (HEAD -> main) chore(work-items): update WI-0001 — Add greet(name) function with unittest
2b0226d docs(handoff): recompile views and stamp STATUS after lane land
2c83eec (worktree-poga-1) feat: add greet(name) with unittest (WI-0001)
3371a24 chore(work-items): add WI-0001 — Add greet(name) function with unittest
36046ba feat: install hello-poga-arch (bootstrap) (bootstrap kit v0.24.0)
```

The item was already done, so the command printed its status and exited 0. That is the shape the walkthrough gives.

## Questions the person answered

| The runtime or agent asked | The person said | Why |
|---|---|---|
| Trust this folder? (it pre-approves 67 tool permissions) | Yes, I trust this folder | The person made the folder a minute earlier with `poga init`, and the walkthrough says to say yes. |
| Try the new full-screen renderer? | Not now | A newcomer keeps the terminal they know. It does not change POGA. |
| (preflight: answer the dialog and quit `claude` to continue) | `/exit` | Step 3 of the walkthrough says to `/exit` after trusting. |
| What is Hello Poga meant to do? | Not answered; sent the task prompt | The walkthrough says to type the task after the startup. The mission can wait. |
| Permission: `architect-learnings.md` resolves through a symlink to a path outside the lane. Proceed? | Yes (once, not "always allow") | It is the project's own file. Allowing it once is enough. |
| Land WI-0001 now? | `land it` | The planned reply. |

## Findings

1. **The preflight's "quit `claude` to continue" line is wiped.** Claude Code clears the screen when it starts, so the person sees only an idle prompt in their project. Nothing says to `/exit`. A newcomer could type the task there, in the main checkout, outside any lane. The walkthrough now says what happens if step 3 was skipped.
2. **A permission prompt the walkthrough did not mention.** The agent's lesson goes to `architect-learnings.md`. In a lane that file is a link to the main checkout, so Claude Code asks first. The walkthrough now mentions it.
3. **The trust dialog's permission warning was not mentioned.** It lists 67 pre-approved tool permissions. They come from the `.claude/settings.json` that `poga init` wrote. The walkthrough now says so.
4. **The claim line in the walkthrough had the wrong shape.** It showed `claimed 'WI-0001' as worktree-poga-1`. The real line names the session id. Fixed.
5. **The startup ends on a question.** A new project has no mission yet, so the agent asks what the project is for. The walkthrough now says you can leave that for later.
6. **A false-green guard gap, found by the agent.** The guard refuses a `poga test` whose output is piped into `tail`. It refused the one-line form, but let through `./poga test 2>&1 | tail -20` at the end of a command that also wrote two files with here-documents. The verdict that came back happened to be right. The agent recorded this as a lesson. The cause: the command splitter under every Bash guard joins the line after a here-document's end marker onto the here-document's command, so no guard sees that line. A test pins this on purpose as a known fail-open. Closing it makes every guard stricter at once, so it is not changed in this release.
7. **A residency warning means nothing to a newcomer.** Every `poga` in a new project prints `preflight could not verify residency ... (ADR-0106 D1)`. It does not say whether the person should do anything (they need not). Not changed in this release.
8. **A new project starts behind the standard.** The agent reported its harness on standard v1.4.0 with a rollout to v1.20.0 owed. The bootstrap kit's pinned standard lags the working repo. Not changed in this release.
9. **The agent changed things nobody asked for.** At startup it wired its user profile into `session.config.json` (the `git add` of the profile failed, "beyond a symbolic link", because `users/` is a link). Before landing it added `__pycache__/` to `.gitignore`. Both are small and were in its summary before the land, but they were not part of the task. Not changed in this release.

## What this record does NOT show

- A first-ever start of Claude Code. The scratch home had been signed in already, so its first-run questions (theme, sign-in) were done before this run.
- Stopping and resuming (`poga resume`). The [basic acceptance drill](basic-acceptance.md) covers it with the agent's commands typed by hand.
- Sharing a lesson to a second project.
- Other runtimes, other systems, a remote.

## What was kept clean

- The run used its own scratch home. The real Claude Code login, the real `~/.claude` and the real poga config folder were not read or written. No login file was copied.
- The scratch folder `$S` holds the cut, the project and the lane. Nothing else was written.
- The tmux session was the run's own.
