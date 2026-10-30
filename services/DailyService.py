"""Yomk-owned scheduler. No model/network call holds the service dispatch lock."""
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
import re
from framework import api
from services.base import MomoService, ok, require, request_data
from daily_store import DailyStore
from daily_history import ZONE, latest_due
from daily_engine import DailyEngine


class DailyService(MomoService):
    def __init__(self, server, folder, tasks, linear, station, auto_tick=True):
        super().__init__(server)
        self.set_name('/DailyService')
        self.store = DailyStore(folder)
        from storage_retention import Retention
        self.retention=Retention(self.store.folder.parent)
        self.engine = DailyEngine(self.store, tasks, linear.writer, station)
        self.engine.progress = self.progress
        self.linear = linear
        self.auto_tick = auto_tick
        self.cancel = threading.Event(); self.wake = threading.Event()
        self.worker = None; self.manual = False; self.busy = False; self.notice = '选择默认团队后开始自动运行；可以先补跑已有绑定任务的日报'

    def init(self):
        config = self.store.get('config')
        if config is None:
            config = {'enabled':True, 'activated':False, 'target':{}, 'next_day':datetime.now(ZONE).date().isoformat()}
            self.store.put('config', config)
        self.install('/snapshot', self.snapshot, 'None', '查看自动日报设置、到期日期和同步结果')
        self.install('/report_text',self.report_text,'dict','从保留的日报账本读取正文，兼容已清理的文件投影')
        self.install('/configure', self.configure, 'dict', '设置日报启停与用户已选择的 Linear 归属')
        self.install('/retry', self.retry, 'None', '补跑已到期日报及失败同步，不提前发布当前周期')
        # This handler performs read-only I/O on the caller's worker thread.
        self.install_func('/choices', self.choices, 'dict', '分页读取可选团队和项目；UI 在后台线程调用')
        self.install_func('/retention_preview',self.retention_preview,'dict','预览30天前的已结束原始输出与已完成日报留存')
        self.install_func('/retention_apply',self.retention_apply,'dict','一次性确认清单，重新核对后移入系统回收站')
        self.alive = True
        return 0

    def progress(self, message):
        with self.lock:
            self.notice = message

    def start_clock(self):
        if self.auto_tick and self.worker is None:
            self.worker = threading.Thread(target=self._loop, name='momo-daily', daemon=False)
            self.worker.start()

    def snapshot(self, pkg):
        require(pkg is None, 'expected None')
        from daily_pending import pending_work
        config=self.store.get('config'); revision=self.engine.tasks.revision()['revision']
        key=(revision,config['next_day'],repr(config.get('target')),config['enabled'])
        if getattr(self,'_pending_key',None)!=key:
            self._pending_rows=pending_work(self.engine.tasks.tasks,config); self._pending_key=key
        return ok({'report_revision':self.store.get('report_revision',0), 'config':self.store.get('config'), 'reports':self.store.reports(), 'busy':self.busy,
                   'notice':self.notice, 'folder':str(self.store.folder),'pending_sync':self._pending_rows})

    def configure(self, pkg):
        require(isinstance(pkg, dict) and set(pkg) <= {'enabled','target'}, '无效日报设置')
        config = self.store.get('config')
        if 'enabled' in pkg:
            require(type(pkg['enabled']) is bool, '启用标志必须为布尔值')
            config['enabled'] = pkg['enabled']
        if 'target' in pkg:
            target = pkg['target']
            require(isinstance(target, dict) and set(target) <= {'team_id','project_id'} and
                    all(isinstance(v,str) for v in target.values()), '无效 Linear 归属')
            if target:
                require(target.get('team_id'), '请先选择团队')
            config['target'] = target
            config['activated'] = bool(target.get('team_id'))
        self.store.put('config', config)
        self.wake.set()
        return self.snapshot(None)

    def report_text(self,pkg):
        require(isinstance(pkg,dict) and set(pkg)=={'day'} and isinstance(pkg['day'],str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',pkg['day']),'无效日报日期')
        datetime.fromisoformat(pkg['day'])
        report=self.store.report(pkg['day']);require(report is not None,'日报不存在')
        return ok(report['content']['markdown']+('\n\n待处理：\n'+'\n'.join('- '+error for error in report['errors']) if report['errors'] else ''))

    def retry(self, pkg):
        require(pkg is None, 'expected None')
        self.manual = True; self.wake.set()
        return self.snapshot(None)

    def choices(self, pkg):
        try:
            with self.lock:
                require(self.alive, '日报服务已停止')
            require(isinstance(pkg, dict) and set(pkg) <= {'team_id'}, '无效归属查询')
            require(isinstance(pkg.get('team_id',''),str), '无效团队 ID')
            return ok(self.linear.writer().choices(pkg.get('team_id','')))
        except Exception as exc:
            return api.YomkResponse(api.ResStatus.eNo, str(exc))

    def retention_request(self,pkg,apply=False):
        try:
            require(isinstance(pkg,dict) and set(pkg)==({'id','cancel'} if apply else {'cancel'}),'无效留存请求')
            require(isinstance(pkg['cancel'],threading.Event),'无效取消标志')
            with self.lock:require(self.alive,'日报服务已停止')
            if apply:
                require(isinstance(pkg['id'],str),'无效确认清单')
                result=self.retention.apply(pkg['id'],pkg['cancel'].is_set)
                self.store.put('last_retention_result',result)
                return ok(result)
            candidates=request_data('/LearningService/retention_artifacts')
            done={row['day'] for row in self.store.reports() if row['state']=='done'}
            candidates.extend('daily/'+day+'.md' for day in done)
            git=self.store.folder/'git'
            if git.is_dir() and not git.is_symlink():
                candidates.extend('daily/git/'+path.name for path in git.iterdir()
                                  if re.fullmatch(r'\d{4}-\d{2}-\d{2}-[A-Za-z0-9-]+',path.name) and path.name[:10] in done)
            compression=self.store.folder/'compression'
            if compression.is_dir() and not compression.is_symlink():
                candidates.extend('daily/compression/'+path.name for path in compression.iterdir()
                                  if re.fullmatch('[0-9a-f]{64}',path.name) and self.store.cached_summary(path.name) is not None)
            return ok(self.retention.preview(candidates,pkg['cancel'].is_set))
        except Exception as exc:return api.YomkResponse(api.ResStatus.eNo,str(exc))

    def retention_preview(self,pkg):return self.retention_request(pkg)
    def retention_apply(self,pkg):return self.retention_request(pkg,True)

    def _loop(self):
        retry_at = 0
        while not self.cancel.is_set():
            with self.lock:
                config = self.store.get('config'); manual = self.manual; self.manual = False
            now = datetime.now(ZONE); due = latest_due(now)
            if ((config['enabled'] and config.get('activated',False)) or manual) and (manual or time.monotonic() >= retry_at):
                days = set(self.store.pending_days())
                day = config['next_day']
                # Process in bounded batches; persist each daily checkpoint.
                for _ in range(30):
                    if day > due: break
                    days.add(day)
                    day = (datetime.fromisoformat(day).date()+timedelta(days=1)).isoformat()
                for target_day in sorted(days):
                    if self.cancel.is_set(): break
                    with self.lock:
                        self.busy = True; self.notice = '正在处理 '+target_day+' 日报'
                    try:
                        self.engine.run(target_day, config, self.cancel)
                        with self.lock:
                            current = self.store.get('config')
                            if target_day >= current['next_day']:
                                current['next_day'] = (datetime.fromisoformat(target_day).date()+timedelta(days=1)).isoformat()
                                self.store.put('config', current)
                    except Exception as exc:
                        with self.lock: self.notice = '日报未完成：'+str(exc)
                        break
                    finally:
                        with self.lock: self.busy = False
                else:
                    with self.lock:
                        self.notice = '到期日报已检查；失败项会在 5 分钟后重试' if days else '尚未到下一次 22:00，未提前发布'
                retry_at = time.monotonic()+300
            self.wake.wait(30); self.wake.clear()

    def stop_clock(self):
        self.cancel.set(); self.wake.set()
        if self.worker:
            self.worker.join(); self.worker = None

    def deinit(self):
        self.stop_clock()
        super().deinit()
        self.store.close()
