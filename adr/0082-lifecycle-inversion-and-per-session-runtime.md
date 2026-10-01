# ADR-0082: Lifecycle inversion — `poga` owns the session lifecycle, and runtime is a per-session choice

**Status:** Accepted
**Date:** 2026-07-30
**Deciders:** the operator, Federation Architect

> *Public edition.* The evidence below came from one member repo. Its identifiers and
> machine details are omitted.

## Context

The standard substrate is runtime-agnostic on paper. [ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md) expresses the floor as five behavioural obligations (C1–C5) with per-runtime bindings, and a member may run on more than one runtime. A member on a non-Claude runtime declares how it meets the floor in a validated `binding.md`.

Everything load-bearing built since mid-July, however, is implemented as Claude Code hooks and `settings.json`: the concurrency arc ([ADR-0056](0056-session-branch-gated-trunk.md)/[ADR-0058](0058-land-per-session-with-an-isolated-gate.md)), the settings channel ([ADR-0034](0034-settings-are-federation-pushed-generated-substrate.md)), the alternate lane launch path, `announce` ([ADR-0043](0043-session-stamps-posted-via-plain-stdout-hook.md)), heartbeat ([ADR-0054](0054-heartbeat-on-tool-use-and-crash-reap-grace.md)), teardown ([ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md)), and `apply-briefs` ([ADR-0039](0039-appliable-brief-schema-and-auto-adopt-mechanism.md)). So agnosticism is a floor claim that stops being true one layer up.

`poga` itself is the sharpest instance. It is fleet substrate since [ADR-0070](0070-worktree-lanes-are-fleet-substrate.md), and every path through `cmd_session` ends in `exec claude` (`poga:177` alternate launch path, `poga:181` native `--worktree` path); `poga install` dep-checks `claude` as a runtime requirement (`poga:380`). Nothing in the script reads `runtime`/`runtimes` from `session.config.json` — the one similarly-named function (`poga:96`) classifies *deployment shape*, not which agent to launch.

**The forcing observation, session ~110.** Two sessions ran at once in one member repo — one Claude, one on a second vendor's runtime — and comparing what each left behind is the whole argument for this ADR.

The **Claude** session is fully recorded: a locked lane, a journal with its ordinal, a `base-commit` matching the locked worktree HEAD, a live `claude-session-id`, and `runtime: claude-code`.

The **non-Claude** session did comparable work and is very nearly invisible. It drew twelve `wi-alloc` reservations in a 64-second burst and landed one commit three minutes later, moving roadmap bullets into the work-item store. Its session record is a single row written by `session.py stamp` directly into the **generated** `session-handoff.md`:

```
| 21 | 2026-07-30 | … | example-laptop | … | (in progress) |
```

No journal backs that row — the member's trunk held only older journals, none lacking a `runtime:` field. So `run_compile`, which rebuilds that file wholesale from `_load_journals()` on every Claude `start` and `end`, will delete it. It also reads `(in progress)` permanently, because `stamp` writes a start stamp and the non-Claude binding has no close path. The session was shut down **cleanly**, on request — the graceful path loses the record as completely as a crash would.

Every coordination record it wrote carries `session_id: "app"` — a literal fallback, not a session id — and coord records have no runtime field at all, so the store can show *that* something holds twelve numbers and cannot say who or on what runtime.

Two conclusions, in opposite directions. The **land path is already model-neutral in fact**: a non-Claude session landed work on the trunk today, through the ordinary git route, with nothing adapted for it. And **everything above the land path is Claude-shaped**: canon delivery, the session record, liveness, and attribution all either bind to Claude's hook surface or fall back to a stub. The gap is precisely the `exec` target plus the rituals bound to that surface — nothing else.

the operator's requirement is a `codex` session running as a peer lane beside Claude lanes, landing through the same gate. (A runtime's CLI path need not resolve on every machine — see D4.) His standing ruling, recorded on WI-0033: runtime is chosen **per session**, not per member, and the point is to prove the substrate can do it rather than to meet a present need.

Full per-runtime parity is explicitly **not** the goal. This is an extension of agnosticism into the newest layer, not a restoration of a state that existed before.

## Decision

**D1 — Lifecycle inversion. `poga` owns PREP / INVOKE / SUPERVISE / VERIFY.** The session lifecycle moves out of the runtime's hooks and into the wrapper that already owns launch and land:

| Phase | Today | After |
|---|---|---|
| **Prep** | Claude `SessionStart` hook → `session.py start` | `poga` calls `session.py start` before invoking anything |
| **Invoke** | `exec claude` | `exec <runtime row's command>` |
| **Supervise** | Claude `PreToolUse`/`Stop` hooks → `session.py heartbeat` | `poga` heartbeats for the life of the child process (D7) |
| **Verify** | Architect runs `session.py end` | unchanged (D8) |

