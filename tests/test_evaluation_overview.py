"""Manager summaries preserve explicit attribution and source uncertainty."""
import copy
from datetime import datetime, timezone
import unittest

from evaluation.overview import build_overview, filter_issues


NOW = datetime(2026, 11, 24, 8, tzinfo=timezone.utc)


def fixture():
    people = [dict(id='alice', name='陈晨', department='软件', active=True),
              dict(id='bob', name='陈晨', department='机械', active=True),
              dict(id='carol', name='李欣', department='测试', active=True)]
    summaries = [dict(person, devices=[], accounts=[], last_report='', last_synced='') for person in people]
    roster = dict(employees=people, summaries=summaries, connections=[], unassigned_device_count=0)
    def issue(identity, title, state='started', owner='alice', **extra):
        return dict(id=identity, identifier='DEV-'+identity, title=title, linear_state_type=state,
                    parent_id='', owner_person_id=owner, owner_name='陈晨' if owner else '',
                    contributor_person_ids=['bob'], contributor_names=['陈晨'], percent=None,
                    last_report='2026-11-24T07:00:00+00:00', **extra)
    progress = dict(issues=[issue('1', '控制接口联调'), issue('2', '设备连接', owner=''),
                           issue('3', '日志导出', state='completed'), issue('4', '旧方案', state='canceled')])
    records = [dict(id='log-'+row['id'], issue_id=row['id'], source_device='laptop', name='陈晨的设备',
                    period_end='2026-11-24T07:00:00+00:00', digest='hash', summary='接口已接通，等待实机联调', evidence=[])
               for row in progress['issues']]
    owners = {('laptop', row['id']): 'bob' for row in records}
    updates = [dict(issue_id='1', summary='接口开发完成，正在联调', blockers='缺少测试夹具',
                    next_step='协调夹具后完成验证', editor='项目经理', updated_at='2026-11-24T08:00:00+00:00')]
    return progress, roster, updates, records, owners


