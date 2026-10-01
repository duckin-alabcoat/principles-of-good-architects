# Federation mail acceptance evidence

> **Advanced (optional).** This belongs to the multi-machine case. The core does not use it.

`curate/mailacceptance.py` supports WI-0366 with read-only observations and a missing-evidence report. It cannot promote a release, install or retire services, send a probe, accept the work item, or start OPS-0010. Live promotion and designated messages still follow their existing authorization and deployment paths. The collector belongs in that deployment/return-channel workflow; it is not a request for somebody to operate Runner by hand.

The collector reads explicit production roots, local Git identity, actual `launchctl print` results, the configured `poga` symlink, local worker health, the existing member resolver, and specified probe-message lifecycles. It neither fetches nor contacts another host. Its output includes only the loaded working directory, arguments and production-config environment entry, plus a digest of the complete launchctl result. Unrelated environment values are omitted. Python import-cache writes are disabled.

By default an observation is labeled `fixture`. The explicit `--live-observation` option records a local operational observation with the machine's actual hostname. That label alone does not authenticate a remote artifact. After transport, the receiving workflow must attach a matching `return_channel` record from independently verified delivery evidence. The checker validates the snapshot hash and expected source ref; a reviewer must still authenticate the reference against the real receipt and approval records.

The checker returns `blocked` when anything is missing or inconsistent and `ready-for-review` when the supplied artifacts satisfy its structural checks. It always returns `accepted: false` and `ops_observation_started_at: null`. Fixture success, an installer log, and a merely detached checkout cannot satisfy live acceptance.

## Invocation by the deployment workflow

```text
python3 curate/mailacceptance.py snapshot --config <external-config.json> --manifest <manifest.json> --host <declared-host-id> --live-observation
python3 curate/mailacceptance.py check --manifest <manifest.json> --observation <host1-release1.json> --observation <host2-release1.json> --observation <host1-release2.json> --observation <host2-release2.json> --rehearsal <rehearsal.json> --operational <operational.json>
```

All output goes to stdout. Snapshot output is deterministic JSON serialization (sorted keys, compact separators, terminating newline); its exact bytes are the message payload whose SHA-256 the return receipt must name. Attaching `return_channel` later does not change the canonical bytes of the original observation used for this check. Exit status 1 means evidence is incomplete, and 2 means an input/probe configuration could not be read. A read-only snapshot may report unknown worker health without claiming it ran successfully.

## Manifest schema version 1

The external manifest contains these fields. It should be prepared as part of the approved release plan; repository defaults do not invent hosts, release approvals, recipients, or probe traffic.

| Field | Required contents |
| --- | --- |
| `schema_version` | Integer `1` |
| `hosts` | Exactly two host IDs mapped to the host records below |
| `releases` | Exactly two different approved releases, in migration/second-release order |
| `probes` | Explicit unique probe identities for each sender and release |
| `max_observation_age_seconds` | Optional second-release freshness limit, default 1800; historical first-release observations remain historical evidence |

Each host record names `hostname`, absolute `code_root`, `old_code_root`, `config_path` and `poga_path`, its `owned_ref`, and a `services` list of actual `user/<uid>/<label>` or `gui/<uid>/<label>` targets. Each host must declare one `com.federation.mail-poller` target. Across the two hosts, the manifest must also cover `com.federation.deploy-sweep` and `com.federation.adopt-runner`. This does not require unrelated fleet members or every host to run all three jobs.

Each release record contains `tag`, full 40-character `commit`, timezone-qualified `approved_at`, and an `approval_evidence` reference. A referenced approval must still be checked against the actual approval record; writing a string does not grant it.

Each probe record contains `message_id`, `sender` and `recipient` host IDs, the actual mailbox `destination`, and its release `commit` under the `release` key. The collector only reads these messages; it never creates them. Sender lifecycle evidence must say delivered and contain an acknowledgment matching identity, content hash, destination, sender ref and the expected receiver-owned acknowledgment ref.

Each returned snapshot is annotated with `return_channel`: `source_ref`, full `source_commit`, `payload_sha256` and `receipt_evidence`. The source ref must match its host declaration, and the payload digest must match the original canonical snapshot bytes. Retain the received bytes and actual receiver receipt referenced here for review.

### Taking the first release's observation before the second release exists

The manifest names **two** releases and `snapshot` validates it before it probes anything, so at the moment release 1 is running there is no manifest that describes only what has happened. The order below is the one that works. It was measured against the checker, not reasoned from the schema.

