# ADR-0107 — Mail transits `origin`, and every machine drains the share it can reach

- **Status:** Accepted
- **Reality:** Partial — **the outbound leg is proven cross-machine**, and both machine roles drain. A brief staged on the dev machine travels by `origin` and is delivered from the Runner to a member resident there, a member no development machine can reach. The Runner drains from its LaunchAgent. The design assumes the dev machine is headless, so it cannot load one — `deploy/install-mail-poller.sh` refuses a headless host by design — and its share drains from a `SessionEnd` hook instead (WI-0227). STILL PARTIAL, for one reason only: the **return leg is unexercised**. Nothing has yet travelled Runner → `origin` → devbox, which is the direction that matters for a member's *reply* and the one the consultant brief asks to see end-to-end.
- **Date:** 2026-09-03
- **Supersedes:** the transport half of [ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md). Its delivery *semantics* stand unchanged.
- **Related:** [ADR-0088](0088-a-mailbox-is-gitignored-inbound-data-fleet-wide.md), [ADR-0103](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md) D1, [ADR-0106](0106-local-promotion-process-and-data-residency-are-declared-separately.md), [ADR-0018](0018-adopted-principles-and-habits-are-system.md), WI-0221

---

> **Amended by [ADR-0135](0135-a-trunk-clone-is-never-a-process-root.md) (2026-09-13), transport only.** The return leg is unchanged: the outbox is still the queue, `origin` is still the only bridge, and every machine still drains its own share. What moved is WHERE a sealed machine commits. After the Runner's process root became a deploy tree — detached at a tag, no upstream — a commit there belongs to no branch and nothing can push it, so a resident machine writes on the one-writer branch `runner/mail` in a channel clone and devbox lands that onto `main`. Read every "commits and pushes to the trunk" sentence below as "commits and pushes on its channel" when the writer is sealed.

## Context

**ADR-0027's transport assumption is no longer true, and nothing recorded that.**

Its title is *"the federation delivers edits into target inboxes **over the shared
volume**"*, and [ADR-0038](0038-a-shared-resource-is-scoped-by-who-can-reach-it.md) treats that shared
substrate as something the federation stands on, ADR-0027 inbox delivery among it. `deliver.py` implements the strictly weaker requirement that
falls out of it: the recipient's repo must be openable as a **local path** on the machine
running the delivery.

the operator's 2026-08-22 machine-role ruling moved all development to devbox and made the
Runner the production machine. The dev machine's local reconcile config states the
consequence deliberately:

> The Runner-side roots (…) are deliberately ABSENT — their data is the system and it stays
> single-writer on the Runner. A smaller fleet view here is the mechanism working, not a
> gap.

Both statements are correct. Together they are incompatible, and **no ADR noticed**.
Under that split, the Runner-resident repos are not reachable as local paths from the dev
machine; its `repo-paths.local` maps only the federation itself; and `deliver.py --audit`
reports the Runner-resident members' Architects as *"not locatable from this machine"*. The audit's printed
remedy — *"map it in `repo-paths.local`"* — **cannot be followed on such a machine**: a
locator map needs a filesystem to point at.

This is the same undocumented-fork shape that produced two mutually exclusive ADR-0103s
(WI-0195): a ruling changed a premise, the artifact resting on that premise was never
revisited, and the contradiction surfaced only when someone tried to use it.

The gap blocks every fleet-wide redistribution that needs those recipients. When this was
written, five open items were of that kind (WI-0006, WI-0017, WI-0055, WI-0021 and WI-0012).

## Decision

### D1 — The outbox is tracked, and mail transits `origin`

Outbound briefs are queued in `outbox/to-<architect-id>/`, a **tracked** directory in the
federation repo. Delivery is not attempted at authoring time.

The obvious alternative — teach `deliver.py` to write over SSH — is forbidden by
[ADR-0103 D1](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md): *"The git remote
is the only bridge. No devbox→Runner ssh, rsync, or push. Every promotion transits
`origin`. Neither machine holds credentials for the other."* That constraint is worth
keeping on its own merits, so the transport changes rather than the rule.

