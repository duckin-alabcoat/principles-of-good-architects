# ADR-0020: Session-start/end rituals are a code harness, not a hand-run checklist

**Status:** Accepted
**Date:** 2026-06-04
**Deciders:** the operator, Federation Architect

## Context

The [federation-arch.md §11](../federation-arch.md) session rituals are a long
prose checklist the Federation Architect executes by hand at every session
boundary: `git status` → `git pull --ff-only`, detect the machine, stamp the
time, read the SESSION LOG to compute the next session number, check for an
orphan, write the start stamp, announce. The close-out mirror computes a
duration, writes the end stamp, fills the SESSION LOG title, and commits.

This is ten-plus tool calls of pure mechanism every session, re-derived from
prose each time. It is exactly the kind of deterministic work the operator's standing
rule targets: any step that can be done in code is done in code
([ADR-0019](0019-curate-gather-and-staging-boundary.md)). [ADR-0019](0019-curate-gather-and-staging-boundary.md)
already moved the curate gather to code and stood up the first `SessionStart`
hook (curate `--status`, read-only). This ADR extends the same treatment to the
session rituals themselves — the harness candidate carried since session 12.

The split is the same one ADR-0019 drew: code does the mechanism, the Architect
keeps the judgment. The judgment in the session rituals is narrow and specific —
the orphan **bad/keep** decision (which only the operator can make), the *"what happened
/ what's owed"* summary prose, the commit message, and the memory/registry/
producer sweeps. Everything else is mechanical.

## Decision

**The mechanical half of §11 is `session.py`, with `start` wired into a
`startup`-scoped `SessionStart` hook. The Architect stops hand-running the
checklist and instead reads the hook's orientation block.**

**Code (`session.py start`) — runs automatically at startup, no judgment:**
- `git status --porcelain`; if dirty, **BLOCK** and emit the dirty paths (never
  auto-resolve — [P9](../principles/master.md#p9--destructive-ops-confirmed)); if
  clean, `git pull --ff-only`.
- Detect the machine (`scutil --get ComputerName` → `Laptop` / `Runner`).
- Stamp the current time in the configured zone (abbreviation via `%Z`, so
  the stamp always matches the zone's current offset rather than a hardcoded
  abbreviation).
- Parse the SESSION LOG table → next session number (`NC` rows do not burn it).
- Orphan-detect: if the topmost entry has a `Start` but no `End`, emit `ORPHAN`
  and **do not write** the new stamp — the orphan decision is the operator's.
- Otherwise **write the start stamp itself** — the SESSION LOG row and the prose
  entry header — so the stamp survives any context loss before the Architect
  speaks.
- Emit a structured orientation block (version, machine, time, session number,
  pull result, inbox contents, orphan flag) the Architect reads to announce and
  summarize.

**Code (`session.py end`) — mechanical close, invoked by the Architect at
close-out:** compute the duration from the parsed `Start`, append the end stamp,
fill the `(in progress)` title in both the SESSION LOG row and the prose header,
and — with `--commit <msg> --push` — stage/commit/push. It refuses to double-
close an already-ended entry.

**Stays with the Architect (judgment, unchanged):** the orphan bad/keep question
to the operator, the handoff body and the summary prose, the commit *message*, and the
memory / registry / producer-learnings sweeps.

**The hook is `startup`-scoped, deliberately.** `SessionStart` also fires on
`resume` and `clear`, which **continue** an existing session rather than open a
new one. `session.py start` writes and increments the counter, so firing it on
resume/clear would spuriously open a phantom session. The curate `--status` hook
stays unscoped (all sources) because it is read-only and harmless.

## Alternatives Considered

- **Advisory script — emit the facts, let the Architect write the stamp.**
  Rejected. the operator's call (this session): the script writes the stamp itself. It is
  the stronger "in code" version and makes the stamp survive context loss before
  the announce, which is the whole reason §11 step 6 demanded an early write.
- **One unscoped `SessionStart` hook running both scripts.** Rejected — it would
  fire `session.py start` on `resume`/`clear` and open phantom sessions. The
  write-vs-read distinction is exactly why the two hooks carry different matchers.
- **Encode the end-side commit message in code too.** Rejected — the commit
  message is judgment ([P8](../principles/master.md#p8--decisions-auditable) /
  [`conventional-commits`](../habits/master.md#conventional-commits)). The script
  takes it as an argument; it does not author it.
- **Leave §11 as hand-run prose.** Rejected — it is the same toil ADR-0019
  eliminated for the curate pass, re-paid every session. The carried candidate is
  worth building.

## Consequences

- Session-start collapses from ~10 manual tool calls to one hook firing plus the
  Architect reading the orientation block and talking to the operator. The §11 prose is
  rewritten to describe reading the hook output, not hand-executing the steps;
  the Architect no longer writes the start stamp manually (doing so would
  double-stamp against the hook).
- `session.py` is **system, tracked in git** — it writes to `session-handoff.md`
  (already tracked) and reads `federation-arch.md` for the version. No new
  gitignore surface.
- Orphan handling is unchanged in outcome: the harness *detects* and *flags*, the
  resolution stays the Architect-plus-the operator conversation §11 step 5 already
  specifies. A flagged orphan blocks the auto-write, so a phantom stamp is never
  written past an unresolved orphan.
- This is the second instance of the [ADR-0019](0019-curate-gather-and-staging-boundary.md)
  "code does the mechanism, LLM does the judgment" pattern. Two instances
  (curate, session rituals) make the pattern a candidate for promotion to a named
  habit — logged for a future curate review, not promoted here.
- No effect on any participating Architect — federation-internal tooling. The
  *shape* (rituals as a hook-driven harness) is a lift candidate other Architects
  could adopt later, surfaced through the normal redistribute path, not pushed.

## References

- [ADR-0019: Curate pass is a code gather plus human review](0019-curate-gather-and-staging-boundary.md)
  — the "code does the mechanism, LLM does the judgment" rule and the first
  `SessionStart` hook this ADR builds beside.
- [federation-arch.md §11](../federation-arch.md) — the session rituals this
  harness mechanizes; rewritten in the same version bump.
- [`session-start-git-ritual`](../habits/master.md#session-start-git-ritual),
  [`session-stamp-and-counter`](../habits/master.md#session-stamp-and-counter),
  [`session-orphan-detection`](../habits/master.md#session-orphan-detection),
  [`session-end-git-ritual`](../habits/master.md#session-end-git-ritual) — the
  universal habits this harness is the federation-side code enforcement of.
- [ADR-0007: GitHub as system storage](0007-github-as-system-storage-data-excluded.md)
  — the blanket maintenance authority under which `session.py end` commits.
- Source conversation: Federation Architect session 22 (2026-06-04).
