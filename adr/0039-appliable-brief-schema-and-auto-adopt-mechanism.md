# ADR-0039: Appliable-brief schema + auto-adopt mechanism (`apply.py`)

**Status:** Accepted
**Date:** 2026-07-06
**Deciders:** the operator, Federation Architect

## Context

[ADR-0029](0029-receiving-architects-auto-adopt-at-startup.md) decided that a
receiving Architect **auto-adopts** pending federation edits at startup rather than
re-asking the user to gate them — the user's approval already happened upstream at
registry-Accept, so a second gate at delivery is redundant. Until now that adoption
was performed **by the model**: session-start step 7 tells the Architect to read each
`pending/` brief, apply the `## After` content, bump the role-doc version, add the
CHANGELOG line, and move the brief `pending/` → `applied/`. ADR-0029's central safety
promise — *"never half-apply"* — was an **instruction to a model**, not a guarantee.

That is exactly the class of work [P15](../principles/master.md#p15--code-for-mechanism-not-judgment)
(`code-for-mechanism-not-judgment`) says belongs in code: a deterministic
find-and-replace against a versioned anchor is not a judgment call. The
[design](../design/inbox-auto-adopt.md) (approved session 49) set out to mechanize it
as `apply.py` — apply clean briefs to a verified copy and atomic-replace, before the
model wakes, with anything ambiguous routed to the human.

The load-bearing finding of that design: **the existing brief format cannot be applied
deterministically.** Real applied briefs (e.g.
`applied/2026-06-05-federation-p14-adoption.md`) mix deterministic and judgment content
in the same document — a `### Before`/`### After` pair (mechanical) sits beside prose
like *"append after the P13 row,"* *"renumber 3→4, 4→5,"* *"also update the related command's
step list,"* and CHANGELOG text *given as a template to adapt.* A parser cannot execute
"renumber the following" without reading surrounding context — that is judgment. So
auto-application is not a parsing problem to be solved against today's briefs; it
requires a **stricter brief schema** that a brief must be *authored to* in order to
qualify as `Apply: auto`. Anything not conforming falls through to the existing
`Apply: manual` / surface path — no partial or guessed application, ever.

This ADR fixes that schema and the mechanism that consumes it. It is the ADR piece owed
by increment 1 of the design ("this design + ADR + strict schema"); the design doc
itself is the companion.

## Decision

### 1. The strict appliable-brief schema

A brief is `Apply: auto` **iff** it declares so in a machine-read header **and** every
mutation is one of four fully-specified operations. Anything else → `Apply: manual`
(surfaced to the human, never auto-applied).

**Header — YAML frontmatter** at the very top of the brief (chosen over bold-markdown
fields because frontmatter is unambiguous to parse and cannot be typo'd into a
half-recognized field):

```
---
edit-id: <id>
target-file: <repo-relative path to the file the ops mutate>
expected-base-version: vX.Y.Z      # MUST equal target-file's current **Version:** line
proposed-new-version: vX.Y.Z       # explicit — never "next MINOR"
apply: auto
---
```

**Body — an ordered list of operations.** Each operation is introduced by a
`## op: <type>` heading. Operation *content* is carried in **tilde-fenced blocks**
(`~~~before` … `~~~`), not backtick fences: role docs contain backtick (```` ``` ````)
fences themselves, so a backtick-fenced payload could not safely wrap them. Tilde
fences of length ≥3 nest cleanly around arbitrary backtick content (CommonMark rule;
lengthen the tilde run if a payload itself contains `~~~`).

The four operations:

- **`replace`** — a `~~~before` block whose text occurs **exactly once** in
  `target-file`, and a `~~~after` block. **Uniqueness is the anchor:** zero or multiple
  matches → surface (never guess which). No renumbering is ever implied — if line
  numbers change, the before/after blocks must span the **full** renumbered range so the
  replacement is literal.
- **`create-file`** — a `Path:` line (repo-relative, must **not** exist) + a
  `~~~content` block. Refuses if the file exists (never clobbers).
- **`version-bump`** — no block. A specialised `replace` on `target-file`'s
  `**Version:** <expected>` line, derived from `expected-base-version` →
  `proposed-new-version`. Requires exactly one matching Version line.
- **`changelog-prepend`** — an `After:` line naming an anchor line that occurs
  **exactly once** in `target-file`, + a `~~~content` block inserted immediately after
  that anchor. The content is a **literal** block, not a template to adapt.
  *Extended by [ADR-0123](0123-the-role-doc-changelog-moves-to-a-tracked-sibling.md):*
  an optional `File:` names a different, already-existing file to prepend into, so a
  system whose role-doc changelog lives in a tracked sibling can still bump the role
  doc and write its entry in one `auto` application. Omitting it is byte-identical to
  the original behaviour; a `File:` resolving outside the repo is refused.

Explicitly **out of `auto`** (→ `manual`): "renumber the following," "also update X
elsewhere," "set focus to a true value," or any instruction whose result depends on
reading surrounding context. These are precisely ADR-0029's judgment carve-outs, now
enforced structurally rather than trusted to a model.

The schema's normative, executable specification is `curate/apply.py`'s module
docstring + code; this ADR records the decision, `PROCESS.md` documents it for authors,
and `bootstrap-kit/` carries an authored template.

### 2. The mechanism — `apply.py`

`curate/apply.py`, pure-stdlib, mirrors the `reconcile.py` / `gather.py` shape
(deterministic, one clear job, shares `curate/common.py`). Per brief in `pending/`:

1. **Parse + classify.** Read the frontmatter. If `apply` ≠ `auto`, or
   `expected-base-version` is missing / ≠ `target-file`'s current version, or any
   operation is not in the strict set, or any anchor is non-unique → **surface, do not
   apply** (the reason is recorded and reported).
2. **Plan.** Build the ordered op list against an **in-memory copy** of each touched
   file.
3. **Verify.** Re-check every anchor is still unique, apply to the copy, assert the copy
   differs as expected and the version line moved expected → proposed. Any mismatch →
   abort **this brief whole** (atomicity) and surface it.
4. **Commit to disk.** `atomic_write` (temp + `os.replace`, already in `common.py`) each
   touched file — all-or-nothing per brief.
5. **File the brief.** Move `pending/<id>.md` → `applied/<id>.md`, stamping the applied
   timestamp + role-doc version into its frontmatter.
6. **Emit** a one-line-per-brief applied/surfaced summary for the orientation block.

**Safety invariants:** never partial-apply (whole-brief atomicity); never apply on
version drift; never guess an ambiguous anchor; never touch a brief that isn't
`apply: auto`. **`apply.py` does not commit** — the session-end close commits, so a bad
apply is visible in `git diff` before it is ever persisted, and the model reports what
code did. Any unexpected error **fails open** (applies nothing, surfaces the error, exits
0) so it can never brick startup — the same discipline as the `check-bash` guard.

### 3. Rollout — federation-first, then the fleet (Fork A)

`apply.py` is built and dogfooded **federation-only** first, then its verified core is
lifted into a shipped `session.py apply-briefs` subcommand so auto-adopt runs at every
Architect's startup (the whole point of ADR-0029). This matches how every other harness
piece (`distill` / `standardize` / `gen_settings`) was proven federation-side before
shipping, and keeps fleet blast radius at zero until the core is proven. The fleet lift
is a **later** increment, deliberately deferred — it rides a `push-substrate` run, which
carries its own hazards to settle first.

Wiring for the federation: `apply.py` runs as a `SessionStart` (matcher `startup`) hook,
added via `session.config.json` `settings_extras` (like the existing `gather`/`reconcile`
status probes), so it is federation-only and does not touch the shipped floor.

### 4. New briefs only (Fork B)

The strict schema is **not** retrofitted onto existing pending prose briefs. Today's
pending briefs are all `Apply: manual` (a member alignment, a member Option B) and are already being
handled manually; rewriting historical briefs buys nothing. `apply.py` starts earning
the moment the **next** canon/substrate redistribution is authored `Apply: auto`. It is
infrastructure slightly ahead of demand, by design.

## Alternatives Considered

- **Parse today's loose briefs directly.** Rejected — the load-bearing finding: today's
  "clean" briefs embed judgment (renumber, side-effects elsewhere, template CHANGELOG
  text) a parser cannot execute without guessing. Determinism requires authoring to a
  schema, not decoding prose.
- **Bold-markdown header fields** (`**Apply:** auto`) instead of frontmatter. Rejected —
  frontmatter parses unambiguously and a malformed field fails classification loudly
  rather than half-matching.
- **Backtick-fenced op payloads.** Rejected — role docs contain backtick fences, so a
  backtick payload cannot wrap them. Tilde fences nest around arbitrary backtick content.
- **Ship `session.py apply-briefs` first** (fleet-wide immediately). Rejected for now —
  larger blast radius on the one script that ships verbatim everywhere, and a member is not on
  `session.py` yet. Federation-first, lift later (Fork A).
- **Model keeps applying briefs (status quo).** Rejected — "never half-apply" as a model
  instruction is not a guarantee; a deterministic anchored replace belongs in code
  ([P15](../principles/master.md#p15--code-for-mechanism-not-judgment)).
- **`apply.py` commits its own changes.** Rejected — not committing keeps git as the
  backstop: a bad auto-apply shows in `git diff` before the session-end commit, and the
  model reports it. Single writer of the commit stays the session-end close.

## Consequences

- ADR-0029's "never half-apply" becomes a **code guarantee** (apply to a verified copy,
  atomic-replace, whole-brief atomicity), not a model instruction.
- Federation authors of redistribution briefs gain a second target format: a brief is
  either authored to the strict `Apply: auto` schema (and auto-applies) or stays
  `Apply: manual` prose (and surfaces). The schema is documented in `PROCESS.md` + a
  `bootstrap-kit` template; `apply.py`'s docstring is the executable spec.
- On the federation's next startup, `apply.py` scans `pending/`, auto-applies any
  conforming `Apply: auto` brief, and surfaces the rest (the current a member `Apply: manual`
  brief surfaces untouched, exactly as before).
- **Downstream work (deferred, tracked):** lift the verified core into
  `session.py apply-briefs` and ship it fleet-wide on a substrate push (Fork A step 2) —
  gated behind settling the `push-substrate` settings-clobber hazard. Until then,
  auto-adopt is a federation-only capability; other Architects continue to apply briefs
  via the model at startup step 7.
- `apply.py` joins the `tests/` green-suite precondition that `push-substrate.py`
  enforces, so a regression in the apply engine cannot propagate.

## Update — 2026-07-15 (session 66): Fork A shipped

The deferred downstream work above is done. The `push-substrate` settings-clobber hazard
that gated Fork A is confirmed **structurally settled** ([ADR-0034](0034-settings-are-federation-pushed-generated-substrate.md):
settings are generated from each repo's declared `settings_extras`, and the push is
refresh-only + skips uncommitted substrate edits — a push cannot erase a system's local
hooks/permissions). So the verified engine was **lifted from `curate/apply.py` into the
byte-identical shipped `session.py` as the `apply-briefs` subcommand** (reusing session's
own `atomic_write`; the inbox comes from `CFG["inbox"]`, and a repo with no inbox no-ops).
`curate/apply.py` is **retired** (single-source, [P16](../principles/master.md#p16--avoid-duplication)),
`tests/test_apply.py` retargets the lifted engine, and the federation's own `SessionStart`
hook now runs `session.py apply-briefs`. Every Architect that carries the shipped
`session.py` now *has* the capability. The **remaining** step (zero-touch-adoption R1
proper): promote the `apply-briefs` startup hook into the standard settings **floor** so it
is wired on for the whole fleet, and push. Driven by the consultant brief
`2026-07-14-zero-touch-adoption` (R1), the operator's "do 2 then 1" disposition, session 66.

## Update — 2026-07-15 (session 67): version-line dialect tolerance

The engine's `VERSION_LINE` regex matched only the strict `**Version:** X.Y.Z` dialect (colon
inside the bold, no wrapper). One member's role doc carries a line of the form `` **Version**: `vX.Y.Z` `` — colon
*outside* the bold, backtick-wrapped, `v`-prefixed — which `current_version()` couldn't parse, so
its comms-adoption brief surfaced as un-appliable and would have been forced to `manual`. Under the
*everything-automatic, everything-code* rule ([ADR-0049](0049-apply-auto-is-the-authoring-default.md),
the operator session 67), a parse gap is an engine bug, not a reason to fall back to a human. `VERSION_LINE`
was broadened to the **same tolerance `role_doc_version()` already had** (colon inside or outside
the bold, optional backtick, optional `v`), with three capture groups so `version-bump` rewrites the
line **style-preservingly** — a wrapped `` `v1.4.2` `` becomes `` `v1.4.3` ``, an unwrapped
`1.4.2` stays unwrapped. The strict dialect is unaffected (regression-tested). Result: that member
auto-adopts on the code path like every Claude-runtime member. Whole-brief atomicity, base-version
verification, and the never-guess uniqueness rule are unchanged. Covered by new `test_apply.py`
cases (`VariantVersionLineTest`).

## References

- [design/inbox-auto-adopt.md](../design/inbox-auto-adopt.md) — the approved design; this
  ADR is its increment-1 decision record.
- [ADR-0029](0029-receiving-architects-auto-adopt-at-startup.md) — auto-adopt at startup;
  this mechanizes its "never half-apply" promise.
- [ADR-0013](0013-receipt-ritual.md) — the receipt ritual / inbox model this consumes.
- [ADR-0028](0028-converge-inbox-model-onto-repo-local.md) — repo-local `pending/`/`applied/` inbox layout.
- [ADR-0020](0020-session-rituals-are-a-code-harness.md) — session rituals as a code harness (sibling pattern).
- [ADR-0022](0022-architect-ingest-distillation-and-code-channel.md) — the code channel + `session.config.json` per-system config.
- [P15 `code-for-mechanism-not-judgment`](../principles/master.md#p15--code-for-mechanism-not-judgment) · [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence).
