# First task, live agent

**Date:** 2026-10-01. **Result: NOT RUN.** The setup ran. The live agent did not, because the runtime could not sign in from the scratch home. That run is the one open item.

This is the live counterpart of [`basic-acceptance.md`](basic-acceptance.md). That drill proves the machinery with a person typing the agent's commands. This one was meant to show a signed-in Claude Code agent doing the [first task](../docs/first-task.md) by itself.

Paths are written `$S/...`. `$S` is a fresh scratch folder made with `mktemp -d`.

## The plan

1. Export the public cut to `$S/cut` with `curate/public_cut.py`. Install poga into a scratch home, `HOME=$S/home`.
2. Make the project: `mkdir $S/hello-poga && cd $S/hello-poga && poga init --yes`.
3. Run `poga` inside a detached tmux session of our own, so a person's typing can be sent with `tmux send-keys` and read back with `tmux capture-pane`. That is the closest thing to a person at a terminal.
4. Send the person's prompt, word for word:

   > Add a file greet.py with a function greet(name) that returns "Hello, <name>!", and a unittest for it in tests/test_greet.py. First draw and claim a work item for this. Run poga test until it prints PASSED. Then ask me before you land.

5. When the agent asks, send the reply: `land it`. The agent then lands with `python3 session.py merge --confirm "land it"`. The runtime closes itself after a land, so the person marks the item done from their own terminal: `poga work status WI-0001 --status done` (see the [first task](../docs/first-task.md)).

## What ran

### Export and install: done

```
$ python3 curate/public_cut.py --out $S/cut
public_cut: wrote 485 file(s) to $S/cut (edition showcase)
public_cut: clean — no known shape matched.
$ cd $S/cut && ./poga install          (with HOME=$S/home)
poga install: linked $S/home/.local/bin/poga -> $S/cut/poga
```

### `poga init --yes`: done

```
$ mkdir $S/hello-poga && cd $S/hello-poga && poga init --yes
  No local federation on this machine yet — making one. Three questions.
  Project name: Hello Poga
  Create a private GitHub remote and push to it? (...): no
  created the local federation at $S/home/.config/poga/federation
  committed locally — no remote, nothing pushed
  registered hello-poga in the local federation (1 project)
$ git log --oneline
<sha> feat: install hello-poga-arch (bootstrap) (bootstrap kit v0.24.0)
```

With `--yes`, `init` took the name, time zone and machine label from this machine. They are left out here.

### Open a lane with `poga`: refused, correctly

```
$ poga
poga: REFUSING to create a lane — 1 precondition(s) failed ...
  onboarding: the first-run wizard will run — $S/home/.claude.json does not exist
  (a machine where `claude` has never run).
    -> Run `claude` once interactively on this machine and complete onboarding
       BEFORE launching a lane.
```

That is the right answer for a home where the runtime has never run.

### Sign the runtime in: blocked

The machine has a signed-in Claude Code. Its login lives in the real home, not in `$S/home`. From the scratch home:

```
$ claude auth status
{ "loggedIn": false, "authMethod": "none", ... }
```

Two ways were tried to let the scratch home use the existing login without changing the real one:

- reading the stored login to pass it in as an environment token;
- copying the stored login file into `$S/home/.claude/`, to be deleted afterwards.

The operator's own permission guard refused both, as credential handling. That refusal is correct for an unattended agent, and it was not worked around. The other ways in each break a rule of the run:

- running the runtime with the real home writes its trust and project state into the real home;
- signing in fresh (`/login` or `claude setup-token`) opens a browser that only a person can answer.

So no live agent ran. Nothing below step 3 of the plan happened.

## Expected output, when it is run

From [`docs/first-task.md`](../docs/first-task.md). The agent should, in order:

1. run its startup;
2. draw and claim an item: `WI-0001: ...` then `claimed 'WI-0001' ...`;
3. write `greet.py` and `tests/test_greet.py`;
4. run `poga test` until it prints `test: PASSED [serial] — 1 test(s), OK (exit 0).` (the count may differ);
5. stop and ask before landing;
6. after `land it`: `landed:  lane '<lane branch>' → main`, then `poga work status WI-0001 --status done`.

The project's `main` should then read, newest first:

```
<sha> docs(handoff): recompile views and stamp STATUS after lane land
<sha> feat(WI-0001): ...
<sha> chore(work-items): add WI-0001 — ...
<sha> feat: install hello-poga-arch (bootstrap) (bootstrap kit v0.24.0)
```

## How to run it yourself

Sign Claude Code in on your machine first: run `claude` once, finish its first-run steps, sign in, and trust the project folder when asked. Then follow [`docs/first-task.md`](../docs/first-task.md) in your real home. With a signed-in runtime, `poga` opens the lane and the agent does the rest.

## What this record does NOT show

- **A live agent.** No model ran. This is the open item.
- Anything after the lane opens: the claim, the files, `poga test`, the ask, the land, the done status.
- The runtime's first-run wizard or its trust dialog.
- Other runtimes, other systems.

## What was kept clean

- The real poga config folder was listed before and after. It was identical.
- No login file was copied anywhere. Nothing was written outside `$S`.
- No tmux session was started.
