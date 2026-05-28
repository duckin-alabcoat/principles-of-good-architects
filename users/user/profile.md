# User profile — `user`

Base profile for user `user`. Per [ADR-0008](../../adr/0008-three-bucket-taxonomy.md) and [ADR-0009](../../adr/0009-data-storage-mechanics.md). Read at session start by every Architect bound to this user. Per-Architect overrides live in each Architect's role doc under `## User overrides`.

> **Note (public version).** In the private working repo this file holds the user's actual collaboration preferences — it is **data**, gitignored, and never committed. For this public version it is an **empty stub**: the format below plus one illustrative example entry, with no real preferences. When you adopt this framework, this becomes your own preference profile.

This file is **preference-scoped** — how the user prefers to be collaborated with. User attributes (background, age, current learning focus, etc.) are out of scope.

**Entry format.** Slug as header, then `Added` / `Last edited` / `Citation` metadata, then content. The slug is stable; wording may be edited. Override references in Architect role docs cite the slug.

Change history is in [`profile-history.md`](profile-history.md) (append-only) — omitted from this public version (data).

---

## (example) Communication style

### (example) response-length

- **Added:** YYYY-MM-DD
- **Last edited:** YYYY-MM-DD
- **Citation:** (where the preference was stated or confirmed — e.g., a dated session)

Illustrative entry showing the preference format — it carries no real preference. A real entry records one durable collaboration preference (tone, verbosity, units, naming, how terminal instructions should be formatted, …) with provenance back to where the user stated it. Replace this with your own preferences when you adopt the framework.
