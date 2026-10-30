"""Yomk-owned asynchronous OTA preparation and exit-time activation."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from ota_core import GitHubSource, atomic_text, cancelled, stage_release, version_tuple, REPOSITORY
from services.base import MomoService, ok, require
from typedefine.TypeDefine import APP_VERSION


class UpdateService(MomoService):
    def __init__(self, server, folder, auto_tick=False, source=None, current=APP_VERSION, stager=stage_release):
        super().__init__(server); self.set_name('/UpdateService')
        self.folder=Path(folder); self.current=current; self.source=source or GitHubSource(); self.stager=stager
        self.auto_tick=auto_tick; self.worker=None; self.clock=None
        self.automatic=False
        self.cancel=threading.Event(); self.stopping=threading.Event()
        self.path=self.folder/'state.json'
        self.state={'enabled':True,'status':'idle','current':current,'available':None,'ready':None,
                    'last_checked':None,'message':'退出后切换更新，下次启动生效','done':0,'total':0}
        if self.path.exists():
            try:
                saved=json.loads(self.path.read_text())
                if type(saved.get('enabled')) is bool:self.state['enabled']=saved['enabled']
                if isinstance(saved.get('last_checked'),str):self.state['last_checked']=saved['last_checked']
                ready=saved.get('ready')
                if isinstance(ready,dict) and version_tuple(ready.get('version'))>version_tuple(current):
                    stamp=self.folder/'releases'/ready['version']/'ota-install.json'
                    if not stamp.is_symlink() and json.loads(stamp.read_text())==ready:
                        self.state.update(ready=ready,available=ready['version'],status='ready',message='更新已准备，完整退出后切换，下次启动生效')
            except (OSError,ValueError,TypeError,AttributeError):
                self.state['message']='更新缓存暂不可读，可重新检查；原安装不受影响'
        failed=self.folder/'failed-version'
        if failed.is_file():
            try:
                if self.state['ready'] and failed.read_text().strip()==self.state['ready']['version']:
                    self.state.update(ready=None,status='error')
                self.state['message']='上次更新启动失败，已回退原安装；可重新检查更新'
            except OSError:pass

    def init(self):
        for name,handler,description in [('snapshot',self.snapshot,'读取更新状态，不触发网络请求'),
                ('check',self.check,'后台检查正式版本，发现升级则校验并准备'),
                ('configure',self.configure,'设置自动检查与下载更新'),
                ('cancel',self.cancel_update,'停止本次准备，保留当前版本')]:
            self.install('/'+name,handler,'dict',description)
        self.alive=True
        return 0

    def persist(self):
        atomic_text(self.path,json.dumps({k:self.state[k] for k in ('enabled','last_checked','ready')},ensure_ascii=False))

    def snapshot(self,pkg):
        require(pkg=={},'expected empty dict')
        return ok(dict(self.state,busy=bool(self.worker and self.worker.is_alive()),repository=REPOSITORY))

    def configure(self,pkg):
        require(isinstance(pkg,dict) and set(pkg)=={'enabled'} and type(pkg['enabled']) is bool,'更新偏好无效')
        old=self.state['enabled'];self.state['enabled']=pkg['enabled']
        try:self.persist()
        except OSError:self.state['enabled']=old;raise
        if not pkg['enabled']:self.cancel.set()
        elif not old and self.auto_tick:self.begin(True)
        return ok(self.state['enabled'])

    def check(self,pkg):
        require(pkg=={},'expected empty dict')
        return self.begin(False)

    def begin(self,automatic):
        require(not self.stopping.is_set(),'更新服务正在退出')
        if self.worker and self.worker.is_alive():return ok(False)
        self.automatic=automatic
        self.cancel.clear();self.state.update(status='checking',message='正在检查 GitHub 正式版本',done=0,total=0)
        self.worker=threading.Thread(target=self.prepare,name='orbit-ota',daemon=True);self.worker.start()
        return ok(True)

    def cancel_update(self,pkg):
        require(pkg=={},'expected empty dict');self.cancel.set()
        if self.state['ready'] and (self.state['status']=='ready' or not (self.worker and self.worker.is_alive())):
            old=copy.deepcopy(self.state)
            self.state.update(ready=None,status='cancelled',message='已取消待更新版本，退出时保留当前版本')
            try:self.persist()
            except OSError:self.state=old;raise
        return ok()

    def progress(self,status,done,total):
        with self.lock:
            self.state.update(status=status,done=done,total=total,
                message='后台下载更新，不影响当前任务' if status=='downloading' else '正在校验安装包并进行隔离自检')

    def prepare(self):
        try:
            release=self.source.latest();cancelled(self.cancel)
            require(isinstance(release,dict),'GitHub 返回的版本信息无效')
            require(release.get('draft') is False and release.get('prerelease') is False,'忽略草稿与预发布版本')
            latest=version_tuple(release.get('tag_name')); version=release['tag_name'].removeprefix('v')
            failed=self.folder/'failed-version'
            if self.automatic and failed.is_file() and failed.read_text().strip()==version:
                raise ValueError('此版本上次启动失败，已停止自动重试；可手动检查更新')
            with self.lock:
                self.state['last_checked']=datetime.now(timezone.utc).isoformat()
                ready=copy.deepcopy(self.state['ready'])
                if latest<=version_tuple(self.current):
                    if ready:self.state.update(status='ready',message='已准备更新，完整退出后切换')
                    else:self.state.update(status='current',message='当前已是最新正式版本',available=None)
                    self.persist();return
                self.state['available']=version
                if ready and latest<=version_tuple(ready['version']):
                    self.state.update(status='ready',message='已准备更新，完整退出后切换');self.persist();return
            ready=self.stager(self.folder,release,self.cancel,self.progress,self.source)
            cancelled(self.cancel)
            with self.lock:
                cancelled(self.cancel)
                previous=copy.deepcopy(self.state)
                self.state.update(ready=ready,status='ready',message='更新 '+ready['version']+' 已就绪，完整退出后切换，下次启动生效')
                try:self.persist()
                except OSError:self.state=previous;raise
        except Exception as exc:
            with self.lock:
                self.state.update(status='ready' if self.state['ready'] else ('cancelled' if isinstance(exc,InterruptedError) else 'error'),
                                  message='已取消本次更新，已有就绪版本保留' if isinstance(exc,InterruptedError) and self.state['ready'] else
                                          '本次更新已取消，当前版本保持可用' if isinstance(exc,InterruptedError) else
                                          '更新未完成，原安装保持可用：'+(str(exc) if isinstance(exc,ValueError) else type(exc).__name__))

    def start_clock(self):
        if not self.auto_tick:return
        def clock():
            if self.stopping.wait(5):return
            while not self.stopping.is_set():
                with self.lock:
                    if self.state['enabled'] and not self.state['ready'] and not (self.worker and self.worker.is_alive()):self.begin(True)
                if self.stopping.wait(6*60*60):return
        self.clock=threading.Thread(target=clock,name='orbit-ota-clock',daemon=True);self.clock.start()

    def stop(self):
        self.stopping.set();self.cancel.set()
        if self.clock:self.clock.join()
        if self.worker:self.worker.join()

    def activate(self):
        """Called only after domain workers and data locks have fully closed."""
        with self.lock:
            ready=self.state.get('ready')
            if not ready or version_tuple(ready['version'])<=version_tuple(self.current):return
            target=self.folder/'releases'/ready['version']
            require(not target.is_symlink() and json.loads((target/'ota-install.json').read_text())==ready,'更新目录已变化，未切换版本')
            atomic_text(self.folder/'previous-version',self.current+'\n')
            atomic_text(self.folder/'current-version',ready['version']+'\n')

    def mark_started(self):
        failed=self.folder/'failed-version'
        try:
            if failed.is_file() and failed.read_text().strip()==self.current:failed.unlink()
        except OSError:pass
        marker=os.environ.get('ORBIT_OTA_HEALTH_FILE')
        if not marker:return
        marker=Path(marker)
        if marker.parent.resolve()!=self.folder.resolve() or not marker.name.startswith('launch-health.'):
            return
        try:atomic_text(marker,self.current+'\n')
        except OSError:pass
