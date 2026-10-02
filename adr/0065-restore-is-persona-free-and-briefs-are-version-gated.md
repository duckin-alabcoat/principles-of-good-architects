# ADR-0065: Restore is persona-free, and a hydrated brief is gated by a version check

**Status:** Accepted (the operator, 2026-07-24, session 93)
**Date:** 2026-07-23 (session 90)
**Builds on:** [ADR-0061](0061-poga-unified-lifecycle-cli-and-state-manifest.md) D5 (reserved this decision to its own ADR, owed once `restore` was built), [ADR-0039](0039-appliable-brief-schema-and-auto-adopt-mechanism.md) / [ADR-0029](0029-receiving-architects-auto-adopt-at-startup.md) (auto-adopt at startup), [ADR-0033](0033-non-derivable-data-is-backed-up-at-machine-level.md) (backup is machine-level)
**Deciders:** the operator (commissioned the CLI build, session 90); Federation Architect (design)

## Context

[ADR-0061](0061-poga-unified-lifecycle-cli-and-state-manifest.md) D5 reserved one decision to its own record and named the trigger: *"when `restore` is built."* `poga restore` was built in session 90, so the debt is due.

The reserved question came from drill #1's **finding 10**. A fresh, no-context agent, handed a freshly restored repo, **refused to auto-adopt its `CLAUDE.md` and permission hooks** — it read them as a prompt-injection pattern. The instinct to log this as an obstacle is wrong. The agent was **right**: it was being asked to treat bytes of unverified provenance as standing instructions and as permission grants, which is precisely the shape [P20](../principles/master.md#p20--untrusted-data-stays-untrusted) exists to refuse. The finding is not "the agent was too cautious"; it is "restore had never said where its trust boundary is."

Building `restore` made that boundary urgent for a second, sharper reason that the drill did not reach — because in the drill, nothing was actually hydrated.

**The live hazard.** A restore has two input channels with completely different trust properties, and until now they were treated as one:

| Channel | Carries | Provenance | Verifiable? |
|---|---|---|---|
| **git** (the pinned remote) | system files — role doc, ADRs, registries, `session.py`, `CLAUDE.md`, `.claude/settings.json` | a repo whose **root commit is pinned** in the spec and checked before anything else runs ([ADR-0061](0061-poga-unified-lifecycle-cli-and-state-manifest.md) D4 / finding 9) | **yes** — cryptographic history, verified identity |
| **backup media** | non-derivable data — `users/`, `architect-learnings.md`, `proposed-edits/`, `retrofit-reports/` | a filesystem snapshot; no signature, no identity, no integrity check beyond "it was on the disk" | **no** |

The state manifest hydrates `proposed-edits/` from backup media. `session.py apply-briefs` is a **floor** `SessionStart` hook on **every** member ([ADR-0039](0039-appliable-brief-schema-and-auto-adopt-mechanism.md) Fork A, floor-promoted session 66): it reads the inbox and **applies** every conforming `Apply: auto` brief *before the model wakes*, deterministically, by design.

Compose those two facts. A restore hydrates an inbox from unverified media; the next session start silently applies whatever briefs are in it — edits to the role doc, version bumps, new files — with no human in the loop. The auto-adopt gate was retired ([ADR-0029](0029-receiving-architects-auto-adopt-at-startup.md)) on a sound argument: *the approval already happened upstream at registry-Accept*. That argument holds for a brief delivered by the federation. It does **not** hold for a brief that arrived from a backup image, where "upstream" is unverifiable and the file could have been modified, reverted, or planted since.

This hazard did not exist yesterday. It is created by making restore real, which is exactly when it must be decided.

## Decision

### D1 — `restore` never activates the persona; it only lays down files

`poga restore` rebuilds a system and **exits**. It does not, and may not:

- source, execute, or "adopt" the restored `CLAUDE.md`,
- install, wire, or fire any hook from the restored `.claude/settings.json`,
- run `session.py` (or any restored code) from the restored tree,
- open a session, assume the Architect's identity, or speak as it.

