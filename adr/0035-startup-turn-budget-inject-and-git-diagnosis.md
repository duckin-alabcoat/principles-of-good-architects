# ADR-0035: Startup turn budget — inject-don't-read and git-state diagnosis

**Status:** Accepted
**Date:** 2026-07-05
**Deciders:** the operator, Federation Architect

## Context

[ADR-0020](0020-session-rituals-are-a-code-harness.md) moved the mechanical half
of the §11 session rituals into `session.py start`, wired as the `startup`
`SessionStart` hook. That collapsed the *hook* cost. It did not collapse the
cost that actually dominates startup: the **LLM turn**.

An external consultant engagement (Auditor role class,
[ADR-0004](0004-auditor-as-separate-role-class.md)), commissioned by the operator and
delivered 2026-07-05, measured the federation repo in its slowest configuration — the worst case.
The hooks are cheap: `session.py start` ~1.3s, the two status probes ~0.4s
combined, a GitHub fetch ~1.1s. The expense is **tool-call turns after the
hook**, each a full model round-trip:

- **Happy path today: ~6–10 turns of pure mechanism.** The standard-section
  steps have the Architect *read* the role doc, *read* the prior handoff entry,
  *read* the user profile and apply overrides — all deterministic, none of it
  needing a model.
- **Failure paths spiral, because `start` detects but does not diagnose.** A
  bare `BLOCKED` prints a symptom; doctrine says "resolve with the user, never
  auto-resolve"; the Architect does git archaeology one turn at a time. The
  observed archetype — a session ends without pushing, the *other* machine's
  next `git pull --ff-only` fails, and an Architect who doesn't know a
  one-commit `git push` is the whole fix spirals through remotes, logs, and
  questions. This is the "github problem" the operator named.

The target: a normal startup is hook-runs → one Architect message; an abnormal
startup is hook-prints-diagnosis-and-prescription → Architect runs the one
prescribed command. Every existing feature — stamps, counter, orphan safety,
canon injection, inbox, status surfaces — retained.

This is the same split [ADR-0020](0020-session-rituals-are-a-code-harness.md)
drew ([ADR-0019](0019-curate-gather-and-staging-boundary.md): code does the
mechanism, the LLM keeps the judgment) — pushed one level further, from "don't
hand-run the steps" to "don't spend a model turn on a step a model isn't needed
for."

## Decision

**`session.py start` spends the Architect's read-turns for it (inject, don't
read) and, on any abnormal git state, prints the cause and its one-command fix
instead of a bare `BLOCKED`.**

### 1. Inject, don't read

`start` already injects `CANON.md` + `STANDARD.md` as `additionalContext`
([ADR-0022](0022-architect-ingest-distillation-and-code-channel.md),
[ADR-0024](0024-standard-role-doc-section-is-generated-and-injected.md)). It now
**also** injects into the same payload:

- the **topmost handoff entry only** — never the whole file, which is ~300KB and
  growing;
- the **bound user's resolved profile/overrides** (`users/<id>/profile.md`);
  profile + role-doc override application is deterministic
  ([ADR-0009](0009-data-storage-mechanics.md)), so code composes the effective
  set.

The standard-section steps that said "read X" are rewritten to "X is in your
injected context," and the summary step needs no reads. **Injection-size
discipline is load-bearing:** topmost-entry-only is what keeps this from trading
turns for tokens.

**Deferred: injecting the role doc's own custom section.** Role docs are large
and heterogeneous across Architects; a bounded per-system injection needs a
config field and pays less than the handoff/profile wins. The role-doc read is
retained for now.

### 2. Git-state diagnosis — never a bare `BLOCKED`

`start` classifies working-tree/remote state (one
`git rev-list --left-right --count @{u}...HEAD` plus the porcelain status it
already has) and, for **every** abnormal case, names the cause and its
one-command fix:

| State | Verdict | Action |
|---|---|---|
| Up-to-date | Proceed | Normal start |
| Behind only | ff-pull | The existing `git pull --ff-only` |
| Ahead only | Our own unpushed session commits | Auto-push under `routine-ops-autonomy`; note it in the orientation block |
| Diverged | The one genuinely human case | Surface both sides' commits, "needs the user" — do **not** hard-block the whole session |
| Remote unreachable / auth | Degraded **offline start** | Skip network, stamp locally, carry "sync owed: pull+push when reachable" in the orientation block **and** the STATUS `blocked` field |

