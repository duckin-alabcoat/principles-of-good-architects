# ADR-0103: Promotion is a tag; the Runner runs a per-repo deploy contract

**Status:** Accepted
**Date:** 2026-08-25
**Deciders:** Federation Architect (the tag gate, the contract schema, the separate deploy tree, the ledger, the rollback posture). the operator ruled the pilots' first version numbers and, after the trade-off was laid out, delegated the deploy-tree question to the Federation Architect.
**Work items:** WI-0178 (this job). Fans out to per-system contract items.
**Source brief:** `proposed-edits/federation-arch/applied/2026-08-23-consultant-promotion-pipeline.md` (withheld)

## Context

On 2026-08-23 the operator asked how a new version developed on devbox ends up as a running
program on the Runner. At the time the answer was **nothing does it** — a human
remembers to `git pull` in the right folder and restart the right service, or the new
version simply never runs. The pilot systems were measurably in that state: each was serving a commit
several behind what origin held, and none had ever carried a git tag.

Three constraints shape the answer.

**The machines must stay decoupled.** All development moves to devbox, which in the
session model is a rebuildable development machine. A promotion path that requires
devbox to reach into the Runner — ssh, rsync, a push to a working tree — makes the
transient machine load-bearing and needs credentials in the direction that is hardest to
audit.

