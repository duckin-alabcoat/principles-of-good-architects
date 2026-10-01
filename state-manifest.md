# Per-system state & credentials manifests

The two artifacts [ADR-0061](adr/0061-poga-unified-lifecycle-cli-and-state-manifest.md) D3 mandates. Together they are the answer key `poga restore --plan` renders and the quarterly restore drill scores against — the artifact whose absence made drill #1's RPO *total data loss*.

`restore = bootstrap + data + credentials` (D2). `bootstrap` is automated; **data** is what the state manifest addresses; **credentials** are what the credentials manifest addresses. Neither can be replaced by the CLI knowing more — the state manifest declares *what* the system's non-derivable data is and *how to know it came back correct* (it does not back anything up, and since [ADR-0094](adr/0094-a-project-declares-its-data-the-backup-layer-owns-retrieval.md) it does not say where backups live either — that belongs to the backup layer), and credentials by design travel through neither git nor backup media.

## The files

| File | Schema | What it declares |
|---|---|---|
| `state-manifest.<system>.json` | [`state-manifest.schema.json`](state-manifest.schema.json) | The system's **non-derivable data**: which paths, where they sit, and how to verify them. No backup medium. |
| `credentials-manifest.<system>.json` | [`credentials-manifest.schema.json`](credentials-manifest.schema.json) | Each **secret's transport and re-provision procedure** — never its value. |

Both are tracked in the federation repo (they name locations and procedures — no secrets, no user data), one pair per onboarded system. The federation's own pair — `state-manifest.federation.json` and `credentials-manifest.federation.json` — is the reference instance (withheld from the public copy, which ships the schemas only).

## State manifest

Schema **2.0.0** ([ADR-0094](adr/0094-a-project-declares-its-data-the-backup-layer-owns-retrieval.md)). A project declares only what nobody else can supply, and it declares it **once**:

```json
"data": {
  "dest": "${FEDERATION_ROOT}",
  "paths": [
    {"path": "users/", "contains": ["the operator/profile.md"]},
    {"path": "architect-learnings.md"}
  ]
}
```

- **Which files are non-derivable**: not in git and not regenerable. That is a judgment about the project's own contents. The federation leaves out `inputs/` because those files are mirrors it can re-fetch.
- **Where they sit.** `dest` is never a hardcoded home (finding 5). Use a parameter token (`${FEDERATION_ROOT}`, `${SYSTEM_ROOT}`) or an install-relative path, because the same checkout can sit under a different absolute path on each machine. The schema rejects a `/Users/<u>/…` or `/Volumes/<v>/Users/<u>/…` dest, and a `path` that is absolute or contains `..`.
- **How to know it came back correct.** Every path must be present and non-empty, and that rule is never restated per entry. `contains` adds what only the project knows: presence is not integrity.

**What a project must not carry:** the backup medium, volume name, snapshot path syntax, provider, account, bucket, backup set, restore URL, 2FA steps, encryption-key pointers, `failure_domain` or `automation`. All of that belongs to the backup layer, which keeps its own runbook saying where data is kept and how to get it back.

An empty `paths` list is a **positive claim** that the system has no non-derivable state. It is not an omission.

### Migrating a 1.x manifest

1.x grouped `data_sources` by backup medium, repeating each path once per medium. `poga restore` now refuses that shape by name, before it builds anything. To migrate:

1. Take the union of every entry's `paths` and put it in `data.paths`, one object per path.
2. Put the shared `dest` in `data.dest`.
3. Move each retrieval check you had written as prose ("users/ has the the operator profile") into that path's `contains`.
4. Delete `data_sources` and set `version` to `2.0.0`.
5. Do not simply drop retrieval facts. Give them to the backup layer first. Until it confirms it holds them, a dated `//quarantine-<date>-…` block keeps them in the file where no code reads them. The federation's manifest has two.

## Credentials manifest

