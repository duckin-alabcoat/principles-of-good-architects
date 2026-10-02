# User profile — `operator`

Base profile for user `operator`. Per [ADR-0008](../../adr/0008-three-bucket-taxonomy.md) and [ADR-0009](../../adr/0009-data-storage-mechanics.md). Read at session start by every Architect bound to this user. Per-Architect overrides live in each Architect's role doc under `## User overrides`.

This file is **preference-scoped**: how the operator prefers to be collaborated with. Personal attributes (background, history, anything identifying) are out of scope.

In a working repository this file is data, gitignored per [ADR-0007](../../adr/0007-github-as-system-storage-data-excluded.md). A new adopter receives this blank copy.

**Entry format.** Slug as header, then `Added` / `Last edited` / `Citation` metadata, then content. The slug is stable; the wording may be edited. Override references in Architect role docs cite the slug.

Change history goes in `profile-history.md` beside this file (append-only).

---

## Communication style

_No entries yet._

## Naming

_No entries yet._

## Data and lists

_No entries yet._

## Terminal and coding interaction

_No entries yet._

## Escalation

_No entries yet._

---

<!-- An entry, when one is added:

### some-stable-slug

- **Added:** YYYY-MM-DD
- **Last edited:** YYYY-MM-DD
- **Citation:** where the preference was stated -- a session, a quote, a ruling.

The preference, stated so an Architect can act on it without asking.

-->
