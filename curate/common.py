"""Shared mechanics for the federation-only curate scripts (P16 avoid-duplication).

Hosts the pieces reconcile.py / push-substrate.py / gather.py previously carried
as verbatim copies or should have shared from the start:

  - read_roots_config: the gitignored machine-local search-roots reader (P3),
    previously duplicated between reconcile.py and push-substrate.py.
  - walk_tree: a depth-bounded, pruned directory walk. rglob does not prune —
    it enumerates the whole tree and filters afterwards, so one deep
    node_modules/venv under a configured root turned the SessionStart reconcile
    hook into a minutes-long crawl (worse over a network mount). os.walk with in-place
    dirnames pruning never descends past the depth bound or into heavy dirs.
  - atomic_write: same-directory temp file + os.replace, so a crash mid-write
    can never truncate the target. session.py carries its own copy deliberately:
    it ships standalone to every Architect and cannot import federation-only
    modules; this one serves the curate-side scripts.
  - shared_work_root: mirrors session.py's `_shared_work_root()` for the curate
    scripts, which resolve their `FED_ROOT` from `__file__` — correct in the main
    checkout, silently wrong inside a `poga` lane (a lane's checkout carries the
    same `curate/*.py`, so `__file__` resolves to the LANE, not the main
    checkout). `reconcile.py --status` runs as a SessionStart hook, before
    `session.py`'s own `_link_shared_data()` has materialized (that happens at
    the first heartbeat, per ADR-0055 lazy start) — so every lane's FIRST session
    read "no roots" even though the very next tool call would have fixed it by
    symlinking. Resolving the git common dir directly removes the race instead of
    outrunning it.

Federation-only, like everything else in curate/ — not shipped to fresh
Architects.
"""

import json
import os
import pathlib
import subprocess
import sys
import time

# Never descended into: dependency/venv/cache trees that can be arbitrarily deep
# and can never contain a managed system's repo root.
PRUNE_DIRS = {"node_modules", "__pycache__", ".venv", "venv", ".Trash"}


def roots_diagnosis(roots, config_path):
    """"" when the roots are usable, else a sentence saying they are not (WI-0132).

    DECLARED-BUT-NONE-EXIST is a distinct state from DECLARED-NOTHING, and folding them
    together is what let a whole fleet read as unreachable. Observed on a machine that
    reads another machine's repo through a shared mount: the roots config is machine-local
    by design — its own first line says whose view it is — but it lives at one path inside
    a repo the two machines SHARE, so the second machine read the first one's absolute
    paths, none of which exist there. Every root was skipped, the reader returned nothing,
    and `deliver --audit` went on to report every recipient unreachable: a confident
    answer built entirely out of a config that could not be read from where it was
    standing.

    The machine-scoped-filename fix is deferred (nothing fleet-wide runs on the machine
    that hit it, and the machine roles were about to change). What is NOT
    deferred is this: a tool whose entire input evaporated must say so rather than answer
    ([`declare-what-a-check-assumes`]). That failure is machine-independent — a renamed
    root, a mid-migration checkout, or a new machine's first day before its roots exist
    all produce it."""
    if not roots:
        return ""                      # declared nothing — a different, honest state
    if any(pathlib.Path(r).expanduser().is_dir() for r in roots):
        return ""
    return (f"NO USABLE SEARCH ROOTS — all {len(roots)} path(s) in {config_path} are "
            f"missing on this machine, so nothing below is a measurement of the fleet. "
            f"Most likely this config belongs to a DIFFERENT machine (it is per-machine "
            f"by design, and lives in a repo that may be shared).")


