# Example: a multi-machine installation (fictional)

**This installation is made up.** The person, the hosts, the org and the paths are invented. It shows what the advanced case can look like with most optional capabilities turned on. It is an illustration, not the spec. Nothing here is required, and none of it is part of the core.

For the core — one person, one machine, one project — see [Try it](../../README.md#try-it) in the main README. You need none of this to start.

## The setup

Alex works on a few projects. Alex has three Macs, all in the `Europe/Lisbon` time zone.

| Host | Label | What it does |
|---|---|---|
| `laptop` | `Laptop` | The portable machine. Alex opens sessions here too. Holds clones of the projects. |
| `devhost` | `DevHost` | Where most lanes run. Holds the dev trunk. Nobody logs into it graphically. |
| `runner` | `Runner` | Always on. Takes tagged releases and runs the nightly adoption job. |

**Git is the only bridge.** The hosts share nothing else: no shared disk, no sync service, no remote shell between them. Every fact one host needs from another arrives as a commit on a remote at `github.com/example-org`. A host pulls, works, commits and pushes. A receipt from the runner comes back the same way.

## What is turned on

| Capability | How it is turned on here | File |
|---|---|---|
| Remote sync and GitHub provisioning | Each project was created with `poga init --create-remote`. | — |
| Federation sharing across projects | Every project points at one federation checkout on each host. | [`repo-paths.example`](repo-paths.example) |
| Machine labels | Each host's name maps to a label that session stamps use. | [`session.config.json`](session.config.json) |
| Deploy runner | One project carries a deploy contract. The runner deploys its tags. | [`deploy/deploy.json`](deploy/deploy.json) |
| Scheduled jobs | Two `launchd` jobs, one on `devhost`, one on `runner`. | the two `.plist` files |
| Board hook | A status board starts when a lane opens on `devhost`. | [`poga.local.example`](poga.local.example) |

Parallel dispatch is used on `devhost` only, from a terminal multiplexer. It needs no file of its own.

## The nightly schedule

Both times are local time on the host that runs the job.

| Time | Host | Job | Kind | Why there |
|---|---|---|---|---|
| 02:30 | `devhost` | Serial run of the full suite on the dev trunk | LaunchDaemon | It needs the dev trunk. The host has no graphical login, so an agent could not load. |
| 03:15 | `runner` | Headless adoption of pending briefs | LaunchAgent | It starts the agent runtime, which needs a logged-in session to reach its login. |

The files are [`org.example.poga.nightly-suite.plist`](org.example.poga.nightly-suite.plist) and [`org.example.poga.adopt-runner.plist`](org.example.poga.adopt-runner.plist). They are filled-in copies of the templates in [`deploy/`](../../deploy/README.md). The installers there render a template for the host they run on.

## One day, end to end

1. On `laptop`, Alex opens a session, claims a work item and starts a lane. The session ends before the work is done. The lane's checkpoint is pushed.
2. On `devhost`, a later session resumes the lane, runs `poga test`, and lands it. The trunk is pushed.
3. Alex cuts a release with `poga release cut`. The tag is pushed.
4. On `runner`, the deploy runner sees the new tag, puts it in place, runs the contract's smoke and verify commands, and pushes a receipt.
5. At 02:30 `devhost` runs the full suite serially. At 03:15 `runner` adopts any brief that waited overnight.

Every step crosses between hosts through Git and nothing else.
