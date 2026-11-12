"""Certificate-pinned TLS transport, bounded responses and no redirect/proxy leakage."""
import hashlib
import http.client
import ipaddress
import json
import re
import ssl
from urllib.parse import urlsplit
from collaboration.protocol import PROTOCOL


def endpoint(value):
    parsed=urlsplit(value)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ValueError('请输入 https://服务器地址:端口，不包含路径或账号')
    try:
        ip=ipaddress.ip_address(parsed.hostname)
        if not ip.is_private or ip.is_unspecified or ip.is_multicast:raise ValueError('协作服务器应使用局域网地址')
    except ValueError:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,251}',parsed.hostname):raise ValueError('服务器地址无效')
        # A public numeric address must not pass as a DNS hostname.
        if re.fullmatch(r'[0-9.]+',parsed.hostname):raise ValueError('协作服务器应使用局域网地址')
    port=parsed.port or 443
    if not 1<=port<=65535:raise ValueError('端口无效')
    return parsed.hostname,port


class Transport:
    def __init__(self,url,fingerprint,device_id,token):
        self.host,self.port=endpoint(url);self.device_id=device_id;self.token=token
        fingerprint=fingerprint.replace(':','').strip().lower()
        if not re.fullmatch('[0-9a-f]{64}',fingerprint):raise ValueError('请填写管理员提供的 SHA256 证书指纹（64位）')
        self.fingerprint=fingerprint

    def request(self,operation,payload):
        # Self-signed LAN TLS is authenticated by the explicit pinned SHA256,
        # checked BEFORE sending any HTTP bytes or token. No trust-on-first-use.
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);context.check_hostname=False;context.verify_mode=ssl.CERT_NONE
        context.minimum_version=ssl.TLSVersion.TLSv1_2
        connection=http.client.HTTPSConnection(self.host,self.port,timeout=5,context=context)
        try:
            connection.connect()
            actual=hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
            if actual!=self.fingerprint:raise ValueError('服务器证书指纹不符，未发送凭据或内容；请联系管理员确认')
            body=json.dumps(payload,ensure_ascii=False).encode()
            connection.request('POST','/v1/'+operation,body,{'Content-Type':'application/json',
                'Authorization':'Bearer '+self.token,'X-Orbit-Device':self.device_id,'X-Orbit-Protocol':PROTOCOL})
            response=connection.getresponse();raw=response.read(2_000_001)
            if len(raw)>2_000_000:raise ValueError('服务器响应过大')
            try:value=json.loads(raw)
            except (ValueError,UnicodeError):raise ValueError('服务器响应不是有效 JSON') from None
            if response.status!=200:raise ValueError(value.get('error','协作请求失败') if isinstance(value,dict) else '协作请求失败')
            if not isinstance(value,dict) or set(value)!={'data'}:raise ValueError('服务器响应格式不符')
            return value['data']
        finally:connection.close()
