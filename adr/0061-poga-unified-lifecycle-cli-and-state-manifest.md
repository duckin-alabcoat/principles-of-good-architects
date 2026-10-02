# ADR-0061: `poga` is the unified spec-driven lifecycle CLI; the per-system state manifest

**Status:** Accepted
**Date:** 2026-07-22 (accepted session 85)
**Builds on:** [ADR-0025](0025-bootstrap-is-a-code-harness.md) (bootstrap is a code harness), [ADR-0033](0033-non-derivable-data-is-backed-up-at-machine-level.md) (non-derivable data backed up at machine level), [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) (the concurrency wrapper *is* `poga`)
**Deciders:** the operator (proposed the bootstrap/restore CLI unification, 2026-07-20; ran drill #1); Consultant (`2026-07-21-poga-cli-bootstrap-restore-brief`); Federation Architect (scope + design)

## Context

Two independent pressures arrived at the same command name, from opposite directions, within one day:

- **The Consultant's `poga`-CLI brief** (`2026-07-21-poga-cli-bootstrap-restore-brief.md`, released after drill #1) pushed *up* from the data-durability side: make new-system prework a CLI with two verbs sharing one code path — `poga bootstrap <spec>` and `poga restore <system>` — so that restore, which is exercised *never* today (drill #1's root finding), inherits bootstrap's exercise frequency. The unification is the value, not the convenience: rot in a shared path gets caught by ordinary use instead of by a drill cadence no schedule can match.
- **[ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md)** arrived *down* from the concurrency side: the terminal concurrency wrapper — a deterministic outer shell that allocates a git worktree lane, execs `claude`, and tears down through the gate — is best expressed as a `poga` terminal command, with `session` / `run` joining `bootstrap` / `restore` as verbs on **one spec-driven tool** (ADR-0060 D5).

They are the same tool. The `poga` script already exists at the repo root: `session` (the default) and `lanes` are **built** and live; `bootstrap` / `restore` / `run` are **reserved** stubs that today point the caller back at this brief and at `bootstrap.py`. So the naming and the surface are settled. What is *not* yet settled — and what the brief's §6 explicitly hands to the federation — is the formal charter (is `poga` genuinely one tool, or four scripts sharing a dispatcher?) and the one artifact the brief could only sketch: **the per-system state manifest** that tells `restore` what non-derivable data to fetch, from where, and how. Drill #1's headline was that the software restores in ~13 minutes and the data does not restore *at all*, because nothing declares per system where the backups live. That gap is this ADR's payload.

This ADR records the decision. It does **not** build the `bootstrap` / `restore` / `run` verbs — per the brief's §4 sequencing (the operator-endorsed), drill #1's findings *are* the requirements spec, and the build follows the spec, not the other way around.

## Decision

### D1 — `poga` is the single spec-driven lifecycle CLI, not four scripts behind a dispatcher

`poga` is one tool that owns a system's whole lifecycle, with every verb sharing [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md)'s shape — a deterministic outer shell around a non-deterministic LLM core, or around no core at all for the pure-mechanical verbs:

| Verb | Status | What it does |
|---|---|---|
| `session` | **Built** ([ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md)) | Allocate a worktree lane, exec `claude` in it, land through the gate on teardown. |
| `bootstrap <spec>` | **Reserved / specified** | Build a new Architect from its `bootstrap-spec.<system>.json` — formalizes today's `bootstrap.py`. |
| `restore <system>` | **Reserved / specified** | `bootstrap` **plus** hydrate non-derivable data from backup media (D2). |
| `run` | **Reserved** | Placeholder for a future non-session invocation; not specified here. |

