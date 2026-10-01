# Changelog

This file lists what changed in each public release of POGA.

The public repository is a cut of a private working repository, published as a single commit, so it has no commit history to read. This file is that history for public readers. A public release is named by the date it was cut (`YYYY.MM.N`, N counting cuts in that month), because the working repository's own versions move on a different clock. The working repository keeps a release record for each of its versions; one ships here as a sample: [`releases/federation/7.6.0.md`](releases/federation/7.6.0.md).

## 2026.10.2 — 2026-10-01

This release records a live run of the first task and fixes what that run showed in the walkthrough.

### Changed

- **A live agent run is recorded.** A signed-in Claude Code agent did the [first task](docs/first-task.md) by itself, from a fresh install: it claimed a work item, wrote `greet.py` and its test, ran `poga test` to PASSED, asked before landing, and landed on `land it`. [`drills/first-task-live.md`](drills/first-task-live.md) records the run, every question the person answered and why, and nine findings. This closes the 2026.10.1 limit "No live agent run is recorded yet."
- **The first-task walkthrough matches the live run.** [`docs/first-task.md`](docs/first-task.md) now says what the trust question's pre-approved permissions are, what `poga` does if you skip signing in first (and why its "quit to continue" line is gone from the screen), that a new project's agent asks about its mission and that this can wait, which permission prompt to expect during the task, and the real shape of the claim line.
- **CI passes on a runner without `mosh`.** On GitHub, 2026.10.1's full-suite job failed two `test_attach` tests in both editions. They assumed `mosh` was installed, and the `macos-15` runner has none, so the attach stopped on the missing transport before the line each test checks. The transport is not what they test, so they now stub that check. `poga` itself did not change.

### Release notes

**Supported environments**

- **macOS.** The working system runs on macOS, and so do the drill, the live run and CI. Nothing else has been tried.
- **Claude Code** as the agent runtime. Every recorded session ran on it, and so did the live run. Other runtimes: see the limits below.
- **Python 3.9 or newer.** 3.9 is the floor the harness is written for ([ADR-0118](adr/0118-session-py-is-a-package-behind-a-thin-entry-point.md)). On macOS the harness runs on the system Python at `/usr/bin/python3` ([`interpreter.py`](interpreter.py)). The drill, the live run and CI use that Python, 3.9.6.
- **Git 2.31 or newer.** The harness calls `git rev-parse --path-format=absolute`, which arrived in Git 2.31. That floor comes from reading the code; no older Git has been run. CI uses the Git on the pinned `macos-15` runner.

**Known limits**

- **One live run, one task.** The live run covers install, a new project, one task and its land. It did not cover a first-ever start of Claude Code, stopping and resuming, or sharing a lesson; the [basic acceptance drill](drills/basic-acceptance.md) covers the last two with the agent's commands typed by hand.
- **Some steps need a person.** Signing the runtime in, trusting the project folder, agreeing to the land, and writing a lesson.
- **A guard gap the live run found.** The Bash guards do not see a command on the line right after a here-document's end marker. The false-green guard let a piped `poga test` through that way. It is recorded in [`drills/first-task-live.md`](drills/first-task-live.md) and not fixed in this release.
- **New projects start a little behind.** A project made with `poga init` reports a harness standard older than the working repository's, and every `poga` in it prints a `residency` warning that does not say whether to act on it (you need not). Both are harmless and not fixed in this release.
- **Other runtimes are not proven as agents.** Codex CLI and Antigravity pass the substrate half of the runtime drill only. Their guards are undeclared, so a lane on them runs without the runtime hooks. See the README's Runtimes section.
- **The drill needs macOS `sandbox-exec`** and a `claude` binary, so it does not run in CI or on other systems.
- **Scheduled jobs need macOS `launchd`.** The nightly jobs under [`deploy/`](deploy/README.md) are optional and install as `launchd` agents.
- **The finish line is not reached.** `python3 curate/finish_line.py` read NOT YET on the private repository when the cut was taken. Run in this tree, it reads different numbers, because most of its inputs are withheld.
- **No history, and pull requests are not merged here.** Each cut replaces the repository's single commit. Most of the working record is withheld; a small sample ships, and links out of it may dangle. A fix sent here is re-made in the private repository and arrives in a later cut ([`CONTRIBUTING.md`](CONTRIBUTING.md)).
- **Existing projects do not update themselves.** A project made with `poga init` keeps the copy of the harness it was made with. Updating your clone does not change it.