def read_roots_config(config_path, warn=True):
    """Return the search roots from the gitignored machine-local config, or [].

    One path per line; blank lines and `#` comments ignored. The config keeps
    machine-specific paths out of tracked files (P3).

    Warns on stderr when the config named roots and NONE of them exist (WI-0132). The
    warning lives here, at the one choke point every roots-driven tool passes through —
    reconcile, deliver, push-substrate, standard_version, metrics, adopt-runner and
    gen_registry all call this — because a check each caller must remember to make is a
    check that gets forgotten by the next caller added. The return value is unchanged, so
    nothing that reads roots today behaves differently; `warn=False` is for callers that
    want the fact without the print (and for tests)."""
    config_path = pathlib.Path(config_path)
    if not config_path.exists():
        return []
    roots = []
    for line in config_path.read_text(errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            roots.append(line)
    if warn:
        note = roots_diagnosis(roots, config_path)
        if note:
            print(f"  {note}", file=sys.stderr)
    return roots


def read_repo_paths_config(config_path):
    """Return an ordered {system-id: repo-path} map from a gitignored machine-local
    config, or {}.

    Format: one `<system-id> = <absolute repo path>` per line; split on the FIRST
    `=` so paths may contain spaces and `=` (cloud-synced folder paths often do — e.g.
    `.../Synced Folder/Some Project`). Blank lines and `#` comments ignored.
    Keys and values are stripped. A later line for the same id wins.

    This is the LOCATOR for `reconcile.py`: it maps a managed system's id to where
    its live repo actually lives on THIS machine, so a system whose repo is not
    under a walk root (e.g. a member whose repo lives in a cloud-synced folder) still reconciles — and UNRECONCILED
    can mean "not located here," never "not onboarded." Machine-specific paths stay
    out of tracked files (P3), like `reconcile-roots.local`.
    """
    config_path = pathlib.Path(config_path)
    if not config_path.exists():
        return {}
    out = {}
    for line in config_path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        sid, _, path = line.partition("=")
        sid, path = sid.strip(), path.strip()
        if sid and path:
            out[sid] = path
    return out


def walk_tree(base, max_dir_depth):
    """Yield (dirpath, dirnames, filenames) for directories at depth 0..max_dir_depth
    below `base` (base itself is depth 0), pruning BEFORE descent.

    Dot-directories (including .git — callers key on its *presence* in dirnames,
    never its contents) and PRUNE_DIRS are never descended into. The yielded
    dirnames list is a snapshot for inspection; mutation happens on the live list
    after the yield, which is what actually stops os.walk from descending.
    """
    base = os.fspath(base)
    base_depth = base.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(base):
        depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
        yield dirpath, list(dirnames), filenames
        if depth >= max_dir_depth:
            dirnames[:] = []
        else:
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".") and d not in PRUNE_DIRS
            ]


def shared_work_root(fed_root):
    """The MAIN checkout's working-tree root — where gitignored machine-local config
    (`reconcile-roots.local`, `repo-paths.local`) physically lives.

    `fed_root` is the caller's own best guess (typically `Path(__file__).resolve().
    parent.parent`), correct in the main checkout and silently wrong inside a `poga`
    lane. Resolved via the git COMMON dir, shared by every lane and the main
    checkout alike (the same trick session.py's `_shared_work_root()` and the C4/C5
    coordination store use) — so this is right BEFORE any lane-local symlinking has
    happened, not just after. Falls back to `fed_root` on any git error or outside a
    git repo, so a non-git layout degrades to today's behaviour rather than
    breaking."""
    fed_root = pathlib.Path(fed_root)
    try:
        r = subprocess.run(
            ["git", "-C", str(fed_root), "rev-parse", "--path-format=absolute",
             "--git-common-dir"],
            capture_output=True, text=True, check=False)
    except OSError:
        return fed_root
    if r.returncode != 0 or not r.stdout.strip():
        return fed_root
    parent = pathlib.Path(r.stdout.strip()).resolve().parent
    return parent if (parent / ".git").exists() else fed_root


def member_config_root(fed_root):
    """Machine-local member lookup files: explicitly external in production."""
    import production
    roots = production.resolve(fed_root)
    return roots.config_root if roots else shared_work_root(fed_root)


