# Portfolio registry

Live mapping of the user's systems, their orchestrator agents (where named), and their Architect roles. Naming convention: see [ADR-0006](adr/0006-naming-convention-corrected.md).

> **Note (public version).** The systems below are **illustrative examples** — a few hobby projects to show the registry format and the naming convention in action. Replace them with your own systems when you adopt this framework. Nothing in the architecture depends on these specific entries.

Last updated: 2026-05-27

| System | Orchestrator agent | Canonical Architect | System ID | Architect ID | Status |
|---|---|---|---|---|---|
| recipe box | Basil | Recipe Box Architect | `recipe-box` | `recipe-box-arch` | Active |
| home-hub | Hearth | Home Hub Architect | `home-hub` | `home-hub-arch` | Active |
| trail-log | Cairn (Chief Trail Agent) | Trail Log Architect | `trail-log` | `trail-log-arch` | Active |
| campaign-tracker | (none — WIP, no named agent yet) | Campaign Tracker Architect | `campaign-tracker` | `campaign-tracker-arch` | WIP |
| arcade | (none — no orchestrator agent) | Arcade Architect | `arcade` | `arcade-arch` | Re-establishing (see [ADR-0005](adr/0005-tinkerer-split.md)) |
| federation | (none — internal-only system) | Federation Architect | `federation` | `federation-arch` | Active (bootstrap) |
| plant care | (none) | Plant Care Architect | `plant-care` | `plant-care-arch` | Active (bootstrap) |

## Notes

- **Orchestrator agent** is *optional*. Not every system has one. Campaign Tracker has no named agent yet; Arcade has none by design.
- **Status meanings**: Active = system exists and is being maintained. WIP = under construction. Re-establishing = Architect role doc being rebuilt (see referenced ADR).
- This registry is **system**, committed to GitHub. The actual role doc contents and producer files are **data**, kept in `inputs/` and excluded by `.gitignore`. See [ADR-0007](adr/0007-github-as-system-storage-data-excluded.md).

## How to update

This file is a live registry, not an immutable ADR. Update it directly when:

- A new system joins the portfolio
- A system's status changes (WIP → Active, etc.)
- An orchestrator agent is named or removed
- A system is retired

Material changes to the *convention* (new slot, new role class) still require an ADR. Updating this table to reflect new portfolio state does not.
