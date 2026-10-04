# ADR-0047: Versioned standard substrate + code-verified rollout reconciliation

**Status:** Accepted
**Date:** 2026-07-14
**Deciders:** the operator, Federation Architect

## Context

The federation ships standard substrate (hooks, `session.py`, guards, `comms/`, `STATUS.md`, …) on a **dogfood-first** cadence: build and prove a capability in the federation's own repo (zero blast radius), then roll it out to the rest of the members. ADR-0039 §3 ("Fork A") is the canonical example — `apply.py` was deliberately built federation-only, with the fleet lift "deferred, tracked."

The cadence is correct. Its **failure mode is not**: "try it in one place, then roll out" has repeatedly become **"try it in one place, forget to roll out, then assume it's everywhere."** Session 64 surfaced two live instances at once — auto-adopt (`apply.py`) never left the federation repo, and the assumption that it *had* went unexamined until the operator pushed on it. This is not the first occurrence; it is a recurring class.

Three root causes, none of them the phasing:

1. **"Deferred, tracked" is a comment, not a state.** "Tracked" meant a sentence in an ADR's Consequences section that nothing reconciles. There is no ledger that stays red until a rollout closes.
2. **No drift detection.** The federation probes mirror *freshness* (`reconcile.py`), curate, and auto-adopt — but nothing answers *"which members have capability X and which don't."* The belief "it's everywhere" is therefore **unfalsifiable**, and an unfalsifiable assumption drifts to optimistic.
3. **Optimism compounds.** Once a capability exists in the federation, "we shipped that" *feels* true. Nothing tests the feeling.