def tree_provenance(path):
    """WHICH tree a read came from, and at WHAT commit — the ADR-0108 D2 helper.

    `shared_work_root()` above answers *where* to read and is deliberately left alone:
    most of its callers render nothing, so widening its return type to serve the few
    that do report would churn every call site to fix a reporting defect. This is the
    other half — a path in, a provenance record out — and the CALLER renders it.

    The defect it exists to close (WI-0236, instance 2): `reconcile.py` reads
    `portfolio.md` through `shared_work_root()`, i.e. from the MAIN checkout, so a
    roster row applied and verified inside a lane read back as *off-roster*. The
    verdict was not wrong about the file it read; it was silent about which file that
    was. Note the direction is not fixed — the same session also had a lane reading
    its OWN frozen copy while main was newer — so no anchoring rule can fix this and
    naming the source is the only honest move.

    Three outcomes, never two: read with a commit, read without one, unreadable.
    Returns `{"path", "sha", "readable"}` where `readable` is False only when the tree
    itself could not be interrogated."""
    path = pathlib.Path(path)
    try:
        r = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False)
    except OSError:
        return {"path": path, "sha": None, "readable": False}
    if r.returncode != 0:
        # An existing directory that git will not answer for is NOT the same as one
        # whose commit is merely unknown, and neither is "fine".
        return {"path": path, "sha": None, "readable": path.exists()}
    sha = r.stdout.strip() or None
    return {"path": path, "sha": sha, "readable": True}


def provenance_clause(path):
    """`tree_provenance` rendered as the clause a verdict carries (ADR-0108 D1).

    Deliberately a fragment rather than a sentence, so a caller appends it to its own
    verdict instead of emitting a second line the reader has to join up."""
    p = tree_provenance(path)
    if not p["readable"]:
        return f"source UNREADABLE ({p['path']})"
    if not p["sha"]:
        return f"per {p['path']}, commit UNKNOWN"
    return f"per {p['path']} at {p['sha'][:7]}"


# --- remote-tracking refs: the THIRD kind of source (WI-0283) ------------------
#
# ADR-0108 made a read name the TREE and COMMIT it came from, and `tree_provenance`
# above answers for a working tree. `origin/<branch>` is neither of the two things
# that rule reaches, which is exactly why it slipped through it:
#
#   * it is NOT the remote. It is a local ref, as old as the last SUCCESSFUL fetch —
#     and a fetch that FAILS leaves it unchanged rather than unreadable, so every
#     probe against it keeps answering, in the old shape, with no error at all.
#   * it is NOT your tree either. The lanes here share one git common dir (ADR-0060,
#     ADR-0062), so a SIBLING's push moves `refs/remotes/origin/*` underneath you.
#     The ref can be FRESHER than anything you did.
#
# So its freshness is uncorrelated with your own connectivity in BOTH directions, and
# "ahead/behind origin" is a reading whose timestamp belongs to whoever last touched
# the ref — which may be nobody in this session. Measured while WI-0283 was filed: a
# lane confirmed its work was published against an `origin/main` its own `git fetch`
# had never reached (rc 128, an ssh timeout to the remote); the ref was current only
# because a peer lane had pushed into the shared store. The conclusion happened to be
# true, and not one of the probes could have told anyone that.
#
# WHAT DATES A REF IS CONTACT, AND A PUSH IS CONTACT. It is tempting to treat "moved
# by a local push" as the unreliable case, but a push that succeeded reached the
# remote and the remote accepted that sha — it dates the ref exactly as a fetch does,
# as of that moment. What differs is only WHOSE act it was and what it proves, so the
# honest move is to report the timestamp together with the act, never to rank them.
#
# THIS HELPER NEVER FETCHES. Like `tree_provenance` it only reads, so it is safe to
# call anywhere — in a hook, offline, in a loop — at no cost and with no side effect.
# Refreshing is a separate decision with a separate price (0.81s against a 0.15s verb,
# measured in WI-0222) and it already has an owner in `session.py`'s
# `_checkout_staleness`. What is added here is the half nothing computes today: how
# old the ref's knowledge is, and what put it there.

# The stamp `session.py` writes after every fetch attempt: `{upstream: {at, tried}}`,
# where `at` advances only on SUCCESS. Gitignored and machine-local; a lane and the
# main checkout share one, because they share one set of remote-tracking refs.
FETCH_STAMP_REL = pathlib.Path(".session-state") / "fetch-stamp.json"

