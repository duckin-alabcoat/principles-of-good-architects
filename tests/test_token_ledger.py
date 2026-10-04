"""R5: runtime accounting, independent of private transcript fixtures."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'curate'))
import token_ledger as ledger


def record(kind, payload, minute=0):
    return dict(type=kind, payload=payload, timestamp=f'2026-09-07T10:{minute:02d}:00Z')


def usage(i, cached, out, reasoning=0):
    return dict(input_tokens=i, cached_input_tokens=cached,
                output_tokens=out, reasoning_output_tokens=reasoning)


def count(total, last=None, minute=0, reset=100):
    return record('event_msg', dict(type='token_count', info=dict(
        total_token_usage=total, last_token_usage=last or total),
        rate_limits={'primary': {'resets_at': reset}}), minute)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, name, rows):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        return p

    def test_claude_last_usage_tools_across_fragments_users_and_response_idle(self):
        def assistant(mid, out, minute, content):
            return dict(type='assistant', timestamp=f'2026-09-07T10:{minute:02d}:00Z',
                        message=dict(id=mid, model='claude', usage=dict(input_tokens=100,
                        cache_read_input_tokens=900, output_tokens=out), content=content))
        tool = dict(type='tool_use', id='call1', name='Bash')
        rows = [dict(type='user', uuid='u1', message={'content': 'hello'}),
                dict(type='user', uuid='u1', message={'content': 'hello'}),
                assistant('r1', 1, 0, [tool]), assistant('r1', 2, 5, [tool]),
                assistant('r1', 7, 10, [{'type': 'text', 'text': 'done'}]),
                dict(type='user', message={'content': [{'type': 'tool_result'}]}),
                assistant('r2', 3, 25, [])]
        r = ledger.read_session(self.write('claude/s.jsonl', rows))
        self.assertEqual(r['responses'], 2)
        self.assertEqual(r['out'], 10)
        self.assertEqual(r['input'], 2000)
        self.assertEqual(r['tool_calls'], 1)
        self.assertEqual(r['user_turns'], 1)
        self.assertEqual(r['wall_s'], 900)
        self.assertEqual(r['idle_s'], 900)

    def test_codex_totals_inclusive_repeat_and_allowance_change(self):
        a, b = usage(100, 80, 10, 3), usage(250, 200, 30, 8)
        rows = [record('session_meta', {'id': 's', 'cwd': '/x/principles-of-good-architects'}),
                record('turn_context', {'model': 'gpt-example'}),
                record('response_item', {'type': 'message', 'role': 'user', 'id': 'u'}),
                record('response_item', {'type': 'message', 'role': 'user', 'id': 'env',
                    'content': [{'type': 'input_text', 'text': '<environment_context>host</environment_context>'}]}),
                record('response_item', {'type': 'function_call', 'call_id': 'c', 'name': 'exec'}),
                record('response_item', {'type': 'function_call', 'call_id': 'c', 'name': 'exec'}),
                count(a), count(a, reset=200), count(b, usage(150, 120, 20, 5), 20, reset=200)]
        r = ledger.read_codex_session(self.write('codex/s.jsonl', rows))
        self.assertEqual((r['input'], r['fresh'], r['cache_read'], r['out'], r['reasoning']),
                         (250, 50, 200, 30, 8))
        self.assertEqual(r['tokens'], 280)
        self.assertEqual(r['responses'], 2)
        self.assertEqual(r['tool_calls'], 1)
        self.assertEqual(r['user_turns'], 1)
        self.assertEqual(len(r['allowance_events']), 1)
        self.assertEqual(r['ctxs'], [100, 150])
        self.assertEqual(r['project'], 'principles-of-good-architects')

    def test_counter_discontinuity_is_explicit_and_last_snapshot_wins(self):
        r = ledger.read_codex_session(self.write('s.jsonl', [
            count(usage(100, 80, 10)), count(usage(20, 10, 2), minute=5)]))
        self.assertEqual(r['input'], 20)
        self.assertEqual(r['counter_discontinuities'], 1)
        self.assertTrue(r['warnings'])

    def test_resumed_files_share_identity_and_replayed_counts_are_not_responses(self):
        meta = record('session_meta', {'id': 'shared', 'cwd': '/x/principles-of-good-architects'})
        a, b = usage(100, 80, 10), usage(250, 200, 30)
        self.write('codex/one.jsonl', [meta, count(a)])
        self.write('codex/sub/two.jsonl', [meta, count(a), count(b, usage(150,120,20), 20)])
        rows = ledger.read_sessions(self.root/'claude', days=10000, codex_dir=self.root/'codex')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['responses'], 2)
        self.assertEqual(rows[0]['input'], 250)
        self.assertEqual(len(rows[0]['files']), 2)

    def test_project_filter_and_shared_report_show_exact_totals_and_runtime(self):
        self.write('codex/s.jsonl', [record('session_meta', {'id': 's',
            'cwd': '/x/principles-of-good-architects/.claude/worktrees/poga-1'}),
            record('turn_context', {'model': 'gpt-example'}),
            count(usage(11328300, 10944128, 22675, 6383))])
        self.write('codex/other.jsonl', [record('session_meta', {'id': 'other',
            'cwd': '/x/other'}), count(usage(1, 0, 1))])
        self.write('claude/-Users-x-principles-of-good-architects/c.jsonl', [
            {'type': 'assistant', 'message': {'id': 'r', 'model': 'claude',
                'usage': {'input_tokens': 100, 'output_tokens': 10}}}])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = ledger.main(['--days', '10000', '--projects-dir', str(self.root/'claude'),
                                  '--codex-dir', str(self.root/'codex'), '--project', 'federation'])
        self.assertEqual(result, 0)
        text = out.getvalue()
        for expected in ('runtime', 'codex', '11,328,300', '22,675', '6,383', 'responses',
                         'wall-clock between responses', 'claude | claude'):
            self.assertIn(expected, text)
        self.assertNotIn('other.jsonl', text)

    def test_identical_claude_fragments_count_once_and_missing_ids_are_disclosed(self):
        message = dict(id='r', usage={'input_tokens': 100, 'output_tokens': 10})
        fragment = dict(type='assistant', message=message)
        r = ledger.read_session(self.write('claude/duplicate.jsonl', [fragment, fragment]))
        self.assertEqual((r['responses'], r['tokens']), (1, 110))
        message.pop('id')
        r = ledger.read_session(self.write('claude/legacy.jsonl', [fragment, fragment]))
        self.assertEqual(r['responses'], 2)
        self.assertTrue(r['warnings'])

    def test_allowance_only_event_is_visible_without_usage_or_new_response(self):
        rows = [count(usage(100, 80, 10)), record('event_msg', {
            'type': 'token_count', 'info': None,
            'rate_limits': {'primary': {'resets_at': 200}}}, 5)]
        r = ledger.read_codex_session(self.write('c.jsonl', rows))
        self.assertEqual(r['responses'], 1)
        self.assertEqual(r['input'], 100)
        self.assertEqual(len(r['allowance_events']), 1)

    def test_missing_response_usage_and_replayed_final_counter_are_disclosed(self):
        a = usage(100, 80, 10)
        missing = record('event_msg', {'type': 'token_count', 'info': {
            'total_token_usage': usage(200, 160, 20)}})
        r = ledger.read_codex_session(self.write('c.jsonl', [count(a), missing, count(a, minute=5)]))
        self.assertEqual(r['input'], 100)
        self.assertEqual(r['responses'], 1)
        self.assertTrue(any('distribution incomplete' in w for w in r['warnings']))
        self.assertTrue(any('replays' in w for w in r['warnings']))

    def test_empty_and_malformed_tail_and_model_change(self):
        p = self.write('c.jsonl', [record('turn_context', {'model': 'one'}),
            count(usage(10, 0, 1)), record('turn_context', {'model': 'two'}, 5),
            count(usage(30, 0, 3), usage(20, 0, 2), 10)])
        with p.open('a') as f:
            f.write('{broken')
        r = ledger.read_codex_session(p)
        self.assertEqual(r['model'], 'one,two')
        self.assertEqual(ledger.summarize([])['responses'], 0)
        self.assertIsNone(ledger.read_codex_session(self.write('empty.jsonl', [])))

    def test_old_shards_of_resumed_session_remain_in_window(self):
        import os
        meta = record('session_meta', {'id': 'resumed', 'cwd': '/x/test'})
        p = self.write('codex/old.jsonl', [meta, count(usage(10, 0, 1))])
        os.utime(p, (1, 1))
        self.write('codex/new.jsonl', [meta, count(usage(30, 0, 3), usage(20, 0, 2), 10)])
        rows = ledger.read_sessions(self.root/'claude', days=1, codex_dir=self.root/'codex')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['responses'], 2)
        self.assertEqual(rows[0]['input'], 30)



def call(cid, text, ordinal=None, kind='custom_tool_call', name='exec', minute=0):
    """One model-visible tool call. Real transcripts wrap the shell command in a JS
    snippet, and the fixtures below keep that shape on purpose — a parser written
    against a bare command string reads nothing from a real file."""
    r = record('response_item', {'type': kind, 'call_id': cid, 'name': name,
                                 ('input' if kind == 'custom_tool_call' else 'arguments'): text},
               minute)
    if ordinal is not None:
        r['ordinal'] = ordinal
    return r


def output(cid, body, ordinal=None, kind='custom_tool_call_output', minute=0):
    r = record('response_item', {'type': kind, 'call_id': cid,
                                 'output': [{'type': 'input_text', 'text': body}]}, minute)
    if ordinal is not None:
        r['ordinal'] = ordinal
    return r


TRUNC = 'Warning: truncated output (original token count: 20055)\nTotal output lines: 965\n'


def js(cmd):
    return 'text(await tools.exec_command({cmd:"%s",max_output_tokens:12000}));' % cmd


class StartupAuditTests(unittest.TestCase):
    """WI-0324 / R4: the first ten tool calls carry no truncated output and no
    overlapping reads of a file that did not change in between."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, name, rows):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(''.join(json.dumps(r) + '\n' for r in rows))
        return p

    def test_a_whole_file_read_then_a_range_of_it_is_an_overlap(self):
        """The exact defect measured in an outside reviewer's transcript: `cat` the
        payload, get it truncated, then recover it with `sed` ranges that re-fetch
        bytes already paid for."""
        rows = [call('a', js('cat .session-state/session-context.md'), 1),
                output('a', TRUNC, 2),
                call('b', js("sed -n '130,380p' .session-state/session-context.md"), 3),
                output('b', 'fine', 4)]
        a = ledger.startup_audit(rows)
        self.assertEqual(a['verdict'], 'FAIL')
        self.assertEqual(a['truncated_calls'], [1])
        self.assertEqual(len(a['overlaps']), 1)
        self.assertEqual(a['overlaps'][0]['path'], '.session-state/session-context.md')
        self.assertEqual((a['overlaps'][0]['first_call'],
                          a['overlaps'][0]['second_call']), (1, 2))

    def test_adjacent_chunks_that_do_not_overlap_are_not_flagged(self):
        """The behaviour the rule asks for must PASS, or the check fires on correct code
        and gets deleted."""
        rows = [call('a', js("sed -n '1,460p' habits/master.md"), 1),
                output('a', 'fine', 2),
                call('b', js("sed -n '461,940p' habits/master.md"), 3),
                output('b', 'fine', 4)]
        a = ledger.startup_audit(rows)
        self.assertEqual(a['overlaps'], [])
        self.assertEqual(a['truncated_calls'], [])
        self.assertEqual(a['verdict'], 'PASS')

    def test_a_reread_after_a_write_is_deliberate_and_is_not_counted_against_it(self):
        """R4 asks for this distinction by name. Re-reading a file that just changed is
        correct, and scoring it as waste would make the check fire on the code it
        exists to bless."""
        changed = record('event_msg', {'type': 'item_completed', 'item': {
            'type': 'FileChange', 'changes': {'/repo/sessionlib/land.py': {'type': 'update'}}}})
        changed['ordinal'] = 3
        rows = [call('a', js('cat sessionlib/land.py'), 1),
                output('a', 'fine', 2),
                changed,
                call('b', js('cat sessionlib/land.py'), 4),
                output('b', 'fine', 5)]
        a = ledger.startup_audit(rows)
        self.assertEqual(a['overlaps'], [], 'a write in between makes the re-read honest')
        self.assertEqual(len(a['rereads_after_change']), 1)
        self.assertEqual(a['verdict'], 'PASS')

    def test_a_call_with_no_output_record_is_unverifiable_not_clean(self):
        """"Couldn't tell" must never render the same as "checked and it's fine" — a
        missing output record cannot show the call was untruncated."""
        rows = [call('a', js('cat CANON.md'), 1)]
        a = ledger.startup_audit(rows)
        self.assertEqual(a['unverifiable_calls'], [1])
        self.assertEqual(a['verdict'], 'UNVERIFIABLE')

    def test_a_transcript_with_no_tool_calls_cannot_pass(self):
        """PASS requires positive evidence. An empty window is not a clean window."""
        a = ledger.startup_audit([record('session_meta', {'id': 's', 'cwd': '/x'})])
        self.assertEqual(a['verdict'], 'UNVERIFIABLE')
        self.assertEqual(a['calls_examined'], 0)
        self.assertFalse(a['full_window'])

    def test_a_short_window_is_marked_weak_evidence(self):
        """A session that made one tool call literally satisfies "the first ten contain
        no truncation", and reading that as equal to ten clean calls is the trap."""
        rows = [call('a', js("sed -n '1,5p' x.md"), 1), output('a', 'fine', 2)]
        a = ledger.startup_audit(rows, limit=10)
        self.assertEqual(a['verdict'], 'PASS')
        self.assertFalse(a['full_window'])
        self.assertEqual(a['calls_examined'], 1)

    def test_only_the_first_limit_calls_are_examined(self):
        rows = []
        for i in range(6):
            rows += [call(str(i), js('cat a%d.md' % i), i * 2 + 1),
                     output(str(i), TRUNC if i >= 3 else 'fine', i * 2 + 2)]
        a = ledger.startup_audit(rows, limit=3)
        self.assertEqual(a['calls_examined'], 3)
        self.assertEqual(a['truncated_calls'], [])
        self.assertTrue(a['full_window'])

    def test_calls_are_ordered_by_ordinal_not_by_file_order(self):
        """Two records inside one turn share a timestamp to the millisecond, so a
        timestamp sort reorders them arbitrarily; `ordinal` is the real sequence."""
        rows = [call('b', js('cat second.md'), 3),
                output('b', 'fine', 4),
                call('a', js('cat first.md'), 1),
                output('a', 'fine', 2)]
        calls, _ = ledger.codex_tool_calls(rows)
        self.assertEqual([c['call_id'] for c in calls], ['a', 'b'])

    def test_a_path_stops_at_the_quote_that_closes_the_js_argument(self):
        """A greedy `\\S+` swallows the closing quote and the rest of the JSON object
        into the path, and a path that matches nothing is a read the audit cannot see."""
        text = ('const r = await tools.exec_command({cmd:"sed -n \'1,240p\' /tmp/f.py",'
                '"workdir":"/repo","max_output_tokens":3000}); text(r.output);')
        reads = ledger._parse_reads(text)
        self.assertEqual([r[0] for r in reads], ['/tmp/f.py'])

    def test_prose_after_cat_is_not_read_as_a_filename(self):
        """An unrestricted match produced findings against a file literally named "the"."""
        self.assertEqual(ledger._parse_reads('please cat the file for me'), [])
        self.assertEqual([r[0] for r in ledger._parse_reads('cat a/b.md')], ['a/b.md'])

    def test_the_bounded_reader_is_understood_as_a_bounded_read(self):
        """`session.py show --bytes A-B` is the command the rule tells an agent to use;
        an audit that could not parse it would score compliant sessions as overlapping."""
        r = ledger._parse_reads('python3 session.py show big.md --bytes 0-40000')
        self.assertEqual(r, [('big.md', 'byte', 0, 40000)])
        rows = [call('a', js('python3 session.py show big.md --bytes 0-40000'), 1),
                output('a', 'fine', 2),
                call('b', js('python3 session.py show big.md --bytes 40000-80000'), 3),
                output('b', 'fine', 4)]
        self.assertEqual(ledger.startup_audit(rows)['overlaps'], [])

    def test_a_line_range_and_a_byte_range_are_not_compared(self):
        """Comparing them needs the file, and guessing manufactures overlaps that did
        not happen."""
        rows = [call('a', js("sed -n '1,10p' f.md"), 1), output('a', 'fine', 2),
                call('b', js('python3 session.py show f.md --bytes 0-50'), 3),
                output('b', 'fine', 4)]
        self.assertEqual(ledger.startup_audit(rows)['overlaps'], [])

    def test_two_reads_inside_one_call_are_not_scored_against_each_other(self):
        """Spans issued together cannot be a recovery from each other's truncation."""
        rows = [call('a', js("sed -n '1,10p' f.md; cat f.md"), 1),
                output('a', 'fine', 2)]
        self.assertEqual(ledger.startup_audit(rows)['overlaps'], [])

    def test_the_audit_rides_on_the_codex_row(self):
        """The verdict must reach `--json` and the report without a second parse."""
        rows = [record('session_meta', {'id': 's', 'cwd': '/x/principles-of-good-architects'}),
                call('a', js('cat CANON.md'), 1), output('a', TRUNC, 2),
                count(usage(100, 80, 10, 3), usage(100, 80, 10, 3))]
        r = ledger.read_codex_session(self.write('codex/s.jsonl', rows))
        self.assertEqual(r['startup']['verdict'], 'FAIL')
        self.assertEqual(r['startup']['truncated_calls'], [1])

    def test_the_report_names_claude_sessions_as_not_applicable(self):
        """A reader who sees only Codex rows cannot tell whether the Claude ones passed
        or were never asked."""
        out = ledger.startup_report([{'runtime': 'claude-code', 'file': 'c.jsonl'}])
        self.assertIn('no Codex sessions', out)
        self.assertIn('n/a', out)

    # --- tests written to kill mutations that survived the sweep -------------------
    # Each one below exists because a mutation to the line it covers left the suite
    # green. They are not extra coverage for its own sake: a surviving mutation is a
    # line whose behaviour no assertion depends on.

    def test_a_sed_range_is_parsed_to_its_actual_half_open_span(self):
        """`sed -n 'A,Bp'` is inclusive of B; the span must be half-open or two
        genuinely adjacent chunks read as overlapping by one line."""
        self.assertEqual(ledger._parse_reads(js("sed -n '130,380p' f.md")),
                         [('f.md', 'line', 130, 381)])

    def test_head_dash_c_is_a_byte_bounded_read(self):
        """`head -c N` is the one shell idiom that is already byte-bounded; an audit
        blind to it would score a compliant read as an unbounded one."""
        self.assertEqual(ledger._parse_reads(js('head -c 4096 big.md')),
                         [('big.md', 'byte', 0, 4096)])

    def test_an_open_ended_byte_range_keeps_its_open_end(self):
        """`--bytes 40000-` means "to the end"; collapsing the missing end to 0 would
        make the span empty and overlap nothing."""
        self.assertEqual(ledger._parse_reads('session.py show f.md --bytes 40000-'),
                         [('f.md', 'byte', 40000, None)])
        self.assertEqual(ledger._parse_reads('session.py show f.md --bytes -900'),
                         [('f.md', 'byte', 0, 900)])

    def test_a_section_read_is_treated_as_unbounded(self):
        """A named section can be any size, so it must overlap any other read of that
        file rather than be assumed small."""
        self.assertEqual(ledger._parse_reads("session.py show f.md --section 'Scope'"),
                         [('f.md', 'whole', 0, None)])

    def test_a_flag_after_cat_is_not_a_filename(self):
        """`cat -n f.md` reads one file, not two."""
        self.assertEqual([r[0] for r in ledger._parse_reads(js('cat -n f.md'))],
                         ['f.md'])

    def test_chunks_read_out_of_order_still_do_not_overlap(self):
        """The boundary case the in-order fixture cannot reach: when the SECOND chunk is
        read first, the adjacency test runs with its operands swapped. An off-by-one
        there reports every correctly-chunked pair as an overlap - a check that fires on
        the behaviour it asks for gets deleted."""
        rows = [call('a', js("sed -n '461,940p' habits/master.md"), 1),
                output('a', 'fine', 2),
                call('b', js("sed -n '1,460p' habits/master.md"), 3),
                output('b', 'fine', 4)]
        self.assertEqual(ledger.startup_audit(rows)['overlaps'], [])

    def test_a_record_carrying_an_ordinal_sorts_ahead_of_one_without(self):
        """Ordinal is the real sequence; a timestamp is shared to the millisecond inside
        one turn. A transcript mixing both must not fall back to timestamps wholesale."""
        rows = [call('late_but_ordinal', js('cat a.md'), ordinal=1, minute=50),
                call('early_no_ordinal', js('cat b.md'), minute=10)]
        calls, _ = ledger.codex_tool_calls(rows)
        self.assertEqual([c['call_id'] for c in calls],
                         ['late_but_ordinal', 'early_no_ordinal'])

    def test_a_write_AFTER_the_second_read_does_not_excuse_it(self):
        """The bug a mutation sweep found and the original fixture missed: the change
        index was computed from a list that was still empty, so EVERY write recorded
        position 0 and retroactively blessed every earlier overlap. A write must only
        excuse a re-read that comes after it."""
        changed = record('event_msg', {'type': 'item_completed', 'item': {
            'type': 'FileChange', 'changes': {'/repo/f.md': {'type': 'update'}}}})
        changed['ordinal'] = 5
        rows = [call('a', js('cat f.md'), 1), output('a', 'fine', 2),
                call('b', js('cat f.md'), 3), output('b', 'fine', 4),
                changed]
        a = ledger.startup_audit(rows)
        self.assertEqual(a['rereads_after_change'], [],
                         'the write came after both reads; it excuses neither')
        self.assertEqual(len(a['overlaps']), 1)
        self.assertEqual(a['verdict'], 'FAIL')

    def test_a_changed_path_matches_on_a_path_boundary_not_a_suffix(self):
        """`/repo/xland.py` is not `land.py`. Matching a bare suffix would let an
        unrelated file's write excuse a re-read."""
        changed = record('event_msg', {'type': 'item_completed', 'item': {
            'type': 'FileChange', 'changes': {'/repo/xland.py': {'type': 'update'}}}})
        changed['ordinal'] = 3
        rows = [call('a', js('cat land.py'), 1), output('a', 'fine', 2),
                changed,
                call('b', js('cat land.py'), 4), output('b', 'fine', 5)]
        a = ledger.startup_audit(rows)
        self.assertEqual(a['rereads_after_change'], [])
        self.assertEqual(len(a['overlaps']), 1)

    def test_a_finding_reports_the_two_spans_it_compared(self):
        """A finding that names only the calls cannot be checked by hand; the spans are
        what make it auditable."""
        rows = [call('a', js("sed -n '1,100p' f.md"), 1), output('a', 'fine', 2),
                call('b', js("sed -n '50,150p' f.md"), 3), output('b', 'fine', 4)]
        o = ledger.startup_audit(rows)['overlaps'][0]
        self.assertEqual(o['first'], ('line', 1, 101))
        self.assertEqual(o['second'], ('line', 50, 151))

    def test_the_truncated_token_count_is_carried_not_just_the_flag(self):
        """How much was lost is the number that says whether a read was nearly complete
        or barely started."""
        rows = [call('a', js('cat CANON.md'), 1), output('a', TRUNC, 2)]
        calls, _ = ledger.codex_tool_calls(rows)
        self.assertTrue(calls[0]['truncated'])
        self.assertEqual(calls[0]['truncated_tokens'], 20055)

    def test_the_report_marks_a_short_window_in_the_row_and_the_tally(self):
        row = {'runtime': 'codex', 'project': 'p', 'file': 's.jsonl',
               'startup': {'verdict': 'PASS', 'limit': 10, 'calls_examined': 1,
                           'full_window': False, 'truncated_calls': [], 'overlaps': [],
                           'rereads_after_change': [], 'unverifiable_calls': []}}
        out = ledger.startup_report([row])
        self.assertIn('weak evidence', out)
        self.assertIn('PASS (short)=1', out)


