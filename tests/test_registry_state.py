"""WI-0321: persisted content, including data git cannot see."""
import importlib
import json
from pathlib import Path
import subprocess
import tempfile
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class RegistryStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.main = self.root / 'main'
        self.lane = self.root / 'lane'
        for root in (self.main, self.lane):
            for name in ('principles/master.md', 'habits/master.md', 'users/operator/profile.md'):
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('# Registry\n## Group\nintro\n### keep\noriginal\n### remove\nold\n')

    def api(self):
        return importlib.import_module('sessionlib.registry_state')

    def line(self):
        return self.api().orientation(self.lane, self.main)

    def save(self):
        self.api().record_end(self.lane, self.main, 'prior-session')

    def test_unknown_without_baseline(self):
        self.assertEqual(self.line(), 'registries: unknown (no baseline)')

    def test_equal_hashes_use_persisted_prior_session(self):
        self.save()
        self.assertEqual(self.line(), 'registries: unchanged since prior-session')
        data = json.loads((self.main / '.session-state/registries-baseline.json').read_text())
        self.assertEqual(len(data['files']['habits/master.md']['hash']), 64)
        # A different lane sees the same baseline.
        self.assertEqual(self.api().orientation(self.main, self.main), self.line())

    def test_changed_entries_write_small_diff(self):
        self.save()
        (self.lane / 'habits/master.md').write_text(
            '# Registry\n## Group\nintro\n### keep\nreworded\n### added\nnew\n')
        line = self.line()
        self.assertIn('registries: CHANGED — habits/master.md (+2 −2 entries; diff: ', line)
        path = Path(line.split('diff: ', 1)[1].rstrip(')'))
        self.assertEqual(path.parent, self.lane / '.session-state')
        diff = path.read_text()
        for expected in ('added: ### added', 'removed: ### remove', 'reworded: ### keep'):
            self.assertIn(expected, diff)
        self.assertNotIn('reworded: ## Group', diff)
        self.assertLess(len(diff), 800)
        self.assertIn('CHANGED', self.line())  # start never advances baseline

    def test_gitignored_profile_is_counted(self):
        subprocess.run(['git', 'init', '-q', str(self.main)], check=True)
        (self.main / '.gitignore').write_text('users/\n')
        ignored = subprocess.run(['git', '-C', str(self.main), 'check-ignore',
                                  'users/operator/profile.md'], capture_output=True, text=True)
        self.assertEqual(ignored.returncode, 0)
        self.save()
        with (self.main / 'users/operator/profile.md').open('a') as f:
            f.write('### preference\nAlways inject me.\n')
        self.assertIn('users/operator/profile.md (+1 −0 entries; diff: ', self.line())
        self.save()
        self.assertEqual(self.line(), 'registries: unchanged since prior-session')
