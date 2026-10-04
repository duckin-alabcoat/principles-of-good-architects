# ADR-0062: Cross-lane coordination lives in the git common dir; claims/leases carry their own TTL

**Status:** Accepted
**Date:** 2026-07-22 (session 89)
**Builds on:** [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) (realizes its C4 claims + C5 leases, Phase 3), [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) (the `poga` worktree lanes this coordinates), [ADR-0057](0057-same-tree-concurrency-attributed-commits.md) (realizes its deferred D3 cross-tree visibility)
**Deciders:** the operator (chose the shape — claims sit on stable ROADMAP "Next" ids, one file per claim, a startup claimed-vs-available surface; session 78 handoff); Federation Architect (design)

## Context

[ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) gave the terminal
*isolation*: each `poga` session runs in its own worktree lane (own dir, index, branch) and
lands by compare-and-swap. That makes two lanes **safe** — neither clobbers the other. It does
**not** make them **coordinated**: nothing marks a work item as taken, so two lanes can both pick
up the same backlog item and build it twice, and two lanes drafting ADRs both compute "max + 1"
and grab the same number. This is [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md)'s
**C4 (claims)** and **C5 (leases)**, the last unbuilt phase of the concurrency arc — "only now is
true simultaneity safe *end-to-end*."

The load-bearing constraint is a direct consequence of how isolation was achieved. The liveness
sidecar `.session-state/` is deliberately **per-tree** (ADR-0057 D3 deferred cross-tree
visibility): a lane cannot see a sibling lane's `.live` file — that is *why* a `poga` lane is
never counted as an app-surface sibling. Coordination needs the **opposite**: a store every
concurrent lane can read and write. So the coordination store cannot live in a worktree's own
tree, and it cannot live on a branch either — a claim you must *land to the trunk* to advertise is
useless (the sibling won't see it until you merge, which is exactly when the collision is already
over).

## Decision

**1. The coordination store lives in the git common dir.** Every linked worktree of one repo
shares its `--git-common-dir` (the main `.git`). So `<git-common-dir>/poga-coord/<kind>/` resolves
to the *same physical directory* from every lane **and** the main checkout, sits outside every
working tree (never committed, no `.gitignore` needed), and is naturally ephemeral. This is the
cross-tree visibility ADR-0057 D3 deferred — realized where it belongs, in the substrate git
already shares across worktrees, not hand-rebuilt. `kind ∈ {claims, leases, adr-alloc}`.

**2. Each record carries its own epoch TTL; a live session refreshes on heartbeat.** Because the
per-tree sidecar can't carry cross-tree liveness, the *record* does: `expires_at` (epoch) is
authoritative, and the all-tools heartbeat (`session.py heartbeat`, ADR-0054) bumps every record
this session holds. A live session's holds never expire; a dead session's lapse. **Read-time
expiry is authoritative** — an expired record is reclaimable whether or not the reaper has swept
it — so correctness never depends on the reaper; the reaper (at start, beside the journal/lane
reapers) only tidies.

**3. The one atomicity guarantee is `O_EXCL` on an available name.** Two lanes racing to claim a
*free* name: exactly one wins the exclusive create, the other is refused and shown the holder. The
expired-reclaim path has a benign last-writer race (two reclaimers of the *same already-dead*
record) — harmless at POGA's minutes-to-days collaboration granularity (ADR-0051 alt D), and it
never lets two sessions hold a genuinely-available resource.

