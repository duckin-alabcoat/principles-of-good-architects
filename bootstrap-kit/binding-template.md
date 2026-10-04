# Binding manifest — <System Name> (`<system-id>`)

**Runtime:** <runtime-id, e.g. gemini-antigravity>
**Contract:** [ADR-0041](adr/0041-runtime-agnostic-substrate-floor-contract-vs-binding.md) (obligations C1–C5)
**Status:** draft

<!--
  WHAT THIS IS (ADR-0041). The federation's substrate floor is a runtime-agnostic
  CONTRACT of five behavioural obligations (C1–C5). How a given runtime satisfies
  each is its BINDING. Claude Code's binding is the reference — every obligation
  satisfied by the standard substrate — so a Claude-Code system needs NO binding.md
  (its manifest is implicit/all-satisfied). Author a binding.md ONLY for a system on
  a non-Claude runtime.

  Each obligation gets EXACTLY ONE disposition:
    - satisfied               — met by the reference Claude Code mechanism.
    - satisfied-differently   — met by a NAMED native mechanism of this runtime
                                (or a runtime-agnostic control the retrofit installs,
                                 e.g. a git hook). Name the file/mechanism.
    - waived-with-compensation — the runtime structurally cannot satisfy it; name a
                                COMPENSATING CONTROL. A waiver with no compensation is
                                NON-CONFORMANT (a silent hole = a subtraction, ADR-0041 §3).

  Prefer ENFORCED over honour-system (ADR-0041 §4): a git hook / CI gate fires
  regardless of which agent invokes git, so it satisfies an obligation structurally.
  Waive only where no structural enforcement point exists on this runtime.

  `curate/check-binding.py <this file>` validates the manifest. Keep the obligation
  keys (canon-delivery … identity-provenance) verbatim — the validator anchors on them.
-->

Per [ADR-0041](adr/0041-runtime-agnostic-substrate-floor-contract-vs-binding.md): every floor obligation carries exactly one disposition — `satisfied` / `satisfied-differently` / `waived-with-compensation`. A waiver with no compensating control is non-conformant.

| Obligation | Disposition | Mechanism / Compensation |
|---|---|---|
| C1 canon-delivery | <disposition> | <how the accepted principles + habits reach this Architect at session start> |
| C2 state-continuity | <disposition> | <handoff, session counter/stamp, orphan detection> |
| C3 operational-guards | <disposition> | <how destructive/ambiguous ops are gated before they execute> |
| C4 auditable-evolution | <disposition> | <semver + changelog + receipt/auto-adopt + ADRs> |
| C5 identity-provenance | <disposition> | <4-slot identity (ADR-0006), portfolio registration, data/system separation> |

## Notes

<One short paragraph per `waived-with-compensation` row: what the runtime cannot do, and exactly what compensates for it (a runtime-agnostic control installed elsewhere, and/or governance sessions run on Claude Code per ADR-0041 §6).>

---

# Alternative: symmetric multi-runtime matrix

<!--
  Use THIS form instead of the single-runtime table above when the system runs on
  CO-EQUAL runtimes — every session, in EITHER runtime, both builds and governs (no
  build-here / govern-there split).

  Rules that change vs. the single form:
    - Header is `**Runtimes:**` (plural, comma-separated list) — session.config.json
      declares `"runtimes": [...]`.
    - The table gains a RUNTIME column: one disposition per (obligation, runtime) pair.
    - EVERY obligation must appear once PER declared runtime — an obligation met in one
      runtime but silent in another is a subtraction. `check-binding.py` enforces this.
    - A guard that has no surface in a runtime (e.g. Claude's check-bash/check-question
      may have no analog in another runtime) records the BEHAVIOUR met by that runtime's own means in
      that runtime's row — the missing mechanism is a platform quirk, not a waiver of
      the behaviour.
    - The shared-spine pattern: hang C1/C2 on `session.py` invoked from
      EACH runtime's native session-start ritual, so both are satisfied symmetrically.
-->

**Runtimes:** <runtime-a, runtime-b>
**Contract:** [ADR-0041](adr/0041-runtime-agnostic-substrate-floor-contract-vs-binding.md) (multi-runtime matrix form)
**Status:** draft

| Obligation | Runtime | Disposition | Mechanism / Compensation |
|---|---|---|---|
| C1 canon-delivery | <runtime-a> | <disposition> | <how canon reaches a session in this runtime> |
| C1 canon-delivery | <runtime-b> | <disposition> | <how canon reaches a session in this runtime> |
| C2 state-continuity | <runtime-a> | <disposition> | <handoff / stamp / orphan in this runtime> |
| C2 state-continuity | <runtime-b> | <disposition> | <handoff / stamp / orphan in this runtime> |
| C3 operational-guards | <runtime-a> | <disposition> | <how destructive/ambiguous ops are gated in this runtime> |
| C3 operational-guards | <runtime-b> | <disposition> | <how destructive/ambiguous ops are gated in this runtime> |
| C4 auditable-evolution | <runtime-a> | <disposition> | <semver + changelog + ADRs in this runtime> |
| C4 auditable-evolution | <runtime-b> | <disposition> | <semver + changelog + ADRs in this runtime> |
| C5 identity-provenance | <runtime-a> | <disposition> | <identity + provenance in this runtime> |
| C5 identity-provenance | <runtime-b> | <disposition> | <identity + provenance in this runtime> |
