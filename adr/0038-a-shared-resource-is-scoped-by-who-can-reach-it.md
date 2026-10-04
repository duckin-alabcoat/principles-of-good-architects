# ADR-0038: A shared substrate's exposure is scoped by who can reach it, and enforced on the host that serves it

**Status:** Proposed (public summary)
**Date:** 2026-07-06
**Deciders:** the operator; Federation Architect (drafted); an outside Auditor ([ADR-0004](0004-auditor-as-separate-role-class.md)).

## Context

This ADR states a generic pattern for any POGA deployment that spans machines. In such a
deployment, some machine serves a resource that the others depend on, such as a shared directory, an inbox or a remote shell
([ADR-0026](0026-cross-machine-execution-is-standard-substrate.md),
[ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md)). Other systems depend on
it, so it cannot simply be switched off. Its exposure has to be *scoped* instead.

A surface like this tends to go unowned, because no single member owns it. Owning it is the
federation's job.

## Decision

1. **Scope who can reach it before scoping what it holds.** Limiting reachability to
   authenticated peers usually breaks nothing. Per-consumer least-privilege shares are the
   better end state, but they force path migrations, so they are planned work and not the
   first move.
2. **Enforce on the serving host.** On a flat network, traffic between neighbours on the
   same segment never crosses a gateway, so a gateway rule cannot protect a host from its neighbours.
   The host's own filter is the control that can.
3. **Write the rule by destination, not by interface.** A rule tied to an interface name
   silently misses a second interface or a change of cabling.
4. **Scope a shell in the same change as the file share.** It is the same kind of exposure,
   with a bigger prize.
5. **Verify after a reboot.** A filter that does not survive a restart is the most likely
   silent failure, so the check runs again after the machine comes back up.
6. **Fail closed.** If the allowed path changes, the resource becomes unreachable, not
   exposed.
7. **A person makes the change at the machine.** Security changes to a host are never
   carried out remotely by an agent ([P9](../principles/master.md#p9--destructive-ops-confirmed),
   [P4](../principles/master.md#p4--identity-boundaries-non-collapsing)).

## Consequences

- The shared resource now depends on the health of whatever authenticates its peers. That
  is the one availability trade-off, and the post-reboot probe also serves as the drift check.
- Least-privilege shares stay open as later work. This ADR is the who-can-reach layer, not
  the what-is-shared layer.
- The principle is offered to members as a candidate threat-model entry.

## References

- Habits: [`capture-the-probe`](../habits/master.md#capture-the-probe), [P18](../principles/master.md#p18--verify-everything).
