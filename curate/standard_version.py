#!/usr/bin/env python3
"""Standard-version fleet parity — the FEDERATION-side rollout reconciliation (ADR-0047).

The federation-only companion to the shipped, member-side `standard_check.py`: it
locates every member repo, runs the SAME manifest + detectors against each (imported
from `standard_check` — single source of truth, P16, never duplicated here), and
prints the fleet parity view. It MUTATES NOTHING (P18 read-only; P15 pure mechanism —
the model reads the verdict, never computes it).

Beyond what a member can check about itself, this adds the **substrate-currency**
axis: every byte-identical substrate file has the federation as its single writer, so a
member whose copy differs is behind on that file even if its capability version is
clean. A member self-checking cannot see this (it has no federation reference, and one
shipped alongside the file would go stale with it); the federation can, so it lives
here. WI-0375 widened this from the two files it used to cover (`session.py` and
`STANDARD.md`) to the whole push manifest — `CANON.md` was checked by nobody on either
side, so a member three weeks behind on canon read `ok`.

Parked members (the `PARKED` set below — deliberately off-standard, operator
session 64) are skipped, so a known-and-chosen absence is never re-surfaced as drift.

CLI (locating members mirrors reconcile.py — walk roots for session.config.json +
the repo-paths.local map; P3 keeps machine paths out of tracked files):
  python3 curate/standard_version.py <root> [root…]          # full fleet report
  python3 curate/standard_version.py --fleet-status <root> …  # one-line fleet signal
  python3 curate/standard_version.py --residency <root> …     # ADR-0106 D1 adoption view
  python3 curate/standard_version.py --residency-status …     # its one-line form

The residency view is a SECOND rollout over the same members and is deliberately not
folded into the parity table or its gate: standard-version measures what a member CAN
DO, residency measures what a member has DECLARED, and only the first is the federation's
to enforce (ADR-0106 D5). See the `residency adoption` section below.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile

from common import (
    read_repo_paths_config as _read_repo_paths_config,
    read_roots_config as _read_roots_config,
    member_config_root,
    walk_tree,
)

FED_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FED_ROOT))
import standard_check as sc  # noqa: E402  (federation root on the path; the shared manifest)

# WI-0361: the one resolver for the machine-local locator — external under a declared
# production service, and `shared_work_root` everywhere else.
ROOTS_CONFIG = member_config_root(FED_ROOT) / "reconcile-roots.local"
REPO_PATHS_CONFIG = member_config_root(FED_ROOT) / "repo-paths.local"
MAX_DEPTH = 4

# Members deliberately parked off-standard (operator, session 64) — see memory
# project_parked_systems. Never flagged as drift; revisited on onboarding.
#
# This is a CONVERGENCE exemption and is deliberately NOT read from
# `fleet-cadence.json`. That file's own `_note` scopes its `parked` flag to
# staleness ("Parked systems are staleness-exempt"), and it marks other
# members parked — all of which are located and converge
# fine. Reusing it here would excuse members that need no excusing, which is an
# exemption defaulting to PASS (`ship-the-detector-with-the-capability`). Two
# different questions, two different declarations.
PARKED = set()  # member ids deliberately off-standard, if any

# The DENOMINATOR's source (WI-0098). Every id here is validated against the roster,
# so a parked entry naming a system the portfolio does not list is a loud error
# rather than a silent exemption of nothing.
PORTFOLIO = FED_ROOT / "portfolio.md"

LATEST = sc.LATEST


class RosterUnreadable(Exception):
    """portfolio.md could not be read, or parsed to zero systems.

    Raised rather than returning an empty set, because an empty denominator makes
    every coverage check trivially pass — the exact shape WI-0098 exists to close.
    The gate catches this and fails CLOSED."""


def read_roster(path=None):
    """The set of system-ids the portfolio declares, EXCLUDING the federation itself.

    This is the gate's denominator. Parsed from `portfolio.md`'s roster table, whose
    row shape is `| … | `<system-id>` | `<system-id>-arch` | … |`. The architect-id
    column is used as the ANCHOR — a row counts only when column 5 is exactly column
    4 plus `-arch` (the ADR-0006 convention). That is what lets this ignore the other
    tables in the same file without hardcoding a line range, and it means a change to
    the table's shape yields zero rows and an exception, never a quietly short roster.
    """
    p = pathlib.Path(path) if path else PORTFOLIO
    try:
        text = p.read_text(errors="replace")
    except OSError as exc:
        raise RosterUnreadable(f"cannot read the portfolio roster at {p}: {exc}") from exc

    roster = set()
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        sid, aid = cells[3].strip("`"), cells[4].strip("`")
        if sid and aid == f"{sid}-arch":
            roster.add(sid)

    if not roster:
        raise RosterUnreadable(
            f"parsed zero systems from {p} — the roster table's shape has changed. "
            f"Refusing to treat an empty denominator as full coverage.")
    roster.discard("federation")  # the federation is not a member of its own fleet
    return roster


# --------------------------------------------------------------------------- substrate currency
#
# WI-0375. THE AXIS THIS FILE EXISTS FOR, GENERALISED. `session.py` (the
# harness-currency axis) and `STANDARD.md` (WI-0226) were compared by bytes here; every
# OTHER byte-identical substrate file — `CANON.md` above all — was checked nowhere at
# all, on either side. The member side cannot check it (see `standard_check._generated`:
# a member has no federation reference on its disk, and a digest shipped alongside the
# file goes stale with it), so `CANON.md` was measured by a bare `.is_file()` and a
# member three weeks behind on canon read `ok`. The reference exists HERE and only here,
# so the content comparison belongs here — and over the whole manifest, not two files of
# it, because fixing two of three members of a class is the shape that keeps coming back.

# The federation's byte-identical substrate — the SUBJECTS of the currency axis.
# PINNED to `push-substrate.BYTE_IDENTICAL` by
# `tests/test_standard_version.py::SubstrateManifestTest`, which fails the suite if the
# two ever disagree. Restated rather than imported ON PURPOSE: `push-substrate.py` is
# hyphenated (importlib, not `import`) and its module body imports `gen_settings` and
# the whole `session` harness, and `fleet_status_line` below runs inside a SessionStart
# hook — paying a harness import at every session start to avoid a seven-item list is
# the wrong trade. The test is what makes the restatement safe: adding a file to the
# push manifest goes red here until it is added, which is the forcing function, not an
# errand (`ship-the-detector-with-the-capability`).
SUBSTRATE_ROOT_FILES = ["CANON.md", "STANDARD.md", "STANDARD-REFERENCE.md",
                        "session.py", "interpreter.py", "standard_check.py",
                        "standard-capabilities.json", "poga"]
SESSIONLIB_DIRNAME = "sessionlib"


def substrate_subjects():
    """Every byte-identical substrate path, as repo-relative strings.

    The `sessionlib/` members are READ from the federation's own tree for the same
    reason `push-substrate` reads them there: which modules exist is a fact of the
    federation, so a module shipped tomorrow is compared here without anyone editing
    this file. Total — an unreadable package contributes nothing rather than raising."""
    try:
        mods = sorted(f"{SESSIONLIB_DIRNAME}/{m.name}"
                      for m in (FED_ROOT / SESSIONLIB_DIRNAME).iterdir()
                      if m.suffix == ".py" and m.is_file())
    except OSError:
        mods = []
    return [*SUBSTRATE_ROOT_FILES, *mods]


def substrate_currency(repo):
    """{rel: "IDENTICAL" | "DIFFERS" | "NOT CHECKED"} for every substrate subject.

    ONLY files present on BOTH sides are compared. An ABSENCE is not staleness and is
    deliberately not reported as it: a member that legitimately carries no STANDARD.md
    (a resident member's CANON-floor repo — `push-substrate.REFRESH_ONLY` never introduces one there)
    would otherwise read stale forever on a file the federation has chosen not to ship
    it, which is a guard firing on correct code. What a member is MISSING is the
    capability detectors' question, and they answer it; this axis answers only "is what
    it has current?".

    The federation's own repo compares against itself and is IDENTICAL by construction."""
    same_repo = False
    try:
        same_repo = repo.resolve() == FED_ROOT.resolve()
    except OSError:
        pass
    out = {}
    for rel in substrate_subjects():
        src_p, dst_p = FED_ROOT / rel, repo / rel
        if same_repo:
            out[rel] = "IDENTICAL" if src_p.is_file() else "NOT CHECKED"
            continue
        try:
            if not (src_p.is_file() and dst_p.is_file()):
                out[rel] = "NOT CHECKED"
                continue
            out[rel] = "IDENTICAL" if dst_p.read_bytes() == src_p.read_bytes() else "DIFFERS"
        except OSError:
            out[rel] = "NOT CHECKED"
    return out


# --------------------------------------------------------------------------- isolated evaluation
#
# Since the capability manifest (standard-capabilities.json), `sc.evaluate` LOADS a
# member's harness to check it: it runs that member's session.py and sessionlib parts.
# Doing that inside this process would run every member's code with the federation's
# environment, cwd and open files at every federation session start. The consultant's
# 2026-10-02 ruling (b) refused that: each member is evaluated in its OWN child process,
# with a timeout, a scratch HOME/TMPDIR/cwd, a minimal environment and (on macOS) a
# sandbox that denies every write outside that scratch dir. The child returns its verdict
# as data on one stdout line marked with a per-run nonce, so a member's stray output
# cannot pass for a verdict (it guards against accidents; the member's code shares the
# child's memory, so it is not a defence against a hostile member).
#
# A member whose child fails to start, times out, crashes, or prints no readable verdict
# reads UNKNOWN — never clean. UNKNOWN blocks the rollout gate like any other blocker.

EVAL_TIMEOUT_S = 60
EVAL_WORKERS = 8
_RESULT_MARK = "STANDARD-VERSION-RESULT "
_KNOWN_STATUS = {"clean", "behind", "drift", "below-floor"}
_SANDBOX_PROFILE = """(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write* (subpath "{scratch}") (literal "/dev/null") (regex #"^/dev/tty"))
"""
_SANDBOX_OK = None


def _sandbox_available():
    """True when `sandbox-exec` exists AND can apply a profile here. Inside another
    sandbox (a sandboxed test run, say) applying one fails, and every member would then
    read UNKNOWN for a reason that is about this box, not the member — so the child falls
    back to environment containment and the verdict names which one it got."""
    global _SANDBOX_OK
    if _SANDBOX_OK is None:
        exe = shutil.which("sandbox-exec")
        ok = False
        if exe:
            try:
                ok = subprocess.run([exe, "-p", "(version 1)(allow default)", "/usr/bin/true"],
                                    capture_output=True, timeout=10).returncode == 0
            except (OSError, subprocess.SubprocessError):
                ok = False
        _SANDBOX_OK = ok
    return _SANDBOX_OK


def _unknown(cfg, reason, containment):
    return {"runtime": ((cfg or {}).get("runtime") or "claude").lower(),
            "detected": None, "caps": {}, "missing": [], "stale": [],
            "status": "unknown", "note": f"unknown — evaluation {reason}",
            "manifest_error": None, "containment": containment}


def _evaluate_isolated(repo, cfg, timeout=EVAL_TIMEOUT_S):
    """`sc.evaluate(repo, cfg)` run in a contained child process; see the section note."""
    with tempfile.TemporaryDirectory(prefix="sv-eval-") as scratch:
        scratch = os.path.realpath(scratch)
        env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": scratch, "TMPDIR": scratch,
               "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
        cmd = [sys.executable, "-B", "-E", "-s", str(pathlib.Path(__file__).resolve()),
               "--evaluate-one", str(repo)]
        containment = "env"
        if _sandbox_available():
            profile = pathlib.Path(scratch) / "eval.sb"
            profile.write_text(_SANDBOX_PROFILE.format(scratch=scratch))
            cmd = [shutil.which("sandbox-exec"), "-f", str(profile), *cmd]
            containment = "sandbox"
        nonce = secrets.token_hex(8)
        try:
            proc = subprocess.run(cmd, input=json.dumps({"cfg": cfg or {}, "nonce": nonce}),
                                  capture_output=True,
                                  text=True, errors="replace", cwd=scratch, env=env,
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            return _unknown(cfg, f"timed out after {timeout}s", containment)
        except OSError as exc:
            return _unknown(cfg, f"could not start ({exc})", containment)
    mark = f"{_RESULT_MARK}{nonce} "
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(mark)]
    if proc.returncode != 0 or not lines:
        tail = (proc.stderr.strip().splitlines() or [""])[-1][:200]
        return _unknown(cfg, f"failed (exit {proc.returncode}{': ' + tail if tail else ''})",
                        containment)
    try:
        r = json.loads(lines[-1][len(mark):])
    except ValueError:
        return _unknown(cfg, "returned an unreadable verdict", containment)
    if not isinstance(r, dict) or r.get("status") not in _KNOWN_STATUS:
        return _unknown(cfg, "returned no recognised status", containment)
    r["containment"] = containment
    return r


def _evaluate_one_child(repo):
    """The child side of `_evaluate_isolated`: cfg on stdin, one marked verdict line out.
    Any failure exits non-zero with nothing marked, which the parent reads as UNKNOWN."""
    req = json.loads(sys.stdin.read())
    r = sc.evaluate(pathlib.Path(repo), req["cfg"])
    sys.stdout.write(f"\n{_RESULT_MARK}{req['nonce']} {json.dumps(r)}\n")
    sys.stdout.flush()
    return 0


def evaluate_members(members):
    """{sid: evaluate_member(repo, cfg)} over located members, children run in parallel.
    Reaches `evaluate_member` through the module, so a test that patches it is honoured."""
    if not members:
        return {}
    workers = min(EVAL_WORKERS, len(members))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {sid: pool.submit(evaluate_member, repo, cfg)
                for sid, (repo, cfg) in members.items()}
        return {sid: f.result() for sid, f in futs.items()}


def evaluate_member(repo, cfg):
    """sc.evaluate (run in a contained child — ruling (b)) + the federation-only
    SUBSTRATE-CURRENCY axis (WI-0375).

    Byte-identical substrate has the federation as its single writer, so a member whose
    copy differs is behind on that file even when its capability version is clean. A
    member self-checking cannot see this (it has no federation reference); the
    federation can, so it lives here.

    `harness_stale` and `standard_identity` are kept as VIEWS on the one computation
    rather than as second and third comparisons (P16) — they are what the old two-file
    axis reported, and the report, the gate and their tests still read them."""
    r = _evaluate_isolated(repo, cfg)
    cur = substrate_currency(repo)
    r["substrate"] = cur
    r["substrate_stale"] = sorted(k for k, v in cur.items() if v == "DIFFERS")
    # session.py's own view of the axis — the harness-currency line this tool has
    # printed since ADR-0047, and the blocker the rollout gate already keys on.
    r["harness_stale"] = cur.get("session.py") == "DIFFERS"
    # WI-0226: capability presence cannot prove delivery of the current ask rule.
    r["standard_identity"] = cur.get("STANDARD.md", "NOT CHECKED")
    return r


# --------------------------------------------------------------------------- member location


def _load_cfg(repo):
    try:
        return json.loads((repo / "session.config.json").read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None


def _sid(cfg):
    aid = cfg.get("architect_id", "")
    return aid[:-5] if aid.endswith("-arch") else aid


def find_members(roots):
    """{system-id: (repo, cfg)} for every repo under `roots` with a session.config.json
    declaring an architect_id. The federation's own repo is excluded."""
    seen = {}
    for root in roots:
        base = pathlib.Path(root).resolve()
        if not base.is_dir():
            continue
        for dirpath, _dirnames, filenames in walk_tree(base, MAX_DEPTH - 1):
            if "session.config.json" not in filenames:
                continue
            repo = pathlib.Path(dirpath)
            if repo == FED_ROOT:
                continue
            cfg = _load_cfg(repo)
            if not cfg or not cfg.get("architect_id"):
                continue
            seen.setdefault(_sid(cfg), (repo, cfg))
    return seen


def resolve_mapped(repo_paths):
    """Members located directly via repo-paths.local (precedence over a walked hit)."""
    out = {}
    for _sid_key, raw in repo_paths.items():
        repo = pathlib.Path(raw).expanduser()
        if not repo.is_dir():
            continue
        cfg = _load_cfg(repo)
        if not cfg or not cfg.get("architect_id"):
            continue
        out[_sid(cfg)] = (repo, cfg)
    return out


def locate(roots, repo_paths):
    members = find_members(roots)
    members.update(resolve_mapped(repo_paths))  # mapped wins
    for sid in PARKED:
        members.pop(sid, None)
    return members


def coverage(located_sids, roster=None):
    """Reconcile what we LOCATED against what the portfolio SAYS EXISTS (WI-0098).

    Without this, `locate()` is both the numerator and the denominator: a member that
    became unreachable simply left the fleet, so 11-of-11 and 9-of-9 are the same exit
    code and the gate gets EASIER to pass as coverage gets worse. Four distinct
    outcomes, none folded into another (`declare-what-a-check-assumes`):

      expected     roster minus the federation minus the declared PARKED exemptions
      unlocated    expected but not found HERE — cannot be verified, so not passed
      unknown      found running the substrate but absent from the roster — either an
                   unregistered system or a stale portfolio; either way, someone
                   should know
      stale_parked an exemption naming a system the roster does not list — the
                   exemption is excusing nothing and has probably outlived its subject
    """
    roster = read_roster() if roster is None else set(roster)
    located = set(located_sids)
    expected = roster - PARKED
    return {
        "roster": roster,
        "expected": expected,
        "located": located,
        "unlocated": sorted(expected - located),
        "unknown": sorted(located - roster),
        "stale_parked": sorted(PARKED - roster),
        "parked": sorted(PARKED & roster),
    }


def coverage_parts(cov):
    """One phrase per coverage problem, or [] when the sets reconcile."""
    parts = []
    if cov["unlocated"]:
        parts.append(f"{len(cov['unlocated'])} UNLOCATED ({', '.join(cov['unlocated'])})")
    if cov["unknown"]:
        parts.append(f"{len(cov['unknown'])} not in the roster ({', '.join(cov['unknown'])})")
    if cov["stale_parked"]:
        parts.append(f"{len(cov['stale_parked'])} parked-but-unrostered "
                     f"({', '.join(cov['stale_parked'])})")
    return parts


def coverage_line(cov):
    """The `located M of N` line — printed on EVERY surface, passing or not, so the
    denominator is visible rather than inferred from a number that looks like a total."""
    n_expected, n_located = len(cov["expected"]), len(cov["expected"] & cov["located"])
    line = f"Coverage: located {n_located} of {n_expected} expected member(s)"
    if cov["parked"]:
        line += f" (parked, not expected: {', '.join(cov['parked'])})"
    parts = coverage_parts(cov)
    return line + ("." if not parts else f" — {'; '.join(parts)}.")


# ------------------------------------------------------- residency adoption (WI-0209)

# ADR-0106 D1 says a member declares its own residency; D5 says the federation may not
# declare it for them. `standard_check.d_residency` therefore asks only whether a
# member's SUBSTRATE can carry a declaration, and says so in its own docstring - it is
# deliberately not a check that the member has declared. That left the adoption itself
# with no surface at all: the brief went to every member, and the only
# way to learn how many had answered was to open each config by hand.
#
# This is the third federation-only axis in this module, and it is here for the reason
# the module docstring already gives for the other two: a member self-checking has no
# fleet to compare against, and the federation does. "Has the fleet answered?" is a
# question only this side can ask.
#
# IT DOES NOT GATE. `fleet_complete` is the standard-VERSION predicate, and residency
# declaration is not a standard-version fact - folding it in would make a member read
# BEHIND on a capability it demonstrably has. Adoption of an ASK is reported, never
# enforced; the whole point of D5 is that the answer is the member's to give.


def _pure_residency():
    """The PURE residency resolvers from `sessionlib.config`, imported late.

    `sessionlib/*.py` is exec'd into `session.py`'s namespace, not imported - its
    `ROOT` is `Path(__file__).parent`, which resolves to `sessionlib/` under a real
    import, so the module-level `CFG` lands None and anything touching it raises.
    Verified, not assumed: `detect_machine()` imported from here dies on
    `CFG["machine_map"]` with a TypeError.

    So this reaches for the three documented-PURE functions ONLY - they take an
    already-parsed config dict and read nothing - and never for module state. Late,
    inside a function, so the `--fleet-status` session-start path does not pay for an
    import it never uses and cannot be broken by one.

    Returns None when the import fails, which every caller renders as a distinct
    "NOT CHECKED" rather than as a clean fleet."""
    try:
        from sessionlib.config import (  # noqa: PLC0415  (late by design, see above)
            machine_labels_of, residency_of, residency_state,
        )
    except Exception:
        return None
    return machine_labels_of, residency_of, residency_state


def reference_labels(fed_cfg=None):
    """Every machine LABEL the federation's own `machine_map` can produce.

    The reference set for the resolvability axis, taken from the federation's config
    rather than from a constant in this file. Two reasons, and the second is the one
    that matters: a `"devbox"`/`"DevBox"` literal here would be a second declaration
    of a fact `session.config.json` already holds (P16), and it would not learn about
    a machine added later. The brief itself names the federation's config as the
    reference implementation for the spelling, so this reads the source the brief cited.

    Returns an empty set when the federation's own config cannot be read - callers must
    report that as NOT CHECKED, never as "every member is fine"."""
    pure = _pure_residency()
    if pure is None:
        return set()
    machine_labels_of, _, _ = pure
    if fed_cfg is None:
        fed_cfg = _load_cfg(FED_ROOT)
    return machine_labels_of(fed_cfg)


# What a member's machine_map does with the reference labels. Four outcomes, kept apart
# for `declare-what-a-check-assumes`: "yields it", "yields a near-miss", "yields nothing
# like it", and "we could not look" are four different pieces of news and three
# different fixes.
LABELS_OK = "ok"
LABELS_DRIFT = "label-drift"
LABELS_MISSING = "missing"
LABELS_NOT_CHECKED = "not-checked"


def label_reach(cfg, reference):
    """(state, missing, drift) - can this member resolve a residency naming each
    reference machine?

    `missing` is the reference labels the member's `machine_map` cannot produce.
    `drift` is the near-misses that explain a miss: a label the member DOES yield which
    differs from a reference label only by case. Those are the rows that look adopted
    and are not - `residency_state` matches labels by exact string, so a member carrying
    `"devbox": "devbox"` who then declares `"process": "DevBox"` lands in
    `declared-but-unresolvable`, which is a harder failure to read than having never
    answered at all. The brief warned about this spelling in prose; this is the half
    that measures it.

    THE LABEL AXIS ONLY, and that bound is deliberate rather than an oversight. This
    answers "would a residency value naming that machine resolve?" It does NOT answer
    "do this member's map KEYS match that machine's live ComputerName" - only a session
    standing on that machine can answer that, and a strict key comparison against the
    federation's map would flag members for missing a curly-apostrophe variant they will
    never be asked for. A guard that fires on correct code gets deleted; this one fires
    only on the thing that actually breaks."""
    pure = _pure_residency()
    if pure is None or not reference:
        return (LABELS_NOT_CHECKED, [], [])
    machine_labels_of, _, _ = pure
    have = machine_labels_of(cfg)
    missing = sorted(l for l in reference if l not in have)
    folded = {l.lower() for l in reference}
    drift = sorted(l for l in have if l not in reference and l.lower() in folded)
    if not missing:
        return (LABELS_OK, [], drift)
    return (LABELS_DRIFT if drift else LABELS_MISSING, missing, drift)


def _this_machine():
    """This machine's declared LABEL, or `"UNKNOWN-MACHINE"`.

    Deliberately NOT `sessionlib.config.detect_machine` - that reads module-level `CFG`,
    which is None under a real import (see `_pure_residency`). Deliberately not a copy
    of its hostname-guessing fallback either: this is a report, and a report that
    guesses "probably the Runner" from a substring is worse than one that says it does
    not know. A miss returns the sentinel, and because `residency_row` passes
    `production_root_present=None`, the value reaches no branch - only prose."""
    import subprocess  # noqa: PLC0415  (one caller, on a hand-run path)
    # WI-0468: the macOS ComputerName first, then — on a host with no `scutil` — the
    # hostname, full then short. The same keys in the same order `detect_machine` tries
    # (WI-0467) and `poga init` writes (bootstrap.machine_name), so a Linux member that
    # mapped its hostname is named here too. Exact map hits only: still no guessing.
    keys = []
    for cmd in (["scutil", "--get", "ComputerName"], ["hostname"]):
        try:
            name = subprocess.run(cmd, text=True, capture_output=True,
                                  check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            continue
        if name:
            keys = [name] if cmd[0] == "scutil" else [name, name.split(".")[0]]
            break
    fed = _load_cfg(FED_ROOT) or {}
    mm = fed.get("machine_map")
    for key in keys:
        if isinstance(mm, dict) and key in mm:
            return mm[key]
    return "UNKNOWN-MACHINE"


def residency_row(cfg, reference, machine=None):
    """(declared_state, why, labels_state, missing, drift) for one located member.

    `declared_state` is `sessionlib.config.residency_state`'s own four-state verdict,
    reused rather than recomputed - a second implementation of "what does this
    declaration mean" is exactly the drift this module's docstring refuses.

    `production_root_present` is passed as None ON PURPOSE. That function's contract
    says None makes `declared-and-contradicted` unreachable, and calls it "the correct
    answer for a caller that did not look" - which this caller is. A fleet survey run on
    devbox cannot see whether the Runner's production data root exists, and guessing
    would turn an unobserved disk into a reported contradiction. The report states this
    rather than leaving it to be inferred.

    `machine` is injectable for the same reason `residency_state`'s is: a test that read
    the real machine would pass on the box that wrote it and prove nothing about the
    other one."""
    pure = _pure_residency()
    if pure is None:
        return ("not-checked", "sessionlib.config could not be imported - verified "
                "nothing about this member", LABELS_NOT_CHECKED, [], [])
    _, _, residency_state = pure
    state, why = residency_state(cfg, machine or _this_machine(),
                                 production_root_present=None)
    lstate, missing, drift = label_reach(cfg, reference)
    return (state, why, lstate, missing, drift)


def residency_adoption(roots, repo_paths, roster=None):
    """The fleet adoption view: {rows, cov, reference, counts}.

    `rows` is {sid: residency_row(...)} over LOCATED members, the federation included -
    ADR-0003 puts it in its own fleet on equal footing, and it is the one member that
    has answered, so excluding it would make the reference implementation invisible in
    the view that exists to show who answered.

    `counts` asserts over the ROSTER, not over what we reached (WI-0098). An unlocated
    member is counted as `unlocated`, never quietly dropped: a fleet that gets harder to
    reach must not read as a fleet that got more compliant."""
    members = locate(roots, repo_paths)
    reference = reference_labels()
    machine = _this_machine()
    rows = {sid: residency_row(cfg, reference, machine)
            for sid, (_repo, cfg) in members.items()}
    cov = coverage(members.keys(), roster=roster)

    def over(pred):
        return sorted(s for s in cov["expected"] if s in rows and pred(rows[s]))

    counts = {
        "expected": sorted(cov["expected"]),
        "declared": over(lambda r: r[0] == "declared"),
        "undeclared": over(lambda r: r[0] == "undeclared"),
        "unresolvable": over(lambda r: r[0] not in ("declared", "undeclared")),
        "unlocated": list(cov["unlocated"]),
        "label_gap": over(lambda r: r[2] in (LABELS_MISSING, LABELS_DRIFT)),
        "label_drift": over(lambda r: r[2] == LABELS_DRIFT),
    }
    return {"rows": rows, "cov": cov, "reference": sorted(reference), "counts": counts}


def residency_status_line(roots, repo_paths):
    """One line: how far the ADR-0106 D1 ask has actually got.

    NOT wired into SessionStart, and that is a decision rather than an omission. The
    startup banner already carries eleven hook lines; this number moves when a member
    answers a brief, which is weekly at best, and a twelfth permanent line for it would
    be noise that trains the reader to skip the block. It is printed at the foot of the
    hand-run report - where someone is already asking about fleet adoption - and is
    available here for any surface that later wants it."""
    a = residency_adoption(roots, repo_paths)
    c, n = a["counts"], len(a["counts"]["expected"])
    if not a["reference"]:
        return ("Residency adoption: NOT CHECKED - the federation's own machine_map "
                "yielded no reference labels, so nothing was compared.")
    parts = ["%d of %d expected member(s) have declared" % (len(c["declared"]), n)]
    if c["unresolvable"]:
        parts.append("%d UNRESOLVABLE (%s)"
                     % (len(c["unresolvable"]), ", ".join(c["unresolvable"])))
    if c["label_drift"]:
        parts.append("%d label-drift (%s)"
                     % (len(c["label_drift"]), ", ".join(c["label_drift"])))
    if c["label_gap"]:
        parts.append("%d missing a reference label (%s)"
                     % (len(c["label_gap"]), ", ".join(c["label_gap"])))
    if c["unlocated"]:
        parts.append("%d UNLOCATED, not asked here (%s)"
                     % (len(c["unlocated"]), ", ".join(c["unlocated"])))
    return "Residency adoption (ADR-0106 D1): " + "; ".join(parts) + "."


_RESIDENCY_MARK = {
    "declared": "declared",
    "undeclared": "UNDECLARED",
    "declared-but-unresolvable": "UNRESOLVABLE",
    "declared-and-contradicted": "CONTRADICTED",
    "not-checked": "NOT CHECKED",
}

# Printed at the foot of every run, passing or not
# ([`declare-what-a-check-assumes`]): a reader must not have to infer which of these
# three questions the table did and did not answer.
RESIDENCY_CAVEATS = [
    "WHAT THIS DOES NOT CHECK, said rather than left to be inferred:",
    "  - `declared-and-contradicted` is UNREACHABLE here. It needs an observation of",
    "    whether a production data root is present, and a survey run on one machine",
    "    cannot see another's disk. `production_root_present` is passed as None, which",
    "    that resolver documents as the correct answer for a caller that did not look.",
    "  - The label axis compares LABELS, not machine_map KEYS. It answers 'would a",
    "    residency value naming that machine resolve?', not 'does this map carry that",
    "    machine's live ComputerName' - only a session on that machine can answer the",
    "    second.",
    "  - Declaring is the MEMBER's act (ADR-0106 D5). UNDECLARED is a fact to report,",
    "    never a defect to fix from here, and nothing in this module writes to a member",
    "    repo.",
]


def residency_report(roots, repo_paths):
    """The hand-run adoption view. Read-only (P18); mutates nothing, asks nobody."""
    a = residency_adoption(roots, repo_paths)
    rows, cov = a["rows"], a["cov"]
    print("Residency adoption (ADR-0106 D1) - has the fleet answered the ask?")
    print("Reference labels, from the federation's own machine_map: %s"
          % (", ".join(a["reference"]) or "NONE - nothing was compared"))
    print(coverage_line(cov))
    print()
    print("UNDECLARED means no `residency` block at all, which is NOT the same as "
          "declaring no\nproduction mode (ADR-0106 D1): `{\"process\": null}` is an "
          "answer, a missing block is\nsilence. The state word carries that for every "
          "row, so the detail column holds only\nwhat is specific to the member.")
    print()
    header = "%-28s %-13s %-13s  detail" % ("system", "residency", "labels")
    print(header)
    print("-" * len(header))
    for sid in sorted(set(rows) | set(cov["unlocated"])):
        if sid not in rows:
            print("%-28s %-13s %-13s  not reachable from this machine - unverified, "
                  "not passed" % (sid, "UNLOCATED", "-"))
            continue
        state, why, lstate, missing, drift = rows[sid]
        # The shared UNDECLARED prose is printed ONCE above, not once per row. It is
        # `residency_state`'s wording for a single member's preflight, where it is the
        # whole message; repeated down a fleet-sized table it is the wall of text that
        # makes a list unreadable, and it pushes the one part that DOES differ per row
        # off the end of the line.
        bits = [] if state == "undeclared" else [why]
        if missing:
            bits.append("machine_map cannot produce: " + ", ".join(missing))
        if drift:
            bits.append("near-miss spelling(s) present: " + ", ".join(drift))
        print("%-28s %-13s %-13s  %s"
              % (sid, _RESIDENCY_MARK.get(state, state), lstate, " | ".join(bits)))
    print()
    print(residency_status_line(roots, repo_paths))
    print()
    for line in RESIDENCY_CAVEATS:
        print(line)
    return 0


# --------------------------------------------------------------------------- reporting


_STATUS_MARK = {"clean": "ok", "behind": "BEHIND", "drift": "DRIFT",
                "below-floor": "BELOW-FLOOR", "unknown": "UNKNOWN"}


def fleet_report(roots, repo_paths):
    members = locate(roots, repo_paths)
    print(f"Standard-version fleet parity — {len(members)} member(s) located "
          f"(latest v{LATEST}; parked skipped: {', '.join(sorted(PARKED))})")
    try:
        print(coverage_line(coverage(members.keys())))
    except RosterUnreadable as exc:
        print(f"Coverage: NOT CHECKED — {exc}")
    print()
    header = f"{'system':<28} {'runtime':<7} {'detected':<9}  status"
    print(header)
    print("-" * len(header))
    verdicts = evaluate_members(members)
    for sid in sorted(members):
        r = verdicts[sid]
        det = f"v{r['detected']}" if r["detected"] else "—"
        mark = _STATUS_MARK.get(r["status"], r["status"])
        note = r["note"] + ("; HARNESS STALE (session.py behind — run push-substrate)"
                            if r["harness_stale"] else "")
        print(f"{sid:<28} {r['runtime']:<7} {det:<9}  {mark}: {note}")
        # WI-0375: every byte-identical file, NAMED. The line this replaced reported
        # STANDARD.md alone, so a member whose CANON.md was three weeks behind printed
        # the same two words as one that was byte-current.
        stale = r.get("substrate_stale") or []
        if stale:
            print(f"  substrate STALE ({len(stale)}): {', '.join(stale)} "
                  f"— behind the federation; run push-substrate")
        else:
            checked = sum(1 for v in (r.get("substrate") or {}).values() if v != "NOT CHECKED")
            print(f"  substrate current: {checked} byte-identical file(s) match the federation")
    kinds = sorted({r.get("containment", "?") for r in verdicts.values()})
    if verdicts:
        print(f"\nEach member was evaluated in its own child process "
              f"(containment: {', '.join(kinds)}; timeout {EVAL_TIMEOUT_S}s) — ruling (b).")
    print("\nRead-only signal (P18). What a member CAN DO is the only version signal — "
          "there is no self-reported claim to compare against (ADR-0068); surface drift, "
          "never silently self-heal (ADR-0047).")
    # A POINTER, deliberately not the number. Residency adoption is a second, unrelated
    # rollout over the same members, and printing its count under this table is how a
    # reader starts using one rollout's progress as the other's — the failure
    # `tests/test_reconcile_residency.py` opens by describing, where two tools disagreed
    # inside a single startup banner about the same-sounding word.
    print("Residency adoption (ADR-0106 D1) is a SEPARATE rollout over these members "
          "and is not counted above — `--residency`.")
    return 0


def classify_fleet(members):
    """Blocker lists over located members — the shared completeness computation
    behind both the fleet status line and the rollout-complete gate (P16: one
    source, never two divergent copies of 'what counts as behind'). A member is a
    blocker if it drifts, is below-floor, is behind LATEST, is harness-stale, or
    carries any OTHER stale byte-identical substrate file (WI-0375).

    `stale` stays session.py-only and `substrate` carries the rest, rather than one
    merged bucket: "the harness is behind" and "canon is behind" send you to the same
    command but are different news, and collapsing them would have silently renamed the
    line the startup banner has printed for months.

    `.get` on the new key, not `[...]`: `fleet_complete`'s callers inject synthetic
    verdicts, and a mandatory key would turn every one of them into a KeyError."""
    drift, below, behind, stale, substrate, unknown = [], [], [], [], [], []
    for sid, r in evaluate_members(members).items():
        if r["status"] == "unknown":
            # Ruling (b): a member whose evaluation failed is unverified, never clean.
            unknown.append(sid)
        elif r["status"] == "drift":
            drift.append(sid)
        elif r["status"] == "below-floor":
            below.append(sid)
        elif r["status"] == "behind" or (r["detected"] and sc._cmp(r["detected"], LATEST) < 0):
            behind.append(sid)
        if r["harness_stale"]:
            stale.append(sid)
        if [f for f in (r.get("substrate_stale") or []) if f != "session.py"]:
            substrate.append(sid)
    return {"drift": sorted(drift), "below": sorted(below),
            "behind": sorted(behind), "stale": sorted(stale),
            "substrate": sorted(substrate), "unknown": sorted(unknown)}


def fleet_complete(roots, repo_paths, roster=None):
    """The rollout-complete predicate (ADR-0047): (complete, blockers, n_members, cov).

    Complete iff every expected member is BOTH located AND detecting at LATEST with no
    drift, below-floor, behind, harness-staleness, or other stale substrate. Detection is
    the only version signal (ADR-0068).

    WI-0375 — WHY STALE SUBSTRATE BLOCKS THE GATE BUT DOES NOT CHANGE A MEMBER'S STATUS
    WORD. The two questions are different. `status` is what a member CAN DO, computed
    from its own detectors; a member holding a correct-but-one-push-old CANON.md can do
    everything v1.20.0 asks, and calling that DRIFT would turn the fleet red every time
    canon is regenerated and not yet pushed — a normal hourly state, and a guard that
    fires on correct code gets deleted. `complete` is whether the ROLLOUT is finished,
    and a fleet with stale canon in it plainly is not. So stale substrate joins
    `harness_stale` as a named blocker on exactly the precedent `harness_stale` already
    set, and the member's own word is left alone.

    WI-0098 — THE DENOMINATOR. This predicate used to assert only over what it happened
    to reach, and the docstring handed the naming of the unlocated set to "the caller",
    which no caller ever discharged. That made an unreachable member a SUBTRACTION from
    the fleet rather than a failure: 11-of-11 and 9-of-9 returned the same exit code, so
    the gate got easier to pass exactly as coverage got worse — a check that certifies a
    gap rather than detecting it. Coverage is now part of the predicate, not a note
    beside it: an unlocated expected member blocks COMPLETE, because unverifiable is not
    the same as passed (ADR-0047), which is the distinct-UNLOCATED-verdict rule ADR-0037
    already gave `reconcile.py` and this sibling never inherited.

    `n_members == 0` is still never complete."""
    members = locate(roots, repo_paths)
    blockers = classify_fleet(members)
    cov = coverage(members.keys(), roster=roster)
    complete = (len(members) > 0
                and not any(blockers.values())
                and not coverage_parts(cov))
    return complete, blockers, len(members), cov


def _blocker_parts(blockers):
    parts = []
    if blockers.get("unknown"):
        parts.append(f"{len(blockers['unknown'])} UNKNOWN — evaluation failed "
                     f"({', '.join(blockers['unknown'])})")
    if blockers["drift"]:
        parts.append(f"{len(blockers['drift'])} DRIFT ({', '.join(blockers['drift'])})")
    if blockers["below"]:
        parts.append(f"{len(blockers['below'])} below-floor ({', '.join(blockers['below'])})")
    if blockers["stale"]:
        parts.append(f"{len(blockers['stale'])} harness-stale ({', '.join(blockers['stale'])})")
    if blockers.get("substrate"):
        parts.append(f"{len(blockers['substrate'])} substrate-stale "
                     f"({', '.join(blockers['substrate'])})")
    if blockers["behind"]:
        parts.append(f"{len(blockers['behind'])} behind ({', '.join(blockers['behind'])})")
    return parts


def fleet_status_line(roots, repo_paths):
    members = locate(roots, repo_paths)
    if not members:
        return "Standard-version: no member repos located (see reconcile-roots.local / repo-paths.local)."
    parts = _blocker_parts(classify_fleet(members))
    # Coverage is reported on the startup line too — a member that quietly stopped
    # being reachable is exactly the news this surface used to swallow. Fail-soft
    # here (unlike the gate): an unreadable roster degrades this line to the old
    # located-only phrasing rather than bricking a session start.
    try:
        cparts = coverage_parts(coverage(members.keys()))
        n_expected = len(coverage(members.keys())["expected"])
        denom = f"{len(members)} of {n_expected}"
    except RosterUnreadable:
        cparts, denom = [], f"{len(members)} located"
    if not parts and not cparts:
        return f"Standard-version: all {denom} expected member(s) at v{LATEST}."
    return (f"Standard-version rollout OPEN: {'; '.join(parts + cparts)} — "
            f"run `python3 curate/standard_version.py <roots>` to inspect.")


# --------------------------------------------------------------------------- CLI


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description="Standard-version fleet parity (ADR-0047).")
    ap.add_argument("--fleet-status", action="store_true", help="one-line fleet signal")
    ap.add_argument("--residency", action="store_true",
                    help="ADR-0106 D1 adoption view: which members have declared a "
                         "residency block, and which cannot resolve one yet (WI-0209). "
                         "Reports; never gates — declaring is the member's act (D5)")
    ap.add_argument("--residency-status", action="store_true",
                    help="the one-line form of --residency")
    ap.add_argument("--assert-complete", action="store_true",
                    help="rollout-complete GATE: exit 0 iff every located member detects at "
                         "the latest standard-version; else exit 1 (fails closed on error)")
    ap.add_argument("--evaluate-one", metavar="REPO", help=argparse.SUPPRESS)
    ap.add_argument("roots", nargs="*", help="search roots (default: reconcile-roots.local)")
    args = ap.parse_args(argv)

    # The contained child of `_evaluate_isolated`. It fails LOUD (non-zero, nothing
    # marked), never open: the parent turns that into UNKNOWN.
    if args.evaluate_one:
        return _evaluate_one_child(args.evaluate_one)

    # The rollout-complete gate (ADR-0047) fails CLOSED: it must never claim
    # completeness on an error, and — unlike the report / status-line paths — it is
    # NOT a session-start path, so it deliberately does not share the fail-open
    # wrapper below (a gate that returns 0 on error is no gate).
    if args.assert_complete:
        try:
            roots = args.roots or _read_roots_config(ROOTS_CONFIG)
            repo_paths = _read_repo_paths_config(REPO_PATHS_CONFIG)
            complete, blockers, n, cov = fleet_complete(roots, repo_paths)
        except Exception:
            import traceback
            traceback.print_exc()
            print("Standard-version GATE: errored — NOT declaring rollout complete.", file=sys.stderr)
            return 1
        if n == 0:
            print("Standard-version GATE: no members located — cannot assert completeness "
                  "(see reconcile-roots.local / repo-paths.local).", file=sys.stderr)
            return 1
        if complete:
            print(f"Standard-version GATE: ROLLOUT COMPLETE — {coverage_line(cov)} "
                  f"All at v{LATEST}.")
            return 0
        # THREE outcomes, never two (WI-0098). A coverage gap and a failing detector are
        # different facts needing different fixes — "go find the missing repo" versus "go
        # fix this member" — so they are never folded into one message. Both still exit 1:
        # what changed is that a coverage gap can now reach exit 1 AT ALL, where it used
        # to shrink the denominator and exit 0.
        bparts, cparts = _blocker_parts(blockers), coverage_parts(cov)
        if bparts:
            print(f"Standard-version GATE: ROLLOUT INCOMPLETE — {'; '.join(bparts)}; "
                  f"detectors do not pass on all {n} located member(s). Not declaring "
                  f"v{LATEST} fleet-complete.", file=sys.stderr)
        if cparts:
            print(f"Standard-version GATE: COVERAGE GAP — {coverage_line(cov)} "
                  f"An expected member that cannot be reached here is UNVERIFIED, not "
                  f"passed; it does not leave the fleet by being unreachable.",
                  file=sys.stderr)
        return 1

    try:
        roots = args.roots or _read_roots_config(ROOTS_CONFIG)
        repo_paths = _read_repo_paths_config(REPO_PATHS_CONFIG)
        if args.fleet_status:
            print(fleet_status_line(roots, repo_paths))
            return 0
        if args.residency_status:
            print(residency_status_line(roots, repo_paths))
            return 0
        if args.residency:
            return residency_report(roots, repo_paths)
        if not roots and not repo_paths:
            print(__doc__)
            print("ERROR: pass a search root, or populate reconcile-roots.local / "
                  "repo-paths.local (P3).", file=sys.stderr)
            return 2
        return fleet_report(roots, repo_paths)
    except Exception:  # fail open — never brick a session start
        import traceback
        print("Standard-version: fleet check errored; verified nothing.", file=sys.stderr)
        traceback.print_exc()
        print("Standard-version: fleet check errored — verified nothing.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
