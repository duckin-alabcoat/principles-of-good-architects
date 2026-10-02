# deploy/ — federation runtime deployment artifacts

> **Advanced (optional).** Nothing in this directory is part of the core. One project on
> one machine needs none of it. It covers the deploy runner, promotion, and the scheduled
> `launchd` jobs, all macOS-only. The [README](../README.md#optional-capabilities) says what
> turns each one on. The machine roles below describe one multi-machine installation; a
> fictional one is laid out in [`examples/multi-machine/`](../examples/multi-machine/README.md).

Machine-side deployment for federation substrate that runs *outside* an interactive
session. Tracked files here are **path-free templates** and installers (P3); the
concrete, machine-specific artifacts they produce live outside the repo
(`~/Library/LaunchAgents/…`, `/Library/LaunchDaemons/…`) and are gitignored.

## Which machine each unit runs on

The federation ships **four** launchd units, and they do not all live in the same place.
The table below is the one place that says which machine role runs each unit, so no other
record has to restate it (OPS-0009).

| Unit | Machine | Kind | Fires | Why there |
| --- | --- | --- | --- | --- |
| `com.federation.deploy-sweep` | Runner | LaunchAgent | every 600s | the deploy host (`channel.writer_label()`) |
| `com.federation.mail-poller` | every machine holding member repos | LaunchAgent | every 600s | delivers the share it can reach |
| `com.federation.adopt-runner` | Runner | LaunchAgent | 03:15 nightly | spawns `claude -p`, which needs the login keychain |
| `com.federation.gate-inputs` | devbox | Launch**Daemon** | 02:30 nightly | derives the dev trunk; needs no `claude` and no keychain |

**The last row is the exception in two ways, and both are forced.** It is the only unit
that is not the Runner's: deriving the land gate's input record is dev work on the dev
trunk, it spawns no `claude` and reads no keychain, so it belongs on the machine holding
that trunk (WI-0407, OPS-0009). And it is the only **daemon**, because the design assumes
the dev machine is headless: it has no graphical login, and a LaunchAgent loads only at one.
On such a host the user GUI domain is unreachable, so a LaunchAgent cannot be bootstrapped.
`install-mail-poller.sh` detects a headless host and *refuses*
to install an agent there, which is right for the poller — a session-start drain costs
nothing — and wrong for a long serial full-suite run, which cannot ride a session
start. Without a daemon that obligation lapses, so the unit type gave rather than the
obligation. `deploy/install-gate-inputs.sh` is therefore the one installer here that needs
`sudo`, and the only one that writes to `/Library/LaunchDaemons`.

## What triggers a deploy (ADR-0103 D8, as amended 2026-09-13)

**The mail poller does.** `curate/mail-poller.py` already runs every 600s on every machine
and already fetches and fast-forwards the trunk that carries this runner and its registry;
after a refresh that **completed**, it calls `deploy/runner.py --unattended`. There is no
separate deploy-sweep unit to install. `install-deploy-sweep.sh` and its plist template are
superseded and carry a notice — they are kept, not deleted, because deleting an installer
does not retire a job already bootstrapped on a machine nobody can reach from here.

> **This describes the trunk, not every deployed release (WI-0365/WI-0366).** A deploy host
> still running an older release has no `--unattended` and no `--drill`; its CLI is
> `[--version] [--rollback] [--dry-run] [--status] [--offline] [--sweep] [--trigger]`.
> On such a host the scheduled door is `deploy/runner.py --sweep`, fired every 600s by
> `com.federation.deploy-sweep`. The paragraph above takes effect with the release that
> carries it. Host evidence — the deployed program's own `--help`, carried on the channel —
> is how to tell which applies.

`--unattended` is the verb that carries the policy, and `--sweep` is the bare mechanism:

- **Machine gate.** It deploys only on the machine `curate/channel.py` already declares
  resident — `channel.writer_label()`, the same declaration that decides who may write the
  one-writer mail branch. **Not a second key:** two settings that both say "Runner" today
  are two settings that can disagree tomorrow, which is the drift P16 exists to prevent and
  the complaint WI-0217 already records about declaring one fact twice. The poller runs
  everywhere, so without a gate devbox would clone every production system into `~/deploy`
  and repoint the federation's own units at a tree it never meant to serve from. A machine
  that cannot name itself declines. A person typing `--sweep` or naming a system is asking
  on purpose and is answered — the gate is on the unattended path only, matching how
  ADR-0135 narrowed `refuse_unsealed_process_root` for the same reason: a guard that fires
  on correct use is a guard somebody deletes.
- **D9 gate.** If no rollback has ever been drilled on this machine, it runs
  `--drill` first and refuses to sweep if that drill fails. If nothing is deployed yet
  there is nothing to drill, so the first sweep establishes one and the drill falls due on
  the next pass — bounded at one pass, and said out loud.
- **The trigger is a completed refresh, not an advanced trunk.** The promotion is a tag in
  the *member's* remote and arrives whether or not the federation trunk moved. What a
  completed refresh rules out is deploying from a stale, dirty or diverged checkout.

### `--drill` — proving the rollback without an outage

`runner.py --drill [<system>]` redeploys the tag a system is **already running**, forces
its verify gate to fail, and lets the real failure path take over: roll back, re-read the
contract, restart, settle, re-verify. Because it rolls back to the version it started from,
production ends the drill where it began — which is what makes it safe unattended. It then
restores the ledger exactly as it found it and hangs a `drill` record off it.

It proves detection, rollback, re-verification, the ledger write and the escalation. It
does **not** prove that an older tag's contract still parses — rolling back to a genuinely
earlier release would, and was rejected because it parks production on an old version until
a restore step succeeds. `--rollback`, attended, is where that question belongs.

A drill posts **no receipt to the member's Architect**: it ends in `rolled-back`, and the
unexempted rule would push a brief telling a member its release had been withdrawn. The
comms escalation is still written, labelled `DRILL`.

## Adoption runner (ADR-0050) — the R3 agent path

`com.federation.adopt-runner.plist.template` + `install-adopt-runner.sh` schedule the
headless adoption runner ([`curate/adopt-runner.py`](../curate/adopt-runner.py)) as a
**LaunchAgent on the Runner**, fired **nightly at 03:15** by `StartCalendarInterval`
(template lines 45-50).

> Corrected 2026-09-17 (WI-0228): this paragraph said *"polling … every 600s"*, which is
> the **deploy sweep** and the **mail poller**, not this unit. The 03:15 window is
> load-bearing rather than incidental — [ADR-0142](../adr/0142-a-release-candidate-is-a-tag-you-can-canary-but-never-deploy.md)
> D5 names an unmeasured contention between it and the 600s sweep, and a reader who
> believed this sentence would have concluded there was no window to contend with.

**Why a LaunchAgent, on the Runner, in the GUI session.** The runner adopts genuinely-
manual briefs by spawning `claude -p` in each target repo. On the subscription-login auth
path, `claude -p` reads **the subscription login's token** from the login keychain —
reachable only from a process in the user's logged-in Aqua/GUI session. A LaunchAgent
loads there; a LaunchDaemon or a non-interactive `ssh` session does not (a `claude -p`
probe over non-interactive ssh returns *"Not logged in"*). The design assumes the Runner (the deploy
host) is always on, so the scheduled runs fire unattended.

### Install (run on the Runner, in an interactive shell)

```
cd <federation repo on the Runner>
bash deploy/install-adopt-runner.sh
```

The installer renders the template with this machine's `python3`, `claude`, and repo
paths, installs the plist to `~/Library/LaunchAgents/com.federation.adopt-runner.plist`,
`plutil -lint`s it, and bootstraps it into `gui/$(id -u)`. Idempotent.

### Fire once / verify

```
launchctl kickstart -k gui/$(id -u)/com.federation.adopt-runner
cat .session-state/adopt-runner.launchd.out.log
```

A healthy first fire prints the sweep summary. With **no runner-eligible brief in the
fleet it is a no-op** — the expected state until a genuinely-manual (`verify:`-carrying,
non-`attended`) brief is delivered.

### Auth: the subscription-login path and its one human step

The runner **does not** provision auth. On the subscription-login path, the login's token
**expires and only refreshes in an interactive Claude session**. If it is stale when the
sweep fires, the runner **aborts at its auth preflight** and authors a `comms/` `blocked`
note (federation repo) — it never touches or half-applies a brief. Keep the token fresh
by using Claude interactively on the Runner now and then; the next sweep resumes.

For a **fully-unattended** path that never depends on token freshness, export
`ANTHROPIC_API_KEY` in the plist's `EnvironmentVariables` block (metered API billing,
separate from the subscription). The runner code is identical either way.

