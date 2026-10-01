# ADR-0048: Federation metrics — one deterministic mining layer, three renderings

**Status:** Accepted
**Date:** 2026-07-15
**Deciders:** the operator, Federation Architect

## Context

The federation governs a growing fleet, but it cannot *see* it. Three startup probes
already surface point signals — `gather.py --status` (curate backlog), `reconcile.py
--status` (mirror drift), `standard_version.py --fleet-status` (rollout parity) — but there
is no consolidated view of fleet health, and no evidence base for the claims POGA makes
about itself. Two consumers need this and are underserved:

1. **the operator as operator** — *what needs attention right now?* Today the answer is scattered
   across each system's `STATUS.md`, `pending/` inbox, and handoff; nothing ranks it.
2. **The market story** — POGA claims to be an implemented governance system (systems
   governed, behavior changes propagated, invariants enforced). Those claims are currently
   unbacked by mined numbers; they are assertions.

the operator's framing (2026-07-14): collecting metrics is the system's job, and a code task rather
than a model task. Evidence capture is the system's job, not his — and it must be
deterministic, because one self-reported number poisons the rest.

Two facts today make the most important metrics **unminable**:

- **Runtime is not recorded.** A member may run on more than one runtime, so
  cross-runtime operation is a headline claim — but nothing in the session record says which
  runtime ran a session, so "runtimes operated, with per-runtime session counts" cannot be
  mined, only asserted.
- **Guards fire silently.** `check-bash`, `check-question`, and the `pre-push` git hook emit a
  decision and forget it. Enforced-invariant counts — the proof that the guards do anything —
  are unminable.

This ADR is commissioned by the external consultant brief `2026-07-14-federation-metrics`
(Auditor role class, [ADR-0004](0004-auditor-as-separate-role-class.md)), dispositioned by
the operator (session 66, "do 2 then 1").

## Decision

**Metrics are mined deterministically from artifacts operation already produces, by one
`curate/`-class generator, and rendered three ways. Nothing is self-reported; where a source
does not exist yet, the metric is emitted as `n/a (source pending: …)`, never estimated.**

### Two schema additions that make the unminable minable

- **S1 — runtime in session stamps.** The `runtime` is recorded as the last ` · `-delimited
  field on every `**Start:**` / `**End:**` stamp line (`session.py`). The Claude-Code hook
  path (`start`/`end`) records `claude-code`; the external `stamp` path
  ([ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md)) records the
  member's declared runtime (arg `--runtime` > config `runtime` field > `external`). Legacy
  stamp lines lacking the field default to `claude-code` at mine-time (definitional for every
  historical Claude session, not a guess). **The SESSION LOG table schema is deliberately
  unchanged** — runtime lives on the stamp line, avoiding a table-column migration across
  every existing row; the miner reads runtime from the stamp lines, using the table only as
  the session index it already is.