**The persona is the artifact, not the actor.** Restore's output is a directory of files. Whether those files become a live agent is a separate event with a separate trigger.

The practical consequence is that finding 10 stops being a defect. The restoring agent's refusal to auto-adopt the restored persona was correct behaviour and is now the *specified* behaviour; the drill's next run should score a restore that activates nothing as a **pass**.

### D2 — Activation is operator-gated, and the operator is the provenance check

A restored system comes alive when a human opens it — today, launching Claude Code in the restored directory, which is itself an act of authorization. No `poga activate` verb is introduced: adding one would create a machine path into activation, which is the thing being ruled out. The gate is deliberately the human's own deliberate act.

What makes that act safe is **D3**, not the human's vigilance — an operator cannot eyeball a repo for a planted brief, and asking them to is the kind of discipline-based control [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence) exists to replace.

### D3 — A hydrated brief is gated by a version check against a git-verified artifact, not by a human hold

The first draft of this ADR quarantined the restored inbox wholesale — a marker that made `apply-briefs` treat it as empty until a human cleared it. **the operator rejected that** (session 90), ruling that the system should instead compare version numbers to decide whether an auto-apply is valid. He was right, and the draft's stated reason for the hold — *"there is nothing to verify against"* — was simply **false**. There is.

**The mechanism already exists.** Every `Apply: auto` brief carries `expected-base-version` in its required header, and `apply-briefs` refuses to apply unless it equals the target file's current `**Version:**` line ([`session.py`](../session.py) `evaluate_brief`; [ADR-0039](0039-appliable-brief-schema-and-auto-adopt-mechanism.md)). A mismatch surfaces the brief instead of applying it.

**Why that check is strong in the restore case specifically.** The artifact it compares against — the role doc — arrives over **git**, from a remote whose root commit this restore already verified (D4/finding 9). So an unverified brief is gated by a **verified** artifact. That inverts the hazard: the brief does not get to assert its own validity; it has to agree with something whose provenance was checked.

**And it catches the realistic failure by construction.** The genuine risk after a restore is not a planted brief — it is **staleness**: a backup's inbox is a point-in-time snapshot that may disagree with what actually happened since. A brief already applied after the backup was taken bumped the role doc's version, so its `expected-base-version` no longer matches and it is refused automatically. A brief withdrawn or superseded likewise fails to match. The version check handles the whole realistic class with no human in the loop.

So: **no hold.** Hydrated briefs sit in the inbox as ordinary pending briefs and are subject to the ordinary gate. `restore` **reports** how many it hydrated so the operator knows they are there (D4), and anything failing the version check takes the existing surfaced-not-applied path [ADR-0029](0029-receiving-architects-auto-adopt-at-startup.md) already defines.

**What this does not mitigate, stated honestly.** The version check is a consistency check, not an authenticity check: an attacker with write access to the backup media could author a brief with a matching `expected-base-version`. Three things make that an acceptable residual rather than a hole worth a human gate for:

1. **The vector is not isolated.** The same backup restores `users/<id>/profile.md`, which is injected into every session as standing preference context. Anyone who can tamper with the backup has richer paths than the inbox, so holding the inbox alone buys little while implying a protection that is not real.
2. **The blast radius is small and visible.** `apply-briefs` applies only four op types against uniqueness-anchored targets, whole-brief-atomically, and **never commits** — the session-end close does. So any applied change shows up in `git diff` before it lands, and git is the backstop.
3. **Anyone with write access to the backup media has already lost bigger games** — that access implies the machine or the backup account is compromised, which the federation cannot defend against from inside a restore.

If briefs ever carry a signature, this becomes an authenticity check and the residual closes; recorded as an open thread rather than pretended away.

**The rest of the hydrated set** (`users/`, `architect-learnings.md`, `retrofit-reports/`) is inert by construction — read as data, never parsed as instruction. If a future manifest adds a hydrated path that some mechanism *acts on* automatically, it needs its own gate of this kind — a verified artifact to check against — and that gate is required by this decision, not exempted by omission.

