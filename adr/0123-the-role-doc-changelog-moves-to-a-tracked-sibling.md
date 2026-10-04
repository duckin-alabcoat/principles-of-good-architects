# ADR-0123: The role-doc CHANGELOG moves to a tracked sibling, and the receipt moves with it

**Status:** Accepted
**Date:** 2026-09-10
**Deciders:** the operator (ruled session ~241), Federation Architect
**Supersedes:** [ADR-0013](0013-receipt-ritual.md) §"the role-doc CHANGELOG is the canonical record of adoption" — the *locus* clause only. Everything else in ADR-0013 stands.
**Reality:** Built — session ~260, WI-0284 with WI-0282.
**Work item:** WI-0284 (this), WI-0282 (the silent-empty-set defect it lands with).

## Context

`federation-arch.md` was **332,752 bytes in 449 lines**. By section:

| Section | Bytes | % |
|---|---:|---:|
| `## CHANGELOG` | 245,430 | **73.8%** |
| `## 7. Artifacts I maintain` | 49,108 | 14.8% |
| Identity + Mission + Scope + operating principles + everything else | 38,214 | 11.5% |

The role doc is read at **every** session start, and it is the one orientation read the
harness does not inject ([ADR-0035](0035-startup-turn-budget-inject-and-git-diagnosis.md)),
so it is paid uncached, per session, forever. The finish-line board measures the whole
start payload at 411 KB against a 160 KB target, and the role doc is most of the overhang.
Nothing in the CHANGELOG is read to orient. It is read to audit — rarely, deliberately, by
someone who came looking for it.

So the fix is a **move**, not a rewrite. What made it a decision rather than a chore is
that three things pinned the section inside the file, and one of them is doctrine.

**(1) A load-bearing receipt.** `curate/deliver.py:changelog_edit_ids` mines the role-doc
CHANGELOG for already-applied `edit-id`s. That index is the second, durable half of the
re-delivery guard: the first half is the mailbox tree under `proposed-edits/`, which is
gitignored data ([ADR-0007](0007-github-as-system-storage-data-excluded.md),
[ADR-0088](0088-a-mailbox-is-gitignored-inbound-data-fleet-wide.md)) that exists on one
machine and that nothing backs up or rebuilds. On a rebuilt data root the CHANGELOG is the
*only* surviving record of what a member has settled.

**(2) The auto-apply path.** `evaluate_brief` resolves a brief's single `target-file`, and
`changelog-prepend` writes into that same file — which must also carry the `**Version:**`
line the base-version check, the bump and the did-it-move verify all key on. A changelog
sibling carries no version line, so one `apply: auto` brief could not bump the role doc
*and* prepend to a sidecar. [ADR-0049](0049-apply-auto-is-the-authoring-default.md) makes
`auto` the authoring default, so this is the main path, not an edge.

**(3) Doctrine.** [ADR-0013](0013-receipt-ritual.md) says in four places (:151, :159, :216,
:269) that the receiving Architect's role-doc CHANGELOG **is** the canonical record of
adoption, and that where it and `applied/` disagree, the role doc wins.

Two things that look like constraints and are not, stated so nobody re-derives them:

- The habit [`versioned-role-doc-and-changelog`](../habits/master.md#versioned-role-doc-and-changelog)
  says *the role doc has a version, and every change adds a CHANGELOG line.* It is silent
  on **location**. A tracked sibling satisfies it as written.
- [ADR-0010](0010-registries-canon-only-sidecar-history.md) does **not** extend here. Its
  sidecar argument is *substrate opacity* — the data root may not be a git working tree —
  which does not hold for a tracked role doc, and its Alternatives section explicitly
  names and rejects merging the role-doc pattern into the registry pattern. It is cited
  here for its "keeps the main file clean for reading current state" clause and nothing
  more.

And one fact that makes this a real decision: **no member does this.** Every other
Claude-runtime member role doc carries an inline CHANGELOG, the bootstrap
kit ships it inline (`bootstrap-kit/role-doc-template.md:201`), and one member is
a deliberate *counter*-precedent that keeps its changelog inline on purpose. The federation would be fleet-first. the operator ruled
it, session ~241, group A: **the CHANGELOG leaves the role doc.**

## Decision

### D1 — The CHANGELOG may live in a tracked sibling, and for the federation it now does

`federation-arch.md`'s history moves verbatim to `federation-arch-CHANGELOG.md` at the
repo root. Every entry moves byte-for-byte; the role doc keeps a `## CHANGELOG` heading
carrying a pointer. Measured: **332,752 B → 89,369 B, a 73.1% cut**, under the 100 KB bar
the 2026-09-07 consultant brief's R3 set.

One line was *repositioned* rather than moved: the `Per [ADR-0001] … this role doc is
versioned` preamble had drifted 44 lines down into the middle of the entry list, and it
returns to the top of the changelog where the role-doc template puts it. No entry text was
edited.

### D2 — The receipt is durable because the file is TRACKED, never because of its filename

This is the clause that supersedes ADR-0013's locus rule, and the reason the supersession
is narrow. ADR-0013's argument was always about **durability against a lost data root**:
`applied/` is gitignored, the CHANGELOG is not, so the CHANGELOG is the record that
survives. That argument is a property of *git tracking*, not of which file the section
sits in. A tracked sibling carries it unchanged.

So ADR-0013 is amended to read: **the receiving Architect's role-doc CHANGELOG is the
canonical record of adoption, wherever that Architect keeps it.** The `applied/` directory
remains corroborating evidence; on disagreement the CHANGELOG still wins. The two-way
pointer, the per-edit granularity, the `edit-id` citation, the transport, and the
`pending`/`applied`/`rejected`/`withdrawn` lifecycle are all untouched.

### D3 — `changelog_doc` locates it, and `role_doc` is NOT repointed

A new layout key, resolved through the one shared door (`layout_of` / `member_layout`,
WI-0029) so a member declares it once and every reader — its own harness and the federation
mining every member checkout — resolves it identically. No second copy of the precedence rules on
the curate side.

**`role_doc` keeps naming the role doc.** It resolves the file carrying the `**Version:**`
line, and repointing it would silently drag `role_doc_version()`, the land warn-gate
`_new_roledoc_violation`, `curate/reconcile.py`, `curate/metrics.py` and
`curate/adopt-runner.py` along with it. Two keys, two subjects.

**Absent means inline, and inline stays the fleet default.** The other members keep an inline
CHANGELOG and none of them is edited by this ADR. Whether the fleet should split too is a
later ruling, deliberately not taken here.

### D4 — A declared-but-unreadable `changelog_doc` is *could not check*, never a fallback

`changelog_edit_ids` must not fall back to the role doc when a declared sidecar is missing.
The fallback looks harmless and is the entire failure mode: a split member's role doc still
carries a `## CHANGELOG` pointer line, so the fallback would find a heading, parse zero
receipts, and report a **clean cross-check of a file it never read** — a miss grants
nothing, so the visible consequence is a silently re-enabled duplicate delivery.

This is the same distinction WI-0282 landed alongside: *could not check* and *checked and
found nothing* must not share a return value. That defect — a role doc with no recognised
CHANGELOG heading returning the empty set — is fixed in the same commit, and the heading
match is widened to numbered and non-`##` forms (one member numbered its changelog heading,
which matched neither old pattern, so its whole applied history read as
empty).

Receipt scope follows the file's shape: inline, the section heading bounds it; split, the
**first entry line** does, so a history file's title and preamble are not receipts. It
stops at the entry list and never at the entry *line* — a wrapped entry's `edit-id` is
still a receipt, and dropping it would narrow a guard whose whole contract is that it may
only ever widen.

### D5 — `changelog-prepend` gains an optional `File:`

```
## op: changelog-prepend
File: federation-arch-CHANGELOG.md     # optional; default = the brief's target-file
After: ## CHANGELOG
~~~content
- **2.61.0** (2026-09-10) — …
~~~
```

The field belongs on the **op**, not the header. `target-file` names the file whose
`**Version:**` line gates the whole brief; a changelog sibling has no version line and
could never be a second `target-file`. It is one op's destination, and that is the grain.
Omitting the field is byte-identical to the old behaviour, so no existing brief on any
member changes meaning.

A `File:` that resolves outside the repo is refused. An `apply: auto` brief is applied
unattended at session start, from a gitignored mailbox — the one input to this pipeline
that did not come from the repo — so which files it can reach is a boundary, not tidiness.

### D6 — The standard says *wherever your system keeps it*

STANDARD session-start step 7 told every Architect to add the CHANGELOG entry naming the
brief's `edit-id`. Its wording is regenerated to name the changelog **by name, not by
location**, so the obligation is unchanged for an inline member and correct for a split
one. The load-bearing paragraph's argument is re-anchored from *the role doc is tracked* to
*the changelog is tracked*, which is what it always meant.

## Alternatives Considered

- **Leave it inline; trim elsewhere.** Rejected on arithmetic. §7 is 14.8% and everything
  else together is 11.5%; there is no combination that reaches the target without this
  section. Trimming §7 is worth doing and is a follow-on, not a substitute.
- **Repoint `role_doc` at the sidecar and let the role doc become the "current state"
  file.** Rejected — it moves the `**Version:**` line out from under five readers that
  resolve `role_doc` to find it, converting a documentation change into a harness change
  with no benefit. Two subjects, two keys.
- **Convention over declaration — always look for `<role-doc-stem>-CHANGELOG.md`.**
  Rejected. A guess that silently succeeds is how the WI-0282 defect worked; and a member
  is entitled to put its history somewhere else. Declared or inline, never inferred.
- **Fall back to the role doc when a declared sidecar is unreadable.** Rejected as D4
  states — it is the silent-empty-set bug wearing a different hat.
- **A second `target-file` in the brief header.** Rejected: the header's target is defined
  by carrying the version line. Two version-gated targets is a different and worse schema
  than one op with a destination.
- **Regress federation briefs to `Apply: manual`.** Rejected — it contradicts
  [ADR-0049](0049-apply-auto-is-the-authoring-default.md) and pays for a file move with a
  permanent manual step at every future adoption.
- **Split the whole fleet in the same pass.** Rejected as out of scope and not ours to rule
  alone. The other members' role docs are a fraction of that size and none is on anyone's critical path;
  the federation's was 332 KB. Split the one that hurts, leave the default alone, and let
  evidence decide the rest.

## Consequences

- The per-session role-doc read drops **332,752 B → 89,369 B**. The finish-line
  `probe_start_budget` re-measures it from `session.CFG["role_doc"]` with no change.
- **The federation is fleet-first.** The bootstrap kit still ships inline, so a freshly
  bootstrapped Architect is unaffected. If the split is later ruled fleet-wide, the kit,
  `role-doc-template.md`, and a retrofit brief per member are the work — and D3 already
  makes the mechanism member-agnostic.
- `curate/deliver.py` now imports `session` for `member_layout`, joining
  `curate/metrics.py` on the WI-0029 pattern. `_roledoc_path`'s hand-rolled config read is
  replaced by the shared resolver, so a member declaring `role_doc` in a `layout` block is
  now honoured by delivery too — it was not before.
- **WI-0282 lands with this**, and had to: a sidecar built on the old `changelog_edit_ids`
  would have inherited the silent empty set on day one.
- Anyone reading the role doc's history now follows one link. No inbound anchor pointed at
  `#changelog`, so nothing breaks.
- The move exposed three pre-existing data defects in the history it carried verbatim: a
  duplicated `2.58.0` version, one descending-order violation at `2.57.0`/`2.58.0`, and
  irregular blank-line separation. Recorded, not silently repaired — rewriting entries
  during a move would destroy the audit property the move exists to preserve.

## References

- [ADR-0013](0013-receipt-ritual.md) — the receipt ritual; its adoption-locus clause is superseded here.
- [ADR-0010](0010-registries-canon-only-sidecar-history.md) — the sidecar-history pattern, cited for its clean-main-file clause only.
- [ADR-0007](0007-github-as-system-storage-data-excluded.md) / [ADR-0088](0088-a-mailbox-is-gitignored-inbound-data-fleet-wide.md) — why the mailbox is not durable and the CHANGELOG is.
- [ADR-0039](0039-appliable-brief-schema-and-auto-adopt-mechanism.md) / [ADR-0049](0049-apply-auto-is-the-authoring-default.md) — the brief schema D5 extends, and the default it protects.
- [ADR-0035](0035-startup-turn-budget-inject-and-git-diagnosis.md) — the role doc is the orientation read the harness does not inject.
- WI-0284 (this decision), WI-0282 (the `changelog_edit_ids` defect landing with it), WI-0029 (the one layout door).
- `proposed-edits/federation-arch/pending/2026-09-04-consultant-finish-line-tests-and-scoreboard.md` R6; `…/2026-09-07-consultant-curator-session-mode.md` R3.
