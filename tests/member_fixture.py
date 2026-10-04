"""The one authoritative synthetic-member fixture (P16 `avoid-duplication`).

`standard_check.py` (the shipped member-side self-check) and
`curate/standard_version.py` (the federation-side fleet parity tool) share a
manifest, so their tests share a subject: a synthetic repo carrying every
capability marker through the latest release. That fixture used to be written
out twice — once in `test_standard_check.py`, once in `test_standard_version.py`
— and it drifted exactly as [P16](../principles/master.md#p16--avoid-duplication)
predicts: session ~102 added the v1.6.0 interactive-close-guard markers to one
copy and left nine tests failing in the other, then repaired them in two separate
edits.

Adding a capability is now a **one-place** edit here. If you are bumping
`standard_check.LATEST`, wire the new capability into the executable `SESSION_PY`
(and/or `HOOK_SETTINGS`) the way `standard-capabilities.json` declares it, and both
suites move together.

Not named `test_*`, so `unittest discover` does not collect it. Both entry
points — `python3 -m unittest discover -s tests` and running a test file
directly — put this directory on `sys.path`, so a plain `import member_fixture`
resolves in either.
"""

import pathlib

# Every runtime-agnostic file a current member carries (standard_check.py = the
# rollout-selfcheck capability; session.py carrying `run_compile` = session-journals).
# `poga` (the lane launcher) is the file half of worktree-lanes (v1.5.0); its hook
# half lives in HOOK_SETTINGS below.
# `interpreter.py` is the file half of interpreter-pin (v1.17.0); its call-site half
# lives in SESSION_PY below, for the same reason `poga`'s marker lives in POGA.
# `STANDARD-REFERENCE.md` is the file half of standard-reference (v1.20.0) — the
# delivered-not-injected tier of the standard section (ADR-0136). It has no second
# half: the split is a delivery decision, so presence on disk IS the capability.
FULL_FILES = ["session.py", "standard_check.py", "CANON.md", "STANDARD.md",
              "STANDARD-REFERENCE.md",
              "session-handoff.md", "STATUS.md", "ROADMAP.md", "poga",
              "interpreter.py"]