### Uninstall

```
launchctl bootout gui/$(id -u)/com.federation.adopt-runner
rm ~/Library/LaunchAgents/com.federation.adopt-runner.plist
```

## The federation deploys itself, last (WI-0224)

The federation is the system running the sweep, so it is also a system the sweep deploys.
It has a registry row like anyone else, a contract at `deploy/deploy.json` like anyone
else, and one extra key nobody else has:

```json
"federation": { "remote": "...", "subdir": null, "self": true }
```

**`"self": true` means *this row is the system the runner is made of*.** Deploying it
checks a tag out over the tree this process is executing from. Everything the sweep did
after that would run against source swapped underneath it — lazily-imported modules,
rewritten bytecode, and every subprocess the runner spawns would come from code this run
never read. `sweep_order()` therefore puts the self row **last**. `status()` enumerates through the
same function, so the two surfaces cannot disagree about the order.

Be precise about what that buys: it empties the **sweep's** remaining work, not the self
deploy's own. Its later steps — seeding, the gates, the escalation and receipt writes —
still run in a process whose tree was replaced a moment ago. That residue is real and it
belongs to the cutover decision, not to the order.

Three things about this are deliberate:

- **The flag is declared, not derived from the name.** An alphabetical sweep is not an
  order guarantee: wherever the self row happens to sort, a runner that deployed it before
  any other row would overwrite its own tree and then run every later deploy against
  replaced source. Even where the alphabet happens to land a self system late, that is
  luck the next registered system can take away with nobody noticing.
