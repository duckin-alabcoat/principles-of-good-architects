# POGA — architecture, benefits, and scalability

POGA (*Principles of Good Architects*) is a federation-management system for a
portfolio of long-lived AI Architect roles. Each Architect maintains one product or
system—a web service, a scheduled job, a command-line tool—while this
repository supplies the shared operating layer that lets the fleet improve without
each member rediscovering the same practices.

It is not a conventional end-user application. It is Git-backed operational
governance for AI collaborators.

## What the federation does

The Federation Architect has four co-equal responsibilities:

1. **Bootstrap** new Architect-managed systems from a reusable kit.
2. **Curate** learnings from member systems into shared principles and habits.
3. **Redistribute** approved improvements to the relevant member Architect.
4. **Standardize** the shared operational substrate without flattening each
   system's distinct mission and voice.

The core loop is:

```text
Member Architects
  │
  │  learnings, status, and proposed changes
  ▼
Federation repository
  │
  ├─ curates shared principles and habits
  ├─ generates and distributes common substrate
  ├─ bootstraps and restores new member systems
  └─ supplies safe session, lane, backlog, and release mechanics
  ▼
Member Architects receive the applicable shared improvements
```

The authoritative descriptions are the Federation Architect role document,
Architecture Decision Records (ADRs), and the tested operational code. The project
uses ADRs so structural decisions have a visible rationale rather than only a code
diff.

## Main codebase components

### Session and collaboration engine

`session.py` is the entry point, not the engine. It is a ~150-line dispatcher that
loads the `sessionlib/` package into its own namespace; the mechanics live in that
package, split by concern in
[ADR-0118](adr/0118-session-py-is-a-package-behind-a-thin-entry-point.md) after the
single file reached 25,620 lines.

Nine of the modules are *parts* — they are compiled and executed into one shared
namespace by `sessionlib/__init__.py`, so they behave exactly as the monolith did and
are not imported individually. `registry_state.py` is the exception: a small ordinary
module that is imported directly.

| Module | Concern |
|---|---|
| `sessionlib/__init__.py` | The loader. `load(ns)` compiles each part and executes it into the namespace it is handed, so the parts share one set of globals. |
| `sessionlib/config.py` | Config and paths — module-level constants, layout resolution, member-config reading, and the shared low-level helpers every other part uses. |
| `sessionlib/goal.py` | Session goals — the clauses a goal is made of, what became of each, and the authoring-time refusal of a clause gated on a condition the session may never settle. |
| `sessionlib/coord.py` | The cross-lane coordination store — claims, leases, allocations, locks, authorization, and handoff injection. |
| `sessionlib/store.py` | The record stores — work items, operational obligations, ADR and session number allocation, orphan triage. |
| `sessionlib/journal.py` | Session journals and the compiled views — journal render/parse/finalize, the session banners, and handoff, ROADMAP and STATUS compilation. |
| `sessionlib/land.py` | Land and gate — rebase, the fail-closed merge gate, CAS trunk advance, checkout sync, main materialization. |
| `sessionlib/trunkcheck.py` | The trunk check (WI-0427 / ADR-0148 D5) — after each land a detached runner checks the sharded suite on the newest trunk tip, bisects a red by land boundaries, and refuses the next code land while the trunk is red. |
| `sessionlib/lanes.py` | Dispatch and lanes — worktree allocation, reap, attach, notify, supervise. |
| `sessionlib/hooks.py` | Hooks and start/end — the `SessionStart` and `PreToolUse` bodies, the structural bash and question guards, preflight, and the CLI surface. |
| `sessionlib/registry_state.py` | Content-hash receipts for the registries and the user profile. Imported directly; not one of the nine parts. |

Between them these provide deterministic mechanics for:

- Session start/end, journaling, handoff compilation, and liveness tracking.
- Worktree lanes, isolated concurrent work, landing, and cleanup.
- Claims and leases so concurrent sessions do not silently co-own work.
- Work items, operational obligations, release declarations, and release cuts.
- Inbox validation and application of approved briefs.
- Runtime resolution and dispatch.

Every one of those is still reached as `python3 session.py <verb>`: the split moved the
code, not the interface, and `session.py` remains the file distributed byte-identically
to every member repository.

The design intentionally separates deterministic work from judgment: code allocates,
validates, compiles, and checks; an Architect and the user decide what should change.

This module table is checked rather than remembered — `curate/check_substrate_docs.py`
fails if a file exists in `sessionlib/` and is not named here.

### `poga` lifecycle command

The `poga` shell command is the human-facing entry point.

- `poga` opens an Architect session in an isolated worktree lane.
- `poga lanes` manages lanes.
- `poga bootstrap` creates or adopts a system into the federation.
- `poga restore` performs the same lifecycle pipeline as bootstrap, adding state
  hydration and an explicit credential-reprovisioning checklist.