## 2026.10.1 — 2026-10-01

This release answers an outside review of the first public cut.

### Changed

- **The acceptance drill script is executable.** [`drills/basic-acceptance.sh`](drills/basic-acceptance.sh) now ships with its executable bit set. The cut tool now refuses a cut in which a script the docs tell you to run directly is not executable.
- **The README says only what was shown.** It no longer calls Claude Code on macOS "tested end to end". It says the working system runs on that pair, that the drill checks the harness with the agent's commands typed by hand, and that a live run with a signed-in agent has not been recorded yet. [`drills/first-task-live.md`](drills/first-task-live.md) records that attempt and what stopped it.
- **A first-task walkthrough.** [`docs/first-task.md`](docs/first-task.md) takes one small task from install to a landed commit: what you type, what the agent does, and the output to expect. [`docs/configuration.md`](docs/configuration.md) lists the settings a newcomer is most likely to want.
- **CI, a contributing guide and this changelog.** [`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push and pull request: the install and onboarding tests, an install-and-init run in a throwaway home, quick consistency checks, and the full suite. [`CONTRIBUTING.md`](CONTRIBUTING.md) says how to run the tests, what happens to a fix you send, and how to update or remove an install.
- **Better advice after `poga install`.** When `~/.local/bin` is not on the current shell's `PATH`, the installer now prints the exact `export` line to run, or says to open a new terminal. It used to suggest `rehash`, which does not read `~/.zshrc` and so did not help.

### Release notes

**Supported environments**

- **macOS.** The working system runs on macOS, and so do the drill and CI. Nothing else has been tried.
- **Claude Code** as the agent runtime. Every recorded session ran on it. Other runtimes: see the limits below.
- **Python 3.9 or newer.** 3.9 is the floor the harness is written for ([ADR-0118](adr/0118-session-py-is-a-package-behind-a-thin-entry-point.md)); the code uses `zoneinfo` and `str.removeprefix`, which arrived in 3.9. On macOS the harness runs on the system Python at `/usr/bin/python3` ([`interpreter.py`](interpreter.py)), not on whatever `python3` comes first on your `PATH`. The drill and CI use that Python, 3.9.6.
- **Git 2.31 or newer.** The harness calls `git rev-parse --path-format=absolute`, which arrived in Git 2.31. That floor comes from reading the code; no version older than the one Apple ships has been run. The drill used Git 2.54.0. CI uses the Git on the pinned `macos-15` runner, at `/usr/bin/git`.

**Known limits**

- **No live agent run is recorded yet.** The drill proves the harness, not an agent: the runtime could not sign in, so a person typed the commands the agent would type ([`drills/basic-acceptance.md`](drills/basic-acceptance.md)). The live attempt is recorded as not run ([`drills/first-task-live.md`](drills/first-task-live.md)). *Closed in 2026.10.2.*
- **Some steps need a person.** Signing the runtime in, the agent's own work, agreeing to the land, and writing a lesson. The drill record lists them under "Needs a person".
- **Other runtimes are not proven as agents.** Codex CLI and Antigravity pass the substrate half of the runtime drill only. Their guards are undeclared, so a lane on them runs without the runtime hooks. See the README's Runtimes section.
- **The drill needs macOS `sandbox-exec`** and a `claude` binary, so it does not run in CI or on other systems.
- **Scheduled jobs need macOS `launchd`.** The nightly jobs under [`deploy/`](deploy/README.md) are optional and install as `launchd` agents.
- **The finish line is not reached.** `python3 curate/finish_line.py` read NOT YET on the private repository when the cut was taken. Run in this tree, it reads different numbers, because most of its inputs are withheld.
- **No history, and pull requests are not merged here.** Each cut replaces the repository's single commit. Most of the working record (work items, journals, briefs) is withheld; a small sample ships, and links out of it may dangle. A fix sent here is re-made in the private repository and arrives in a later cut ([`CONTRIBUTING.md`](CONTRIBUTING.md)).
- **Existing projects do not update themselves.** A project made with `poga init` keeps the copy of the harness it was made with. Updating your clone does not change it.
