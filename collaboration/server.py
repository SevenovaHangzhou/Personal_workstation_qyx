"""Run the opt-in knowledge authority: python -m collaboration.server --help."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import signal
import ssl
import threading
import time
from framework import api
from services.base import MomoService,ok
from collaboration.protocol import PROTOCOL
from collaboration.store import KnowledgeStore

OPERATIONS={'register','heartbeat','state','invite','accept','reject','withdraw','entries','publish','leave','project','lease_acquire','lease_renew','lease_release','files_manifest','files_read','files_begin','files_chunk','files_commit','files_status'}


class KnowledgeService(MomoService):
    def __init__(self,server,store):
        super().__init__(server);self.store=store;self.set_name('/KnowledgeService')
    def init(self):
        self.install('/dispatch',self.dispatch,'dict','鉴权后的协作事务；不会执行任何聊天内容')
        self.alive=True;return 0
    def dispatch(self,pkg):
        try:return ok(self.store.dispatch(pkg['operation'],pkg['device'],pkg['payload']))
        except PermissionError as exc:return api.YomkResponse(api.ResStatus.eInvalid,str(exc))


class KnowledgeHTTP(ThreadingHTTPServer):
    daemon_threads=False;block_on_close=True;allow_reuse_address=True
    def __init__(self,address,store,dispatch):
        self.store=store;self.dispatch=dispatch;self.slots=threading.BoundedSemaphore(16)
        self.tls_context=None
        self.attempt_lock=threading.Lock();self.register_attempts={}
        super().__init__(address,Handler)
    def process_request(self,request,client_address):
        if not self.slots.acquire(False):request.close();return
        try:super().process_request(request,client_address)
        except BaseException:self.slots.release();raise
    def process_request_thread(self,request,client_address):
        try:
            request.settimeout(8)
            if self.tls_context:
                request=self.tls_context.wrap_socket(request,server_side=True,do_handshake_on_connect=False)
                request.do_handshake()
            super().process_request_thread(request,client_address)
        except (OSError,ssl.SSLError):request.close()
        finally:self.slots.release()
    def handle_error(self,request,client_address):
        # No request/body/token is included in diagnostics.
        pass


class Handler(BaseHTTPRequestHandler):
    server_version='OrbitKnowledge/1';sys_version=''
    def setup(self):
        self.request.settimeout(8);super().setup()
    def log_message(self,*args):pass  # Never log tokens, join codes or private summaries.
    def reply(self,status,value):
        raw=json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('Connection','close');self.end_headers()
        try:self.wfile.write(raw)
        except OSError:pass
        self.close_connection=True
    def do_GET(self):
        if self.path=='/health':self.reply(200,{'protocol':PROTOCOL,'status':'ok'})
        else:self.reply(404,{'error':'未知路径'})
    def do_POST(self):
        try:
            operation=self.path.removeprefix('/v1/')
            if self.path!='/v1/'+operation or operation not in OPERATIONS:raise ValueError('未知操作')
            if self.headers.get('X-Orbit-Protocol')!=PROTOCOL:raise ValueError('协作协议版本不符')
            if self.headers.get('Transfer-Encoding'):raise ValueError('不接受分块请求')
            count=int(self.headers.get('Content-Length','0'))
            if not 0<count<=600000:raise ValueError('请求长度无效')
            if self.headers.get('Content-Type')!='application/json':raise ValueError('请求应为 JSON')
            if operation=='register':
                with self.server.attempt_lock:
                    now=time.monotonic();key=self.client_address[0]
                    attempts=[t for t in self.server.register_attempts.get(key,[]) if now-t<60]
                    if len(attempts)>=10:raise PermissionError('加入尝试过多，请一分钟后重试')
                    self.server.register_attempts={k:v for k,v in self.server.register_attempts.items() if v and now-v[-1]<60}
                    self.server.register_attempts[key]=attempts+[now]
                device=None
            else:
                token=self.headers.get('Authorization','')
                if not token.startswith('Bearer '):raise PermissionError('缺少设备认证')
                device=self.server.store.authenticate(self.headers.get('X-Orbit-Device'),token[7:])
            raw=self.rfile.read(count)
            if len(raw)!=count:raise ValueError('请求未完整接收')
            payload=json.loads(raw)
            result=self.server.dispatch(dict(operation=operation,device=device,payload=payload))
            self.reply(200,{'data':result})
        except PermissionError as exc:self.reply(403,{'error':str(exc)})
        except (ValueError,KeyError,TypeError,UnicodeError) as exc:self.reply(400,{'error':str(exc)[:300]})
        except OSError:self.reply(503,{'error':'知识库暂不可用，请稍后重试'})
        except Exception:self.reply(500,{'error':'服务内部错误，未返回私有诊断内容'})


def serve(args):
    folder=Path(args.folder);join=(folder/'join-code').read_text(encoding='utf-8').strip()
    store=KnowledgeStore(folder,join);api.init(2)
    service=KnowledgeService(api.server(),store)
    if api.add_service(service,'/KnowledgeService')!=0:raise RuntimeError('知识库 Yomk 服务启动失败')
    def dispatch(pkg):
        result=api.request('/KnowledgeService/dispatch',pkg)
        if result.status!=api.ResStatus.eOk:raise ValueError(result.msg)
        return result.data
    server=KnowledgeHTTP((args.host,args.port),store,dispatch)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert,args.key);server.tls_context=context
    stopped=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stopped.set())
    server.timeout=.5
    try:
        while not stopped.is_set():server.handle_request()
    finally:
        server.server_close();api.del_service('/KnowledgeService');store.close();api.shutdown()


def main():
    parser=argparse.ArgumentParser(description='星序局域网项目知识库，仅处理显式共享摘要')
    parser.add_argument('--folder',required=True);parser.add_argument('--cert',required=True);parser.add_argument('--key',required=True)
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=18765)
    serve(parser.parse_args())

if __name__=='__main__':main()