- **S2 — guard-firing log lines.** Each guard firing appends one structured JSONL line to the
  gitignored `.session-state/guard-firings.jsonl` (`{ts, guard, op, repo}`). Wired into the
  live `check-bash` and `check-question` denials in `session.py` and the `pre-push` git-hook
  blocks. The log is **telemetry (data), not system state** — gitignored per
  [P3](../principles/master.md#p3--data-system-separation), mined in place where it is
  written. The firing helper is **fail-open**: a telemetry-write failure can never change a
  guard's decision (the guard invariant).
- **S3 — ring promotion records** are a **design constraint on the in-flight rings work
  ([ADR-0047](0047-versioned-standard-substrate-and-rollout-reconciliation.md) §4), not a
  retrofit** — born minable (ring entry/exit stamps, verify results, in-ring vs. escaped
  failures). Not built here; recorded so the rings work ships it from day one.

### The mining layer + three renderings — `curate/metrics.py`

One generator, one `mine(roots) -> dict` core that gathers the fleet **read-only** from the
member repos it can reach (the [ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md) write
boundary is untouched — it writes only its own `EVIDENCE.md`), then four thin renderers:

1. **`--status`** — the one-line SessionStart signal (sibling of the three existing probes):
   `Metrics: N attention item(s) …`, silent/all-clear at zero.
2. **`--exceptions`** (Rendering 1, the operator report) — a ranked attention list, **empty
   when healthy**; every line is one system + one action + an age. Cumulative/impressive
   numbers are excluded by design: boring or actionable, never impressive. Signals whose
   source does not exist yet are omitted and named in a trailing `# not yet mined: …` line, so
   the omission is visible, never silent.
3. **`--money-slide`** (Rendering 2) — seven numbers, each mined or explicitly `n/a`: systems
   governed (active/parked); months in operation; behavior changes shipped; median
   Accept→adopted propagation; human interventions per change (auto vs. surfaced); half-apply
   incidents (interesting only at zero); runtimes operated with per-runtime session counts.
4. **`--evidence`** (Rendering 3) — writes the generated `EVIDENCE.md` (full mined dataset:
   per-system session/adoption tables, propagation-lag distribution, guard counts, runtime
   matrix). Generated, never hand-edited — same discipline as `CANON.md`.

The exception report is emitted at federation session-start and, when non-empty, mirrored as a
comms note ([ADR-0046](0046-per-system-comms-surface.md)) so a consumer surfaces it sessionlessly.

### Cadence expectations live in a federation-side map, not per-system STATUS

Staleness is judged against an expected cadence. That cadence is the **observer's** expectation
of a system, not the system's self-declaration — so it lives in a federation-owned
`fleet-cadence.json` (system-id → cadence-days + parked flag; parked = staleness-exempt),
seeded from `portfolio.md`, single-written by the federation. Putting cadence in each system's
`STATUS.md` frontmatter was rejected: it would make the observed system the author of the
standard it is judged against, and parked-exemption is already federation-held knowledge. The
map is tracked federation config (like `portfolio.md`), not user data.

## Alternatives Considered

- **A SESSION LOG runtime column (instead of the stamp-line field).** Rejected: a new column
  forces a migration across every existing row and renders raggedly for legacy rows, for no
  gain — the stamp line is already per-session and parseable. Runtime on the stamp line is
  purely additive.
- **A per-member declared runtime/metrics stamp the member self-reports.** Rejected on the same
  ground [ADR-0047](0047-versioned-standard-substrate-and-rollout-reconciliation.md) rejected a
  declared rollout marker: a self-reported field can lie; detection from artifacts is
  authoritative.
- **Cadence in STATUS frontmatter** — rejected above (observed-authors-its-own-standard).
- **Fold the metrics layer into the rings ADR.** Rejected: the mining layer ships now and is
  independent of the deferred ring machinery; coupling a ready layer to unstarted work delays
  it. The rings ADR references this one for S3.
- **Commit the guard-firing log to git.** Rejected: it is a growing operational telemetry
  stream ([P3](../principles/master.md#p3--data-system-separation)); mined in place,
  gitignored like the liveness sidecars.

## Consequences

- The federation gains a fleet-observability surface it can render for itself (operator
  exceptions) and for the market (money slide + `EVIDENCE.md`), all P15-mined.
- **Self-governance hardening now has its substrate.** The sibling brief
  `2026-07-14-self-governance-hardening` (R1 restore drill, R3 mined verification, R4 game
  days) emits minable records into this same layer — this ADR is a prerequisite for it.
- **Time-sensitive win captured:** S1 ships before any member's
  first non-Claude session, so a cross-runtime proof run is minable from its first stamp.
- New surfaces to maintain: `metrics.py`, `fleet-cadence.json`, the generated `EVIDENCE.md`,
  and a fourth SessionStart hook (`metrics.py --status`). The cadence map needs updating as the
  roster changes (single-writer: federation, at the same cadence as `portfolio.md`).
- The `--status` hook adds one more startup probe; it must fail-open (a metrics error must
  never break session start) and stay fast (read-only sweep).

**Reality status (per [`mark-adr-reality-status`](../habits/master.md#mark-adr-reality-status)):**
S1 + S2 **Built** (`session.py`, `pre-push`; 131 session tests green; `pre-push` firing path
exercised end-to-end). Mining layer + all three renderings + `fleet-cadence.json` **Built**
(`curate/metrics.py`, 22 tests; wired as a 5th `--status` SessionStart probe). The exception
report's **comms-note mirror** (non-empty → an [ADR-0046](0046-per-system-comms-surface.md)
note for the consumer) is **Not-built** — deferred pending a dedup/churn design (a static attention
set must not spawn a fresh note every federation session); the report is emitted at session
start today, just not yet mirrored. S3 (ring records) **Not-built** — design constraint carried
to the rings work. Signals with no source yet (ring stall, comms-note dwell,
backup-verification age) surface as `n/a`/omitted until their sources land.

## References

- Consultant brief: `proposed-edits/federation-arch/applied/2026-07-14-federation-metrics.md`.
- Sibling brief: `2026-07-14-self-governance-hardening` (rides this layer).
- [ADR-0047](0047-versioned-standard-substrate-and-rollout-reconciliation.md) §4 — rings; home of S3/R4.
- [ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md) — the read-only fleet boundary the miner respects.
- [ADR-0046](0046-per-system-comms-surface.md) — the comms note the non-empty exception report is mirrored as.
- [ADR-0021](0021-cross-system-status-surface.md) — STATUS, the cadence-location alternative rejected here.
- [P15](../principles/master.md#p15--code-for-mechanism-not-judgment) / [`no-fabricated-data`](../habits/master.md#no-fabricated-data) — the deterministic-mining constraint.