def prompt(text, ordinal=None, minute=0):
    """One `user`-role message, the shape a Codex transcript records an opening prompt in."""
    r = record('response_item', {'type': 'message', 'role': 'user',
                                 'content': [{'type': 'input_text', 'text': text}]}, minute)
    if ordinal is not None:
        r['ordinal'] = ordinal
    return r


EMBEDDED = ('=== SESSION START ===\nversion: v2.77.0\n...payload...\n'
            '--- end of session-start context ---\n'
            'The same bytes are on disk at .session-state/session-context.md')
POINTED = ('Read .session-state/session-context.md first - it carries your session-start '
           'orientation, the principles and universal habits you inherit. Use '
           '`python3 session.py show .session-state/session-context.md --bytes 0-40000`.')
BARE = 'run your startup and tell me who you are'


class CanonDeliveryClassTests(unittest.TestCase):
    """WI-0324 / R4: which delivery the session was HANDED, reported beside the reading
    verdict. The two are separate claims, and conflating them is the error this item made
    and withdrew on 2026-09-18: a session handed nothing cannot re-read it, so its clean
    overlap record says nothing about the rule that travels inside the payload."""

    def test_an_embedded_payload_is_recognised(self):
        self.assertEqual(ledger.codex_delivery([prompt(EMBEDDED)])['class'], 'embedded')

    def test_a_pointer_is_recognised_and_is_not_an_embed(self):
        self.assertEqual(ledger.codex_delivery([prompt(POINTED)])['class'], 'pointed')

    def test_a_bare_prompt_is_absent_not_unknown(self):
        """The measured shape of both authorized readings: poga's own 40-character default
        prompt and no canon at all. `absent` is a finding; `unknown` would hide it."""
        d = ledger.codex_delivery([prompt(BARE)])
        self.assertEqual(d['class'], 'absent')
        self.assertEqual(d['prompt_chars'], len(BARE))

    def test_a_transcript_with_no_operator_prompt_is_unknown(self):
        """No prompt in the transcript means the question was not answered - which is not
        the same as answering that nothing was delivered."""
        rows = [record('session_meta', {'id': 's', 'cwd': '/x'}),
                call('a', js('cat CANON.md'), 1), output('a', 'fine', 2)]
        self.assertEqual(ledger.codex_delivery(rows)['class'], 'unknown')

    def test_the_harness_block_before_the_prompt_is_not_the_prompt(self):
        """Measured on a real transcript: `<environment_context>` arrives as a user-role
        message at record 5, the operator's prompt at record 8. Reading the first user
        message blindly classifies every session `absent`, embedded ones included."""
        rows = [prompt('<environment_context>\n  <cwd>/x</cwd>\n</environment_context>', 1),
                prompt(EMBEDDED, 2)]
        self.assertEqual(ledger.codex_delivery(rows)['class'], 'embedded')

    def test_a_later_turn_does_not_overwrite_the_opening_delivery(self):
        """Delivery is a fact about how the session STARTED. A pointer typed into turn
        four does not retroactively make the opening an embed, or the reverse."""
        rows = [prompt(BARE, 1), prompt(EMBEDDED, 2)]
        self.assertEqual(ledger.codex_delivery(rows)['class'], 'absent')

    def test_delivery_rides_on_the_audit_without_moving_the_verdict(self):
        """The separation, asserted rather than described: identical calls under two
        deliveries give one verdict and two delivery classes."""
        calls = [call('a', js('cat CANON.md'), 2), output('a', TRUNC, 3)]
        absent = ledger.startup_audit([prompt(BARE, 1)] + calls)
        embed = ledger.startup_audit([prompt(EMBEDDED, 1)] + calls)
        self.assertEqual(absent['verdict'], embed['verdict'], 'FAIL')
        self.assertEqual((absent['delivery'], embed['delivery']), ('absent', 'embedded'))

    def test_the_report_names_the_delivery_on_every_row(self):
        row = {'runtime': 'codex', 'project': 'p', 'file': 's.jsonl',
               'startup': {'verdict': 'FAIL', 'limit': 10, 'calls_examined': 10,
                           'full_window': True, 'truncated_calls': [2], 'overlaps': [],
                           'rereads_after_change': [], 'unverifiable_calls': [],
                           'delivery': 'absent', 'prompt_chars': 40}}
        out = ledger.startup_report([row])
        self.assertIn('absent', out)
        self.assertIn('delivery: absent=1', out)

    def test_a_window_with_no_embed_says_so_rather_than_leaving_it_to_be_noticed(self):
        """The whole point. A reader who sees PASS and FAIL counts will reason about
        them; if not one session was handed the payload, every reading is about the
        degraded path, and the report has to say that out loud."""
        row = {'runtime': 'codex', 'project': 'p', 'file': 's.jsonl',
               'startup': {'verdict': 'PASS', 'limit': 10, 'calls_examined': 10,
                           'full_window': True, 'truncated_calls': [], 'overlaps': [],
                           'rereads_after_change': [], 'unverifiable_calls': [],
                           'delivery': 'pointed', 'prompt_chars': 300}}
        self.assertIn('no verdict above is evidence about it', ledger.startup_report([row]))

    def test_the_caveat_is_absent_once_a_session_was_actually_embedded(self):
        """A caveat that prints unconditionally is wallpaper, and stops being read on the
        first run where it matters."""
        row = {'runtime': 'codex', 'project': 'p', 'file': 's.jsonl',
               'startup': {'verdict': 'PASS', 'limit': 10, 'calls_examined': 10,
                           'full_window': True, 'truncated_calls': [], 'overlaps': [],
                           'rereads_after_change': [], 'unverifiable_calls': [],
                           'delivery': 'embedded', 'prompt_chars': 56664}}
        out = ledger.startup_report([row])
        self.assertIn('delivery: embedded=1', out)
        self.assertNotIn('no verdict above is evidence about it', out)

    def test_the_sentinels_this_audit_matches_still_exist_in_poga(self):
        """The detector keys on text the LAUNCHER writes. If `deliver_the_context` is
        reworded, this audit does not break loudly - it classifies every session `absent`,
        a real class, and goes on printing confident rows. Tie the two together here, in
        the only place that can notice: read the producer and fail if either line is gone.

        This is the guard the fixtures cannot be: every test above builds its own text,
        so all of them pass against a launcher that no longer emits any of it."""
        poga = (Path(__file__).resolve().parents[1] / 'poga').read_text()
        self.assertIn(ledger.CANON_EMBED_SENTINEL, poga,
                      'the embed sentinel is gone from poga; the audit now reads every '
                      'embedded session as absent')
        self.assertIn(ledger.CANON_POINTER_SENTINEL, poga,
                      'the pointer sentinel is gone from poga; the audit now reads every '
                      'pointed session as absent')


if __name__ == '__main__':
    unittest.main()