# What counts as "recently confirmed". Must equal `session.CHECKOUT_FETCH_TTL_SECONDS`.
# The two cannot be shared — `session.py` ships to every member and cannot import
# federation-only `curate/`, the same split `atomic_write` already lives with — so a
# test pins them equal instead. Divergence would be silent and in the UNSAFE direction:
# a larger value here would call refs fresh that `session.py` had already given up on.
REMOTE_FRESH_TTL_SECONDS = 600

_REFLOG_FMT = "%H%x1f%gs%x1f%ct"


def _now(now=None):
    return time.time() if now is None else now


def short_remote_ref(ref):
    """`refs/remotes/origin/main` and `origin/main` name one ref two ways, and the
    fetch stamp is keyed by the SHORT one (it records what `rev-parse --abbrev-ref
    ...@{u}` returned). Normalising here lets a caller pass either spelling and still
    get a freshness answer, rather than silently getting None for the long one."""
    ref = str(ref)
    return ref[len("refs/remotes/"):] if ref.startswith("refs/remotes/") else ref


def _reflog_last(path, ref):
    """`(subject, unix_time)` of the ref's most recent movement, or `(None, None)`.

    Reflogs for `refs/remotes/*` are on by default in a non-bare repo but are NOT
    guaranteed — a fresh clone, an expired or pruned log, or a `git gc` can leave
    none. Absence therefore means "I cannot tell what moved it", never "nothing moved
    it", and the caller must render those two differently."""
    try:
        r = subprocess.run(
            ["git", "-C", str(path), "reflog", "show", "--format=" + _REFLOG_FMT,
             "-n", "1", str(ref)],
            capture_output=True, text=True, check=False)
    except Exception:
        return None, None            # git absent or unrunnable is "cannot tell", not a crash
    if r.returncode != 0 or not r.stdout.strip():
        return None, None
    parts = r.stdout.strip().split("\x1f")
    if len(parts) != 3:
        return None, None
    try:
        when = float(parts[2])
    except ValueError:
        when = None
    return (parts[1].strip() or None), when


def _classify_move(subject):
    """A reflog subject -> the kind of act that moved the ref.

    Three answers matter to a reader: a FETCH means someone here read the remote's
    state; a PUSH means the ref advanced to what we sent, which dates it but proves
    something narrower; anything else is honestly `other` rather than forced into one
    of the two. Git writes `update by push`, `fetch <remote>: <how>`, `pull: <how>`
    and `storing head` among others, so this matches the verb rather than trying to
    enumerate a list that git is free to extend."""
    if not subject:
        return None
    s = subject.lower()
    if "push" in s:
        return "push"
    if s.startswith("fetch") or s.startswith("pull") or "storing head" in s:
        return "fetch"
    return "other"


def fetch_age(path, ref, now=None):
    """Seconds since the last SUCCESSFUL fetch of `ref`'s remote, or None for "no idea".

    None is not freshness and must never render as a number. An unreadable, absent or
    corrupt stamp all answer None, as does a clock that moved backwards."""
    try:
        rec = json.loads((shared_work_root(path) / FETCH_STAMP_REL)
                         .read_text(encoding="utf-8"))
        at = float(rec[short_remote_ref(ref)]["at"])
    except Exception:
        return None
    if at <= 0:
        return None
    age = _now(now) - at
    return age if age >= 0 else None


