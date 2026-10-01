#!/usr/bin/env python3
"""Deliver a receipt-ritual brief to a member's real inbox — and audit the channel.

Two commands, one subject: the delivery channel that was silently losing mail.

    python3 curate/deliver.py <architect-id> <brief-file>   # deliver one brief
    python3 curate/deliver.py --audit                       # sweep BOTH directions
    python3 curate/deliver.py --audit --status              # one line, for SessionStart
    python3 curate/deliver.py --probe [--write]             # re-probe the registry itself

WHY THIS EXISTS. Filing by hand-authored path was the interface, and it lost briefs in
both directions without either side noticing:

  * A reply to one member was written into the FEDERATION's own tree and sat undelivered
    for eight days. Our ROADMAP recorded it as "delivered to their inbox"; their startup
    banner correctly read `inbox: (empty)` every session. Both sides reported success
    (WI-0072).
  * A brief addressed to federation-arch sat in another member's tree for five days, found only
    because the first sweep was re-run in the other direction. The first sweep checked
    only our tree and its result was reported as the answer (WI-0079).

The layout invites the mistake: `proposed-edits/<architect-id>/` exists inside the
federation repo for other Architects, which is structurally indistinguishable from an
outbox, and writing there produces no error and a plausible directory listing.

THE AUDIT THEN MADE THE SAME MISTAKE ITSELF (WI-0101). It inferred the verdict from our
tree alone — presence in our `pending/` printed as "never sent" — so of five briefs it
flagged, four had already arrived and three were in the recipient's `applied/`. It is now
a LOOKUP too: each brief is matched by edit-id inside the recipient's real mailbox, and
the answer is one of three states that are never folded together — DELIVERED (our copy is
a leftover), UNDELIVERED (absent from a mailbox we could read), UNKNOWN (we could not
tell, with the reason). The old shape had no way to say the third thing, so "could not
tell" came out as "definitely not sent", and a member's Architect reasonably relayed that
count to us as fact in a brief of their own — a detector that over-reports propagates
into other Architects' findings.

THEN IT STATED THOSE VERDICTS GLOBALLY WHILE READING ONLY ONE MACHINE (WI-0205, ADR-0110).
ADR-0088 D1 gitignores every member's mailbox, deliberately — so a mailbox's contents exist
on exactly one machine and never travel. A brief one member delivered to another on the
Runner was therefore invisible from devbox, and the audit called it UNDELIVERED: a
confident wrong answer whose obvious next action would have duplicated a brief the
recipient already held. The fix is not to weaken the verdict but to name its evidence.
Where OUR copy travels (git-tracked) and THEIR mailbox does not, absence is not evidence
and the verdict is OFF_MACHINE — WI-0223's axis extended one position, since the answer
exists but no re-run HERE will ever produce it. Where our copy is machine-local too, this
machine is the only possible sender and UNDELIVERED stands. The report says which machine
it speaks for before it says anything else. A member the roster places on ANOTHER machine
also gets its own verdict — ELSEWHERE BY DESIGN — instead of being filed with broken
mailboxes under advice ("map it in repo-paths.local") that could only be followed by
inventing a path.

WHAT MAKES DELIVERY RESOLVABLE AT ALL is `mailboxes.json` (ADR-0088 D5). Before it,
"the recipient's inbox" was not a well-defined instruction — the fleet ran three
conventions, and a survey that ASSUMED the standard layout reported one member (which has a
working mailbox at a non-standard path) as having none. Delivery is a LOOKUP here, never
a guess, and every way it can fail is a distinct named refusal rather than a successful
write to the wrong place.

THE SEND-SIDE GUARD is the `tracked` check. Writing into a mailbox that git tracks does
not merely litter — the recipient's session-end sweep commits it into THEIR history under
THEIR authorship, in a commit they never reviewed (ADR-0088 D1). That is the one
mechanical rule the old advice ("never write inside an observed repo") could not express:
it was too strict, since a gitignored inbox perturbs nothing, and too loose, since it said
nothing about the case that actually causes harm.

Federation-only: the locator map and `mailboxes.json` are federation data, so only the
federation can resolve another member's repo. Members reach US directly (that path works);
this mechanizes the relay back out.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys
import typing

# `curate/` on the path, not the repo root: the sibling curate scripts import each other
# by bare name (`from common import …` inside reconcile), which is the house pattern for
# tools run as `python3 curate/<x>.py`. Importing this as `curate.reconcile` instead makes
# reconcile's own bare imports fail.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from reconcile import (  # noqa: E402
    ROOTS_CONFIG, find_status_candidates, read_repo_paths, read_roots_config,
    resolve_mapped, self_entry,
)
from common import (  # noqa: E402
    declared_elsewhere,
    read_roots_config as read_external_roots,
    read_repo_paths_config,
    roots_diagnosis,
    shared_work_root,
    this_machine,
)
# The queue's own retire, not a second one written here. `outbox.retire` already handles
# the case two briefs of one name arrive at `delivered/` at different times, and a copy of
# that logic in this file is the drift P16 names. Import is one-directional — `outbox`
# knows nothing about delivery — so there is no cycle to unpick.
import outbox  # noqa: E402
import channel  # noqa: E402
import production  # noqa: E402
# WI-0029: layout resolution has ONE implementation, and it lives in the shipped
# harness rather than here — the member declares where its files are, and the
# federation-side reader resolves that declaration through the same door the member's
# own harness does. Same import shape `curate/metrics.py` uses; importing `session` is
# cheap (script-relative paths, one config read, no repo commands).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session as _session  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAILBOXES = production.config_path(ROOT, "mailboxes.json", ROOT / "mailboxes.json")
# Through the git COMMON dir, not ROOT (WI-0211, same class as `curate/gather.py`).
# `proposed-edits/` is gitignored shared data living only in the main checkout; a lane
# sees it through a symlink `_link_shared_data()` creates at its FIRST heartbeat, and
# `--audit --status` runs as a SessionStart hook, which fires before that. Read
# lane-local, `FED_PROPOSED.is_dir()` below is simply False and the federation's whole
# outbound queue drops out of the audit with nothing saying it was never looked at.
# Used only for reads and resolved-path identity, so this cannot move where a brief
# is written. Falls back to ROOT outside a git repo.
FED_PROPOSED = shared_work_root(ROOT) / "proposed-edits"
FED_ARCH_ID = "federation-arch"

sys.path.insert(0, str(ROOT))         # for the harness's own machine detector


# ───────────────────────────────────────────────────────── which machine is asking (WI-0205)
#
# `this_machine()` and `declared_elsewhere()` now live in `curate/common.py` and are
# imported at the top of this module. They moved there in WI-0253, when
# `curate/reconcile.py` needed the identical judgement against the `portfolio.md`
# roster: the two tools print residency verdicts into the SAME startup banner, so a
# second copy of the rule could only ever drift into contradicting the first one
# line above it (P16). The reasoning — why a declaration and never an inference, and
# why an unnamed machine WITHHOLDS the exemption rather than granting it — is in the
# docstrings there, unchanged.


# ─────────────────────────────────────────────────────────────────── registry + locator

def load_mailboxes(production_roots=None):
    """{system-id: row} from mailboxes.json, or {} if absent/unreadable.

    Absent from that file means UNSURVEYED, never "has no mailbox" — the exact conflation
    that produced one member's wrong row in the survey this registry replaced."""
    roots = production_roots or production.resolve(ROOT)
    path = roots.mailboxes_path if roots else MAILBOXES
    try:
        members = json.loads(path.read_text(encoding="utf-8")).get("members", {})
    except Exception as exc:
        if roots:
            raise ValueError(f"production mailbox registry unreadable: {path}: {exc}") from exc
        return {}
    # Outside the try on purpose: an unreadable file degrades to "unsurveyed", but a
    # readable one that makes one address mean two members is a broken registry.
    check_aliases(members, where=path)
    return members


def _row_ids(row):
    """Every address a registry row answers to: its `architect_id`, then its `aliases`."""
    if not isinstance(row, dict):
        return []
    ids = [row["architect_id"]] if isinstance(row.get("architect_id"), str) else []
    aliases = row.get("aliases") or []
    return ids + [a for a in aliases if isinstance(a, str)] if isinstance(aliases, list) \
        else ids


def check_aliases(members, where="mailboxes.json"):
    """Refuse a registry in which one address would reach two rows.

    `aliases` exists for a RENAME: an envelope's destination is immutable, so mail queued
    under a member's old id (22 messages to `Orchestrator 1-arch`, after WI-0422 made the row's
    id `member-1-arch`) is unroutable forever unless the row still answers to
    the old name. That is safe only while the old name is unambiguous. An alias equal to
    another row's `architect_id`, or claimed by two rows, would deliver by row ORDER —
    a guess shaped like a lookup — so it is refused when the registry loads, before any
    mail is routed by it."""
    owner = {}
    for sid, row in (members or {}).items():
        if not isinstance(row, dict):
            continue
        aid = row.get("architect_id")
        if isinstance(aid, str) and aid:
            owner.setdefault(aid, sid)
    for sid, row in (members or {}).items():
        if not isinstance(row, dict) or "aliases" not in row:
            continue
        aliases = row["aliases"]
        if not isinstance(aliases, list) or not all(
                isinstance(a, str) and a.strip() for a in aliases):
            raise ValueError(f"{where}: row '{sid}' has malformed `aliases` ({aliases!r}); "
                             f"it must be a list of recipient ids.")
        for alias in aliases:
            if alias in owner and owner[alias] != sid:
                raise ValueError(
                    f"{where}: row '{sid}' lists alias '{alias}', which is already an "
                    f"address of row '{owner[alias]}'. One address may reach one member; "
                    f"remove the alias or the other row's claim.")
            if alias == row.get("architect_id"):
                raise ValueError(f"{where}: row '{sid}' lists its own architect_id "
                                 f"'{alias}' as an alias.")
            owner[alias] = sid


def _find_row(architect_id, mailboxes):
    """(system-id, row) whose `architect_id` or `aliases` name `architect_id`, or None.

    The ONE matcher every routing reader goes through (resolve, the mailbox tree, the
    collision check, `_row_for`), so an alias cannot route on the send path and read as
    unrostered on the audit path. Validates on every call because callers inject
    registries that never passed through `load_mailboxes`."""
    check_aliases(mailboxes)
    for sid, row in (mailboxes or {}).items():
        if architect_id in _row_ids(row):
            return sid, row
    return None


def _canonical(architect_id, row):
    """The row's own `architect_id` — what the member's repo declares, and so the id the
    locator fallback and the role-doc lookups must be asked. An alias is only an address."""
    aid = (row or {}).get("architect_id")
    return aid if isinstance(aid, str) and aid else architect_id


class RepoMap(dict):
    """{system-id: repo-root Path}, plus `refused`: {system-id: (why, [candidate repos])}
    for ids the walk found but could not pick ONE main checkout for. A dict so every
    caller that indexes the map keeps working; the refusals ride along so `resolve` can
    say WHY a member is not locatable instead of calling it absent."""

    def __init__(self, *args, refused=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.refused = dict(refused or {})


def _is_linked_worktree(repo):
    """Is `repo` a LINKED git worktree (`git worktree add`), not a main checkout?

    Git's own definition, read from disk rather than one subprocess per candidate (the
    devbox walk meets ~55 of them): a linked worktree's `.git` is a FILE naming a
    per-worktree git dir that carries a `commondir` file. A submodule's `.git` is also a
    file, but its git dir has no `commondir`, so it stays a main checkout. A `.git` file
    whose git dir is gone is a pruned worktree — never a delivery target, so True. A
    directory with no `.git` at all asks git (the STATUS.md may sit below the top level);
    outside any repository it is a plain directory, which is not a worktree."""
    dotgit = pathlib.Path(repo) / ".git"
    if dotgit.is_dir():
        return False
    if dotgit.is_file():
        try:
            line = dotgit.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return True
        if not line.startswith("gitdir:"):
            return True
        gitdir = pathlib.Path(line[len("gitdir:"):].strip())
        gitdir = gitdir if gitdir.is_absolute() else pathlib.Path(repo) / gitdir
        return (gitdir / "commondir").is_file() or not gitdir.is_dir()
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute",
                            "--git-dir", "--git-common-dir"],
                           capture_output=True, text=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    lines = r.stdout.split()
    if r.returncode != 0 or len(lines) != 2:
        return False
    return pathlib.Path(lines[0]).resolve() != pathlib.Path(lines[1]).resolve()


def _pick_checkouts(candidates):
    """({sid: repo}, {sid: (why, [repos])}) from every walked STATUS.md per id.

    Never a linked worktree: it shares the member's history but not its gitignored data,
    so its `proposed-edits/` is absent or stale, and delivery either refuses or writes
    where nobody reads. Never a GUESS between two main checkouts: both are real repos,
    and which one the member's sessions read is a fact only `repo-paths.local` can
    declare — so the id is refused with both paths named."""
    chosen, refused = {}, {}
    for sid, hits in candidates.items():
        repos = list(dict.fromkeys(path.parent for _fm, path in hits))
        mains = [r for r in repos if not _is_linked_worktree(r)]
        if len(mains) == 1:
            chosen[sid] = mains[0]
        elif mains:
            refused[sid] = (
                f"{len(mains)} main checkouts declare STATUS id '{sid}' "
                f"({', '.join(map(str, mains))}); refusing to guess which one the member "
                f"reads — map the right one in repo-paths.local", mains)
        else:
            shown = ", ".join(map(str, repos[:5])) + (" …" if len(repos) > 5 else "")
            refused[sid] = (
                f"only linked git worktrees declare STATUS id '{sid}' ({len(repos)}: "
                f"{shown}); a worktree does not carry the member's mailbox — map the main "
                f"checkout in repo-paths.local", repos)
    return chosen, refused