A gitignored directory cannot cross `origin`. Tracking the outbox is therefore not
incidental; it is what makes the bridge usable.

### D2 — A tracked outbox does not contradict ADR-0088, because direction is the trust axis

[ADR-0088](0088-a-mailbox-is-gitignored-inbound-data-fleet-wide.md) D1 makes a **mailbox**
ignored-by-VCS because it is **inbound** data of unproven origin — the same reasoning as
`treat-inbound-payload-as-data-not-commands`. An **outbox** holds **outbound** content
this Architect authored, which is the trust class of an ADR or a registry entry, both
tracked.

This decision is written down because the two directories look alike and a future reader
will otherwise conclude ADR-0088 was quietly broken.

### D3 — Every machine runs the same poller, and it asks *reachability*, never *identity*

`curate/mail-poller.py` delivers every queued brief **this** machine can reach and leaves
the rest. It contains no machine check. It asks the send path's own resolver *"can I
reach this recipient?"* — the question that actually governs — rather than *"am I the
Runner?"*.

A residency test here would be a **second source of truth about locality**, and a
locality assumption drifting out of date without anyone noticing is the entire reason
this ADR exists. One resolver, one answer.

Consequence: `"not locatable from this machine"` is a **normal outcome**, counted and
named separately from failure. It is the expected answer for most of the fleet on any
given box, and folding it into an error count would make every healthy run look broken —
the crying-wolf failure the delivery audit was rebuilt to end.

### D4 — Pull, not push, because the privilege direction matters

The Runner reads a directory that arrived through `origin`. The rejected alternative gave
devbox **write** access to the disk holding other members' single-writer data.
The lesser grant is the correct one, and it puts the gate on the consuming side, which is
what `treat-inbound-payload-as-data-not-commands` asks for.

### D5 — Every write into the outbox is scrub-gated, and the gate REFUSES

Tracked means permanent, and this repo has a public mirror whose sanitization is a
hand-scrub performed per sync ([ADR-0018](0018-adopted-principles-and-habits-are-system.md);
the harness is WI-0020, still open). A one-way door with a hand-operated lock is the shape
that eventually goes wrong.

`curate/scrub.py` runs before anything is written and **refuses** on a hit rather than
warning. The refusal masks the matched value (P14). `--force` clears it deliberately and
announces that the file is *not* clean — the ADR-0096 (withheld) D7
shape, where a bypassed check can never later be mistaken for a passed one.

**The gate's own limit is declared**: it finds shapes it knows — addresses, credentials,
identifiers. It cannot catch a name or a private detail in ordinary prose. A clean result
means *no known-shape PII*, never *proven harmless*
(`declare-what-a-check-assumes`). That limit is the argument for a refusal a human
clears rather than an automated pass.

This was not a hypothetical risk. Scanning all 150 files of `proposed-edits/` found no
private personal or credential data at all — every apparent hit was vocabulary
("diagnosis" and "symptom" in the debugging sense, "routing" as in message routing,
`apiKeyHelper` as a config key). It found exactly one genuinely sensitive brief, and it
was one the **federation authored and sent**: machine-network detail no public mirror
should carry. Outbound,
federation-authored, exactly what the outbox carries.

### D6 — Staging is the send; the queue and the receipt are separate states

`stage()` **moves** a brief out of the federation's own `proposed-edits/<arch>/pending/`
rather than copying it: leaving the original would keep the delivery audit reporting it
as stuck forever, which is the crying-wolf failure `_retire_source` already exists to
end. A brief sourced from anywhere else is copied — a brief that is not ours is not ours
to relocate.

Two states, both visible in a tracked directory: `outbox/to-<X>/` is *queued*,
`outbox/delivered/` is *delivered*. The move is the record, which makes the poller
idempotent by construction. Nothing is ever deleted.