def remote_provenance(path, ref, ttl=REMOTE_FRESH_TTL_SECONDS, now=None):
    """WHEN a remote-tracking ref was last confirmed against its remote, and WHAT
    confirmed it — `tree_provenance`'s job for the source that helper cannot reach.

    THREE OUTCOMES, NEVER TWO (ADR-0108 D1), and here the third is the entire point
    rather than an edge case:

      readable=False              the ref is not there; there is no reading at all
      readable=True, fresh=True   contact within `ttl`, and `confirmed_by` says how
      readable=True, fresh=False  the ref reads fine and its freshness is CANNOT TELL

    The last state is the one today's code silently folds into the second. It covers
    every way the item's defect arrives: no fetch has ever succeeded here, or the last
    contact has aged out, or nothing survives to say when contact happened.

    `age` is the age of the most recent CONTACT of any kind, which is what actually
    dates the ref; `fetch_age` is kept separately beside it because "we reached the
    remote and read it" and "we sent to the remote and it took it" are different
    evidence and a caller may care which. Returns `tree_provenance`'s three keys plus
    the remote half, so one caller can render either kind of source the same way:
    `{"path", "ref", "sha", "readable", "fresh", "age", "confirmed_by", "fetch_age",
    "moved_by", "moved_age"}`."""
    path = pathlib.Path(path)
    out = {"path": path, "ref": short_remote_ref(ref), "sha": None, "readable": False,
           "fresh": False, "age": None, "confirmed_by": None, "fetch_age": None,
           "moved_by": None, "moved_age": None}
    try:
        return _remote_provenance_read(path, ref, ttl, now, out)
    except Exception:
        # CANNOT RAISE — the same guarantee the sessionlib copy makes, stated the same
        # way and for the same reason. A provenance annotation that cannot be computed
        # must never cost the caller the verdict it was qualifying, and a dead git is
        # simply the UNREADABLE outcome that `out` already holds.
        return out


def _remote_provenance_read(path, ref, ttl, now, out):
    """The body of `remote_provenance`, split out so the never-raises guarantee wraps
    EVERY statement rather than only the calls someone remembered to wrap."""
    r = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--verify", "--quiet", str(ref)],
        capture_output=True, text=True, check=False)
    if r.returncode != 0 or not r.stdout.strip():
        return out
    out["sha"], out["readable"] = r.stdout.strip(), True
    out["fetch_age"] = fetch_age(path, ref, now=now)

    subject, when = _reflog_last(path, ref)
    out["moved_by"] = _classify_move(subject)
    if when is not None:
        moved_age = _now(now) - when
        out["moved_age"] = moved_age if moved_age >= 0 else None

    # THE MOST RECENT CONTACT DATES THE REF, whichever kind it was, and there are two
    # independent witnesses to it. The STAMP is exact but exists only where `session.py`
    # runs — every foreign portfolio checkout has none, which is precisely where the
    # dead-clone question gets asked. The REFLOG exists everywhere and under-claims by
    # construction: a fetch that finds nothing new moves no ref, so the log is at least
    # as old as the true last contact. Both err safely, so take the most recent.
    #
    # A push counts. It is tempting to treat "moved by a local push" as the unreliable
    # case, but a push that succeeded reached the remote and the remote accepted that
    # sha — what differs is whose act it was and what it proves, not whether it happened.
    # `confirmed_by` therefore names the act instead of ranking it.
    #
    # A move we CANNOT classify (`other`, or no reflog at all) is NOT counted: it may
    # have been a local `update-ref` that never left the machine, and guessing in the
    # generous direction here is exactly the false all-clear this exists to stop.
    moved_age = out["moved_age"] if out["moved_by"] in ("fetch", "push") else None
    candidates = [(a, k) for a, k in ((out["fetch_age"], "fetch"),
                                      (moved_age, out["moved_by"]))
                  if a is not None]
    if candidates:
        out["age"], out["confirmed_by"] = min(candidates)
    out["fresh"] = out["age"] is not None and out["age"] < ttl
    return out


