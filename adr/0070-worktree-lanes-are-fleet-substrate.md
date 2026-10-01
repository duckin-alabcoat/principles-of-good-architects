# ADR-0070: Worktree lanes are fleet substrate, not a federation privilege

**Status:** Accepted
**Date:** 2026-07-26 (session 98)
**Builds on:** [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) (the lane lifecycle), [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) (the coordination store), [ADR-0034](0034-settings-are-federation-pushed-generated-substrate.md) (settings are pushed generated substrate), [ADR-0047](0047-versioned-standard-substrate-and-rollout-reconciliation.md) (versioned standard substrate)
**Deciders:** the operator (session 98 — asked whether any system can do concurrency, wanting it for one member, then directed the build); Federation Architect (design)

## Context

the operator asked directly whether any system could do concurrency, because one member needed it. Answering it honestly exposed a gap the federation had been carrying without noticing.

The concurrency arc landed in three layers, and they shipped on different tracks:

| Layer | Mechanism | Shipped to members? |
|---|---|---|
| Journal model ([ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md)) | every session owns its journal; the handoff is compiled | **yes** — in `session.py` |
| Coordination store ([ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md)) | `claim` / `lease` / `adr-next` in the git common dir | **yes** — in `session.py`, standard v1.4.0 |
| Land machinery ([ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md)) | `_land_worktree_lane`, `worktree-remove` | **yes** — in `session.py` |
| Teardown wiring | the `WorktreeRemove` hook | **no** — federation `settings_extras` |
| Launcher | the `poga` script | **no** — federation-only file |

Verified live against that member's repo, not a mirror: it self-checks at **standard v1.4.0, clean**, and its `session.py` carries `_land_worktree_lane`, the `worktree-remove` subcommand, the coord store, claims, leases and `adr-next`. It also has **no** `WorktreeRemove` hook and **no** `poga`.

So every member could already *arbitrate* between lanes it had no way to *launch*. That is a strange shape to have shipped: the hard, subtle half (a race-free shared store, a linear land that advances the trunk by plumbing) went out fleet-wide, and the two easy halves — a bash wrapper and eleven lines of JSON — stayed home. Nothing decided this. It is residue from `poga` having been built when the federation was the only repo that needed it.

The cost was not theoretical. `push-substrate.py` refreshes `session.py` fleet-wide on every rollout, so members have been carrying lane-landing code that nothing could reach.

### Why `poga` was not shippable as written

Its `ROOT` — the repo it operates on — was resolved from the **script's own location**. That was correct when there was exactly one repo. With N members it is actively wrong: an installed `poga` symlink on the PATH would drive the *federation* checkout no matter which member you were standing in. Shipping the file unchanged would have produced a launcher that silently allocated lanes in the wrong repo — the same silent-wrong-root class as the session-90 symlink bug, one level up.

## Decision

### D1 — `poga` resolves its repo from the caller's cwd

`ROOT` comes from the **caller's cwd** (via the git common dir, so a lane resolves to its main checkout), not from where the script lives. This is what makes one byte-identical file correct substrate: the same bytes drive whichever member you are standing in.

Resolution is **lazy** — only `session` and `lanes` require a repo. `bootstrap --adopt` in a plain non-git directory is a supported [ADR-0067](0067-adopt-in-place-is-the-third-onboarding-path.md) case (2.39.0 auto-inits it), so a hard repo requirement at script top would have broken the one verb that must work outside a repo.

`install` is the deliberate exception: it anchors the symlink on the **main checkout** of whichever repo the copy lives in. A link into an ephemeral lane worktree (or the merge gate's scratch checkout) would dangle the moment that worktree was removed.

### D2 — One file, two scopes, honestly labeled

`session` / `lanes` / `install` are **universal**. `bootstrap` / `restore` are **federation-only** — they need `poga_cli.py`, which is not shipped — and resolve against the script's own directory, refusing on a member copy with a named message that says what does work there and where to go for the rest.

Rejected: a members-only fork of the script. That is a hand-maintained duplicate ([P16](../principles/master.md#p16--avoid-duplication)) of a file whose universal half is the overwhelming majority of its content.

### D3 — The teardown hook is floor, not an extra

`WorktreeRemove` → `session.py worktree-remove` moves from the federation's `session.config.json` `settings_extras` into `standard-settings.json`, the shared floor — the same promotion `apply-briefs` got in session 66, for the same reason. A capability every member should have does not belong in one member's local additions.

### D4 — Standard v1.5.0, capability `worktree-lanes`, scope `claude-hook`

One capability requiring **both** halves (launcher file + teardown hook): a member with one and not the other is exactly the half-state the marker exists to make visible. The land half needs no detector — it is `session.py end`, already covered by `session-harness`.

Scope is `claude-hook`, unlike v1.4.0's runtime-agnostic coordination store: `claude --worktree` and the `WorktreeRemove` event are Claude-Code bindings, so a non-Claude member (or the non-Claude side of a multi-runtime member) reads **n/a**, never "behind" — [ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md)'s rule that an obligation met by another binding is not a gap.

### D5 — The push carries the executable bit

`push-substrate.py` wrote bytes only. A pushed `poga` would have landed at the umask default and been **present but unrunnable** — and the capability detector, which tests presence, would have agreed while `./poga` returned "permission denied". So `poga` joins an `EXECUTABLE` set that is chmod'd after every write, and `diff_files` treats a **mode-only drift as pending** (bytes-only comparison would call such a repo up to date forever). `bootstrap.py`'s seeding has the same hazard — `shutil.copyfile` does not carry mode — and is fixed the same way, via one `SHARED_SUBSTRATE` list used by both onboarding paths instead of two hand-maintained copy blocks.

## Consequences

- **Concurrency becomes a fleet capability.** Any member at v1.5.0 can run `poga` for isolated parallel lanes. The member that prompted the question gets it on the next rollout.
- **One `poga` symlink serves every repo.** The installed `poga` link is now a shared host resource ([`choose-shared-host-resource-defaults-for-siblings`](../habits/master.md#choose-shared-host-resource-defaults-for-siblings)); byte-identical bytes plus cwd-resolution make whichever copy wins the link correct for every repo. The one asymmetry — a link installed from a member cannot reach `bootstrap`/`restore` — is reported, not silent.
- **The fleet reads "behind" until the rollout runs.** Every member is at v1.4.0 against a v1.5.0 latest. That is the honest signal, and it clears with the push.
- **Non-Claude members are unaffected**, reading n/a rather than a false gap.

## Notes

- The launcher/teardown split was found by answering a user question, not by an audit — the fleet-parity view showed v1.4.0 clean everywhere because the missing pieces had no capability marker. A capability with no detector is invisible to the tool built to detect capabilities; D4 closes that for this one, and the general lesson (a *shipped* capability needs a marker or it cannot be reconciled) is the reusable part.
- 697 tests green. Coverage in [`tests/test_poga_fleet.py`](../tests/test_poga_fleet.py): cwd-resolution both directions (a member's lane is seen; federation lanes do not leak in), the named refusals, floor placement + no double-declaration, the four detector states, and the executable-bit path through both push and seed.
