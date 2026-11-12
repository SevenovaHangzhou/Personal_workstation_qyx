"""Employee-only Yomk bridge to Evaluator. It never evaluates or writes Linear."""
import copy
import json
from pathlib import Path
import threading
import uuid

from evaluation.journals import JournalExporter
from evaluation.protocol import PROTOCOL, valid_uuid
from evaluation.report_archive import ReportArchive, write_json
from observation import entry
from services.base import MomoService, ok, require


def eligible_tasks(tasks):
    return [task for task in tasks if task.get('category') == 'work' and not task.get('is_sample')
            and isinstance(task.get('linear_issue'), dict) and valid_uuid(task['linear_issue'].get('id'))]


class JournalShareService(MomoService):
    def __init__(self, server, folder, tasks, daily):
        super().__init__(server)
        self.set_name('/JournalShareService')
        self.folder = Path(folder)
        # Compatible with the employee identity/grants already used by Evaluator.
        self.path = self.folder / 'state.json'
        self.tasks, self.daily = tasks, daily
        self.state = dict(version=1, revision=0, employee_id=str(uuid.uuid4()), name='', department='',
                          sharing=False, evaluator=False, share_port=0, shared_task_ids=[], reports_root='', peers=[])
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding='utf-8'))
            require(loaded.get('version') == 1 and valid_uuid(loaded.get('employee_id')), '评估共享配置损坏，原文件保留')
            self.state.update(loaded)
        self.archive = ReportArchive(daily)
        self.server = self.discovery = None
        self.endpoint = None
        self.worker = None
        self.stopping = threading.Event()
        self.wake = threading.Event()
        self.notice = '共享默认关闭；保存范围并为评估员创建授权后才能读取。'
        self._write()

    def _write(self):
        self.state['revision'] += 1
        write_json(self.path, self.state)

    def init(self):
        for name, handler, description in [
            ('snapshot', self.snapshot, '员工端共享状态、可选工作 issue 和授权元数据，不返回 token'),
            ('configure', self.configure, '明确选择工作 issue 共享范围，不改变完整报告的单独授权'),
            ('grant', self.grant, '为指定评估员创建独立只读授权，完整日报周报须另外确认'),
            ('revoke', self.revoke, '撤销指定评估员后续读取'),
        ]:
            self.install('/' + name, handler, 'dict', description)
        self.alive = True
        return 0

    def start(self):
        self.worker = threading.Thread(target=self._run, name='orbit-journal-share', daemon=False)
        self.worker.start()

    def snapshot(self, pkg):
        require(pkg == {}, '状态请求不接受其他字段')
        tasks = eligible_tasks(self.tasks.tasks)
        return ok(dict(config={key: self.state[key] for key in ('employee_id', 'revision', 'name', 'department', 'sharing', 'shared_task_ids')},
                       tasks=[dict(id=task['id'], title=task['title'], issue=task['linear_issue'].get('identifier', '')) for task in tasks],
                       endpoint=self.endpoint, grants=self.server.grants() if self.server else [], notice=self.notice,
                       protocol=PROTOCOL))

    def configure(self, pkg):
        require(isinstance(pkg, dict) and set(pkg) == {'name', 'department', 'sharing', 'shared_task_ids', 'confirmed'} and pkg['confirmed'] is True, '请明确确认共享设置')
        require(type(pkg['sharing']) is bool, '共享开关格式无效')
        for key in ('name', 'department'):
            require(isinstance(pkg[key], str) and len(pkg[key]) <= 80 and not any(ord(c) < 32 for c in pkg[key]), '姓名、部门应为 80 字以内的可见字符')
        require(not pkg['sharing'] or bool(pkg['name'].strip()), '请填写姓名或员工标识')
        ids = pkg['shared_task_ids']
        require(isinstance(ids, list) and len(ids) <= 1000 and all(isinstance(item, str) for item in ids) and len(set(ids)) == len(ids), '共享任务范围无效')
        choices = {task['id'] for task in eligible_tasks(self.tasks.tasks)}
        require(set(ids) <= choices, '仅可共享已绑定 Linear issue 的工作任务')
        self.state.update(name=pkg['name'].strip(), department=pkg['department'].strip(), sharing=pkg['sharing'], shared_task_ids=ids)
        self._write()
        self.notice = '范围已保存，后台正在应用连接设置。'
        self.wake.set()
        return ok()

    def grant(self, pkg):
        require(isinstance(pkg, dict) and set(pkg) == {'label', 'full_reports', 'confirmed'} and pkg['confirmed'] is True, '请明确确认授权范围')
        require(type(pkg['full_reports']) is bool, '完整报告授权格式无效')
        require(self.state['sharing'] and self.server and self.endpoint, '请先保存并启用共享，等待连接就绪')
        grant = self.server.grant(pkg['label'], reports=pkg['full_reports'])
        return ok(dict(protocol=PROTOCOL, employee_id=self.state['employee_id'], name=self.state['name'],
            department=self.state['department'], port=self.endpoint['port'], fingerprint=self.endpoint['fingerprint'],
            token=grant['token'], grant_id=grant['id'], capabilities=['journals'] + (['full_reports'] if pkg['full_reports'] else [])))

    def revoke(self, pkg):
        require(isinstance(pkg, dict) and set(pkg) == {'id'}, '撤销参数无效')
        require(self.server is not None, '请先启用共享以管理授权')
        self.server.revoke(pkg['id'])
        return ok()

    @entry('/JournalShareService')
    def export_records(self):
        with self.lock:
            require(self.alive and self.state['sharing'] and not self.stopping.is_set(), '员工共享已停用')
            state = copy.deepcopy(self.state)
        rows = JournalExporter(self.daily, self.tasks, state['employee_id'], state['name'], state['department']).records(state['shared_task_ids'])
        with self.lock:
            require(self.alive and not self.stopping.is_set() and self.state == state, '共享范围已变化，请重新同步')
        return rows

    @entry('/JournalShareService')
    def export_reports(self, operation, payload):
        with self.lock:
            require(self.alive and self.state['sharing'] and not self.stopping.is_set(), '员工共享已停用')
            revision = self.state['revision']
        value = self.archive.query(operation, payload)
        with self.lock:
            require(self.alive and self.state['sharing'] and revision == self.state['revision'] and not self.stopping.is_set(), '共享设置已变化，请重新读取')
        return value

    def _stop_network(self):
        with self.lock:
            discovery, server = self.discovery, self.server
            self.discovery = self.server = None
            self.endpoint = None
        if discovery:
            discovery.stop()
        if server:
            server.stop()

    def _run(self):
        active = None
        try:
            while not self.stopping.is_set():
                try:
                    with self.lock:
                        state = copy.deepcopy(self.state)
                    key = (state['sharing'], state['name'])
                    if key != active:
                        self._stop_network()
                        if state['sharing']:
                            from evaluation.mesh import ShareServer, Discovery
                            server = ShareServer(self.folder / 'share', state['employee_id'], state['name'],
                                                 self.export_records, reports_callback=self.export_reports)
                            with self.lock:
                                self.server = server  # Also owned if start() fails partway.
                            endpoint = server.start(port=state['share_port'])
                            discovery = Discovery(state['employee_id'], state['name'], endpoint['port'], endpoint['fingerprint'])
                            with self.lock:
                                self.discovery = discovery
                            discovery.start()
                            with self.lock:
                                self.endpoint = endpoint
                                if self.state['share_port'] != endpoint['port']:
                                    self.state['share_port'] = endpoint['port']
                                    self._write()
                        active = key
                        with self.lock:
                            self.notice = '评估员连接已就绪；创建授权后交给管理员导入。' if state['sharing'] else '评估员共享已关闭。'
                except Exception as exc:
                    self._stop_network()
                    active = None
                    with self.lock:
                        self.notice = '评估员连接暂不可用：' + str(exc)[:220]
                self.wake.wait(1)
                self.wake.clear()
        finally:
            self._stop_network()

    def stop(self):
        with self.lock:
            self.alive = False
            self.stopping.set()
            self.wake.set()
        if self.worker:
            self.worker.join(10)
            if self.worker.is_alive():
                raise RuntimeError('评估员共享尚未停止，请稍后重试退出')