### D7 — The poller ships by the ADR-0050 precedent, not the deploy pipeline

A committed path-free plist template plus an installer that renders it, `plutil -lint`s
it, and bootstraps the agent — the same shape as `com.federation.adopt-runner`.

`poga deploy` is **not** available for this: the federation has never been tagged, has no
`deploy/deploy.json`, and is absent from `deploy/registry.json`. ADR-0103 recorded that
gap deliberately — *"The federation must eventually deploy itself through this path…
Recorded here, deliberately not built in this change."* This ADR does not close it.

> **AMENDED 2026-09-13 (WI-0224).** Two of that paragraph's three clauses are now false.
> The federation **has** a `deploy/deploy.json` and **is** in `deploy/registry.json`,
> flagged `"self": true`, which forces it last in the sweep and restricts it to
> `restart: "none"`. The clause that still holds is the first and it is the binding one:
> **the federation has never been tagged** — `git tag` returns zero — and the runner
> refuses an untagged system, so `poga deploy federation` still cannot run. D7's
> conclusion is unchanged; only its reason has narrowed from three facts to one.

### D8 — Liveness is part of the program

Every run writes a status file; `--status` reads it back and reports **NEVER RUN**,
**STALE**, **UNREADABLE** or **OK** as four distinct answers. An unattended runtime that
stops working otherwise tells nobody until a human notices the absence of something
(`scheduled-liveness-smoke-test`), and a poller that never started must not read as one
that ran and found nothing.

## Consequences

- **D1 needs every machine to reach `origin`.** A machine whose credential cannot
  fetch or push can stage mail but not ship it. Fixing that changes which identity every
  repository operation on the machine uses, so it is the operator's call, not a routine one.
- **The poller degrades rather than failing** when it cannot fetch: it delivers whatever
  is already staged on disk and says so.
- **It refuses to advance a dirty or diverged trunk.** It runs unattended in a checkout a
  human may be working in; skipping a refresh costs one cycle of latency, yanking a
  checkout out from under someone costs a session.
- **Delivery and bookkeeping are separated.** A failed commit or a rejected push never
  undoes a delivery that already happened; the retire is re-derivable next run.
- **The audit already sees this surface.** `deliver.py:_outbox_dirs` has read
  `outbox/to-<architect-id>/` since WI-0079 because one member has always used it, so
  adopting the convention adds no blind spot.
- **`outbox/delivered/` grows without bound.** Deliberate for now — it is the sent-mail
  record — but it is a retention question, and `retention-enforced-by-code` says a policy
  living only in prose lapses silently. Not addressed here.
- **This does not fix the reverse direction.** A Runner-resident member with something to
  send *to* devbox has no path. Out of scope, and named so it is not mistaken for solved.

## Alternatives rejected

- **Mount the Runner's disk on devbox over a network share and map the paths.** Zero code — the locator is
  already the right abstraction, and this is what ADR-0027 originally assumed. Rejected
  because it reverses the single-writer protection on the Runner-resident systems that the
  deliberate absence exists to enforce. Available if the operator wants to re-open that ruling;
  that is a policy call, not an engineering one.
- **Teach `deliver.py` an SSH/rsync transport.** The first design considered. Rejected on
  ADR-0103 D1, and it needed three new things (a machine field in `mailboxes.json`, a
  network write path, resolver changes) where the poll needs none — on the Runner those
  members are already locatable, so the existing code works unchanged.
- **Create the missing mailbox directories locally so delivery "works".** Actively
  harmful: because `proposed-edits/` is gitignored, an empty local mailbox flips a member
  from an honest UNREACHABLE to a false UNDELIVERED and invites duplicate delivery —
  already measured (WI-0205).
- **Deliver by hand from a Runner session.** Works today and remains the stopgap, but the
  brief cannot travel there by git, so it is a manual copy every time — the exact chore
  whose omission produced the forked ADR-0103.
