# ADR-0052: Canon budget + consolidation ritual — govern the governor

**Status:** Accepted
**Date:** 2026-07-18
**Deciders:** the operator, Federation Architect

## Context

The injected doctrine set — [`CANON.md`](../CANON.md) (every Accepted principle + universal
habit, one line each) plus [`STANDARD.md`](../STANDARD.md) (the standard role-doc section:
session rituals + substrate) — is injected into **every Architect's context at every session
start, forever** ([ADR-0022](0022-architect-ingest-distillation-and-code-channel.md) /
[ADR-0024](0024-standard-role-doc-section-is-generated-and-injected.md)). It is the one cost
paid on every session by every member of the fleet.

That set only ever grows. Every curate pass can add a principle or a habit; every substrate
change can lengthen `STANDARD.md`; the ADR corpus that canon distills from is at 51 and rising.
**Nothing in the system prunes, merges, or retires.** The presenting complaint that motivated
the whole zero-touch/startup-budget arc — slow session starts — recurs at the doctrine
layer itself, structurally, unless something pushes back on growth the way
[ADR-0035](0035-startup-turn-budget-inject-and-git-diagnosis.md) pushed back on read-turns.

Measured today (session 72, `wc -c`):

| File | Chars | Note |
|---|---|---|
| `CANON.md` | 13,177 | 20 principles · 34 universal habits, one line each |
| `STANDARD.md` | 25,080 | session rituals + standard substrate section |
| **Injected doctrine set** | **38,257** | ~9.5k tokens, paid every session by every Architect |

There is no signal on this number — its growth is *felt* (as slow startups) rather than
*seen*. And there is no ritual that ever spends effort making the set smaller; the entire
propagation pipeline is built to ship growth, never shrinkage.

This ADR is commissioned by the external consultant brief
`2026-07-14-self-governance-hardening` **R2** (Auditor role class,
[ADR-0004](0004-auditor-as-separate-role-class.md)), dispositioned by the operator (session 72).
It is the doctrine-layer sibling of the metrics mining layer
([ADR-0048](0048-federation-metrics-mining-layer.md)): metrics made *fleet health* visible;
this makes *doctrine mass* visible, and adds the one ritual that can push it back down.

## Decision

**The injected doctrine set carries a size budget, measured deterministically. Crossing the
budget does not block the change that crossed it — it triggers a consolidation ritual that is
owed until the set is back under budget or the budget is deliberately raised. Doctrine mass is
a mined trend line, not a felt one.**

### 1. The budget

