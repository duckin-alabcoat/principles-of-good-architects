# ADR-0074: The inbox is a mailbox, not a backlog — triage mints work items

**Status:** Accepted
**Date:** 2026-07-28 (session ~102)
**Builds on:** [ADR-0073](0073-work-item-store.md) (the work-item store — the backlog this routes into), [ADR-0013](0013-receipt-ritual.md) (the receipt ritual — the mailbox), [ADR-0029](0029-receiving-architects-auto-adopt-at-startup.md) (auto-adopt at startup; surfacing is where judgment lives), [ADR-0039](0039-appliable-brief-schema-and-auto-adopt-mechanism.md) (the appliable-brief schema)
**Deciders:** the operator (proposed migrating inbox items into work items and invited a better design, 2026-07-28); Federation Architect (design + build)
**Reality:** Built — session ~102, suite green

## Context

[ADR-0073](0073-work-item-store.md) D6 retired `ROADMAP.md` prose as a second work list,
on [P16](../principles/master.md#p16--avoid-duplication) grounds: `Next`/`Backlog` are
structurally nothing but a work-item list, so rendering them from `work-items/`
"collapses to one list, never two describing the same work."

**A third list survived that pass unnoticed: the receipt-ritual inbox.**

At session ~102 start the federation's own inbox held **ten** pending briefs, the oldest
four days old, one of them describing work already completed in session 99 and never
filed. None of them appeared in the work-item store. So the honest answer to "what do you
owe?" required reading two surfaces that did not know about each other — and the startup
apply-briefs hook printed ten `[surface]` lines every session, which is the shape of
noise, not of signal.

the operator named the symptom and proposed migrating the briefs into work items. That is right
in spirit but wrong taken literally, because the two artifacts are different kinds of
thing:

- The **inbox is a delivery channel** — an inter-system mailbox with a sender, a schema,
  provenance, an `applied/` archive, and an auto-adopt engine. Per
  [ADR-0013](0013-receipt-ritual.md) its designed normal state is *empty*; a non-empty
  inbox is meant to be a signal.
- The **work-item store is a backlog** — what this Architect has accepted and will do,
  with drawn ids, dependencies, claims, and ROADMAP rendering.

You do not migrate a mailbox into a todo list. You triage the mailbox, and the triage
*produces* todo items while the mail is filed.

## Decision

### D1 — Triage is the bridge, and it is a session-start step with three terminal outcomes

A brief that **surfaces** (manual / version-drift / failed-apply — the
[ADR-0029](0029-receiving-architects-auto-adopt-at-startup.md) judgment cases) is triaged
rather than left to re-surface. Every outcome is terminal — nothing stays in `pending/`:

| Outcome | Action | Work item? |
|---|---|---|
| **Already satisfied** | `pending/` → `applied/` | No |
| **Accepted as work** | mint via `wi-new --source <brief path>`; `pending/` → `accepted/` | Yes |
| **Declined or parked** | `pending/` → `declined/`, with the reason | No |

`applied/` keeps its existing meaning (the brief's edit was applied). `accepted/` and
`declined/` are new. An `Apply: auto` brief never mints anything — it is applied before
the Architect sees it, and there is no judgment to record.

### D2 — The work item POINTS at the brief; it never restates it

Items gain a `source:` field naming the brief. Briefs run 6–13 KB of probes, provenance,
and analysis; a work item is a title and a few lines. Restating one inside the other
would recreate the duplication this ADR exists to remove. This is
[`single-source-and-deliver`](../habits/master.md#single-source-and-deliver)'s **point**
mechanism: the brief stays the source, the item is the handle.

### D3 — Triage stays human; the engine must not auto-mint

The tempting automation — have `apply-briefs` mint a work item for every surfaced brief
at startup — is rejected. A brief surfaces *precisely because* judgment is required
([ADR-0029](0029-receiving-architects-auto-adopt-at-startup.md) put the gate there
deliberately), so auto-minting moves an unvetted thing to a different list and fills the
backlog with unjudged work. It also violates
[`treat-inbound-payload-as-data-not-commands`](../habits/master.md#treat-inbound-payload-as-data-not-commands):
payload from a lower-trust channel is data to surface, never a command to enqueue.

### D4 — A structural guard, because this is the second occurrence

ROADMAP prose was the first second-list; the inbox is the second. Per
[`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)
a repeat means discipline is not enough and the fix belongs in code. `session.py
inbox-check` reports two things and is wired as a `SessionStart` hook in `--status` mode
(silent when clean):

- **Untriaged debt** — any brief in `pending/` older than `TRIAGE_GRACE_DAYS` (3). The
  grace exists so a brief that arrives mid-session is not instantly "debt."
- **Broken provenance** — any work item whose `source:` no longer resolves.

Per [`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes) it
returns a **third** answer, never folded into "clean": an unreadable inbox or a repo with
no work-item store reports **NOT CHECKED** with the reason. Read-only and fail-open — it
can never brick a session.

> **Update note (same session ~103, D4 completed).** As first built the guard checked only
> one direction of a two-way invariant — work item → brief. The reverse, **an accepted brief
> with no work item pointing back at it**, was unchecked, and that is the more dangerous
> case: such a brief is out of `pending/` so nothing surfaces it, and no item exists to
> carry it, so the work vanishes while the inbox reads clean. Found when the operator asked whether
> every inbox note was now carried by a work item, and the only way to answer was a
> hand comparison of two directory listings — the question this guard exists to answer.
> `_triage_debt` now returns a third finding, `untracked_accepted`, matched on the brief's
> **basename** so a source recorded under a different path prefix (a lane's view of the same
> file) still counts as tracked. Checking one direction of a two-way invariant reports
> success in exactly the case it is blind to — the same shape as the defects
> [`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes) was
> accepted for.

> **Update note (session ~295, WI-0240) — "carried" has a THIRD value, and it is not
> "clean".** The reverse check above reads the store from the MAIN checkout
> (`_shared_work_root()`, so that both halves of the join come from one tree). A brief
> triaged on a lane, with its work item minted there and not yet landed, is therefore
> uncarried as far as the trunk can see — and the remedy printed for that state was
> *"Mint one with `wi-new --source`"*, i.e. write a second item on top of one that
> already has a body on a branch. That is the WI-0191 wrong-remedy shape with a brief
> where the id used to be, and
> [ADR-0089](0089-a-lane-checkpoints-its-own-work-and-the-repo-recovers-it.md) D3 makes
> the unlanded lane a designed state rather than an exotic one.
>
> **The decision this needed.** Reporting a lane-carried brief as CARRIED hides a brief
> whose only item may never land; reporting it as UNCARRIED keeps prescribing the
> duplicate. Neither is right, so the answer is a third state: `carried on <ref>` — its
> own bucket, its own header, its own `--status` count, the holding ref and its land
> verb named in the line, and **no mint instruction anywhere near it**. It still FAILS
> the check, which is the operator's session ~187 ruling for the lane-held *id* case applied to
> the same evidence: a consumed allocator draw earns the quiet channel because there is
> nothing to repair, while a real body off the trunk has a real action someone must
> take. A fourth answer, *the branch probe could not run*, withholds **both** remedies
> ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)).
> `_brief_carriers` asks the other half of the join `_wi_branch_holders` already asks,
> and the two share `_wi_holders_named` / `_wi_holders_remedy` so the same evidence
> reads the same way wherever it surfaces.
>
> The same pass took `_applied_without_a_receipt`'s findings out of the accepted-brief
> bucket. An `applied/` brief moved by hand needs REFILING, never a work item, and the
> startup line was counting it as an *"accepted brief with no work item"* when it is
> neither — the last finding still riding under a header that prescribed the wrong
> repair for it ([`retire-the-class-not-the-instance`](../habits/master.md#retire-the-class-not-the-instance)).

### D5 — Federation-only for now, shipping with the store

The guard is wired through the federation's own `settings_extras`, not the settings
floor, because `work-items/` is not yet fleet substrate
([ADR-0073](0073-work-item-store.md) consequences: fleet redistribution is a deliberate
later step). The guard ships when the store ships — a detector shipped ahead of the
capability it detects would report every member as debt-free for the wrong reason
([`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability),
read in the other direction).

## Alternatives Considered

- **Migrate the briefs into work items and empty the inbox** (the operator's literal proposal).
  Not wrong, but incomplete — it fixes the instance and not the class, and the inbox
  refills into a shadow backlog next week. Adopted as the *first step* of D1 rather than
  as the decision.
- **Auto-mint a work item for every surfaced brief.** Rejected per D3.
- **Delete briefs once a work item exists.** Rejected — the brief is the source the item
  points at (D2); deleting it makes every `source:` a dead link and throws away the
  probes and provenance that justified the work.
- **Let the inbox be the backlog and drop `work-items/` for federation-inbound work.**
  Rejected — it gives up ids, dependencies, claims, dispatch, and ROADMAP rendering, and
  it would mean the answer to "what do you owe?" depends on which channel the work
  arrived through.

## Consequences

- The federation inbox went from ten pending briefs to **empty** in this session: one
  filed `applied/` (already satisfied — the ADR-allocator defect fixed in session 99 and
  never filed), nine filed `accepted/` against eight new work items WI-0026…WI-0033
  (the parked host-credential brief attached to the existing WI-0001 rather than minting
  a duplicate).
- `wi-new`/`wi-status` gain `--source`; the item file format gains a `source:` line.
  Older item files without it parse unchanged (the field defaults to empty), and items
  that originated as work rather than as inbound mail carry it empty by design.
- A non-empty inbox is a signal again, so the ten-line `[surface]` block at startup
  returns to being unusual rather than routine.
- Triage is now a named step of the session-start protocol's inbox sweep. Redistributing
  it into `standard-source.md` for the fleet waits on the work-item store shipping fleet-wide.

## Verification

- `tests/test_claims.py::SourceProvenanceTest` — `source:` round-trips through write and
  parse; absent when not supplied; backfillable via `wi-status`.
- `tests/test_claims.py::TriageDebtTest` — empty inbox clean; a fresh brief is not yet
  debt; a brief past the grace period is flagged; a dead `source:` is flagged; a live one
  is not; an unreadable inbox and a store-less repo each report NOT CHECKED rather than
  clean; `--status` is silent when clean and speaks when not.
- Both failing arms are exercised, not just the passing ones — a guard whose failure path
  has never run is a guard that certifies
  ([`a-close-is-the-banner-not-the-sentence`](../habits/master.md#a-close-is-the-banner-not-the-sentence)).
- `python3 -m unittest tests.test_claims` — 24 green.