- **At most one row may carry it.** A runner is made of one tree, so two claims is a
  contradiction; `sweep_order()` refuses rather than picking one and being right half the
  time.
- **A self row may only declare `restart: "none"`, and the runner refuses anything else**
  — before checkout, with the previous version still in place. Every unit of the self
  system runs the runner's own code, and the sweep's own unit is necessarily among them:
  `launchctl kickstart -k` on it kills the sweep issuing the command, mid-deploy, before
  the ledger write, so the next sweep finds the work undone and repeats it forever with
  nothing reporting why.

**There is no in-process re-exec, and that is the answer rather than a missing piece**
(decided in session ~241). All four federation units are scheduled one-shots —
`StartInterval 600` for the sweep and the mail poller, `StartCalendarInterval 03:15` for
the adopt runner, `StartCalendarInterval 02:30` for the gate-input deriver — and a
one-shot re-execs its program at every fire. So the next fire
already runs the new tag. The schedule *is* the re-exec, it is the strategy `restart:
none` was written for, and it fails visibly where a half-replaced process re-execing
itself fails in ways nobody can read afterwards.

## Where a deploy's result goes (ADR-0132)

The **ledger** (`~/.local/state/poga/deploy/<system>.json`) stays on the machine that
deployed — ADR-0103 D7, and not reopened. It records what runs *here*, and a tracked file
two machines both write is WI-0132 exactly.

So a **copy** travels instead. Every terminal outcome — deployed, awaiting-cutover,
cutover-confirmed, smoke-refused, rolled-back, rollback-failed — posts a receipt into
`outbox/to-<system>-arch/` carrying the tag, the rollback target, both gates' output with
their exit codes, the trigger and the timestamps. The outbox is tracked, so the receipt is
**committed and pushed by the runner itself** and reaches the development machine by
`origin`; the mail poller then files it into that Architect's mailbox where it can.

> **And under production it does not go that way at all, deliberately.** Once
> `POGA_FEDERATION_CONFIG` selects an external configuration, `post_result` enqueues through
> `curate/mailqueue.py` into the state root and the worker publishes it; `outbox.publish`
> refuses to commit from a sealed tree. That fork is the whole point — a deploy tree is
> detached, so a commit made inside it belongs to no branch and the next tag checkout
> discards it. **Until a release binds the units to a configuration, the federation's own
> jobs are on the losing side of that sentence** (WI-0365): they run released code with
> production dormant, so their receipts, comms notes and queued mail are written into the
> sealed tree and thrown away. "Silence means unchanged" cannot be read as reassurance
> until the binding lands. The same fork, and the same caveat, applies to the diagnosis
> bundles described under *Asking a deployed system what it is doing*.

