import copy
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import uuid

from evaluation.insights import RevisionConflict
from evaluation.roster import RosterStore


def record(device, identifier=None):
    return dict(source_device=device, id=identifier or uuid.uuid4().hex * 2)


class RosterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = RosterStore(self.root)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def employee(self, name='同名员工', **settings):
        before = {row['id'] for row in self.store.snapshot()['employees']}
        snapshot = self.store.save_employee(name, **settings)
        return next(row for row in snapshot['employees'] if row['id'] not in before)

    def test_same_names_are_separate_people_and_disabling_preserves_associations(self):
        first = self.employee(department='电气')
        second = self.employee(department='软件')
        self.assertNotEqual(first['id'], second['id'])
        device = str(uuid.uuid4())
        self.store.bind_device(device, first['id'], '开发机')
        account = self.store.save_account(first['id'], 'sevenova', str(uuid.uuid4()))['accounts'][0]
        log = record(device)
        self.store.claim_records([log])
        self.store.save_employee('已离职同名员工', '电气', active=False, employee_id=first['id'])
        snapshot = self.store.snapshot()
        updated = next(row for row in snapshot['employees'] if row['id'] == first['id'])
        self.assertFalse(updated['active'])
        self.assertEqual(updated['created_at'], first['created_at'])
        self.assertEqual(snapshot['devices'][0]['employee_id'], first['id'])
        self.assertEqual(snapshot['accounts'], [account])
        self.assertEqual(self.store.record_owners([log]), {(device, log['id']): first['id']})
        self.assertEqual(len(snapshot['employees']), 2)

    def test_multiple_devices_and_explicit_transfer_never_reassign_claimed_history(self):
        first, second = self.employee('员工甲'), self.employee('员工乙')
        one, two = str(uuid.uuid4()), str(uuid.uuid4())
        old, later, other = record(one), record(one), record(two)
        self.assertEqual(self.store.claim_records([old]), dict(claimed=0, unchanged=0, unbound=1))
        self.assertEqual(self.store.record_owners([old]), {})
        self.store.bind_device(one, first['id'], '笔记本')
        self.store.bind_device(two, first['id'], '台式机')
        self.assertEqual(self.store.claim_records([old, other]), dict(claimed=2, unchanged=0, unbound=0))
        self.store.bind_device(one, second['id'], '交接后的笔记本')
        self.assertEqual(self.store.claim_records([old, later]), dict(claimed=1, unchanged=1, unbound=0))
        owners = self.store.record_owners([old, later, other])
        self.assertEqual(owners[(one, old['id'])], first['id'])
        self.assertEqual(owners[(one, later['id'])], second['id'])
        self.assertEqual(owners[(two, other['id'])], first['id'])
        transfer = next(event for event in self.store.snapshot()['history'] if event['type'] == 'device_rebound')
        self.assertEqual(transfer['before']['employee_id'], first['id'])
        self.assertEqual(transfer['after']['employee_id'], second['id'])
        self.store.unbind_device(one)
        self.assertEqual(self.store.record_owners([old, later, other]), owners)
        self.assertEqual(self.store.claim_records([record(one)]), dict(claimed=0, unchanged=0, unbound=1))

    def test_linear_account_slug_normalization_and_global_conflicts(self):
        first, second = self.employee('员工甲'), self.employee('员工乙')
        user = str(uuid.uuid4())
        saved = self.store.save_account(first['id'], ' Sevenova-Work ', user, 'Linear名称')
        account = saved['accounts'][0]
        self.assertEqual(account['workspace'], 'sevenova-work')
        before = self.store.snapshot()
        for person in (first, second):
            with self.assertRaisesRegex(ValueError, '已归属'):
                self.store.save_account(person['id'], 'SEVENOVA-WORK', user)
        self.assertEqual(self.store.snapshot(), before)
        # User UUIDs are scoped by workspace; these two accounts are distinct.
        self.store.save_account(second['id'], 'other-workspace', user)
        self.assertEqual(len(self.store.snapshot()['accounts']), 2)
        self.store.save_account(second['id'], 'sevenova-work', user, '明确转交', account_id=account['id'])
        moved = next(row for row in self.store.snapshot()['accounts'] if row['id'] == account['id'])
        self.assertEqual(moved['employee_id'], second['id'])
        self.store.remove_account(account['id'])
        self.assertEqual(len(self.store.snapshot()['accounts']), 1)
        self.assertEqual(self.store.snapshot()['history'][-1]['before']['id'], account['id'])

    def test_strict_identity_workspace_and_boolean_validation_are_atomic(self):
        employee = self.employee()
        before = self.store.snapshot()
        for workspace in ('https://linear.app/sevenova', 'lin_api_fake_credential', 'sk-fake-credential',
                          'space name', 'x/y', '-leading', 'trailing-', '', 'a'*101):
            with self.subTest(workspace=workspace):
                with self.assertRaises(ValueError):
                    self.store.save_account(employee['id'], workspace, str(uuid.uuid4()))
        for action in (
                lambda: self.store.save_employee(' ', employee_id=employee['id']),
                lambda: self.store.save_employee('员工', active=1),
                lambda: self.store.save_employee('员工', employee_id=str(uuid.uuid4())),
                lambda: self.store.bind_device('device-name', employee['id']),
                lambda: self.store.bind_device(str(uuid.uuid4()), str(uuid.uuid4())),
                lambda: self.store.save_account(employee['id'], 'sevenova', 'display-name'),
                lambda: self.store.save_account(employee['id'], 'sevenova', '00000000-0000-4000-8000-00000000000A'),
                lambda: self.store.remove_account(str(uuid.uuid4()))):
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(self.store.snapshot(), before)

    def test_revision_conflicts_and_snapshot_reads_do_not_mutate_state(self):
        created = self.store.save_employee('员工', expected_revision=0)
        employee = created['employees'][0]
        for action in (
                lambda: self.store.save_employee('员工更新', employee_id=employee['id'], expected_revision=0),
                lambda: self.store.bind_device(str(uuid.uuid4()), employee['id'], expected_revision=True),
                lambda: self.store.save_account(employee['id'], 'sevenova', str(uuid.uuid4()), expected_revision=0)):
            with self.assertRaises(RevisionConflict):
                action()
        self.assertEqual(self.store.snapshot(), created)
        self.assertEqual(self.store.record_owners([record(str(uuid.uuid4()))]), {})
        self.assertEqual(self.store.snapshot(), created)

    def test_restart_preserves_fixed_ownership_and_returns_detached_snapshots(self):
        employee = self.employee()
        device = str(uuid.uuid4())
        row = record(device)
        self.store.bind_device(device, employee['id'])
        self.store.claim_records([row])
        before = self.store.snapshot()
        modified = copy.deepcopy(before)
        modified['employees'][0]['name'] = '调用方的临时修改'
        self.store.close()
        self.store = RosterStore(self.root)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.store.record_owners([row]), {(device, row['id']): employee['id']})
        revision = before['revision']
        self.assertEqual(self.store.claim_records([row, row]), dict(claimed=0, unchanged=1, unbound=0))
        self.assertEqual(self.store.snapshot()['revision'], revision)

    def test_audit_failure_rolls_back_employee_and_revision(self):
        self.store.db.execute('''CREATE TRIGGER reject_audit BEFORE INSERT ON roster_history
          BEGIN SELECT RAISE(ABORT,'injected audit failure'); END''')
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.save_employee('不应部分创建的人员')
        self.assertEqual(self.store.snapshot(), dict(revision=0, employees=[], devices=[], accounts=[], history=[]))

    def test_record_claim_batch_failure_rolls_back_ownership_and_audit(self):
        employee = self.employee()
        device = str(uuid.uuid4())
        self.store.bind_device(device, employee['id'])
        before = self.store.snapshot()
        first, second = record(device, 'a'*64), record(device, 'b'*64)
        self.store.db.execute('''CREATE TRIGGER reject_claim BEFORE INSERT ON roster_claims
          WHEN NEW.record_id=''' + "'" + second['id'] + "'" + ''' BEGIN SELECT RAISE(ABORT,'claim failure'); END''')
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.claim_records([first, second])
        self.assertEqual(self.store.record_owners([first, second]), {})
        self.assertEqual(self.store.snapshot(), before)
        with self.assertRaises(ValueError):
            self.store.claim_records([first, dict(source_device=device, id='invalid-id')])
        self.assertEqual(self.store.record_owners([first]), {})

    def test_two_connections_with_same_revision_have_one_successful_writer(self):
        other = RosterStore(self.root)
        barrier = threading.Barrier(2)
        successes, errors = [], []
        def save(store, name):
            try:
                barrier.wait(2)
                successes.append(store.save_employee(name, expected_revision=0))
            except Exception as exc:
                errors.append(exc)
        one = threading.Thread(target=save, args=(self.store, '甲'))
        two = threading.Thread(target=save, args=(other, '乙'))
        try:
            one.start()
            two.start()
            one.join(3)
            two.join(3)
            self.assertFalse(one.is_alive() or two.is_alive())
            self.assertEqual(len(successes), 1)
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], RevisionConflict)
            self.assertEqual(len(self.store.snapshot()['employees']), 1)
        finally:
            other.close()


if __name__ == '__main__':
    unittest.main()