The universal portion of this command is distributed byte-for-byte to member
repositories. Federation-only lifecycle code remains in `poga_cli.py`.

### Curation and fleet rollout

The `curate/` tools make federation-wide improvement mechanical where it can be:

- `gather.py` finds new producer learnings.
- `distill.py` generates the canon digest.
- `standardize.py` generates the standard role-doc section.
- `reconcile.py` compares federation records against live member repositories.
- `push-substrate.py` updates shared generated substrate safely.
- `deliver.py` routes approved briefs to member inboxes and verifies delivery.
- Supporting generators maintain settings, indexes, metrics, and conformance data.

### Policy, state, and audit surfaces

- `principles/master.md` and `habits/master.md` hold the federation's shared canon.
- `bootstrap-kit/` holds the initial files for a new member repository.
- `work-items/` and `ops-items/` are structured backlog stores.
- `releases/` records delivered releases and declared major-version promises.
- `sessions/journal/` is the durable per-session narrative; `session-handoff.md`
  is a generated state-transfer view.
- State and credential manifests describe recovery without storing credentials in Git.
- `tests/` exercises core mechanisms such as lanes, claims, bootstrap, restore,
  releases, curation, inboxes, and runtime bindings.

## Are Architect prompts generated?

Partly. An Architect's effective session instruction set is assembled from different
sources rather than generated as a single generic prompt.

```text
CLAUDE.md                  → small static pointer to the role doc
Role doc                   → authored identity, mission, scope, and voice
CANON.md                   → generated shared principles and habits
STANDARD.md                → generated shared operating section
Session context            → injected prior handoff and user-profile context
```

`CANON.md` is generated from the principle and habit registries. `STANDARD.md` is
generated from `standard-source.md`. `session.py start` injects both, plus relevant
per-session context.

The role document itself is not centrally generated after initial bootstrap. The
bootstrap kit supplies its first template, but the member Architect owns its
system-specific identity, mission, voice, and local operating additions. This preserves
the shared floor while preventing every member from becoming the same generic agent.

## Benefits

1. **Fleet learning rather than isolated chats.** A durable lesson from one system
   can become a reviewed, tested shared practice for other applicable systems.
2. **Common reliability floor with local autonomy.** Every member can share session
   continuity, Git safety, status, recovery, and data-boundary practices while keeping
   its own domain and personality.
3. **Continuity across context loss.** Journals and generated handoffs preserve what
   happened, what remains, and why.
4. **Safer parallel work.** Worktree lanes isolate concurrent edits; claims and land
   gates make ownership and integration explicit.
5. **Verification of reality.** Capability detectors and tests check for actual
   structural evidence instead of trusting a version string or a prose assertion.
6. **Repeatable onboarding and recovery.** Bootstrap and restore make a new or
   replacement system a rehearsable process rather than a one-off reconstruction.
7. **Auditable governance.** ADRs, versioned role documents, work items, journals,
   and release records make decisions and outcomes reconstructable.
8. **Appropriate use of automation.** The substrate automates deterministic tasks;
   people retain approval over policy and behavior changes.

## Is it scalable?

Yes, for the intended scale: a portfolio of dozens of Architect-managed systems. It
is designed to avoid the early scaling failures of hand-maintained prompt copies,
invisible state, unsafe concurrency, and undocumented process.

It does *not* yet target a large organization running thousands of independently
operating agents. Its current boundaries are deliberate:

| Constraint | Why it limits scale |
|---|---|
| Human approval gates | Central review is correct for governance, but becomes the throughput ceiling under high policy churn. |
| Large central `session.py` module | It is tested, but a growing command surface concentrates maintenance and review cost. |
| Federation fan-out | Updating and delivering to many repositories is practical for a portfolio, but needs queues and stronger observability at much larger fleet sizes. |
| Shared-machine assumptions | Some coordination and configuration concern one user's small machine set rather than a distributed organization. |
| Context budget | The canon is distilled, but injected guidance cannot grow forever without impairing agent usability. |

## Path to greater scale

The right next move is not to replace the current system with a central service. First
preserve its strongest properties—local autonomy, Git auditability, explicit human
authority, and graceful offline operation—while extracting the parts that become
coordination bottlenecks.

### 1. Define scale targets and service-level objectives

Decide which scale is actually needed: number of member systems, concurrent sessions,
acceptable update latency, restoration target, and approval turnaround. Without these
numbers, a "distributed control plane" would be premature architecture.

### 2. Split `session.py` by stable domain boundaries — **done, 2026-09-06**

This was a recommendation when this document was written; it has since shipped as
[ADR-0118](adr/0118-session-py-is-a-package-behind-a-thin-entry-point.md), and the
result is the `sessionlib/` table under *Session and collaboration engine* above. The
CLI did not change and the test suite was preserved, as recommended.

