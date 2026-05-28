# ADR-0001: Record federation decisions as ADRs

**Status:** Accepted
**Date:** 2026-05-21
**Deciders:** the user, Federation Architect

## Context

The Federation Architect is bootstrapping. As soon as work begins, decisions accumulate fast — naming conventions, role-class taxonomies, scope rules, cadence, conflict resolution policy, file layouts. Without a durable record of *why* each choice was made, future-the user (and future-Federation-Architect after a session reset) will revisit the same questions and risk relitigating them differently.

The user's existing product Architects already use ADRs (e.g., Basil's ADR-0019 referenced in the founding brief). Industry practice for system design is well-established here: capture each non-trivial decision in a short, immutable record with status, context, decision, alternatives, and consequences.

The Federation Architect should adopt the same practice — both because it's good design hygiene and because, per the recursive principle (see ADR-0003), whatever we ask the product Architects to do, we do too.

## Decision

Every non-trivial decision the Federation Architect makes — alone or with the user — gets an ADR.

**Layout:**
- ADRs live in `adr/` at the root of this project.
- Filenames: `NNNN-kebab-title.md`, 4-digit zero-padded, monotonically increasing.
- One decision per ADR.
- Once an ADR is `Accepted`, it is **not edited in place**. A new decision that overrides it gets its own ADR with `Status: Accepted`; the old one is updated only to flip its status to `Superseded by ADR-XXXX`.

**Template:** See [template.md](template.md). Sections: Status, Date, Deciders, Context, Decision, Alternatives Considered, Consequences, References.

**Index:** [README.md](README.md) in this directory lists every ADR with one-line summary and current status.

**What counts as "non-trivial":** A decision whose reversal would cause rework or confusion later. Naming, taxonomy, structural conventions, scope rules, cadence — yes. Choosing which file to read first in a session — no.

## Alternatives Considered

- **No formal record; rely on memory + conversation transcripts.** Rejected — memory degrades, transcripts aren't indexed, and the cost of relitigating decisions compounds.
- **Lightweight notes (CHANGELOG-style entries) instead of full ADRs.** Rejected — too little context to be useful when revisiting in 6 months. The Alternatives Considered section is the highest-value part and a CHANGELOG entry won't carry it.
- **Track decisions only in the master principles doc.** Rejected — that doc is about *cross-system principles*, not about how the federation system itself works. Different audience, different lifecycle.

## Consequences

- Every structural decision in this conversation already deserves an ADR. Backfill expected for: naming convention (ADR-0002, accepted), recursive principle (ADR-0003, pending), Auditor as separate role class (ADR-0004, pending), Tinkerer split (ADR-0005, pending).
- ADR adoption is itself a candidate cross-system principle to propose to the product Architects via the normal federation channel, *if* not all of them already use it. Track for later.
- Writing ADRs adds friction. That's a feature — it forces the decider to articulate alternatives and consequences before committing.

## References

- [architect-learnings-federation-brief.md](../architect-learnings-federation-brief.md) — references ADR-0019 from Basil, confirming ADRs are established practice in the user's portfolio.
- Michael Nygard's classic ADR format (2011) — the structural ancestor of this template.