A receipt for a run that answered the **cutover** question also carries a `## Cutover`
section, and it is the only place that answer ever travels. `deployed` is reached two
ways — the runner bootstrapped the unit itself under `cutover: auto`, or it found the unit
already pointing at the deploy tree because a person had booted it — and those are
different facts about whether the loop needs a human. For a while they produced
identical receipts, so a member's cutover receipt could not say which happened to it;
the discriminator existed in the ledger and the ledger does not travel. The section says
`performed` / `confirmed` / `refused` / `deferred` / `pending` in prose, names the units,
and is **stamped with the tag it belongs to** — the ledger merges, so an unstamped record
would reappear under the next release as a claim about a cutover that release never made.

Silence there is the normal case: an ordinary redeploy of a system that was already live
prints no section, because nothing about its cutover happened.

Four properties worth knowing before reading one:

- **A no-op posts nothing.** The sweep re-runs every ten minutes; a receipt lands on a
  change in `(status, current, attempted)`, not per invocation. A dry run posts nothing at
  all.
- **A refusal posts nothing either.** A `DeployError` (bad contract, unregistered system,
  git failure) leaves production untouched and the ledger unchanged. The non-zero exit and
  stderr carry it, as they always did.
- **The header is `apply: manual` / `manual-reason: attended` on purpose.** A receipt has
  nothing to apply; the other manual route would hand it to the adopt-runner above, which
  would open a session per deploy to adopt a record that asks for no change.
- **A REFUSED automatic cutover asks for something; plain `awaiting-cutover` does not.**
  They share a status and mean opposite things — a row on `manual` stopping there is the
  runner obeying, while a row that opted into `auto` and was refused is the runner unable
  to finish, and every refusal it can raise is fixed by cutting a corrected release. Only
  the second is marked `blocked` and ends in *Something needs you*.

## Asking a deployed system what it is doing (ADR-0141)

`deploy/diagnose.py` collects, in one read-only pass and on the machine the system runs on:
the **ledger** record, the **contract** and whether it validates, each **unit**'s liveness,
pid and last exit code from `launchctl print`, a **tail of each unit's log** derived from
the plist's `StandardOutPath`/`StandardErrorPath`, and the contract's own **`verify`**.

```
poga diagnose <system>              # prose, on this machine
poga diagnose <system> --json       # the same bundle as data
poga diagnose --all                 # every registered system
poga diagnose <system> --no-verify  # skip the one probe that EXECUTES
```

**It changes nothing.** That is the property the whole thing rests on, and it is why this is
a separate program from `runner.py` rather than a flag on it — every other function in the
runner exists to change this machine. The one exception is the contract's `verify`, which is
run rather than observed; the report says so under its own heading, and `--no-verify` prints
`skipped` rather than a silent pass.

It also means `runner.read_contract` is deliberately NOT reused: that function writes a
`contract-invalid` ledger row on a schema failure, which is right for a deploy and fatal for
an observer. The validator is called directly instead.

### How a bundle reaches devbox

The deploy sweep runs it after every pass and mails the bundle to `outbox/to-<system>-arch/`
on the same path a deploy receipt takes — so it arrives in that Architect's mailbox by
`origin`, with nobody opening a session on the Runner to look.

**A bundle posts on a CHANGE in the stable state**, not once per sweep: deployed tag,
per-unit liveness and last exit code, contract validity, verify exit, ledger status. A
healthy system that stays healthy posts nothing.

**The log text is deliberately not part of that comparison.** Logs gain lines continuously,
so a digest containing them would post on every ten-minute pass and bury the record in
itself. The logs still travel — they are the evidence *in* the bundle, cut at the moment the
stable state moved, which is the moment worth reading them from.

Four things worth knowing before reading one:

- **Silence means unchanged, never unseen.** Every bundle says so at the bottom. If you need
  to know the collector is still running, the sweep's own output is the place to look, not
  the mailbox.
- **What it could not see is listed.** Every probe that failed to answer appends to a
  `gaps` section — a unit whose fields did not parse, a log that could not be read, a
  redaction that fired. A collection that stopped early says where it stopped, so *"this
  machine has no copy of that system"* never renders as *"that system has no units"*.
