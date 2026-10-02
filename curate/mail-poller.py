#!/usr/bin/env python3
"""Mail poller — drain every outbox from whichever machine can reach the recipient.

THE ONE IDEA. `deliver.py` can only write into a mailbox it can open locally. Rather
than give one machine a network write path into the other's disk, the outbox travels by
`origin` (ADR-0103 D1: "the git remote is the only bridge") and EVERY machine runs this
same poller against it. Each delivers what it can reach and leaves the rest. devbox
drains the development fleet; the Runner drains the members whose repos live only
there.

THERE IS DELIBERATELY NO MACHINE CHECK. The poller does not ask "am I the Runner?" — it
asks the resolver "can I reach this recipient?", which is the question that actually
governs, and answers it with the same code the send path uses. A residency test here
would be a SECOND source of truth about locality that could disagree with the first, and
the whole reason this program exists is that a locality assumption drifted out of date
without anyone noticing.

"NOT LOCATABLE FROM THIS MACHINE" IS A NORMAL OUTCOME, NOT AN ERROR. It is the expected
answer for two thirds of the fleet on any given box. Folding it into the error count
would make every healthy run look broken, and a report that always says "problem" is one
nobody reads. It is counted and named separately.

NEITHER IS "THEY ALREADY HAVE IT". The collision guard on the send path refuses a brief
the recipient already holds, and that refusal is evidence the brief ARRIVED — the audit
already has a name for the state (DELIVERED, our copy a leftover to file). Booking it as
`failed` reported a correctly-delivered brief as a failure on every run, forever, and a
permanently stuck count is worse than a wrong one: it teaches the reader to skip the
number that exists to warn them (WI-0235). It gets its own bucket, its copy is filed to
`delivered/` so the count actually clears, and it never touches the exit code.

EVERY OUTBOX ON THIS MACHINE, NOT JUST OURS (WI-0336). The first cut drained
`outbox.pending()` — the federation's own queue — and other members of the fleet keep
outbound mail in the identical `outbox/to-<id>/` shape. `deliver._outbox_dirs`
already read that surface, but only from `audit()`, which classifies and does not
deliver, so those briefs were reported on every startup and carried by nobody, some of
them for weeks. A defect that leaves its
evidence in plain sight is the slow kind — the channel looked healthy because the mail
was VISIBLE.

AND WE DELIVER THEIR MAIL WITHOUT FILING THEIR COPY. The retire that ends one of our own
deliveries is a move of a TRACKED file; doing it in a member's tree would commit to their
history under our authorship, and `curate/push-substrate.py` already draws exactly this
line and keeps it — the federation may write its own generated substrate into any tree it
reaches, never a target's authored files. A sender's queued brief is theirs. So a
couriered delivery gets its own bucket, changes nothing in the sender's repo, and the
sender's copy becomes what the audit already has a name for: a delivered leftover to
file, by the Architect who owns it.

THAT LEAVES A STANDING COUNT, AND IT IS A CENSUS, NOT AN ALARM. On the next run the
collision guard recognises a couriered brief as one the recipient holds, and since we
still may not move it, `sender_files` reports the same number tomorrow. WI-0235's lesson
was narrower than "no count may persist": what poisoned that report was a permanent `1`
in the FAILED channel, the one number someone watches to notice the mail breaking. This
one is named for what it is, never touches the exit code, and clears when its owner files
it — the same shape the delivery audit's own "delivered leftover(s) to file" line has
carried on the startup banner all along.

AND A DRY RUN THAT CANNOT REFUSE IS NOT A REHEARSAL. The first cut of the member sweep
reported *11 would be delivered* and the live run delivered **none**: `--dry-run` returned
before `deliver.deliver()` and therefore before both of its gates, so it was answering
"is this queued and is the recipient reachable?" while calling the answer a delivery. That
number went into a journal before anyone ran the real thing. The dry run now calls
`deliver.precheck` — the write path's OWN refusals, the same object the live branch calls,
not a second listing of them — and classifies their outcomes identically. It skips exactly
one thing: the write.

AND THE AGREEMENT IS PINNED BY RUNNING BOTH, NOT BY READING THEM (WI-0251). The repair
above was itself re-listed at this call site, and two hand-maintained copies of one
sequence is how the first divergence happened. So the guarantee is now structural (one
`precheck`) and the test runs the identical fixture through `--dry-run` and live and
compares the buckets field by field — including the edit-id and the recipient-side
location an `already_held` verdict cites. A rehearsal that reports the right BUCKET with a
blank citation is the WI-0101 shape: a verdict that cannot say what convinced it.

A BRIEF WHOSE OWN HEADER ROUTES IT TO NOBODY IS NOT A TRANSPORT FAILURE. `_check_apply_mode`
enforces the ADR-0049/ADR-0050 partition, and it refuses the same brief on every run until
its AUTHOR edits the header — which for a couriered brief means an author in another repo.
Eleven such briefs surfaced the moment the sweep widened. Left in `failed` they would have
pinned the alarm channel non-zero forever, on this program's own schedule, which is the
exact harm WI-0235 names; so `malformed` is its own bucket, carries `check-apply.py`'s
report and the holder who must act, and stays out of the exit code. `deliver.MalformedBrief`
is a TYPE for the reason `AlreadyHeld` is.

"ELSEWHERE" MUST NOT SWALLOW "NOWHERE". `elsewhere` means *another machine will carry
this*, and it is a healthy outcome precisely because someone else finishes the job. A
recipient with no row in `mailboxes.json` is UNSURVEYED, not remote: no poller on any
disk can resolve them, so filing them under `elsewhere` hands the brief to a courier who
does not exist — the same "delivered by nobody" that WI-0336 is about, one bucket over.
Told apart by ASKING THE REGISTRY whether a row exists, never by matching `resolve()`'s
prose, for the reason `AlreadyHeld` is a type: a caller that separates outcomes by
reading error text is one wording change from folding them back together.

It does not set the exit code. The remedy is to survey the member and add a row — human
work on the registry, not a transport fault the next poll could clear — and a launchd job
that fails forever until someone does paperwork is the alarm nobody reads again.

IT ALSO TRIGGERS THE DEPLOY SWEEP, AND THAT IS A SECOND JOB IN ONE PROCESS ON PURPOSE
(WI-0316). ADR-0103 D8 wanted a scheduled sweep; ADR-0103 D9 would not let it be installed
until a rollback had been drilled; the drill was an execution on the Runner; and there is
no Runner session, ever, because federation sessions run on devbox only. The gate could
only be opened by a person running a script by hand on a machine no session reaches — the
routed-through-a-human step this whole path exists to delete. This program was already the
way out: it is plain code, it already runs on a schedule, and it already fetches and
fast-forwards the trunk that carries the runner and the registry. So after a refresh that
actually completed, it calls `deploy/runner.py --unattended`, which drills the rollback
path itself the first time and then sweeps. No new launchd unit, no bootstrap, nobody asked.

THE SWEEP IS GATED ON A DECLARED HOST, AND THAT IS NOT THE MACHINE CHECK THIS MODULE
REFUSES TO MAKE. The refusal above is about DELIVERY, where "can I reach this recipient?"
is a better question than "am I the Runner?" and answering it twice is how locality claims
drift. Deploying has no such substitute question: every machine can reach every remote, so
reachability licenses nothing. Worse, the sweep's only gate used to be that
`install-deploy-sweep.sh` was run on the Runner and nowhere else — the launchd job WAS the
machine gate, and routing the trigger through a poller that runs everywhere deletes it.
Ungated here, devbox would clone every production system into ~/deploy, kickstart units
that are not loaded on it, and repoint the federation's own units at a tree it never meant
to serve from. So the runner asks the question, not this module, and answers it from
`channel.writer_label()` — the machine the channel already declares as the one that does
the federation's unattended work. A `deploy_host` registry key was this item's first cut
and was withdrawn before it shipped, because two settings that both say "Runner" today
are two settings that can disagree tomorrow; see ADR-0103 and `unattended_refusal`. It
fails closed both when the declaration is absent and when the machine cannot name itself.

LIVENESS IS PART OF THE PROGRAM, NOT AN AFTERTHOUGHT. An unattended runtime that stops
working tells nobody until a human notices the absence of something
([`scheduled-liveness-smoke-test`](../habits/master.md#scheduled-liveness-smoke-test)),
so every run writes a status file with its outcome and `--status` reads it back, saying
plainly when the last run is too old rather than printing a stale success.
"""
import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import deliver                                                     # noqa: E402
import outbox                                                      # noqa: E402
import channel                                                     # noqa: E402
import production                                                  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAIL_ROOT = ROOT
_PRODUCTION = production.resolve(ROOT)
STATE_DIR = _PRODUCTION.session_state_root if _PRODUCTION else ROOT / ".session-state"