**4. C4 claims bind ROADMAP "Next" ids (the operator's chosen shape).** The claimable work list **is** the
`ROADMAP.md` `## Next` section — a bullet is claimable iff it carries an `<!-- id: slug -->`
anchor. The Next list is the single source; there is no second work list to drift
([P16](../principles/master.md#p16--avoid-duplication)). A claim is one record held by the **lane
branch** (`worktree-poga-N`), so "which lane is working on what" is the natural key and it survives
a claude-session restart within the lane. `session.py claim / release / claims` are the verbs
(exit 0 claimed / 1 refused / 2 unknown-id); a `claims:` line joins the start orientation block;
lane teardown frees a landed lane's claims immediately.

**5. C5 leases exclude the few genuinely single-writer ops.** Almost nothing qualifies (ADR-0051
C5 — "leasing broadly is Option A returning"). The enumerated federation set is the
`gen_settings`/`push-substrate` file-set: `push-substrate` writes every reachable **member
repo**, which worktree isolation does *not* cover (the target repos are shared,
not per-lane). `push-substrate` acquires the `push-substrate` lease around its fleet-mutating loop;
a live sibling is refused (exit 2); a dry-run needs no lease. `gen_settings`' *standalone*
own-file write is **not** leased on purpose — it writes this worktree's own
`.claude/settings.json`, so two lanes writing their own files never collide and a lease there would
only false-block a sibling.

**6. Rule 3 — atomic ADR-number allocation.** `session.py adr-next` reserves the next free number
in `adr-alloc/`, counting **both** `adr/*.md` files and live reservations, so a sibling that
reserved 0062 pushes the next lane to 0063. A reservation TTLs out; once the ADR file exists its
number is permanently accounted for by the file scan and never reused.

**Fail-open throughout.** If the common dir can't be resolved, every coordination op degrades to a
no-op (`try_acquire → (True, {})`, `push-substrate` proceeds unguarded): isolation still holds, and
a coordination-layer hiccup never bricks the caller.

## Consequences

- **Two lanes now coordinate, not just avoid clobbering.** `poga claims` (→ `session.py claims`)
  answers the operator's founding question — *"two sessions open; will they know what to work on?"* — with
  a live claimed-vs-available view. The final concurrency phase (ADR-0051 Phase 3) is built.
- **Coordination state is ephemeral and machine-local by construction.** It lives in `.git`, is
  never committed, and evaporates with the repo — correct, since it describes *this machine's*
  concurrent lanes. Cross-machine concurrency is not a goal (worktree lanes are local).
- **The reaper gained a third sweep** (coord records) beside journals (ADR-0051) and lanes
  (ADR-0060) — same "provably-dead evidence frees the hold" model, here via the record's own TTL.
- **New surfaces to get right:** the `<!-- id: slug -->` ROADMAP convention (a bullet with no
  anchor is silently non-claimable — intended, but a renamed id orphans its claim, surfaced as
  `[orphan]` in `claims`); the benign expired-reclaim race (documented, bounded); the fail-open
  paths (a wrong common-dir resolution silently disables coordination — mitigated by the tests
  pinning the common dir equals `<main>/.git` from every lane).
- **Not yet built:** the collision-proving **game day** (ADR-0051 hardening R4) — two live lanes
  proving double-claim refusal, lease exclusion, and distinct ADR numbers on the real substrate,
  not just in unit tests (which the session-83 isolation drill showed is where hidden defects
  surface). Fleet rollout of the coordination verbs is deliberately held with the rest of the
  concurrency substrate until the live drill passes.

## Alternatives considered

- **A committed `claims/` dir on the trunk.** Rejected: a claim only becomes visible on land, so a
  sibling can't see it during the window that matters, and every claim/release would be a commit +
  CAS on the trunk — turning coordination into contention on the exact ref the lanes race over.
- **Cross-tree liveness via the `.session-state/` sidecar.** Rejected: the per-tree sidecar is
  load-bearing for the ADR-0057/0060 isolation model (it's why a lane isn't miscounted as an
  app sibling). Making it cross-tree would reopen that. Putting liveness *in the record* (its TTL)
  keeps the two models cleanly separate.
- **A lock daemon / single-writer coordinator.** Rejected — Option A returning (ADR-0051): a
  master with a transferable crown, cross-machine babysitting, and it serializes what we want
  parallel. One-file-per-claim + O_EXCL is git-native optimistic concurrency, no daemon.
- **Leasing `gen_settings`' own-file write too.** Rejected: it writes a per-worktree file, so a
  lease there is a false-block, not an exclusion. The real shared resource is the *push*, which is
  the one that's leased.

## References

- [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) — C4 claims / C5 leases /
  Rule 3, the model this realizes (Phase 3).
- [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) — the `poga` worktree lanes.
- [ADR-0057](0057-same-tree-concurrency-attributed-commits.md) — D3 cross-tree visibility, deferred
  there, realized here.
- [ADR-0054](0054-heartbeat-on-tool-use-and-crash-reap-grace.md) — the heartbeat that refreshes
  holds.
- Implementation: `session.py` (`_coord_*`, `_lease_*`, `lease()`, `_adr_reserve_next`, the
  `claim`/`release`/`claims`/`lease`/`unlease`/`adr-next` subcommands),
  `curate/push-substrate.py` (lease wiring); tests `tests/test_coord.py`, `test_claims.py`,
  `test_leases.py`, `test_adr_alloc.py`.