- **Quoted machine output is redacted.** `launchctl print` can carry the loaded job's whole
  environment and a log can carry anything, while the outbox is tracked and permanent. Every
  quoted byte goes through `scrub.redact` first, the counts are reported in `gaps`, and
  `outbox.post`'s scrub gate still stands behind that as an independent check — nothing here
  passes `force`, so a bundle carrying a class the redactor missed is NOT sent.
- **A diagnosis never fails a deploy.** A collection that could not run says so and leaves
  the sweep's exit code alone.

### What it does not do

**There is no way to ask from devbox for a fresh bundle on demand.** The round trip through
two ten-minute pollers is ten to twenty-five minutes, which is no use to the session doing
the asking — so the design puts the answer in the mailbox ahead of the question instead. The
gap that leaves is real and named: a system whose deploy state has not moved, whose log you
want to read *right now*, still has no answer from here.

**A system with no launchd units gets less.** A member may declare an `apply` command and no
units, so there is no local process to ask about and its `verify` is the only liveness
answer available; the bundle says that in `gaps` rather than printing an empty unit table
that reads like a clean one.

## Trying a release without promoting it (ADR-0142)

```
poga deploy <system> --canary v2.0.0-rc.1
```

Checks that tag out into `~/deploy-canary/<system>`, syncs its deps there, runs **that
tag's own `smoke` command** in that tree, records a verdict, and stops. It restarts no
unit, cuts nothing over, repoints no CLI link, and writes no byte of the production
ledger. `poga deploy <system> --status` prints the last verdict per system, below the
running/available table.

**The canary root is derived, never configured.** It is `deploy_root()` plus an
unconditional `-canary` suffix, so redirecting `POGA_DEPLOY_ROOT` moves the canary with it
and **no configuration exists** in which a canary resolves to a production tree. That is
why *"touches no production tree"* is a property of arithmetic here rather than a promise
about the code — see [ADR-0142](../adr/0142-a-release-candidate-is-a-tag-you-can-canary-but-never-deploy.md)
D1 for why a `canary=True` flag threaded through the deploy path was rejected.

**Release candidates.** `vX.Y.Z-rc.N` is now a tag this runner can spell. It is the only
pre-release form it speaks, it ranks below its own release, and it is deliberately
**unselectable**: `newest_tag` excludes candidates, so a sweep will never deploy one, and
`--version v2.0.0-rc.1` is refused by name with `--canary` offered in the refusal. Tagging
a candidate to try it can therefore never become deploying it.

**Three outcomes, two exit codes.**

| Verdict | Exit | Means |
|---|---|---|
| `CANARY PASSED` | 0 | the tag's own smoke exited 0 in the canary tree |
| `CANARY FAILED` | 1 | it ran and did not pass; the output is in the verdict and the record |
| `CANARY INCONCLUSIVE` | 1 | the tag's contract declares **no** `smoke` command, so nothing was measured — **not a pass** |

**What it deliberately does not do.** It does not seed state: `seed_state` copies from the
machine-local vault and writes a seed map, which are production-shaped writes performed on
behalf of a probe, and a smoke check is specified as a fast offline command anyway. A
contract whose smoke needs seeded state fails here, and the verdict says that is a limit of
the canary rather than a defect in the release. It also does not mail the result home —
that is B2 of the 2026-09-03 brief and a separate item; until it ships, a canary verdict is
readable only on the machine that produced it, like the ledger and for the same reason
(ADR-0103 D7).

**A canary is not a rollback drill.** It exercises the forward path. ADR-0103 D9's
requirement — the failure path, run once for real — is untouched by it.

## Federation release code, persistent state, and mail (WI-0361–0366)

The release checkout is program code. `POGA_FEDERATION_CONFIG` selects external
state, machine-local member lookup files, a dedicated bare transport repository, and
an optional existing authoritative federation inbox. The service refuses a production
configuration that names a mutable Git branch or puts state in a checkout. It does not
create a second inbox on a host that does not own one. See
[`production-state.md`](production-state.md) for the configuration and state inventory.