STATUS_FILE = STATE_DIR / "mail-poller.status.json"
LOCK_FILE = STATE_DIR / "mail-poller.lock"

# A run that has not finished in this long is treated as dead rather than live. Generous
# because a delivery is a file copy and a fetch, not a computation: anything approaching
# this is stuck, not slow.
LOCK_STALE_SECONDS = 900

# How old the last run may be before `--status` calls the poller silent. Twice the
# installed StartInterval (600s) plus slack, so one skipped fire is not an alarm and two
# are.
STATUS_STALE_SECONDS = 1500


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt):
    return dt.replace(microsecond=0).isoformat()


def _git(args, check=False):
    """Run one git command with an ARGUMENT LIST, never an interpolated string
    ([`enforce-the-data-code-boundary-where-it-exists`]). Returns (rc, stdout, stderr)."""
    p = subprocess.run(["git", "-C", str(MAIL_ROOT)] + list(args),
                       capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def _tree_is_clean():
    rc, out, _ = _git(["status", "--porcelain"])
    return rc == 0 and out == ""


class Refresh:
    """What `refresh` did, rather than a bare yes/no.

    `ok` is "the refresh path ran to completion without refusing". `advanced` is "the trunk
    actually moved", which `ok` alone cannot tell you: `git merge --ff-only @{u}` exits 0
    both when it fast-forwards and when the branch is already up to date, so the old bare
    `True` was recorded in the status file as `refreshed_trunk` on every quiet run and read
    like news. They are different facts and two callers wanted different ones."""

    def __init__(self, ok, advanced=False, before="", after=""):
        self.ok, self.advanced, self.before, self.after = ok, advanced, before, after


def refresh(log):
    """Fetch, and fast-forward the trunk only when that is provably safe.

    CONSERVATIVE ON PURPOSE. This program runs unattended in a checkout a human may be
    working in. A fetch touches no working file and is always safe; a pull is not. So the
    trunk is advanced only when the tree is clean and the branch can fast-forward, and
    otherwise the run proceeds against whatever is already on disk — which is still
    correct, because delivering a brief that is already staged locally does not need the
    newest trunk. Skipping the refresh costs at most one cycle of latency; yanking a
    checkout out from under someone costs their session.
    """
    if production.resolve(ROOT):
        log("refresh: production remote refs belong to the mail worker")
        return Refresh(False)
    rc, _, err = _git(["fetch", "--quiet", "origin"])
    if rc != 0:
        log(f"refresh: fetch failed ({err.splitlines()[0] if err else 'no detail'}); "
            f"proceeding against the tree on disk")
        return Refresh(False)
    if not _tree_is_clean():
        log("refresh: working tree is dirty — not advancing the trunk. Delivering what "
            "is already staged here.")
        return Refresh(False)
    _, head_before, _ = _git(["rev-parse", "HEAD"])
    rc, _, err = _git(["merge", "--ff-only", "--quiet", "@{u}"])
    if rc != 0:
        log("refresh: trunk could not fast-forward (diverged, or no upstream) — "
            "delivering what is already staged here.")
        return Refresh(False)
    _, head_after, _ = _git(["rev-parse", "HEAD"])
    before, after = head_before.strip(), head_after.strip()
    if before and after and before != after:
        log(f"refresh: trunk advanced {before[:8]} -> {after[:8]}")
    return Refresh(True, advanced=bool(before and after and before != after),
                   before=before, after=after)


def _reachable(architect_id, mailboxes, repos):
    """(True, "") if this machine can deliver to `architect_id`, else (False, why).

    Asks the SEND PATH's own resolver rather than reimplementing reachability, so the two
    can never disagree — a second locality rule is exactly the drift this program exists
    to correct."""
    try:
        deliver.resolve(architect_id, mailboxes, repos)
        return True, ""
    except deliver.RetiredInbox:
        raise                      # its own bucket in run_once, never `elsewhere`
    except Exception as e:
        return False, str(e)


def _on_the_roster(architect_id, mailboxes):
    """Does `mailboxes.json` carry a row for this recipient at all?

    Absent means UNSURVEYED, never "has no mailbox" — `deliver.load_mailboxes` says so,
    and the distinction is the whole reason this is a separate question from reachability.
    """
    return deliver._find_row(architect_id, mailboxes) is not None   # id or alias


def _foreign_queues(repos):
    """[(architect_id, path, holder_sid)] queued in OTHER members' outboxes (WI-0336).

    OUR OWN TREE IS SKIPPED, however the locator reaches it. The federation joined the
    locator map on 2026-08-13 so briefs could be delivered TO the hub, which silently
    enrols us here as a member as well — and sweeping our queue twice would deliver from
    one path and then decline to retire from the other, stranding mail we are entitled to
    file. Identity is the repo's OWN declared `architect_id`, the same authority
    `deliver.audit()` uses for the same question, so a lane worktree and the main checkout
    both answer `federation-arch` and both are skipped; a resolved-path comparison would
    call the main checkout foreign whenever this runs from a lane."""
    out = []
    for sid, repo in sorted(repos.items()):
        if deliver._own_architect_id(repo) == deliver.FED_ARCH_ID:
            continue
        for architect_id, path in outbox.pending_in(repo):
            out.append((architect_id, path, sid))
    return out


def _retired_queues(mailboxes):
    """[(architect_id, path)] left in the federation's own `proposed-edits/<id>/pending/`
    for someone who does not read it there (WI-0012).

    THAT DIRECTORY WAS AN INBOX ONCE. Under read-federation-direct (ADR-0027 §2) a member read
    its briefs straight out of our tree, so writing one there WAS the send. ADR-0028
    converged every member onto its own repo-local inbox, and this poller drains one
    queue, `outbox/to-<id>/`. A brief still written the old way is delivered by nobody,
    and nothing in this report said so. It is named here with the fix instead.

    Two directories in that tree ARE inboxes and are skipped: ours, and any row that
    DECLARES its mailbox is hosted there (`hosted_by`, the consultant). Decided from the
    registry's declaration, never from which directories happen to exist."""
    hosted = {row.get("architect_id") for row in mailboxes.values()
              if row.get("hosted_by")
              and row.get("mailbox") == f"proposed-edits/{row.get('architect_id')}/pending"}
    out = []
    if not outbox.FED_PROPOSED.is_dir():
        return out
    for pend in sorted(outbox.FED_PROPOSED.glob("*/pending")):
        aid = pend.parent.name
        if aid == deliver.FED_ARCH_ID or aid in hosted:
            continue
        for f in sorted(pend.glob("*.md")):
            out.append((aid, f))
    return out


def run_once(dry_run=False, log=print):
    """Deliver every queued brief this machine can reach. Returns a result dict.

    ONE BAD BRIEF NEVER BLOCKS THE QUEUE. Each delivery is attempted independently and a
    failure is recorded against that brief alone — a poller that aborts the run on the
    first error lets one malformed file stop all mail indefinitely, and the symptom (mail
    stops) looks nothing like the cause (one bad file).

    "COULD NOT DELIVER" AND "DID NOT NEED TO" ARE DIFFERENT OUTCOMES (WI-0235). The
    collision guard refuses a brief the recipient already holds, and that refusal is proof
    the brief ARRIVED — but for as long as every raised exception read as `failed`, a
    correctly-delivered brief was reported as a failure on every run, forever. That is not
    untidiness: the failed count is the signal someone watches to notice the channel
    breaking, and a permanently stuck `1` trains the reader to ignore it, so the real
    failure arrives looking identical. `already_held` is its own bucket, and the copy is
    filed to `outbox/delivered/` so the run that reports it is also the run that ends it —
    a fourth state that reported honestly and then repeated forever would fail this item's
    own test just as the third one did."""
    # The registry and the locator are read BEFORE the queue now, because the queue is no
    # longer one directory we own — half of it is discovered by walking the members this
    # machine can see. The old early return on an empty queue went with them; a disk walk
    # every ten minutes is what the audit on the same schedule already costs.
    if production.resolve(ROOT):
        raise ValueError("production delivery requires the dedicated mail worker")
    mailboxes = deliver.load_mailboxes()
    repos = deliver.locate_repos()

    queued = [(architect_id, path, "") for architect_id, path in outbox.pending()]
    queued += _foreign_queues(repos)

    result = {"queued": len(queued), "delivered": [], "elsewhere": [],
              "already_held": [], "couriered": [], "sender_files": [],
              "unroutable": [], "malformed": [], "retired": [], "failed": []}
    # Named BEFORE the empty-queue return: a run with nothing in the outbox is exactly
    # the run in which a brief written the old way would otherwise go unmentioned.
    for architect_id, path in _retired_queues(mailboxes):
        result["retired"].append({
            "architect_id": architect_id, "brief": path.name, "holder": "",
            "why": f"left in the federation's proposed-edits/{architect_id}/pending/, the "
                   f"retired read-federation-direct inbox (ADR-0028); nothing reads it. "
                   f"Stage it: python3 curate/outbox.py stage {architect_id} {path}"})
    if not queued:
        return result

    for architect_id, path, holder in queued:
        if not _on_the_roster(architect_id, mailboxes):
            result["unroutable"].append({"architect_id": architect_id,
                                         "brief": path.name, "holder": holder})
            continue
        try:
            ok, why = _reachable(architect_id, mailboxes, repos)
        except deliver.RetiredInbox as e:
            # Not `elsewhere`: no other machine will carry this either, because the row
            # itself points at a directory nobody reads.
            result["retired"].append({"architect_id": architect_id,
                                      "brief": path.name, "holder": holder,
                                      "why": str(e)})
            continue
        if not ok:
            result["elsewhere"].append({"architect_id": architect_id,
                                        "brief": path.name, "holder": holder,
                                        "why": why})
            continue
        try:
            if dry_run:
                # THE write path's own refusals, not a copy of them. `deliver.precheck` is
                # everything `deliver.deliver` does before it writes; the live branch below
                # calls it too, so the rehearsal cannot drift from the run. Re-listing the
                # gates here — which is what the first repair did — leaves two sequences to
                # keep in agreement by hand, in the one program whose subject is a
                # rehearsal that stopped agreeing (WI-0251).
                deliver.precheck(architect_id, path, mailboxes, repos)
                dest = "(dry-run)"
            else:
                dest = deliver.deliver(architect_id, path, mailboxes, repos)
        except deliver.MalformedBrief as e:
            # Caught FIRST: it subclasses ValueError like `AlreadyHeld`, and the order of
            # these three handlers is the classification.
            result["malformed"].append({"architect_id": architect_id,
                                        "brief": path.name, "holder": holder,
                                        "detail": e.detail})
            continue
        except deliver.AlreadyHeld as e:
            # Caught BEFORE the blanket handler, and it must stay there: `AlreadyHeld`
            # subclasses ValueError precisely so no other caller had to change, which is
            # the same reason an ordering slip here would silently restore the defect.
            if holder:
                # Their brief, their copy. We already carried it; the recipient holds it;
                # filing what is left is a write in the sender's tree and not ours to make.
                result["sender_files"].append({"architect_id": architect_id,
                                               "brief": path.name, "holder": holder,
                                               "edit_id": e.edit_id, "where": e.where})
                continue
            filed = "(dry-run)" if dry_run else outbox.retire(path)
            result["already_held"].append({"architect_id": architect_id,
                                           "brief": path.name,
                                           "edit_id": e.edit_id,
                                           "where": e.where,
                                           "filed": str(filed)})
            log(f"  already held {architect_id} <- {path.name} (edit-id "
                f"{e.edit_id or 'unstated'} at {e.where or 'their tree'}) — delivered, "
                f"our copy "
                f"{'WOULD BE filed' if dry_run else 'filed'} to outbox/delivered/")
            continue
        except Exception as e:
            result["failed"].append({"architect_id": architect_id,
                                     "brief": path.name, "holder": holder,
                                     "error": f"{type(e).__name__}: {e}"})
            log(f"  FAILED {architect_id} <- {path.name}: {e}")
            continue
        if holder:
            result["couriered"].append({"architect_id": architect_id,
                                        "brief": path.name, "holder": holder,
                                        "dest": str(dest)})
            log(f"  couriered {architect_id} <- {path.name} (from {holder}'s outbox; "
                f"their queued copy is theirs to file)")
            continue
        if dry_run:
            result["delivered"].append({"architect_id": architect_id,
                                        "brief": path.name, "dest": str(dest)})
            continue
        outbox.retire(path)
        result["delivered"].append({"architect_id": architect_id,
                                    "brief": path.name, "dest": str(dest)})
        log(f"  delivered {architect_id} <- {path.name}")
    return result


def persist(result, log):
    """Commit whatever is staged under the outbox and push, PATHSPEC'D to it and nothing
    else. The commit/push itself is `outbox.publish` — one implementation, shared with the
    unattended writer that also posts here (WI-0230), so a fix to the push retry cannot
    land on only one of them.

    Bookkeeping must never undo a delivery that already happened, so every failure here
    is logged and swallowed: the mail is in the recipient's mailbox either way, and the
    move to `delivered/` is re-derivable on the next run. A blanket `add` would sweep up
    whatever else is in the tree, which is not this program's to commit (ADR-0091).

    An `already_held` retire is committed on exactly the same footing as a delivery. It
    moved a tracked file, and a retire that never lands is a retire the next clone has not
    got — which is how the brief comes back and the stuck count with it."""
    # `couriered` and `sender_files` are deliberately absent from the MESSAGE. Those briefs
    # live in another member's repo, this commit is scoped to OUR `outbox` pathspec, and
    # nothing in their tree was touched — so there is nothing of theirs to record here, and
    # a commit that named them would claim a write we did not make (WI-0336).
    moved = result["delivered"] + result.get("already_held", [])
    n_d, n_h = len(result["delivered"]), len(result.get("already_held", []))
    what = " and ".join(b for b in (f"deliver {n_d} brief(s)" if n_d else "",
                                    f"file {n_h} already-held brief(s)" if n_h else "")
                        if b)
    if moved:
        names = ", ".join(sorted({d["architect_id"] for d in moved}))
        subject = f"chore(outbox): {what} for {names}"
    else:
        # WI-0230. This used to return here when nothing had MOVED, and the gap that left
        # is the reason a machine-written brief could never leave the machine: a receipt
        # queued for a recipient this host cannot reach is booked `elsewhere`, moves
        # nothing, and was therefore never committed — while sitting in the tree as the
        # dirt that stops `refresh` fast-forwarding on every later run. The commit this
        # program owes is "the outbox is tracked and must not be left uncommitted", which
        # is a fact about the DIRECTORY, not about whether we happened to deliver today.
        subject = "chore(outbox): commit queued mail this machine cannot deliver"
    body = ("Written by curate/mail-poller.py on this machine. Retired deliveries move to "
            "outbox/delivered/; an already-held brief is one the recipient's mailbox or "
            "CHANGELOG already records — delivered, and our copy a leftover to file, not "
            "a delivery that failed. Anything else staged here is queued mail no host "
            "reachable from this one can take, committed so it travels by origin instead "
            "of waiting in a dirty tree.")
    outbox.publish(subject, body, log=lambda m: log(f"persist: {m}"))


# A sweep that has not finished in this long is abandoned. The budget is set from the
# POLLER's lock, not from what a deploy might want: the sweep runs inside that lock, the
# lock goes stale at 900s, and a run that outlived its own lock would be overtaken by the
# next fire — two sweeps on one deploy tree, which is the one outcome worth a hard stop.
SWEEP_TIMEOUT_SECONDS = 480


def sweep_deploys(log, dry_run=False):
    """Hand the deploy runner its scheduled turn. Returns a record, never raises.

    ORDERED AFTER DELIVERY, DELIBERATELY. Mail is this program's job and a deploy is its
    rider; a sweep that clones and smoke-tests every deployed system must not be able to delay a
    brief that was already staged and ready to hand over.

    ITS FAILURES DO NOT SET THIS PROGRAM'S EXIT CODE, for the reason the buckets below
    don't either: the launchd alarm channel is about whether MAIL is moving, and a deploy
    that refuses would otherwise leave the job red for days over something a re-run cannot
    clear. It is recorded in the status file and printed unconditionally instead — quiet
    is for routine outcomes, and a refused deploy is not one."""
    cmd = [sys.executable, str(ROOT / "deploy" / "runner.py"), "--unattended"]
    if dry_run:
        cmd.append("--dry-run")
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           timeout=SWEEP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        log(f"sweep: ABANDONED after {SWEEP_TIMEOUT_SECONDS}s — the deploy runner did not "
            f"finish inside its budget")
        return {"ran": True, "rc": None, "detail": "timed out"}
    except OSError as e:
        log(f"sweep: could not start the deploy runner ({e.__class__.__name__}: {e})")
        return {"ran": False, "rc": None, "detail": f"{e.__class__.__name__}: {e}"}
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    for line in out.splitlines():
        log(f"  sweep: {line}")
    return {"ran": True, "rc": r.returncode, "detail": out[-2000:]}


def write_status(result, refreshed, sweep=None):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_run": _iso(_now()),
        "machine": os.uname().nodename,
        "refreshed_trunk": refreshed.ok,
        "trunk_advanced": refreshed.advanced,
        "deploy_sweep": sweep,
        "queued": result["queued"],
        "delivered": len(result["delivered"]),
        "couriered": len(result.get("couriered", [])),
        "elsewhere": len(result["elsewhere"]),
        "already_held": len(result.get("already_held", [])),
        "sender_files": len(result.get("sender_files", [])),
        "unroutable": len(result.get("unroutable", [])),
        "malformed": len(result.get("malformed", [])),
        "retired": len(result.get("retired", [])),
        "failed": len(result["failed"]),
        "detail": result,
    }
    tmp = STATUS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(STATUS_FILE)                       # atomic: a torn status file reads as
    return payload                                 # corrupt and that is worse than stale