def locate_repos(production_roots=None):
    """{system-id: repo-root Path} for every member locatable on THIS machine.

    Reconcile's locator pieces (our own entry, the walk over the roots, the
    `repo-paths.local` map) in reconcile's precedence — self, then walked, then mapped —
    rather than a second locator (P16). What differs is the walk: reconcile keeps the
    first STATUS.md per id, which on devbox was one of ~55 `Orchestrator 1-wt-*` linked
    worktrees, so this picks among ALL hits (`_pick_checkouts`). A mapped path is a
    declaration and wins as before, which is also how an ambiguity is settled. A member
    absent here is NOT REACHABLE FROM THIS MACHINE, which is a different fact from
    having no mailbox, and the refusals below keep them distinct."""
    if production_roots:
        roots = read_external_roots(production_roots.config_root / "reconcile-roots.local")
        paths = read_repo_paths_config(production_roots.config_root / "repo-paths.local")
    else:
        roots, paths = read_roots_config(), read_repo_paths()
    # Idless STATUS.md files (WI-0373) cannot name a recipient, so they stay out of the
    # repo map here and are reported by `curate/reconcile.py` instead.
    found, _idless = self_entry()
    repos = {sid: status_path.parent for sid, (_fm, status_path) in found.items()}
    candidates, _idless = find_status_candidates(roots)
    chosen, refused = _pick_checkouts(candidates)
    repos.update(chosen)
    if paths:
        mapped, _broken = resolve_mapped(paths)
        repos.update({sid: status_path.parent for sid, (_fm, status_path) in mapped.items()})
    return RepoMap(repos, refused={sid: why for sid, why in refused.items()
                                   if sid not in repos})


def _refusal_for(sid, architect_id, repos):
    """The locator's refusal for this member, if it made one: keyed by the registry key,
    or by any refused candidate repo that declares this architect_id (the `Orchestrator 1` row is
    walked under STATUS id `member-1`)."""
    refused = getattr(repos, "refused", None) or {}
    if sid in refused:
        return refused[sid][0]
    for why, candidates in refused.values():
        if any(_own_architect_id(c) == architect_id for c in candidates):
            return why
    return ""


def _locate(sid, architect_id, repos, row=None):
    """The member's repo root on THIS machine, or None.

    The registry key first, then the member's own declared `architect_id` as a fallback
    (see `_repo_by_architect_id` for the rename case). Extracted because three callers —
    `resolve`, `recipient_mailbox_tree` and the reachability sweep — must agree on what
    "locatable from here" means; the sweep decides whether an absence is a DEFECT or a
    DECLARED residency, and it would be answering a different question from the send path
    if either copy of this drifted ([P16]).

    `hosted_by` is for a recipient that has NO REPO OF ITS OWN. The locator finds a member
    by reading a `STATUS.md` that declares its system-id, which is the right rule for an
    Architect and unsatisfiable for anyone else — the external consultant holds no
    repo and is not an Architect, so there is nothing on disk for the walk to find. Such a
    row DECLARES which member's repo hosts its mailbox and we resolve that instead. It is a
    declaration, never an inference: a row without it behaves exactly as before, and a row
    whose host is not locatable here returns None rather than falling through to a guess —
    "hosted somewhere we cannot reach" and "hosted nowhere" must not collapse into one
    answer ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes))."""
    host = (row or {}).get("hosted_by")
    if host:
        return repos.get(host)
    return repos.get(sid) or _repo_by_architect_id(architect_id, repos)


class RecipientUnavailable(ValueError):
    """A routing decision that retries on this host cannot repair."""

    def __init__(self, message, outcome):
        super().__init__(message)
        self.outcome = outcome


class RetiredInbox(RecipientUnavailable):
    """A member's mailbox that resolves into the FEDERATION's own `proposed-edits/`.

    That was the read-federation-direct model (ADR-0027 §2): a resident-runtime member read its briefs out of our
    tree, so "delivery" was the federation writing into its own data root. ADR-0028
    retired it and that member now reads its own repo-local inbox, so nobody reads that directory
    any more. A row that still resolves there would copy the brief, read it back, report
    DELIVERED, and leave it where no session ever looks. That is a silent drop dressed as
    a success, so it is a refusal with its own type, not a quiet write (WI-0012).

    A type for the reason `AlreadyHeld` is one: the poller files it in its own bucket, and
    a caller that told outcomes apart by reading the message would fold them back together
    on the first rewording."""

    def __init__(self, architect_id, box):
        super().__init__(
            f"REFUSING to write to '{architect_id}': its mailbox resolves to {box}, inside "
            f"the federation's own proposed-edits/. That is the retired "
            f"read-federation-direct inbox (ADR-0028); no session reads it, so a brief "
            f"written there is lost while reporting delivered. Fix the member's row or "
            f"its repo-paths.local mapping. Nothing was written.", "retired")
        self.architect_id = architect_id
        self.box = box


def _in_federation_tree(box):
    """Is `box` at or under the federation's own `proposed-edits/`? Compared RESOLVED,
    because a lane reaches that directory through a symlink into the main checkout."""
    try:
        return pathlib.Path(box).resolve().is_relative_to(FED_PROPOSED.resolve())
    except OSError:
        return False


def resolve(architect_id, mailboxes=None, repos=None, *, production_roots=None):
    """(repo_root, mailbox_dir, row) for an architect-id, or raise ValueError naming the
    problem. Every failure mode gets its own sentence — a delivery tool whose errors are
    interchangeable is how mail goes missing quietly."""
    mailboxes = load_mailboxes(production_roots) if mailboxes is None else mailboxes
    repos = locate_repos(production_roots) if repos is None else repos
    hit = _find_row(architect_id, mailboxes)
    if not hit:
        known = ", ".join(sorted(r.get("architect_id", "?") for r in mailboxes.values()))
        raise RecipientUnavailable(f"unknown recipient '{architect_id}'. mailboxes.json knows: {known}. "
                         f"An id missing from that file is UNSURVEYED, not mailbox-less — "
                         f"add a row rather than guessing a path.", "unroutable")
    sid, row = hit
    # An alias is an ADDRESS; the member is found and judged by the row's own id.
    canonical = _canonical(architect_id, row)
    if row.get("tracked"):
        raise ValueError(
            f"REFUSING to write to '{architect_id}': its mailbox ({row['mailbox']}) is "
            f"TRACKED in git. A brief dropped there is swept into that system's history "
            f"under THEIR authorship, in a commit they never reviewed (ADR-0088 D1). "
            f"They have been briefed to gitignore it; deliver once they have, or hand "
            f"the content over out-of-band.")
    if not row.get("reachable", True):
        raise RecipientUnavailable(f"'{architect_id}' has no reachable mailbox on record "
                         f"({row.get('note', '')[:120]}).", "unroutable")
    roots = production_roots or production.resolve(ROOT)
    if canonical == FED_ARCH_ID and roots:
        if roots.inbox_root is None:
            raise RecipientUnavailable("federation inbox is not authoritative on this installation; deliver on its owning host", "elsewhere")
        return roots.inbox_root.parent, roots.inbox_root, row
    repo = _locate(sid, canonical, repos, row)
    if repo is None:
        why = _refusal_for(sid, canonical, repos)
        if why:
            raise RecipientUnavailable(f"'{architect_id}' ({sid}) is not locatable from this "
                                       f"machine: {why}. Nothing was written.", "unreachable")
        raise RecipientUnavailable(f"'{architect_id}' ({sid}) is not locatable from this machine. "
                         f"That is a LOCATION problem, not a missing mailbox — map it in "
                         f"repo-paths.local. Nothing was written.",
                         "elsewhere" if declared_elsewhere(row, this_machine()) else "unreachable")
    box = repo / row["mailbox"]
    # Before the existence check: the retired location usually still exists on disk, and
    # "it exists" is exactly what made the read-direct drop look like a delivery.
    if (canonical != FED_ARCH_ID and not row.get("hosted_by")
            and _in_federation_tree(box)):
        raise RetiredInbox(architect_id, box)
    if not box.is_dir():
        raise ValueError(f"'{architect_id}' mailbox {box} does not exist on disk, though "
                         f"mailboxes.json records it. Re-probe that row — a registry that "
                         f"has drifted from the filesystem is worse than none.")
    return repo, box, row


# Module-level so a test can point it somewhere absent and exercise the fail-closed
# branch directly, rather than by mocking `Path.is_file` — which the missing-brief check
# above also calls, so a blanket mock would trip THAT branch and the test would pass
# without ever reaching this guard.
CHECK_APPLY = pathlib.Path(__file__).resolve().parent / "check-apply.py"


class MalformedBrief(ValueError):
    """The brief's own header routes it to nobody, so no machine will ever deliver it.

    THE SAME SPLIT `AlreadyHeld` DRAWS, one refusal over. A transport failure is worth an
    alarm because a re-run may clear it and someone should look; a brief whose `apply:`
    header is non-conforming will be refused identically on every run until its AUTHOR
    edits the header. Folding the second into `failed` is how a permanently stuck count
    gets into the channel someone watches — WI-0235's whole lesson — and the courier path
    made that concrete: widening the sweep to members' outboxes (WI-0336) surfaced 11
    briefs in this state at once, every one of them authored in a repo that is not ours.

    TYPED, NOT A MESSAGE THE CALLER GREPS, and subclassing `ValueError` so every existing
    `except ValueError` on this module behaves exactly as it did. `detail` carries
    `check-apply.py`'s own report, because a refusal the reader cannot act on is one they
    learn to skip."""

    def __init__(self, message, brief="", detail=""):
        super().__init__(message)
        self.brief = brief
        self.detail = detail


