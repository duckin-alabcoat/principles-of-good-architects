# Design — inbox auto-adopt (`apply.py` / `session.py apply-briefs`)

**Status:** BUILT (session 51) — increments 2–4 landed federation-only ([ADR-0039](../adr/0039-appliable-brief-schema-and-auto-adopt-mechanism.md), `curate/apply.py`, `tests/test_apply.py`, wired into the federation `SessionStart`). Increment 5 (lift the core into a shipped `session.py apply-briefs` for the fleet) is **deferred** — it rides a `push-substrate` run, gated behind the settings-clobber hazard. Design retained below for the record.
> **Original status:** DESIGN APPROVED (session 49) — forks decided, ready to build increment 2. the operator: build it as its own fresh session.
**Drafted:** session 49 (2026-07-06), Federation Architect.
**Source:** startup brief item 3 (`proposed-edits/federation-arch/applied/2026-07-05-startup-turn-budget-and-git-diagnosis.md` §3); mechanizes [ADR-0029](../adr/0029-receiving-architects-auto-adopt-at-startup.md). Handoff calls it "the largest single build; its own session."

## Goal

At SessionStart, a deterministic pass reads the repo-local inbox (`pending/`), **applies clean briefs before the model wakes**, and emits an applied/surfaced list into orientation. The Architect *reports* what code did and only handles the judgment cases. ADR-0029's "never half-apply" becomes a code guarantee (apply to a copy, verify, atomic-replace), not an instruction to a model.

## The load-bearing finding (why this is design-first, not just coding)

The startup brief asserts "the brief format already carries everything needed." **It does not, uniformly.** Reviewing real applied briefs (e.g. an earlier status-surface redistribution brief), the current format mixes deterministic and judgment content in the *same* brief:

- ✅ Deterministic: `### Before` / `### After` fenced blocks = an anchored find-and-replace.
- ❌ Judgment, today, in "clean" briefs:
  - `### Insertion site` prose: *"insert between step 2 and 3, renumber 3→4, 4→5, 5→6"* — the After block doesn't encode the renumber.
  - Side-effects in prose: *"update the W command's parenthetical step list too."*
  - Multi-part briefs: Part 2 = *seed a new file at repo root* (not a text anchor at all).
  - CHANGELOG text given as a template to adapt, not a literal block.

So a brief is auto-appliable **only if** it is authored to a stricter schema. The build is therefore two things: **(1) define the strict schema** apply.py enforces, and **(2) build apply.py** against it — with anything non-conforming routed to the existing `Apply: manual` / surface path (no partial or guessed application, ever).

## The strict appliable-brief schema (proposed)

A brief is `Apply: auto` **iff** it declares so and every mutation is one of these fully-specified operations. Anything else → `Apply: manual`.

Header (machine-read):
```
Edit ID: <id>
Target file: <repo-relative path>
Expected base version: vX.Y.Z        # must equal the target's current version
Proposed new version: vX.Y.Z         # explicit — no "next MINOR"
Apply: auto
```

Body = an ordered list of operations, each self-contained:

- **`replace`** — a `### Before` block whose text occurs **exactly once** in the target, and an `### After` block. Uniqueness is the anchor; zero or multiple matches → surface (never guess). No renumbering implied — if numbers change, the Before/After blocks must span the full renumbered range so the replacement is literal.
- **`create-file`** — a target path (must not exist) + a fenced content block. Refuses if the file exists.
- **`version-bump`** — the `**Version:**`/header line replacement, derived from Expected→Proposed (a specialized `replace` so it's uniform).
- **`changelog-prepend`** — a literal block inserted at a fixed anchor (the CHANGELOG marker line). Literal, not a template.

Explicitly **out** of auto (→ manual): "renumber the following", "also update X elsewhere", "set focus to a true value", any instruction whose result depends on reading surrounding context. These are exactly ADR-0029's judgment carve-outs, now enforced structurally.

## apply.py — architecture

`curate/apply.py`, pure-stdlib, mirrors the `reconcile.py` / `gather.py` shape (deterministic, read-mostly, one clear job). Pipeline per brief in `pending/`:

1. **Parse + classify.** Read header. If `Apply: manual`, or missing/!= `Expected base version`, or any operation not in the strict set, or any anchor non-unique → **surface, don't apply** (reason recorded).
2. **Plan.** Build the ordered op list against an **in-memory copy** of each target file.
3. **Verify.** Re-open targets, re-check every anchor still unique, apply to the copy, assert the copy differs as expected and the version line moved Expected→Proposed. Any mismatch → abort this brief whole (atomicity), surface it.
4. **Commit to disk.** `atomic_write` (temp + `os.replace`, already in `common.py`) each target. All-or-nothing per brief: stage all target copies, only `os.replace` them once every op verified.
5. **File the brief.** Move `pending/<id>.md` → `applied/<id>.md`, stamp `State: Applied` + `Applied at role-doc version`.
6. **Emit** a one-line-per-brief applied/surfaced summary for the orientation block.

Safety invariants: never partial-apply (whole-brief atomicity); never apply on version drift; never guess an ambiguous anchor; never touch a brief that isn't `Apply: auto`. Git is the backstop — apply.py does not commit; the session-end close commits, so a bad apply is visible in `git diff` before it's ever committed (and the model reports it).

## Forks — DECIDED (the operator, session 49)

- **Fork A → build `curate/apply.py` federation-first**, dogfood, then lift the verified core into `session.py apply-briefs` for the fleet.
- **Fork B → new-briefs-only.** Do not retrofit pending prose briefs.
- **Fork C → schema in an ADR + `PROCESS.md` + a kit brief template; apply.py docstring is the executable spec.**

Rationale for each is below; kept for the record.

## Forks (rationale, for the record)

### Fork A — `curate/apply.py` (federation-only tool) vs `session.py apply-briefs` (shipped subcommand)
- **`session.py apply-briefs`** — auto-adopt runs at *every* Architect's startup, which is the whole point (ADR-0029 pays "at every federated Architect's startup"). But it enlarges the one script that ships verbatim fleet-wide, and not every member is on `session.py` yet.
- **`curate/apply.py`** — federation-only, faster to iterate, zero fleet blast radius; but then Architects don't get auto-adopt until it's ported into `session.py` later.
- **Recommendation: build `curate/apply.py` first, federation-only, dogfood it, then lift the verified core into `session.py apply-briefs`.** Matches the handoff's "federation-first-later," and every other harness piece (distill/standardize/gen_settings) was proven federation-side before shipping. Two increments, low risk.

### Fork B — retrofit existing pending briefs to the strict schema, or only new briefs?
When this was designed, pending briefs were `Apply: manual` prose, not `Apply: auto`. So apply.py starts with no customers — it's infrastructure ahead of demand (which the handoff acknowledges).
- **Recommendation: new-briefs-only.** Don't rewrite historical/pending briefs into the strict schema — they're already being applied manually and one of them is mid-flight. apply.py starts earning the moment the *next* canon/substrate redistribution is authored `Apply: auto`. Define the schema, ship the tool, and author future briefs to it.

### Fork C (minor) — where the strict schema lives
It's federation-owned brief format → document it in `standard-source.md`? No — the brief *format* is a federation-internal redistribution mechanism, not Architect-facing standard substrate. **Recommendation: document the schema in `PROCESS.md` (or an ADR) + a `bootstrap-kit` brief template; apply.py's docstring is the executable spec.** An ADR ("appliable-brief schema + auto-adopt mechanism") records the decision.

## Test plan (R1 suite, `tests/test_apply.py`)
- replace: unique anchor applies; zero-match surfaces; multi-match surfaces (never guesses).
- version gate: Expected != current → surface, no write.
- atomicity: op 2 fails after op 1 planned → target on disk unchanged (both or neither).
- create-file: applies when absent; refuses when present.
- Apply: manual / missing header → untouched, surfaced.
- pending→applied move only on full success; stamp correct.
- dry-run mode writes nothing.

## Proposed build increments
1. ✅ **This design + ADR + strict schema** (schema is the leverage point). — ADR-0039 (session 51).
2. ✅ `curate/apply.py` parser + classifier + `--dry-run` + tests (the heart; no disk writes yet). — session 51.
3. ✅ Mutation engine (plan→verify→atomic-replace, whole-brief atomicity) + tests. — session 51 (folded into the same build; 24 tests).
4. ✅ Wire apply into federation SessionStart; ~~dogfood by authoring one real redistribution brief~~ → **synthetic/dry-run dogfood** (the operator's session-51 call — no genuine redistribution pending, so a throwaway self-edit wasn't authored; the positive path is proven by the test suite + a temp-inbox dogfood). — session 51.
5. ⏳ **Deferred:** lift the verified core into `session.py apply-briefs`; ship fleet-wide on a substrate push (Fork A step 2). Gated behind settling the `push-substrate` settings-clobber hazard and the operator's fleet greenlight.