def read_status():
    """(payload, verdict). Verdict distinguishes NEVER RUN from STALE from OK — folding
    the first two together is how a poller that never started reads as merely quiet."""
    if not STATUS_FILE.is_file():
        return None, ("NEVER RUN", "no status file — this poller has not completed a "
                                   "single run on this machine")
    try:
        payload = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, ("UNREADABLE", f"status file present but unreadable ({e.__class__.__name__}) "
                                    f"— treat as NOT checked")
    try:
        last = datetime.datetime.fromisoformat(payload["last_run"])
    except (KeyError, ValueError):
        return payload, ("UNREADABLE", "status file has no readable last_run timestamp")
    age = (_now() - last).total_seconds()
    if age > STATUS_STALE_SECONDS:
        return payload, ("STALE", f"last run was {int(age)}s ago (>{STATUS_STALE_SECONDS}s) "
                                  f"— the schedule is not firing")
    return payload, ("OK", f"last run {int(age)}s ago")


class _Lock:
    """Refuse to run two pollers at once, and never wedge on a dead one.

    A stale lock is RECLAIMED rather than respected: the failure mode of honouring a
    lock left by a killed process is that mail silently stops forever, which is strictly
    worse than the double-delivery this guards against — and double delivery is already
    guarded independently by `changelog_edit_ids` on the send path."""

    def __init__(self, path=LOCK_FILE):
        self.path = path
        self.held = False

    def __enter__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                age = _now().timestamp() - self.path.stat().st_mtime
            except OSError:
                age = LOCK_STALE_SECONDS + 1
            if age < LOCK_STALE_SECONDS:
                raise RuntimeError(
                    f"another poller holds {self.path.name} ({int(age)}s old). Not "
                    f"running. This is normal if a run is in flight.")
        self.path.write_text(str(os.getpid()), encoding="utf-8")
        self.held = True
        return self

    def __exit__(self, *exc):
        if self.held:
            try:
                self.path.unlink()
            except OSError:
                pass
        return False


