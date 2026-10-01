# ADR-0050: Headless background adoption runner — the agent path for genuinely-manual briefs

**Status:** Accepted
**Date:** 2026-07-15
**Deciders:** the operator, Federation Architect

## Context

[ADR-0049](0049-apply-auto-is-the-authoring-default.md) established that a redistribution
brief has exactly two *automatic* destinations, and `manual` is a routing label between
them, never a request for the operator's interactive time:

- **the code path** — strict-schema `Apply: auto`, applied by `session.py apply-briefs`
  before the model wakes (the default; most briefs); and
- **the agent path** — the residue that genuinely can't be strict-schema'd (substrate
  installs, multi-file migrations, real target-side judgment), run by a headless adoption
  runner, unattended.

R1 (session 66) shipped the code path fleet-wide; R2 (session 67) made `auto` the authoring
default and redrafted the whole comms wave onto it. The code path is done. **The agent path
was named in ADR-0049 but not built** — so today a genuinely-manual brief has nowhere to go
but a member's interactive startup, where it surfaces to the operator. That is the last piece of the
residue zero-touch is chartered to remove (the operator's session-67 ruling: nothing stays manual;
every adoption is automatic and carried by code).

The consultant's `2026-07-14-zero-touch-adoption` R3 specifies the shape: run the manual
residue as headless `claude -p` sessions on the always-on Runner, liveness-guarded so they
never race an interactive session, and gated on a declared `Verify:` command so an
unattended adoption is never trusted blind.

