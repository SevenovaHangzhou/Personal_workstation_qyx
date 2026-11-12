"""Minimal LAN Mesh presence adapter. No chat, task title, credential or reply storm."""
import hashlib
import json
import socket
import threading
import time
from collaboration.protocol import PROTOCOL, DISCOVERY_PORT, identifier, text


class Discovery:
    def __init__(self,device_id,name,server,port=DISCOVERY_PORT):
        self.id=device_id;self.name=name;self.port=port
        self.server=hashlib.sha256(server.encode()).hexdigest()[:24]
        self.peers={};self.lock=threading.RLock();self.stopping=threading.Event()
        self.thread=None;self.socket=None;self.error=''

    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stopping.clear();self.error=''
        sock=None
        try:
            sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1);sock.bind(('',self.port));sock.settimeout(.4)
            self.socket=sock
        except OSError as exc:
            if sock:sock.close()
            self.error='局域网广播不可用，仍可查看同一服务器的在线同事：'+str(exc);return
        self.thread=threading.Thread(target=self.run,name='orbit-lan-presence',daemon=True);self.thread.start()

    def run(self):
        next_send=0
        while not self.stopping.is_set():
            if time.monotonic()>=next_send:
                packet=json.dumps(dict(protocol=PROTOCOL,device_id=self.id,name=self.name,server=self.server),ensure_ascii=False).encode()
                try:self.socket.sendto(packet,('255.255.255.255',self.port))
                except OSError: self.error='广播未送达；可通过统一服务器发现同事'
                next_send=time.monotonic()+10
            try:
                raw,address=self.socket.recvfrom(2048);value=json.loads(raw)
                if not isinstance(value,dict) or value.get('protocol')!=PROTOCOL or value.get('server')!=self.server:continue
                peer=identifier(value.get('device_id'));name=text(value.get('name'),80)
                if peer!=self.id:
                    with self.lock:self.peers[peer]=dict(id=peer,name=name,address=address[0],seen=time.monotonic())
            except (OSError,ValueError,UnicodeError,TypeError):continue

    def snapshot(self):
        with self.lock:
            now=time.monotonic();self.peers={i:p for i,p in self.peers.items() if now-p['seen']<35}
            return {i:dict(p) for i,p in self.peers.items()}

    def stop(self):
        self.stopping.set()
        if self.socket:self.socket.close()
        if self.thread:self.thread.join(2)
        if self.thread and self.thread.is_alive():raise RuntimeError('局域网发现线程未停止')