def _check_apply_mode(src):
    """Refuse to deliver a brief whose apply mode routes it to nobody.

    `check-apply.py` has enforced the ADR-0049/ADR-0050 apply-mode partition since
    session 68 — but only as a thing an author was expected to REMEMBER to run, and the
    delivery path never called it. So `2026-08-04-your-mailbox-is-tracked-and-should-be-
    gitignored` shipped to a resident-runtime member declaring `apply: manual` with no `verify:` and a
    manual-reason that is not `attended`: the runner cannot verify it, operator is not routed
    it, and it sat in that member's inbox for days in nobody's queue. Running the existing guard
    HERE is the structural half (`add-structural-guard-on-recurrence` / P15): a brief that
    lands in the unowned gap can no longer be delivered into it.

    Fail-CLOSED, unlike the startup guards: this is an interactive send, so refusing a
    brief costs one re-author, while delivering an unroutable one costs days of silence.
    A missing/unrunnable checker is itself a refusal — never a pass.
    """
    if not CHECK_APPLY.is_file():
        # NOT a `MalformedBrief`: nothing is known about the brief. A missing checker is a
        # failure of OUR tooling, it is fixable here, and a re-run clears it — so it stays
        # in the alarm channel rather than joining the standing census below.
        raise ValueError(f"cannot verify apply mode: {CHECK_APPLY} is missing. REFUSING "
                         f"to deliver unchecked — nothing was written.")
    proc = subprocess.run([sys.executable, str(CHECK_APPLY), str(src)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stdout or proc.stderr).strip()
        raise MalformedBrief(
            f"REFUSING to deliver {src.name} — its apply mode routes it to nobody:\n"
            f"{detail}\n"
            f"Fix the header and deliver again. Nothing was written.",
            brief=str(src), detail=detail)


class AlreadyHeld(ValueError):
    """The recipient already holds this brief's edit-id. A refusal, but EVIDENCE OF
    DELIVERY rather than a transport failure — and the two must never share an output
    (`declare-what-a-check-assumes`, applied to a counter rather than a check).

    WI-0235 is what this type is for. A brief was delivered by hand and its outbox copy
    left queued; every subsequent poll hit the collision guard below, which is exactly
    right, and the poller booked the outcome as `1 failed` because a raised exception was
    the only thing it could see. Nothing had failed — the brief was in the recipient's
    mailbox — but the failed count is the signal someone watches to notice the channel
    breaking, and a permanently stuck `1` trains the reader to ignore it. The real failure
    then arrives looking identical.

    TYPED, NOT A MESSAGE THE CALLER GREPS. The only consumer that matters is unattended,
    and a caller that separates *could not deliver* from *did not need to* by matching
    prose is one wording change away from counting a delivered brief as a failure again.
    Subclasses `ValueError` so every existing `except ValueError` on this module keeps
    behaving exactly as it did — this widens what a caller CAN know, and narrows nothing.

    `where` is the recipient-side evidence: the path in their mailbox, or the role doc
    whose CHANGELOG records the edit-id. A verdict that cannot say what convinced it is
    the shape that persuaded another Architect to relay a wrong number as fact (WI-0101).
    """

    def __init__(self, message, architect_id="", edit_id="", where=""):
        super().__init__(message)
        self.architect_id = architect_id
        self.edit_id = edit_id
        self.where = where


def _check_collision(architect_id, src, mailboxes, repos, production_roots=None):
    """Refuse to write over something the recipient already holds.

    `resolve()` answers *may we write to this mailbox at all* — reachable, untracked,
    on the roster. It says nothing about what is already sitting in it, so the write
    below was `box / src.name` with no existence check: a brief whose name matched one
    of the recipient's would silently replace their unread mail, and a brief they had
    already triaged to `applied/` or `declined/` would come back as fresh `pending/`
    mail with its terminal record still on disk beside it. That is the third
    misdelivery instance WI-0072 records — a destination that is reachable AND
    untracked AND still the wrong place to write.

    Matched on edit-id FIRST and filename second, the same order and index the audit
    uses (`classify`), because the id is the brief's identity and the filename is only
    its label. Fail-CLOSED on an unreadable tree, like `_check_apply_mode`: not being
    able to see what is there is not evidence that nothing is.

    TWO OF THE FOUR REFUSALS BELOW ARE `AlreadyHeld`, AND TWO ARE NOT — the split is the
    point, not a detail. An edit-id found in their mailbox, or recorded in their tracked
    CHANGELOG, says *this brief arrived*; the caller may honestly file its copy and carry
    on. An unreadable tree says *we could not tell*, and a same-name-different-id clash
    says *two different briefs are colliding* — neither is evidence of anything arriving,
    both stay plain refusals, and both must stay loud.
    """
    tree, why = recipient_mailbox_tree(architect_id, mailboxes, repos, production_roots)
    if tree is None:
        raise ValueError(f"cannot check for a collision in '{architect_id}' mailbox: {why}. "
                         f"REFUSING to write blind — nothing was written.")
    by_id, by_name = _index_mailbox(tree)
    if by_id is None:
        raise ValueError(f"cannot read '{architect_id}' mailbox tree ({tree}) to check for a "
                         f"collision. REFUSING to write blind — nothing was written.")
    text = _read_text(src)
    eid = brief_edit_id(text) if text is not None else None
    if eid and eid in by_id:
        raise AlreadyHeld(
            f"REFUSING to deliver {src.name} to '{architect_id}': they already hold "
            f"edit-id '{eid}' at {by_id[eid]}. If that is a terminal state ("
            f"applied/declined/withdrawn) redelivering re-opens a settled record; if it is "
            f"still pending they have not read it yet. Amend the edit-id for a genuine "
            f"revision, or pass --replace to overwrite deliberately. Nothing was written.",
            architect_id=architect_id, edit_id=eid, where=str(by_id[eid]))
    # SECOND INDEX, from tracked history. The mailbox above is gitignored data on one
    # machine; the CHANGELOG survives a rebuilt data root. Consulted only to REFUSE, never
    # to permit — see `changelog_edit_ids` for why a miss here proves nothing.
    if eid:
        hit = _find_row(architect_id, mailboxes)
        sid = hit[0] if hit else None
        canonical = _canonical(architect_id, hit[1] if hit else None)
        repo = _locate(sid, canonical, repos, mailboxes.get(sid) or {}) if sid \
            else _repo_by_architect_id(architect_id, repos)
        if repo is not None:
            applied_ids, why = changelog_edit_ids(repo, canonical)
            if applied_ids and eid in applied_ids:
                raise AlreadyHeld(
                    f"REFUSING to deliver {src.name} to '{architect_id}': their role-doc "
                    f"CHANGELOG records edit-id '{eid}' as already applied, even though it "
                    f"is not in their mailbox. The mailbox is gitignored data on one "
                    f"machine; the CHANGELOG is tracked history, so this is the more "
                    f"durable record and it says the decision is settled. Redelivering "
                    f"would re-open it. Amend the edit-id for a genuine revision, or pass "
                    f"--replace to overwrite deliberately. Nothing was written.",
                    architect_id=architect_id, edit_id=eid,
                    where=str(_roledoc_path(repo, canonical) or repo))
            if why:
                # Not fatal — the mailbox index above already answered, and this one is
                # only ever additional evidence. But say that it could not be consulted,
                # rather than letting an unread index look like a clean one.
                print(f"note: could not cross-check '{architect_id}' CHANGELOG for "
                      f"already-applied briefs ({why}) — mailbox evidence only.",
                      file=sys.stderr)

    if src.name in by_name:
        raise NameTaken(
            f"REFUSING to deliver {src.name} to '{architect_id}': a different file of that "
            f"name is already at {by_name[src.name]} (no matching edit-id). Writing would "
            f"replace it. Rename this brief, or pass --replace to overwrite deliberately. "
            f"Nothing was written.",
            architect_id=architect_id, where=str(by_name[src.name]), taken=set(by_name))


class NameTaken(ValueError):
    """Only the brief's LABEL collides: its edit-id is not held, the filename is.

    Typed for the same reason as `AlreadyHeld`: the unattended mail worker must tell
    this refusal apart from the others without matching prose. The worker renames a
    brief that carries its own edit-id and delivers it (`free_name`); a human sending
    by hand still gets the refusal, because two hand-named briefs alike is a mistake.

    `taken` is every filename in the recipient's mailbox TREE, not just `pending/`: a
    triaged brief in `applied/` still owns its name there.
    """

    def __init__(self, message, architect_id="", where="", taken=()):
        super().__init__(message)
        self.architect_id = architect_id
        self.where = where
        self.taken = frozenset(taken)


def free_name(filename, taken):
    """`filename`, or the same stem with `.2`, `.3` … — the first not in `taken`.

    The same suffix convention `outbox._free_name` gives generated receipts, so a
    renamed brief reads in the recipient's mailbox the way the others already do."""
    if filename not in taken:
        return filename
    stem, suffix = pathlib.PurePath(filename).stem, pathlib.PurePath(filename).suffix
    n = 2
    while f"{stem}.{n}{suffix}" in taken:
        n += 1
    return f"{stem}.{n}{suffix}"


def precheck(architect_id, brief_path, mailboxes=None, repos=None, replace=False,
             *, production_roots=None):
    """Every refusal `deliver()` applies BEFORE it writes, and nothing else. Returns
    `(repo, box, row)` — the resolved destination — or raises the refusal.

    THIS EXISTS SO A REHEARSAL CANNOT DIVERGE FROM THE RUN IT REHEARSES (WI-0251). The
    first `--dry-run` returned before `deliver()` entirely and therefore before every gate
    below, so it reported eleven deliveries the live run refused; the repair re-listed the
    gates at the poller's call site, in the poller's own order. That repair was correct
    and structurally the same defect one move on: two hand-maintained copies of one
    sequence, kept in agreement by discipline, in a program whose whole subject is a
    rehearsal that stopped agreeing with the real thing. There is now ONE sequence. The
    dry run calls this; the write path calls this and then writes. The only difference
    between them is the write, which is the only difference there was ever meant to be.

    EVERY STEP HERE IS READ-ONLY, and that is a load-bearing property, not an observation:
    it is what makes the sequence safe to call from a run that has promised to change
    nothing. `_retire_source` — the one write `deliver()` makes besides the copy — stays
    on the far side of this boundary deliberately.

    `replace=True` skips the collision guard exactly as it always did; it is passed
    through rather than re-implemented so the rehearsal of a `--replace` delivery is
    the rehearsal of the delivery that would actually happen."""
    src = pathlib.Path(brief_path)
    if not src.is_file():
        raise ValueError(f"no brief at {src}. Nothing was written.")
    _check_apply_mode(src)
    repo, box, row = resolve(architect_id, mailboxes, repos, production_roots=production_roots)
    if not replace:
        _check_collision(architect_id, src, mailboxes if mailboxes is not None
                         else load_mailboxes(production_roots),
                         repos if repos is not None else locate_repos(production_roots),
                         production_roots)
    return repo, box, row


def deliver(architect_id, brief_path, mailboxes=None, repos=None, replace=False,
            *, production_roots=None):
    """Copy `brief_path` into the recipient's real inbox. Returns the written Path.

    Verified by READING THE FILE BACK, not by the copy returning without raising — the
    whole failure class this tool addresses is a write that appeared to succeed.

    The refusals live in `precheck()`, which `--dry-run` calls too — see there for why
    they are one sequence and not two.

    `replace=True` is the deliberate overwrite. It is a parameter rather than the default
    because replacing a brief the recipient has not read yet destroys mail, and a tool
    that does that silently is the failure this one exists to prevent."""
    src = pathlib.Path(brief_path)
    context = {"production_roots": production_roots} if production_roots else {}
    _repo, box, _row = precheck(architect_id, src, mailboxes, repos, replace=replace, **context)
    dest = box / src.name
    payload = src.read_bytes()
    shutil.copy2(src, dest)
    if not dest.is_file() or dest.read_bytes() != payload:
        raise ValueError(f"wrote {dest} but it does not read back identical. Treat as "
                         f"NOT delivered.")
    _retire_source(src)
    return dest


def _retire_source(src):
    """Move a delivered brief out of the place it was queued, into a `delivered/` sibling.

    Found by using the tool: after five real deliveries the audit still reported all five
    as stuck, because `deliver` copied and left the original where it was. An audit that
    keeps flagging mail that DID arrive is the crying-wolf failure — and this one would
    have trained us to ignore the very report built to catch silent non-delivery.

    Moved rather than deleted: the sender's copy is the record that we sent it, and
    `delivered/` is outside the queue so the audit is satisfied while the history stays.
    Only ever touches a source under one of OUR OWN two queues — a brief delivered from
    somewhere else is not ours to relocate.

    THE SECOND QUEUE IS THE OUTBOX, AND OMITTING IT IS WHAT MANUFACTURED WI-0235. A brief
    authored in `outbox/to-<id>/` and then delivered directly — the correct move when the
    recipient is reachable from this machine, since waiting for a poll buys nothing — left
    its queued copy behind, because the containment test below knew only `proposed-edits/`.
    Every later poll then re-delivered it, hit the collision guard, and booked a failure
    for a brief that had arrived. Retiring here removes the whole class at its source
    rather than teaching the poller to forgive it after the fact; the poller's own handling
    of `AlreadyHeld` stays, because a hand-delivery from ANOTHER machine's checkout can
    still leave a copy queued here that this function never sees."""
    try:
        # Resolve BOTH sides. Comparing a resolved path against an unresolved one differs
        # by form alone wherever the tree sits under a symlink (macOS `/tmp` →
        # `/private/tmp`), so the containment test silently answered "not ours" and the
        # retire never fired — the same symlinked-checkout trap `inbox_dirs` documents.
        parents = src.resolve().parents
        # A queued brief lives at `outbox/to-<id>/<brief>.md`, one level down. Testing the
        # immediate parent rather than mere containment keeps a file already sitting in
        # `outbox/delivered/` from being "retired" onto itself and silently gaining a `.2`.
        if src.resolve().parent.name.startswith("to-") and \
                outbox.OUTBOX.resolve() in parents:
            outbox.retire(src)
            return
        if FED_PROPOSED.resolve() not in parents:
            return
        out = src.parent.parent / "delivered"
        out.mkdir(exist_ok=True)
        src.replace(out / src.name)
    except Exception:
        pass          # delivery already succeeded; bookkeeping must never undo it


# ──────────────────────────────────────────────────────── brief identity + the three states

DELIVERED = "DELIVERED"       # matched by edit-id in the recipient's own mailbox
UNDELIVERED = "UNDELIVERED"   # absent from the recipient's mailbox, which we could read
UNKNOWN = "UNKNOWN"           # could not tell THIS RUN — a named blocker, fixable, re-runnable
CORROBORATED = "CORROBORATED" # no content key, but their disk backs the sender's own delivery note
UNRESOLVABLE = "UNRESOLVABLE" # no content key exists at all, and no re-run will ever produce one
OFF_MACHINE = "OFF_MACHINE"   # the evidence is on another machine's disk — no re-run HERE will settle it

# WI-0205 ADDS A SIXTH STATE, ON WI-0223's OWN AXIS.
#
# WI-0223 graded "could not tell" by asking *will a re-run ever answer this?* — UNKNOWN for
# a named blocker you can fix and re-run, UNRESOLVABLE for a brief with no content key,
# where no later ever comes. That axis has a third position it did not need a name for
# yet: a brief that HAS a content key, whose answer no re-run on THIS machine will ever
# produce, and which another machine could answer today.
#
# ADR-0088 D1 gitignores every mailbox fleet-wide, deliberately, so a mailbox's contents
# exist on exactly one machine and never travel. One member delivered a brief to another on
# the Runner; from devbox that mailbox looks empty and always will. Reporting it UNKNOWN
# would send the reader back to a check that cannot answer here — the exact folding
# WI-0223 removed. Reporting it UNRESOLVABLE would be worse: it is resolvable, just not
# from here, and the remedy is a machine rather than an amended brief.
#
# Three states because there are three different next actions: fix the blocker and re-run;
# ask the sender to amend; run the audit on the machine that can see the disk.
# ([`declare-what-a-check-assumes`] — print a distinct answer when the assumption is false.)

# WI-0223 SPLIT THE OLD SINGLE `UNKNOWN` IN THREE, and the split is the whole item.
#
# One state was carrying five different facts. Four of them are transient and name their
# own fix — the recipient is unlocatable (map it), our copy is unreadable, their tree is
# unreadable, a same-named file needs a human look. The fifth is not transient at all: a
# brief that declares no `edit-id` has no content key, and `proposed-edits/` is gitignored
# as data (ADR-0007) so there is no history to fall back on. Re-running the audit produces
# the same answer forever. Reporting both under "could not tell" told the reader to come
# back later about something no later would ever resolve — the exact folding
# [`declare-what-a-check-assumes`] forbids: state what the check assumes about its subject,
# and print a DISTINCT answer when the assumption is false.
#
# Briefs sat UNDETERMINED for weeks on that account. Both had in fact
# been acted on, and both said so themselves — see `declared_destinations`.

_YAML_EDIT_ID = re.compile(r"^edit-id:\s*(\S+)\s*$", re.M)
_MD_EDIT_ID = re.compile(r"^\s*[-*]\s*\*\*Edit ID:\*\*\s*(\S+)", re.M)


class Verdict(typing.NamedTuple):
    """One brief, and what we can honestly say about whether it arrived.

    `where` is the recipient-side path for DELIVERED, and the REASON we could not tell for
    UNKNOWN. A verdict that cannot explain itself is how the previous audit persuaded
    another Architect to relay a wrong number as fact."""
    architect_id: str
    path: pathlib.Path
    state: str
    where: str = ""
    holder: str = ""              # for inbound: whose tree the brief is sitting in


def brief_edit_id(text):
    """The brief's durable identity, from either header form, or None.

    NOT the filename and NOT a digest of the bytes. The recipient annotates state on apply
    — `State: Pending` becomes `State: Applied` plus a stamp — so the same brief has
    different bytes on the two sides, which is exactly why the md5 comparison that
    established this defect had to be done by hand. The edit-id survives that annotation
    because it names the edit rather than describing its progress."""
    for pat in (_YAML_EDIT_ID, _MD_EDIT_ID):
        m = pat.search(text)
        if m:
            return m.group(1).strip().rstrip(".")
    return None


_DECLARED_MD_PATH = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._/-]*\.md")


