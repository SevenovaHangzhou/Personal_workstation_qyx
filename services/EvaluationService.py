"""Work-only Yomk owner of opt-in journal sharing, replicas and evidence questions."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
import uuid

from credentials import CredentialStore
from evaluation.journals import JournalExporter, ReplicaStore
from evaluation.insights import ProgressStore, retrieve, render_local_answer
from evaluation.ledger import IssueLedger
from evaluation.roster import RosterStore
from evaluation.people import project_roster, attribute_result, source_key, matching_records
from evaluation.protocol import PROTOCOL
from evaluation.report_archive import ReportArchive, CollectedReports
from services.base import MomoService, ok, require
from observation import entry


def now():
    return datetime.now(timezone.utc).isoformat()


def write_state(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.chmod(0o600)
    temporary.replace(path)


class EvaluationService(MomoService):
    def __init__(self, server, folder, tasks, daily, runner=None):
        super().__init__(server)
        self.set_name('/EvaluationService')
        self.folder = Path(folder)
        self.path = self.folder / 'state.json'
        self.tasks, self.daily = tasks, daily
        self.report_archive = ReportArchive(daily)
        self.complete_reports = CollectedReports(self.folder / 'complete-reports')
        self.state = {'version': 1, 'revision': 0, 'employee_id': str(uuid.uuid4()),
                      'name': '', 'department': '', 'sharing': False, 'evaluator': False,
                      'share_port': 0,
                      'shared_task_ids': [], 'reports_root': '', 'peers': []}
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding='utf-8'))
            require(loaded.get('version') == 1 and isinstance(loaded.get('peers'), list), '评估配置损坏，原文件保留')
            self.state.update(loaded)
        self.replicas = ReplicaStore(self.folder / 'replicas')
        self.progress_store = ProgressStore(self.folder / 'progress')
        self.ledger = IssueLedger(self.folder / 'ledger')
        self.roster_store = RosterStore(self.folder / 'roster')
        self.runner = runner
        self.server = self.discovery = None
        self.endpoint = None
        self.worker = self.query_worker = self.report_worker = None
        self.closed = False
        self.stopping = threading.Event()
        self.wake = threading.Event()
        self.query_cancel = threading.Event()
        self.query = {'busy': False, 'question': '', 'answer': '', 'sources': [], 'notice': ''}
        self.report_result = {'busy': False, 'files': [], 'text': '', 'notice': ''}
        self.notice = '默认关闭共享；只在明确启用后联网。'
        self.syncing = False
        self.force_sync = False
        self._write()

    def _write(self):
        self.state['revision'] += 1
        write_state(self.path, self.state)

    def init(self):
        for name, handler, description in [
            ('snapshot', self.snapshot, '本机评估状态与已授权副本，不返回凭据'),
            ('configure', self.configure, '明确启用日报共享或评估界面'),
            ('grant', self.grant, '员工创建独立的只读评估端授权'),
            ('revoke', self.revoke, '撤销指定评估端的后续读取'),
            ('add_peer', self.add_peer, '评估人员导入已明确授权的员工连接'),
            ('remove_peer', self.remove_peer, '停止同步并隐藏该员工资料'),
            ('sync', self.sync, '后台增量获取授权日志副本'),
            ('ask', self.ask, '本地检索或明确调用系统 Codex 问答'),
            ('cancel', self.cancel_query, '停止当前问答'),
            ('configure_progress', self.configure_progress, '明确保存完整子 issue 清单与相对权重'),
            ('report_folder', self.report_folder, '创建本机指定任务的汇报资料目录'),
            ('reports', self.reports, '在线只读预览员工明确共享的汇报资料，不落盘'),
            ('clear_reports', self.clear_reports, '清空在线附件内存预览'),
            ('complete_report_text', self.complete_report_text, '读取另行授权收集的完整日报周报，不用于 issue 统计或模型上下文'),
            ('save_issue_update', self.save_issue_update, '据当前日志证据保存本机 issue 进展，不写 Linear'),
            ('issue_history', self.issue_history, '读取当前授权 issue 的进展修改历史'),
            ('roster_save_employee', self.roster_save_employee, '明确建立或编辑本机员工名册'),
            ('roster_bind_device', self.roster_bind_device, '将已配对设备明确归档到员工，保留历史日志归属'),
            ('roster_unbind_device', self.roster_unbind_device, '解除今后设备归属，不改授权和历史日志'),
            ('roster_save_account', self.roster_save_account, '明确绑定员工与 Linear 工作空间用户 ID，不存密钥'),
            ('roster_remove_account', self.roster_remove_account, '解除本机 Linear 账号对应，不改远端'),
        ]:
            self.install('/' + name, handler, 'dict', description)
        self.alive = True
        return 0

    def start(self):
        self.worker = threading.Thread(target=self._run, name='orbit-evaluation', daemon=False)
        self.worker.start()

    @entry('/EvaluationService')
    def export_records(self):
        with self.lock:
            state = copy.deepcopy(self.state)
            require(self.alive and not self.stopping.is_set(), '共享服务正在退出，请稍后重连')
            if not state['sharing']:
                return []
        return JournalExporter(self.daily, self.tasks, state['employee_id'], state['name'], state['department']).records(state['shared_task_ids'])

    def visible_records(self):
        allowed = {peer['employee_id'] for peer in self.state['peers']}
        return [row for row in self.replicas.records() if row['employee_id'] in allowed]

    def snapshot(self, pkg):
        records = self.visible_records() if self.state['evaluator'] else []
        employees = [row for row in self.replicas.employees()
                     if row['id'] in {p['employee_id'] for p in self.state['peers']}]
        state = copy.deepcopy(self.state)
        report_tasks = {(r['employee_id'], r['task_id']): {'employee_id': r['employee_id'], 'task_id': r['task_id'],
                        'title': r['task_title'], 'issue': r['issue_identifier']} for r in records}
        issue_sources = {}
        for record in sorted(records, key=lambda r:r['period_end'], reverse=True):
            sources = issue_sources.setdefault(record['issue_id'], [])
            if len(sources) < 100:
                sources.append({'id':record['id'], 'digest':record['digest'], 'employee_id':record['employee_id'],
                                'name':record['name'], 'date':record['period_end'], 'kind':record['kind']})
        ledger = self.ledger.snapshot(set(issue_sources))
        ledger['updates'] = self._allowed_updates(ledger['updates'], records)
        discovered = self.discovery.snapshot() if self.discovery else []
        progress = self.progress_store.snapshot(records)
        roster = project_roster(self.roster_store.snapshot(), records,
            self.roster_store.record_owners(records), self.state['peers'], employees,
            discovered, progress, ledger['updates'])
        grants = self.server.grants() if self.server else []
        return ok({'config': state, 'notice': self.notice, 'endpoint': self.endpoint,
                   'grants': grants, 'discovered': discovered,
                   'employees': employees, 'progress': progress, 'roster': roster,
                   'record_count': len(records), 'syncing': self.syncing, 'query': self.query, 'reports': self.report_result,
                   'report_tasks': list(report_tasks.values()), 'issue_sources': issue_sources,
                   'ledger': ledger, 'complete_reports': self.visible_complete_reports(),
                   'folder': str(self.folder), 'linear_write_enabled': False})

    def configure(self, pkg):
        require(isinstance(pkg, dict), '配置格式无效')
        candidate = copy.deepcopy(self.state)
        for key in ('sharing', 'evaluator'):
            if key in pkg:
                require(type(pkg[key]) is bool, '启用选项格式无效')
                candidate[key] = pkg[key]
        for key, maximum in (('name', 80), ('department', 80), ('reports_root', 1000)):
            if key in pkg:
                require(isinstance(pkg[key], str) and len(pkg[key]) <= maximum and '\x00' not in pkg[key], '名称或目录格式无效')
                candidate[key] = pkg[key].strip()
        if 'shared_task_ids' in pkg:
            ids = pkg['shared_task_ids']
            require(isinstance(ids, list) and len(ids) <= 1000 and all(isinstance(v, str) for v in ids), '请选择共享的工作 issue')
            require(len(ids) == len(set(ids)), '共享任务重复')
            current = {t['id']: t for t in self.tasks.tasks}
            require(all(i in current and current[i]['category'] == 'work' for i in ids), '仅工作任务可纳入组织共享')
            candidate['shared_task_ids'] = ids
        require(not (candidate['sharing'] or candidate['evaluator']) or bool(candidate['name']), '启用前请填写姓名或员工标识')
        if not candidate['evaluator']:
            self.query_cancel.set()
            self.query.update(answer='', sources=[], notice='评估已停用')
            self._invalidate_reports('评估已停用')
        self.state = candidate
        self._write()
        self.notice = '配置已保存，后台应用连接设置。'
        self.wake.set()
        return ok()

    def grant(self, pkg):
        require(self.state['sharing'] and self.server and self.endpoint, '请先启用共享，等待连接就绪')
        label = pkg.get('label', '').strip()
        require(1 <= len(label) <= 80, '请填写接收此授权的评估人员名称')
        reports = pkg.get('full_reports', False)
        require(type(reports) is bool, '完整报告授权格式无效')
        grant = self.server.grant(label, reports=reports)
        return ok({'protocol': PROTOCOL, 'employee_id': self.state['employee_id'], 'name': self.state['name'],
                   'department': self.state['department'], 'port': self.endpoint['port'],
                   'fingerprint': self.endpoint['fingerprint'], 'token': grant['token'], 'grant_id': grant['id'],
                   'capabilities': ['journals'] + (['full_reports'] if reports else [])})

    def revoke(self, pkg):
        require(self.server is not None, '请先启用共享以管理授权')
        self.server.revoke(pkg.get('id'))
        return ok()

    def _vault(self, employee_id):
        return CredentialStore(self.folder / 'peer-credentials' / employee_id)

    def add_peer(self, pkg):
        require(self.state['evaluator'], '请先启用评估界面')
        from evaluation.mesh import Transport
        require(pkg.get('protocol') == PROTOCOL, '授权连接协议不符')
        employee = str(uuid.UUID(pkg['employee_id']))
        require(employee == pkg['employee_id'] and employee != self.state['employee_id'], '请选择其他员工的授权连接')
        name = pkg.get('name', '').strip()
        department = pkg.get('department', '').strip()
        require(0 < len(name) <= 80 and len(department) <= 80, '员工信息无效')
        url, pin, token = pkg.get('url', ''), pkg.get('fingerprint', ''), pkg.get('token', '')
        require(isinstance(token, str) and 40 <= len(token) <= 256 and not any(c.isspace() for c in token), '授权凭据无效')
        Transport(url, pin, token, employee)  # Validate without making a network request.
        existing = next((p for p in self.state['peers'] if p['employee_id'] == employee), None)
        require(existing is not None or len(self.state['peers']) < 100, '首版最多配置100个员工连接')
        capabilities = pkg.get('capabilities', [])
        require(isinstance(capabilities, list) and all(isinstance(v, str) and v in ('journals', 'full_reports') for v in capabilities), '授权能力格式无效')
        self._vault(employee).save(token)
        peer = {'employee_id': employee, 'name': name, 'department': department, 'url': url, 'fingerprint': pin,
                'full_reports': 'full_reports' in capabilities}
        self.state['peers'] = [p for p in self.state['peers'] if p['employee_id'] != employee] + [peer]
        self._write()
        self.force_sync = True
        self.wake.set()
        return ok()

    def remove_peer(self, pkg):
        employee = pkg.get('employee_id')
        require(any(p['employee_id'] == employee for p in self.state['peers']), '员工连接不存在')
        self._vault(employee).clear()
        self.state['peers'] = [p for p in self.state['peers'] if p['employee_id'] != employee]
        self._write()
        self.query_cancel.set()
        self.query.update(answer='', sources=[], notice='已移除连接，资料已从当前查询范围隐藏。')
        self._invalidate_reports('已移除连接')
        return ok()

    def sync(self, pkg):
        require(self.state['evaluator'], '请先启用评估界面')
        self.force_sync = True
        self.wake.set()
        return ok()

    def _sync_peer(self, peer):
        from evaluation.mesh import Transport
        token, _ = self._vault(peer['employee_id']).load()
        require(bool(token), '员工授权凭据不存在，请重新导入')
        transport = Transport(peer['url'], peer['fingerprint'], token, peer['employee_id'])
        manifest = transport.manifest()
        entries = manifest['records']
        require(len(entries) <= 10000, '日志清单超过首版限制')
        existing = {r['id']: r['digest'] for r in self.replicas.records(peer['employee_id'])}
        updated = []
        total = 0
        for item in entries:
            if self.stopping.is_set():
                raise InterruptedError('已停止同步')
            if existing.get(item['id']) != item['digest']:
                payload = transport.record(item['id'], item['digest'])
                record = payload['record'] if 'record' in payload else payload
                total += len(json.dumps(record, ensure_ascii=False).encode('utf-8'))
                require(total <= 32*1024*1024, '单次变更超过32MiB，请缩小员工共享范围后重试')
                updated.append(record)
        with self.lock:
            require(not self.stopping.is_set() and self.state['evaluator'] and any(p == peer for p in self.state['peers']), '同步期间连接已被移除、更新或停用')
            self.replicas.sync(peer['employee_id'], updated, entries, peer['name'], peer['department'])
            if existing != {i['id']: i['digest'] for i in entries}:
                self._invalidate_query('日志副本或共享范围已更新，请重新检索。')
                self._invalidate_reports('日志副本或共享范围已更新，请重新读取在线资料。')
            self.roster_store.claim_records(self.visible_records())
        if peer.get('full_reports'):
            self._sync_complete_reports(peer, transport)

    def _sync_complete_reports(self, peer, transport):
        def allowed():
            with self.lock:
                return self.alive and self.state['evaluator'] and any(p == peer for p in self.state['peers'])
        try:
            self.complete_reports.pull(peer['employee_id'], transport, self.stopping, allowed)
        except Exception as exc:
            from evaluation.mesh import PermissionDenied
            if isinstance(exc, PermissionDenied):
                self.complete_reports.suspend(peer['employee_id'], '完整报告未授权或已撤销；工作 issue 日志的授权单独生效。')
            else:
                self.complete_reports.mark_error(peer['employee_id'], '完整报告收集未完成，保留上次成功副本：' + str(exc)[:200])

    def visible_complete_reports(self):
        if not self.state['evaluator']:
            return {}
        peers = {p['employee_id']: p for p in self.state['peers'] if p.get('full_reports')}
        rows = self.complete_reports.snapshot()
        return {employee: dict(rows.get(employee, {}), name=peer['name'], department=peer['department'])
                for employee, peer in peers.items()}

    def complete_report_text(self, pkg):
        require(isinstance(pkg, dict) and set(pkg) == {'employee_id', 'path'}, '完整报告读取参数无效')
        require(self.state['evaluator'] and any(p['employee_id'] == pkg['employee_id'] and p.get('full_reports') for p in self.state['peers']), '未启用评估或未导入完整报告授权')
        return ok(self.complete_reports.text(pkg['employee_id'], pkg['path']))

    @entry('/EvaluationService')
    def export_complete_reports(self, operation, payload):
        with self.lock:
            require(self.alive and self.state['sharing'] and not self.stopping.is_set(), '员工共享已停用')
            revision = self.state['revision']
        result = self.report_archive.query(operation, payload)
        with self.lock:
            require(self.alive and self.state['sharing'] and revision == self.state['revision'] and not self.stopping.is_set(), '共享范围已变化，请重试')
        return result

    @entry('/EvaluationService')
    def export_attachments(self, task_id, relative_path=None):
        from evaluation.reports import ReportFolders
        with self.lock:
            require(self.alive and self.state['sharing'], '员工未启用共享')
            root = self.state['reports_root']
        require(bool(root), '员工尚未选择汇报资料目录')
        def authorized():
            with self.lock:
                if not self.alive or not self.state['sharing'] or self.state['reports_root'] != root:
                    return []
                ids = set(self.state['shared_task_ids'])
            return [t for t in self.tasks.tasks if t['id'] in ids]
        reports = ReportFolders(root, authorized)
        return reports.list(task_id) if relative_path is None else reports.read(task_id, relative_path)

    @entry('/EvaluationService')
    def _run(self):
        network_key = None
        next_sync = 0
        while not self.stopping.is_set():
            try:
                with self.lock:
                    config = copy.deepcopy(self.state)
                    requested = self.force_sync
                    self.force_sync = False
                key = (config['sharing'], config['evaluator'], config['name'])
                if key != network_key:
                    self._stop_network()
                    if config['sharing'] or config['evaluator']:
                        from evaluation.mesh import ShareServer, Discovery
                        if config['sharing']:
                            server = ShareServer(self.folder / 'share', config['employee_id'], config['name'], self.export_records,
                                                 attachments_callback=self.export_attachments, reports_callback=self.export_complete_reports)
                            endpoint = server.start(port=config['share_port'])
                            with self.lock:
                                self.server, self.endpoint = server, endpoint
                                if self.state['share_port'] != endpoint['port']:
                                    self.state['share_port'] = endpoint['port']
                                    self._write()
                        discovery = Discovery(config['employee_id'], config['name'],
                                              self.endpoint['port'] if self.endpoint else 0,
                                              self.endpoint['fingerprint'] if self.endpoint else '')
                        discovery.start()
                        with self.lock:
                            self.discovery = discovery
                    network_key = key
                    with self.lock:
                        self.notice = '共享连接已就绪。' if config['sharing'] else '评估端已就绪。' if config['evaluator'] else '共享和评估均已停用。'
                if config['evaluator'] and (requested or time.monotonic() >= next_sync):
                    with self.lock:
                        self.syncing = True
                    for peer in config['peers']:
                        if self.stopping.is_set():
                            break
                        try:
                            self._sync_peer(peer)
                        except Exception as exc:
                            from evaluation.mesh import PermissionDenied
                            if isinstance(exc, PermissionDenied):
                                with self.lock:
                                    self.replicas.suspend(peer['employee_id'], '员工已拒绝当前授权，请重新配对')
                                    self.complete_reports.suspend(peer['employee_id'], '员工授权已失效，完整报告已隐藏')
                                    self._invalidate_query('员工授权已失效，资料已隐藏')
                                    self._invalidate_reports('员工授权已失效，在线资料已隐藏')
                            self.replicas.mark_sync(peer['employee_id'], peer['name'], peer['department'], error=str(exc)[:300])
                    next_sync = time.monotonic() + 60
                    with self.lock:
                        self.syncing = False
                        self.notice = '同步完成；员工离线时继续查询最后成功副本，请查看各人同步时间。'
            except Exception as exc:
                with self.lock:
                    self.notice = '连接暂不可用：' + str(exc)[:300]
                    self.syncing = False
            self.wake.wait(1)
            self.wake.clear()
        self._stop_network()

    def _stop_network(self):
        with self.lock:
            discovery, server = self.discovery, self.server
            self.discovery = self.server = None
            self.endpoint = None
        if discovery:
            discovery.stop()
        if server:
            server.stop()

    def ask(self, pkg):
        require(self.state['evaluator'], '请先启用评估界面')
        require(not self.query['busy'], '已有问答正在进行，可先停止')
        question = pkg.get('question', '').strip()
        require(0 < len(question) <= 6000, '请输入问题，最多6000字')
        model = pkg.get('model', False)
        require(type(model) is bool, '问答选项无效')
        records = self.visible_records()
        roster = self.roster_store.snapshot()
        owners = self.roster_store.record_owners(records)
        person = pkg.get('person_id', '')
        if person:
            require(any(row['id'] == person for row in roster['employees']), '名册人员不存在，请刷新后重试')
            records = [row for row in records if owners.get(source_key(row)) == person]
        filters = {key: pkg.get(key, '') for key in ('employee_id', 'issue_id', 'start', 'end')}
        selected_issue = filters['issue_id']
        descendants = set()
        if selected_issue:
            descendants.add(selected_issue)
            issues = self.progress_store.snapshot(records)['issues']
            changed = True
            while changed:
                changed = False
                for issue in issues:
                    if issue['parent_id'] in descendants and issue['id'] not in descendants:
                        descendants.add(issue['id'])
                        changed = True
            records = [r for r in records if r['issue_id'] in descendants]
            filters['issue_id'] = ''
        result = retrieve(records, question, **filters)
        result['coverage']['issue_id'] = selected_issue
        result['coverage']['included_issue_ids'] = sorted(descendants)
        result['coverage']['person_id'] = person
        attribute_result(result, records, owners, roster)
        involved = set(result['coverage']['available_employee_ids'])
        result['replica_status'] = [e for e in self.replicas.employees() if e['id'] in involved]
        require(not model or bool(result['sources']), '当前范围没有可供模型回答的共享日志，请先同步或调整筛选条件')
        scoped = matching_records(records, result)
        progress = self.progress_store.snapshot(scoped)
        project_roster(roster, scoped, owners, [], [], [], progress, [])
        progress['scope_notice'] = '仅所选日志范围的相关 issue。Linear 状态来自员工端任务缓存，并非实时查询或历史时点回放；asof 为采用依据的日志周期时间。'
        self.query_cancel = threading.Event()
        self.query = {'busy': True, 'question': question, 'answer': '', 'sources': copy.deepcopy(result['sources']),
                      'notice': '正在检索日志副本', 'started_at': now(), 'mode': 'codex' if model else 'local',
                      'statistics': copy.deepcopy(result.get('statistics', {}))}
        self.query_worker = threading.Thread(target=self._answer,
            args=(question, result, progress, model, self.query_cancel), name='orbit-evidence-query', daemon=False)
        self.query_worker.start()
        return ok()

    @entry('/EvaluationService')
    def _answer(self, question, result, progress, model, cancel):
        try:
            if model:
                from evaluation.qa import EvidenceRunner
                answer = (self.runner or EvidenceRunner()).run(question, result, progress, cancel, self._query_notice)
            else:
                answer = render_local_answer(question, result, progress)
            if cancel.is_set():
                raise InterruptedError('已停止本次问答')
            with self.lock:
                if cancel.is_set() or not self.state['evaluator']:
                    raise InterruptedError('已停止本次问答')
                self.query.update(answer=answer, notice='回答完成；未修改 Linear。', completed_at=now())
        except Exception as exc:
            with self.lock:
                self.query.update(notice=str(exc)[:1000], completed_at=now())
        finally:
            with self.lock:
                self.query['busy'] = False

    def _query_notice(self, text):
        with self.lock:
            self.query['notice'] = text

    def cancel_query(self, pkg):
        self.query_cancel.set()
        return ok()

    def _invalidate_query(self, notice):
        self.query_cancel.set()
        self.query.update(answer='', sources=[], statistics={}, notice=notice)

    def _roster_change(self, action):
        require(self.state['evaluator'], '请先启用评估界面，再维护员工名册')
        before = self.roster_store.snapshot()['revision']
        try:
            action()
        finally:
            if self.roster_store.snapshot()['revision'] != before:
                self._invalidate_query('员工名册或对应关系已更新，请重新检索。')
                self._invalidate_reports('员工名册已更新，请重新读取在线资料。')
        return ok(self.roster_store.snapshot())

    def roster_save_employee(self, pkg):
        return self._roster_change(lambda: self.roster_store.save_employee(**pkg))

    def roster_bind_device(self, pkg):
        require(any(peer['employee_id'] == pkg.get('device_id') for peer in self.state['peers']),
                '请先导入并核对该设备的共享授权，再关联名册人员')
        def bind():
            self.roster_store.bind_device(**pkg)
            self.roster_store.claim_records(self.visible_records())
        return self._roster_change(bind)

    def roster_unbind_device(self, pkg):
        return self._roster_change(lambda: self.roster_store.unbind_device(**pkg))

    def roster_save_account(self, pkg):
        return self._roster_change(lambda: self.roster_store.save_account(**pkg))

    def roster_remove_account(self, pkg):
        return self._roster_change(lambda: self.roster_store.remove_account(**pkg))

    def configure_progress(self, pkg):
        require(self.state['evaluator'] and pkg.get('confirmed') is True, '需要评估人员明确确认范围')
        require(any(r['issue_id'] == pkg.get('issue_id') for r in self.visible_records()), 'issue不在当前授权资料范围')
        return ok(self.progress_store.configure(pkg['issue_id'], pkg['items'], pkg.get('expected_revision')))

    def report_folder(self, pkg):
        task = self.tasks.get(pkg.get('task_id'))
        require(task is not None and task['category'] == 'work', '请选择工作任务')
        require(bool(self.state['reports_root']), '请在设置中选择汇报资料根目录')
        from evaluation.reports import create_report_folder
        folder = create_report_folder(self.state['reports_root'], task, self.state['employee_id'])
        return ok({'path': str(folder)})

    def reports(self, pkg):
        require(self.state['evaluator'] and not (self.report_worker and self.report_worker.is_alive()), '评估未启用或正在读取资料')
        employee, task_id = pkg.get('employee_id'), pkg.get('task_id')
        peer = next((p for p in self.state['peers'] if p['employee_id'] == employee), None)
        require(peer is not None and any(r['employee_id'] == employee and r['task_id'] == task_id for r in self.visible_records()), '任务不在当前授权资料范围')
        path = pkg.get('path')
        require(path is None or isinstance(path, str), '资料路径无效')
        self.report_result = {'busy': True, 'files': [], 'text': '', 'notice': '正在在线读取，资料不会保存到评估端磁盘'}
        self.report_worker = threading.Thread(target=self._read_report, args=(copy.deepcopy(peer), task_id, path, self.report_result), name='orbit-evaluation-report', daemon=False)
        self.report_worker.start()
        return ok()

    @entry('/EvaluationService')
    def _read_report(self, peer, task_id, path, request):
        try:
            from evaluation.mesh import Transport
            token, _ = self._vault(peer['employee_id']).load()
            transport = Transport(peer['url'], peer['fingerprint'], token, peer['employee_id'])
            files = transport.attachments(task_id)
            text = ''
            if path is not None:
                content = transport.attachment(task_id, path)['content']
                text = content.decode('utf-8-sig')
            with self.lock:
                require(not self.stopping.is_set() and self.state['evaluator'] and peer in self.state['peers'], '连接已被移除或停用')
                require(any(r['employee_id'] == peer['employee_id'] and r['task_id'] == task_id
                            for r in self.visible_records()), '任务已不在当前共享范围')
                if self.report_result is request:
                    request.update(files=files, text=text, notice='在线资料，仅在当前窗口内存中展示。')
        except Exception as exc:
            with self.lock:
                from evaluation.mesh import PermissionDenied
                if isinstance(exc, PermissionDenied):
                    self.replicas.suspend(peer['employee_id'], '员工已拒绝当前授权，请重新配对')
                    self._invalidate_query('员工授权已失效，资料已隐藏')
                    self._invalidate_reports('员工授权已失效，在线资料已隐藏')
                if self.report_result is request:
                    request.update(files=[], text='', notice='在线资料不可用：'+str(exc)[:300])
        finally:
            with self.lock:
                if self.report_result is request:
                    request['busy'] = False

    def clear_reports(self, pkg):
        self._invalidate_reports('')
        return ok()

    def _invalidate_reports(self, notice):
        # Replace the request object so an in-flight read cannot republish
        # content after a permission/scope change or a closed viewer.
        self.report_result = {'busy': False, 'files': [], 'text': '', 'notice': notice}

    def save_issue_update(self, pkg):
        require(self.state['evaluator'] and pkg.get('confirmed') is True, '请确认本机进展更新')
        records = {(r['employee_id'], r['id']):r for r in self.visible_records()}
        sources = pkg.get('sources')
        require(isinstance(sources, list) and 1 <= len(sources) <= 100, '请至少引用一份当前共享日志')
        for source in sources:
            require(isinstance(source, dict), '引用格式无效')
            current = records.get((source.get('employee_id'), source.get('id')))
            require(current and current['issue_id'] == pkg.get('issue_id') and current['digest'] == source.get('digest'), '证据不属于此 issue 或已更新，请重新检索')
        return ok(self.ledger.save(pkg['issue_id'], pkg.get('summary', ''), pkg.get('blockers', ''), pkg.get('next_step', ''),
                   self.state['name'], sources, pkg.get('expected_revision')))

    def issue_history(self, pkg):
        records = self.visible_records()
        require(self.state['evaluator'] and any(r['issue_id'] == pkg.get('issue_id') for r in records), 'issue不在当前共享范围')
        return ok(self._allowed_updates(self.ledger.history(pkg['issue_id']), records))

    @staticmethod
    def _allowed_updates(updates, records):
        versions = {(r['employee_id'],r['id']):r for r in records}
        visible = []
        for update in updates:
            if not all((current := versions.get((s['employee_id'],s['id']))) and
                       current['issue_id'] == update['issue_id'] for s in update['sources']):
                continue
            update = copy.deepcopy(update)
            if any(versions[(s['employee_id'],s['id'])]['digest'] != s['digest'] for s in update['sources']):
                update['source_versions_changed'] = True
            visible.append(update)
        return visible

    def stop(self):
        if self.closed:
            return
        with self.lock:
            self.alive = False
            self.stopping.set()
            self.query_cancel.set()
            self.wake.set()
        for thread in (self.query_worker, self.report_worker, self.worker):
            if thread:
                thread.join(10)
                if thread.is_alive():
                    raise RuntimeError('评估后台尚未停止，请稍后重试退出')
        self.replicas.close()
        self.progress_store.close()
        self.ledger.close()
        self.roster_store.close()
        self.closed = True
