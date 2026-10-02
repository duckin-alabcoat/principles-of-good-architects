# Architecture Decision Records

This directory holds ADRs for the Federation Architect system — one immutable record per non-trivial decision.

## Conventions

- **Filenames:** `NNNN-kebab-title.md`, 4-digit zero-padded, monotonically increasing.
- **Statuses:** Proposed | Accepted | Deprecated | Superseded by ADR-XXXX.
- **Immutability:** Accepted ADRs are not edited in place except to flip status (and add a historical note when superseded same-day). A revised decision gets a new ADR that supersedes the old one.
- **Template:** [template.md](template.md). Sections: Status, Date, Deciders, Context, Decision, Alternatives Considered, Consequences, References.

See [ADR-0001](0001-use-adrs.md) for the rationale.

## Index

**Reality** tracks each ADR's *implementation* status, separate from its *decision* `Status`, per the [`mark-adr-reality-status`](../habits/master.md#mark-adr-reality-status) habit ([P18](../principles/master.md#p18--verify-everything)) — ADRs are immutable, so reality lives here, not edited into the record. `Built` = the decided artifact exists and works · `Partial` = core built, a named consequence still pending · `Not-built` = decided, not yet built · `—` = doctrine/policy (the decision *is* the artifact) or superseded. Refreshed at session-end.

