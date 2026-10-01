# Federation production state (WI-0361)

> **Advanced (optional).** This belongs to the deploy runner. The core does not use it.

Production is selected only by `POGA_FEDERATION_CONFIG`, naming an external JSON file.
`curate/production.py` validates schema version 1 or 2 and separates these roots:

- `code_root`: absolute address of the running release, unchanged across tag updates.
- `state_root`: persistent service state; default `$HOME/.local/state/poga/federation`.
- `config_root`: machine-local configuration; default `<state_root>/config`.
- `transport_root`: a dedicated repository address, separate from state and config.
- `inbox_root`: optional, existing authoritative federation pending mailbox. Omit on a
  host that does not own it. It may be the existing gitignored DevBox inbox, but cannot
  be inside the release tree. Resolving configuration never creates another inbox.
- `transport`: remote, owned ref, input refs and optional recipient-to-acknowledgment-ref
  mapping (`recipient_refs`). Only explicit `refs/heads/...` names are accepted.

### The transport's refs are not the channel's branch (WI-0418)

`deploy/migrate.py` derives `owned_ref: refs/heads/runner/messages` and
`input_refs: [refs/heads/dev/messages]`. Deriving `runner/mail` and `dev/mail` instead
would be a defect rather than a naming preference: `refs/heads/runner/mail`
is the ADR-0135 channel branch, which `curate/channel.py` publishes a **full working tree**
to. `mailtransport._validate_tree` refuses an owned ref holding anything but
`messages/<sha256>.json` at mode 100644, so the transport would refuse its own ref on every
cycle — the queue growing with nothing published, reporting
`ack_publication: owned ref contains an unauthorized path or mode`. One ref name, two
writers, two shapes.

`curate/channel.py` keeps `runner/mail`; the transport moved. The two must never share a
name again, and `migrate.CHANNEL_REF` is derived from `channel.BRANCH` rather than spelled
a second time so that renaming the channel carries the collision check with it.

**Schema version 2 marks a document that has been examined for that collision.** Nothing a
reader parses differs between versions 1 and 2, so both resolve — refusing version 1 would
make production dormant on every host between a release landing and that host's own
migration running. `author_config` upgrades a configuration whose refs name the channel
branch (both refs move together, every other key preserved) and leaves every other authored
configuration untouched, including one with a host's own ref names.

The transport repository's ownership marker (`poga-mail-transport.json`) binds the owned
ref, so the same migration calls `mailtransport.repoint_ownership` under the transport lock
to move a marker naming the channel branch. That repoint is narrow on purpose — same
remote, marker naming exactly the ref being left — and every other mismatch still reaches
`ensure_repository`'s "transport ownership differs from configuration", which is the
refusal that means another owner.

Provisioning must create the state/config directories and supply `mailboxes.json`,
`repo-paths.local`, and `reconcile-roots.local`. Missing declarations and unsafe paths
raise a configuration fault; there is no fallback to code or an old transport clone.
State and configuration cannot live inside any Git checkout. Resolving uses no network
and makes no filesystem changes. Production stays dormant until migration supplies the
configuration and the dedicated worker. Detached HEAD alone selects nothing.

## Mutable state inventory

| Surface | Production address / ownership |
| --- | --- |
| Deploy receipts and escalation mail | Immutable `<state_root>/queue`; runner enqueues only |
| Generated/staged outbox messages | Same queue, through `outbox.post/stage` |
| Queued/published/delivered evidence | `<state_root>/message-state`; transport/acknowledgment worker |
| Queue producer locks | `<state_root>/queue-locks`; per-identity `flock` |
| Adoption attempt counts and quarantine | `<state_root>/runtime/adopt-runner.briefs.json`; durable, not disposable logs |
| Adoption status and audit records | `<state_root>/runtime/adopt-runner.status` and `.jsonl` |
| Adoption stale/dead-letter condition snapshots | `<state_root>/comms`; changed conditions and recoveries also enqueue mail |
| Member adoption failure notices | Same external comms and queue; development retains prior member behavior |
| Poller last-run record and lock | `<state_root>/runtime/mail-poller.status.json` and `.lock` |
| Self-cutover finisher log | `<state_root>/runtime/self-cutover-<system>.log` |
| Member location files and mailbox registry | `<config_root>`; shared reconcile/adoption/delivery readers |
| Deploy seed lookup | `<config_root>/seed-paths.local.json` |
| Federation inbox | Existing declared authority, independent of service state; absent on non-owning hosts |
| Deploy ledger and state vault | Existing external `~/.local/state/poga/deploy`, honoring `POGA_DEPLOY_LEDGER_DIR`; unchanged |
| Release installation files / seed conversion | Deployment/migration owns these, not background message producers |
| Launchd stdout/stderr and Python bytecode cache | Scheduler must bind these to external runtime state during WI-0364/0365 |
| Member session sidecars and recipient decisions | Remain with those members; not federation service state to relocate |

The metrics reader follows the same adoption status resolver. Existing `.session-state`
files require classification during migration: attempt ledgers and quarantine records
must not be discarded as if they were expendable logs.

## Local queue contract

`mailqueue.enqueue(roots, destination, filename, text, message_id=None, provenance=None)`
returns the payload path after scrub, durable file writes, directory fsync, and atomic
publication of an envelope/payload directory. The default identity is a UUID; producers
with an existing event identity should pass it. Repeating an identity with changed
content, routing or provenance refuses. Concurrent producers cannot overwrite messages.

Version 1 envelopes contain `message_id`, `destination`, `filename`, `sha256`,
`provenance`, and `created_at`. `Envelope.as_dict()` adds `schema_version`, omitting only
the local `payload_path`. Directory identity is `sha256(message_id)`. Original payload
bytes and envelope remain in the queue after delivery; lifecycle evidence is separate.

`pending(roots)` returns unacknowledged `Envelope` objects, including those already
published. Corruption raises; a worker can supply `on_error(path, error)` to report bad
entries while processing healthy ones. An unreadable queue directory always raises.
`lifecycle(roots, message_id)` distinguishes queued, published and delivered.
`mark(roots, message_id, state, **evidence)` persists monotonic lifecycle transitions under
an identity lock. The transport owns publication evidence; matching, verified recipient
acknowledgments are required before its caller marks delivery.

Production `outbox.publish` does no Git work and returns false, meaning publication has
not occurred. Deploy receipts report `queued`, `published`, and `delivered` separately.
Transport failure therefore cannot change a completed deployment into a failed one.