One thing the recommendation got wrong, and it is worth recording rather than quietly
correcting. "Importable modules" is not what was built, because it could not be: the
call graph had a 123-symbol strongly-connected component and the test suite patched
module globals 380+ times, both measured before any code moved. Real submodules each
carry their own `__dict__`, so `session.ROOT = tmpdir` would have rebound the name in
one namespace while the other parts kept reading the old one. The parts therefore share
a single namespace — semantically still the monolith, stored in seven files. The
remaining cost is stated plainly in the ADR: `import sessionlib.land` is not an
interface, and narrow module-level contracts are still owed.

### 3. Add an append-only federation event model

Represent important fleet events—substrate release, brief delivery, adoption result,
detector failure, lane land—as signed or attributable append-only records. Generated
views can continue to be compiled from these records. This provides a durable audit
stream before introducing a database or service.

### 4. Replace synchronous fan-out with idempotent delivery jobs

Model each fleet update as a release artifact plus one delivery job per member. Jobs
need durable status, retries with a capped backoff, explicit terminal failures, and a
dashboard or report. Member repositories should still apply changes locally and verify
their own state; the federation should coordinate delivery, not become their runtime.

For example, a substrate release becomes a visible set of independently verifiable
outcomes:

```text
Release R-123: standard substrate update
  ├─ alpha        → succeeded and verified
  ├─ beta         → blocked: local substrate file modified
  ├─ gamma        → retrying: host unreachable
  └─ delta        → failed permanently: unsupported base version
```

Every job records the exact artifact, target, expected base state, attempts,
timestamps, result, and independent verification result. Jobs have terminal states:
**succeeded**, **blocked**, **failed**, or **deferred**.

An idempotent job is safe to run again. If a member already has and has verified
R-123, a retry recognizes that fact and performs no write. Bounded retries prevent a
temporary-failure loop from becoming an invisible permanent one: retry with backoff up
to a specified limit, then stop and surface the actionable failure. The resulting
question changes from “did the push script run?” to “which members received this exact
artifact, which did not, why, and what action is required?”

### 5. Tier governance rather than removing it

Keep the operator's approval for universal canon, mission/scope changes, and irreversible
policy. Delegate lower-risk classes—generated substrate patches, detector additions,
or pre-approved migration templates—to rules with clear bounds and mandatory audit
records. That raises throughput without silently broadening authority.

### 6. Make capability and fleet health observable

Publish a generated fleet-health view containing member reachability, substrate
capabilities, inbox/delivery status, unresolved jobs, stale sessions, recovery-drill
status, and operational obligations. Alert on deviations and trends, not merely
one-off command failures.

The deliverable should be both a human-readable `FLEET-HEALTH.md` and a
machine-readable `fleet-health.json`, compiled from existing read-only detectors and
reconciliation outputs rather than maintained by hand. It should cover:

| Area | Signals |
|---|---|
| Membership | Registered members, reachable repositories, and unlocated members |
| Standard conformance | Required substrate capabilities each member actually detects |
| Delivery | Pending, blocked, failed, and verified fleet deliveries |
| Inbox safety | Inbox exists, is writable, gitignored, and contains no malformed or stranded briefs |
| Freshness | Last successful session/status and staleness threshold |
| Runtime health | Declared runtime, launch binding, and failed starts or heartbeat gaps |
| Coordination | Open/stale claims, leaked worktrees, and orphaned branches |
| Recovery | State/credential manifest coverage and restore-drill status |
| Operations | Due and overdue operational obligations |
| Rollout | Members that have verified each released substrate capability |

The view must distinguish evidence states:

```text
HEALTHY   verified current
DEGRADED  working, but an obligation is overdue or a member is stale
BLOCKED   known human action is required
FAILED    an attempted operation failed and needs intervention
UNKNOWN   no trustworthy evidence; never treated as healthy
```

Alert only on threshold crossings and meaningful state changes. Failures such as a
malformed inbox, missing required capability, or destructive coordination residue merit
immediate notice; stale members, exhausted delivery retries, and overdue restore drills
need scheduled escalation. The compiler should write views atomically and remain
local-first: it reports what the existing evidence proves rather than inventing a
second monitoring service.

### 7. Treat shared-machine state as a local adapter

Maintain the current local-machine mechanisms where appropriate, but define a narrow
coordination interface so machine-local paths, locks, and credential locations can be
replaced by per-host adapters. This prepares multi-user or multi-host deployment
without forcing a central database today.

The interface should express coordination semantics, not current filesystem details:

```text
Coordination adapter
  - locate_member(system_id)
  - read_member_status(system_id)
  - acquire_claim(resource, owner)
  - release_claim(resource, owner)
  - enqueue_delivery(member, artifact)
  - read_delivery_result(job_id)
  - resolve_secret_reference(name)
```