> **Implementation note (2026-07-24, session 93).** The table gained a fifth, purely
> mechanical verb: `install` — symlink this checkout's `poga` into a user-scope bin
> directory (a PATH line appended to the shell's rc file only if it lacks one; dependency presence
> reported, never fatal; honest exit when a new terminal is owed). Per the 2026-07-23
> consultant brief: symlink never copy, idempotent/converging, user-scope only, no
> hardcoded home. **`install` is not `bootstrap`** — bootstrap builds a *system* from a
> spec; install makes an *existing checkout's CLI* reachable on the current machine.
> Update is `git pull` (the symlink points at the live checkout). `bootstrap`/`restore`
> may later end by invoking it; it takes no spec so it stays call-able from them.
> Verified live. (`bootstrap`/`restore` themselves are **Built** as of
> session 91 — see the index's Reality column.)

The binding claim: **restore *is* bootstrap plus a hydration step.** They share one code path and one input — `bootstrap-spec.<system>.json`, tracked in the federation repo so it survives inside what backup preserves. This is what makes the exercise-frequency argument true: because restore runs the same build bootstrap runs, every system creation is also a partial restore drill. `poga`-the-command (the wrapper you launch) and `poga`-the-label (typed inside a session to name it, ADR-0057) coexist as ADR-0060 D5 already settled.

### D2 — `restore = bootstrap + data + credentials`, and it never reports plain success

The restore equation has three terms; the CLI can automate only two:

1. **bootstrap** — automated (D1).
2. **data** — automated where the source allows (D3).
3. **credentials** — *cannot* be automated: secrets travel through neither git nor backup media, by design.

Therefore the contract, verbatim from the brief §2:

> `poga restore` runs legs 1–2, then **prints the credentials manifest as an explicit checklist and exits reporting "incomplete — N credentials to re-provision."** It never reports plain success.

The named anti-pattern is a restore that exits green while the restored system cannot authenticate to anything. The command's honesty about what it did *not* do is part of its correctness. Drill #1 already produced the first such N (`gh auth login`'s interactive device flow, finding 4).

### D3 — The per-system state manifest (new artifact; schema defined here)

Each system gains a versioned, explicit declaration of its non-derivable state — what `restore` must fetch, from where, to where. This is the artifact [ADR-0033](0033-non-derivable-data-is-backed-up-at-machine-level.md) mandated a *policy* for but never gave *coordinates* to; drill #1's central finding (agent correctly declared `users/`, `architect-learnings.md`, `proposed-edits/` **unrecoverable** rather than guess) is exactly the cost of its absence.

**Artifact:** `state-manifest.<system>.json`, tracked in the federation repo (system, not data — it names locations, holds no secrets or user data). Schema:

```jsonc
{
  "system": "<system-id>",
  "version": "<manifest-schema-version>",
  "spec": "bootstrap-spec.<system>.json",        // the D1 shared input
  "data_sources": [
    {
      "name": "<human label>",
      "paths": ["users/", "architect-learnings.md", "proposed-edits/"],  // what to pull
      "dest": "<parameterized install path>",     // NEVER a hardcoded home path (finding 5)
      "source": {
        "kind": "<backup kind>",
        "failure_domain": "<what loss this source survives>",  // (finding 2)
        "automation": "scriptable" | "human-guided", // a script can fetch it, or only a person can
        "coordinates": { /* where it is — reachable, not a policy name */ },
        "retrieval": [ /* ordered steps to fetch it, path list, expected size */ ]
      }
    }
  ]
}
```

**Sibling — the credentials manifest** (`credentials-manifest.<system>.json`, same repo). Each entry declares a secret's **transport, not just its inventory** (finding 8):

```jsonc
{
  "credentials": [
    {
      "name": "root-identity",                    // item zero — always first (finding 1)
      "transport": "human-only",                  // guided checklist; no machine injection
      "provision": [ /* steps to re-establish */ ]
    },
    {
      "name": "backup-access",                  // item one — required to read leg-2 backups
      "transport": "human-only",
      "provision": [ /* pointer, not the value — reference-secrets-dont-transmit */ ]
    },
    {
      "name": "gh-pat",
      "transport": "machine-injectable",           // fetch-at-use helper (finding 8)
      "provision": [ /* controller-served endpoint pattern; secret never in transcript */ ]
    }
  ]
}
```

