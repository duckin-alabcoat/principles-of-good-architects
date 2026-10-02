---
id: <<SYSTEM_ID>>
phase: bootstrapping
version: 0.1.0
last_active: <<TODAY>>
focus: One line — current focus or what's owed.
blocked: false
---

# <<SYSTEM_ID>> — status

Machine-readable status for this system, per federation [ADR-0021](<<FEDERATION_REPO_REF>>/adr/0021-cross-system-status-surface.md). The frontmatter above is the contract other systems read to list all of <<USER_NAME>>'s projects; the prose is for humans.

Single-writer: this system's Architect, updated at session-end (§11 session-end protocol). `last_active` is the freshness signal — if it's old, treat the rest as stale.

**Schema:** `id` (kebab system id) · `phase` (bootstrapping/active/maintenance/paused/retired) · `version` (role-doc version) · `last_active` (date) · `focus` (one line) · `blocked` (`false` or a reason string).