The federation already holds the principle for exactly this — [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence) ([P15](../principles/master.md#p15--code-for-mechanism-not-judgment)). It has been applied to bash safety and git guards, but never to the federation's *own rollout process*. And [P18](../principles/master.md#p18--verify-everything) (`verify-everything`) says the assumption must be *verified against the real target*, not trusted — which today depends on a human remembering to check, the thing that fails.

the operator's framing (session 64): version the standard itself, so a version *is* the manifest of what's deployed; let the federation run a version ahead of the fleet as the dogfood ring; and add a visible signal for a rollout that finished the federation ring but not the fleet ring. This ADR adopts that spine, corrects its ownership, and makes the verification deterministic code — never a self-report.

## Decision

### 1. The standard substrate is a versioned release train

The standard substrate gets its **own version** — `standard-version`, recorded in [`STANDARD.md`](../STANDARD.md), using the **same three-part semver (`vX.Y.Z`)** as every other version in the system (the operator, session 64 — one versioning methodology everywhere). The bump encodes the migration: **MAJOR** = a capability removed or replaced (a change that is not purely additive), **MINOR** = a capability added, **PATCH** = a fix to an existing capability's wiring. Each release thus names a **capability set**, and each capability carries a **cheap deterministic detector** (see §3). The release train's history is a changelog of what each version added/replaced/removed — and because removals are a MAJOR bump, the version number itself signals when a migration must retire something, not just add.

This is a **distinct version axis from role-doc semver** — same methodology, different number. Role-doc version (e.g. `federation-arch v2.27.0`) = *what principles and habits this Architect has adopted*. Standard-version = *what plumbing this member runs*. The two have been muddled in role-doc CHANGELOGs; separating them into two clearly-named semver numbers is a deliberate cleanup this ADR buys. The **kit** installs a given standard-version (its own packaging version may move independently for template-only fixes).

### 2. Each member declares its deployed version **at-source**; the federation reads, never maintains

Each member records the standard-version it actually runs in its **own repo** — a `.standard-version` marker (or a `standard_version` field in `session.config.json`), stamped by the member when it adopts a version. Single-writer-per-state ([P13](../principles/master.md#p13--single-writer-per-state)), captured at source ([P6](../principles/master.md#p6--observations-captured-at-source)) — the same publish/consume boundary [`STATUS.md`](0021-cross-system-status-surface.md) and [`comms/`](0046-per-system-comms-surface.md) already ride.

**The federation does not keep a central ledger of who-is-on-what.** A federation-maintained "member X is on v4" record would go stale the instant X updated itself, and the federation only runs in federation sessions — rebuilding the exact stale-central-mirror antipattern (the pre-ADR-0021 problem, and the "assume it's everywhere" problem, same shape). The federation *computes* the fleet view at read-time from the members' own markers; it is not the source of truth for it.

### 3. Verification is deterministic code, not a self-report

The `.standard-version` marker is a **claim**; the **detectors are ground truth.** A detector is a small pure function `(repo path) → present | absent | version` — grep the announce hook in `settings.json`, file-exists on `comms/`, read a version constant in `session.py`. The **manifest** maps `standard-version N → {detector: expected result}`. Verification runs the detectors for the declared version and compares.

- **Detectors are structural, not behavioral.** Grep the wiring; do not try to prove the hook *fires*. Structural detection catches the failure we care about (drifted / never-installed) cheaply; behavioral verification is a heavier ring we do not need.
- **The LLM is never in the detection path.** This is [P15](../principles/master.md#p15--code-for-mechanism-not-judgment) — reading a marker and running detectors is pure mechanism, zero judgment. As with `apply.py`, *the model reports what code did*; it never computes the verdict. (the operator's constraint: an LLM is not trusted to self-report version details.)
- **Same detector code, two call sites.** It ships in the kit / `session.py`, so it runs identically **locally** (a member self-checking its own repo at session start) and **from the federation** (reading each member's marker + re-running detectors over each member repo it can reach). One implementation; no divergence between self-check and fleet-check.

### 4. Rings, and the open-rollout signal

The federation is **ring 0** (the dogfood ring); the converged fleet is **ring 1**. The federation running a *newer* standard-version than the fleet is the *normal, healthy* in-flight state — dogfood-first, now legible as a version delta. The bug is only when that delta becomes **permanent and forgotten**.

The forcing function is a **session-start reconciliation** (sibling to `reconcile.py`) that diffs each member's declared version against the target release and flags any **open rollout**, staying red until it closes:

> `Standard rollout OPEN: federation at v5.1.0, fleet at v5.0.0 — 0/N ring-1 members migrated to v5.1.0.`

Because the spine is a version, this diffs **one number per member**, not N feature-detectors — cheap. Two-ring model now; **ring-orchestration machinery is deferred** (YAGNI — at the fleet's current size there are effectively two rings). The version spine is the enabling primitive and is worth adopting now even though the orchestration is not.

### 5. What happens when a detector disagrees — the three cases

Reconciliation compares **declared version** against **detected reality**:

1. **Declared v5.0.0, detectors confirm exactly v5.0.0, federation at v5.1.0** — *not a fault.* A normal open rollout; the member is honestly behind and is a rollout *target*. Do not cry drift on a member that is legitimately behind.
2. **Declared = detected** — clean; nothing.
3. **Declared ≠ detected** — the **fault**: a declared version whose detector fails (drift, or a lying / half-applied marker), or a member still carrying a capability a later version *retired* (**stale-not-removed**). Governing rules, mirroring `apply.py` / `check-bash`:
   - **The detector is authoritative over the label.** Marker says v5.1.0 but one of that version's detectors fails → the member is *not* at v5.1.0. Report the **detected** state and flag the discrepancy. Never trust the claim over the evidence — that is the whole reason detectors exist.
   - **Surface loudly; do not self-heal.** The detector knows *that* a capability is missing, not *why* — never installed? a deliberate additive per-system divergence ([ADR-0023](0023-standard-operating-substrate.md))? mid-migration? Auto-reinstalling could clobber a legitimate override, which is precisely the push-substrate clobber hazard ([`pushed-substrate-clobber-check`]). So report the specific discrepancy and stop — as `apply.py` surfaces an ambiguous brief instead of guessing.
   - **Fail open, and distinguish *unverifiable* from *verified-wrong*.** If a detector cannot run (repo not mounted, file unreadable) it reports *unknown* and exits 0 — never bricks a session start. Repo-not-reachable is **not** drift ([ADR-0037](0037-repo-path-locator-map-unlocated-is-not-unonboarded.md): unlocated ≠ broken). Only "detector ran and disagreed with the marker" is drift.
   - **Surface where it ran.** In the member's own session: a red banner — *"your substrate does not match your declared standard-version — `announce-hook` missing"* — its own problem, its own fix (re-run the installer / re-adopt). In the federation's session: an aggregate — *"N−1 of N clean; example-system declares v5 but fails `comms-contract`."* The federation **reports**; it cannot repair another member's working files ([ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md): federation writes inboxes only). The fix goes back through the normal rail, or the member self-heals next session.

### 6. "Rollout complete" is a code-gated claim

Because "does this member really have vN" is now a code question, the **assertion that a rollout is complete is gated on it.** `push-substrate.py` already refuses to push on a red test suite; it equally refuses to advance the fleet's target to vN — or to mark a rollout closed — until detectors pass on every **reachable** ring-1 member (reachability-gated, like delivery — an unreachable member holds the rollout *open*, it does not fake it closed). "vN is everywhere" cannot be *asserted* until code confirms it. This is the deepest form of killing the original bug: not merely visible drift, but a "done" state that code will not let you claim falsely.

### 7. Parked members are explicitly out of scope

A member may be deliberately off-standard (the operator's ruling, session 64) and carry no substrate. Such a member is marked **parked / out-of-ring** in the manifest so reconciliation does not flag it as drift every session — the same "do not re-diagnose a known state" discipline as [ADR-0037](0037-repo-path-locator-map-unlocated-is-not-unonboarded.md). It re-enters scope only on onboarding/bootstrap.

## Alternatives Considered

- **Federation keeps the central ledger of who-is-on-what.** Rejected — a federation-maintained record goes stale the instant a member self-updates, and the federation is blind between its own sessions. This *is* the stale-central-mirror antipattern the whole ADR exists to kill. At-source declaration + read-time reconciliation is the fix.
- **Trust the declared version; skip detectors.** Rejected — a marker can lie (drift, half-applied migration, copy-paste). A version label with no ground-truth check is the same unfalsifiable optimism, one indirection later. Declared version = fast path; detectors = trust-but-verify.
- **LLM self-reports its substrate version.** Rejected explicitly (the operator's constraint) — self-report is not a guarantee ([P15](../principles/master.md#p15--code-for-mechanism-not-judgment)); the model reads code's verdict, never produces it.
- **Behavioral detectors (prove each hook fires).** Rejected for now — heavier, and the failure mode we need to catch (drifted/never-installed wiring) is caught by cheap structural markers. Behavioral verification is a later ring if drift proves subtle.
- **Auto-remediate on detected drift.** Rejected for now — the detector knows *that*, not *why*; silent re-install risks clobbering a legitimate additive divergence (the push-substrate clobber hazard). Detect→surface is the fix for "forgot to roll out"; self-healing is a separate, clobber-risky capability for a later ring, justified only if drift becomes frequent.
- **A per-architect hand-written feature check.** Rejected — one shared manifest checked against everyone, not bespoke logic per member. Add a capability once (with its detector) and it is audited across all members forever.
- **Full ring-orchestration engine now.** Rejected — YAGNI at the current fleet size (two effective rings). Adopt the version spine + the 2-ring model; defer orchestration until enough members exist to need ring 2+.
- **Keep flat capability-manifest probing (the session-64 first proposal), no versions.** Subsumed, not rejected — the manifest becomes the *contents* of each version (its capability set + detectors). The version spine adds the compact desired-state label and the ring structure the flat manifest lacked. The two compose: versions are the release train, the manifest is what is inside each release.

## Consequences

- **The "assume it's everywhere" recurrence gets a structural guard** ([`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence) applied, for once, to the federation's own process). An unfinished rollout is now a red session-start signal that stays red until closed, not a forgotten sentence in an ADR.
- **A new orthogonal version axis** (`standard-version`) lives in `STANDARD.md`; role-doc semver and standard-version stop being muddled. The kit becomes "the installer for standard-version N."
- **New substrate to build (Reality: Not-built):** the version marker + at-source stamping; the capability manifest with a detector per capability; the local self-check (a `session.py` step / probe); the federation reconciliation (a `reconcile.py`-sibling that reads markers + re-runs detectors); the `push-substrate.py` completion gate. Federation-first (ring 0), as always — but this is the one capability whose *whole point* is to then reach ring 1, so its own rollout is the first the reconciliation will track (it dogfoods itself).
- **Migrations must encode removals**, and reconciliation must catch **stale-not-removed**, not only missing. The forgotten-removal is the same disease as off-roster residue (`comms/2026-07-14-off-roster-residue` (withheld)): nothing today catches "should be gone but isn't."
- **Ordering vs. auto-adopt.** The ADR-0039 auto-adopt fleet lift and this reconciliation are complementary: auto-adopt makes floor edits self-*apply* at converged members; this makes rollout *state* legible and verified. Neither helps the parked members (no harness to run either) — those need the retrofit first.
- **This is doctrine + mechanism.** The doctrine (versioned standard, at-source declaration, code-verified, detect-don't-heal) is decided here; the mechanism is owed and tracked via the Reality column, not a second sentence that rots.

## References

- [ADR-0039](0039-appliable-brief-schema-and-auto-adopt-mechanism.md) — the "Fork A" deferred-rollout whose forgotten step motivated this; auto-adopt (self-apply) is the complement to this (rollout-state verification).
- [ADR-0023](0023-standard-operating-substrate.md) — the standard operating substrate this versions; the additive-divergence rule that forbids blind auto-repair.
- [ADR-0021](0021-cross-system-status-surface.md) / [ADR-0046](0046-per-system-comms-surface.md) — the publish-at-source / consume-at-read boundary the version marker follows.
- [ADR-0037](0037-repo-path-locator-map-unlocated-is-not-unonboarded.md) — unlocated ≠ broken; the unverifiable-vs-verified-wrong distinction.
- [ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md) — the federation-writes-inboxes-only boundary that scopes what reconciliation may do about drift.
- [ADR-0034](0034-settings-are-federation-pushed-generated-substrate.md) — `push-substrate` generation, extended here with a completion gate.
- [P15 `code-for-mechanism-not-judgment`](../principles/master.md#p15--code-for-mechanism-not-judgment), [P18 `verify-everything`](../principles/master.md#p18--verify-everything), [P13](../principles/master.md#p13--single-writer-per-state), [P6](../principles/master.md#p6--observations-captured-at-source) · [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence).
- Federation session 64 (2026-07-14) — the operator's versioned-standard proposal; the auto-adopt-not-rolled-out finding that occasioned it.