- **Measured artifact:** the injected doctrine set = `CANON.md` + `STANDARD.md` (the two files
  the harness injects every session). The prior-session entry and user profile are also
  injected but are *not* doctrine-that-only-grows — they are out of scope. The ADR corpus is
  *not* in the budget (ADRs aren't injected); ADR-outcome compression is a consolidation
  *technique* (§3), not a budgeted quantity.
- **Unit:** **characters** (`wc -c` on the two files, summed). Characters are deterministic and
  tokenizer-independent — a token count needs a model-specific tokenizer and would violate
  [P15](../principles/master.md#p15--code-for-mechanism-not-judgment) for a mined metric. Chars
  are a stable proxy; the human intuition is ~chars ÷ 4 ≈ tokens.
- **Ceiling:** **48,000 characters** for the combined set — ~25% headroom over today's 38,257,
  enough for a handful of genuinely-needed additions before the trigger fires, tight enough to
  create real back-pressure within a few sessions of unchecked growth. The ceiling is a
  federation-owned constant, sourced to this ADR, encoded in `curate/metrics.py`; changing it is
  a deliberate, recorded act (a new commit + a note here), never a silent bump to dodge a
  consolidation pass.

### 2. The budget is a soft trigger, never a hard block

Exceeding the budget **must not block** the change that exceeded it. A genuinely-needed new
principle or habit ships when it is ready ([P11](../principles/master.md#p11--ship-working-system-on-time)
/ [P12](../principles/master.md#p12--manage-scope-for-schedule)); this preserves
[ADR-0023](0023-standard-operating-substrate.md)'s *additions-allowed* stance. Crossing the
budget instead **flags a consolidation pass as owed** — visible in the metrics exception report
until the set is back under budget or the ceiling is deliberately raised. Back-pressure, not a
gate.

### 3. The consolidation ritual

Triggered by **either** the budget being crossed **or** the quarter boundary (whichever comes
first). A pass does some or all of:

1. **Merge overlapping habits** — two habits saying the same thing under one parent principle
   collapse into one sharper statement.
2. **Retire superseded habits/principles** — an entry a later one subsumes is retired. The
   registry **keeps the retired record** via the canon-only + append-only sidecar-history model
   ([ADR-0010](0010-registries-canon-only-sidecar-history.md)) — retiring removes it from the
   *injected* set, never from the *audit* record.
3. **Compress long-stable ADR outcomes into registry lines** — a settled ADR's operative rule
   becomes a one-line registry/canon reference, so future readers chase one line instead of a
   full ADR. (Reduces reference-chasing cost; ADRs stay immutable.)
4. **Tighten `STANDARD.md` prose** — via its source `standard-source.md`
   ([ADR-0024](0024-standard-role-doc-section-is-generated-and-injected.md)); regenerate, never
   hand-edit the generated file.

A pass produces: a short **distillation report** (what merged, what retired, why), the registry
+ sidecar-history edits, a regenerated `CANON.md` (`curate/distill.py`) / `STANDARD.md`
(`curate/standardize.py`), and — for any retirement — **normal fleet briefs**, because the
propagation pipeline handles shrinkage exactly as it handles growth: a retirement is a
`replace`/`remove` brief that lands at each Architect's next startup like any other edit
([ADR-0029](0029-receiving-architects-auto-adopt-at-startup.md)). Retiring from canon is a
**curator act shipped fleet-wide** (everyone shrinks together) — it is *not* a member
subtracting from the floor, so [ADR-0023](0023-standard-operating-substrate.md)'s
*no-subtraction* rule is not in tension: no-subtraction bars a member from opting out of the
shared floor; consolidation lowers the floor for everyone at once.

### 4. The trend line — mined, in the metrics layer

Doctrine mass becomes a mined metric in `curate/metrics.py`
([ADR-0048](0048-federation-metrics-mining-layer.md)), never self-reported:

- **Live signal (cheap, every startup):** current injected-set size vs. the 48,000 ceiling. When
  over, `--exceptions` emits one line — *"injected doctrine set N chars, over budget by M —
  consolidation pass owed"* — and `--status` counts it as an attention item. Under budget: silent.
- **Trend (on-demand, `--evidence`):** injected-set size over time, mined from the **git history**
  of `CANON.md` + `STANDARD.md` (deterministic, already recorded — no new stamp artifact), plus
  the principle/habit counts. Computed only in the evidence rendering, not on every startup, so
  the startup probe stays fast ([ADR-0048](0048-federation-metrics-mining-layer.md)'s fail-open /
  fast-sweep constraint).

## Alternatives Considered

- **Budget in tokens, not characters.** Rejected: tokens require a model-specific tokenizer,
  which is nondeterministic across models and versions and would make the mined number
  un-reproducible ([P15](../principles/master.md#p15--code-for-mechanism-not-judgment)). Chars
  are an exact, tokenizer-free proxy; the ~÷4 intuition covers the human read.
- **A hard block at the ceiling.** Rejected: it would let doctrine-size gate a genuinely-needed
  principle, inverting the priority ([P11](../principles/master.md#p11--ship-working-system-on-time)).
  The budget must bend to a real need and register the debt, not refuse the need.
- **Separate per-file budgets (CANON vs. STANDARD).** Rejected for now: two ceilings is more
  machinery for no decision it changes — the cost paid per session is the *sum*, and the
  `--evidence` breakdown still shows each file. Revisit only if one file's growth is
  systematically masking the other's shrinkage.
- **Budget the whole ADR corpus / repo mass.** Rejected: those aren't injected per session, so
  they aren't the recurring cost this ADR governs. ADR compression is a consolidation technique,
  not a budgeted quantity.
- **A stamped size-record artifact appended on each canon change (for the trend).** Rejected: git
  history already records every size of both files deterministically; a parallel stamp file is a
  redundant second source to keep in sync ([P16](../principles/master.md#p16--avoid-duplication)).
- **Do nothing / keep it a felt cost.** Rejected: this is exactly the "rolled it out, forgot the
  rest" / slow-startup class the federation has repeatedly chosen to make *seen* rather
  than *felt* — the metrics layer and the standard-version rollout are the precedents.

## Consequences

- Doctrine mass stops being invisible: the federation can see its own injected-set size and its
  trend, and a consolidation pass becomes *owed and visible* the moment growth crosses the line —
  instead of surfacing years later as unexplained startup slowness.
- The propagation pipeline is now understood to run **both directions**: it ships shrinkage
  (retirements) exactly as it ships growth. This is a genuinely new use of the brief mechanism —
  every prior brief added or changed doctrine; none removed it.
- **New surfaces:** the 48,000-char ceiling constant + the size/over-budget signal in
  `curate/metrics.py` (extends [ADR-0048](0048-federation-metrics-mining-layer.md)); the trend
  rendering in `--evidence`; a quarterly-or-triggered consolidation ritual (a federation session
  step, not a member obligation). The metrics `--status` probe gains one more possible attention
  line; it must stay fail-open and fast (the ADR-0048 constraint).
- **Downstream work this ADR implies (none built here):** (a) implement the budget constant +
  live over-budget signal in `metrics.py`; (b) implement the `--evidence` trend rendering from
  git history; (c) run the **first consolidation pass** — likely due soon given the set is
  already at 38k against a 48k ceiling and no pass has ever run. The first pass finding real
  merge/retire candidates is the ritual working, the same way the metrics layer's first run
  correctly flagged real stranded briefs.
- **Interaction with R3 (mined verification, same brief):** the over-budget signal is one more
  conformance line the metrics layer emits — boring-or-actionable, never impressive
  ([ADR-0048](0048-federation-metrics-mining-layer.md)).

**AMENDED 2026-09-13 (session ~330, WI-0208).** §2's soft budget is superseded by
[ADR-0137](0137-the-canon-budget-is-a-land-gate-not-a-report.md): a change that is over the
ceiling AND grows the set no longer lands. §2 rejected a hard block by name, predicting that a
reported ceiling would "create real back-pressure within a few sessions." Measured over the
28.4 days to 2026-09-13: **16 first-parent size steps, 14 UP and 2 DOWN**, the two down-steps
totalling 264 bytes against 21,748 bytes of growth; the set went 46,444 → 67,928; the budget
was re-crossed **three days** after the pass that cleared it; over ceiling 25 of the last 28
days. Everything else in this ADR stands unchanged — the ceiling, the two-file definition of
the set, the four techniques, the mined trend and the exceptions line. The consolidation pass
§2 deferred was taken in the same session, by the structural route in
[ADR-0136](0136-the-standard-splits-into-an-injected-tier-and-a-reference-tier.md) rather than
by the merge/retire work over canon, because **subtraction could not clear the ceiling**:
every meaning-preserving cut available totalled 15,907 bytes and still landed 4,021 over.

**Reality status (per [`mark-adr-reality-status`](../habits/master.md#mark-adr-reality-status)):**
**Built** — the budget constant, the over-budget signal (`--exceptions` / `--status`) and the
`--evidence` size-over-time trend in `curate/metrics.py`; the **land gate** in
`curate/check_canon_budget.py` (ADR-0137, 13 tests in `tests/test_canon_budget_gate.py`,
proven on this repo's own pre-consolidation set); and the **first consolidation pass**, landed
2026-09-13, which brought the set to 44,930 of 48,000. The fleet retirement brief WI-0010 owed
is still **Not-built** and is sequenced deliberately AFTER the pass — every reachable
member is on pre-consolidation substrate, so pushing before the consolidation would have
taken the fleet from 732,997 bytes to 815,136 rather than to roughly 576,000.

*The figures this block carried until 2026-09-13 ("at 38,257 of the 48,000 ceiling today, so
no consolidation is owed yet") were from session 72 and had been false for weeks. A Reality
block that states a measurement states it as of a date; this one did not, and nothing
re-checked it.*

## References

- Consultant brief: `proposed-edits/federation-arch/pending/2026-07-14-self-governance-hardening.md`
  R2.
- [ADR-0048](0048-federation-metrics-mining-layer.md) — the metrics mining layer this trend line rides on.
- [ADR-0022](0022-architect-ingest-distillation-and-code-channel.md) — `CANON.md` generation + injection (the cost being budgeted).
- [ADR-0024](0024-standard-role-doc-section-is-generated-and-injected.md) — `STANDARD.md` generation + injection; `standard-source.md` as the tighten-here source.
- [ADR-0010](0010-registries-canon-only-sidecar-history.md) — canon-only + sidecar history; how a retirement keeps its record.
- [ADR-0023](0023-standard-operating-substrate.md) — additions-allowed / no-subtraction; why fleet-wide retirement is not a subtraction.
- [ADR-0029](0029-receiving-architects-auto-adopt-at-startup.md) — how a retirement brief lands at each Architect.
- [P15](../principles/master.md#p15--code-for-mechanism-not-judgment) / [P16](../principles/master.md#p16--avoid-duplication) — deterministic mining; single-source trend.