The initial filesystem/common-Git-directory implementation remains an adapter, not
something the core workflow assumes. Other implementations can follow as actual needs
arise:

| Adapter | Appropriate use |
|---|---|
| Git/common-directory + filesystem | The current single-user, multi-worktree setup |
| Shared-volume | Two machines sharing a data volume |
| Git remote / repository | Members on separate machines without a shared disk |
| Service-backed | A larger fleet requiring durable shared coordination |

For example, the delivery workflow should request “enqueue this verified brief for
member alpha,” not “write this exact file to a shared-volume path.” The current adapter may write the
file directly; a remote adapter could create an attributable Git commit or enqueue a
signed message. Members remain locally autonomous and Git-backed in every case; only
the transport and coordination implementation changes.

This abstraction should follow module separation and event recording, not precede
them. Introducing a generic interface before the stable semantics are known would add
an abstraction layer without solving a demonstrated problem.

### 8. Keep the injected canon small and targeted

Continue generating a concise universal digest. Add class- or capability-scoped
guidance only when a detector proves relevance; keep full rationale in linked source
documents. This prevents context bloat while preserving a single authoritative source.

### 9. Exercise the future architecture through drills

Before declaring the system scalable, run failure-injection drills: offline member,
duplicate delivery, partial rollout, failed land, stale capability report, recovery on
a fresh machine, and an approval backlog. Each drill should produce a measurable
result and a work item for every uncovered gap.

## Recommendation

Prioritize steps 1–3 first: define a concrete target scale, modularize the central
engine, and add an append-only fleet event model. These improvements expose the real
bottlenecks and create a sound foundation for asynchronous delivery and richer
observability. Building a centralized control plane before those foundations would add
operational complexity without proving that the portfolio needs it.

## Open-source release and Apache-2.0

Apache-2.0 is a strong default if the goal is broad adoption—including by companies—of
the reusable operating substrate. It is a permissive license, includes an explicit
patent grant, and requires preservation of applicable attribution and notice material
in redistributed derivatives. It allows a team to adopt the substrate inside a
proprietary system without forcing that system to be open-sourced.

The trade-off is equally deliberate: a downstream user may fork, modify, host, or sell
a derivative without contributing those changes back. Apache-2.0 is therefore a good
fit for adoption and ecosystem influence, not for a strategy whose primary goal is
reciprocal publication of all improvements.

### Recommended release boundary

Publish a sanitized public distribution rather than simply making the private working
repository public. The public project should include reusable code and generic
documentation:

- `session.py`, `poga`, lifecycle and bootstrap code, schemas, detectors, and tests.
- The bootstrap kit and generic documentation.
- Generic examples that contain no real user, machine, repository, credential, or
  recovery-location data.

It should exclude personal fleet state and operational history, including user data,
machine-local configuration, real credential or state-manifest coordinates, private
communications, session journals, and any Git history that carries such content. A
tracked file being technically non-secret does not make it suitable for a public
repository: paths, portfolio details, operational history, and personal context all
need a deliberate public-release review.

### Release checklist

1. Audit the complete intended public history and artifact set for personal,
   confidential, and machine-specific material.
2. Create a clean public repository or carefully sanitized export; do not rely solely
   on `.gitignore`, which does not remove tracked history.
3. Add an unmodified `LICENSE` file containing Apache License 2.0 and a root `NOTICE`
   file with correct copyright and attribution information.
4. Review every third-party dependency, copied asset, and template for compatible
   licensing and retained notices.
5. Add `CONTRIBUTING.md`, `SECURITY.md`, a code-of-conduct decision, and a concise
   governance document explaining who maintains releases and how proposals are
   accepted.
6. For early contributions, use a lightweight Developer Certificate of Origin or an
   explicit inbound-contribution policy. Revisit a Contributor License Agreement only
   if significant or corporate contributions make that worthwhile.
7. Decide separately how the project name and logo may be used. Apache-2.0 licenses
   copyright and patent rights; it does not create a trademark policy.
8. Make the first release small and runnable: a durable Architect session in a Git
   repository, one bootstrap path, and one conformance check are a clearer adoption
   story than exposing every federation artifact at once.

### Positioning

Avoid presenting the project as another generic agent framework. The sharper claim is:

> A Git-native operating substrate for long-lived AI agents: durable sessions, policy
> distribution, safe concurrent work, verifiable rollout, and recoverable state.

Conventional agent platforms increasingly offer custom agent profiles, tool controls,
session visibility, and enterprise governance. The differentiated opportunity here is
the lifecycle around those runtimes: keeping an agent fleet consistent, auditable,
upgradeable, and recoverable over months or years while preserving local autonomy.