class OverviewTests(unittest.TestCase):
    def build(self, data=None):
        return build_overview(*(data or fixture()), now=NOW)

    def test_same_names_keep_owner_contributor_and_no_data_person_distinct(self):
        overview = self.build()
        people = {row['id']: row for row in overview['people']}
        self.assertEqual(people['alice']['owned_count'], 1)
        self.assertEqual(people['bob']['owned_count'], 0)
        self.assertEqual(people['bob']['contributed_count'], 2)
        self.assertEqual(people['carol']['owned_count'], 0)
        self.assertNotIn('percent', people['carol'])
        self.assertEqual(len(filter_issues(overview, 'alice')), 1)
        self.assertEqual(len(filter_issues(overview, 'bob')), 2)
        self.assertEqual(filter_issues(overview, 'carol'), [])
        self.assertEqual([row['id'] for row in filter_issues(overview, '__unassigned__')], ['2'])

    def test_closed_and_canceled_are_distinct_and_attention_counts_issues_once(self):
        overview = self.build()
        self.assertEqual(overview['counts'], dict(people=3, active=2, attention=2, completed=1))
        self.assertEqual(len(filter_issues(overview, status='completed')), 1)
        self.assertEqual(len(filter_issues(overview, status='all')), 4)
        self.assertEqual(filter_issues(overview, status='attention')[0]['id'], '1')
        self.assertTrue(all(not row['attention'] for row in overview['issues'] if row['closed']))

    def test_latest_tied_periods_include_both_authors_and_ignore_old_reports(self):
        data = fixture()
        old = data[3][0]
        old['period_end'] = '2026-11-23T07:00:00+00:00'
        first = dict(old, id='new-a', period_end='2026-11-24T07:00:00+00:00', summary='软件接口已通过')
        second = dict(old, id='new-b', source_device='desktop', period_end='2026-11-24T15:00:00+08:00', summary='机械结构仍待调整')
        data[3].extend([first, second])
        data[4][('laptop', 'new-a')] = 'alice'
        data[4][('desktop', 'new-b')] = 'bob'
        data[2].clear()
        row = next(row for row in self.build(data)['issues'] if row['id'] == '1')
        self.assertEqual({report['person_id'] for report in row['reports']}, {'alice', 'bob'})
        self.assertIn('软件接口已通过', row['summary'])
        self.assertIn('机械结构仍待调整', row['summary'])
        self.assertNotIn('等待实机联调', row['summary'])

    def test_missing_progress_is_unknown_and_unmapped_device_name_does_not_assign_person(self):
        data = fixture()
        data[2].clear()
        data[3][1]['summary'] = ''
        data[4].clear()
        rows = {row['id']: row for row in self.build(data)['issues']}
        self.assertIsNone(rows['2']['percent'])
        self.assertIn('待补充进展', rows['2']['attention'])
        self.assertEqual(rows['2']['owner_id'], '')
        self.assertEqual(rows['2']['reports'][0]['person_id'], '')
        self.assertTrue(rows['1']['summary'].startswith('未归档来源'))

    def test_log_age_not_sync_or_edit_time_drives_stale_and_revised_notes_are_flagged(self):
        data = fixture()
        data[0]['issues'][0]['last_report'] = '2026-11-17T08:00:00+00:00'
        data[2][0]['source_versions_changed'] = True
        data[1]['connections'] = [dict(connected=True, last_synced=NOW.isoformat(), error='offline')]
        overview = self.build(data)
        row = next(row for row in overview['issues'] if row['id'] == '1')
        self.assertIn('超过 7 天未更新', row['attention'])
        self.assertIn('阻塞待核对', row['attention'])
        self.assertIn('进展待核对', row['attention'])
        self.assertEqual(overview['last_synced'], NOW.isoformat())
        self.assertEqual(overview['sync_errors'], 1)

    def test_only_explicit_current_log_blockers_surface_without_parsing_prose(self):
        data = fixture()
        data[2].clear()
        data[3][0]['summary'] = '可能阻塞，文本不作为自动状态判断'
        data[3][1]['evidence'] = [dict(kind='blocker', text='等待接口权限'),
                                  dict(kind='blocker', text='旧阻塞', retracted_at='2026-11-24')]
        rows = {row['id']: row for row in self.build(data)['issues']}
        self.assertEqual(rows['1']['blockers'], '')
        self.assertIn('等待接口权限', rows['2']['blockers'])
        self.assertNotIn('旧阻塞', rows['2']['blockers'])
        self.assertEqual(rows['2']['blocker_source'], '员工日志')

    def test_search_combines_filters_and_input_is_never_mutated(self):
        data = fixture()
        before = copy.deepcopy(data)
        overview = self.build(data)
        self.assertEqual(data, before)
        self.assertEqual([row['id'] for row in filter_issues(overview, 'alice', 'attention', 'DEV-1 夹具')], ['1'])
        self.assertEqual(filter_issues(overview, 'carol', 'all', '夹具'), [])
        self.assertEqual(filter_issues(overview, status='completed', query='DEV-1'), [])

    def test_new_log_is_not_hidden_by_an_old_manual_summary(self):
        data = fixture()
        data[2][0]['updated_at'] = '2026-11-23T08:00:00+00:00'
        row = next(row for row in self.build(data)['issues'] if row['id'] == '1')
        self.assertIn(data[3][0]['summary'], row['summary'])
        self.assertEqual(row['summary_source'], '员工日志')
        self.assertEqual(row['manual_summary'], data[2][0]['summary'])
        self.assertTrue(row['notes_outdated'])
        self.assertIn('阻塞待核对', row['attention'])

    def test_conflicting_same_period_states_do_not_award_completion(self):
        data = fixture()
        data[3][2]['linear_state_type'] = 'completed'
        data[3].append(dict(data[3][2], id='conflict', linear_state_type='started'))
        overview = self.build(data)
        row = next(row for row in overview['issues'] if row['id'] == '3')
        self.assertEqual(row['state_label'], '状态待确认')
        self.assertFalse(row['closed'])
        self.assertEqual(overview['counts']['completed'], 0)


if __name__ == '__main__':
    unittest.main()