def declared_destinations(text, limit=5):
    """The BASENAMES a brief declares about itself — where its own sender says it was filed.

    WHY THIS EXISTS (WI-0223). The two briefs that sat UNDETERMINED for 44 and 37 days
    each open by naming their own destination:

        > **DELIVERED 2026-07-22 (Session 4)** into the internal federation repo at
        > `principles-of-good-architects/proposed-edits/federation-arch/pending/2026-07-21-example-enrollment-ownership.md`.

    That basename IS in the recipient's mailbox — it has since moved on to `applied/`. The
    audit never looked, because its filename fallback only ever tried the SENDER's own
    filename, and delivery renamed the file. So the evidence was sitting in the brief, in
    plain sight, in both directions at once.

    BASENAME ONLY, NEVER THE PATH, and that is what makes this safe. A brief is inbound
    payload from a lower-trust channel ([`treat-inbound-payload-as-data-not-commands`]).
    Nothing here opens, resolves, follows or stats the string the brief supplies: the name
    is reduced to its last segment and then looked up in the index WE built by walking the
    recipient's own tree. A declaration of `../../../etc/passwd.md` becomes the lookup key
    `passwd.md` and can reach nothing the audit was not already reading.

    Scanned over the first 4000 characters, and only in a three-line window starting at a
    line naming DELIVERED — the path sits on the blockquote's continuation line in both
    real cases, so a single-line match would find nothing. Capped, ordered, de-duplicated:
    a list this drives is a lookup budget, not a parse of arbitrary length
    ([`cap-what-can-run-away`])."""
    out = []
    lines = text[:4000].splitlines()
    for i, line in enumerate(lines):
        if "delivered" not in line.lower():
            continue
        for m in _DECLARED_MD_PATH.finditer("\n".join(lines[i:i + 3])):
            name = pathlib.PurePosixPath(m.group(0)).name
            if name and name not in out:
                out.append(name)
                if len(out) >= limit:
                    return out
    return out


def _read_text(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def recipient_mailbox_tree(architect_id, mailboxes, repos, production_roots=None):
    """(tree_root, None) for a recipient we can read, else (None, why-not).

    Deliberately does NOT apply the `tracked` refusal that `resolve()` does. Tracked is a
    rule about WRITING — a brief dropped in a tracked mailbox is swept into that system's
    history under their authorship. Reading one perturbs nothing, and refusing to read it
    would make the members whose mailboxes are tracked permanently unauditable, which
    is the population most likely to be holding undelivered mail.

    Returns the mailbox's PARENT, not `pending/`: a delivered brief moves on to `applied/`,
    `accepted/`, `declined/` or `delivered/` the moment the recipient triages it, so a
    scan that looked only in `pending/` would call every applied brief undelivered — the
    same off-by-one-directory mistake in a new place."""
    hit = _find_row(architect_id, mailboxes)
    if not hit:
        return None, ("unrostered in mailboxes.json — UNSURVEYED, which is not the same "
                      "fact as having no mailbox")
    sid, row = hit
    canonical = _canonical(architect_id, row)
    if not row.get("reachable", True):
        return None, f"no reachable mailbox on record ({row.get('note', '')[:80]})"
    roots = production_roots or production.resolve(ROOT)
    if canonical == FED_ARCH_ID and roots:
        if roots.inbox_root is None:
            return None, "federation inbox is not authoritative on this installation"
        return roots.inbox_root.parent, None
    repo = _locate(sid, canonical, repos, row)
    if repo is None:
        why = _refusal_for(sid, canonical, repos)
        return None, (f"not locatable from this machine: {why}" if why else
                      "not locatable from this machine (a LOCATION problem, not a mailbox one)")
    box = repo / row["mailbox"]
    if not box.is_dir():
        return None, f"mailbox {row['mailbox']} recorded but absent on disk — re-probe the row"
    return box.parent, None


def _index_mailbox(tree):
    """({edit-id: path}, {filename: path}) for everything in a recipient's mailbox tree,
    or (None, None) if the tree cannot be walked.

    An unreadable tree must reach the caller as UNKNOWN rather than as an empty index —
    an empty index would make every brief read UNDELIVERED, which is a confident wrong
    answer built out of a permissions error."""
    by_id, by_name = {}, {}
    try:
        for f in sorted(tree.rglob("*.md")):
            by_name.setdefault(f.name, f)
            text = _read_text(f)
            if text is None:
                continue
            eid = brief_edit_id(text)
            if eid:
                by_id.setdefault(eid, f)
    except Exception:
        return None, None
    return by_id, by_name


CHANGELOG_EDIT_ID_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]{3,})\b")

# A CHANGELOG heading, at any level, with or without a section number. The old form was
# two `rfind`s on a lowercased copy (`"\n## changelog"`, then `"\nchangelog"`), which is
# why one member's `## 10. Changelog` matched neither and its whole applied history read
# as empty (WI-0282). A member is free to number its sections; the guard must not depend
# on it not doing so.
CHANGELOG_HEADING_RE = re.compile(r"(?mi)^\s{0,3}#{1,6}\s+(?:\d+[.)]\s*)*changelog\s*$")
# The first line that looks like a CHANGELOG ENTRY — `- **2.60.0** (2026-09-09) — …`.
# Used to skip a history file's title and preamble, never to select entries one by one:
# an entry that wraps onto a continuation line must keep contributing its receipt, so
# everything from the first entry to EOF is in scope. See `_changelog_scope`.
CHANGELOG_ENTRY_RE = re.compile(r"(?m)^\s{0,3}[-*]\s+\*{0,2}v?\d+\.\d+\.\d+")


def _roledoc_path(repo, architect_id):
    """The recipient's role doc, from its own `session.config.json` where it declares one
    (the canonical answer) and by convention otherwise. Returns None if neither resolves —
    which the caller must treat as *cannot tell*, never as *nothing applied*.

    The declaration is read through the shipped harness's `member_layout` (WI-0029): the
    member declares its layout in one place and every reader — its own harness, and the
    federation mining every member checkout — resolves it through the same door. A second copy of
    the `layout` block / legacy-key / default precedence here is exactly the drift that
    item exists to remove."""
    name = _session.member_layout(repo, "role_doc")
    if name and (repo / name).is_file():
        return repo / name
    guess = repo / f"{architect_id}.md"
    if guess.is_file():
        return guess
    hits = sorted(repo.glob("*-arch.md"))
    return hits[0] if len(hits) == 1 else None


def _changelog_source(repo, architect_id):
    """`(path, whole_file, None)` for the file holding the recipient's role-doc CHANGELOG,
    or `(None, None, why)` when it cannot be located.

    Two shapes, because the fleet now has two (ADR-0123):

    - **Split.** The member declares `changelog_doc`, and that file IS the changelog —
      `whole_file` is True and no heading has to be found inside it.
    - **Inline.** The member declares nothing, and the changelog is a section of the role
      doc — `whole_file` is False and the section heading bounds it. Every member but the
      federation is here today, and none of them needs an edit.

    A DECLARED-BUT-UNREADABLE `changelog_doc` never falls back to the role doc. The
    fallback looks harmless and is the whole bug this function was rewritten for: a split
    member's role doc still carries a `## CHANGELOG` pointer line, so falling back would
    find a heading, parse zero receipts, and report a clean cross-check of a file that was
    never read. Declared and missing is *could not check*, which is its own answer."""
    declared = _session.member_layout(repo, "changelog_doc")
    if declared:
        doc = repo / declared
        if not doc.is_file():
            return None, None, (f"declared changelog_doc '{declared}' is not a file in the "
                                f"recipient's repo")
        return doc, True, None
    doc = _roledoc_path(repo, architect_id)
    if doc is None:
        return None, None, "no role doc located in the recipient's repo"
    return doc, False, None


def _changelog_scope(text, whole_file):
    """The slice of `text` that receipts may be mined from, or None when there is none.

    NARROWING IS THE DANGER, not breadth. A receipt this misses is a re-delivery nobody
    refuses, so each step here only ever removes text that cannot be part of an entry:

    1. Inline: everything before the LAST CHANGELOG heading — an edit-id quoted in prose
       elsewhere in a role doc is a mention, not a receipt. No heading at all returns None,
       and the caller turns that into *could not check* (WI-0282).
    2. Either shape: everything before the FIRST entry line — a history file's title and
       preamble are prose in exactly the way §-scoped role-doc text already was. When no
       entry line is recognisable the whole region is kept, because an unfamiliar entry
       grammar is not evidence that there are no entries."""
    if not whole_file:
        hits = list(CHANGELOG_HEADING_RE.finditer(text))
        if not hits:
            return None
        text = text[hits[-1].end():]
    first = CHANGELOG_ENTRY_RE.search(text)
    return text[first.start():] if first else text


def changelog_edit_ids(repo, architect_id):
    """`(set_of_edit_ids, None)` mined from the recipient's role-doc CHANGELOG, or
    `(None, why)` when it cannot be read.

    WHY THIS EXISTS. Applying a brief produces four artifacts and git carries three: the
    role-doc edit, the version bump, the CHANGELOG entry. The fourth — the brief moving
    `pending/` → `applied/` — lives in `proposed-edits/`, which is gitignored as data. So
    the mailbox tree, which is the only index `_index_mailbox` can build, exists on
    exactly one machine and nothing reconstructs it. A rebuilt data root loses every
    settled record, and a re-delivered brief becomes indistinguishable from a new one —
    which matters now that development happens on a machine that is deliberately
    rebuildable rather than restorable. The standard section requires the brief's
    `edit-id` in the CHANGELOG line for exactly this reason; this function is what turns
    that convention into a usable index.

    WHERE the changelog lives is now the member's declaration, not this function's
    assumption (ADR-0123). What makes the receipt durable was never the role doc's
    filename — it is that the file is TRACKED IN GIT while the mailbox is not — so a
    tracked sibling carries the identical argument. `_changelog_source` resolves both
    shapes; everything below is the same for either.

    IT WIDENS THE GUARD AND MUST NEVER NARROW IT. A CHANGELOG can only ever be evidence
    that something was APPLIED. It cannot show `declined` or `withdrawn`, and an Architect
    that adopted the convention late has entries with no id at all. So a hit here is
    grounds to REFUSE a delivery; a miss here is grounds for nothing. Unreadable is
    returned as its own answer for the same reason — *could not check* and *checked and
    found nothing* must not share a return value."""
    doc, whole_file, why = _changelog_source(repo, architect_id)
    if doc is None:
        return None, why
    text = _read_text(doc)
    if text is None:
        return None, f"changelog {doc.name} could not be read"
    scope = _changelog_scope(text, whole_file)
    if scope is None:
        # A role doc with no CHANGELOG heading is a doc this function CANNOT CHECK. It
        # used to return the empty set here — "checked and found nothing" — in violation
        # of the rule its own docstring states, and that is not a theoretical slip: it is
        # how one member's applied history read as clean for months (WI-0282).
        return None, f"no CHANGELOG heading found in {doc.name}"
    return set(CHANGELOG_EDIT_ID_RE.findall(scope)), None


_DELIVERED_STAMP = re.compile(r"^delivered:\s*(.+?)\s*$", re.M)


