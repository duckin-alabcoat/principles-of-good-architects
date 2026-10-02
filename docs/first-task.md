# Your first task with POGA

This walkthrough takes one small task from a fresh install to a landed commit. You type a few commands. The agent does the rest, and asks you before it lands.

It takes about fifteen minutes. A signed-in agent has done this exact task live; [`drills/first-task-live.md`](../drills/first-task-live.md) records the run.

## What you need

- **macOS or Linux.** The core works on both. A few optional pieces are macOS-only; [macOS and Linux](../README.md#macos-and-linux) in the README lists them. Run POGA as your own user: it refuses to run as root, except inside a Claude cloud container, which is thrown away after the session.
- **Git 2.31 or newer.** The harness uses `git rev-parse --path-format=absolute`, which arrived in 2.31. The recorded acceptance drill used Git 2.54.0, the one Apple ships ([`drills/basic-acceptance.md`](../drills/basic-acceptance.md)).
- **Python 3.9 or newer.** 3.9 is the floor the harness is written for ([ADR-0118](../adr/0118-session-py-is-a-package-behind-a-thin-entry-point.md)). On macOS the harness runs on `/usr/bin/python3`, not on whatever `python3` your `PATH` finds first ([`interpreter.py`](../interpreter.py)). On Linux it runs on the `python3` your `PATH` finds, unless you pin one ([configuration](configuration.md)). The drill used Python 3.9.6.
- **Claude Code**, installed, with the `claude` command on your `PATH`, and an account you can sign in with.
- **tmux**, only if you later use parallel dispatch or attach. This walkthrough does not need it.

If `git` or `/usr/bin/python3` is missing on macOS, `xcode-select --install` installs Apple's command line tools, which carry both. On Linux, install `git` and `python3` with your package manager.

You do not need a GitHub account, a second machine or a scheduler.

## 1. Install

```sh
git clone https://github.com/duckin-alabcoat/principles-of-good-architects.git
cd principles-of-good-architects
./poga install
```

The installer links `poga` into `~/.local/bin`. Keep the clone where it is: the link points into it.

If the installer says it added an export line to your shell's startup file, your current terminal does not have it yet. The file depends on your shell: `~/.zshrc` for zsh, and for bash `~/.bash_profile` on macOS or `~/.bashrc` on Linux. For any other shell it writes nothing and prints the line for you to add. Do one of these:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

or open a new terminal. Then check:

```sh
command -v poga
```

It should print a path ending in `.local/bin/poga`.

## 2. Make a project

The project is its own folder, outside the clone. This walkthrough puts it in your home folder:

```sh
mkdir ~/hello-poga
cd ~/hello-poga
poga init --yes
```

`--yes` takes every default. On the first run it also makes a small local federation at `~/.config/poga/federation`. No remote is created. Among the lines it prints (it prints the full path to your home folder, shown here as `/Users/you`; on Linux it is usually `/home/you`):

```
  created the local federation at /Users/you/.config/poga/federation
  remote:     none -> LEFT AS-IS, local only (pass --create-remote to create one)
  committed locally — no remote, nothing pushed
  registered hello-poga in the local federation (1 project)
```

## 3. Sign Claude Code in, once

Still in `hello-poga`:

```sh
claude
```

Sign in, finish the first-run questions, and say yes when it asks whether to trust this folder. Then type `/exit`.

The trust question also says the folder pre-approves some tool permissions in `.claude/settings.json`. `poga init` wrote that file. They are the commands the harness runs, such as `git status` and `git log`.

Only a person can do this step. If you skip it, `poga` in step 4 either refuses to open a lane and tells you why, or opens Claude Code here for you first and prints a line asking you to quit it when you are done. Claude Code clears the screen as it starts, so that line is gone by the time you look: answer the trust question, type `/exit`, and `poga` carries on and opens the lane. Do not type the task into that first window. It is not in a lane.

### What `git status` shows now

Every session writes a few records in the folder it runs in when it starts. The session you just opened ran in `hello-poga`, on `main`. So `git status --short` there is no longer empty. You will see some or all of these (shape; your file name differs):

```
 M STATUS.md
 M session-handoff.md
?? sessions/journal/<time>-<machine>-<id>.md
```

The journal is that session's own record. `STATUS.md` and `session-handoff.md` are views the harness rebuilds from the journals. This is by design ([ADR-0051](../adr/0051-multi-session-concurrency-retire-the-master-session.md)). You do not commit them. The harness commits its own files later, by itself, and leaves your files alone ([ADR-0091](../adr/0091-the-harness-commits-what-the-harness-writes.md)). Any other file in that list is yours, and the harness will not touch it.

## 4. Do the task

Keep your steps and the agent's steps apart. You type in two places: your terminal, and the agent's session. The agent types its own commands. You do not type them.

### You type

In your terminal, in `hello-poga`:

```sh
poga
```

It prints a line like this and starts Claude Code in a new lane:

```
poga: launching claude-code session in lane 'poga-1' (worktree .claude/worktrees/poga-1, branch worktree-poga-1)
```

A lane is a separate git worktree with its own branch. The agent works there, not on `main`.

The agent runs its startup and says who it is. In a new project it also says its mission is not written yet, and asks what the project is for. That can wait. Type this prompt into the agent's session, exactly:

> Add a file greet.py with a function greet(name) that returns "Hello, <name>!", and a unittest for it in tests/test_greet.py. First draw and claim a work item for this. Run poga test until it prints PASSED. Then ask me before you land.

When the agent asks whether to land, reply:

```
land it
```

Claude Code may ask your permission before the agent touches a file. One you are likely to see: `architect-learnings.md` "resolves through a symlink" to a path outside the lane. It is your project's lesson file, linked into the lane. Answering yes, once, is fine.

### The agent does

These are the commands the agent runs in its lane. Its exact words and commit messages will differ.

1. **Draws a work item and claims it.**

   ```
   $ poga work new "Add greet() with a unittest" --scope product
   WI-0001: Add greet() with a unittest  [scope: product]
   $ poga work claim 1
   claimed 'WI-0001' as <session id> — Add greet() with a unittest
   ```

   The item is saved on `main` right away, as its own commit. The claim tells other sessions the item is taken.

2. **Writes `greet.py` and `tests/test_greet.py`**, and commits them on the lane's branch.

3. **Runs the tests.**

   Among the lines it prints, these two matter (shape):

   ```
   $ poga test
   verdict: filed PASSED for this tree (<tree>) — a land of this tree runs no suite (ADR-0148).
   test: PASSED [serial] — 1 test(s), OK (exit 0).
   ```

   The test count is however many tests the agent wrote. `FAILED` or `DID NOT REPORT` means it is not done yet; the agent fixes it and runs `poga test` again.

4. **Asks you** whether to land, in a plain sentence. It does not land on its own judgment.

5. **Lands, passing your reply word for word.**

   ```
   $ python3 session.py merge --confirm "land it"
   ```

   It may also pass `--title` or `--commit "<message>"`. `--confirm` takes what you said, exactly as you said it. There is no list of accepted phrases. `merge` lands the lane on `main` and closes the session, and closing a session you opened needs your agreement. Without `--confirm`, the merge refuses and tells the agent to ask you. Your words are saved in the session's journal.

   Among the lines it prints, you should see these (shape; the tree id and times differ):

   ```
   verdict: GREEN for this lane's tree <tree> (poga-test, <time>) — the land runs no suite.
   landed:  lane 'worktree-poga-1' → main
            lane is clean + merged; close it to free the worktree.
   close: this session is complete. The window closes itself in 8s — the full summary is on the board.
   ```

   If the lane's files changed after the last `poga test`, the first line instead says the land is running the suite once, and then `verdict: GREEN, filed for tree <tree> (<n>s).`

6. **The session ends itself.** A few seconds after a successful land, Claude Code exits and you are back at your shell prompt in `hello-poga`. That is expected. A lane that did not land stays open instead, and says why.

### You type, to finish

The land does not mark the work item done. Do that from your terminal, in `hello-poga`:

```sh
poga work status WI-0001 --status done
```

It prints one line (shape):

```
WI-0001: status=done section=<section> impact=(unset) migration=no scope=product waiting-on=(nobody) blocked-by=(none)
```

POGA refuses `done` until a commit on `main` cites `WI-0001`, so this cannot run ahead of the land. If the agent already marked it done before its session ended, running it again is harmless.

## 5. Check the result

```sh
poga work show 1
git log --oneline -3
ls greet.py tests/test_greet.py
```

`poga work show 1` includes:

```
  status:     done
```

`git log --oneline -3` on `main` looks like this. The ids differ, and the third line is the lane's last commit, so its message is whatever the agent wrote:

```
<sha> chore(work-items): update WI-0001 — Add greet() with a unittest
<sha> docs(handoff): recompile views and stamp STATUS after lane land
<sha> <the lane's last commit, for example: docs(handoff): close <session-id>>
```

Further down are the agent's own commit with `greet.py` and its test, and `chore(work-items): add WI-0001 — ...` from step 1. `greet.py` and `tests/test_greet.py` are now on `main`.

`poga lanes` shows `poga-1` as merged. `poga lanes --clean` deletes merged branches.

## Stopping partway, and coming back

You can stop at any point before the land without losing work.

### You type

In the agent's session:

> Commit what you have in the lane as a checkpoint. I am stopping here.

The agent commits its unfinished work on the lane's branch. That commit is the checkpoint. It is not on `main`. Then type `/exit`.

Later, in your terminal, in `hello-poga`:

```sh
poga resume
```

It lists the lanes you can re-enter (shape):

```
poga-1     yes  — unmerged, 1 commit(s) not on main
```

A lane that closed only seconds ago may answer `WAIT`. Give it half a minute and ask again.

```sh
poga resume 1
```

This re-enters the same lane, on the same branch, with the same journal. It does not make a new one. The agent starts by reporting what is already there and what it needs to land. Tell it to carry on with `WI-0001`, and the rest of step 4 goes as before.

### The agent does

On resume, the agent runs its startup, reads the checkpoint commit and its journal, and picks up where it stopped. It still asks you before it lands.

## Next

None of this is needed for the first task. Do it once the first task has worked.

- **Record a lesson.** Add an entry to `architect-learnings.md` in `hello-poga`, headed like this:

  ```
  ## <today, as YYYY-MM-DD> · scope: architect-general · <what you learned, in a few words>
  ```

  Then run `poga share`. With one project there is nowhere to send it yet, and it says so.

- **Add a second project.** In another new, empty folder:

  ```sh
  poga init --yes
  ```

  It joins the same local federation and prints `shared: hello-poga -> <new project>` for each lesson. The new project's first session lists the lesson under `inbox:`. A shared lesson is a suggestion for review; it does not change the new project's rules by itself.

- **Turn on more.** Remote sync, parallel lanes, deploys and scheduled jobs are all off until you turn them on. See [Optional capabilities](../README.md#optional-capabilities) in the README.

- **Change a setting.** [`configuration.md`](configuration.md) lists the `session.config.json` settings worth knowing, including the default ban on multiple-choice pickers.

## Troubleshooting

### Python bytecode is tracked in git

Older versions of POGA could commit Python bytecode (`__pycache__/` folders and `.pyc` files) to `main`. The symptom: after a land, `main` says it is not synced because an untracked `.pyc` file would be overwritten. New projects ignore bytecode, and the harness no longer stages it. But a project that was already hit still tracks those files, and the harness leaves tracked files alone.

Fix it once. In your project's main folder, with nothing else staged, run:

```sh
git rm -r --cached --quiet --ignore-unmatch -- '*.pyc' '*__pycache__/*'
git diff --cached --quiet || git commit --quiet -m "Stop tracking Python bytecode"
```

The first line stops tracking every bytecode file. The files stay on disk. The second line commits that, and does nothing if there was nothing to remove. Both lines work in bash and zsh. Also make sure your `.gitignore` has these two lines:

```
__pycache__/
*.pyc
```

## What this walkthrough is based on

The commands, outputs and refusals above come from the code and from the basic acceptance drill ([`drills/basic-acceptance.md`](../drills/basic-acceptance.md)). That drill runs the same harness steps in a sandbox, with the agent's commands typed by hand, because the runtime cannot sign in there. On 2026-10-01 a signed-in agent followed this walkthrough live, from a fresh install. [`drills/first-task-live.md`](../drills/first-task-live.md) records what it did, the questions the person answered, and what the run found; the corrections it showed are in this page.