Neither manifest ever contains a secret **value** — only names, pointers, and procedures ([`reference-secrets-dont-transmit`](../habits/master.md#reference-secrets-dont-transmit) / [P14](../principles/master.md#p14--no-confidential-data-in-chats)). The manifests are the answer key `restore --plan` renders and the drill scores against.

### D4 — Design constraints ratified as build requirements

The brief's §3 constraints and drill-#1 findings become firm requirements on the future `bootstrap` / `restore` build (not built now, but binding when it is):

- **Idempotent, resumable, atomic-or-cleanup.** A casually-invoked CLI *will* be interrupted; re-run must converge with no duplicate or orphaned GitHub repo (2026-07-02 review), and the authenticated clone must be atomic-or-cleanup (finding 7 — an interrupted clone's skeleton `.git` poisons every retry).
- **`--plan` dry-run.** Print what would be built and pulled, touch nothing. Doubles as a freshness check and as the generator of the drill's sealed answer key.
- **Step-zero bare-machine incantation** lives *outside* the repo (chicken-and-egg), is GUI-free, and **orders Command Line Tools before any `git` invocation including `git config`** (finding 6 — `git config` silently no-ops before CLT, producing a misleading later error).
- **Repo identity is pinned** in the spec (URL + expected root-commit hash) and verified before building, so a restore can never rebuild from the public sanitized sibling repo (finding 9).
- **Source-decision step:** the restore picks its source by what was lost, since different losses can call for different backups with different retrieval paths, some automated and some a guided human checklist. Drills must exercise every declared retrieval path, including the human-only one, not just the convenient one (finding 2).

### D5 — Scope and sequencing

- **Built now:** nothing beyond what exists. `session` / `lanes` are live; this ADR does not touch them.
- **Reserved and specified:** `bootstrap` (wraps today's `bootstrap.py`), `restore` (D2), the two manifests (D3). These build **after** drill #1's findings are folded — which this ADR does. Drill #2 then exercises the CLI rather than the docs.
- **The state-manifest schema (D3) is the immediate next build** — it is the artifact whose absence made drill #1's RPO "total loss," and it is authorable now without the CLI (the CLI *consumes* it).
- **Reserved to its own ADR:** the operator-gated, **persona-free `activate` step** (drill finding 10 — a fresh no-context agent correctly refused to auto-adopt `CLAUDE.md` + permission hooks from the repo, reading them as a prompt-injection pattern). "Restore is persona-free; the persona is the artifact, not the actor" is a distinct decision about the activation boundary and gets its own record when `restore` is built. Named here so it is not lost.

`poga` lives in the federation repo, spec-driven, built and owned by the Federation Architect — same class as `curate/` and `apply.py`, not a new Architect's system (brief §6, Consultant's suggested shape, accepted).

## Alternatives Considered

- **Keep `bootstrap.py` and add a separate `restore.py`.** Rejected — this is the status quo that drill #1 indicted. Two code paths means restore is exercised only on drills; the whole value is restore *inheriting* bootstrap's exercise frequency, which only a shared path delivers.
- **A federation mechanism that backs up the data itself** (instead of a manifest pointing at machine-level backups). Rejected — [ADR-0033](0033-non-derivable-data-is-backed-up-at-machine-level.md) already decided backup is machine-level, not a federation mechanism; the manifest declares *where machine-level backup put things*, it does not replace it.
- **Fold the persona-free `activate` decision into this ADR.** Rejected — it is a genuine standalone decision about the activation trust boundary (when do persona + hooks come alive, and against what provenance check), and it only bites when `restore` is built. Reserving it (D5) keeps this ADR focused on the CLI charter + manifest schema.
- **Build the CLI now, manifest later.** Rejected — inverts the brief's endorsed sequencing (§4): automating first encodes today's *unknown* assumptions into code. The manifest schema (declarative, CLI-independent) is the correct first build; the verbs consume it.

## Consequences

- **The `poga` reserved stubs now have a decision behind them** — `bootstrap` / `restore` / `run` stop being "reserved, TBD" and become "reserved, specified" with a schema and a contract to build against.
- **`state-manifest.<system>.json` becomes a required per-system artifact.** Every onboarded system (and the federation itself) needs one; a system without a reachable manifest has, by drill #1's evidence, an RPO of total data loss. Populating them is downstream work — federation first, as the reference implementation, then each system.
- **`restore` can never lie about credentials.** The "incomplete — N to re-provision" contract (D2) is now the definition of a *correct* restore exit, not a nicety.
- **Drill #2 has a target.** Once the schema + `restore` exist, the next drill verifies the data + credentials delta against the manifest rather than re-discovering the whole procedure each time (brief §1).
- **One more standalone ADR is owed** — the persona-free `activate` step (D5), when `restore` is built.
- **Negative:** two new tracked artifacts per system to keep current (the two manifests); stale coordinates are their failure mode, caught by `--plan` freshness checks and the drill, not by ordinary session use (unlike the shared build path, which self-exercises).

## References

- Consultant brief: `proposed-edits/federation-arch/pending/2026-07-21-poga-cli-bootstrap-restore-brief.md` (withheld) (dispositioned into this ADR)
- Prior brief: `2026-07-14-poga-hardening-brief.md` (R1 three-leg restore drill — this ADR's ancestor)
- [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) — the concurrency wrapper is `poga`; D5 named the shared verb set
- [ADR-0033](0033-non-derivable-data-is-backed-up-at-machine-level.md) — backup is machine-level; this ADR gives it coordinates
- [ADR-0025](0025-bootstrap-is-a-code-harness.md) — bootstrap is a deterministic code harness
- Drill #1 record: kept outside the repo
- `poga` (repo root) — the CLI; `session` / `lanes` live, `bootstrap` / `restore` / `run` reserved
