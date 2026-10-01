# Portfolio registry

The roster of member systems in this federation: each system, its orchestrator agent (if it has one), and its Architect. Naming convention: see [ADR-0006](adr/0006-naming-convention-corrected.md).

This is a template. Replace the example rows with your own members.

Last updated: YYYY-MM-DD

| System | Orchestrator agent | Canonical Architect | System ID | Architect ID | Resides | Status |
|---|---|---|---|---|---|---|
| federation | (none — internal-only system) | Federation Architect | `federation` | `federation-arch` | — | Active |
| example app | (none) | Example App Architect | `example-app` | `example-app-arch` | — | Active (bootstrap) |
| example service | Example Agent | Example Service Architect | `example-service` | `example-service-arch` | — | WIP |

## Columns

- **System ID** and **Architect ID** are read by code. A row counts as a member only when the Architect ID is exactly the System ID plus `-arch`. Keep that pair adjacent, in backticks, in the fourth and fifth columns.
- **Orchestrator agent** is optional. Write `(none)` when a system has no named agent.
- **Resides** is the data-residency declaration: the `machine_map` label from `session.config.json` of the one machine that holds this member's repo, or `—` when undeclared. Declare it only for a member that lives on exactly one machine. A tool may excuse a member's absence only when this cell says it lives elsewhere; `—` means the absence is reported.
- **Status**: Active = exists and is maintained. WIP = under construction. Dormant = parked on purpose. Re-establishing = its Architect role doc is being rebuilt.

## How to update

A new system is registered by `poga bootstrap`, which files a registration request into the Federation Architect's inbox rather than editing this file directly. Edit a row by hand when a system's status changes, an orchestrator agent is named or removed, or a system is retired.
