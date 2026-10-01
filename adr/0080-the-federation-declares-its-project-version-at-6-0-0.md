# ADR-0080: The federation's project version is 6.0.0, and a major is a named milestone

**Status:** Accepted (D1–D6). **D7 was Proposed and is now RESOLVED by [ADR-0081](0081-a-major-is-declared-not-derived.md)** (same day) — the operator agreed, and went further: the `breaking` label itself is retired, because a major means the system was *fundamentally altered*, which may or may not break anything. See ADR-0081 for the decision as landed. (Status-flip only; the text below is the record as written and is not edited.)
**Date:** 2026-07-30
**Deciders:** the operator (third-number ruling 2026-07-29; accepted the proposed historic majors and the minor/patch rule, 2026-07-30), Federation Architect.

## Context

The federation has run ~110 sessions without ever versioning **itself**. Two numbers
exist and neither can carry the work list:

| Number | Versions | Current |
|---|---|---|
| `federation-arch.md` | this Architect's identity, mission, scope | 2.47.0 |
| standard substrate | the contract members conform to — a *declared* claim, retired by [ADR-0068](0068-retire-the-declared-standard-version.md); detection is now the only signal | n/a |

Half the work list is neither: a backup check, one member's floor, the lane reaper.
Stretching the substrate number over them would make it lie to every other system
about what it means. the operator's 2026-07-29 ruling (recorded on WI-0045): a **third**
number, three genuinely different subjects.

That ruling put the starting point at `1.0.0`, reasoning that `0.x` ends at MVP and we
are far past it. On 2026-07-30 the operator opened it up instead: estimate where the number would be had
versioning started on day one, and go from there. So the number was reconstructed from the record rather than
picked.