**The numbers are not contiguous, and that is by design.** ADR numbers are *drawn* from the allocator, never picked ([ADR-0069](0069-shared-numbers-are-compiled-or-drawn-never-declared-or-picked.md)), and a drawn number is never reused — so a number that was drawn and then not spent leaves a permanent hole. Every hole is listed in [Unused numbers](#unused-numbers) below, and that list is **read by the allocator**, not just by people: `adr-next` refuses to hand out any number the table declares, and the land gate refuses one written by hand (WI-0379). Editing that table changes what numbers the harness will issue, so keep its rows in the shape below — `| NNNN | when it was drawn | why |`. **A number missing from this table and missing from that list is a missing ROW, not a missing ADR** — `curate/check_substrate_docs.py` is what tells the two apart, because a reader cannot.

| # | Title | Status | Reality | Date |
|---|---|---|---|---|
| [0001](0001-use-adrs.md) | Record federation decisions as ADRs | Accepted | — | 2026-05-21 |
| [0002](0002-naming-convention.md) | Architect and system naming convention | Superseded by [ADR-0006](0006-naming-convention-corrected.md) | — | 2026-05-21 |
| [0003](0003-federation-architect-is-a-participant.md) | The Federation Architect is a federation participant | Accepted | — | 2026-05-21 |
| [0004](0004-auditor-as-separate-role-class.md) | Auditor as a separate role class from Architect | Accepted | — | 2026-05-21 |
| [0005](0005-reviewer-split.md) | Split a mixed Architect role into a build Architect and a separate Auditor pattern | Accepted | — | 2026-05-21 |
| [0006](0006-naming-convention-corrected.md) | Naming convention — corrected to include orchestrator-agent slot | Accepted | — | 2026-05-21 |
| [0007](0007-github-as-system-storage-data-excluded.md) | GitHub as system storage; data excluded by policy and gitignore | Accepted | — | 2026-05-21 |
| [0008](0008-three-bucket-taxonomy.md) | Three-bucket taxonomy — principle, habit, preference | Accepted | — | 2026-05-22 |
| [0009](0009-data-storage-mechanics.md) | Data storage mechanics — data root, habit registry, user profile | Accepted | Built | 2026-05-23 |
| [0010](0010-registries-canon-only-sidecar-history.md) | Registries are canon-only; sidecar history pattern is uniform across all three | Accepted | — | 2026-05-23 |
| [0011](0011-data-root-config.md) | Data-root configuration form — declared in role-doc top metadata | Accepted | — | 2026-05-26 |
| [0012](0012-per-architect-repo-conventions.md) | Per-Architect repository conventions | Accepted | — | 2026-05-26 |
| [0013](0013-receipt-ritual.md) | Receipt ritual — federation edit distribution mechanism | Accepted | Built | 2026-05-26 |
| [0014](0014-existing-architect-retrofit.md) | Existing-Architect retrofit mechanism | Accepted | Built | 2026-05-26 |
| [0015](0015-work-package-briefs.md) | Work-package briefs as the federation parallel-work mechanism | Accepted | — | 2026-05-26 |
| [0016](0016-memory-is-local.md) | Memory is local; cross-machine continuity is the session-handoff + registries job | Accepted | — | 2026-05-26 |
| [0017](0017-bootstrapping-is-a-federation-responsibility.md) | Bootstrapping new Architects is a first-class federation responsibility | Accepted | — | 2026-05-28 |
| [0018](0018-adopted-principles-and-habits-are-system.md) | Adopted principles and habits are system, not data | Accepted | — | 2026-05-28 |
| [0019](0019-curate-gather-and-staging-boundary.md) | Curate pass is a code gather plus human review, not an agent harness | Accepted | Built | 2026-06-04 |
| [0020](0020-session-rituals-are-a-code-harness.md) | Session-start/end rituals are a code harness, not a hand-run checklist | Accepted | Built | 2026-06-04 |
| [0021](0021-cross-system-status-surface.md) | Cross-system status surface (per-system `STATUS.md`; federation publishes, does not aggregate) | Accepted | Built | 2026-06-07 |
| [0022](0022-architect-ingest-distillation-and-code-channel.md) | How Architects ingest principles & habits — generated distillation + code channel, inherited not negotiated | Accepted | Built | 2026-06-14 |
| [0023](0023-standard-operating-substrate.md) | Standard operating substrate — systems converge to one plumbing; additions allowed, subtractions not | Accepted | Built | 2026-06-14 |
| [0024](0024-standard-role-doc-section-is-generated-and-injected.md) | The standard role-doc section is generated and injected, not hand-authored per Architect | Accepted | Built | 2026-06-15 |
| [0025](0025-bootstrap-is-a-code-harness.md) | Fresh-Architect bootstrap is a deterministic code harness | Accepted | Built | 2026-06-18 |
| [0026](0026-cross-machine-execution-is-standard-substrate.md) | Cross-machine execution is standard substrate | Accepted | — | 2026-06-20 |
| [0027](0027-federation-delivers-edits-into-target-inboxes.md) | Federation delivers proposed-edit briefs into target inboxes over the shared volume | Accepted | — | 2026-06-21 |
| [0028](0028-converge-inbox-model-onto-repo-local.md) | Converge the federation inbox model onto repo-local; the outlier's Option B is the vehicle | Accepted | Partial | 2026-06-21 |
| [0029](0029-receiving-architects-auto-adopt-at-startup.md) | Receiving Architects auto-adopt federation edits at next startup | Accepted | Built | 2026-06-21 |
| [0030](0030-roadmap-deliverable-format-federation-owned.md) | `ROADMAP.md` is a standard deliverable; its format is federation-owned and rolls out on change | Accepted | Built | 2026-06-21 |
| [0031](0031-delivery-integrity-self-contained-briefs.md) | Delivery integrity — self-contained briefs and auto-pushed generated substrate | Accepted | Built | 2026-06-21 |
| [0032](0032-session-label-derived-from-architect-id.md) | Session-picker label is derived from `architect_id`, not a hand-set field | Accepted | Built | 2026-06-21 |
| [0033](0033-non-derivable-data-is-backed-up-at-machine-level.md) | Non-derivable gitignored data is backed up at machine level | Accepted | — | 2026-07-02 |
| [0034](0034-settings-are-federation-pushed-generated-substrate.md) | `.claude/settings.json` is federation-pushed generated substrate (floor + per-system extras) | Accepted | Built | 2026-07-05 |
| [0035](0035-startup-turn-budget-inject-and-git-diagnosis.md) | Startup turn budget — inject-don't-read and git-state diagnosis | Accepted | Built | 2026-07-05 |
| [0036](0036-session-liveness-sidecar-and-orphan-auto-triage.md) | Session liveness sidecar and evidence-based orphan auto-triage | Accepted | Built | 2026-07-05 |
| [0037](0037-repo-path-locator-map-unlocated-is-not-unonboarded.md) | A repo-path locator map for reconcile; UNLOCATED is not "not onboarded" | Accepted | Built | 2026-07-06 |
| [0038](0038-a-shared-resource-is-scoped-by-who-can-reach-it.md) | A shared substrate's exposure is scoped by who can reach it, and enforced on the host that serves it | Proposed | Not-built | 2026-07-06 |
| [0039](0039-appliable-brief-schema-and-auto-adopt-mechanism.md) | Appliable-brief schema + auto-adopt mechanism (`apply.py`) | Accepted | Built | 2026-07-06 |
| [0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md) | Runtime-agnostic substrate floor — contract vs. binding | Accepted | Built | 2026-07-11 |
| [0042](0042-deployment-classes-and-promotion-discipline.md) | Deployment classes and promotion discipline | Accepted | Partial | 2026-07-12 |
| [0043](0043-session-stamps-posted-via-plain-stdout-hook.md) | Session stamps are posted to the user via a plain-stdout hook, not systemMessage | Accepted | Built | 2026-07-12 |
| [0044](0044-check-bash-auto-approves-safe-git.md) | check-bash auto-approves the safe git surface (the `git -C` prompt fix) | Accepted | Built | 2026-07-13 |
| [0046](0046-per-system-comms-surface.md) | Per-system `comms/` surface (agent→the operator communication; federation publishes the contract, does not aggregate) | Accepted | Partial | 2026-07-14 |
| [0047](0047-versioned-standard-substrate-and-rollout-reconciliation.md) | Versioned standard substrate + code-verified rollout reconciliation | Accepted | Built | 2026-07-14 |
| [0048](0048-federation-metrics-mining-layer.md) | Federation metrics — one deterministic mining layer, three renderings | Accepted | Partial | 2026-07-15 |
| [0049](0049-apply-auto-is-the-authoring-default.md) | `Apply: auto` is the authoring default; `manual` requires a stated reason | Accepted | Built | 2026-07-15 |
| [0050](0050-headless-background-adoption-runner.md) | Headless background adoption runner — the agent path for genuinely-manual briefs | Accepted | Built | 2026-07-15 |
| [0051](0051-multi-session-concurrency-retire-the-master-session.md) | Multi-session concurrency — retire the master session | Accepted | Partial | 2026-07-17 |
| [0052](0052-canon-budget-and-consolidation-ritual.md) | Canon budget + consolidation ritual — govern the governor | Accepted | Partial | 2026-07-18 |
| [0053](0053-mined-ritual-conformance-and-spot-audit.md) | Mined ritual conformance + spot-audit cadence — replace self-reporting with evidence | Accepted | Built (amended WI-0207) | 2026-07-18 |
| [0054](0054-heartbeat-on-tool-use-and-crash-reap-grace.md) | Heartbeat on tool use + a crash-reap grace — a stale heartbeat is not death evidence | Accepted | Built | 2026-07-18 |
| [0055](0055-lazy-session-start.md) | Lazy session start — mutating start work waits for the first heartbeat | Accepted | Built | 2026-07-18 |
| [0056](0056-session-branch-gated-trunk.md) | Session branch → gated trunk (concurrency Phase 2 / C3) | Accepted | Partial | 2026-07-19 |
| [0057](0057-same-tree-concurrency-attributed-commits.md) | Same-tree concurrency — attributed commits and last-one-out landing | Superseded by [0058](0058-land-per-session-with-an-isolated-gate.md) | Partial | 2026-07-19 |
| [0058](0058-land-per-session-with-an-isolated-gate.md) | Land per session with an isolated gate | Accepted | Built | 2026-07-19 |
| [0059](0059-same-tree-concurrency-fails-safe.md) | Same-tree concurrency fails safe — the app surface catches accidents, it doesn't isolate work | Accepted (amended s88) | Built | 2026-07-21 |
| [0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) | The concurrency substrate is a git lifecycle wrapper; shared-tree is the app-only fallback | Accepted | Built | 2026-07-21 |
| [0061](0061-poga-unified-lifecycle-cli-and-state-manifest.md) | `poga` is the unified spec-driven lifecycle CLI; the per-system state manifest | Accepted | Built | 2026-07-22 |
| [0062](0062-cross-lane-coordination-in-the-git-common-dir.md) | Cross-lane coordination lives in the git common dir; claims/leases carry their own TTL | Accepted | Built | 2026-07-22 |
| [0063](0063-poga-lanes-vs-orchestrated-subagents.md) | `poga` peer-lanes and orchestrated subagents (Workflow / ultracode) are complementary, not competing | Accepted | — | 2026-07-22 |
| [0065](0065-restore-is-persona-free-and-briefs-are-version-gated.md) | Restore is persona-free, and a hydrated brief is gated by a version check | Accepted | Built | 2026-07-23 |
| [0066](0066-the-lane-land-owns-the-checkout-it-strands.md) | A lane land owns the checkout it strands, and the reaper is not gated by surface | Accepted | Built | 2026-07-23 |
| [0067](0067-adopt-in-place-is-the-third-onboarding-path.md) | Adopt-in-place is the third onboarding path | Accepted | Built | 2026-07-23 |
| [0068](0068-retire-the-declared-standard-version.md) | Retire the declared standard-version — detection is the only version signal | Accepted | Built | 2026-07-23 |
| [0069](0069-shared-numbers-are-compiled-or-drawn-never-declared-or-picked.md) | Shared numbers are compiled or drawn, never declared or picked | Accepted | Built | 2026-07-24 |
| [0070](0070-worktree-lanes-are-fleet-substrate.md) | Worktree lanes are fleet substrate, not a federation privilege | Accepted | Built | 2026-07-26 |
| [0073](0073-work-item-store.md) | A canonical work-item store, drawn WI-NNNN ids, one file per item | Accepted | Built | 2026-07-27 |
| [0074](0074-inbox-is-a-mailbox-triage-mints-work-items.md) | The inbox is a mailbox, not a backlog — triage mints work items | Accepted | Built | 2026-07-28 |
| [0075](0075-dispatch-is-a-launcher-and-the-wave-trigger-is-the-landing-session.md) | Dispatch is a launcher, and the wave trigger is the landing session | Accepted | Built | 2026-07-29 |
| [0076](0076-operational-work-is-its-own-kind.md) | Operational work is its own kind — own numbers, a due date, and a result | Accepted | Built | 2026-07-29 |
| [0077](0077-spawn-surface-is-detected-and-tmux-is-first-class.md) | The spawn surface is detected, and tmux is a first-class one | Accepted | Built | 2026-07-29 |
| [0078](0078-version-sets-are-planned-releases-are-harvested.md) | Version sets are planned; releases are harvested — and `next` is derived from set membership | Accepted | Partial | 2026-07-29 |
| [0080](0080-the-federation-declares-its-project-version-at-6-0-0.md) | The federation's project version is 6.0.0, and a major is a named milestone | Accepted (D7 resolved by [0081](0081-a-major-is-declared-not-derived.md)) | Built | 2026-07-30 |
| [0081](0081-a-major-is-declared-not-derived.md) | A major is declared, not derived — `breaking` splits into size and migration | Accepted | Built | 2026-07-30 |
| [0082](0082-lifecycle-inversion-and-per-session-runtime.md) | Lifecycle inversion — `poga` owns the session lifecycle, and runtime is a per-session choice | Accepted | Not-built | 2026-07-30 |
| [0084](0084-a-major-ships-when-its-promise-is-complete.md) | A major ships when its promise is complete | Accepted | Built | 2026-07-31 |
| [0085](0085-the-bootstrap-intake-is-a-session-launched-from-the-empty-folder.md) | The bootstrap intake is a session, launched from the empty folder | Accepted | Built | 2026-08-02 |
| [0087](0087-a-guard-whose-motivation-was-a-harness-limitation-retires-with-it.md) | A guard whose motivation was a harness limitation retires with it | Accepted | Built | 2026-08-04 |
| [0088](0088-a-mailbox-is-gitignored-inbound-data-fleet-wide.md) | A mailbox is gitignored inbound data, fleet-wide | Accepted (amended s~155) | Built | 2026-08-04 |
| [0089](0089-a-lane-checkpoints-its-own-work-and-the-repo-recovers-it.md) | A lane checkpoints its own work, and the repo that owns it recovers it | Accepted | Built | 2026-08-06 |
| [0090](0090-the-substrate-owns-the-litter-it-creates.md) | The substrate owns the litter it creates, and a reaper reports outcomes | Accepted | Built | 2026-08-07 |
| [0091](0091-the-harness-commits-what-the-harness-writes.md) | The harness commits what the harness writes, and dirty-tree warnings name only authored work | Accepted | Built | 2026-08-13 |
| [0093](0093-the-close-harvests-a-sessions-claims-into-its-journal.md) | The close harvests a session's claims into its journal | Accepted | Built | 2026-08-14 |
| [0094](0094-a-project-declares-its-data-the-backup-layer-owns-retrieval.md) | A project declares its data; the backup layer owns retrieval | Accepted | Partial | 2026-08-16 |
| [0095](0095-shared-data-lives-outside-the-repo-under-a-declared-data-root.md) | Shared data lives outside the repo, under a declared data root | Accepted | Not-built | 2026-08-16 |
| [0096](0096-poga-refuses-a-launch-a-machine-cannot-host.md) | poga refuses a launch the machine provably cannot host, and says how to fix it | Accepted | Built | 2026-08-22 |
| [0097](0097-a-lane-integrates-the-trunk-with-its-remote.md) | A lane integrates the trunk with its remote, instead of asking a human to push | Accepted | Built | 2026-08-22 |
| [0098](0098-a-lane-repairs-main-and-clears-its-own-blocked-rebase.md) | A lane repairs the main checkout under proof, and clears its own blocked rebase under a named policy | Accepted | Built | 2026-08-22 |
| [0099](0099-the-user-is-not-an-execution-surface.md) | The user is not an execution surface — commands belong to the Architect, guidance belongs to the user | Accepted | Partial | 2026-08-22 |
| [0100](0100-dispatch-spawns-headless-the-operator-s-machine-attaches.md) | Dispatch spawns headless; the operator's machine attaches | Accepted | Partial | 2026-08-22 |
| [0101](0101-a-lane-may-block-on-a-question-never-invisibly.md) | A lane may block on a question, never invisibly | Accepted | Partial | 2026-08-22 |
| [0102](0102-integrate-merges-a-diverged-trunk-never-replays-it.md) | `integrate` merges a diverged trunk; it never replays it | Accepted | Built | 2026-08-23 |
| [0103](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md) | Promotion is a tag; the Runner runs a per-repo deploy contract | Accepted | Partial | 2026-08-25 |
| [0104](0104-a-session-closes-on-the-user-s-word-never-the-agent-s-judgment.md) | A session closes on the user's word, never on the agent's judgment | Accepted | Built (D5 superseded) | 2026-08-30 |
| [0105](0105-roadmap-is-a-compiled-trunk-only-view.md) | ROADMAP.md is a compiled, trunk-only view; the outcome paragraph lives in the journal | Accepted | Built | 2026-08-30 |
| [0106](0106-local-promotion-process-and-data-residency-are-declared-separately.md) | Local systems promote devbox → Runner, and process residency is declared separately from data residency | Accepted | Partial | 2026-08-30 |
| [0107](0107-mail-transits-origin-and-every-machine-drains-its-own-share.md) | Mail transits `origin`, and every machine drains the share it can reach | Accepted | Partial | 2026-09-03 |
| [0108](0108-a-read-names-the-tree-and-commit-it-read.md) | A read names the tree and commit it read — read provenance is a fact returned, never a constant assumed | Accepted | Partial | 2026-09-03 |
| [0109](0109-preflight-repairs-what-it-can-and-escalates-only-what-it-tried.md) | preflight repairs what it can, and escalates only what it tried | Accepted | Built | 2026-09-04 |
| [0110](0110-a-delivery-verdict-declares-the-machine-its-evidence-covers.md) | A delivery verdict declares the machine its evidence covers, and residency is declared rather than inferred | Accepted | Built | 2026-09-04 |
| [0111](0111-a-spawn-is-a-receipt-from-the-lane-not-a-launched-process.md) | A spawn is a receipt from the lane, not a launched process | Accepted | Built | 2026-09-04 |
| [0112](0112-a-relayed-approval-cites-a-grant-not-a-peer.md) | A relayed approval cites a grant, not a peer | Accepted | Partial | 2026-09-04 |
| [0113](0113-a-dispatched-lane-closes-on-its-dispatch.md) | A dispatched lane closes on its dispatch, and the exemption follows authorization rather than location | Proposed | Built | 2026-09-04 |
| [0114](0114-the-land-gate-is-serialized-one-at-a-time.md) | The land gate is serialized — one land at a time, the rest queue | Proposed | Built | 2026-09-04 |
| [0115](0115-the-resolvable-conflict-class-is-per-region-not-per-file.md) | the resolvable conflict class is per REGION, not per file; `ROADMAP.md` rejoins it conditionally | Accepted | Built | 2026-09-04 |
| [0116](0116-the-trunk-has-two-writers-and-now-one-door.md) | The trunk has two writers, and now one door — but the door is not the fix | Proposed | Built | 2026-09-05 |
| [0117](0117-a-land-classifies-its-own-diff-before-it-gates.md) | A land classifies its own diff before it gates, and the input set is measured | Proposed | Built | 2026-09-05 |
| [0118](0118-session-py-is-a-package-behind-a-thin-entry-point.md) | `session.py` is a package behind a thin entry point | Accepted | Built | 2026-09-06 |
| [0119](0119-the-gates-three-side-doors-are-shut.md) | The gate's three side doors are shut — a reservation outlives its journal, the gate sees two inputs, and the trunk writes its own views | Proposed | Built | 2026-09-06 |
| [0120](0120-renumbering-an-unlanded-adr-is-an-identifier-correction.md) | Renumbering a not-yet-landed ADR is an identifier correction, not a revision of a decision | Accepted | Built | 2026-09-06 |
| [0122](0122-validation-reuse-and-resumable-completion.md) | Validation reuse and resumable completion | Accepted | Not-built | 2026-09-10 |
| [0123](0123-the-role-doc-changelog-moves-to-a-tracked-sibling.md) | The role-doc CHANGELOG moves to a tracked sibling, and the receipt moves with it | Accepted | Built | 2026-09-10 |
| [0124](0124-the-land-lock-holds-the-merge-not-the-validation.md) | The land lock holds the merge, not the validation — and the derive leaves the land path | Proposed | Built | 2026-09-10 |
| [0125](0125-an-unattended-run-closes-on-its-own-receipt.md) | An unattended run closes on its own receipt, and says that no human confirmed it | Proposed | Built | 2026-09-11 |
| [0126](0126-the-federation-couriers-a-members-mail-and-never-files-their-copy.md) | The federation couriers a member's mail, and never files their copy | Accepted | Built | 2026-09-11 |
| [0127](0127-the-gate-input-measurement-follows-its-own-subprocesses.md) | The gate-input measurement follows its own subprocesses | Proposed | Built | 2026-09-11 |
| [0128](0128-each-gate-command-is-classified-against-its-own-measured-inputs.md) | Each gate command is classified against its own measured inputs | Accepted | Built | 2026-09-11 |
| [0129](0129-a-lane-is-named-by-its-session-and-addressed-by-its-slot.md) | A lane is named by its session and addressed by its slot — the two are never the same string | Accepted | Built | 2026-09-12 |
| [0130](0130-a-start-time-ordinal-is-provisional-on-every-surface.md) | A start-time ordinal is provisional on every surface — the trunk is a concurrent writer too | Accepted | Built | 2026-09-12 |
| [0131](0131-a-missing-verb-is-asked-of-origin-before-it-is-refused.md) | A missing verb is asked of origin before it is refused, and the substrate refreshes itself | Accepted | Built | 2026-09-13 |
| [0132](0132-a-deploy-result-is-published-by-the-machine-that-produced-it.md) | A deploy result is published by the machine that produced it | Accepted | Built | 2026-09-13 |
| [0133](0133-evidence-is-separated-from-state-by-an-immovable-anchor.md) | Evidence is separated from state by an immovable anchor and a floor at the write | Accepted | Built | 2026-09-13 |
| [0134](0134-a-land-publishes-or-says-it-did-not.md) | A land publishes, or says out loud that it did not | Accepted | Built | 2026-09-13 |
| [0135](0135-a-trunk-clone-is-never-a-process-root.md) | A trunk clone is never a process root, and a sealed machine writes on a channel | Accepted | Built | 2026-09-13 |
| [0136](0136-the-standard-splits-into-an-injected-tier-and-a-reference-tier.md) | The standard splits into an injected tier and a reference tier | Accepted | Built | 2026-09-13 |
| [0137](0137-the-canon-budget-is-a-land-gate-not-a-report.md) | The canon budget is a land gate, not a report | Accepted | Built | 2026-09-13 |
| [0139](0139-a-closed-work-item-stays-closed-and-a-residue-is-minted-fresh.md) | A closed work item stays closed, and a residue is minted fresh with a resolvable citation | Accepted | — | 2026-09-17 |
| [0140](0140-the-integrate-validates-outside-the-land-gate.md) | The integrate validates outside the land gate, and the escalation leaves the section | Accepted | Built | 2026-09-17 |
| [0141](0141-diagnosis-is-a-read-only-collector-that-publishes-on-a-state-change.md) | Diagnosis is a read-only collector, and it publishes on a state change rather than answering a request | Accepted | Built | 2026-09-17 |
| [0142](0142-a-release-candidate-is-a-tag-you-can-canary-but-never-deploy.md) | A release candidate is a tag you can canary but never deploy | Accepted | Built | 2026-09-17 |
| [0143](0143-a-lane-takes-one-item-and-closes-and-the-fleet-s-width-is-measured.md) | A lane takes one item and closes itself, and the fleet's width is measured rather than fixed | Accepted | Built | 2026-09-17 |
| [0144](0144-canon-reaches-a-hookless-runtime-by-delivery.md) | Canon reaches a hookless runtime by delivery, not by a pointer | Accepted | Built | 2026-09-18 |
| [0145](0145-a-dispatch-brief-rides-the-orientation-not-the-argv.md) | A dispatch brief rides the orientation, not the argv | Accepted | Built | 2026-09-18 |
| [0146](0146-rehearsal-and-host-evidence-are-separate-and-neither-substitutes.md) | Rehearsal evidence and host evidence are produced separately, and neither substitutes for the other | Proposed | Built | 2026-09-18 |
| [0147](0147-a-closed-session-ends-its-own-runtime-and-publishes-what-it-leaves.md) | A closed session ends its own runtime, and publishes what it leaves behind | Accepted | Built | 2026-09-19 |
| [0148](0148-a-land-is-a-merge.md) | A land is a merge — validation leaves the land path | Proposed | Built | 2026-09-25 |
| [0150](0150-the-harness-gets-real-module-boundaries-behind-explicit-context-and-narrow-ports.md) | The harness gets real module boundaries behind explicit context and narrow ports | Proposed | Not-built | 2026-10-02 |

## Unused numbers

Five numbers have been drawn and never spent. None of them is a lost ADR. Seven more (0040, 0045, 0064, 0071, 0079, 0092, 0149) are spent, but their records are withheld from this copy; they are listed here so the allocator and the index check treat them as taken.

| # | Drawn | Why it is unused |
|---|---|---|
| 0072 | not recorded | **Cause not recorded.** No ADR-0072 has ever been on the trunk, and no file, ADR, journal or work item in this repo mentions one. Reconstructed 2026-09-11 from the directory alone; stated as unknown rather than guessed. |
| 0083 | 2026-07-31 | Drawn from the **main checkout** while the work was in a lane, so the allocator recorded the holder as `main` and the land gate correctly refused the lane's own ADR. Left to expire rather than hand-edited out of the coordination store; the ADR was renumbered to 0084. Underlying defect: WI-0061. |
| 0086 | 2026-08-04 | The same cause as 0083, second instance — caught before the land this time by reading the gate. Redrawn from the lane as 0087. Second data point for WI-0061. |
| 0138 | 2026-09-13 | **Spent to learn that `adr-next` allocates rather than reports.** The 0121 case again, and for the same reason it happened then: the verb prints `next after NNNN` — the wording of a report — and the only way to find out it also RESERVES is to call it twice and watch the number move. This lane needed two numbers (0136, 0137), called it a third time confirming the behaviour, and 0138 was drawn. Recorded here rather than in a journal, which is the whole lesson of the 0121 row below (WI-0339). **RE-ISSUED 2026-09-17 (session ~336) — and this row is what caught it.** `adr-next` handed 0138 out a second time, to a different session, four days later: the first reservation had expired out of the coordination store, and the allocator's floor is a scan of `adr/`, where a burned number leaves a hole indistinguishable from an unused one. The lane held `adr-alloc 0138` and would have written ADR-0138 over a number this table declares permanently unused, which is the reuse [ADR-0069](0069-shared-numbers-are-compiled-or-drawn-never-declared-or-picked.md) forbids; it drew 0139 instead and left 0138 to expire again. **The hole is therefore self-refilling** — every declared-unused number returns to the front of the queue once its reservation lapses, so this will recur for 0072, 0083, 0086, 0121 and 0138 until the allocator's floor reads this table as well as the directory. Recorded, not fixed at the time: the allocator is fleet-pushed code. **FIXED 2026-09-17 (WI-0379).** The allocator's floor now reads this table as well as the directory and steps past every number in it, saying on stderr which row stopped it; the land gate refuses a burned number filed by hand, which the allocator fix alone could not reach. Proven against this very row: the tree as it stood on 2026-09-17 hands out 0138 with the table unread and 0139 with it read. |
| 0121 | 2026-09-07 | **Spent to learn that drawing works.** `adr-next` reserved 0120; a second call made to confirm the mechanism returned 0121, which was never used and expired out of the coordination store. Recorded at the time in `sessions/journal/20260907-c4ce.md` — *"worth knowing before someone reads the gap as a lost ADR"* — and on 2026-09-10 a session read the gap as a lost ADR anyway, because the note was filed where it was learned rather than where it would be read (WI-0339). |
| 0040 | withheld | **Withheld.** Spent on an ADR that is not published in this copy. |
| 0045 | withheld | **Withheld.** Spent on an ADR that is not published in this copy. |
| 0064 | withheld | **Withheld.** Spent on an ADR that is not published in this copy. |
| 0071 | withheld | **Withheld.** Spent on an ADR that is not published in this copy. |
| 0079 | withheld | **Withheld.** Spent on an ADR that is not published in this copy. |
| 0092 | withheld | **Withheld.** Spent on an ADR that is not published in this copy. |
| 0149 | withheld | **Withheld.** Spent on an ADR that is not published in this copy. |

The two long-form accounts below are the original records for 0083 and 0086; the table is the list a check reads.

> **0086 is deliberately unused, for the identical reason as 0083 — the second instance.**
> Drawn on 2026-08-04 by running `adr-next` from the **main checkout** while the work was in
> lane `worktree-poga-4`. The record was keyed by the Claude session id with `branch: main`,
> and the land gate compares on `_coord_identity()` — the *lane branch* — so both the
> `session_id` and `branch` arms mismatched and the gate would have refused the lane's own
> ADR (`sessionlib/land.py:850`). Caught **before** land this time, by reading the gate rather than
> hitting it. Redrawn from the lane as **0087**, which the coord record correctly attributes
> to `worktree-poga-4`. There is no sanctioned release verb for an `adr-alloc` hold — `release`
> and `unlease` cover work-item claims and leases only — so 0086 was left to expire rather
> than hand-edited out of the coordination store, matching the 0083 precedent. Two burned
> numbers from one cause: **WI-0061** already records the fix (coordination records name the
> *checkout*, not the session that holds them) and this is its second data point.
>
> **0083 is deliberately unused.** It was drawn on 2026-07-31 by running `adr-next` in the
> **main checkout** while the work was happening in a worktree lane, so the allocator
> recorded the holder as `main`. The land gate then correctly refused the lane's ADR for
> claiming a number reserved by someone else — the [ADR-0069](0069-shared-numbers-are-compiled-or-drawn-never-declared-or-picked.md)
> guard doing exactly its job. The number was left to expire rather than hand-edited out of
> the coordination store, and the ADR was renumbered to the 0084 the lane itself drew. The
> underlying defect — coordination records naming the *checkout* rather than the session
> that actually holds them — is WI-0061.

## Pending ADRs (likely future decisions)

Open questions tracked in [federation-arch.md §13](../federation-arch.md#13-open-questions) will likely produce ADRs as they get resolved:

- Repo access / file-shuttling mechanic
- Federation cadence
- Conflict-resolution policy (initial answer in PROCESS.md; may formalize)
- Master principles doc versioning policy
- Whether feedback-memory files are in scope for federation
- Standard Auditor briefing template (deferred until first commissioned engagement)
- "Architect role doc corruption" failure mode characterization (cf. ADR-0005)
- ~~Restore is persona-free — the operator-gated `activate` step~~ **DISCHARGED** as [ADR-0065](0065-restore-is-persona-free-and-briefs-are-version-gated.md) (session 90, when `poga restore` was built)
- **Signed briefs.** [ADR-0065](0065-restore-is-persona-free-and-briefs-are-version-gated.md) gates a hydrated brief with a *consistency* check (`expected-base-version` vs the git-verified role doc), which catches staleness but not tampering. A signature would make it an *authenticity* check and close that residual. Not owed; recorded so the option isn't lost.