**Nothing may be asked of the operator.** [`never-route-your-own-work-through-the-user`](../habits/master.md#never-route-your-own-work-through-the-user)
is canon as of session ~166. A deploy story whose steps live in a runbook the operator executes is
the failure this federation was told three times to stop.

**The running program is currently the developer's working tree.** A pilot's service
definition names a repo folder as its working directory. That folder is where an Architect session also
works. There is no separation at all between "what is checked out" and "what is running".

## Decision

**D1 — The git remote is the only bridge.** No devbox→Runner ssh, rsync, or push. Every
promotion transits `origin`. Neither machine holds credentials for the other; every
promotion is a git object with a signature-shaped audit trail; backup-by-push is preserved.

**D2 — The Runner runs tags, never branch tips.** `if it isn't tagged, it isn't deployable.`
Deploying `main` would make every push a production deploy, which is accidental promotion
by construction. **Cutting the tag is the promotion decision** — the deliberate human-scale
act — and everything after it is machinery. A hotfix is a patch tag through the same gate;
there is no fast path, because a sanctioned fast path is how a gate dies.

> **AMENDED by [ADR-0142](0142-a-release-candidate-is-a-tag-you-can-canary-but-never-deploy.md) D2/D3** — the tag vocabulary now also
> admits a release CANDIDATE, `vX.Y.Z-rc.N`. It is **matchable**, and therefore
> canary-able; it is never selected by `newest_tag`, and it is refused by name on the
> production path. Everything above stands unchanged: cutting the *release* tag is still
> the promotion decision, and a candidate is precisely the tag that says that decision has
> not been made yet.

**D3 — Production is a separate checkout, never the developer's tree.** Each deployed system
gets `~/deploy/<system>`, a clone of its own repo held in detached HEAD at the deployed tag,
and its LaunchAgent is repointed there once. The developer's clone is left exactly as it is,
on a branch, editable.

The alternative — pinning the existing clone to the tag — was rejected for a reason that is
structural rather than tidy: it leaves the *only* copy of the code in permanently detached
HEAD in the folder agents and humans both work in, so a stray edit is live in production the
instant it is written and a later session fighting the detached state can silently discard a
deployed tag. A separate tree converts *"nothing edits production"* from a rule someone must
remember into a property of the filesystem.

**Gitignored runtime state is the cost, and it is paid explicitly.** A tag checkout does not
touch ignored files, so state that lives inside the tree (a JSON state file, for example)
survives deploys and rollbacks once it exists in the deploy tree — but it has to get there
the first time. The contract declares that state in `state:`; the runner **copies, never
moves**, on first deploy, and leaves the developer-tree copy untouched as a fallback.

**D4 — The runner is plain code, and it is one program.** A deploy is deterministic:
fetch → verify tag → checkout → sync deps → smoke → restart units → verify → write ledger.
No `claude -p` anywhere on the happy path ([P15](../principles/master.md#p15--code-for-mechanism-not-judgment)).
That also means the deploy verb works headless and over SSH, unaffected by the Aqua-session
login constraint that binds the adopt-runner. An LLM session enters only when a deploy
has already failed and its escalation summons one. Prod changes are executed by code and
*witnessed* by agents, never performed by them.

**D5 — All system knowledge lives in the system's repo, in `deploy/deploy.json`.** The runner
is generic; the contract is versioned alongside the code it deploys, so a release that changes
how it must be deployed carries that change in the same commit. Schema in
[`deploy/contract.schema.json`](../deploy/contract.schema.json); fields:

| field | meaning |
|---|---|
| `system` | system id, must match the directory it deploys into |
| `units` | launchd **labels** this system owns (never plist filenames) |
| `deps` | how to materialize the runtime env — one of the schema's supported installers, or `none` |
| `state` | gitignored paths that must survive, seeded on first deploy |
| `smoke` | fast command that must exit 0 **before** a restart is kept |
| `verify` | post-restart aliveness check |
| `apply` | optional override for non-launchd systems (cloud, CLI-only) |

**D6 — A deploy that cannot prove it worked is rolled back.** Smoke fails → nothing is
restarted, the tree returns to the previous tag, nothing was disturbed. Verify fails after
restart → re-checkout the previous tag, re-sync, restart, confirm the *old* version is
healthy, then escalate loudly. The runner never leaves a system half-deployed silently and
never retries its way past a failing smoke. **Deploys are boring or they are loud; there is
nothing in between.**

**D7 — The ledger is the answer to "what is running where".** One record per system —
current version, previous version, timestamp, trigger, smoke/verify results — at
`deploy/ledger/<system>.json`. `previous` is what makes rollback a one-verb operation, and
the ledger is what makes the question answerable without remembering. A system that has
never deployed reads **unknown**, never *"up to date"*
([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)).

**D8 — Two triggers, one path.** `poga deploy <system> [version]` on demand, and a ~~nightly~~
**600s polling** reconcile sweep that compares each registered system's newest tag against the
ledger and applies anything newer. Both call the same function; the sweep is not a second
implementation. `poga deploy <system> --rollback` is the same verb pointed at the ledger's
`previous`.

> **AMENDED by [ADR-0142](0142-a-release-candidate-is-a-tag-you-can-canary-but-never-deploy.md) D4** — the word *nightly* is withdrawn. The cadence
> changed to `StartInterval 600` with the 2026-09-02 addendum
> (`deploy/com.federation.deploy-sweep.plist.template:71`) and this sentence was never
> updated, so the record and the artifact disagreed about the trigger for two weeks.
> Everything else in D8 stands; polling often is safe because **the sweep is not the
> promotion event** — the tag is.

**D9 — The sweep is installed only after the on-demand path has succeeded twice and
a rollback has been drilled.** An unattended deployer whose failure path has never run is
[`verify-in-the-created-configuration`](../habits/master.md#verify-in-the-created-configuration)'s
exact subject.

> **AMENDED by [ADR-0142](0142-a-release-candidate-is-a-tag-you-can-canary-but-never-deploy.md) D5** — the gate stands and gains one named,
> unmeasured precondition: a 600s sweep fire may land inside the adopt-runner's nightly window
> (`deploy/com.federation.adopt-runner.plist.template:45-50`), both write git state, neither
> knows about the other, and **the contention has never been measured**. It is inert only
> while this gate is closed, so it goes live on exactly the day someone decides the gate is
> satisfied. And a canary is **not** a rollback drill: it proves the forward path, while
> this clause is about the failure path.

**D10 — Releases carry code only.** Secrets stay in the production machine's secret store;
per-machine config stays in gitignored `.local` files, which deploys never touch. A release
that needs a new secret to boot fails its smoke — the runner does not prompt, because
prompting is the thing this whole ADR exists to delete.

**D11 — Ownership.** The runner, the schema, the ledger and this ADR are the Federation
Architect's. Each **per-system contract belongs to that system's Architect**, delivered as a
work item once the schema exists. The pilot contracts were written directly by the
federation as a one-time bootstrap, and each pilot Architect is told so in its inbox — a
bootstrap act, not a precedent for the federation editing member repos.

## Alternatives Considered

**Deploy the branch tip on every push.** Simplest, and it is what "just pull" means today.
Rejected: it removes the promotion decision entirely — every landed commit becomes a
production deploy, including the ones a session lands at 2am mid-refactor.

**Pin the existing clone to the tag** (no separate deploy tree). Smallest change, no plist
edits, no state to seed. Rejected under D3: production and the workspace stay the same
directory, so the gate protects nothing against the most likely source of breakage, which is
An agent session in that very folder.

**A git worktree instead of a second clone.** Shares the object store, so one fetch serves
both. Rejected: production would share a `.git` with a tree agents mutate, and the lane
machinery already treats worktrees as its own namespace — a production worktree would appear
in `poga lanes` output as something reapable.

**devbox pushes to the Runner directly.** Fastest path from "landed" to "running". Rejected
under D1: it makes the deliberately-transient machine hold Runner credentials and puts an
un-auditable, un-taggable channel between development and production.

**An LLM session performs the deploy.** Rejected under D4 — a deterministic procedure routed
through a non-deterministic actor inherits skipping, drift and fabrication for zero upside,
and would drag the whole thing back under the Aqua-session login constraint.

## Consequences

- *"What version is running?"* becomes a file anyone can read rather than something someone
  remembers. The dashboard row is derived from the ledger.
- Landing on `main` stops being a deploy. Systems that were being deployed by accident will
  stop moving until someone tags them — which is the point, and which will surface as a
  visible gap for any system whose Architect never cuts a release.
- Every member now owes a `deploy/deploy.json` or an explicit declaration that it deploys
  nothing. Absent contracts must read **absent**, never *"nothing to deploy"*.
- Each pilot's service units change their working directory once, and any in-tree state
  file is copied into the deploy tree. The developer-tree copy is left in place; retiring it is a
  later, separate decision.
- The federation must eventually deploy itself through this path (self-hosting, ordered last
  in the sweep, re-exec after its own update). Recorded here, deliberately not built in this change.
- **A refused remote repository probe is D1 working, not a guard defect.** From a lane, a
  hand-written remote-shell command that would ask the *other* machine about its repository is
  refused by the runtime's own worktree-isolation and command classifiers. Twice — sessions
  ~178 and ~185, both recorded in WI-0216 — that refusal was read as a bug in the federation's
  `check-bash` guard and filed for repair. It is neither. `check-bash` *allows* those payloads
  (measured session ~187: both reported shapes exit 0 with no output and fall through to the
  normal permission prompt), and the capability the proposed repair would restore is precisely
  the devbox→Runner ssh channel D1 forbids and this ADR's alternatives explicitly reject. The
  refusal is the policy holding, arriving from an unexpected direction. **Ask `origin` instead,
  from the machine whose answer you want** — reachability, the newest tag, and the other trunk's
  position relative to yours are all local questions there (`ls-remote --exit-code origin HEAD`
  confirms the transport works rather than inferring it). The facts `origin` genuinely cannot answer — the other machine's unpushed
  commits, working-tree state, live lanes, and deploy ledger — are not this ADR's to solve and
  are carried by WI-0213, WI-0154, WI-0229 and WI-0230.

## References

- Consultant brief, 2026-08-23 — *Promotion: how a version developed on devbox becomes a
  running program on the Runner*.
- [ADR-0050](0050-headless-background-adoption-runner.md) — the headless Runner runner and its escalation posture.
- [ADR-0069](0069-shared-numbers-are-compiled-or-drawn-never-declared-or-picked.md) — why a version is drawn/declared, never picked.
- [ADR-0078](0078-version-sets-are-planned-releases-are-harvested.md), [ADR-0081](0081-a-major-is-declared-not-derived.md) — releases are harvested; a major is declared by a human.
- [ADR-0099](0099-the-user-is-not-an-execution-surface.md) — a denied hand-run becomes a verb.
- The 2026-08-22 machine-role ruling — all development moves to devbox; the development machine is rebuildable, not restorable.

## Amendment — the poller is the trigger and the drill is code (session ~328, 2026-09-13, WI-0316)

D8 and D9 were written for a Runner somebody logs into. They describe a nightly launchd
sweep, installed by hand once its safety evidence exists. Neither half survived contact
with the machine, and the failure was silent in the way that matters: nothing went red,
one member simply sat deployed-and-not-running for days while every surface
reported the pipeline as built.

**What actually happened.** D9's gate can only be opened by evidence in the ledger; the
only way to produce that evidence was for a person to break a smoke check on purpose on the
Runner; and per the operator's 09-05 ruling that federation architect sessions run on devbox only, so
there is no Runner session, ever, to do it. The adopt-runner — the one unattended Runner
executor — skips `manual-reason: attended` briefs by design and had lost its
authentication. The gate was therefore not strict, it was **shut**, and D9 as written had
no reachable path to satisfying itself. A gate whose only key is held by someone who has
said they will not turn it is a deadlock wearing a safety argument.

**D8 is amended: the trigger is `curate/mail-poller.py`, and the verb is `--unattended`.**
The poller is already plain code, already on a 600s schedule, and already does the fetch
and `--ff-only` that syncs a checkout with the trunk carrying the runner and registry. It
now calls `deploy/runner.py --unattended` after a refresh that completed. No new launchd
unit, no `launchctl bootstrap`, no Runner session, nobody asked. `install-deploy-sweep.sh`
and its plist template are **superseded, not deleted** — deleting the installer would not
retire a job already bootstrapped on a machine this repo cannot reach, so they stay in
place carrying a notice, and the runner tolerates either trigger.

Two corrections of record travel with this. **D8 says "nightly" and the shipped schedule is
every 600 s** — changed by the 2026-09-02 brief addendum on the argument that the sweep is
not the promotion event (D2 makes the tag the promotion), so polling more often shortens
latency without creating promotions. The ADR was never amended to match; it is amended now.
And the trigger fires on a **completed refresh, not on an advanced trunk**: the promotion is
a tag in the *member's own* remote, which arrives whether or not the federation trunk moved,
so gating on movement here would couple every member's release to an unrelated commit.

**D9 is amended: the drill is `runner.py --drill`, and the gate moved from install time to
run time.** The drill redeploys the tag a system is already running, forces the verify gate
to fail, and lets the untouched production failure path take over — roll back, re-read the
previous tag's contract, restart, settle, re-verify for real — then restores the ledger and
records the evidence. Because the version it rolls back *to* is the version it started
*from*, production cannot end a drill anywhere but where it began, which is what makes it
safe to run unattended on a live box with nobody watching.

- **What a drill proves:** that a verify failure is detected, that the rollback executes,
  that the restored version is re-verified rather than assumed, that the ledger and the
  escalation are written. **What it does not prove:** that an *older* tag's contract still
  parses, which only rolling back to a genuinely earlier release would exercise. That was
  rejected deliberately — it parks production on an old release until a restore step
  succeeds, and a drill that can cause the outage it was written to predict is worse than
  the unproven claim it retires. `--rollback`, attended, is where that question belongs.
- **The injection is one verdict at one gate**, and it is the *verify* gate, not the smoke
  gate the original recommendation named. A smoke failure returns at `smoke-failed` and
  never writes `rollback_verify` — the exact key the evidence test reads — so a
  smoke-injected drill would have run, reported success, and left the gate shut.
- **A drill posts no result to the member's Architect.** Every other outcome of a deploy
  does (WI-0230), but a drill ends in `rolled-back`, so the unexempted rule would commit
  and push a brief telling a member that its release failed verification and was withdrawn.
  The escalation is still written, labelled `DRILL`, because the escalation is part of what
  is being proven.
- **A contract with no `verify` block is refused before anything is touched.** There is no
  gate to force, and the rollback would then be accepted with nothing confirming the
  restored version is healthy.

**Where this is weaker than the original, stated rather than glossed.** On a machine where
nothing has ever deployed there is nothing to drill, so requiring the drill first deadlocks
exactly as before. The amended rule lets the first sweep establish a deployment and makes
the drill due on the very next pass. For one pass, an unattended sweep runs on a failure
path that has never executed. That is a real weakening of D9's property, it is bounded at
one pass, and `--unattended` says so out loud rather than passing over it. If the drill
then fails, **no sweep runs at all** and an escalation is written: an unattended deployer
whose recovery path has just been demonstrated broken is the one thing worse than one that
was never tested.

**The rejected alternative, recorded because it was a decision.** the operator runs
`install-deploy-sweep.sh --force` once on the Runner. One command, and the pipeline is
live. Rejected: it is the routed-through-a-human step this ADR exists to delete (a
WI-0159-counted defect), the ruling is that there are no Runner sessions, and `--force` would
skip the evidence rather than produce it — trading the deadlock for an unproven claim
instead of resolving either.

**The unattended trigger is gated on the resident machine — the one the channel already
declares.** The sweep's machine gate used to be *installation*: `install-deploy-sweep.sh`
was run on the Runner and nowhere else, so the launchd job's existence was the gate. The
poller runs on **every** machine by design, so moving the trigger there deletes it — and an
ungated sweep on devbox would clone every production system into `~/deploy`, kickstart
units that are not loaded there, and repoint the federation's own units at a tree it never
meant to serve from.

The replacement is a declaration rather than an inference, and **not a new declaration**.
This lane first added a `deploy_host` key to `deploy/registry.json` and a private
`this_machine()` to `deploy/runner.py`; [ADR-0135](0135-a-trunk-clone-is-never-a-process-root.md)
landed underneath it mid-lane with both already built —
`channel.writer_label()` (`POGA_CHANNEL_WRITER`, default `Runner`) naming the machine that
does the federation's unattended work, and `common.this_machine()` naming the machine we are
on, the latter carrying a docstring that commits the project to exactly one implementation.
Two keys that both say "Runner" today are two keys that can disagree tomorrow — the drift
[P16](../principles/master.md#p16--avoid-duplication) exists to prevent, and the precise
complaint WI-0217 already records about declaring one fact twice. So the gate reads the
channel's declaration, and the registry gained no key. If the deploy host ever has to differ
from the channel's writer, that is a split to make deliberately at that moment rather than a
second key kept ready for it.

It fails closed on a machine that cannot name itself, because not knowing where you are
standing is a reason to withhold an unattended deploy, never to grant one. It gates
`--unattended` only — a person who types `--sweep` or names a system is asking on purpose —
which is the same narrowing ADR-0135 applied to `refuse_unsealed_process_root`, for the same
stated reason: *a guard that fires on correct use is a guard somebody deletes.*

**Also corrected: D7's ledger path.** D7 says the ledger lives at `deploy/ledger/<system>.json`,
in the repo. It does not and should not — `ledger_dir()` resolves to
`~/.local/state/poga/deploy/<system>.json`, machine-local and untracked, because it records
what is running on *this* machine and a tracked file two machines both write is WI-0132's
defect. The implementation and `deploy/README.md` have always agreed with each other; only
D7's sentence was wrong. Noted here rather than edited in place, per P2.