def _mins(seconds):
    return int(seconds // 60)


def remote_clause(path, ref, ttl=REMOTE_FRESH_TTL_SECONDS, now=None):
    """`remote_provenance` rendered as the clause a verdict carries (ADR-0108 D1).

    A FRAGMENT, not a sentence, so a caller appends it to its own number instead of
    emitting a second line the reader has to join up — and unlike `provenance_clause`
    it is never silent in the good case, because the whole defect is a comparison that
    renders as a bare measurement. `12 behind origin/main` where someone fetched a
    minute ago and `12 behind origin/main` where nobody has reached the remote all
    week are the same eight characters today; this is what separates them."""
    return remote_clause_of(remote_provenance(path, ref, ttl=ttl, now=now))


def remote_clause_of(p):
    """Render a provenance record someone ALREADY HOLDS — see the note on the
    sessionlib copy. A caller that decided something from a record must quote that
    record rather than re-probing, or the verdict and its own clause can disagree."""
    if not p["readable"]:
        return "%s UNREADABLE (no such ref in %s)" % (p["ref"], p["path"])
    where = "per %s at %s" % (p["ref"], p["sha"][:7])
    if p["fresh"]:
        when = "just now" if _mins(p["age"]) < 1 else "%d min ago" % _mins(p["age"])
        if p["confirmed_by"] == "push":
            return "%s, last confirmed %s by a local push, not a fetch" % (where, when)
        return "%s, fetched %s" % (where, when)
    if p["age"] is None:
        why = "nothing here has ever reached the remote successfully"
    else:
        why = "the last contact with the remote was %d min ago (%s)" % (
            _mins(p["age"]), p["confirmed_by"])
    if p["moved_by"] is None:
        why += ", and no reflog survives to say what last moved the ref"
    elif p["moved_by"] == "push" and p["confirmed_by"] != "push":
        why += ", and the ref last moved by a local push rather than a fetch"
    return "%s — freshness CANNOT TELL (%s)" % (where, why)


# A fetch is a NETWORK call, and the write-side caller makes one per member in a loop
# over the whole fleet, so it is bounded three ways rather than trusted to come back:
# stdin is closed, `GIT_TERMINAL_PROMPT=0` refuses the credential prompt that would
# otherwise wait forever, and a timeout kills whatever still hangs. `git fetch` against
# an unreachable remote BLOCKS rather than failing, and a block reads as "still
# working" — one unreachable member must not stall a fleet-wide push.
FETCH_TIMEOUT_SECONDS = 45


def _no_prompt_env():
    """`os.environ` with git's interactive prompts turned off."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _upstream_ref(path, timeout=15):
    """The short name of the current branch's upstream (`origin/main`), or None.

    ASKED FIRST, ALWAYS, and never skipped as an optimisation: a fetch in a checkout
    with no tracking ref does not fail fast, it blocks on DNS or a credential prompt.
    The cheap question gates the expensive one."""
    try:
        r = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--abbrev-ref",
             "--symbolic-full-name", "@{u}"],
            capture_output=True, text=True, timeout=timeout, check=False,
            stdin=subprocess.DEVNULL, env=_no_prompt_env())
    except (OSError, subprocess.SubprocessError):
        return None
    ref = r.stdout.strip()
    return ref if r.returncode == 0 and ref else None


def _refresh_upstream(path, upstream, ttl, now):
    """Bring `upstream` up to date if it is not already known-fresh.

    Returns one of `fresh` (a successful fetch is already on record within `ttl`, so
    none was made), `ok`, or `failed`. The skip is what keeps a fleet-wide run to one
    round trip per member instead of one per question asked about it."""
    age = fetch_age(path, upstream, now=now)
    if age is not None and age <= ttl:
        return "fresh"
    remote = short_remote_ref(upstream).split("/", 1)[0]
    try:
        r = subprocess.run(
            ["git", "-C", str(path), "fetch", "--quiet", remote],
            capture_output=True, text=True, timeout=FETCH_TIMEOUT_SECONDS,
            check=False, stdin=subprocess.DEVNULL, env=_no_prompt_env())
    except (OSError, subprocess.SubprocessError):
        return "failed"
    return "ok" if r.returncode == 0 else "failed"


def git_divergence(path, fetch=False, ttl=REMOTE_FRESH_TTL_SECONDS, now=None):
    """Where a checkout's branch stands against its upstream — the ONE predicate behind
    both the read-only stale-clone tell and the write-side freshness precondition.

    Returns `{"path", "upstream", "readable", "ahead", "behind", "diverged",
    "fetch_state", "reason"}`.

    THREE OUTCOMES, NEVER TWO (ADR-0108 D1). `readable=False` means the question could
    not be ASKED — no upstream, not a repo, git absent, unrelated histories — and must
    never render the same as `readable=True, diverged=False`, which means it was asked
    and the answer was no.

    `diverged` is `ahead > 0 and behind > 0` AT ANY DEPTH. That is deliberately not the
    stale-clone threshold, which answers a different question — "is this an abandoned
    copy?" — and keeps its own number in `reconcile.py`. Any divergence at all, even
    1/1, fails the `git pull --ff-only` that every member's session-start ritual runs,
    so a threshold tuned for abandonment renders the cheap-to-fix case as `ok`
    (WI-0213: one member at 1 ahead / 2 behind and another at 1 ahead / 1 behind both read
    clean while genuinely diverged).

    `fetch=True` refreshes the remote-tracking ref FIRST — the opt-in mode for a caller
    about to WRITE. A cached ref cannot tell you that the cache is old, so a read-only
    answer is worthless to a writer: the substrate push once committed into
    members whose local trunk was already behind their remote, because both of
    its integrity gates compared the target only to ITSELF, and a clone that has not
    fetched in weeks is perfectly self-consistent. It discovered the staleness at push
    time, by which point the commit existed and the divergence had been created rather
    than avoided. Read-only stays the default so `reconcile.py` keeps its no-network,
    no-ref-mutation contract unchanged."""
    path = pathlib.Path(path)
    out = {"path": path, "upstream": None, "readable": False, "ahead": None,
           "behind": None, "diverged": False, "fetch_state": "not-requested",
           "reason": None}
    upstream = _upstream_ref(path)
    if not upstream:
        out["fetch_state"] = "no-upstream" if fetch else "not-requested"
        out["reason"] = "no upstream tracking ref (not a repo, or a branch with no remote)"
        return out
    out["upstream"] = short_remote_ref(upstream)
    if fetch:
        out["fetch_state"] = _refresh_upstream(path, upstream, ttl, now)
    try:
        r = subprocess.run(
            ["git", "-C", str(path), "rev-list", "--left-right", "--count",
             "HEAD...@{u}"],
            capture_output=True, text=True, timeout=30, check=False,
            stdin=subprocess.DEVNULL, env=_no_prompt_env())
    except (OSError, subprocess.SubprocessError):
        out["reason"] = "git could not be run here"
        return out
    if r.returncode != 0:
        out["reason"] = "git rev-list refused (unrelated histories, or no upstream)"
        return out
    parts = r.stdout.split()
    if len(parts) != 2:
        out["reason"] = f"unparseable rev-list output: {r.stdout.strip()!r}"
        return out
    try:
        ahead, behind = int(parts[0]), int(parts[1])
    except ValueError:
        out["reason"] = f"unparseable rev-list counts: {r.stdout.strip()!r}"
        return out
    out.update(readable=True, ahead=ahead, behind=behind,
               diverged=ahead > 0 and behind > 0)
    return out


def atomic_write(path, text):
    """Write text via a same-directory temp file + os.replace (atomic on POSIX)."""
    path = pathlib.Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ───────────────────────────────────────────── declared data residency (WI-0205/WI-0253)
#
# "Absent from this machine" and "broken" are DIFFERENT FACTS, and absence is the
# symptom they share. The only way to tell them apart is a DECLARATION — a member
# saying out loud which machine carries its repo — never an inference from the absence
# itself. A tool that infers the exemption from absence does not merely fail to detect
# a gap, it certifies one ([`ship-the-detector-with-the-capability`]).
#
# WI-0205 shipped this reasoning inside `curate/deliver.py` for the mailbox registry.
# WI-0253 needed the identical judgement in `curate/reconcile.py` against a different
# roster (`portfolio.md`), and two copies of a rule whose whole value is that it says
# the same thing in every surface is exactly the drift P16 exists to prevent. It lives
# here now; both tools import it, so the fleet has one answer to "is this one elsewhere
# by design?" and one place to change it.


def this_machine():
    """This machine's label (`DevBox` / `Runner` / …), or "" when it cannot be told.

    Reused from the harness rather than reimplemented ([P16]): `session.detect_machine()`
    already maps `scutil --get ComputerName` through `machine_map`, and is the value every
    stamp, banner and residency check in the fleet is written against. A second
    implementation here would be free to drift into disagreeing with the thing it
    describes. Imported lazily so a `curate/` script keeps a cheap module import.

    RETURNING "" IS LOAD-BEARING, not a tidy default. A machine that cannot name itself
    must not be able to EXCUSE an absent member as "elsewhere by design": not knowing
    where you are standing is a reason to WITHHOLD that verdict, never to grant it. An
    exemption that defaults to "pass" does not merely fail to detect a gap, it certifies
    one ([`ship-the-detector-with-the-capability`], [`declare-what-a-check-assumes`])."""
    try:
        # The harness lives one level up from `curate/`. Put it on the path HERE rather
        # than leaving each caller to remember: `curate/deliver.py` happened to do its
        # own `sys.path.insert` before WI-0253 moved this function, and a caller that
        # forgot would not crash — it would land in the `except` and return "", which
        # reads as "cannot name this machine" and silently withholds every residency
        # verdict in the fleet. A missing import is a bug; a missing import that
        # degrades into a plausible answer is a worse one.
        harness_root = str(pathlib.Path(__file__).resolve().parent.parent)
        if harness_root not in sys.path:
            sys.path.insert(0, harness_root)
        from session import detect_machine
        return detect_machine() or ""
    except Exception:
        return ""


def machine_labels():
    """Every machine label this member's `machine_map` can produce, or an empty set.

    The set `this_machine()` draws FROM, so a value can be checked for being resolvable at
    all rather than only for matching here. A declaration naming a machine the map has
    never heard of is not "somewhere else" -- it is a typo, and the two have to read
    differently or a misspelled host silently means "nowhere" ([`machine_labels_of`]'s own
    WI-0148 argument, reused rather than restated).

    Same lazy import and same failure shape as `this_machine()`: an empty set means the
    question could not be answered, never that the answer is "no labels"."""
    try:
        harness_root = str(pathlib.Path(__file__).resolve().parent.parent)
        if harness_root not in sys.path:
            sys.path.insert(0, harness_root)
        import session
        return set(session.machine_labels_of(session.CFG))
    except Exception:
        return set()


def declared_elsewhere(row, machine):
    """The machine label a roster row declares this member's repo lives on, when that
    is a DIFFERENT machine from `machine` — else "".

    WHY A DECLARATION AND NOT AN INFERENCE (WI-0205). devbox deliberately carries only a
    subset of the fleet; the runner-resident members are absent on purpose,
    and `reconcile-roots.local` says so in its own words — *"a smaller fleet view here is
    the mechanism working, not a gap"* — in a PROSE COMMENT, which no check can read. (That
    comment also cites an "ADR-0005 R2" that does not resolve: neither ADR-0005 nor ADR-0006
    contains an R2 or an R1prime. Quoting the sentence rather than repeating the citation,
    per [`no-fabricated-data`].) So the sweep folded "deliberately elsewhere" into "broken"
    and then prescribed a remedy (*map it in repo-paths.local*) that would require
    fabricating a path to a repo that is not on this disk. Wrong advice is worse than no
    advice: it is actionable.

    The fix is a field a member's row must DECLARE, never one this tool infers from
    absence of evidence — absence is exactly the symptom both states share
    ([`ship-the-detector-with-the-capability`]). A row with no residency label is
    UNDECLARED, which reads as an ordinary unreachable member, so a genuinely missing
    repo keeps reporting as the gap it is (ADR-0095 D2's empty-value rule).

    The value is a MACHINE-MAP LABEL, never a path ([ADR-0106](../adr/0106-local-promotion-process-and-data-residency-are-declared-separately.md) D2) —
    which is what makes it safe in a tracked file: the same disk can resolve to different
    absolute paths on different machines, so a literal path is wrong on some machine by
    construction, while the label is correct on every one of them ([P3]).

    `row` is a mapping with a `resides` key (`mailboxes.json`'s registry row, or the
    roster row `curate/reconcile.py` builds from `portfolio.md`'s Resides column) — the
    two registries share the FIELD NAME on purpose, so the same declaration reads the
    same way whichever tool is asking.

    Returns "" when we cannot name this machine, so the caller withholds rather than
    excuses; and "" when the declared machine IS this one, since a member that claims to
    live here and cannot be found here is a real gap, not a design."""
    if not machine:
        return ""
    label = (row or {}).get("resides")
    if not isinstance(label, str) or not label.strip():
        return ""
    label = label.strip()
    return "" if label == machine else label