`credentials` is an **ordered** list (finding 1): the root identity first (nothing else provisions without it), then secrets that gate reading the backups (e.g. the offsite encryption key), then application secrets. Each entry declares:

- **`transport`** — `human-only` (a guided checklist a human performs: device flows, web logins, the root identity, the offsite key) or `machine-injectable` (a fetch-at-use helper serves it; the value never lands in a transcript).
- **`provision`** — ordered steps to *re-establish* the secret. Where a step would name a value it names a **pointer** ([`reference-secrets-dont-transmit`](habits/master.md) / [P14](principles/master.md)).

Every `human-only` entry is a term in restore's `incomplete — N credentials to re-provision` count. That honesty is part of restore's correctness: it **never reports plain success** (D2). The schema forbids a secret value structurally — `additionalProperties: false` means a stray `value` key fails validation.

## Authoring & validating a new system's pair

1. Copy the federation pair as the template; set `system`, `spec` (the `bootstrap-spec.<system>.json` it builds from), and `version`.
2. Fill `data.paths` from the system's actual `.gitignore`d / non-derivable set — the paths that cannot be rebuilt from git — each with a `contains` for anything that makes it *correct* rather than merely present. Re-derivable mirrors (e.g. the federation's `inputs/`) do **not** belong.
3. Fill `credentials` with every secret the running system authenticates with, root-identity first.
4. Validate against the schemas (`jsonschema` draft 2020-12). `tests/test_poga_cli.py` `TestSchema` runs the federation's own pair through a stdlib validator on every suite run.

## The CLI that consumes them

`poga bootstrap` and `poga restore` are **built** (session 90, [`poga_cli.py`](poga_cli.py)). Both verbs run one seven-phase pipeline — resolve → identity → acquire → materialize → hydrate → verify → report — differing only in `materialize` (bootstrap renders the kit; a restored system's files come from git history) and `hydrate` (this manifest). Five of seven phases are exercised by every onboarding, which is the D1 exercise-frequency payoff.

```
poga restore <system> [--plan] [--into <dir>] [--scenario machine-loss|site-loss]
poga bootstrap --spec <spec.json> [--plan] [--into <dir>]
```

What the manifests buy you concretely:

- **`--plan` is the read-only preview.** It reads both manifests and prints what would be verified and what would be owed. It touches nothing.
- **Restore verifies; it never retrieves** ([ADR-0094](adr/0094-a-project-declares-its-data-the-backup-layer-owns-retrieval.md)). The backup layer places the data. Hydrate then checks each declared path at the install root and reports what is missing as owed to the backup layer, and what is present but fails `contains` as blocked. Both outcomes exit `3`. No manifest string is ever executed ([P20](principles/master.md)). `--scenario` is still accepted, but it no longer selects anything.
- **The `spec` field is load-bearing.** Restore reaches the build input *through* the manifest, so the two cannot silently drift apart; a mismatch between the spec's derived system-id and the manifest's `system` aborts before anything is built.
- **Exit codes make the honesty contract testable.** `0` complete · `1` failure · `2` usage · `3` *incomplete — N credentials to re-provision*. Every `human-only` credential is a term in N.
- **Identity is pinned before the build** ([ADR-0061](adr/0061-poga-unified-lifecycle-cli-and-state-manifest.md) D4 / finding 9): the spec carries `repo_url` + `root_commit`, and a mismatch aborts — so a restore can never rebuild from the public sanitized sibling repo.
- **Hydrated data never becomes authority** ([ADR-0065](adr/0065-restore-is-persona-free-and-briefs-are-version-gated.md)): restore **activates nothing** — it lays down files and exits. A brief that came off backup media is gated by the `expected-base-version` check against the **git-verified** role doc, so a stale or already-applied one is refused automatically, with no human hold and no step for you in a rebuild.
- **A bare `poga restore` lists what can be rebuilt — and what cannot.** The second half matters more: a system with no manifest has an RPO of total data loss, and that should be visible now, not during a disaster.