def _print_status():
    payload, (verdict, why) = read_status()
    print(f"mail-poller: {verdict} — {why}")
    if payload:
        print(f"  last run   {payload.get('last_run')} on {payload.get('machine')}")
        print(f"  queued {payload.get('queued')} · delivered {payload.get('delivered')} "
              f"· couriered {payload.get('couriered', 0)} · elsewhere "
              f"{payload.get('elsewhere')} · already held "
              f"{payload.get('already_held', 0)} · sender's to file "
              f"{payload.get('sender_files', 0)} · unroutable "
              f"{payload.get('unroutable', 0)} · malformed "
              f"{payload.get('malformed', 0)} · retired path "
              f"{payload.get('retired', 0)} · failed {payload.get('failed')}")
    return 0 if verdict == "OK" else 1


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Deliver queued outbox briefs this machine can reach.")
    ap.add_argument("--dry-run", action="store_true",
                    help="say what would be delivered; write nothing")
    ap.add_argument("--status", action="store_true",
                    help="read back the last run's outcome and whether it is recent")
    ap.add_argument("--no-refresh", action="store_true",
                    help="skip the fetch/fast-forward; deliver what is staged on disk")
    ap.add_argument("--no-deploy-sweep", action="store_true",
                    help="deliver mail only; do not give the deploy runner its turn")
    ap.add_argument("--quiet", action="store_true", help="only report work done")
    args = ap.parse_args(argv)

    if production.resolve(ROOT):
        import mailworker
        if args.dry_run or args.no_refresh:
            ap.error("production uses the mail worker; use --status for a read-only check")
        worker_args = (["--status"] if args.status else []) + (["--quiet"] if args.quiet else [])
        return mailworker.main(worker_args)

    if args.status:
        return _print_status()

    def log(msg):
        if not args.quiet:
            print(msg)

    try:
        with _Lock():
            refreshed = Refresh(False) if args.no_refresh else refresh(log)
            result = run_once(dry_run=args.dry_run, log=log)
            if not args.dry_run:
                persist(result, log)
            # INSIDE THE LOCK, which is what makes the sweep single-flight for free: two
            # pollers cannot run two sweeps over one deploy tree. It is also why the sweep
            # carries its own timeout — see SWEEP_TIMEOUT_SECONDS.
            #
            # ONLY AFTER A REFRESH THAT COMPLETED. `ok` and not `advanced` is the right
            # test and the difference matters: the promotion is a TAG in the member's own
            # remote (ADR-0103 D2), which arrives whether or not the federation trunk moved,
            # so sweeping only on `advanced` would couple every member's release to an
            # unrelated commit here. What `ok` rules out is the case that actually matters
            # — a stale, dirty or diverged checkout deploying from runner code and a
            # registry that are not the trunk's.
            if args.no_deploy_sweep:
                sweep = None
            elif not refreshed.ok:
                log("sweep: skipped — the trunk was not refreshed, so the runner and "
                    "registry on disk may not be the trunk's.")
                sweep = {"ran": False, "rc": None, "detail": "refresh did not complete"}
            else:
                sweep = sweep_deploys(log, dry_run=args.dry_run)
            if not args.dry_run:
                write_status(result, refreshed, sweep)
    except RuntimeError as e:
        print(f"mail-poller: {e}", file=sys.stderr)
        return 0                      # a concurrent run is not a failure of this one

    d, el, f = (len(result["delivered"]), len(result["elsewhere"]), len(result["failed"]))
    h = len(result.get("already_held", []))
    c, sf = len(result.get("couriered", [])), len(result.get("sender_files", []))
    u, m = len(result.get("unroutable", [])), len(result.get("malformed", []))
    rt = len(result.get("retired", []))
    if d or c or h or f or u or m or rt or not args.quiet:
        print(f"mail-poller: {result['queued']} queued · {d} delivered · {c} couriered "
              f"for members · {el} not reachable from here · {h} already held · {sf} "
              f"sender's copy to file · {u} unroutable · {m} malformed · {rt} on a "
              f"retired path · {f} failed")
    if sweep and sweep.get("ran") and sweep.get("rc") not in (0, None):
        # Printed past --quiet for the same reason `unroutable` and `malformed` are: it is
        # not a routine outcome, and the one reader is a log nobody opens unless something
        # in it says to.
        print(f"  DEPLOY SWEEP: exit {sweep['rc']} — the scheduled deploy did not come "
              f"back clean. It does not affect this run's mail. See "
              f"`deploy/runner.py --status`.")
    if el and not args.quiet:
        for item in result["elsewhere"]:
            held = f" (from {item['holder']})" if item.get("holder") else ""
            print(f"  elsewhere: {item['architect_id']} <- {item['brief']}{held}")
    if u:
        for item in result["unroutable"]:
            held = f" (from {item['holder']})" if item.get("holder") else ""
            print(f"  UNROUTABLE: {item['architect_id']} <- {item['brief']}{held} — no "
                  f"row in mailboxes.json, so NO machine can deliver this. Survey the "
                  f"member and add a row (`curate/deliver.py --probe`).")
    if m:
        for item in result["malformed"]:
            whose = item["holder"] or "ours"
            print(f"  MALFORMED: {item['brief']} -> {item['architect_id']} (author: "
                  f"{whose}) — its apply header routes it to nobody, so no run will ever "
                  f"deliver it. The fix is a header edit by its author.")
    if rt:
        for item in result["retired"]:
            print(f"  RETIRED PATH: {item['brief']} -> {item['architect_id']} — "
                  f"{item['why']}")
    if sf and not args.quiet:
        for item in result["sender_files"]:
            print(f"  {item['holder']}'s copy of {item['brief']} is a leftover to file — "
                  f"{item['architect_id']} holds it at {item['where'] or 'their tree'}")
    # None of `already_held`, `sender_files`, `unroutable` or `malformed` sets the exit code. Nothing
    # failed in the first two: one shortened our own queue, the other names a brief that
    # ARRIVED whose sender's copy is not ours to move. The third IS a real gap, but its
    # remedy is a registry row a person has to add, so a non-zero exit would leave the
    # launchd job failing for days on end — the alarm channel WI-0235 exists to keep clean.
    # `malformed` is the same argument with a different owner: the header edit that clears
    # it belongs to the brief's author, who for a couriered brief is in another repo, so no
    # re-run here can ever change the outcome. Both print unconditionally instead, `--quiet`
    # included, and both name the fix. `retired` (WI-0012) is the same again: a brief
    # written to the read-direct location, or a row pointing into it, is cleared by a
    # person staging the brief or fixing the row, not by the next poll. What is left in `failed` is what a re-run might
    # actually clear: an unreadable tree, a name collision, a write that did not read back.
    return 1 if f else 0


if __name__ == "__main__":
    sys.exit(main())
