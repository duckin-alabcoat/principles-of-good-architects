"""The machine-local member locator must never be read from the running checkout.

WI-0361. `reconcile-roots.local` and `repo-paths.local` are gitignored, so a release
clone carries neither. A reader that resolves them against its own checkout therefore
locates ZERO members after cutover and reports a clean run — the quiet failure this
guard exists to prevent. `common.member_config_root` is the one resolver: external
under an explicitly configured production service, and byte-identical to
`shared_work_root` everywhere else, so routing through it costs development nothing.

The guard is structural rather than a list of known modules, because the defect it
found was four readers that were simply missed when two others were converted.
"""
import ast
import unittest
from pathlib import Path

CURATE = Path(__file__).resolve().parent.parent / "curate"
LOCATOR_FILES = {"reconcile-roots.local", "repo-paths.local"}
RESOLVER = "member_config_root"
CHECKOUT = "shared_work_root"


def _called_names(node):
    return {n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def _locator_joins(tree):
    """Every `<expr> / "<locator file>"`, with the calls and names on its left."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
                and isinstance(node.right, ast.Constant)
                and node.right.value in LOCATOR_FILES):
            names = {n.id for n in ast.walk(node.left) if isinstance(n, ast.Name)}
            yield node.lineno, node.right.value, _called_names(node.left), names


def _checkout_helpers(tree):
    """Functions returning the checkout root without consulting the resolver.

    `member_config_root` itself is excluded: returning `shared_work_root` is exactly
    its development branch. A helper is only ever reported at a join site below, so a
    producer-file helper like `gather.producer_root` is never a finding.
    """
    names = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name == RESOLVER:
            continue
        for ret in (n for n in ast.walk(node) if isinstance(n, ast.Return) and n.value):
            called = _called_names(ret.value)
            if CHECKOUT in called and RESOLVER not in called:
                names[node.name] = node.lineno
    return names


def _checkout_bound_names(tree, helpers):
    """Variables assigned the checkout root, directly or through such a helper.

    One hop is enough for the real shape, `root = config_root()` followed by
    `root / "repo-paths.local"`, which no expression-local check can see.
    """
    bound = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        called = _called_names(node.value)
        source = ([CHECKOUT] if CHECKOUT in called else []) + sorted(called & set(helpers))
        if not source or RESOLVER in called:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                bound[target.id] = source[0]
    return bound


def findings(source):
    tree = ast.parse(source)
    helpers = _checkout_helpers(tree)
    bound = _checkout_bound_names(tree, helpers)
    out = []
    for lineno, filename, called, names in _locator_joins(tree):
        if CHECKOUT in called:
            out.append((lineno, "%s composed with %s" % (CHECKOUT, filename)))
        for helper in sorted(called & set(helpers)):
            out.append((lineno, "%s reached through %s(), which returns the checkout root"
                        % (filename, helper)))
        for name in sorted(names & set(bound)):
            out.append((lineno, "%s joined to `%s`, bound to the checkout root by %s()"
                        % (filename, name, bound[name])))
    return out


class MemberLocatorIsExternal(unittest.TestCase):
    def test_no_curate_module_reads_the_locator_from_its_own_checkout(self):
        offenders = []
        for path in sorted(CURATE.glob("*.py")):
            for lineno, why in findings(path.read_text(encoding="utf-8")):
                offenders.append("%s:%d  %s" % (path.name, lineno, why))
        self.assertEqual(offenders, [], "read these through common.member_config_root:\n"
                         + "\n".join(offenders))

    def test_the_guard_reports_the_shape_it_was_built_from(self):
        # The four readers this guard was written against, in their pre-fix form.
        direct = ("from common import shared_work_root\n"
                  "ROOTS_CONFIG = shared_work_root(FED_ROOT) / 'reconcile-roots.local'\n")
        self.assertEqual(len(findings(direct)), 1)
        indirect = ("def config_root():\n"
                    "    return shared_work_root(FED_ROOT)\n"
                    "def locate():\n"
                    "    root = config_root()\n"
                    "    return read(root / 'repo-paths.local')\n")
        self.assertEqual(len(findings(indirect)), 1)

    def test_the_guard_is_silent_on_the_resolver_and_on_producer_files(self):
        # `member_config_root`'s own fallback is the correct answer, not a finding.
        resolver = ("def member_config_root(fed_root):\n"
                    "    roots = production.resolve(fed_root)\n"
                    "    return roots.config_root if roots else shared_work_root(fed_root)\n")
        self.assertEqual(findings(resolver), [])
        # Producer files are checkout-relative by design; only the locator files move.
        producer = ("def producer_root():\n"
                    "    return shared_work_root(ROOT)\n"
                    "path = producer_root() / 'architect-learnings.md'\n")
        self.assertEqual(findings(producer), [])
        # And the fixed shape must pass.
        fixed = "ROOTS_CONFIG = member_config_root(FED_ROOT) / 'reconcile-roots.local'\n"
        self.assertEqual(findings(fixed), [])


if __name__ == "__main__":
    unittest.main()