def _frontmatter(text):
    """The YAML frontmatter block of a brief, or "" when it has none.

    Scoped deliberately: a brief's BODY is prose that may quote or discuss a delivery
    ("delivered: 2026-08-13" inside a quoted reply, say), and matching there would let
    narrative text move a verdict. A header field is a declaration; a sentence is not."""
    t = (text or "").lstrip()
    if not t.startswith("---"):
        return ""
    end = t.find("\n---", 3)
    return t[3:end] if end != -1 else ""


def _row_for(architect_id, mailboxes):
    """The registry row addressed to `architect_id` (by id or alias), or None."""
    hit = _find_row(architect_id, mailboxes)
    return hit[1] if hit else None


def _tracked_names(directory):
    """{filenames git tracks in `directory`}, or None when git could not tell.

    Answers one question: do the briefs in this directory TRAVEL BETWEEN MACHINES? A
    tracked file is in the repo's history, so every clone has it. An ignored or untracked
    one exists on exactly the machine that wrote it.

    ONE CALL PER DIRECTORY, not per brief. `_registry_staleness` already records why a
    probe that shells out per row has no business on the SessionStart path, and
    `audit_status_line` runs there every session.

    A directory in no git repository at all answers with an EMPTY SET, not None: nothing
    outside git travels by git, so that fact is established rather than unknown. `None` is
    reserved for git genuinely failing to answer, and must never collapse into either
    real answer ([`declare-what-a-check-assumes`])."""
    try:
        r = subprocess.run(["git", "-C", str(directory), "ls-files"],
                           capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    if r.returncode == 0:
        # DIRECT CHILDREN ONLY. `ls-files` reports paths relative to the directory, so a
        # tracked `applied/foo.md` would contribute the bare name `foo.md` and mark an
        # UNTRACKED `foo.md` sitting beside it as travelling — a same-name match one
        # directory down, which is the filename-matching this audit exists to refuse.
        # The callers only ever glob `*.md` at this level, so anything with a separator
        # is a different file.
        return {l.strip() for l in r.stdout.splitlines()
                if l.strip() and "/" not in l.strip()}
    if "not a git repository" in (r.stderr or "").lower():
        return set()
    return None


def _travels(tracked_names, brief):
    """Does OUR copy of `brief` travel between machines? True / False / None.

    `None` in means `None` out — a directory git could not answer for yields no fact about
    the files in it, and inventing `False` there would manufacture the confident verdict
    this whole change exists to remove."""
    return None if tracked_names is None else (brief.name in tracked_names)


def _lazy_travels(cache, directory, brief):
    """A zero-argument probe answering `_travels` only if someone actually asks.

    LAZY BECAUSE IT SHELLS OUT, and `audit_status_line` runs on the SessionStart path.
    `_registry_staleness` below already records the rule this file learned once: a check
    that shells out to git per row has no business at startup. The answer is only ever
    needed on the one branch about to say UNDELIVERED — which is rare, and on the live
    fleet the eager version cost 15 subprocesses and ~90ms every session to change a
    single verdict. Measured before and after, not guessed.

    Memoised per directory, so N briefs sharing a mailbox still cost at most one call."""
    def probe():
        key = str(directory)
        if key not in cache:
            cache[key] = _tracked_names(directory)
        return _travels(cache[key], brief)
    return probe


def _ask(travels):
    """`travels` may be a plain answer or a deferred probe — resolve either.

    Callers inside this module pass the probe (see `_lazy_travels`); a test, or anyone
    who already knows, passes the value directly."""
    return travels() if callable(travels) else travels


def absence_is_not_evidence(row, travels, text=""):
    """Why an absence from the recipient's mailbox proves nothing — or "" when it proves
    something and UNDELIVERED stands.

    THE ASYMMETRY (WI-0205). [ADR-0088](../adr/0088-a-mailbox-is-gitignored-inbound-data-fleet-wide.md) D1
    makes every member's mailbox GITIGNORED fleet-wide, deliberately: a mailbox holds
    letters other people sent you, and tracking it lets anyone who writes to you edit your
    history. The consequence nobody costed is that its contents then exist on exactly one
    machine and never travel.

    A brief WE are holding may have a different reach. One member keeps its outbound mail in
    `outbox/`, which is TRACKED — so its copy is in git and readable from every machine at
    once. Put the two together and the evidence is lopsided: a brief delivered on the runner host
    leaves a sender's copy the development host can read, and a recipient's mailbox it can
    never read. An audit that reads the copy and an empty mailbox reports UNDELIVERED for a
    brief that was delivered. The natural next
    action — deliver it — would have duplicated a brief the recipient already holds.

    So when OUR copy travels and THEIR mailbox does not, a delivery made from any other
    machine leaves both sides looking exactly as they look from here. That is not evidence
    of anything, and the honest verdict is *cannot tell from here*.

    THE CONVERSE IS WHAT KEEPS UNDELIVERED SHARP rather than gutting it. A brief sitting
    in our own gitignored `proposed-edits/*/pending/` is machine-local too — so this
    machine is the only machine that could have sent it, and `_retire_source` would have
    moved it out of `pending/` if it had. There, absence really is evidence, and the
    verdict stands. Today this fires on exactly one brief in the fleet: the one that was
    wrong.

    ONE SEAM, NAMED RATHER THAN CLOSED. [ADR-0107](../adr/0107-mail-transits-origin-and-every-machine-drains-its-own-share.md) D6
    gives the federation's OWN outbox a receipt that does travel: `outbox/to-<X>/` is
    queued and `outbox/delivered/` is delivered, and the move is the record. For mail
    under that convention, queue position is tracked evidence and an absence really would
    mean undelivered — so this rule is more conservative than it needs to be there. It is
    left that way deliberately: the sweep does not currently reach the federation's own
    ADR-0107 outbox at all, one member's `outbox/` runs the OLDER convention (it stamps
    frontmatter and leaves the file in place, so its queue position is not a receipt), and
    being conservative here can only ever understate — *cannot tell* where *undelivered*
    was warranted, never a false accusation. Refine it when a case actually arises, not on
    anticipation ([`add-structural-guard-on-recurrence`])."""
    if travels is False:
        return ""                       # our copy is machine-local; we are the only sender
    if (row or {}).get("tracked") is True:
        # Their mailbox is IN git, so a delivery from anywhere is committed and visible
        # from here. (Writing there is refused for other reasons — ADR-0088 D1 — but that
        # is a rule about writing, and this is a question about reading.)
        return ""
    stamp = _DELIVERED_STAMP.search(_frontmatter(text))
    corroboration = (f" Its own frontmatter records a delivery — `delivered: {stamp.group(1)}` — "
                     f"which is the sender's self-report, not a receipt, and is quoted as "
                     f"corroboration rather than relied on." if stamp else "")
    if travels is None:
        return ("could not tell whether our copy of this brief travels between machines, "
                "so we cannot establish whether its absence from their mailbox means "
                "anything." + corroboration)
    return ("their mailbox is gitignored (ADR-0088 D1), so its state is MACHINE-LOCAL and "
            "never travels — while our copy of this brief is tracked and therefore visible "
            "from every machine. A delivery made from another machine would look exactly "
            "like this from here, so absence is not evidence." + corroboration)


def classify(architect_id, brief, mailboxes, repos, holder="", travels=None):
    """Did `brief` reach `architect_id`? One of the six states, always with a reason.

    THE ORDER IS THE DESIGN. Identity first (`edit-id`), then the sender's own declared
    destination, then the bare filename — strongest evidence to weakest, each reported at
    its own grade and never promoted to the one above it. An id match proves the brief IS
    that file; a declared-name match only corroborates that it is, and collapsing the two
    into one verdict would be exactly the filename-matching this audit was rebuilt to stop
    (module docstring, WI-0101).

    THE INDEX IS NOW BUILT BEFORE THE KEYLESS BRANCH RETURNS (WI-0223). It used to
    short-circuit on a missing edit-id without ever reading the recipient's tree — which
    is why the corroborating evidence sitting in front of it was never consulted. A
    keyless brief and an unreadable tree now resolve in the honest order: an unreadable
    tree is a blocker with a fix, so it is reported ahead of a verdict about content we
    had no way to compare against anyway.

    AND THE LAST STEP ASKS WHOSE DISK THE ANSWER IS ON (WI-0205). `travels` says whether
    OUR copy of this brief is visible from other machines; combined with whether THEIR
    mailbox travels, it decides whether an absence is evidence at all. It may be a plain
    answer or a deferred probe — see `_lazy_travels` for why it is deferred, and
    `absence_is_not_evidence` for the rule."""
    tree, why = recipient_mailbox_tree(architect_id, mailboxes, repos)
    if tree is None:
        return Verdict(architect_id, brief, UNKNOWN, why, holder)
    text = _read_text(brief)
    if text is None:
        return Verdict(architect_id, brief, UNKNOWN, "our own copy is unreadable", holder)
    by_id, by_name = _index_mailbox(tree)
    if by_id is None:
        return Verdict(architect_id, brief, UNKNOWN,
                       f"their mailbox tree ({tree}) could not be read", holder)
    eid = brief_edit_id(text)
    if not eid:
        declared = declared_destinations(text)
        for name in declared:
            if name in by_name:
                return Verdict(architect_id, brief, CORROBORATED,
                               f"declares no edit-id, but its own delivery note names "
                               f"'{name}' — and that file is in their mailbox at "
                               f"{by_name[name]}. A name match under the sender's own "
                               f"pointer corroborates arrival; it does not prove identity",
                               holder)
        if brief.name in by_name:
            return Verdict(architect_id, brief, UNKNOWN,
                           f"declares no edit-id, and a file of the same name is in their "
                           f"mailbox ({by_name[brief.name]}) carrying none either — "
                           f"verify by hand", holder)
        if declared:
            return Verdict(architect_id, brief, UNRESOLVABLE,
                           f"declares no edit-id; its own delivery note names "
                           f"{', '.join(declared)}, and none of those is in their mailbox. "
                           f"A failed corroboration is not a proven non-delivery — a "
                           f"rename on the far side looks identical from here. Remedy: the "
                           f"sender amends their retained copy to declare an `edit-id`",
                           holder)
        return Verdict(architect_id, brief, UNRESOLVABLE,
                       "declares no edit-id and no delivery destination, so there is "
                       "nothing to match on by content and nothing a re-run will change. "
                       "Remedy: the sender amends their retained copy to declare an "
                       "`edit-id`, or delivery is established by hand", holder)
    if eid in by_id:
        return Verdict(architect_id, brief, DELIVERED, str(by_id[eid]), holder)
    if brief.name in by_name:
        # Same name, different (or absent) edit-id. Reporting DELIVERED here would be the
        # filename-matching this audit exists to stop; reporting UNDELIVERED would ignore
        # evidence sitting in front of us. Neither is knowledge.
        return Verdict(architect_id, brief, UNKNOWN,
                       f"a file of the same name is in their mailbox ({by_name[brief.name]}) "
                       f"but carries no matching edit-id — verify by hand", holder)
    # The verdict would be UNDELIVERED. Before asserting it, ask what THIS MACHINE's
    # evidence covers — the mailbox we just read is machine-local by design (WI-0205).
    blind = absence_is_not_evidence(_row_for(architect_id, mailboxes), _ask(travels), text)
    if blind:
        return Verdict(architect_id, brief, OFF_MACHINE, blind, holder)
    return Verdict(architect_id, brief, UNDELIVERED, str(tree), holder)


# ────────────────────────────────────────────────────────────────────────────── audit

def _outbox_dirs(repo):
    """Every `outbox/to-<architect-id>/` directory in a member's tree, as
    (architect_id, dir) pairs.

    THE SECOND CONVENTION (WI-0079). The sweep above knows one shape —
    `proposed-edits/<architect-id>/pending/` — because that is the shape the federation
    ships. one member keeps its outbound mail at `outbox/to-federation-arch/` instead, and a
    sweep that knows only the first shape does not report that surface as unexamined: it
    reports the channel CLEAN, having never looked. That is the same
    certifies-without-looking failure the audit was rebuilt to stop, one directory
    convention over.

    Conventions are enumerated here rather than inferred, so a third one is a visible
    one-line addition instead of a new silent blind spot."""
    box = repo / "outbox"
    if not box.is_dir():
        return []
    out = []
    for d in sorted(box.glob("to-*")):
        if d.is_dir():
            out.append((d.name[len("to-"):], d))
    return out


def audit(mailboxes=None, repos=None):
    """Every brief sitting in the wrong tree, CLASSIFIED against the recipient. Returns
    (outbound, inbound) as lists of `Verdict`.

    The first sweep of this checked only the federation's own tree and its count was
    reported as the answer to "how many others?". The defect is symmetric, and half a
    sweep certifying the channel clean is worse than no sweep — it converts an unknown
    into a false assurance.

      outbound  a brief sitting in OUR `proposed-edits/<someone-else>/pending/`.
      inbound   a brief addressed to someone sitting in a MEMBER's tree — written to the
                sender's own repo instead of the recipient's.

    WHAT CHANGED, AND WHY IT MATTERS. This used to infer the verdict from OUR tree alone:
    presence in our `pending/` printed as "UNDELIVERED — never sent". "We still hold a
    copy" and "they never received it" are different facts, and it reported the first
    while asserting the second. Of five briefs it flagged, four had already been delivered
    and three were sitting in the recipient's `applied/`. The count was not merely wrong
    here — a member's Architect reasonably relayed it to us as fact in a brief of their own, so
    a detector that over-reports propagates into other Architects' findings.

    Each brief is now looked up in the recipient's real mailbox by edit-id, and the result
    is one of three states that are never folded together
    ([`declare-what-a-check-assumes`]): DELIVERED (our copy is a leftover to file),
    UNDELIVERED (absent from a mailbox we could actually read), UNKNOWN (we could not
    tell, and the reason is printed). The old two-state shape had no way to say the third
    thing, which is why "could not tell" came out as "definitely not sent"."""
    mailboxes = load_mailboxes() if mailboxes is None else mailboxes
    repos = locate_repos() if repos is None else repos
    outbound, inbound = [], []
    # Per-run, not module-level: a long-lived process or a test that writes a file and
    # re-audits the same directory must not be answered from a cache built before it.
    tcache = {}

    if FED_PROPOSED.is_dir():
        for pend in sorted(FED_PROPOSED.glob("*/pending")):
            if pend.parent.name == FED_ARCH_ID:
                continue                      # our own inbox — mail that ARRIVED
            for f in sorted(pend.glob("*.md")):
                outbound.append(classify(pend.parent.name, f, mailboxes, repos,
                                         travels=_lazy_travels(tcache, pend, f)))

    for sid, repo in sorted(repos.items()):
        box = repo / "proposed-edits"
        if not box.is_dir():
            continue
        # OUR OWN tree, reached as a member. The federation joined the locator map on
        # 2026-08-13 so briefs could be delivered TO the hub — which silently enrolled us
        # in the inbound sweep as well, and every outbound brief began reporting twice:
        # once as outbound, once as "held in federation's tree". A duplicated row inflates
        # a count that gets relayed onward as fact, which is the failure this audit exists
        # to stop. Compared by RESOLVED path, not `repo == ROOT`: from a worktree lane ROOT
        # is the lane while this resolves to the main checkout, and `proposed-edits` is a
        # symlink into it — so the identity holds from either.
        try:
            if box.resolve() == FED_PROPOSED.resolve():
                continue
        except OSError:
            pass
        own = _own_architect_id(repo) or (mailboxes.get(sid, {}) or {}).get("architect_id")
        seen = set()
        for pend in sorted(box.glob("*/pending")):
            if pend.parent.name == own:
                continue                      # their own inbox — mail that ARRIVED
            # Dedupe by RESOLVED path. A renamed member keeps a compatibility symlink at
            # its old id (a renamed member keeps `proposed-edits/<old-id>-arch -> <new-id>-arch`), so
            # the same directory appears twice under two names and its own mail would be
            # reported as misrouted — twice.
            real = pend.resolve()
            if real in seen:
                continue
            seen.add(real)
            if own and real == (box / own / "pending").resolve():
                continue                      # the same inbox reached by its old name
            for f in sorted(pend.glob("*.md")):
                # Classified against the addressee for the same reason outbound is: a
                # brief in the wrong tree may still have reached its recipient by another
                # route, and "sitting somewhere odd" is not "never arrived".
                inbound.append(classify(pend.parent.name, f, mailboxes, repos, holder=sid,
                                        travels=_lazy_travels(tcache, pend, f)))

        # …and the same tree again under the OTHER convention (WI-0079). Classified by
        # the identical three-state rule, because the question is the same one: a brief
        # in an outbox may well have been delivered and merely not cleared — which is
        # what a file COUNT of this directory would have called a backlog of six.
        for addressee, d in _outbox_dirs(repo):
            for f in sorted(d.glob("*.md")):
                inbound.append(classify(addressee, f, mailboxes, repos, holder=sid,
                                        travels=_lazy_travels(tcache, d, f)))
    return outbound, inbound


class Unreachable(typing.NamedTuple):
    """A recipient nothing could be written to, and the send path's own reason why.

    `by_design` splits a population that used to be one bucket (WI-0205). Both kinds
    genuinely cannot be written to FROM HERE — that part was never wrong. What was wrong is
    that a member the roster deliberately keeps on another machine was reported with the
    same urgency and the same repair advice as a member whose mailbox is broken, and the
    advice (*map it in repo-paths.local*) could only have been followed by inventing a path
    to a repo that is not on this disk.

    So the distinction is not reachable-vs-not, it is DEFECT vs EXPECTED: one is a channel
    failure to fix, the other is the fleet layout working as designed. Folding them made
    the real one — one member's committed symlink loop — the fourth line of four, which
    is how a report trains its reader to skim it."""
    architect_id: str
    system: str
    reason: str
    by_design: bool = False


def reachability(mailboxes=None, repos=None):
    """Every rostered recipient that CANNOT be written to, swept from the registry
    (WI-0129, reported by a member's Architect).

    `audit()` is brief-centric: it enumerates briefs sitting in the wrong tree and
    classifies each against its recipient. That classification is good — an unlocatable
    recipient yields UNKNOWN with the reason printed. But the ENUMERATION is over briefs,
    and an unreachable recipient never acquires one: the send path refuses *before
    writing*, so nothing is left behind to be swept. Stated as a sequence, you could not
    learn a recipient was unreachable until a brief was stuck on them, and delivery
    declined to create that brief.

    It landed on the hub, which is the worst instance of the class: the federation's own
    repo lives outside every walk root, so no brief could reach `federation-arch` from the
    Runner at all — while `--audit` said "1 undelivered" and an operator reasonably read
    the channel as otherwise healthy. Every member's route for a cross-member finding runs
    through the hub.

    Driven by calling the SEND PATH's own `resolve()` per roster row rather than
    re-deriving the conditions here: unknown recipient, tracked mailbox, unreachable on
    record, unlocatable on this machine, mailbox absent on disk. Those five sentences
    already exist and are already the authority on whether a write can happen
    ([P16](../principles/master.md#p16--avoid-duplication)) — a second implementation
    would be free to drift into disagreeing with the thing it describes."""
    mailboxes = load_mailboxes() if mailboxes is None else mailboxes
    repos = locate_repos() if repos is None else repos
    # WI-0132: with no usable search roots, EVERY member resolves as unlocatable and the
    # sweep would report the whole fleet unreachable — a confident answer built out of a
    # config that belongs to another machine. That is the shape this sweep exists to stop,
    # so it must not be the shape the sweep itself produces. Raise, so `--audit` prints
    # NOT CHECKED rather than a list whose cause is not what it says.
    note = roots_diagnosis(read_roots_config(warn=False), ROOTS_CONFIG)
    if note and repos is not None and not repos:
        raise RuntimeError(note)
    machine = this_machine()
    out = []
    for sid, row in sorted(mailboxes.items()):
        aid = (row or {}).get("architect_id")
        if not aid:
            out.append(Unreachable(f"<no architect_id>", sid,
                                   "registry row declares no architect_id, so nothing can "
                                   "be addressed to it"))
            continue
        # BEFORE the send path's refusal, because `resolve()` can only say "not locatable"
        # — it has no way to know whether that absence is a defect or the fleet layout.
        # Both conditions are required: the row must DECLARE another machine, and the repo
        # must actually be missing here. A member that declares `resides: Runner` and is
        # nonetheless sitting on this disk falls through to the normal path, and a member
        # that is missing with NO declaration stays an ordinary unreachable — the gap it is.
        elsewhere = declared_elsewhere(row, machine)
        if elsewhere and _locate(sid, aid, repos, row) is None:
            out.append(Unreachable(
                aid, sid,
                f"lives on {elsewhere}, by declaration ({machine} does not carry this "
                f"repo). Nothing is missing here and there is nothing to map — deliver "
                f"to it from {elsewhere}.", by_design=True))
            continue
        try:
            resolve(aid, mailboxes, repos)
        except ValueError as e:
            out.append(Unreachable(aid, sid, str(e)))
        except Exception as e:                       # never let a probe fail the audit
            out.append(Unreachable(aid, sid,
                                   f"could not be resolved ({type(e).__name__})"))
    return out


# ─────────────────────────────────────────────────────────────────── registry self-probe

def _check_ignore(repo, rel):
    """(tracked?, detail) for a would-be brief at `rel` inside `repo`.

    Asked of git rather than inferred from a `.gitignore` we parsed ourselves —
    `check-ignore` accounts for negations, precedence and nested ignore files, and the
    question that matters is what git will actually do with a brief dropped there.
    Returns None for the verdict when git could not tell, which must never collapse into
    "fine" ([`declare-what-a-check-assumes`])."""
    target = f"{str(rel).rstrip('/')}/probe.md"
    try:
        r = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", target],
                           capture_output=True, timeout=30)
    except Exception as e:
        return None, f"check-ignore failed: {type(e).__name__}"
    # 0 = ignored (safe), 1 = not ignored (tracked-on-write, the harm).
    if r.returncode in (0, 1):
        return (r.returncode == 1), ""
    return None, f"check-ignore exit {r.returncode}: {r.stderr.decode().strip()[:80]}"


