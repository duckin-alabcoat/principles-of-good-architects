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

Adding a capability marker is now a **one-place** edit here. If you are bumping
`standard_check.LATEST`, teach `SESSION_PY` (and/or `HOOK_SETTINGS`) the new
marker in this file and both suites move together.

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

# The synthetic session.py body. It must carry the compactor marker
# (session-journals, 1.2.0), the lazy-start completion hook (lazy-start, 1.3.0),
# the cross-lane coordination primitives (cross-lane-coordination, 1.4.0), and
# the interactive-close guard's flag + receipt field (interactive-close-guard,
# 1.6.0), the store verbs (work-item-store, 1.7.0), and the janitor's registration
# + implementation (janitor, 1.8.0), the close's work-item harvest
# (session-work-items, 1.9.0), and the ops front door's two halves
# (ops-front-door, 1.10.0 — the registered verb plus the write that saves itself),
# and the trunk integrate's two halves (trunk-integrate, 1.11.0 — the verb itself
# plus the LAND calling it, which is the half that makes the escalation gone rather
# than merely available), and the lane-repairs-main pair (1.12.0 — the repair verb
# plus the land consulting the rebase policy), and the merge-not-replay pair
# (trunk-merge-integrate, 1.13.0 — the merge mechanism plus the integrate reaching it
# with the parents in the load-bearing order), and the notes-as-records pair (also
# 1.13.0 — the record writer plus the compiled reader, because a member that writes
# notes nobody reads back is worse off than one with the bug), and the residency trio
# (1.14.0), and the dispatched-close authorization plus its citation (1.15.0 — the
# exemption that names its own reason, and the receipt that makes it auditable),
# and the counter renumberer plus its registry binding (counter-renumber, 1.16.0 —
# the remap itself and the row that makes the land reach it).
#
# `trunk-integrate` needs TWO call sites here, not the literal `()` it used to match:
# its detector counts calls now, because pinning it to an argument list made it report
# ABSENT the moment the land grew a `resolve=` keyword (WI-0153). The fixture mirrors
# the real shape — cmd_integrate's call and the land's — rather than the old spelling.
# Its bytes still differ from the federation's
# real session.py, so a fixture member reads capability-clean AND harness-stale —
# the two axes are independent, which is what `test_standard_version.py` leans on.
#
# Adding a capability means adding its marker HERE too, or every "full member" test
# starts reading one version behind — which is the fixture telling the truth: a
# member missing the new marker IS behind. Update the fixture with the release.
SESSION_PY = ('def run_compile\ndef _complete_lazy_start\n'
              'def _coord_try_acquire\ndef _git_common_dir\n'
              '"--confirm"\n"close-confirm"\n'
              '"wi-new"\n"wi-check"\n'
              '"janitor"\ndef cmd_janitor\n'
              '_harvest_session_work_items\nwork_items=\n'
              '"ops-new"\n_store_autocommit(OPS_DIRNAME\n'
              'def cmd_integrate\n_integrate_trunk_with_remote(push=args.push)\n'
              '_integrate_trunk_with_remote(resolve=resolve)\n'
              'def cmd_main_restore\n_resolve_rebase_conflict(resolve)\n'
              'def _merge_onto\n_merge_onto(local, remote, resolve)\n'
              'def _note_append\n_notes_compiled(\n'
              # residency, 1.14.0 (ADR-0106) — THREE markers, because the realistic
              # partial here is not a missing feature but an unwired one. The gate tuple
              # is written out in full rather than as a bare mention: the detector reads
              # INSIDE it, since a `_pf_residency` that exists and is never called never
              # refuses anything while the surface reads compliant. Same lesson as
              # trunk-integrate's call-site count, one release later.
              'def residency_state\ndef cmd_promote\n'
              'PREFLIGHT_GATE_CHECKS = (_pf_terminfo, _pf_workspace_trust, '
              '_pf_onboarding, _pf_residency)\n'
              # dispatched-close-authorization, 1.15.0 (ADR-0113 / WI-0249) — the close
              # guard keyed on the DISPATCH that authorized the close rather than on the
              # lane it runs in, plus the citation that dispatch writes into
              # `close-confirm`. Four markers for the reason residency needs three: the
              # authorization must be COMPUTED and CONSULTED (a function the guard never
              # calls refuses nothing), RESOLVED against the dispatch record rather than
              # asserted from the environment (ADR-0112 D7), and WRITTEN into the journal
              # — a widened exemption with an empty receipt is a bigger hole than the one
              # v1.6.0 left on lanes. The `_dispatch_read` line sits INSIDE the function
              # body here because the detector reads it there, not file-wide: the harness
              # has read dispatch records since ADR-0104.
              'def _dispatched_close_authorization():\n'
              '    rec = _dispatch_read(did)\n'
              'dispatch_confirm = _dispatched_close_authorization()\n'
              'confirm=confirm or None,\n'
              # counter-renumber, 1.16.0 (WI-0169 / WI-0245) — TWO markers, and the
              # second is the one that matters. The renumberer being IMPLEMENTED is
              # not the capability: the land walks `_counters()` and skips every row
              # whose `renumberer` is None, so a harness carrying the function with
              # no bound row remaps nothing and refuses exactly as before — present,
              # unreachable, and indistinguishable from working until a collision
              # lands. Same lesson as trunk-integrate's call-site count and
              # residency's gate tuple, and here the two halves sit ~1300 lines apart
              # in the real file, which makes the split realistic rather than
              # theoretical.
              'def _store_renumber_batch(c, pairs, parent=None, commit=True):\n'
              'renumberer=_store_renumber_batch,\n'
              # interpreter-pin, 1.17.0 (WI-0328) — the CALL SITE, not the definition.
              # `_harness_src` includes `interpreter.py` itself now, so a detector
              # matching `def ensure` would pass every member carrying the file whether
              # or not the entry point ever invoked it — and the entry point invokes it
              # from exactly one line, inside a try/except that keeps a member without
              # the module starting normally. A half-delivered pin is invisible from
              # outside; this marker is what makes it visible.
              '_interpreter.ensure(_ROOT)\n'
              # unattended-close-authorization, 1.18.0 (WI-0331) — the SECOND
              # non-human close authorization. Three markers, on the row above's
              # reasoning plus one: computed AND consulted, RESOLVED against the
              # run record the runner wrote rather than asserted from
              # `POGA_UNATTENDED_RUN` (a marker, not a control — ADR-0112 D7), and
              # LABELLED. The label is the half unique to this row: the defect was
              # never merely that the headless adoption session could not close, it
              # was that its only escape was `--confirm "<an invented user quote>"`
              # into the field that exists to prove a human agreed. An exemption
              # without the label makes every unattended close indistinguishable in
              # the record from a human-confirmed one, which is the same corruption
              # reached politely.
              'def _unattended_close_authorization():\n'
              '    rec = _unattended_run_read(rid)\n'
              '    return "NO HUMAN CONFIRMED THIS CLOSE"\n'
              'unattended_confirm = _unattended_close_authorization()\n'
              # false-green-guard, 1.19.0 (WI-0344) — check-bash denies a run whose
              # EXIT CODE is the verdict when anything runs after it in the same
              # command. The marker is the predicate's DEFINITION, matching how the
              # detector reads it, and the fixture's HOOK_SETTINGS already wires
              # `check-bash` — which is the other half the detector requires, and the
              # reason this capability is scoped claude-hook rather than all. A member
              # holding the function with no wired hook has the file and not the
              # guard, and a fixture that satisfied the detector on the source alone
              # would be testing a property the real detector does not have.
              'def false_green_violation(cmd):\n'
              '    return None\n')

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
    bodies = {"session.py": SESSION_PY, "poga": POGA,
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