The [P9](../principles/master.md#p9--destructive-ops-confirmed) "never
auto-resolve" floor survives intact for what it was written for — genuinely
ambiguous state (diverged history, unexplained dirt). Ff-pulling and pushing
one's own committed sessions are already routine ops
([`routine-ops-autonomy`](../habits/master.md#routine-ops-autonomy),
[P10](../principles/master.md#p10--architect-owns-operational-substrate)); the
change is that **code classifies which case you're in** instead of the Architect
discovering it turn by turn.

## Alternatives Considered

- **Inject the whole handoff file / whole role doc too.** Rejected. The handoff
  is ~300KB and grows every session; injecting it trades the turns this ADR
  saves for a token bill that grows without bound. Topmost-entry-only is the
  discipline that makes inject-don't-read a net win. The role-doc custom section
  is deferred for the same size reason, pending a bounded per-system injection
  config.
- **Keep emitting a bare `BLOCKED` and let the Architect diagnose.** Rejected —
  it is the "github problem": a symptom with no cause sends the Architect into
  one-turn-at-a-time git archaeology. Code already has the porcelain status; one
  more cheap git call classifies the case.
- **Hard-block the whole session on any non-clean remote state.** Rejected — it
  blocks an offline start on GitHub availability and blocks an ahead-only start
  on a fix the Architect is authorized to run itself. Only the diverged case is
  genuinely human, and even it does not stop the session — it surfaces and
  proceeds.
- **Auto-resolve the diverged case in code.** Rejected — [P9](../principles/master.md#p9--destructive-ops-confirmed).
  Diverged history is exactly the ambiguous state the never-auto-resolve floor
  exists for; code names it and hands it to the Architect+user.

## Consequences

- **Happy-path startup drops toward zero tool calls** before the first
  user-facing message (down from ~6–10). The acceptance signal is transcript
  tool-call counts: a happy-path startup trends to 0; a pull-failure startup is
  ≤2 (the prescribed command, maybe a re-run of `start`).
- **Extends [ADR-0020](0020-session-rituals-are-a-code-harness.md).** The
  mechanical half of the ritual grows an injection step and a diagnosis step;
  the judgment — the diverged resolution — stays with the Architect+user. The
  [`session-start-git-ritual`](../habits/master.md#session-start-git-ritual)
  habit is the doctrine this hardens in code.
- **Composes with [ADR-0021](0021-cross-system-status-surface.md).** An offline
  start's "sync owed" flows into the STATUS `blocked` field, so the owed
  pull+push announces itself at the cross-system surface instead of being
  silently carried.
- **Ships fleet-wide via push-substrate.** `session.py` is byte-identical
  everywhere ([ADR-0031](0031-delivery-integrity-self-contained-briefs.md)), so
  the **R1 test gate is a precondition** — ahead/behind/diverged/offline
  classification cases go in the pytest file before this rides a push. The wins
  land at every federated Architect's startup, not just the federation's.
- **The standard-section step wording changes** ride the normal
  [ADR-0024](0024-standard-role-doc-section-is-generated-and-injected.md)
  regenerate path; no canon event.

## References

- [ADR-0020](0020-session-rituals-are-a-code-harness.md) — the session-ritual
  code harness this extends; inject-and-diagnose is the next turn past
  don't-hand-run.
- [ADR-0019](0019-curate-gather-and-staging-boundary.md) — the code-does-the-
  mechanism / LLM-does-the-judgment rule pushed one level further here.
- [ADR-0022](0022-architect-ingest-distillation-and-code-channel.md) /
  [ADR-0024](0024-standard-role-doc-section-is-generated-and-injected.md) — the
  `additionalContext` injection channel and the standard-section regenerate path
  this reuses.
- [ADR-0021](0021-cross-system-status-surface.md) — the STATUS `blocked` field
  the offline-start "sync owed" flows into.
- [ADR-0009](0009-data-storage-mechanics.md) — deterministic profile/override
  composition, why the resolved user set can be injected without a model.
- [ADR-0031](0031-delivery-integrity-self-contained-briefs.md) —
  byte-identical `session.py` push; why the R1 test gate precedes the substrate
  push.
- [ADR-0004](0004-auditor-as-separate-role-class.md) — the Auditor role class of
  the commissioning consultant engagement.
- [`session-start-git-ritual`](../habits/master.md#session-start-git-ritual),
  [`routine-ops-autonomy`](../habits/master.md#routine-ops-autonomy) —
  [P9](../principles/master.md#p9--destructive-ops-confirmed),
  [P10](../principles/master.md#p10--architect-owns-operational-substrate).
- Source brief: `proposed-edits/federation-arch/pending/2026-07-05-startup-turn-budget-and-git-diagnosis.md` (withheld)
  — the consultant findings this decision records.
- Federation session 47 (2026-07-05).
