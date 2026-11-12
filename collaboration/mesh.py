"""LAN Mesh presence and certificate-pinned, signed peer requests. No coordinator."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import threading
import time
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.exceptions import InvalidSignature
from cryptography.x509.oid import NameOID
from collaboration.protocol import identifier, text

PROTOCOL = 'orbit-lan-mesh/v2'
PORT = 45873
MAX_BODY = 650000

# Use the same canonical package name as EmbeddedStation; two names for the
# pinned source confuse frozen importers and duplicate component state.
if not getattr(sys,'frozen',False):
    pinned=str(Path(__file__).resolve().parents[1]/'vendor/work_station')
    if pinned not in sys.path:sys.path.insert(0,pinned)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def fingerprint(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('同事设备指纹无效')
    return value


def lan_address(value):
    address = ipaddress.ip_address(value)
    if address.version != 4 or not address.is_private or address.is_unspecified or address.is_multicast:
        raise ValueError('仅支持局域网 IPv4 地址')
    return str(address)


class Identity:
    def __init__(self, root, device_id):
        root = Path(root); root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.id = identifier(device_id)
        self.key_path = root/'mesh.key'; self.cert_path = root/'mesh.crt'
        if self.key_path.is_symlink() or self.cert_path.is_symlink():
            raise ValueError('设备身份文件不能是符号链接')
        if not self.key_path.exists():
            key = ec.generate_private_key(ec.SECP256R1())
            with self.key_path.open('xb') as stream:
                os.chmod(self.key_path, 0o600)
                stream.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.key = serialization.load_pem_private_key(self.key_path.read_bytes(), None)
        if not isinstance(self.key, ec.EllipticCurvePrivateKey):
            raise ValueError('设备密钥格式无效')
        if not self.cert_path.exists():
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Orbit '+self.id)])
            now = datetime.now(timezone.utc)
            cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(self.key.public_key())
                    .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1))
                    .not_valid_after(now+timedelta(days=3650)).sign(self.key, hashes.SHA256()))
            with self.cert_path.open('xb') as stream:
                os.chmod(self.cert_path, 0o600); stream.write(cert.public_bytes(serialization.Encoding.PEM))
        cert = x509.load_pem_x509_certificate(self.cert_path.read_bytes())
        if cert.public_key().public_numbers() != self.key.public_key().public_numbers():
            raise ValueError('设备证书与密钥不匹配')
        raw = cert.public_bytes(serialization.Encoding.DER)
        self.certificate = base64.b64encode(raw).decode()
        self.pin = hashlib.sha256(raw).hexdigest()

    def envelope(self, target, operation, payload):
        message = dict(protocol=PROTOCOL, sender=self.id, target=identifier(target),
                       timestamp=int(time.time()), nonce=str(uuid.uuid4()), certificate=self.certificate,
                       operation=operation, payload=payload)
        signature = self.key.sign(canonical(message), ec.ECDSA(hashes.SHA256()))
        return dict(message=message, signature=base64.b64encode(signature).decode())


def verify(envelope, target):
    if not isinstance(envelope, dict) or set(envelope) != {'message', 'signature'}:
        raise ValueError('直连请求格式无效')
    message = envelope['message']
    if not isinstance(message, dict) or set(message) != {'protocol','sender','target','timestamp','nonce','certificate','operation','payload'}:
        raise ValueError('直连消息格式无效')
    if message['protocol'] != PROTOCOL or message['target'] != target:
        raise ValueError('消息协议或接收方不匹配')
    identifier(message['sender']); identifier(message['nonce'])
    if type(message['timestamp']) is not int or abs(time.time()-message['timestamp']) > 120:
        raise ValueError('消息已过期，请检查双方系统时间')
    if not isinstance(message['certificate'], str) or len(message['certificate']) > 4096:
        raise ValueError('设备证书无效')
    try:
        raw = base64.b64decode(message['certificate'], validate=True)
        cert = x509.load_der_x509_certificate(raw)
        key = cert.public_key()
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError('不支持的设备密钥')
        key.verify(base64.b64decode(envelope['signature'], validate=True), canonical(message), ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, TypeError, ValueError):
        raise ValueError('设备消息签名无效') from None
    return message, hashlib.sha256(raw).hexdigest()


def pairing_code(pair_id, own_pin, peer_pin):
    value = hashlib.sha256(canonical([pair_id, *sorted([own_pin, peer_pin])])).hexdigest()[:12].upper()
    return ' '.join(value[i:i+4] for i in range(0,12,4))


class PeerTransport:
    def __init__(self, identity, peer):
        self.identity=identity; self.peer=peer
        self.host=lan_address(peer['address']); self.port=peer['port']; fingerprint(peer['fingerprint'])
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('同事直连端口无效')

    def request(self, operation, payload):
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname=False; context.verify_mode=ssl.CERT_NONE
        context.minimum_version=ssl.TLSVersion.TLSv1_2
        connection=http.client.HTTPSConnection(self.host,self.port,timeout=4,context=context)
        try:
            connection.connect()
            actual=hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
            if actual != self.peer['fingerprint']:
                raise ValueError('同事设备指纹已变化，未发送内容；请重新确认设备')
            body=canonical(self.identity.envelope(self.peer['id'],operation,payload))
            if len(body)>MAX_BODY:raise ValueError('直连消息过大')
            connection.request('POST','/mesh',body,{'Content-Type':'application/json'})
            response=connection.getresponse();raw=response.read(MAX_BODY+1)
            if len(raw)>MAX_BODY:raise ValueError('同事响应过大')
            value=json.loads(raw)
            if response.status!=200:raise ValueError(value.get('error','同事未接受请求'))
            return value['data']
        finally:connection.close()


class PeerListener:
    def __init__(self, identity, receive, host='', port=0):
        self.identity=identity; self.receive=receive; self.host=host; self.port=port
        self.server=None; self.thread=None

    def start(self):
        owner=self
        class Server(ThreadingHTTPServer):
            daemon_threads=False; block_on_close=True; allow_reuse_address=True
            slots=threading.BoundedSemaphore(8)
            def handle_error(self,request,address):
                if not isinstance(sys.exception(),OSError):super().handle_error(request,address)
            def get_request(self):
                connection,address=super().get_request();connection.settimeout(4)
                # Handshakes run in bounded handler threads, not the accept loop.
                return connection,address
            def process_request(self, request, address):
                if not self.slots.acquire(False):request.close();return
                try:super().process_request(request,address)
                except BaseException:self.slots.release();raise
            def process_request_thread(self,request,address):
                try:
                    secured=context.wrap_socket(request,server_side=True)
                    super().process_request_thread(secured,address)
                except (OSError,ssl.SSLError):request.close()
                finally:self.slots.release()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                try:
                    if self.path!='/mesh' or self.headers.get('Content-Type')!='application/json':raise ValueError('请求路径或类型无效')
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=MAX_BODY:raise ValueError('请求大小无效')
                    raw=self.rfile.read(size)
                    if len(raw)!=size:raise ValueError('请求不完整')
                    data=owner.receive(dict(envelope=json.loads(raw),address=lan_address(self.client_address[0])))
                    result={'data':data};status=200
                except (ValueError,TypeError,KeyError,OSError) as exc:result={'error':str(exc)[:240]};status=400
                payload=canonical(result)
                self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(self.identity.cert_path),str(self.identity.key_path))
        self.server=Server((self.host,self.port),Handler);self.port=self.server.server_port
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.15},name='orbit-mesh-listener',daemon=True);self.thread.start()

    def stop(self):
        if self.server:self.server.shutdown();self.server.server_close()
        if self.thread:self.thread.join(6)
        if self.thread and self.thread.is_alive():raise RuntimeError('同事直连监听未停止')


class MeshDiscovery:
    """Use pinned LAN Mesh packets and subnet selection, without worker execution."""
    def __init__(self, identity, name, api_port, port=PORT):
        self.identity=identity;self.name=name;self.api_port=api_port;self.port=port
        self.peers={};self.lock=threading.RLock();self.stopping=threading.Event()
        self.socket=None;self.thread=None;self.error=''

    def start(self):
        try:
            sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);sock.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)
            sock.bind(('',self.port));sock.settimeout(.4);self.socket=sock
        except OSError as exc:
            if 'sock' in locals():sock.close()
            self.error='局域网发现不可用，请检查防火墙：'+str(exc);return
        self.thread=threading.Thread(target=self.run,name='orbit-mesh-discovery',daemon=True);self.thread.start()

    def packet(self):
        from lan_mesh.protocol import DiscoveryPacket
        value=DiscoveryPacket(device_id=self.identity.id,device_name=self.name,role='orbit-peer',api_port=self.api_port).to_dict()
        value.update(orbit_protocol=PROTOCOL,fingerprint=self.identity.pin)
        return canonical(value)

    def ingest(self, raw, address):
        from lan_mesh.protocol import DiscoveryPacket
        value=json.loads(raw)
        if not isinstance(value,dict) or value.get('orbit_protocol')!=PROTOCOL:return
        packet=DiscoveryPacket.from_dict(value)
        if not packet.is_lanmesh() or packet.role!='orbit-peer' or packet.device_id==self.identity.id:return
        peer=dict(id=identifier(packet.device_id),name=text(packet.device_name,80),port=packet.api_port,
                  address=lan_address(address),fingerprint=fingerprint(value['fingerprint']),seen=time.monotonic())
        if type(peer['port']) is not int or not 1<=peer['port']<=65535:raise ValueError('同事端口无效')
        with self.lock:self.peers[peer['id']]=peer

    def run(self):
        from lan_mesh.host_info import get_broadcast_targets
        next_send=0
        while not self.stopping.is_set():
            if time.monotonic()>=next_send:
                for target in get_broadcast_targets():
                    try:self.socket.sendto(self.packet(),(target,self.port))
                    except OSError:self.error='广播未送达，请检查局域网连接和防火墙'
                next_send=time.monotonic()+5
            try:
                raw,address=self.socket.recvfrom(4096);self.ingest(raw,address[0])
            except (OSError,ValueError,UnicodeError,TypeError,KeyError):continue

    def snapshot(self):
        with self.lock:return {i:dict(p) for i,p in self.peers.items() if time.monotonic()-p['seen']<20}

    def stop(self):
        self.stopping.set()
        if self.socket:self.socket.close()
        if self.thread:self.thread.join(2)
        if self.thread and self.thread.is_alive():raise RuntimeError('LAN Mesh 发现线程未停止')
