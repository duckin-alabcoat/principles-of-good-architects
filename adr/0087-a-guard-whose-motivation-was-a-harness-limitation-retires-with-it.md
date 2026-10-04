# ADR-0087: A guard whose motivation was a harness limitation retires with it

**Status:** Accepted
**Date:** 2026-08-04
**Deciders:** the operator (registry-Accept, session ~116 — asked in plain terms whether to retire the rule that blocks chained shell commands, answered *"1. yes."*; earlier, via the commissioning brief, asked that the retired guard remain available as a non-default switch), external consultant (the scope analysis separating the three policies), Federation Architect (the retirement mechanism and the check-order defect it exposed).
**Retires:** the `no-compound-bash` universal habit (`habits/master.md`, parent [P7](../principles/master.md#p7--transparency-at-conversation-layer)).
**Leaves intact:** the destructive-git guard in the same function, and [P7](../principles/master.md#p7--transparency-at-conversation-layer) itself.

## Context

`session.py check-bash` was built as a structural guard under
[`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)
after `no-compound-bash` was violated twice in session 26 despite being an adopted
text-only habit, then broadened in session 31 from the git-only subset to any
2+-operation chain. It worked exactly as designed. It has been denying compounds
fleet-wide ever since.

The habit stated two failure modes. The second one was load-bearing:

> **Permission allow-list bypass** — compounds may not match single allow-list prefix
> patterns and surface as prompts with no per-step context.

That is a description of a **harness limitation**, not of a property that makes an
Architect good. Settings allow-strings are prefix-matched, so no allow-string can match
`cd x && find .`; the practical consequence was that every chain prompted, and
one-op-per-call was the discipline that let the allow-list do its job at all.

The harness's **auto permission mode** removes that constraint at the source: commands
are judged directly rather than matched against strings. The tax remains and buys
nothing. During the very session that triaged the consultant's brief, the guard fired
three times, twice on read-only probes — a `cd`-then-read and a plain `mv`.

The first failure mode — narration specificity collapsing across a chain — is real and
survives, but it never needed this habit: it is
[`narrate-consequential-tool-calls`](../habits/master.md#narrate-consequential-tool-calls),
which is untouched. An Architect still says what it is about to do; it simply no longer
pays a per-call tax to say it.

The awkward part is that `check-bash` is **three policies in one function**, and only one
of them has this problem. Retiring "the guard" wholesale would have taken a genuine
safety floor with it.

## Decision

**D1 — The compound deny is retired, and the habit is Deprecated with it.**
`no-compound-bash` flips Accepted → Deprecated in `habits/master.md`, with its statement
and both failure-mode arguments retained in place for provenance and an added *Why this
was retired* section. Retiring the guard while leaving the habit absolute — or the
reverse — is precisely the doctrine-drift this project has spent its life avoiding: a
default-off guard under a still-binding rule means the written rule and the enforced rule
disagree, and the written one loses silently.

**D2 — The code is retired behind a switch, not deleted.** `compound_violation` stays
live in `session.py` and stays covered by its existing parser tests, gated on
`"compound_bash_guard": true` in `session.config.json`. Absent or false — the default,
and no member declares it — the check is skipped entirely.

Two reasons for a flag over a deletion. the operator asked for recoverability explicitly, as a
non-default switch. And a policy that lives only in git
history is not recoverable in practice: it rots against the code around it, and by the
time anyone wants it back, restoring it is a rewrite. Kept behind a flag **and under
test**, it stays true. The switch is only worth having if the code behind it stays green.

**D3 — The destructive-git guard is motivation-independent and stays unconditional.**
`destructive_git_violation` — force/mirror/delete push, `reset --hard`, `clean -f`,
`filter-branch`, `branch -D`, `update-ref -d`, and the rest — is **not** gated on the
flag, and does not consult config at all.

Its motivation was never prompt-matching. It is the
[P9](../principles/master.md#p9--destructive-ops-confirmed) no-work-destroyed floor, and
it is the *only* thing standing in front of the `git -C <path>` forms: the settings
deny-list is prefix-anchored and structurally cannot match a path in the middle of a
command. Remove it and `git -C <repo> push --force` is completely ungated. Under a
harness that now approves by judgment, a deterministic floor underneath matters **more**,
not less.

**D4 — The check order flips, and this was a latent defect, not a cosmetic change.**
`cmd_check_bash` ran `compound_violation(cmd) or destructive_git_violation(cmd)`. That
was safe only because compounds were *always* denied first — with the compound deny off,
a chain like `echo hi && git -C /x reset --hard main` would have reached the destructive
check as a compound. It survives because `destructive_git_violation` already iterates
`top_level_segments()` and therefore covers compounds on its own; its docstring claimed
*"compounds are denied upstream"*, which became false the moment D1 landed, and is
corrected. Destructive now runs first and unconditionally; the compound deny is consulted
only when D2's flag is on. Verified live in both orders before the tests were written.

**D5 — The flag is read independently of `_load_config`, defaulting to off.**
`_load_config` returns `None` on *any* malformed field, and `check-bash` carries a
standing promise that it "can never break just because config is missing." Reading the
flag through the main config parse would make an unrelated typo in `timezone` silently
change a security-relevant policy. `compound_guard_enabled()` does its own read in its own
`try/except` and returns `False` on any error, so the degraded state is
*compounds allowed, destructive git still denied* — the safe direction on both axes.

**D6 — The general rule, which is the reusable half.** When a guard is proposed for
retirement, ask: **was this guard's motivation a limitation of the tooling, or a standing
property of good work?** A guard motivated by a harness limitation retires when that
limitation does. A guard motivated by a safety property does not, and a capability
upgrade in the harness is never an argument against it — often the reverse, since more
capability means more that can go wrong without a deterministic floor. `check-bash`
answers both ways in the same function, which is why the two halves part company here.

## Consequences

**Fleet rollout is free and asymmetric in the right direction.** `session.py` ships
byte-identical, the flag lives in the one per-system file, and no member declares it — so
the next `push-substrate` turns the deny off everywhere with zero per-member action, the
zero-touch shape of [ADR-0039](0039-appliable-brief-schema-and-auto-adopt-mechanism.md) /
[ADR-0050](0050-headless-background-adoption-runner.md). Any member that wants it back
adds one config line.

**Two side benefits are genuinely lost, and were weighed rather than waved off.**
One-op-per-call produced clean one-line-per-operation `guard-firings.jsonl` records and
atomic per-op permission decisions. Both are real; neither justifies an absolute habit
whose founding motivation is gone. If the audit-log granularity turns out to matter,
that is its own problem with its own fix, not a reason to keep this one.

**Doctrine surfaces regenerate rather than being hand-edited.** `CANON.md` (45 → 44
universal habits — `Deprecated` entries are excluded from the digest) via
`curate/distill.py`; `standard-settings.json`'s `//pretooluse` and `//git-c` comment
blocks, which described the compound deny as unconditional, edited at the floor source
and pushed out through `curate/gen_settings.py`. The `--check` gates on all three
generators are what stop this ADR from quietly drifting away from the files it describes.

**The deny message no longer cites a retired habit.** With the flag on, the block now
names the config switch that produced it rather than a rule that no longer exists — a
message pointing at retired doctrine sends its reader to a dead end.

**This ADR is a precedent, and will be used again.** Every guard in the substrate was
built for a reason, and some of those reasons are facts about tooling at a point in time.
D6 is the question to put to the next one.
