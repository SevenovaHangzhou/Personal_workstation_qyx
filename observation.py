"""Optional ORBIT metadata observation. Importing this module starts nothing."""
from contextlib import contextmanager, nullcontext
from functools import wraps
import threading
import time

_active = None

GROUPS = {
    '任务与专注': ['/TaskService', '/FocusService'],
    '外部集成': ['/BindingService', '/GitService', '/LinearService', '/CodexService'],
    '日报与学习': ['/DailyService', '/LearningService'],
    '应用维护': ['/UpdateService'],
}


def caller(name):
    graph = _active
    return graph.observer.caller(name) if graph else nullcontext()


@contextmanager
def client_origin():
    graph = _active
    if graph and graph.observer._caller.get() == '@caller':
        source = '@QtUI' if threading.get_ident() == graph.owner else '@UIWorker'
        with caller(source):
            yield
    else:
        yield


def entry(source):
    """Attribute a known callback/worker without implying it was a Yomk request."""
    def decorate(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            with caller(source):
                return function(*args, **kwargs)
        return invoke
    return decorate


def span(source, target, endpoint, kind='direct-call'):
    graph = _active
    return graph.collector.span(source, target, endpoint, kind=kind) if graph else nullcontext()


def job(source, target, endpoint):
    """Time the actual worker/job, separately from the queue submission request."""
    def decorate(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            with span(source, target, endpoint, kind='job'), caller(target):
                return function(*args, **kwargs)
        return invoke
    return decorate


class LinearWriterView:
    """Explicit, bounded method names only; never retain arguments in telemetry."""
    def __init__(self, writer):
        self.writer = writer

    def __getattr__(self, name):
        if name not in {'choices', 'issue', 'ensure_issue', 'ensure_parent', 'ensure_comment'}:
            raise AttributeError(name)
        function = getattr(self.writer, name)
        @wraps(function)
        def invoke(*args, **kwargs):
            with span('/DailyService', '/LinearService', '/writer/' + name), caller('/LinearService'):
                return function(*args, **kwargs)
        return invoke


def daily_writer(factory):
    writer = factory()
    return LinearWriterView(writer) if _active else writer


class Observation:
    """Attach before Boot; serve after registration; detach after all producers drain."""
    def __init__(self, api, port=18767):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError('YomkGraph 端口必须在 0–65535 之间')
        self.api = api
        self.port = port
        self.owner = threading.get_ident()
        self.observer = None
        self.collector = None
        self.viewer = None
        self.url = None
        self._closed = False

    def attach(self):
        global _active
        self._control_thread()
        if _active is not None:
            raise RuntimeError('星序观测器已经挂接')
        try:
            from yomk_graph import GraphCollector, PythonObserver
        except ImportError as exc:
            raise RuntimeError('YomkGraph 组件缺失；请使用包含 yomk_graph 的完整源码或重新构建发布包') from exc
        collector = GraphCollector('星序 / ORBIT', max_edges=1000)
        for module, services in GROUPS.items():
            collector.module(module, services)
        collector.module('应用入口与回调', ['@QtUI', '@UIWorker', '@Boot', '@Control', '@FocusClock',
                       '@FocusEventLoop', '@DailyWorker', '@CourseWorker', '@TutorWorker', '@UpdateWorker'])
        collector.module('声明的外部依赖', ['@LinearAPI', '@CodexCLI', '@SQLite', '@LocalFiles',
                         '@GitCLI', '@GitHub', '@EmbeddedStation'])
        # These are architecture declarations, never claimed as measured I/O.
        for source, target, endpoint in [
            ('/TaskService', '@LocalFiles', '/tasks-json'),
            ('/LinearService', '@LinearAPI', '/graphql'),
            ('/CodexService', '@CodexCLI', '/app-server'),
            ('/DailyService', '@SQLite', '/daily-store'),
            ('/DailyService', '@LocalFiles', '/history-and-reports'),
            ('/DailyService', '@GitCLI', '/daily-snapshot'),
            ('/LearningService', '@LocalFiles', '/courses-and-answers'),
            ('/LearningService', '@CodexCLI', '/exec'),
            ('/UpdateService', '@GitHub', '/release-metadata-and-assets'),
            ('@EmbeddedStation', '@SQLite', '/station-store'),
            ('@EmbeddedStation', '@CodexCLI', '/execution'),
        ]:
            collector.declare(source, target, endpoint, kind='dependency',
                              description='源码显式声明；不证明 I/O 已发生，不统计耗时')
        collector.declare('/DailyService', '/LinearService', '/writer', kind='direct-call',
                          description='直接 Python 调用；/writer/<method> 的实线另作显式观测')
        self.collector = collector
        self.observer = PythonObserver(self.api, collector=collector)
        _active = self

    def start_http(self):
        self._control_thread()
        from yomk_graph import GraphServer
        try:
            viewer = GraphServer(self.snapshot, host='127.0.0.1', port=self.port)
        except OSError as exc:
            raise RuntimeError('YomkGraph 无法监听本机端口 '+str(self.port)+'；请更换 --graph-port') from exc
        self.viewer = viewer  # Own the socket even if thread creation fails.
        # Drain request threads before removing the observer; also bound idle clients.
        viewer.httpd.daemon_threads = False
        viewer.httpd.block_on_close = True
        get_request = viewer.httpd.get_request
        def accept():
            connection, address = get_request()
            connection.settimeout(2)
            return connection, address
        viewer.httpd.get_request = accept
        viewer.start()
        self.url = viewer.url

    def snapshot(self):
        result = self.observer.snapshot()
        result['warnings'].extend([
            '星序仅观测 framework.api 的主实例：9 个业务服务与 5 个基础服务；工作站独立实例只展示边界，不合并服务表。',
            '@QtUI 为控制线程上的客户端请求；@UIWorker 为调用客户端的后台线程；未标注的入口仍为 @caller。',
            '@FocusEventLoop 是专注回调，@FocusClock 是投递时钟；日报/课程/答疑/更新 worker 已显式标注。',
            'request 耗时只计同步处理；kind=job 另计实际异步作业。direct-call 是显式 Python 调用，不是已注册的 request 功能。',
            '虚线外部依赖没有运行证据；不读取任务正文、聊天、凭据、Context 值、请求或响应载荷。',
            'eOk 中的业务拒绝不计框架错误；job/direct-call 仅在 Python 异常离开对应作用域时计错。',
        ])
        return result

    def _control_thread(self):
        if threading.get_ident() != self.owner:
            raise RuntimeError('请在观测器所属控制线程执行生命周期操作')

    def stop_http(self):
        self._control_thread()
        if self.viewer:
            # GraphServer.start may have assigned an unstarted thread on failure.
            thread = self.viewer._thread
            if thread is not None and thread.ident is None:
                self.viewer._thread = None
            self.viewer.close()
            self.viewer = None

    def detach(self):
        global _active
        self._control_thread()
        if self._closed:
            return
        if self.viewer:
            raise RuntimeError('必须先停止观测 HTTP，再排空业务并卸载观测器')
        if self.collector:
            deadline = time.monotonic() + 10
            while any(edge.get('in_flight', 0) for edge in self.collector.snapshot([])['edges']):
                if time.monotonic() >= deadline:
                    raise RuntimeError('仍有在途调用，保留观测器；排空入口后重试退出')
                time.sleep(.01)
        if self.observer:
            self.observer.close()
        if _active is self:
            _active = None
        self._closed = True