Producers durably enqueue a scrubbed message under the external state root. One
serialized worker publishes messages to its explicitly owned mail ref, reads only
configured input refs, checks recipient content after delivery, and returns an
acknowledgment on the recipient's mail ref. Publication and delivery are separate
states. The worker retries after interruption, and its external health record exposes
aged mail, corrupt entries, and failures. `curate/mail-poller.py` delegates to this
worker when the production configuration is active; development use remains separate.
The old `runner/mail` branch and merged `origin/main` mailbox view are legacy inputs
for migration, not the new publisher's operating model.

A versioned transition request is reachable through the old runner's `apply` hook.
Its detached finisher waits for that runner to finish, checks the exact old federation
jobs, fences them, inventories pending state and mail, and installs the new jobs and
`poga` link. A checkpoint stores the prior configuration for rollback. The transition
must establish the *installed* old version and prove that version reaches the hook
before activation. Fixtures show compatibility; only a loaded-service probe from the
host establishes what is actually running. The old process directory remains intact
as a recovery archive.

Two approved release cycles, both directions of mail, acknowledgments, loaded job
arguments, member lookup, worker liveness, and absence of writes to the old checkout
must be observed on the running hosts before WI-0366 closes. The read-only evidence
collector records missing observations explicitly. Local fixture results never count as
live acceptance, and OPS-0010's seven-day clock begins only after that acceptance.

Three commands produce the evidence, and none of them can produce another's
([ADR-0146](../adr/0146-rehearsal-and-host-evidence-are-separate-and-neither-substitutes.md)):

```
python3 curate/mailrehearsal.py run          # the local rehearsal, from the real tests
python3 curate/mailacceptance.py snapshot …  # one host's read-only observation
python3 deploy/legacydeps.py --old-root …    # what still points at the retiring checkout
python3 curate/mailacceptance.py check …     # adjudicates; never emits acceptance
```

The rehearsal is the half a machine that cannot reach production can supply, and it is
*run*, not written: each check maps to the tests that already own its mechanism and takes
their exit status, so a mapped test that stops resolving fails its check rather than
passing quietly. `legacydeps` is the only enumeration here of jobs nobody named in
advance — every other probe asks `launchctl print` about a label it already knows, which
cannot answer *what else* still points at the old checkout. Its verdict is about the
machine that ran it; a development machine's `poga` link pointing into the working
checkout is correct there and says nothing about the Runner.

## Retiring a dev clone after cutover (WI-0225)

Once a system runs from `~/deploy/<system>`, its old dev clone is a second copy that can be
edited and silently diverge. `retire_clone.py` answers the one question that gates deleting
it — *does this clone hold anything the remote and the deploy tree do not?* — and **never
deletes anything**. Deletion stays a separate, human, explicit act.

```
python3 deploy/retire_clone.py --system <system> --clone <path to the dev clone>
```

Exit `0` = SAFE-TO-DELETE, `1` = REFUSE, `2` = could not check at all. It writes a receipt
and prints the `poga work edit … --append-notes-file` line that files it.

**It does not use `git status --porcelain --ignored`,** which WI-0225's acceptance
originally prescribed. That command collapses an ignored *directory* into a single entry
and never descends: measured on a real clone it reported **3** entries where
`git ls-files -o -i --exclude-standard` reported **14 named files**, and the eleven it hid
included the only copy of a machine's `architect-learnings`. An operator following the
prescription in good faith would have enumerated `.session-state/` as one path and deleted
it. A directory git *declines* to descend into (a nested repo) is reported `OPAQUE` and
refuses — never counted as one satisfied path.

The dispositions live in `clone-retirement.json` (withheld), one written
reason per rule. `blocking` outranks `disposable` unconditionally, so a later broadened glob
cannot swallow a producer file. **The system list is closed** — one real member is excluded
by an operator ruling, another for want of a cutover receipt — and an unlisted system
is refused rather than checked against empty rules, because empty rules would read as a
survivable refusal instead of *"nobody approved deleting this."*

Two traps it refuses rather than absorbs. Pointed at a **subdirectory** (a repo whose
code lives in a subdirectory of its root) `ls-files` would scope to it and report absence for everything above; the check names the real root instead. And a
**missing path is not evidence of deletion** — both clones were *moved* into a differently
named directory and the leaf names were never recorded, so a
`test -e` that exits non-zero cannot tell deleted from moved.