# The synthetic session.py — an EXECUTABLE pre-split (single-file) harness.
#
# Until Package 3 this was a blob of marker strings (`def run_compile\n"--confirm"\n...`)
# that was never valid Python, because every harness detector grepped text. The detectors
# now LOAD the harness the way `session.py` loads itself and check the wiring declared in
# `standard-capabilities.json` — a named callable exists, a verb is registered on the
# argparse tree `main()` builds and handled by the named function, a caller's code
# reaches its callee, a call has the declared argument shape — plus a few pure probes. So
# the fixture is now the smallest harness that is genuinely wired: each capability below
# is a function that EXISTS, is REACHED from where the real harness reaches it, and (for
# the probed ones) BEHAVES.
#
# Every line is load-bearing for one capability; the comment names which, and the tests
# build deliberate partials by `.replace()`-ing a line out (see `without`). Verbs are
# registered from the VERBS table by handler NAME and looked up at registration time, so
# deleting a handler leaves its verb registered-but-unhandled (the "accepts the verb, then
# dies" partial) instead of breaking `main()` for every capability at once.
#
# Its bytes still differ from the federation's real session.py, so a fixture member reads
# capability-clean AND harness-stale — the two axes are independent, which is what
# `test_standard_version.py` leans on.
#
# Adding a capability means adding its wiring HERE too, or every "full member" test
# starts reading one version behind — which is the fixture telling the truth: a member
# missing the new capability IS behind. Update the fixture with the release.
SESSION_PY = '''\
"""Synthetic member harness (tests/member_fixture.py) - executable, minimal, wired."""
import argparse
import functools
import os

_ROOT = os.path.dirname(os.path.abspath(__file__))
OPS_DIRNAME = "ops-items"

# interpreter-pin 1.17.0: the entry point imports the pin and calls it when it IS the program.
try:
    import interpreter as _interpreter
except Exception:
    _interpreter = None
else:
    if __name__ == "__main__":
        _interpreter.ensure(_ROOT)


def run_compile(args=None):  # session-journals 1.2.0
    return None


def _complete_lazy_start():  # lazy-start 1.3.0
    return None


def _git_common_dir():  # cross-lane-coordination 1.4.0
    return None


def _coord_try_acquire(kind, name):  # cross-lane-coordination 1.4.0
    return _git_common_dir()


def _dispatch_read(did):
    return None


def _dispatched_close_authorization():  # dispatched-close-authorization 1.15.0
    rec = _dispatch_read(os.environ.get("POGA_DISPATCH", ""))
    return f"dispatch {rec}" if rec else None


def _unattended_run_read(rid):
    return None


def _unattended_close_authorization():  # unattended-close-authorization 1.18.0
    rec = _unattended_run_read(os.environ.get("POGA_UNATTENDED_RUN", ""))
    return f"unattended run {rec} - NO HUMAN CONFIRMED THIS CLOSE" if rec else None


def _harvest_session_work_items(journal_id):  # session-work-items 1.9.0
    return []


def finalize_journal(text, ended, duration, title=None, confirm=None, work_items=None):
    lines = ["---", "ended: " + ended, "duration: " + duration]
    if confirm:  # interactive-close-guard 1.6.0: the receipt
        lines.append("close-confirm: " + " ".join(str(confirm).split()))
    if work_items is not None:  # session-work-items 1.9.0
        lines.append("work-items: " + ", ".join(work_items))
    return "\\n".join(lines + ["---", ""])


def cmd_end(args):
    confirm = (getattr(args, "confirm", None) or _dispatched_close_authorization()
               or _unattended_close_authorization())
    return finalize_journal("", "", "", confirm=confirm or None,
                            work_items=_harvest_session_work_items(""))


def cmd_janitor(args):  # janitor 1.8.0
    return None


def cmd_wi_new(args):  # work-item-store 1.7.0
    return None


def cmd_wi_check(args):  # work-item-store 1.7.0
    return None


def _store_autocommit(store, changed):
    return None


def cmd_ops_new(args):  # ops-front-door 1.10.0: the ops write saves itself
    return _store_autocommit(OPS_DIRNAME, [])


def _merge_onto(ours, theirs, resolve=None):  # trunk-merge-integrate 1.13.0
    return None


def _integrate_trunk_with_remote(push=True, resolve=None):
    local, remote = "HEAD", "origin/main"
    return _merge_onto(local, remote, resolve)


def cmd_integrate(args):  # trunk-integrate 1.11.0: the verb's call
    return _integrate_trunk_with_remote(push=args.push)


def _escalate_to_integrate(resolve):  # trunk-integrate 1.11.0: the LAND's call
    return _integrate_trunk_with_remote(resolve=resolve)


def _resolve_rebase_conflict(resolve):
    return None


def _land_rebase(resolve):  # lane-repairs-main 1.12.0: the land consults the policy
    return _resolve_rebase_conflict(resolve)


def cmd_main_restore(args):  # lane-repairs-main 1.12.0
    return None


def _note_append(store, item_id, text, retry_verb):  # notes-as-records 1.13.0
    return None


def _notes_compiled(store, item):  # notes-as-records 1.13.0
    return ""


def _render_item(item):
    return _notes_compiled("work-items", item)


def residency_state(cfg, machine, production_root_present=None):  # residency 1.14.0
    return ("undeclared", "fixture")


def cmd_promote(args):  # residency 1.14.0
    return None


def _pf_terminfo():
    return None


def _pf_residency():
    return None


PREFLIGHT_GATE_CHECKS = (_pf_terminfo, _pf_residency)  # residency 1.14.0: the gate, wired


def _store_renumber_batch(c, pairs, parent=None, commit=True):  # counter-renumber 1.16.0
    return None


class _Counter:
    def __init__(self, key, renumberer=None):
        self.key = key
        self.renumberer = functools.partial(renumberer, self) if renumberer else None


def _counters():
    return {"wi": _Counter("wi", renumberer=_store_renumber_batch)}


def false_green_violation(cmd):  # false-green-guard 1.19.0
    return "denied: the exit status is swallowed" if "|" in cmd else None


def cmd_check_bash(args):
    return false_green_violation(os.environ.get("CMD", ""))


def cmd_lane_pad(args):
    return None


VERBS = (
    ("end", "cmd_end"),
    ("check-bash", "cmd_check_bash"),
    ("janitor", "cmd_janitor"),
    ("wi-new", "cmd_wi_new"),
    ("wi-check", "cmd_wi_check"),
    ("ops-new", "cmd_ops_new"),
    ("integrate", "cmd_integrate"),
    ("main-restore", "cmd_main_restore"),
    ("promote", "cmd_promote"),
    ("lane-pad", "cmd_lane_pad"),
)


def main():
    ap = argparse.ArgumentParser(description="synthetic member harness")
    sub = ap.add_subparsers(dest="cmd")
    for verb, handler in VERBS:
        sp = sub.add_parser(verb)
        sp.set_defaults(func=globals().get(handler))
        if verb == "end":
            sp.add_argument("--confirm")  # interactive-close-guard 1.6.0: the gate
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    main()
'''

# The interpreter pin's module half (interpreter-pin 1.17.0) — a real module with a real
# `ensure`, because the detector loads it rather than checking the file exists.
INTERPRETER_PY = "def ensure(root):\n    return None\n"


def without(*pieces, src=None):
    """`SESSION_PY` with each of `pieces` removed — exactly once, or the fixture moved
    under the test and it raises rather than quietly describing nothing."""
    out = SESSION_PY if src is None else src
    for piece in pieces:
        if out.count(piece) != 1:
            raise AssertionError(f"fixture piece not found exactly once: {piece!r}")
        out = out.replace(piece, "")
    return out

# The synthetic `poga` body. Until v1.7.0 every non-session.py file was written as
# the placeholder "x", because no detector read `poga`'s CONTENTS — worktree-lanes
# only tests that the file exists. The work-item store's detector reads it, so the
# fixture now needs a real marker: `cmd_work()` is the `poga work` front door, the
# supported access path onto the store (ADR-0073). `cmd_ops()` is the same door for the
# ops namespace (v1.10.0, WI-0125) — two front doors, two markers, because a member
# carrying one and not the other is exactly the partial the detector exists to catch.
POGA = 'cmd_work() {\n  :\n}\ncmd_ops() {\n  :\n}\n'