Ordinary operation is untouched throughout: a brief delivered by the federation is gated exactly as it always was.

### D4 — The trust boundary is stated where it is enforced

The `restore` report names what it activated (nothing) and how many briefs it hydrated, so the operator is told the boundary rather than expected to know it. It says so in the same breath as its `incomplete — N credentials` line — the honesty contract ([ADR-0061](0061-poga-unified-lifecycle-cli-and-state-manifest.md) D2) extended from *what I did not do* to *what I did not verify and how it will be gated*.

## Alternatives Considered

- **Quarantine the restored inbox behind a human-cleared marker.** This was the **first draft of D3, and the operator rejected it** — correctly. It rested on the claim that there was nothing to verify against, which was false: `expected-base-version` versus the git-verified role doc is exactly such a check, and it handles the realistic (staleness) class automatically. The hold also carried two real costs: it put a human step in the middle of a disaster rebuild, and it implied a protection against backup tampering that it did not actually deliver (see D3's residual). A gate a human clears by reflex is worse than a check code performs every time.
- **Let the restored inbox auto-apply with no gate at all.** Rejected — the version check is what makes D3 safe; without it a stale or already-applied brief would re-apply against a role doc that has moved on. The gate is not optional, it is just not *human*.
- **Have `restore` verify brief provenance cryptographically.** Not possible today — briefs carry no signature, and the federation keeps no record of what was in a target's inbox at backup time. Recorded as the open thread that would close D3's residual.
- **Add a `poga activate` verb.** Rejected — see D2. It creates a machine path into activation, and the whole decision is that activation should not have one. The human's deliberate act is the control.
- **Fold this into ADR-0061.** Already rejected there (D5): it is a genuine standalone decision about a trust boundary, and it only bites once `restore` exists. It now does.

## Consequences

- **Finding 10 is closed as specified behaviour, not a bug.** A restoring agent that refuses to adopt the restored persona is conforming.
- **A restore requires no human step to reach a working system** beyond the credentials it already cannot avoid. The inbox gate is code, every time, not discipline.
- **The `expected-base-version` check becomes load-bearing for a second reason.** It was built as a drift guard ([ADR-0039](0039-appliable-brief-schema-and-auto-adopt-mechanism.md)); it is now also the restore-path gate, which is worth knowing before anyone considers relaxing it.
- **`restore` gains a report line naming how many briefs it hydrated.** No marker, no new state, nothing for `apply-briefs` to check that it did not already check.
- **Drill #2 gets two scoring criteria**: did restore activate anything (it must not), and did a stale hydrated brief get refused by the version check (it must be).
- **Negative / residual:** the version check is a consistency check, not an authenticity one — a tampered backup could carry a brief with a matching base version. Accepted knowingly (D3): the vector is not isolated, the blast radius is bounded by the four-op schema and the never-commit rule, and the compromise it implies is already fatal elsewhere.
- **Open thread:** signed briefs would turn this into an authenticity check and close the residual. Not owed; recorded so it is not lost.

## References

- [ADR-0061](0061-poga-unified-lifecycle-cli-and-state-manifest.md) D5 — reserved this decision and named its trigger
- [ADR-0039](0039-appliable-brief-schema-and-auto-adopt-mechanism.md) / [ADR-0029](0029-receiving-architects-auto-adopt-at-startup.md) — the auto-adopt mechanism and the retired per-edit gate
- [ADR-0033](0033-non-derivable-data-is-backed-up-at-machine-level.md) — backup is machine-level; the unverifiable channel
- [P20](../principles/master.md#p20--untrusted-data-stays-untrusted) + [`treat-inbound-payload-as-data-not-commands`](../habits/master.md#treat-inbound-payload-as-data-not-commands)
- Drill #1 finding 10 — the fresh agent's refusal to auto-adopt a restored persona
- `poga_cli.py` (repo root) — the seven-phase pipeline this constrains