def _check_ignore_via_resolved(box):
    """Retry the ignore question in whichever repo owns the mailbox's REAL location.

    Only reached when the direct ask failed, so the cost lands on the exceptional case."""
    try:
        real = box.resolve()
        r = subprocess.run(["git", "-C", str(real), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None, "path crosses a symlink and its target is not in a git repo"
        top = pathlib.Path(r.stdout.strip())
        return _check_ignore(top, real.relative_to(top))
    except Exception as e:
        return None, f"resolved-path retry failed: {type(e).__name__}"


def probe_member(repo, row):
    """Probe ONE mailbox as ADR-0088 D2 requires: by USE, never by `is_dir()`.

    One member's mailbox was a self-referential symlink — it satisfied every presence check and
    failed every write with ELOOP. So reachability here means "we created a file and
    removed it again", and nothing weaker.

    `tracked` is asked of git rather than inferred from a `.gitignore` we parsed
    ourselves: `check-ignore` accounts for negations, precedence and nested ignore files,
    and the question that matters is what git will actually do with a brief we drop there.
    Returns a dict; `unknown` fields are None, never a default that reads as conforming
    ([`declare-what-a-check-assumes`])."""
    out = {"reachable": None, "tracked": None, "tracked_files": None, "detail": ""}
    box = repo / row["mailbox"]
    probe = box / ".mailbox-probe.tmp"
    try:
        probe.write_text("probe", encoding="utf-8")
        out["reachable"] = probe.read_text(encoding="utf-8") == "probe"
        probe.unlink()
    except Exception as e:
        out["reachable"] = False
        out["detail"] = f"{type(e).__name__}: {str(e)[:80]}"
        return out
    verdict, detail = _check_ignore(repo, row["mailbox"])
    if verdict is None:
        # git refuses to answer for a path that crosses a symlink ("is beyond a symbolic
        # link"). A lane's `proposed-edits` IS such a symlink into the main checkout, so
        # the federation's own row — the one mailbox we can always see — was the only one
        # the probe could not read. Ask the repo that actually owns the resolved path.
        verdict, detail = _check_ignore_via_resolved(box)
    out["tracked"], out["detail"] = verdict, (detail if verdict is None else out["detail"])
    if verdict is None:
        out["detail"] = detail
    try:
        top = row["mailbox"].split("/pending")[0]
        r = subprocess.run(["git", "-C", str(repo), "ls-files", top],
                           capture_output=True, text=True, timeout=30)
        out["tracked_files"] = len([l for l in r.stdout.splitlines() if l.strip()])
    except Exception:
        pass
    return out


def probe_registry(mailboxes=None, repos=None):
    """Probe every rostered mailbox and return [(system-id, row, probe, [drift…])].

    WHY THIS IS CODE AND NOT A CHORE. The registry's own `//probe` note says a row older
    than the fleet's last convention change should be re-probed rather than trusted — and
    then the rows were re-probed by hand, once, and every one of them carried the same
    date. Days later some entries were wrong, all stale in the "still broken"
    direction: members had since changed their mailbox setup,
    so the federation was carrying a to-do list of members that had already fixed
    themselves. A record that decides delivery must be able to check itself
    ([`retention-enforced-by-code`] applied to a registry rather than to a log)."""
    mailboxes = load_mailboxes() if mailboxes is None else mailboxes
    repos = locate_repos() if repos is None else repos
    results = []
    for sid, row in mailboxes.items():
        # Our own row last, because the locator only finds OTHER members: it walks for
        # `STATUS.md` under the configured roots and the federation is not among them. So
        # the one mailbox we could always check was the one row the probe reported as
        # unprobeable — a self-check with a hole exactly where it is standing.
        repo = (ROOT if row.get("architect_id") == FED_ARCH_ID
                else repos.get(sid) or _repo_by_architect_id(row.get("architect_id"), repos))
        if repo is None:
            results.append((sid, row, {"reachable": None, "tracked": None,
                                       "tracked_files": None,
                                       "detail": "not locatable from this machine"}, []))
            continue
        p = probe_member(repo, row)
        drift = []
        if p["reachable"] is not None and p["reachable"] != bool(row.get("reachable", True)):
            drift.append(f"reachable: recorded {bool(row.get('reachable', True))}, "
                         f"probed {p['reachable']}")
        if p["tracked"] is not None and p["tracked"] != bool(row.get("tracked")):
            drift.append(f"tracked: recorded {bool(row.get('tracked'))}, "
                         f"probed {p['tracked']}")
        results.append((sid, row, p, drift))
    return results


def _today():
    try:
        import datetime
        import zoneinfo
        tz = json.loads((ROOT / "session.config.json").read_text(
            encoding="utf-8")).get("timezone", "UTC")
        return datetime.datetime.now(zoneinfo.ZoneInfo(tz)).strftime("%Y-%m-%d")
    except Exception:
        import datetime
        return datetime.date.today().isoformat()


def write_probe_results(results):
    """Fold probed truth back into mailboxes.json. Returns the list of changes made.

    Only `reachable` / `tracked` / `verified` are touched, and only where the probe
    actually determined a value — a row we could not probe keeps its recorded value and
    its old `verified` date, so an unprobeable member cannot be silently re-certified as
    current. Hand-written `note` fields are never rewritten: they carry the reasoning a
    probe cannot regenerate (one member's path is non-standard ON PURPOSE, ADR-0088 D2b)."""
    doc = json.loads(MAILBOXES.read_text(encoding="utf-8"))
    today, changes = _today(), []
    for sid, _row, p, _drift in results:
        if p["reachable"] is None and p["tracked"] is None:
            continue
        target = doc["members"][sid]
        for field in ("reachable", "tracked"):
            if p[field] is not None and bool(target.get(field, True)) != p[field]:
                changes.append(f"{sid}.{field}: {target.get(field)} -> {p[field]}")
                target[field] = p[field]
        if p["reachable"] is not None:
            target["verified"] = today
    # Match the file's existing formatting exactly. Writing it back with different indent
    # or escaping turns a one-field update into a 96-line diff, which buries the change
    # that actually happened and makes the registry's history unreadable.
    # ensure_ascii=FALSE, because the file on disk is UTF-8 and full of em-dashes:
    # writing it back escaped would rewrite every note in the registry as `\u2014` soup
    # on the next probe, burying a two-field update in a whole-file diff. Measured, not
    # assumed — the escaped form of today's registry differs from the file by 95 bytes
    # of pure churn.
    MAILBOXES.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    return changes


def _print_probe(write=False):
    results = probe_registry()
    print(f"{'system':26} {'reachable':10} {'tracked':9} {'tracked files':14} detail")
    print("-" * 96)
    drifted = unprobed = 0
    for sid, _row, p, drift in results:
        def show(v):
            return "?" if v is None else ("yes" if v else "no")
        if p["reachable"] is None and p["tracked"] is None:
            unprobed += 1
        if drift:
            drifted += 1
        tf = "—" if p["tracked_files"] is None else str(p["tracked_files"])
        print(f"{sid:26} {show(p['reachable']):10} {show(p['tracked']):9} {tf:14} "
              f"{p['detail']}")
        for d in drift:
            print(f"{'':26} DRIFT — {d}")
    harmful = [sid for sid, _r, p, _d in results if p["tracked"]]
    # Legacy residue is a DIFFERENT problem from a tracked mailbox, and folding them
    # together would hide which one is still accruing. One member's mailbox is ignored and
    # its 5 tracked briefs are historical; another's mailbox is tracked, so its count is
    # growing — 9 on 2026-08-04, 12 later.
    residue = [(sid, p["tracked_files"]) for sid, _r, p, _d in results
               if not p["tracked"] and (p["tracked_files"] or 0) > 0]
    print()
    if harmful:
        print(f"TRACKED mailboxes ({len(harmful)}) — a brief written there is swept into "
              f"that system's history under THEIR authorship (ADR-0088 D1). This is "
              f"ONGOING: the count grows with every brief that arrives.")
        for sid in harmful:
            print(f"  ! {sid}")
    if residue:
        print(f"\nLEGACY TRACKED BRIEFS ({len(residue)} member(s)) — mailbox is ignored "
              f"now, but git keeps honouring files it already tracked. Historical, not "
              f"accruing; ADR-0088 D3 untracks with `git rm --cached`, never a delete:")
        for sid, n in residue:
            print(f"  ~ {sid}: {n} file(s)")
    if unprobed:
        print(f"{unprobed} row(s) NOT PROBED — recorded values left untouched and their "
              f"`verified` date not advanced.")
    if drifted:
        print(f"{drifted} row(s) DRIFTED from what is recorded.")
    if write:
        changes = write_probe_results(results)
        print("\nmailboxes.json updated:" if changes else "\nmailboxes.json: no field "
              "changes; `verified` re-stamped for every probed row.")
        for c in changes:
            print(f"  {c}")
    elif drifted:
        print("Run with --write to fold the probed truth back into mailboxes.json.")
    return 1 if (drifted or harmful) else 0


class Buckets(typing.NamedTuple):
    """Verdicts grouped by state, in report order: what to act on, then what could not be
    determined, then bookkeeping.

    NAMED rather than positional. This was a bare 3-tuple that two callers unpacked by
    position, and WI-0223 added two states to it — the kind of edit that shifts every
    index one place and leaves both reports confidently wrong while still running. The
    next state added must break loudly at a missing attribute, not quietly at an index.

    WI-0205 is the next state, and this is the shape paying for itself: `off_machine` slots
    into the could-not-determine group without moving `leftover`, and `_split` now builds
    the tuple BY KEYWORD so a future insertion cannot silently renumber the rest either."""
    lost: list
    unknown: list
    unresolvable: list
    off_machine: list
    corroborated: list
    leftover: list


def _split(verdicts):
    """Verdicts grouped by state. Every state gets its own bucket — a verdict this
    function cannot place is a verdict the report cannot print, which is how a newly added
    state goes missing rather than goes wrong."""
    def of(state):
        return [v for v in verdicts if v.state == state]

    return Buckets(lost=of(UNDELIVERED), unknown=of(UNKNOWN),
                   unresolvable=of(UNRESOLVABLE), off_machine=of(OFF_MACHINE),
                   corroborated=of(CORROBORATED), leftover=of(DELIVERED))


def _repo_by_architect_id(architect_id, repos):
    """Find a located repo that DECLARES this architect_id, or None.

    The fallback exists because a rename lands in a member's `session.config.json` before
    its `STATUS.md` catches up, and the locator keys on the STATUS id. One member renamed
    itself on 2026-08-03: `mailboxes.json` says its new id, the locator still
    finds the old one, the keys miss each other, and delivery refused a member sitting right
    there on disk. Refusing was correct — far better than writing somewhere plausible —
    but the member IS reachable, and the way to establish that is to ask each repo what it
    calls itself rather than to trust either of our two indexes
    ([`verify-against-self-report-not-mirrors`]).

    Deliberately a FALLBACK, tried only after the registry key misses: the registry is the
    fast, intended path, and scanning every located repo on every delivery would make the
    exceptional case set the cost of the normal one."""
    for repo in repos.values():
        if _own_architect_id(repo) == architect_id:
            return repo
    return None


def _own_architect_id(repo):
    """A repo's OWN declared architect_id, from its `session.config.json`.

    Read from the member rather than from our registry on purpose. One member renamed itself
    and its `STATUS.md` id has not caught up, so the locator finds it under
    the old system id while `mailboxes.json` keys it under the new one — and the registry
    lookup then finds no row, making the member's OWN inbox look misrouted. The system's
    self-report is the authority for what it is called
    ([`verify-against-self-report-not-mirrors`]); the registry is our record of where its
    mailbox lives, which is a different question."""
    try:
        return json.loads((repo / "session.config.json").read_text(
            encoding="utf-8")).get("architect_id")
    except Exception:
        return None


REGISTRY_GRACE_DAYS = 7


def _registry_staleness(grace=REGISTRY_GRACE_DAYS):
    """"registry N days stale" when the oldest row THIS MACHINE CAN PROBE is past the
    grace, else "" — plus a separate clause for rows only another machine can refresh.

    The registry's own note already said a row older than the fleet's last convention
    change should be re-probed rather than trusted — and then nothing checked, so some
    rows sat wrong for days. Re-probing on someone remembering is the chore
    that silently lapses ([`retention-enforced-by-code`]); this is the threshold that
    makes the lapse visible. Deliberately a DATE READ, not a probe: the probe shells out
    to git once per row and has no business on the startup path.

    Never raises — a startup line must never brick startup — but it does not fall silent
    either. UNREADABLE and NEVER-PROBED are reported as distinct states rather than both
    degrading to "", because `load_mailboxes()` returns {} for an absent registry AND for
    a corrupt one, and a silent audit whose registry cannot be parsed is an audit that
    resolves nothing while looking fine. (Caught by its own test: the first cut of this
    function reported a corrupt registry as "never been probed" — this file's whole
    defect class, committed inside the fix for it.)"""
    import datetime
    try:
        rows = list(json.loads(
            MAILBOXES.read_text(encoding="utf-8")).get("members", {}).values())
    except Exception as e:
        return f"mailbox registry UNREADABLE ({type(e).__name__}) — delivery cannot resolve"
    try:
        # A ROW THIS MACHINE CANNOT PROBE MUST NOT DRIVE A "RUN --PROBE" WARNING (WI-0205).
        # `write_probe_results` only advances `verified` where the probe actually
        # determined something, so a declared-elsewhere member's date can never move from
        # here — measured: the runner-resident members were exactly the
        # OLDEST rows, so devbox reported "registry 19d stale, run --probe" and running
        # it would have changed nothing, for ever. An alarm that cannot be cleared by the
        # command it names is how a status line teaches its reader to skip the line.
        machine = this_machine()
        here = [r for r in rows if not declared_elsewhere(r, machine)]
        away = [r for r in rows if declared_elsewhere(r, machine)]
        dates = [r["verified"] for r in here if r.get("verified")]
        if not dates and not here:
            return ("every rostered mailbox lives on another machine — nothing here to "
                    "probe")
        if not dates:
            return "mailbox registry has never been probed"
        today = datetime.date.fromisoformat(_today())
        age = (today - datetime.date.fromisoformat(min(dates))).days
        bits = []
        if age > grace:
            bits.append(f"mailbox registry {age}d stale (`curate/deliver.py --probe`)")
        # Their own clause, never folded into the count above: it is a different fact with
        # a different owner, and the remedy is a machine, not a command.
        away_dates = [r["verified"] for r in away if r.get("verified")]
        if away_dates:
            away_age = (today - datetime.date.fromisoformat(min(away_dates))).days
            if away_age > grace:
                bits.append(f"{len(away_dates)} row(s) last verified {away_age}d ago can "
                            f"only be re-probed from the machine they live on")
        return "; ".join(bits)
    except Exception:
        return "mailbox registry dates unparseable — re-probe"


def production_mail_clause(roots=None):
    """Return `(clause, fault)` for the production queue — `("", False)` off production.

    THE TWO HALVES ARE NOT THE SAME QUESTION, which is why this returns both rather than
    letting the caller infer one from the other. Mail sitting in the queue is VISIBLE
    (the operator should see it) and is not a FAULT (it is in flight, and the next cycle
    is what moves it). Failing an audit on in-flight mail is the crying-wolf failure this
    file warns about in four other places; omitting it is the blind spot WI-0363 recorded.
    A stopped worker, an unreadable observation, a failed cycle, a queue older than its
    own threshold, and any outcome the delivery path could not route are all faults —
    each has a remedy, and none of them clears by waiting.

    EVERY OTHER CLAUSE ON THIS LINE READS THE WRONG PLACE UNDER PRODUCTION. `audit()`
    walks the tracked `outbox/` directories and `channel.waiting()` reads the Runner
    branch; on a cut-over host neither is carrying the mail any more. The mail is in
    `mailqueue`'s lifecycle states and the worker's health file, and both surfaces above
    are blind to them — recorded on WI-0363 for an owner rather than half-built there,
    and owned here. Without this the startup line reports a clean channel that nothing
    is sending through, which is the quietest way for a delivery system to fail.

    THE SOURCE IS DELIBERATELY LOCAL. `mailworker.read_status` reads the health file the
    worker wrote on this host and never touches the network, so this clause still answers
    when the channel whose health is in question is exactly what is unavailable — the
    "local status source independent of the channel" WI-0364 asks for. It is also the
    reason nothing here re-implements a queue read: the worker already owns that, and a
    second reader would be a second answer.

    STALE AND UNKNOWN SUPPRESS THE COUNTS RATHER THAN ACCOMPANYING THEM. A worker that
    stopped an hour ago still has a `pending_count` in its last record, and printing it
    beside "stale" reads as a current measurement taken by something that is not running.
    `read_status` already refuses to let old success become current health; this keeps the
    same discipline one layer out, where the number is what the reader would act on.

    Off production this is "" — `resolve()` returns None for every member and for every
    development checkout, which is all of them until WI-0365 cuts over.
    """
    try:
        roots = roots or production.resolve(ROOT)
    except Exception as e:
        return (f"production mail configuration UNREADABLE ({type(e).__name__}) — the "
                f"queue behind this line cannot be read"), True
    if roots is None:
        return "", False
    try:
        import mailworker
        status = mailworker.read_status(roots)
    except Exception as e:
        # A startup hook must never brick startup, and must never fall silent either:
        # "not checked" and "nothing to report" are different facts (WI-0129's shape).
        return f"production mail worker NOT CHECKED ({type(e).__name__})", True
    state = status.get("status")
    bits = []
    if state == "unknown":
        bits.append(f"production mail worker UNKNOWN — "
                    f"{status.get('failure') or 'no reason recorded'}; queue not counted")
    elif state == "stale":
        age = status.get("observation_age_seconds")
        bits.append(f"production mail worker STALE — last observation "
                    f"{int(age)}s ago, past its {status.get('stale_after_seconds')}s "
                    f"threshold; queue not counted")
    else:
        if state == "failed":
            bits.append(f"last production mail cycle FAILED — "
                        f"{status.get('failure') or 'no reason recorded'}")
        if state == "aged":
            bits.append(f"oldest queued message {int(status['oldest_pending_age_seconds'])}s "
                        f"old, past its {status.get('aged_after_seconds')}s threshold")
        pending = status.get("pending_count")
        if pending:
            bits.append(f"{pending} queued in the production mail queue")
        # Its own clause: these left the pending count because the recipient declared
        # them undeliverable, and that must never read as the queue draining.
        undeliverable = status.get("undeliverable_count")
        if undeliverable:
            reasons = status.get("undeliverable_reasons") or {}
            bits.append(f"{undeliverable} message(s) UNDELIVERABLE"
                        + (f" ({', '.join(f'{k} {v}' for k, v in sorted(reasons.items()))})"
                           if reasons else ""))
        # Its own clause, never folded into the pending count: a message that is merely
        # waiting and an outcome the delivery path could not route are different facts,
        # and only the second one needs a person (WI-0363's `unmatched-ack` lesson).
        attention = status.get("attention") or []
        if attention:
            bits.append(f"{len(attention)} delivery outcome(s) needing attention "
                        f"({', '.join(sorted({str(a.get('outcome')) for a in attention}))})")
    fault = state in ("unknown", "stale", "failed", "aged", "undeliverable") or bool(
        status.get("attention"))
    return "; ".join(bits), fault


def audit_status_line():
    """One-line SessionStart signal; silent only when there is genuinely nothing to say.

    Every state gets its own clause rather than being counted with the undelivered or
    dropped from the total. A startup line that silently omits what it could not check is
    the same over-confident report one layer up — and one that reports an unresolvable
    brief as merely undetermined sends its reader back to a check that will never
    answer ([`declare-what-a-check-assumes`])."""
    try:
        outbound, inbound = audit()
    except Exception as e:
        return f"Delivery audit: NOT CHECKED ({e})."
    b = _split(outbound + inbound)
    try:
        unreachable = reachability()
    except Exception:
        unreachable = []
    # A member the roster deliberately keeps on another machine is not a channel failure
    # and must not inflate a count an operator reads as one (WI-0205). It stays visible in
    # the full `--audit` under its own heading; what it does not do is cry wolf at startup.
    unreachable = [u for u in unreachable if not u.by_design]
    stale = _registry_staleness()
    # Under production this is the only clause on the line reading where the mail
    # actually is; off production it is "" and nothing below changes (WI-0364). The
    # startup line shows everything visible, fault or not — it has no exit code to press
    # with, so the crying-wolf tradeoff the `--audit` verdict makes does not apply here.
    queue, _ = production_mail_clause()
    # WHAT A SEALED MACHINE HAS SENT THAT THIS TRUNK HAS NOT SEEN (WI-0360). The Runner
    # publishes its receipts and queued mail on `origin/runner/mail`, and nothing on the
    # trunk reads that branch — so without this clause a brief can leave the Runner, arrive
    # on origin, and be invisible on the machine the work happens on. Mail that arrives
    # where nobody looks is mail that did not arrive.
    #
    # THIS IS THE WHOLE DEVBOX SIDE, and deliberately a READ rather than a land. The branch
    # is on origin, which is exactly as durable as main for provenance; folding it onto the
    # trunk is about history, not delivery, and belongs to a later lane. It is read HERE
    # because this function is a SessionStart hook — it is the actor that demonstrably runs
    # on devbox, where there is no mail-poller at all. An earlier cut of this wave hung the
    # same job on the poller's trunk-side refresh, which nothing on devbox ever reaches.
    try:
        on_channel = channel.waiting(ROOT)
    except Exception:
        on_channel = []                      # never let a channel read break the audit line
    if not (b.lost or b.unknown or b.unresolvable or b.off_machine or b.corroborated
            or b.leftover or stale or unreachable or on_channel or queue):
        return ""
    bits = []
    # FIRST, because under production it is the clause about live mail and every other
    # clause on this line is about briefs in a tracked directory.
    if queue:
        bits.append(queue)
    if stale:
        bits.append(stale)
    if on_channel:
        bits.append(f"{len(on_channel)} waiting on the Runner channel")
    # Its own clause, never folded into the undelivered count (WI-0129): "N undelivered"
    # and "M recipients cannot be written to at all" are different facts, and the second
    # is the one that makes the first an understatement.
    if unreachable:
        bits.append(f"{len(unreachable)} recipient(s) unreachable")
    if b.lost:
        bits.append(f"{len(b.lost)} undelivered")
    if b.unknown:
        bits.append(f"{len(b.unknown)} undetermined")
    # Its own clause for the same reason unreachable has one (WI-0223): "could not tell
    # this run" and "cannot be told from here at all" are different facts, and only the
    # first is worth coming back to. Folding them made two briefs read as a 44-day
    # backlog item that no re-run was ever going to clear.
    if b.unresolvable:
        bits.append(f"{len(b.unresolvable)} unknowable by construction")
    # And its own clause again, one axis over (WI-0205): "no re-run will ever answer this"
    # and "no re-run HERE will answer this" are different facts with different remedies —
    # the first needs the sender, the second needs the other machine.
    if b.off_machine:
        bits.append(f"{len(b.off_machine)} answerable only from another machine")
    if b.corroborated:
        bits.append(f"{len(b.corroborated)} corroborated by the sender's own note")
    if b.leftover:
        bits.append(f"{len(b.leftover)} delivered leftover(s) to file")
    return (f"Delivery audit: {'; '.join(bits)} — run "
            f"`python3 curate/deliver.py --audit`.")


def _rel(p):
    try:
        return str(pathlib.Path(p).relative_to(ROOT))
    except Exception:
        return str(p)


def _print_audit():
    outbound, inbound = audit()
    b = _split(outbound + inbound)
    try:
        unreachable = reachability()
    except Exception as e:
        unreachable = []
        print(f"REACHABILITY: NOT CHECKED ({type(e).__name__}) — the recipient sweep did "
              f"not run, so nothing below rules out an unwritable recipient.")
    elsewhere = [u for u in unreachable if u.by_design]
    unreachable = [u for u in unreachable if not u.by_design]

    def _elsewhere_section():
        if not elsewhere:
            return
        print("ELSEWHERE BY DESIGN — the roster puts these on another machine, so they "
              "are not deliverable from here and nothing is wrong:")
        for u in elsewhere:
            print(f"  · {u.architect_id:28} ({u.system})")
            print(f"    {u.reason}")
        print()

    # Read BEFORE the clean verdict, not after it. "Clean" here is a claim about the
    # whole channel, and under production the brief sweep above has not looked at the
    # queue that carries the mail — so a stalled worker printed a clean audit (WI-0364).
    queue, queue_fault = production_mail_clause()
    if queue:
        print("PRODUCTION MAIL QUEUE — this host carries mail through the queue, not the "
              "tracked directories the brief sweep below reads:")
        print(f"  {'!' if queue_fault else '·'} {queue}")
        print("    Full local record: python3 curate/mailworker.py --status --json")
        print()

    if not (b.lost or b.leftover or b.unknown or b.unresolvable or b.off_machine
            or b.corroborated or unreachable):
        print("Delivery audit: clean — every brief in both directions is accounted for "
              "in its recipient's own mailbox, and every rostered recipient that lives "
              "on this machine can be written to.")
        if elsewhere:
            print()
            _elsewhere_section()
        # The brief channel is clean; the queue is a separate verdict and keeps its own.
        return 1 if queue_fault else 0

    # THE SCOPE OF EVERYTHING BELOW, stated before the verdicts rather than assumed
    # ([`declare-what-a-check-assumes`]). Every mailbox is gitignored by ADR-0088 D1, so
    # what a mailbox held on another machine is unknowable from here BY CONSTRUCTION —
    # not a limitation of this sweep, a property of the fleet's own design.
    print(f"Evidence scope: {this_machine() or 'this machine (unnamed)'}. Member mailboxes "
          f"are gitignored (ADR-0088 D1), so their contents are MACHINE-LOCAL and a "
          f"delivery made elsewhere leaves no trace this machine can read. DELIVERED and "
          f"UNDELIVERED below are therefore this-machine verdicts; a brief whose own copy "
          f"travels between machines is reported OFF-MACHINE rather than undelivered.")
    print()

    _elsewhere_section()

    if unreachable:
        print("UNREACHABLE — nothing can be delivered to these at all, so they will "
              "never appear as a stuck brief:")
        for u in unreachable:
            print(f"  ✗ {u.architect_id:28} ({u.system})")
            print(f"    {u.reason}")
        print()

    if b.lost:
        print("UNDELIVERED — absent from the recipient's mailbox, which was read:")
        for v in b.lost:
            held = f" (held in {v.holder}'s tree)" if v.holder else ""
            print(f"  → {v.architect_id:28} {_rel(v.path)}{held}")
    if b.unknown:
        print("\nUNDETERMINED — could not tell THIS RUN; fix the named blocker and "
              "re-run:")
        for v in b.unknown:
            held = f" (held in {v.holder}'s tree)" if v.holder else ""
            print(f"  ? {v.architect_id:28} {_rel(v.path)}{held}")
            print(f"    {v.where}")
    if b.unresolvable:
        print("\nUNKNOWABLE BY CONSTRUCTION — no content key exists, so re-running will "
              "never resolve these:")
        for v in b.unresolvable:
            held = f" (held in {v.holder}'s tree)" if v.holder else ""
            print(f"  ∅ {v.architect_id:28} {_rel(v.path)}{held}")
            print(f"    {v.where}")
    if b.off_machine:
        print("\nOFF-MACHINE — the evidence is on another machine's disk, so no re-run "
              "HERE will settle these:")
        for v in b.off_machine:
            held = f" (held in {v.holder}'s tree)" if v.holder else ""
            print(f"  ⇄ {v.architect_id:28} {_rel(v.path)}{held}")
            print(f"    {v.where}")
    if b.corroborated:
        print("\nCORROBORATED — no content key, but the sender's own delivery note names "
              "a file that IS in the recipient's mailbox:")
        for v in b.corroborated:
            held = f" (held in {v.holder}'s tree)" if v.holder else ""
            print(f"  ≈ {v.architect_id:28} {_rel(v.path)}{held}")
            print(f"    {v.where}")
    if b.leftover:
        print("\nDELIVERED — already in the recipient's mailbox; our copy is a leftover "
              "to file, not a backlog:")
        for v in b.leftover:
            print(f"  ✓ {v.architect_id:28} {_rel(v.path)}")
            print(f"    theirs: {v.where}")

    if b.lost:
        print("\nDeliver with: python3 curate/deliver.py <architect-id> <brief-file>")
    # Leftovers alone are bookkeeping, not a channel failure — failing on them is how an
    # audit trains its reader to ignore it. An unreachable recipient IS a channel failure,
    # and the quietest one there is.
    #
    # UNRESOLVABLE fails, CORROBORATED does not (WI-0223). The asymmetry is deliberate and
    # is not about how confident each verdict is — it is about whether anyone can act. An
    # unresolvable brief is a real hole in the channel's evidence AND it has a real remedy
    # the exit code is there to press for: the sender declares an `edit-id` on their
    # retained copy and the hole closes permanently. A corroborated brief is evidence the
    # thing arrived; our copy is a leftover to file, which is bookkeeping. Failing on
    # bookkeeping is how an audit trains its reader to ignore it.
    #
    # OFF_MACHINE does NOT fail, on the same act-or-not test (WI-0205). There is nothing an
    # operator on this machine can do about it and no amount of work here will change it —
    # failing every session on a fact about a different disk is the crying-wolf failure in
    # its purest form. It is printed, counted in the startup line, and left green. A
    # by-design unreachable is excluded above for the same reason.
    return 1 if (b.lost or b.unknown or b.unresolvable or unreachable
                 or queue_fault) else 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--probe" in argv:
        return _print_probe(write="--write" in argv)
    if "--audit" in argv:
        if "--status" in argv:
            line = audit_status_line()
            if line:
                print(line)
            return 0
        return _print_audit()
    replace = "--replace" in argv
    argv = [a for a in argv if a != "--replace"]
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[0])
        print("\nusage: deliver.py <architect-id> <brief-file> [--replace]")
        print("       deliver.py --audit [--status]")
        print("       deliver.py --probe [--write]")
        return 2
    try:
        dest = deliver(argv[0], argv[1], replace=replace)
    except ValueError as e:
        print(f"deliver: {e}")
        return 1
    print(f"delivered: {dest}")
    print("           verified by reading it back — not by the copy not raising.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