Doctrinally this is [ADR-0029](0029-receiving-architects-auto-adopt-at-startup.md)'s move one
level up. ADR-0029 established that the user's approval already happened upstream at
registry-Accept, so a receiving Architect's adoption is *execution*, not a second gate.
ADR-0039 made *routine* execution happen in code at startup. This ADR makes the
*non-routine* execution happen in an unattended agent. In both, **when and where** execution
runs is operating substrate the Architect owns ([P10](../principles/master.md#p10--architect-owns-operational-substrate) /
[P15](../principles/master.md#p15--code-for-mechanism-not-judgment)); the human gate is not
re-litigated. Headless token cost trades against the operator's interactive time — the scarcer
resource, per the ruling.

## Decision

**Build `curate/adopt-runner.py`: a federation-owned sweep that adopts runner-eligible
manual briefs as headless `claude -p` sessions in each target's own repo, on the always-on
Runner, unattended.** The runner is idempotent, liveness-guarded,
`Verify:`-gated, **commit-only**, and resets any adoption that fails to verify.

### 1. Eligibility — the manual partition becomes total

A brief's apply mode stays the two values ADR-0039/0049 already define — `auto` and
`manual`. `manual` now partitions **totally** into the two paths by two header fields:

- **agent path** — `apply: manual` carrying a non-empty `verify:` command **and** a
  `manual-reason` that is *not* `attended`. The runner adopts it.
- **the operator's path** — `apply: manual` with **no** `verify:`, **or** `manual-reason: attended`.
  The runner skips it; it surfaces at the member's next interactive startup exactly as manual
  briefs do today. `attended` is the reserved escape hatch for work that must run under a
  human's eye (e.g. A member's runtime migration with live state).

`attended` stays a **`manual-reason` value, not a third apply mode** — the engine and the
[ADR-0049](0049-apply-auto-is-the-authoring-default.md) lint already key on `manual-reason`,
so a third mode would be schema sprawl for no gain. The `check-apply.py` delivery lint is
extended to enforce the partition: a `manual` brief must carry either a `verify:` field or
`manual-reason: attended`. A `manual` brief with neither is a delivery error — it would fall
into a gap where no path owns it.

### 2. The runner is Claude-runtime-specific

The runner adopts by spawning `claude -p` — it can only open a target **as a Claude-Code
member**. A non-Claude member (one on another runtime)
is **skipped**, never adopted by this runner: its manual residue is worked by its own
runtime's session or surfaces there. The runner filters targets to `claude-code` runtimes
via `repo_runtimes()` (ADR-0041), the same guard the fleet push uses. A symmetric
agent path for a non-Claude runtime is a future ADR if the need appears; this one does not
pretend to cover it.

### 3. Liveness guard (ADR-0036) — never race a session

Before adopting in a repo, the runner reads that repo's own `.session-state/*.live` sidecar
([ADR-0036](0036-session-liveness-sidecar-and-orphan-auto-triage.md)). If any session shows a
heartbeat fresher than the stale threshold, the repo is **skipped this sweep** — the runner
never opens a session that could race an interactive (or another headless) one. The headless
session the runner spawns itself runs `session.py start`, which writes and heartbeats a
`.live`, so concurrent runner invocations self-guard through the same sidecar. Single writer
per repo per moment ([P13](../principles/master.md#p13--single-writer-per-state)) holds.

### 4. Commit-only — no autonomous push

**The runner commits; it does not push.** The target's next interactive session auto-pushes
its ahead-of-origin commits ([ADR-0035](0035-startup-turn-budget-inject-and-git-diagnosis.md)
ahead→auto-push). This is deliberate:

- The one **irreversible, outward** act in the close protocol is the GitHub push. Keeping it
  off the unattended path means every runner action is a **local commit** the federation (and
  the operator) can inspect in `git log` and undo with `git reset` — nothing leaves the machine
  without a human-run session in the loop.
- It sidesteps the non-interactive credential gap entirely (a headless session
  can't authenticate a push anyway — see `comms/2026-07-15-fleet-substrate-push-unbacked.md`).

The headless session closes through the normal `session.py end` harness (handoff entry,
STATUS, commit) but is invoked **without** `--push`.

> **Amended by [ADR-0134](0134-a-land-publishes-or-says-it-did-not.md) (WI-0355, 2026-09-13).**
> The decision above is unchanged — the one outward act still stays off the unattended
> path — but the mechanism inverted. `end` now publishes by default, so "invoked without
> `--push`" would publish. The runner is invoked with **`--no-push`**, which states the
> same rule in the form that survives a default change instead of inheriting it from one.

### 5. Verification contract — adopt, verify, or reset

For each eligible brief in an idle target:

1. **Record** the pre-adoption commit (`git rev-parse HEAD`).
2. **Adopt** — run `claude -p "<adoption prompt>"` in the repo. The prompt hands the agent the
   brief path and instructs it to work the brief *as that Architect* (stay-in-role,
   [P4](../principles/master.md#p4--identity-boundaries-non-collapsing)) and close through its
   own session-end harness, commit-only.
3. **Verify** — run the brief's `Verify:` command in the repo. Non-zero exit (or a `claude -p`
   error / timeout) fails the adoption.
4. **On pass** — leave the commit in place. The brief has already been filed `pending/` →
   `applied/` by the adoption session. Record the outcome for the report.
5. **On fail** — `git reset --hard <pre-adoption HEAD>` (restoring the working tree, the brief's
   `pending/` location, and the handoff), **keep the brief pending** with a failure note, and
   author a `comms/` note of type `blocked` ([ADR-0046](0046-per-system-comms-surface.md)) in
   the target repo so the failure surfaces to the operator on the ADR-0046 rail. The brief is never
   half-applied ([P18](../principles/master.md#p18--verify-everything)).

#### 5a. What a `Verify:` command owes its reader — the verdict contract

*Amended 2026-09-13 (WI-0304 B1), from the 2026-08-05 external consultant health check.
Federation-side only: this changes what the runner RECORDS, not the appliable-brief schema
every member authors against, so no member needs to do anything.*

**A verify command that fails owes a ONE-LINE HUMAN VERDICT naming what was checked and
what was absent — never a stack trace.** A member may run an MCP server, and a brief may
verify its config with a dictionary lookup. If that lookup raises, a runner that records the
last few hundred characters of a traceback logs a fragment that begins mid-token and ends
`KeyError: '<key>'` on every sweep. Nobody reading that learns *what condition* failed,
only that Python objected somewhere.

Step 3 above hid **two different facts behind one exit code**, and step 5 recorded both the
same way:

- **the verify ran and said no** — the adoption failed. The brief is sound, the work is
  wrong, retrying is meaningful, and three consecutive failures is real evidence (the
  dead-letter counter, WI-0084).
- **the verify broke** — nothing was checked. The brief is **DEFECTIVE**. The adoption's
  fate is *unknown*, not bad, and retrying is waste: tomorrow's sweep raises the identical
  exception.

So the runner now classifies every verify run `pass` / `failed` / `defective`, and:

- **the recorded detail is always a composed one-line verdict**, never raw output. The raw
  output is demoted to the JSONL outcome record — preserved as evidence, removed from the
  headline. Classification runs on the **complete** output, because truncating first is
  what made the original fragment unreadable.
- **`defective` is the brief's defect, not the member's**, so its `comms/` note addresses
  the brief's author and says the fix is a re-authored `verify:`.
- **`defective` quarantines on the FIRST sweep**, not the third. The threshold of three
  buys evidence for *this will not start working on its own*; a crashed check supplies that
  in one sweep, and attempts two and three each cost a full `claude -p` session to learn
  nothing. The release path is unchanged and needs no new machinery: the ledger is keyed on
  brief content, so a corrected brief is eligible on the next sweep.
- **an unverified adoption is still never kept.** `defective` resets exactly like `failed`.
  Only the recorded fact changes; keeping an unverified unattended edit is the blind trust
  this whole section exists to refuse.

`curate/check-apply.py` enforces the half that is provable before delivery: a **bare
`assert` in a `python -c` one-liner** can only ever emit `AssertionError`, naming nothing,
so it is refused at delivery. It deliberately does not try to prove a lookup cannot raise —
a nested subscript such as `m['mcpServers']['<key>']` cannot be shown safe
statically, and a rule that guessed would either be noise or a false clearance. That case
belongs to the runner at runtime, where the answer is real.

**Not covered here:** a brief that is merely *waiting* on an unlanded prerequisite, which
also reads as a failure today. That needs a `requires:` precondition in the appliable-brief
schema — a fleet-facing change carrying its own ADR, standard-version rung and rollout —
and rides the queued standard bump rather than this amendment.

### 6. Report-after, not gate-before (P7)

Adoption results are **reported**, not gated. Each adoption's outcome lands in the target's
own `comms/` / handoff / STATUS and in its next interactive startup orientation, with the git
trail to revert. The runner prints a one-line-per-target summary when run. No result waits on
the operator before it happens; the reversibility (commit-only + reset-on-fail) is what makes
report-after safe.

### 7. Trigger

The default trigger is a **scheduled sweep on the Runner** (a launchd `LaunchAgent`, the
always-on box). The runner is idempotent — a repo with no eligible brief is a no-op, a live
repo is skipped — so re-running it is always safe. A delivery-time trigger (kick a sweep when
a manual brief is delivered) is a possible future optimization, not built here. The runner is
also runnable by hand for the pilot and for on-demand sweeps.

## Alternatives Considered

- **Leave the manual residue to surface at each member's interactive startup (status quo).**
  Rejected — that *is* the residue R3 removes. ADR-0049 already routed `manual` away from the operator
  in doctrine; without the runner the doctrine has nowhere to route to.
- **A third apply mode `attended`.** Rejected — keep two modes; `attended` is a reserved
  `manual-reason`. The engine and lint already branch on `manual-reason`; a third mode
  duplicates that partition in a second dimension for no gain.
- **The runner pushes autonomously.** Rejected — the push is the one outward, irreversible act;
  keeping it off the unattended path bounds the blast radius to local, resettable commits and
  dodges the non-interactive auth gap. ADR-0035 ahead→auto-push carries the commits to
  GitHub at the target's next real session, from an authenticated environment.
- **Adopt in-process — the federation reads the brief and applies it with its own model,
  rather than spawning a real target session.** Rejected — the target must adopt *as itself*,
  through its own harness, producing a real session/handoff/STATUS in its own repo
  ([P4](../principles/master.md#p4--identity-boundaries-non-collapsing) stay-in-role). A
  federation-side apply would impersonate the receiving Architect and leave no session trail in
  the target.
- **Trust the adoption without a `Verify:` gate.** Rejected — an unattended agent edit with no
  verification is exactly the blind-trust failure [P18](../principles/master.md#p18--verify-everything)
  and [`exercise-delegated-work-end-to-end`](../habits/master.md#exercise-delegated-work-end-to-end)
  forbid. No verify command ⇒ not runner-eligible ⇒ surfaces to the operator.

## Consequences

- **Zero-touch is closed.** Interactive startup adoption cost is zero turns: routine briefs
  apply on the code path before the model wakes (R1/R2); the genuine residue is adopted on the
  agent path by the scheduled sweep (R3). Only true exceptions reach the operator — version drift, a failed verify,
  or an `attended`-class brief.
- **The manual partition is total and self-documenting.** Every `manual` brief declares its
  route in its own header: `verify:` → agent path, `attended` → the operator. The lint refuses a brief
  that declares neither, so nothing lands in an unowned gap.
- **Token cost moves to the Runner's scheduled sweep.** Each adoption is a full `claude -p` session.
  That cost is deliberately traded against the operator's interactive time (the scarcer
  resource) and is bounded per sweep by the number of eligible briefs.
- **Every runner action is reversible.** Commit-only + reset-on-verify-fail means a bad
  adoption is a local commit visible in `git log` and undone by `git reset`; nothing reaches
  GitHub without an interactive session in the loop.
- **Failures surface on the ADR-0046 rail.** A verify-fail authors a `blocked` comms note in
  the target and keeps the brief pending — the member picks it up (or the operator does) at the next
  interactive session, informed by the note.
- **The Claude-runtime boundary is explicit.** Non-Claude members are skipped, not silently
  mis-adopted. Their agent path, if needed, is a later ADR.
- **Pilot before widening.** The runner is dogfooded on one low-risk Claude member
  with a real manual brief before any scheduled fleet sweep. The launchd
  trigger is armed only after the pilot runs clean.

## Update note — auth path (2026-07-15, session 68, at build)

The runner spawns `claude -p`, which authenticates headlessly by **API key only** —
`claude`'s own `--bare` contract: *"Anthropic auth is strictly `ANTHROPIC_API_KEY` or
`apiKeyHelper` via `--settings` (OAuth and keychain are never read)"* in bare mode, and
even a normal `claude -p` reads the subscription login's token, which is only reachable
from a process **in the user's logged-in GUI session**. Two deployment paths follow:

- **Subscription path (chosen, session 68).** Run the sweep
  as a **LaunchAgent in the Runner's GUI session** ([`deploy/`](../deploy/)), where
  `claude -p` reads the subscription login's token and bills the sweep to the existing interactive
  subscription. Its one human dependency: that token **expires and refreshes only in
  an interactive session**, so an unattended sweep can meet a stale token. The runner makes
  that safe — an **auth preflight** aborts the sweep and authors a `comms/` `blocked` note
  the moment auth fails, touching no brief; the fix is to use Claude interactively once, and
  the next sweep resumes. A non-interactive path (a LaunchDaemon, or any shell outside the GUI session) can't
  reach that token at all: a non-GUI `claude -p …` returns *"Not logged in."*
- **API-key path (documented, not wired — for later).** Export `ANTHROPIC_API_KEY` (or an
  `apiKeyHelper`) in the LaunchAgent environment for a fully-unattended path independent of
  token freshness. Trade-off: separate metered API billing, not the subscription. The
  runner code is identical; only the environment changes.

The runner's design is auth-path-agnostic; this note records the deployment choice and the
subscription path's one human step. The green (authenticated) end-to-end fire is gated on a fresh
Runner token and is the sole remaining reality gap (index: **Partial**).

## Update note — reporting channel: status line, not per-sweep mail (2026-07-17, session 69)

As first built (session 68), the auth-preflight abort authored a fresh dated `comms/`
`blocked` note **every stale sweep** — so a Runner left un-used for a week would produce
seven near-identical "couldn't authenticate" notes. The `comms/` surface is a *mailbox*
(one file per event), so an ongoing *condition* accreted noise there.

Refined so the runner's routine state lives on a **single overwritten status file**,
`.session-state/adopt-runner.status` (machine-local, like all `.session-state/`), written on
every real sweep: last run, result, proof-of-life `last_success`, and the unauthenticated
streak. `curate/metrics.py` **mines** it (read-only, P15 — never writes numbers in) into the
`EVIDENCE.md` dashboard and the exceptions list. A stale sweep now updates one line instead
of spawning a file — and a *successful* sweep updates the same line, so the dashboard is also
the proof-of-life the green-fire gap otherwise lacked. `comms/` is reserved for genuine
escalation: a **single stable-named standing note** (`comms/adopt-runner-stale.md`, not one
dated file per sweep) raised only once the streak crosses `--escalate-after` (default **5**
consecutive sweeps) and **self-cleared** the moment auth recovers. A verify-fail in a target
still authors that target's own dated `blocked` note (§5, unchanged). No new structural
decision — this refines the §6 report-after channel within this ADR's scope.

## References

- [ADR-0049](0049-apply-auto-is-the-authoring-default.md) — the two automatic paths; this ADR builds the agent path it names.
- [ADR-0039](0039-appliable-brief-schema-and-auto-adopt-mechanism.md) — the strict schema + `apply-briefs` engine (the code path).
- [ADR-0029](0029-receiving-architects-auto-adopt-at-startup.md) — the user gate is upstream at registry-Accept; adoption is execution. This ADR is that move one level up.
- [ADR-0036](0036-session-liveness-sidecar-and-orphan-auto-triage.md) — the liveness sidecar the runner reads to never race a session.
- [ADR-0035](0035-startup-turn-budget-inject-and-git-diagnosis.md) — ahead→auto-push, which carries the runner's commit-only work to GitHub at the target's next interactive session.
- [ADR-0046](0046-per-system-comms-surface.md) — the `comms/` rail a failed adoption reports on.
- [ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md) — the runtime declaration the runner filters on (Claude-only).
- `2026-07-14-zero-touch-adoption` (consultant brief, R3) — the prompting recommendation.
- [P4 `identity-boundaries-non-collapsing`](../principles/master.md#p4--identity-boundaries-non-collapsing) · [P10](../principles/master.md#p10--architect-owns-operational-substrate) · [P15](../principles/master.md#p15--code-for-mechanism-not-judgment) · [P18](../principles/master.md#p18--verify-everything) · [`exercise-delegated-work-end-to-end`](../habits/master.md#exercise-delegated-work-end-to-end).
