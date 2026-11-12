"""Real Yomk roster workflows, with isolated replicas and no external services."""
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from evaluation.protocol import PROTOCOL, record_digest, record_id
from runtime import MomoRuntime


def journal(device, *, source='nightly:2026-11-11', issue_id=None, assignee=None,
            workspace='sevenova', period_end='2026-11-11T22:00:00+08:00'):
    task_id = str(uuid.uuid4())
    row = dict(id=record_id(source, task_id), employee_id=device, source_device=device,
               name='同名设备使用者', department='电气', source_id=source, kind='nightly',
               period_start='2026-11-10T22:00:00+08:00', period_end=period_end,
               task_id=task_id, task_title='共享接口开发', issue_id=issue_id or str(uuid.uuid4()),
               issue_identifier='EE-101', issue_url=f'https://linear.app/{workspace}/issue/EE-101/shared-api',
               parent_issue_id='', linear_state_type='started', summary='接口实现完成，等待现场联调。', evidence=[])
    if assignee is not None:
        row['linear_assignee_id'] = assignee
    row['digest'] = record_digest(row)
    return row


class EvaluationRosterServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.start_runtime()

    def start_runtime(self):
        # The service and requests are real. Only the optional discovery loop is
        # withheld; peer imports validate configuration without opening sockets.
        with patch('services.EvaluationService.EvaluationService.start'):
            self.runtime = MomoRuntime(self.root / 'tasks.json', profile='work', auto_tick=False).start()
        self.addCleanup(self.runtime.close, apply_update=False)
        self.service = self.runtime.evaluation_service
        self.client = self.runtime.evaluation

    def enable(self):
        self.client.configure(name='名册管理员', evaluator=True)

    def roster(self):
        return self.client.snapshot()['roster']

    def revision(self):
        return self.roster()['revision']

    def employee(self, name='张工', department='电气'):
        previous = {p['id'] for p in self.roster()['employees']}
        result = self.client.roster_save_employee(name=name, department=department, active=True,
                                                   expected_revision=self.revision())
        self.assertEqual(result['revision'], self.revision())
        return next(p for p in result['employees'] if p['id'] not in previous)

    def pair(self, device=None):
        device = device or str(uuid.uuid4())
        self.client.add_peer(dict(protocol=PROTOCOL, employee_id=device, name='同名设备使用者', department='电气',
                                 url='https://127.0.0.1:1', fingerprint='0' * 64, token='a' * 43))
        return device

    def seed(self, device, rows):
        self.service.replicas.sync(device, rows, [dict(id=r['id'], digest=r['digest']) for r in rows],
                                   '同名设备使用者', '电气')

    def sync(self, device, rows):
        # Use the real successful-sync path so newly arrived records are claimed.
        peer = copy.deepcopy(next(p for p in self.service.state['peers'] if p['employee_id'] == device))
        with patch('evaluation.mesh.Transport') as transport:
            transport.return_value.manifest.return_value = dict(employee_id=device, name=peer['name'],
                records=[dict(id=r['id'], digest=r['digest']) for r in rows])
            current = {r['id']: r for r in rows}
            transport.return_value.record.side_effect = lambda identifier, digest: copy.deepcopy(current[identifier])
            self.service._sync_peer(peer)

    def bind(self, device, person, label='开发电脑'):
        return self.client.roster_bind_device(device_id=device, employee_id=person['id'], label=label,
                                               expected_revision=self.revision())

    def account(self, person, user_id=None, workspace='sevenova'):
        user_id = user_id or str(uuid.uuid4())
        previous = {a['id'] for a in self.roster()['accounts']}
        result = self.client.roster_save_account(employee_id=person['id'], workspace=workspace,
            user_id=user_id, display_name='Linear 张工', expected_revision=self.revision())
        return next(a for a in result['accounts'] if a['id'] not in previous)

    def query(self, **filters):
        self.client.ask(question='汇总接口开发的进展', model=False, **filters)
        self.service.query_worker.join(3)
        self.assertFalse(self.service.query_worker.is_alive())
        return self.client.snapshot()['query']

    def issue(self, issue_id):
        return next(i for i in self.client.snapshot()['progress']['issues'] if i['id'] == issue_id)

    def test_all_roster_mutations_require_enabled_evaluator(self):
        identifier = str(uuid.uuid4())
        commands = [
            lambda: self.client.roster_save_employee(name='员工', department='电气', active=True, expected_revision=0),
            lambda: self.client.roster_bind_device(device_id=identifier, employee_id=identifier, label='电脑', expected_revision=0),
            lambda: self.client.roster_unbind_device(device_id=identifier, expected_revision=0),
            lambda: self.client.roster_save_account(employee_id=identifier, workspace='sevenova', user_id=identifier,
                                                    display_name='用户', expected_revision=0),
            lambda: self.client.roster_remove_account(account_id=identifier, expected_revision=0),
        ]
        for command in commands:
            with self.subTest(command=command), self.assertRaises(ValueError):
                command()
        self.assertEqual(self.roster()['revision'], 0)
        self.assertEqual(self.roster()['employees'], [])

    def test_old_paired_devices_do_not_automatically_become_people_or_issue_owners(self):
        self.enable()
        device = self.pair()
        row = journal(device)
        self.seed(device, [row])
        roster = self.roster()
        for field in ('summaries', 'connections', 'account_candidates', 'unassigned_device_count', 'history'):
            self.assertIn(field, roster)
        self.assertEqual(roster['employees'], [])
        self.assertEqual(roster['devices'], [])
        self.assertEqual(roster['accounts'], [])
        self.assertEqual(roster['unassigned_device_count'], 1)
        issue = self.issue(row['issue_id'])
        self.assertEqual(issue['owner_person_id'], '')
        self.assertEqual(issue['contributor_person_ids'], [])
        self.assertTrue(issue['owner_notice'])
        result = self.query()
        self.assertEqual(result['statistics']['unique_employees'], 1)
        self.assertEqual(result['statistics']['unique_devices'], 1)
        self.assertEqual(result['sources'][0]['person_id'], '')
        self.assertEqual(result['sources'][0]['digest'], row['digest'])

    def test_crud_has_revision_conflicts_paired_device_boundary_and_explicit_account_uniqueness(self):
        self.enable()
        person = self.employee()
        other = self.employee('李工')
        device = self.pair()
        stale = self.revision()
        self.bind(device, person)
        before = self.roster()
        with self.assertRaises(ValueError):
            self.client.roster_save_employee(employee_id=person['id'], name='过时编辑', department='旧部门',
                                             active=False, expected_revision=stale)
        with self.assertRaises(ValueError):
            self.client.roster_bind_device(device_id=str(uuid.uuid4()), employee_id=person['id'],
                                            label='未配对设备', expected_revision=self.revision())
        self.assertEqual(self.roster(), before)
        account = self.account(person)
        with self.assertRaises(ValueError):
            self.client.roster_save_account(employee_id=other['id'], workspace=account['workspace'],
                user_id=account['user_id'], display_name='不允许隐式转移', expected_revision=self.revision())
        self.assertEqual(self.roster()['accounts'], [account])
        self.client.roster_save_account(account_id=account['id'], employee_id=person['id'],
            workspace=account['workspace'], user_id=account['user_id'], display_name='明确改名',
            expected_revision=self.revision())
        self.assertEqual(self.roster()['accounts'][0]['display_name'], '明确改名')
        self.client.roster_remove_account(account_id=account['id'], expected_revision=self.revision())
        self.client.roster_unbind_device(device_id=device, expected_revision=self.revision())
        self.client.roster_save_employee(employee_id=person['id'], name=person['name'], department='机械',
                                         active=False, expected_revision=self.revision())
        roster = self.roster()
        self.assertEqual(roster['devices'], [])
        self.assertEqual(roster['accounts'], [])
        self.assertFalse(next(p for p in roster['employees'] if p['id'] == person['id'])['active'])
        self.assertGreater(len(roster['history']), 5)

    def test_two_devices_one_person_count_once_and_explicit_linear_owner_can_be_someone_else(self):
        self.enable()
        author, owner = self.employee('研发甲'), self.employee('负责人乙')
        account = self.account(owner)
        first, second = self.pair(), self.pair()
        row1 = journal(first, assignee=account['user_id'])
        row2 = journal(second, issue_id=row1['issue_id'], assignee=account['user_id'])
        self.seed(first, [row1])
        self.seed(second, [row2])
        originals = copy.deepcopy(self.service.replicas.records())
        self.bind(first, author, 'Linux 工作站')
        self.bind(second, author, 'Windows 笔记本')
        result = self.query(person_id=author['id'])
        self.assertEqual(result['statistics']['matching_records'], 2)
        self.assertEqual(result['statistics']['unique_employees'], 1)
        self.assertEqual(result['statistics']['unique_devices'], 2)
        self.assertEqual({s['person_id'] for s in result['sources']}, {author['id']})
        self.assertEqual({s['person_name'] for s in result['sources']}, {'研发甲'})
        self.assertEqual({s['source_device'] for s in result['sources']}, {first, second})
        self.assertEqual({s['digest'] for s in result['sources']}, {row1['digest'], row2['digest']})
        issue = self.issue(row1['issue_id'])
        self.assertEqual(issue['contributor_person_ids'], [author['id']])
        self.assertEqual(issue['owner_person_id'], owner['id'])
        legacy_filter = self.query(employee_id=first)
        self.assertEqual(legacy_filter['statistics']['matching_records'], 1)
        self.assertEqual(legacy_filter['sources'][0]['source_device'], first)
        self.assertEqual(self.query(person_id=owner['id'])['sources'], [])
        self.assertEqual(self.service.replicas.records(), originals)

    def test_owner_requires_consistent_latest_explicit_assignee_and_workspace_account(self):
        self.enable()
        person = self.employee()
        account = self.account(person)
        first, second = self.pair(), self.pair()
        row1 = journal(first, assignee=account['user_id'])
        row2 = journal(second, issue_id=row1['issue_id'])
        self.seed(first, [row1])
        self.seed(second, [row2])
        self.bind(first, person)
        self.bind(second, person)
        # One unknown latest snapshot cannot be filled from another author's
        # identity or the only known account in the roster.
        self.assertEqual(self.issue(row1['issue_id'])['owner_person_id'], '')
        row2['linear_assignee_id'] = str(uuid.uuid4())
        row2['digest'] = record_digest(row2)
        self.sync(second, [row2])
        self.assertEqual(self.issue(row1['issue_id'])['owner_person_id'], '')
        row2['linear_assignee_id'] = account['user_id']
        row2['issue_url'] = row2['issue_url'].replace('/sevenova/', '/other-workspace/')
        row2['digest'] = record_digest(row2)
        self.sync(second, [row2])
        self.assertEqual(self.issue(row1['issue_id'])['owner_person_id'], '')
        row2['issue_url'] = row1['issue_url']
        row2['digest'] = record_digest(row2)
        self.sync(second, [row2])
        self.assertEqual(self.issue(row1['issue_id'])['owner_person_id'], person['id'])
        self.client.roster_remove_account(account_id=account['id'], expected_revision=self.revision())
        self.assertEqual(self.issue(row1['issue_id'])['owner_person_id'], '')

    def test_latest_period_controls_owner_and_unknown_latest_snapshot_is_not_backfilled(self):
        self.enable()
        person = self.employee()
        account = self.account(person)
        device = self.pair()
        old = journal(device, assignee=account['user_id'])
        latest = journal(device, source='nightly:2026-11-12', issue_id=old['issue_id'],
                         period_end='2026-11-12T22:00:00+08:00')
        self.seed(device, [old, latest])
        self.bind(device, person)
        self.assertEqual(self.issue(old['issue_id'])['owner_person_id'], '')
        self.assertEqual(self.issue(old['issue_id'])['contributor_person_ids'], [person['id']])
        latest['linear_assignee_id'] = account['user_id']
        latest['digest'] = record_digest(latest)
        # The older source can disagree; only the newest complete period is the
        # owner basis. Existing ownership claims and authors are unchanged.
        old['linear_assignee_id'] = str(uuid.uuid4())
        old['digest'] = record_digest(old)
        self.sync(device, [old, latest])
        self.assertEqual(self.issue(old['issue_id'])['owner_person_id'], person['id'])

    def test_person_filtered_model_context_does_not_include_another_person_on_same_device(self):
        self.enable()
        old_person, new_person = self.employee('原作者'), self.employee('新作者')
        device = self.pair()
        old = journal(device)
        old['summary'] = 'OLD_PERSON_ONLY_SOURCE'
        old['digest'] = record_digest(old)
        self.seed(device, [old])
        self.bind(device, old_person)
        self.bind(device, new_person)
        new = journal(device, source='nightly:2026-11-12', issue_id=old['issue_id'],
                      period_end='2026-11-12T22:00:00+08:00')
        new['summary'] = 'NEW_PERSON_PRIVATE_SOURCE'
        new['digest'] = record_digest(new)
        self.sync(device, [old, new])
        captured = {}
        class Runner:
            def run(inner, question, result, progress, cancel, notice):
                captured.update(result=copy.deepcopy(result), progress=copy.deepcopy(progress))
                return '所选人员的证据见 [S1]。'
        self.service.runner = Runner()
        self.client.ask(question='仅总结原作者', model=True, person_id=old_person['id'])
        self.service.query_worker.join(3)
        self.assertFalse(self.service.query_worker.is_alive())
        sources = captured['result']['sources']
        self.assertEqual({s['record_id'] for s in sources}, {old['id']})
        self.assertEqual({s['person_id'] for s in sources}, {old_person['id']})
        self.assertEqual(captured['result']['statistics']['matching_records'], 1)
        self.assertEqual(captured['result']['statistics']['unique_employees'], 1)
        self.assertEqual(captured['result']['statistics']['unique_devices'], 1)
        self.assertNotIn('NEW_PERSON_PRIVATE_SOURCE', str(captured))
        self.assertCountEqual(self.service.replicas.records(), [old, new])

    def test_device_transfer_preserves_old_claims_and_only_claims_new_records_for_new_person(self):
        self.enable()
        first_person, next_person = self.employee('原使用者'), self.employee('新使用者')
        device = self.pair()
        old = journal(device)
        self.seed(device, [old])
        self.bind(device, first_person)
        self.client.roster_unbind_device(device_id=device, expected_revision=self.revision())
        unbound = journal(device, source='nightly:2026-11-12', issue_id=old['issue_id'],
                          period_end='2026-11-12T22:00:00+08:00')
        self.sync(device, [old, unbound])
        self.assertEqual({s['record_id'] for s in self.query(person_id=first_person['id'])['sources']}, {old['id']})
        self.bind(device, next_person)
        new = journal(device, source='nightly:2026-11-13', issue_id=old['issue_id'],
                      period_end='2026-11-13T22:00:00+08:00')
        self.sync(device, [old, unbound, new])
        revised_old = dict(old, summary='原有日志修订仍属于原使用者')
        revised_old['digest'] = record_digest(revised_old)
        self.sync(device, [revised_old, unbound, new])
        old_sources = self.query(person_id=first_person['id'])['sources']
        self.assertEqual({s['record_id'] for s in old_sources}, {old['id']})
        self.assertEqual(old_sources[0]['digest'], revised_old['digest'])
        self.assertEqual({s['record_id'] for s in self.query(person_id=next_person['id'])['sources']},
                         {unbound['id'], new['id']})
        self.assertEqual(self.query()['statistics']['unique_employees'], 2)
        self.assertEqual(self.query()['statistics']['unique_devices'], 1)

    def test_roster_claims_accounts_and_history_survive_runtime_restart(self):
        self.enable()
        person = self.employee()
        device = self.pair()
        account = self.account(person)
        row = journal(device, assignee=account['user_id'])
        self.seed(device, [row])
        self.bind(device, person)
        before = self.roster()
        self.runtime.close(apply_update=False)
        self.start_runtime()
        after = self.roster()
        for key in ('revision', 'employees', 'devices', 'accounts', 'history'):
            self.assertEqual(after[key], before[key])
        result = self.query(person_id=person['id'])
        self.assertEqual(result['sources'][0]['person_id'], person['id'])
        self.assertEqual(result['sources'][0]['digest'], row['digest'])
        self.assertEqual(self.issue(row['issue_id'])['owner_person_id'], person['id'])

    def test_source_withdrawal_hides_people_evidence_without_destroying_roster_or_history(self):
        self.enable()
        person = self.employee()
        device = self.pair()
        row = journal(device)
        self.seed(device, [row])
        self.bind(device, person)
        self.assertTrue(self.query(person_id=person['id'])['sources'])
        history = copy.deepcopy(self.roster()['history'])
        self.sync(device, [])
        self.assertEqual(self.client.snapshot()['query']['sources'], [])
        self.assertEqual(self.client.snapshot()['query']['answer'], '')
        result = self.query(person_id=person['id'])
        self.assertEqual(result['sources'], [])
        self.assertEqual(result['statistics']['unique_employees'], 0)
        self.assertEqual(result['statistics']['unique_devices'], 0)
        self.assertEqual(self.client.snapshot()['progress']['issues'], [])
        self.assertEqual(self.roster()['history'], history)
        self.assertEqual(self.roster()['employees'][0]['id'], person['id'])
        self.assertEqual(self.service.replicas.db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0], 1)

    def test_roster_change_cancels_inflight_answer_and_clears_completed_query(self):
        self.enable()
        person = self.employee()
        device = self.pair()
        row = journal(device)
        self.seed(device, [row])
        self.bind(device, person)
        self.assertTrue(self.query()['sources'])
        self.account(person)
        self.assertEqual(self.client.snapshot()['query']['sources'], [])
        self.assertEqual(self.client.snapshot()['query']['answer'], '')

        entered, release = threading.Event(), threading.Event()
        class Runner:
            def run(inner, question, result, progress, cancel, notice):
                entered.set()
                release.wait(3)
                return '不能继续显示旧名册语境的回答 [S1]'
        self.service.runner = Runner()
        self.client.ask(question='旧名册语境问题', model=True, person_id=person['id'])
        try:
            self.assertTrue(entered.wait(1))
            self.client.roster_save_employee(employee_id=person['id'], name='更新后的员工名称', department='机械',
                                             active=True, expected_revision=self.revision())
            self.assertTrue(self.service.query_cancel.is_set())
        finally:
            release.set()
            self.service.query_worker.join(3)
        self.assertFalse(self.service.query_worker.is_alive())
        self.assertEqual(self.client.snapshot()['query']['sources'], [])
        self.assertEqual(self.client.snapshot()['query']['answer'], '')

    def test_date_filtered_people_and_device_counts_use_the_same_inclusive_day_as_retrieval(self):
        self.enable()
        person = self.employee()
        first, second = self.pair(), self.pair()
        row1, row2 = journal(first), journal(second)
        for row in (row1, row2):
            row['period_start'] = '2026-11-11T12:00:00+08:00'
            row['period_end'] = '2026-11-11T16:00:00+08:00'
            row['digest'] = record_digest(row)
        self.seed(first, [row1])
        self.seed(second, [row2])
        self.bind(first, person)
        # The second source remains explicitly unassigned and counts as one
        # additional device identity, rather than being guessed from its label.
        result = self.query(start='2026-11-11', end='2026-11-11')
        self.assertEqual(result['statistics']['matching_records'], 2)
        self.assertEqual(len(result['sources']), 2)
        self.assertEqual(result['statistics']['unique_employees'], 2)
        self.assertEqual(result['statistics']['unique_devices'], 2)

    def test_model_date_scope_excludes_future_same_issue_state_and_metadata(self):
        self.enable()
        person = self.employee()
        device = self.pair()
        current = journal(device)
        current['linear_state_type'] = 'unstarted'
        current['summary'] = 'SELECTED_DAY_ONLY_CONTENT'
        current['digest'] = record_digest(current)
        future = journal(device, source='nightly:2026-11-20', issue_id=current['issue_id'],
                         period_end='2026-11-20T22:00:00+08:00')
        future.update(period_start='2026-11-19T22:00:00+08:00', linear_state_type='completed',
                      summary='FUTURE_DAY_CONTENT_MUST_BE_EXCLUDED')
        future['digest'] = record_digest(future)
        self.seed(device, [current, future])
        self.bind(device, person)
        captured = {}
        class Runner:
            def run(inner, question, result, progress, cancel, notice):
                captured.update(result=copy.deepcopy(result), progress=copy.deepcopy(progress))
                return '所选日期的进展见 [S1]。'
        self.service.runner = Runner()
        self.client.ask(question='只总结11月11日的开发进展', model=True, person_id=person['id'],
                        issue_id=current['issue_id'], start='2026-11-11', end='2026-11-11')
        self.service.query_worker.join(3)
        self.assertFalse(self.service.query_worker.is_alive())
        result, progress = captured['result'], captured['progress']
        self.assertEqual(result['statistics']['matching_records'], 1)
        self.assertEqual(result['statistics']['unique_employees'], 1)
        self.assertEqual(result['statistics']['unique_devices'], 1)
        self.assertEqual({source['record_id'] for source in result['sources']}, {current['id']})
        self.assertEqual({source['person_id'] for source in result['sources']}, {person['id']})
        self.assertEqual(len(progress['issues']), 1)
        issue = progress['issues'][0]
        self.assertEqual(issue['source_ids'], [current['id']])
        self.assertEqual(issue['linear_state_type'], 'unstarted')
        self.assertEqual(issue['last_report'], current['period_end'])
        self.assertEqual(issue['linear_asof'], current['period_end'])
        self.assertEqual(issue['contributor_person_ids'], [person['id']])
        context = str(captured)
        self.assertNotIn('FUTURE_DAY_CONTENT_MUST_BE_EXCLUDED', context)
        self.assertNotIn(future['id'], context)
        self.assertNotIn(future['digest'], context)
        # Synchronizing and asking do not rewrite the original future journal.
        self.assertCountEqual(self.service.replicas.records(), [current, future])

    def test_source_reassigned_to_other_issue_hides_old_note_without_deleting_history(self):
        self.enable()
        person = self.employee()
        device = self.pair()
        original = journal(device)
        self.seed(device, [original])
        self.bind(device, person)
        sources = [{key: original[key] for key in ('id', 'digest', 'employee_id')}]
        saved = self.client.save_issue_update(issue_id=original['issue_id'], summary='旧issue的来源结论',
                                             sources=sources, confirmed=True, expected_revision=0)
        self.assertEqual(self.client.issue_history(original['issue_id']), [saved])
        moved = copy.deepcopy(original)
        moved.update(issue_id=str(uuid.uuid4()), issue_identifier='EE-202',
                     issue_url='https://linear.app/sevenova/issue/EE-202/reassigned',
                     summary='同一稳定记录已明确更正为另一个issue')
        moved['digest'] = record_digest(moved)
        # Another source keeps the old issue visible, so the hide must come from
        # checking the cited record's current issue rather than the issue filter.
        remaining = journal(device, source='nightly:2026-11-12', issue_id=original['issue_id'],
                            period_end='2026-11-12T22:00:00+08:00')
        self.sync(device, [moved, remaining])
        self.assertTrue(any(issue['id'] == original['issue_id']
                            for issue in self.client.snapshot()['progress']['issues']))
        self.assertEqual(self.client.snapshot()['ledger']['updates'], [])
        self.assertEqual(self.client.issue_history(original['issue_id']), [])
        self.assertEqual(self.service.ledger.history(original['issue_id']), [saved])


if __name__ == '__main__':
    unittest.main()
