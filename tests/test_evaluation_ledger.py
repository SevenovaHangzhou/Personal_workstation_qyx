import copy
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import uuid

from evaluation.insights import RevisionConflict
from evaluation.ledger import IssueLedger


def source():
    return dict(id=uuid.uuid4().hex * 2, digest=uuid.uuid4().hex * 2, employee_id=str(uuid.uuid4()))


class IssueLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = IssueLedger(self.root)
        self.evidence = [source()]

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def save(self, issue='issue-1', **updates):
        arguments = dict(issue_id=issue, summary='接口已经完成，待实机核对', blockers='驱动尚未就绪',
                         next_step='联调控制接口', editor='评估人员', sources=self.evidence)
        arguments.update(updates)
        return self.store.save(**arguments)

    def test_current_notes_and_append_only_history_keep_exact_source_revisions(self):
        first = self.save(expected_revision=0)
        old_sources = copy.deepcopy(self.evidence)
        self.evidence[0]['digest'] = 'a' * 64
        second = self.save(summary='联调已经补充，等待确认剩余阻碍', expected_revision=1)
        self.assertEqual(first['revision'], 1)
        self.assertEqual(second['revision'], 2)
        self.assertEqual(self.store.history('issue-1')[0]['sources'], old_sources)
        self.assertEqual(self.store.history('issue-1')[1]['sources'], self.evidence)
        self.assertEqual(self.store.snapshot({'issue-1'})['updates'], [second])
        second['summary'] = '外部修改不可污染已保存结果'
        self.assertNotEqual(self.store.history('issue-1')[-1]['summary'], second['summary'])
        self.store.close()
        self.store = IssueLedger(self.root)
        self.assertEqual(len(self.store.history('issue-1')), 2)
        self.assertEqual(self.store.snapshot({'issue-1'})['revision'], 2)

    def test_empty_or_unrelated_allowed_scope_never_exposes_other_issue_notes(self):
        one = self.save('issue-1')
        two = self.save('issue-2', summary='另一项目的进展')
        self.assertEqual(self.store.snapshot([]), dict(revision=2, updates=[]))
        self.assertEqual(self.store.snapshot({'issue-1'})['updates'], [one])
        self.assertEqual(self.store.snapshot({'issue-2'})['updates'], [two])
        self.assertEqual(self.store.history('unknown'), [])
        with self.assertRaises(ValueError):
            self.store.snapshot(None)

    def test_global_revision_conflict_does_not_write_any_history(self):
        self.save('issue-1', expected_revision=0)
        with self.assertRaises(RevisionConflict):
            self.save('issue-2', expected_revision=0)
        self.assertEqual(self.store.snapshot({'issue-1', 'issue-2'})['revision'], 1)
        self.assertEqual(self.store.history('issue-2'), [])
        with self.assertRaises(ValueError):
            self.save(expected_revision=True)

    def test_strict_fields_and_source_whitelist_reject_unverified_or_oversized_data(self):
        invalid = [dict(editor=' '), dict(editor='a' * 81), dict(summary='x' * 6001),
                   dict(summary=' ', blockers=' ', next_step=' '), dict(sources=[]),
                   dict(sources=self.evidence * 101),
                   dict(sources=[dict(self.evidence[0], digest='wrong')]),
                   dict(sources=[dict(self.evidence[0], employee_id='name-as-id')]),
                   dict(sources=[dict(self.evidence[0], raw_chat='must not enter ledger')]),
                   dict(sources=[self.evidence[0], dict(self.evidence[0], digest='b' * 64)])]
        for updates in invalid:
            with self.subTest(updates=updates):
                with self.assertRaises(ValueError):
                    self.save(**updates)
        self.assertEqual(self.store.snapshot({'issue-1'})['revision'], 0)
        self.assertEqual(self.store.history('issue-1'), [])

    def test_save_transaction_failure_rolls_back_current_history_and_global_revision(self):
        original = self.save(expected_revision=0)
        self.store.db.execute('''CREATE TRIGGER reject_current BEFORE INSERT ON ledger_current
          BEGIN SELECT RAISE(ABORT,'injected current write failure'); END''')
        with self.assertRaises(sqlite3.IntegrityError):
            self.save(summary='这个修订不得部分落盘', expected_revision=1)
        self.assertEqual(self.store.snapshot({'issue-1'}), dict(revision=1, updates=[original]))
        self.assertEqual(self.store.history('issue-1'), [original])

    def test_two_connections_using_same_expected_revision_have_one_winner(self):
        other = IssueLedger(self.root)
        barrier = threading.Barrier(2)
        successes, failures = [], []
        def save(store, label):
            try:
                barrier.wait(timeout=2)
                successes.append(store.save('issue-1', label, '', '', '评估人员', self.evidence, expected_revision=0))
            except Exception as exc:
                failures.append(exc)
        first = threading.Thread(target=save, args=(self.store, '更新一'))
        second = threading.Thread(target=save, args=(other, '更新二'))
        try:
            first.start()
            second.start()
            first.join(3)
            second.join(3)
            self.assertFalse(first.is_alive() or second.is_alive())
            self.assertEqual(len(successes), 1)
            self.assertEqual(len(failures), 1)
            self.assertIsInstance(failures[0], RevisionConflict)
            self.assertEqual(len(self.store.history('issue-1')), 1)
            self.assertEqual(self.store.snapshot({'issue-1'})['revision'], 1)
        finally:
            other.close()


if __name__ == '__main__':
    unittest.main()