**The reconstruction changed the definition, not just the number.** The first pass
asked *"which changes broke things for the other systems?"* — strict semver's question.
the operator's answer to a parallel question redefined the axis: versions are tied to
features; shipping something to every member is at least a minor, and fixing what it
broke is a patch. Breakage is a
**patch**, not a major. Which leaves majors with no trigger — unless a major is a
*milestone*, which is exactly the alternative WI-0045 had already recorded (*"or 'none,
majors are milestones'"*).

Read back that way, the six reconstructed eras are not six breakages. They are six
**chapters**, and they read like chapter titles. the operator accepted them as such.

Why strict semver's compatibility meaning does not apply here, stated once so it is a
choice rather than an oversight: **no member pins a version of the federation.** We
push substrate to them; nobody reads our number to decide whether to upgrade. A number
that cannot gate an upgrade cannot warn anyone, so making it a compatibility contract
would be pretending. WI-0045 anticipated this too — *"strict semver only means
something where another system consumes a contract; where it doesn't, say so rather
than pretending."*

## Decision

**D1 — The federation's project version is `6.0.0` as of 2026-07-30.** Not `1.0.0`.
Reconstructed from the record, accepted by the operator.

**D2 — The six historic majors, recorded here as the provenance for D1.** Each is a
chapter, named by what stopped being true for the other systems:

| Version | Date | The chapter | Rests on |
|---|---|---|---|
| 1.0.0 | 2026-05-28 | The federation exists — naming, taxonomy, registries, receipt ritual settled. One member; nothing to break yet. | ADR-0001–0018 |
| 2.0.0 | 2026-06-15 | **Canon becomes inherited.** Role docs lose their hand-written principles/habits tables; canon is generated and injected instead. | [ADR-0022](0022-architect-ingest-distillation-and-code-channel.md), [ADR-0024](0024-standard-role-doc-section-is-generated-and-injected.md) |
| 3.0.0 | 2026-07-05 | **The substrate becomes mandatory.** Existing Architects retrofit onto `session.py`, move their inbox in-repo, and hand `settings.json` over as a generated file. | [ADR-0023](0023-standard-operating-substrate.md), [ADR-0028](0028-converge-inbox-model-onto-repo-local.md), [ADR-0034](0034-settings-are-federation-pushed-generated-substrate.md) |
| 4.0.0 | 2026-07-13 | **The floor stops assuming Claude Code.** A non-Claude member declares a `binding.md` saying how it meets each obligation. | [ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md), ADR-0045 (withheld) |
| 5.0.0 | 2026-07-21 | **The master session is retired.** One journal per session, compiled handoff, branch-and-gate landing. | [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md), [ADR-0058](0058-land-per-session-with-an-isolated-gate.md), [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md), [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) |
| 6.0.0 | 2026-07-27 | **Lanes become fleet substrate.** Members get `poga` and a teardown hook they never had; a member's runtime shape is declared rather than assumed. | [ADR-0070](0070-worktree-lanes-are-fleet-substrate.md), ADR-0071 (withheld) |

Recorded honestly: **6.0.0 is thinner than the rows above it.** A third ADR originally
grouped there ([ADR-0068](0068-retire-the-declared-standard-version.md), retiring the
declared standard-version) turned out to break nothing — the field was blank on every
established member anyway — so 6.0.0 rests on two ADRs, not three.

No minor or patch history is reconstructed for the ~60 non-breaking ADRs. That is
archaeology with no payoff, and inventing cut points that never happened would be
fabricated data.

**D3 — A MAJOR is a named milestone, declared deliberately.** Not derived, not
triggered by any label. A major is a promise made outward and it gets a name; the next
one is already named (*"Members stop inheriting the federation's shape"*, WI-0045),
carried by the seven work items labelled `impact: breaking`.

**D4 — MINOR: we shipped something to the fleet. PATCH: everything else.** the operator's
words: shipping a feature to everyone is at least a minor; breaking something and going
to fix it is a patch. A repair introduces no new capability, so it is a patch however
much work it was.

**D5 — The breaking-change surface, stated as WI-0045 §6.1 requires: strict semver's
"breaking" is deliberately NOT our major trigger.** We say so rather than pretending.
The `impact: breaking` label keeps its meaning and its value — *this forces members to
change their own files* is exactly the thing worth knowing before shipping — it simply
does not, on its own, cut a major.

**D6 — The number's home is `releases/` once WI-0044 lands.** Until then `6.0.0` is
declared here and in the role doc's versioning section. It is not added to
`session.config.json`: a hand-written version claim in a config file is precisely the
[ADR-0068](0068-retire-the-declared-standard-version.md) mistake, and per
[ADR-0078](0078-version-sets-are-planned-releases-are-harvested.md) the number is
computed at the cut from the release record rather than typed.

**D7 (Proposed — needs the operator) — Amend [ADR-0078](0078-version-sets-are-planned-releases-are-harvested.md)
D2 so the derived bump computes MINOR and PATCH only.** ADR-0078 D2 keeps the number
derived as `max(impact)` over the shipped set, mapping `breaking → major`. D3/D4 above
make majors non-derivable, so the two rules disagree. Where they diverge: **a lone
`breaking` item shipping outside a declared chapter** — ADR-0078 cuts a major for it
automatically; D3 does not. Recommendation: keep `impact` exactly as it is (good data,
cheap per-item question), change the derivation to `feature → minor, else patch`, and
let MAJOR be set only by declaring a milestone complete.

This is cheap to fix **right now** and expensive later: the mapping today exists as a
comment (`sessionlib/config.py:2771`) and CLI help text, and the bump **computation is not
implemented** because `releases/` does not exist yet ([ADR-0078](0078-version-sets-are-planned-releases-are-harvested.md)
Reality: `Not-built`). Nothing changes in practice at this instant either, because the
seven `breaking` items *are* the 7.0.0 chapter, so both rules would currently produce a
major — which is exactly why the divergence would otherwise ship unnoticed and only
surface the first time a breaking item travels alone.

## Alternatives Considered

**Start at `1.0.0` as originally ruled.** Rejected by the operator on 2026-07-30 in favour of a
reconstruction. It would also have made the first release record empty, leaving the
[ADR-0078](0078-version-sets-are-planned-releases-are-harvested.md) machinery with
nothing to run on.

**Keep strict semver — majors mean breaking changes.** Rejected: nothing consumes our
version as a compatibility contract, so the guarantee would be decorative, and it
contradicts the operator's explicit placement of breakage-repair at patch level.

**Majors are derived from the `impact` labels (status quo per ADR-0078 D2).** Rejected
in D7's recommendation: it hands the biggest number to whichever single item happens to
carry a `breaking` label, which is the opposite of a deliberate outward promise. Kept as
`Proposed` rather than decided, because amending an Accepted ADR is the operator's gate.

**Reconstruct minor/patch history too.** Rejected: ~60 ADRs, no payoff, and every cut
point would be invented.

## Consequences

- **The work list finally has a number that can carry it.** The seven `impact: breaking`
  items become the `7.0.0` set rather than a floating list, which is what WI-0045 was
  for.
- **The first capability discussion ([ADR-0078](0078-version-sets-are-planned-releases-are-harvested.md) D6) can now happen** — it was blocked on the federation having no
  version at all. That discussion derives the 7.0.0 set from capabilities; it is the operator's
  and mine together and is not discharged here.
- **WI-0044 becomes more clearly the keystone.** `releases/` is where the number lives
  (D6), where the set lives, and where the derived bump gets implemented — including
  D7's mapping if accepted. It currently reads `frees 2` and understates itself.
- **D7 is a live inconsistency until the operator rules.** Two accepted decisions disagree about
  what makes a major. It is recorded rather than silently resolved in code, and it must
  be settled before the bump computation is written, not after.
- **A number that warns nobody is a communication device.** It tells the operator which chapter
  we are in. It does not, and is not intended to, tell any member whether it is safe to
  upgrade — because no member chooses.

## References

- WI-0045 — declare the project version and name the breaking-change surface; carries
  the operator's 2026-07-29 third-number ruling and the 7.0.0 theme.
- WI-0044 — stand up `releases/`; the keystone D6 defers to.
- [ADR-0078](0078-version-sets-are-planned-releases-are-harvested.md) — version sets are
  planned, releases harvested; D2 is what D7 amends.
- [ADR-0068](0068-retire-the-declared-standard-version.md) — why the project version is
  not a hand-written config field.
- `proposed-edits/federation-arch/accepted/2026-07-29-consultant-versioning-policy.md` —
  the versioning brief WI-0045 was triaged from.