1. Author the manifest with release 1's real tag and commit, and a **placeholder** 40-hex commit for release 2. Validation accepts it — nothing in the schema can distinguish a placeholder from a release, which is why this step is written down rather than discovered.
2. Take each host's observation for release 1 while release 1 is what is running.
3. When release 2 is approved and deployed, replace the placeholder with its real tag and commit and take the second pair of observations.
4. Run `check` with the finalized manifest. The release-1 observations still match: an observation's `payload_sha256` covers its own bytes, never the manifest, so amending the manifest afterwards does not invalidate a reading already taken.

A placeholder left unreplaced fails closed — `check` reports `need exactly one host observation` for that release and returns `blocked`. It names a missing observation rather than a placeholder, so if that gap appears for a release you believe was observed, suspect the manifest first.

Two limits of the schema, stated because neither is enforced: `approved_at` is only checked for ordering and is never compared to the present, so a future-dated approval validates; and a release's `approval_evidence` is a string this program cannot authenticate. Both are the reviewer's to check against the real approval record.

## Rehearsal and operational artifacts

The rehearsal record contains `command`, `artifact_sha256` naming the retained test output, and a `checks` map. Each of these must have value `passed`: `migration`, `second_release`, `both_directions`, `concurrent_producers`, `lost_publication_confirmation`, `worker_restart`, `duplicate_delivery`, `rollback`, `code_unchanged`, `old_checkout_unchanged`. These are local test results; they cannot fill any missing live-host slot.

**Produce that record, never write it** ([ADR-0146](../adr/0146-rehearsal-and-host-evidence-are-separate-and-neither-substitutes.md)):

```text
python3 curate/mailrehearsal.py run [--out <transcript path>]
```

It runs the tests that already exercise each check — one interpreter per test, because these modules mutate `os.environ` in `setUp` — writes their combined transcript to `--out` (default `.session-state/wi-0366-rehearsal.log`), and prints the record with `artifact_sha256` taken from the bytes it wrote. Exit 0 when every check passed, 1 when any did not, 2 when the rehearsal could not be described honestly. `python3 curate/mailrehearsal.py checks` prints the mapping without running anything.

A check is never passed by default. An unresolvable test id, a run reporting a number of tests other than one, and a non-zero exit are three different failures and each is reported as itself in `gaps`. The mapping's key set is read from `REHEARSAL_CHECKS` here, so a check added to that tuple without a mapping is a refusal rather than a question nobody asks.

Each operational entry contains `kind: live-operations`, `status: observed`, a concrete `evidence` reference, the retained artifact's `artifact_sha256`, and the following `value`:

| Entry | Required value |
| --- | --- |
| `two_unattended_releases` | Ordered list of the two release commit hashes |
| `no_runner_hand_operation` | Integer 0, supported by coverage of the complete migration/release interval |
| `no_production_main_writes` | Integer 0 |
| `old_checkout_preserved` | Boolean true |
| `no_pending_work_lost` | Boolean true |
| `rollback_verified` | Boolean true |
| `legacy_schedules_retired` | Boolean true |
| `legacy_dependencies_inspected` | Empty list of remaining dependencies after launchd/cron/poga inspection |

Missing telemetry or an absent incident entry cannot produce these records. Preserve time coverage, sample counts, before/after inventories, rollback results and remaining gaps in the referenced artifacts. The checker does not infer this operational history from a single snapshot, inspect or mutate all system schedules, or independently authenticate supplied artifact references.

`legacy_dependencies_inspected` has a producer, and it is the only enumeration in this repository of jobs nobody named in advance:

```text
python3 deploy/legacydeps.py --old-root <the retiring checkout> [--release-root <path>] [--poga <path>] --json
```

Read-only by construction — it never retires, disables or deletes anything. It reads every `*.plist` in the LaunchAgents directory, `launchctl list` (then `launchctl print` for each `com.federation.*` label), `crontab -l`, and the `poga` link, and reports each source separately as `clean`, `remaining` or a gap. Exit 0 means every source was read and nothing remains; 1 means a remaining dependency; **2 means a source could not be read**, and that case never presents an empty `remaining` as a clean verdict. Raw `launchctl print` output is never stored — only the named fields and a digest of the observation.

**Run it on the host the retirement is about.** The verdict is a statement about the machine that produced it: on a development machine `~/.local/bin/poga` resolving into the working checkout is correct, and it is not evidence about the Runner. The `--old-root` that answers this item is the Runner's retiring process root.

After a reviewer verifies both running hosts, both approved releases, both mail directions, operational coverage and the retirement evidence, WI-0366's authorized workflow can record acceptance and activate OPS-0010 from that real UTC timestamp. A code commit, fixture pass or `ready-for-review` report does not start the seven-day observation.
