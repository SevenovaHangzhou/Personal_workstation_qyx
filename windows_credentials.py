"""User-bound DPAPI credential storage; no plaintext fallback on Windows."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import tempfile


class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_byte))]


def transform(data,decrypt=False):
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_byte)));output=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if decrypt:
        crypt.CryptUnprotectData.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        ok=crypt.CryptUnprotectData(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output))
    else:
        crypt.CryptProtectData.argtypes=[ctypes.POINTER(Blob),wintypes.LPCWSTR,ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        ok=crypt.CryptProtectData(ctypes.byref(source),'ORBIT Linear',None,None,None,1,ctypes.byref(output))
    if not ok:raise OSError('Windows 凭据加密或解密失败')
    try:return ctypes.string_at(output.data,output.size)
    finally:kernel.LocalFree(output.data)


def safe(path):
    for parent in (path,*path.parents):
        if parent.exists() and getattr(parent.lstat(),'st_file_attributes',0)&0x400:
            raise OSError('凭据路径包含重解析点，未访问')


def load(path):
    path=Path(str(path)+'.dpapi');safe(path)
    if not path.exists():return '', ''
    if path.stat().st_size>16384:raise OSError('Windows 凭据文件格式无效')
    value=transform(path.read_bytes(),True).decode('utf-8')
    if not value or len(value)>4096 or any(c.isspace() for c in value):raise OSError('Windows 凭据格式无效')
    return value,'Windows DPAPI'


def save(path,value):
    path=Path(str(path)+'.dpapi');safe(path);path.parent.mkdir(parents=True,exist_ok=True)
    payload=transform(value.encode('utf-8'))
    fd,temporary=tempfile.mkstemp(prefix='linear-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(payload);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:Path(temporary).unlink(missing_ok=True)
    return 'Windows DPAPI'


def clear(path):
    path=Path(str(path)+'.dpapi');safe(path);path.unlink(missing_ok=True)