# The three GENERATED substrate files, as generated files (WI-0375). Until then each
# was written as the placeholder "x", because no detector read their CONTENTS — and
# that was the defect: `d_canon`, `d_standard_section` and `d_standard_reference` were
# bare `.is_file()`, so a member whose canon was an empty file read `ok`. They compare
# content now, so the fixture has to carry content, and these bodies are the SHAPE the
# detectors test rather than copies of the federation's real files: a member one
# release behind carries an older-but-correct file, and pinning the fixture to today's
# bytes would quietly convert these detectors into currency checks they cannot be
# (currency is the federation-side axis in `curate/standard_version.py` — a member has
# no federation reference on its own disk).
#
# The generated banner is present in the one wording all five renderings share, and
# each file has the two `##` sections the shape test requires. CANON.md's tally line
# MATCHES its two principle bullets and one habit bullet — `distill.py` writes that
# number from `len()` of what it just rendered, which is what makes it a checksum the
# member can verify locally.
CANON_MD = (
    "# Canon digest — principles & universal habits\n\n"
    "> **GENERATED — do not edit by hand.** Produced from the federation registries.\n\n"
    "**2 principles · 1 universal habits** (Accepted only).\n\n"
    "## Principles\n\n"
    "- **P1 `first-principle`** — One.\n"
    "- **P2 `second-principle`** — Two.\n\n"
    "## Universal habits\n\n"
    "- **`a-habit`** (P1) — Do the thing.\n"
)
# A CANON.md that is PRESENT and not a canon digest — the shape presence-only could
# not tell apart from the real thing. Its tally says three principles and the body
# carries one, which is what a truncated push looks like on disk.
CANON_MD_TRUNCATED = (
    "# Canon digest — principles & universal habits\n\n"
    "> **GENERATED — do not edit by hand.** Produced from the federation registries.\n\n"
    "**3 principles · 1 universal habits** (Accepted only).\n\n"
    "## Principles\n\n"
    "- **P1 `first-principle`** — One.\n\n"
    "## Universal habits\n\n"
    "- **`a-habit`** (P1) — Do the thing.\n"
)
STANDARD_MD = (
    "# Standard role-doc section\n\n"
    "> **GENERATED — do not edit by hand.** Produced from `standard-source.md`.\n\n"
    "## Inherited principles and universal habits\n\nbody\n\n"
    "## Session-start protocol\n\nbody\n"
)
STANDARD_REFERENCE_MD = (
    "# Standard role-doc section — reference\n\n"
    "> **GENERATED — do not edit by hand.** The reference tier of the standard section.\n\n"
    "## Standard substrate artifacts\n\nbody\n\n"
    "## Emergency commands\n\nbody\n"
)

# Hook-bound settings incl. the v1.3.0 all-tools PreToolUse heartbeat (ADR-0054) —
# real JSON: the heartbeat detector PARSES (the Stop hook runs the same command,
# so a grep can't tell them apart).
# Also carries the v1.5.0 WorktreeRemove teardown hook (ADR-0060), parsed the same way.
HOOK_SETTINGS = (
    '{"hooks": {"SessionStart": "session.py start", '
    '"PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "session.py check-bash"}]}, '
    '{"hooks": [{"command": "session.py heartbeat"}]}], '
    '"WorktreeRemove": [{"hooks": [{"command": "session.py worktree-remove"}]}]}, '
    '"ann": "session.py announce"}'
)
# A v1.2.0-era hook binding: guards wired, no PreToolUse heartbeat yet.
HOOK_SETTINGS_V12 = '{"hooks": {"SessionStart": "session.py start"}, ' \
                    '"pre": "session.py check-bash", "ann": "session.py announce"}'
RESIDENT_SETTINGS = '{"permissions": {"allow": []}}'  # exists but no harness wiring


def make_repo(tmp, files=FULL_FILES, settings=HOOK_SETTINGS):
    """A synthetic member repo under `tmp`. Defaults build a complete member —
    capability-clean at `standard_check.LATEST`. Narrow `files` or swap
    `settings` (or pass `settings=None`) to build a deliberately-deficient one."""
    repo = pathlib.Path(tmp)
    bodies = {"session.py": SESSION_PY, "poga": POGA, "interpreter.py": INTERPRETER_PY,
              "CANON.md": CANON_MD, "STANDARD.md": STANDARD_MD,
              "STANDARD-REFERENCE.md": STANDARD_REFERENCE_MD}
    for f in files:
        (repo / f).write_text(bodies.get(f, "x"), encoding="utf-8")
    if settings is not None:
        (repo / ".claude").mkdir(exist_ok=True)
        (repo / ".claude" / "settings.json").write_text(settings, encoding="utf-8")
    return repo


def cfg(**kw):
    """The minimal `session.config.json` dict both evaluators take."""
    base = {"architect_id": "test-arch"}
    base.update(kw)
    return base