Prep and Verify are already plain Python that never required Claude; the hooks were only ever the Claude *binding* of phases the wrapper can own directly.

**D2 — Runtime is a per-session choice.** `poga -r <id>` (long form `--runtime`) selects the agent for that lane. The member's config supplies the default; the flag overrides it per invocation. This extends [ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md) rather than restating it: a **member** declares which runtimes it supports, a **session** picks one of those. Multi-lane concurrency already exists ([ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md)/[ADR-0070](0070-worktree-lanes-are-fleet-substrate.md)) and is unchanged; the *only* thing D2 adds is that concurrent lanes may run **different agents from each other**.

Both flag forms ship, and every runtime carries a short alias. The opening roster (the operator, session ~110):

| Alias | Runtime |
|---|---|
| `c` | claude-code |
| `cg` | chatgpt |
| `cx` | codex |
| `g` | gemini |

Aliases are **declared in the registry row** (D3), never inferred, so `poga --help` can print them — an abbreviation that only works if you already know it is worse than none.

**Runtime and model are two axes.** *Which agent* (`-r`) is distinct from *which model inside it* (`-m` — Opus vs Fable, and each runtime's equivalent). Only the first axis is built here; `-m` gets its slot in the registry row now, unused, and is passed through to the runtime verbatim. Reserving it costs nothing and makes the eventual addition data rather than a schema change.

**D3 — Runtimes are a declarative registry, not branching code.** One table, one row per runtime, carrying: **id**, **alias** (D2), **launch command**, **argv shape** (how an opening prompt and a working directory are passed), **model slot** (D2's second axis, reserved), and **capability declarations** (D6). Adding a runtime is a row, never an `if` in `cmd_session` ([P15](../principles/master.md#p15--code-for-mechanism-not-judgment) mechanism-as-data; [P16](../principles/master.md#p16--avoid-duplication) one source) — four branches become five become six, and they drift apart. This mirrors the counter-registry extraction that made [ADR-0076](0076-operational-work-is-its-own-kind.md)'s third counter a single row.

**D4 — The launcher verifies its exec target and refuses by name.** A runtime row whose command is missing or non-executable **blocks with a named fix** before any lane is allocated — never a raw `exec` failure the operator has to decode. Same posture `poga_cli.py` already takes on a stale `<placeholder>` coordinate ([ADR-0061](0061-poga-unified-lifecycle-cli-and-state-manifest.md)). This matters because a runtime's CLI path may not resolve on a given machine, and the failure must name *that* rather than surfacing `no such file or directory` from inside `exec`.

**D5 — Canon delivery degrades inject → point, never → nothing.** Where the runtime has a hook surface, canon is injected (today's path). Where it does not, `poga` writes the payload into the lane and the opening prompt names the file to read first. Both are [`single-source-and-deliver`](../habits/master.md#single-source-and-deliver) mechanisms over one source. A hand-maintained per-runtime copy of canon is prohibited — that is the exact drift that produced one member's §11 stamp bug ([P16](../principles/master.md#p16--avoid-duplication)).

**D6 — Guards are declared per runtime, switchable per session, and reported live by the banner.** `check-bash` and `check-question` are Claude `PreToolUse` guards; a runtime with no interception point cannot satisfy them identically. [ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md)'s vocabulary already covers that — **satisfied / satisfied-differently / waived-with-compensation** — and `curate/check-binding.py` already validates it; this decision extends the declaration from the member to the **runtime registry row**. Three further rules:

- **Individually named, individually switchable.** Not one on/off for "guards." Each guard is its own switch, because the reasons to relax them differ per guard.
- **The switch is per SESSION, never per machine.** The substrate knows about guards and knows nothing about machines. Encoding a machine roster would make one member's hardware a dependency for every member — the same defect WI-0029 is in this version to remove. A member wanting a per-machine default expresses that in its own local config; the substrate only ever sees a per-session choice.
- **The banner reports live state, not the declared default.** Once a guard can be switched at launch, declared posture and actual posture can differ, and a disabled guard that still displays as present is the precise failure this decision exists to prevent. The picker guard additionally requires a **deliberate** override rather than an ordinary toggle, so it cannot be switched off by inheriting a config.

An unguarded lane that looks identical to a guarded one is [`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes) failing in its most consequential form, and a capability shipped with no marker reads as present ([`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)). Note the scope: switching a guard off disables *enforcement*; the underlying habit is canon and still binds ([ADR-0022](0022-architect-ingest-distillation-and-code-channel.md) — inherited, not negotiated).

**D7 — Supervision moves to the wrapper, and its granularity change is declared.** `poga` heartbeats while the child process lives, so the [ADR-0054](0054-heartbeat-on-tool-use-and-crash-reap-grace.md) crash-reap evidence model works for every runtime rather than only Claude. The heartbeat becomes **coarser** — process-alive, not tool-active — which strengthens the existing rule that a stale heartbeat is not death evidence, and must be reconciled with the 48 h grace rather than silently changing what a heartbeat means.

**D8 — Land is unchanged.** `session.py end` → rebase → gate → ff-only/CAS advance stays exactly as built ([ADR-0058](0058-land-per-session-with-an-isolated-gate.md), [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md)). It involves no agent — it is git plus a test run — so it is runtime-neutral by construction, and this is the least risky decision here.

The evidence is a non-Claude session landing work on that member's trunk today: one commit, three minutes after the last of the twelve `wi-alloc` reservations it consumed. Stated with its limit — git cannot attribute a session (every commit authors as the same user), so the link is the timing chain plus the commit's content matching exactly what those twelve numbers were drawn for. Strong, not airtight; the honest claim is that nothing in the land path had to be adapted and it worked.

**Phasing.** (1) Registry + verified exec + `--runtime` flag, with only the `claude-code` row — no behaviour change, fully testable. (2) Prep and Supervise inversion. (3) A second row plus its binding declarations. Each phase lands on its own.

## Alternatives Considered

- **Runtime as a per-member property.** Rejected — it is the operator's explicit ruling against, and it cannot express the actual goal: two lanes on one repo running different agents at once.
- **Full per-runtime parity.** Rejected — named the wrong goal by WI-0033 itself. A runtime declares what it meets; differences are declared, not erased.
- **Reimplement the guards natively for each runtime.** Rejected — unbounded work gated on each vendor's hook surface, and ADR-0041 already provides waiver-with-compensation for exactly this.
- **Keep the hooks and add a parallel hook set per runtime.** Rejected — N copies of the session ritual kept in sync by discipline is the [P16](../principles/master.md#p16--avoid-duplication) failure that produced one member's §11 drift.
- **Leave `poga` Claude-only; drive other agents by hand.** Rejected — a hand-opened lane is invisible to the coordination store and the reaper, and does not land through the CAS gate. That is the state the non-Claude runtime was in when this was written.

## Consequences

**Enables.** A codex lane and a Claude lane, live at once on one repo, landing through one gate. A member's choice of runtime becomes exercisable per session rather than per member. `poga` stops being the layer that silently pins the fleet to one vendor.

**Breaking.** Hook wiring moves out of `.claude/settings.json` into the wrapper; members' generated settings shrink. This requires a standard-version bump and a fleet redistribution pass, and interacts with WI-0055 (store redistribution, also a standard bump) and WI-0017 — they should ship as one version, not three.

**New problems.** Heartbeat granularity changes (D7). An unguarded runtime lane is a genuine reduction in safety posture, not a cosmetic difference — D6 makes it *visible*, and whether certain operations should be refused outright in such a lane is left open rather than settled here. And a wrapper that owns prep is a wrapper that can fail before the agent starts, so its own failure modes need the fail-open discipline the hooks already have.

**Downstream.** `curate/check-binding.py` extends to runtime-registry rows. `standard_check.py` needs a detector for the inverted lifecycle, per `ship-the-detector-with-the-capability` — shipped in the same pass, not after. An existing member's `binding.md` is live prior art to lift the declaration shape from.

## References

- WI-0033 — *Runtime-agnostic again: lifecycle inversion, runtime tiers, per-session runtime* (`breaking`); supersedes the held 2026-07-18 lifecycle-inversion draft. Source brief: `proposed-edits/federation-arch/accepted/2026-07-27-consultant-runtime-tiers-and-lifecycle-inversion.md`
- [ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md) — contract vs. binding; C1–C5
- [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) / [ADR-0070](0070-worktree-lanes-are-fleet-substrate.md) — `poga` as the lifecycle wrapper, and as fleet substrate
- [ADR-0054](0054-heartbeat-on-tool-use-and-crash-reap-grace.md) — heartbeat semantics and the reap grace
- [ADR-0058](0058-land-per-session-with-an-isolated-gate.md) / [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) — the land path this decision leaves untouched
- WI-0060 — the non-Claude session record defect, carrying the captured pre-erasure instance (the member's SESSION LOG row 21) this ADR's Context draws on
- WI-0061 — coordination records name neither the session nor the runtime (`session_id: "app"`, no runtime field)
- WI-0029 — federation-layout literals become declarations; D6's per-session-not-per-machine rule is the same defect class
- Live evidence, session ~110, in the member repo: the Claude journal (`runtime: claude-code`, locked lane) beside the non-Claude session's journal-less SESSION LOG row 21, its twelve `session_id: "app"` reservations, and its landed commit
- **Correction, session ~110.** This ADR's first draft attributed the non-Claude session to Claude, having found one Claude journal in the member's repo and assumed it was that one. Two sessions were running, one on each runtime, and the operator corrected the record. The corrected reading is stronger evidence than the original claim, since the two sessions' surviving records can be compared directly.
