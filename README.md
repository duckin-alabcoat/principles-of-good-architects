# Governing several long-lived AI collaborators through one lifecycle

This is **POGA** (*Principles of Good Architects*). It is a working system for long-lived AI coding agents, called **Architects**, that follow one set of rules the agents themselves maintain.

An Architect is a role, not a chat. It outlives any one session. It holds a role doc that says what it owns. It keeps a journal of what it decided. It works in its own git worktree ("a lane"), lands through a gate, and hands state to the next session in writing.

POGA has a small core and a set of optional capabilities. The core is what one person needs on one machine, with one project and one agent runtime. The working system runs on **Claude Code**, on **macOS or Linux**; [macOS and Linux](#macos-and-linux) says what is macOS-only. The reproducible drill checks the harness with the agent's commands typed by hand. A signed-in agent has also done the first task live, from a fresh install; [`drills/first-task-live.md`](drills/first-task-live.md) records the run. Other runtimes are covered in [Runtimes](#runtimes) below. Everything else is opt-in. Start with the core.

This repository is a **public cut**. It holds the doctrine and the machinery in full, plus a small curated sample of POGA's own working record. It holds nothing about the projects POGA governs. [`PUBLIC-CUT-RECEIPT.md`](PUBLIC-CUT-RECEIPT.md) lists what was published, what was withheld, and why.

## The core: one person, one machine, one project

You need Git, Python 3 and one agent runtime. The working system runs Claude Code, on macOS or Linux. No GitHub account, no second machine, no scheduler.

| Piece | What it gives you |
|---|---|
| **Project context** | A role doc, a `CLAUDE.md` or `AGENTS.md` entry point, and a generated canon (`CANON.md`, `STANDARD.md`) that every session starts with. |
| **Work items** | A store of numbered items under `work-items/`, driven by `poga work`. An item is claimed before it is worked, so two sessions never build the same thing. |
| **Lanes** | Each session works in its own git worktree. Bare `poga` opens a fresh lane; `poga resume` re-enters one a previous session left open. |
| **The land gate** | Work reaches the main branch only through the gate. `poga test` records that a lane's tests passed. `python3 session.py merge` runs the quick checks and merges only a lane with a passing record. |
| **Handoffs** | Each session ends by writing a journal entry and a handoff, so the next session starts from what was decided, not from memory. |
| **ADRs** | Structural decisions are written down under `adr/`, with the alternatives and the consequences. |
| **Checks** | Hooks and guards: the Bash guard, the picker guard, the work-item store check, the citation and doc checks. On Claude Code they run on every session. Other runtimes have no demonstrated equivalent of the runtime hooks yet (see [Runtimes](#runtimes)); the checks the land gate runs apply on any runtime. |
| **Captured lessons** | A session records what it learned in `architect-learnings.md`. Curation turns durable lessons into principles and habits in the canon. |

Settings live in each project's `session.config.json`. [`docs/configuration.md`](docs/configuration.md) lists the ones worth knowing, including the default ban on multiple-choice pickers.

### macOS and Linux

The core works on both. A few optional pieces use macOS tools and stay macOS-only.

| What | macOS | Linux |
|---|---|---|
| Install (`poga install`) and `poga init` | Yes | Yes |
| Lanes (git worktrees): `poga`, `poga resume` | Yes | Yes |
| Work items (`poga work`) | Yes | Yes |
| The land gate and `poga test` | Yes | Yes |
| Handoffs and journals | Yes | Yes |
| Preflight checks | Yes | Yes (the quarantine check is skipped; memory is read from `/proc/meminfo`) |
| Parallel dispatch and attach | Yes, with `tmux` | Yes, with `tmux` |
| Opening a lane in a new Terminal or Ghostty window | Yes (`osascript`) | No; use `tmux` |
| Deploy runner and scheduled jobs | Yes (`launchd`) | No |
| Anything that uses `plutil` | Yes | No |
| The basic acceptance drill | Yes, in a full sandbox (`sandbox-exec`) | Yes, with weaker isolation: a scratch `HOME`, plus `unshare` when it is available |
| Running as root | Refused | Refused |

On both, you need Python 3.9 or newer and Git 2.31 or newer. Dispatch and attach also need `tmux`. On macOS the harness runs on `/usr/bin/python3`. On Linux, `poga init` pins the first `python3` on your `PATH` that is 3.9 or newer ([`interpreter`](docs/configuration.md)). [`docs/first-task.md`](docs/first-task.md) has the full list.

## Try it

[`docs/first-task.md`](docs/first-task.md) is the walkthrough. It takes one small task from install to a landed commit: what you type, the prompt you give the agent, what the agent does, the output to expect, and how to stop and come back. In short:

```sh
git clone https://github.com/duckin-alabcoat/principles-of-good-architects.git
cd principles-of-good-architects && ./poga install
# if install says it added ~/.local/bin to your shell's rc file: open a new terminal
mkdir ~/hello-poga && cd ~/hello-poga && poga init --yes
claude    # once: sign in, trust the folder, then /exit
poga      # opens a lane and starts the agent in it
```

A signed-in agent has done that task live, from a fresh install. [`drills/first-task-live.md`](drills/first-task-live.md) records what it did and what the run found.

The basic acceptance drill checks the harness under the walkthrough. Its record is [`drills/basic-acceptance.md`](drills/basic-acceptance.md), and [`drills/basic-acceptance.sh`](drills/basic-acceptance.sh) runs it again from a fresh copy of this cut. It runs in a sandbox with only Git, Python 3 and the runtime: no GitHub, no network, no scheduler and no existing profile. The runtime cannot sign in there, so the drill types the agent's commands by hand, in the lane the runtime opened. Its 50 checks show that the harness works: install, a project, a work item taken through claim, checkpoint, resume, test and land, and a lesson reaching a second project. They do not show what working with a live agent is like; the live run does. The sandbox is full on macOS and weaker on Linux (see [macOS and Linux](#macos-and-linux)). The record lists what needs a person and what the drill did not show.

Adding a second project, in the walkthrough's "Next" section, is the first optional capability below, in its simplest form. It needs no remote.

## Optional capabilities

Each of these is off until you turn it on. None is needed for the core.

| Capability | What it does | What turns it on | What it needs |
|---|---|---|---|
| **Federation sharing across projects** | Shares lessons for review. `poga share` delivers a lesson from one project to the others' inboxes as a brief marked `apply: manual`; it does not change the canon. Turning reviewed lessons into canon and pushing that canon out is the advanced process under **Fleet-wide distribution**. | A second project in the same federation (`poga init` or `poga bootstrap --adopt` in another folder). | The local federation both projects point at. |
| **Remote sync and GitHub provisioning** | Creates a private remote for a project and pushes to it at session end. | `--create-remote` on `poga init` or `poga bootstrap`. Without the flag, nothing is created. | A GitHub account and the `gh` CLI, logged in. |
| **Parallel dispatch** | Fans a range of work items out to peer lanes that run side by side. | `poga dispatch <range> --go`. Without `--go` it only prints a plan. | A terminal multiplexer, and a runtime that can work a lane unattended. |
| **Deploy runner and promotion** | Puts a tagged release on a host, runs the release's own smoke and verify commands, and records a receipt. | A `deploy/deploy.json` contract in the project, a tag from `poga release cut`, and the runner installed on the host. | A macOS host to deploy to, with `launchd`. |
| **Scheduled jobs** | Nightly jobs: a serial run of the full suite, and headless adoption of briefs. | The installers in [`deploy/`](deploy/README.md), one per job. The shipped templates fire at 02:30 and 03:15, local time. Edit the template to change them. | macOS `launchd`. The adoption job also needs a logged-in session so the runtime can reach its login. |
| **Companion integrations** | A status board started at lane launch, a lease broker that lends lanes scarce resources, and a resident runtime that owns a repo's root. | The board: `POGA_BOARD_CMD` in `poga.local`. The resident runtime: `settings_path` and `pad_dir` in `session.config.json`. The broker: its own install. | Each companion is a separate program. None ships here. |
| **Fleet-wide distribution** | Pushes the canon, the standard section and the settings to every project, and delivers briefs to their inboxes. | Registering projects in `portfolio.md` and their paths in `repo-paths.local`, then running the push and delivery tools in [`curate/`](curate/). | More than one project, and a place each one can be reached. |

[`examples/multi-machine/`](examples/multi-machine/README.md) shows most of these turned on at once. It is a **fictional** installation: three hosts, Git as the only bridge, two nightly jobs. It illustrates the advanced case. It is not the spec.

## What is in here

| Path | What it is |
|---|---|
| [`AGENTS.md`](AGENTS.md) | The front door for an arriving agent, on any runtime. |
| [`federation-arch.md`](federation-arch.md) | The reference role doc: the Federation Architect, which governs the others and is bound by its own rules ([ADR-0003](adr/0003-federation-architect-is-a-participant.md)). |
| [`adr/`](adr/README.md) | Every structural decision, with its context, the alternatives, and the consequences. Start at the index. |
| [`principles/`](principles/master.md), [`habits/`](habits/master.md) | The canon: principles about what makes an Architect good, and habits that put them into practice. [`CANON.md`](CANON.md) is the generated digest every session receives. |
| [`STANDARD.md`](STANDARD.md) | The generated session rituals: how a session starts, checkpoints and ends. |
| `session.py`, [`sessionlib/`](sessionlib/), `poga` | The harness: session start and end, lanes, claims, the land gate, the work-item and ops stores. |
| [`curate/`](curate/) | Curation, gates and measurement, including the finish-line board and the tool that made this cut. |
| [`bootstrap-kit/`](bootstrap-kit/), [`PROCESS.md`](PROCESS.md) | What a new Architect is stood up from. |
| [`tests/`](tests/) | The suite. CI runs it on GitHub Actions, from [`.github/workflows/ci.yml`](.github/workflows/ci.yml), which ships in this cut. |
| [`docs/`](docs/first-task.md) | Guides: [your first task](docs/first-task.md) and [configuration](docs/configuration.md). |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | How to run the tests and send a fix. |
| [`CHANGELOG.md`](CHANGELOG.md) | What changed in each release. |
| [`deploy/`](deploy/README.md) | **Advanced.** The deploy runner and the scheduled jobs. |
| [`examples/`](examples/multi-machine/README.md) | **Advanced.** A fictional multi-machine installation. |

## One loop, end to end

The sample was chosen so that a cold reader can follow one piece of work through every stage:

1. **Found.** [WI-0290](work-items/WI-0290-the-adr-counter-still-has-no-renumberer.md) records, on 09-05, that the ADR counter has no renumberer. It also records why that job differs from the one already built.
2. **Governed.** Whether renumbering an unlanded ADR breaks ADR immutability is a doctrine call. It was decided in [ADR-0120](adr/0120-renumbering-an-unlanded-adr-is-an-identifier-correction.md): it is an identifier correction, not a revision.
3. **Built and landed.** A later session claims the item in a lane, builds it, runs a mutation sweep, and lands it through the gate. The item's notes record each step; they are part of the withheld working record.
4. **Released.** [`releases/federation/7.6.0.md`](releases/federation/7.6.0.md) harvests it into a derived minor version.
5. **Deployed** (an optional capability). The deploy runner put the tag on the production host with no person in the loop, and mailed a receipt back to the Architect's inbox. Receipts are part of the withheld working record.

Steps 1 to 4 are the core. Step 5 is the deploy runner, which this installation has turned on.

A second thread shows a lifecycle with a leftover. An earlier item measured a problem. A second item was refused by the readiness gate and split. [WI-0330](work-items/WI-0330-build-the-decisions-view-wi-0288-r3-5-th.md) built the fix. A defect found afterwards was not fixed by reopening the closed item. It was minted fresh as a new item, per [ADR-0139](adr/0139-a-closed-work-item-stays-closed-and-a-residue-is-minted-fresh.md), and [WI-0380](work-items/WI-0380-poga-work-status-refuses-done-to-open-an.md) made that rule mechanical.

Every sample file, and the reason it was chosen, is listed in the receipt. The sample is a subset. Links from it to items, journals or briefs that are not in the sample are expected to dangle.

## The finish line (advanced: the fleet's measure)

This section is about the advanced case: many projects, several machines, lanes running side by side. It is not a bar for the core. POGA states the fleet's finish line and measures it in code:

> The harness is mature when a session on any member can take one work item from claim to landed trunk, alongside other sessions, with nothing handed to the operator, and the fleet stays identical while that happens.

`python3 curate/finish_line.py` prints the board: five capabilities, three exit criteria that must all hold at once, and two measures reported without a bar. **Measured on the private working repository when this cut was taken, the verdict was NOT YET: 0 of 5 capabilities green and 0 of 3 exit criteria held.** Run in this tree, the board reads different numbers, because most of its inputs are the withheld working record. The board says why for each line, for example lands taking minutes where the goal is seconds, and the harness growing when it should be flat. This README does not claim the finish line has been reached.

## Runtimes

The substrate is runtime-agnostic by contract ([ADR-0041](adr/0041-runtime-agnostic-substrate-floor-contract-vs-binding.md)). A session can run on a runtime with no start hook ([ADR-0082](adr/0082-lifecycle-inversion-and-per-session-runtime.md), [ADR-0144](adr/0144-canon-reaches-a-hookless-runtime-by-delivery.md)). On 2026-09-20 the runtime acceptance drill ([`drills/runtime-acceptance.py`](drills/runtime-acceptance.py)) wrote one record for each of three major agent runtimes. There was one substrate run, against a disposable repo, and it was rendered once per runtime. The three records carry the same evidence. They are withheld from this cut, because they carry the recording machine's paths and tool versions; run the drill to produce your own.

| Record | Runtime | What it proves |
|---|---|---|
| `2026-09-20-claude-code.md` | Claude Code | Substrate half: lane, interruption, recovery, land. The record says its agent half is a gap in that file's coverage, exercised outside the drill; every session in this repository's record, sampled here, ran on Claude Code. |
| `2026-09-20-codex.md` | Codex CLI | Substrate half. The record says the agent half is exercised nowhere, and that its guards are undeclared, so a lane on it is unguarded. One applied brief records three Codex lanes that finished their code and tests but could not land without a person. |
| `2026-09-20-antigravity.md` | Antigravity | Substrate half only. The agent half is untested and its guards are undeclared. |

The substrate half does not depend on the runtime. The records say so themselves: it "would prove the same thing if this runtime did not exist". So the honest claim is narrow. The lane, interruption, recovery and land machinery is proven. Claude Code is the runtime every recorded session ran on, which is evidence from use rather than from the drill. Codex has done real work in lanes without landing it. Antigravity is untested as an agent.

## Starting

Start with **Try it** above, which links [`docs/first-task.md`](docs/first-task.md). An arriving agent reads [`AGENTS.md`](AGENTS.md). For standing up an Architect on an existing project, read [`PROCESS.md`](PROCESS.md).

## Licence

Apache License 2.0. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
