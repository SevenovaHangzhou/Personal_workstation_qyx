"""Stable GitHub release downloads and verified, isolated per-user staging."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from process_environment import system_environment

REPOSITORY = 'SevenovaHangzhou/Personal_workstation_qyx'
API = 'https://api.github.com/repos/'+REPOSITORY
VERSION = r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)'
DIGEST = re.compile(r'sha256:([a-f0-9]{64})\Z')
MAX_PACKAGE = 512*1024*1024


def version_tuple(value):
    if not isinstance(value, str) or len(value)>40 or not re.fullmatch('v?'+VERSION, value):
        raise ValueError('更新版本必须采用 x.y.z 正式版本号')
    return tuple(int(part) for part in value.removeprefix('v').split('.'))


def atomic_text(path, text):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.ota-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def cancelled(event):
    if event.is_set():
        raise InterruptedError('更新已停止，当前版本保持可用')


def allowed_url(url):
    parsed = urlsplit(url)
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443) or parsed.hostname not in {
            'api.github.com','github.com','release-assets.githubusercontent.com',
            'objects.githubusercontent.com','github-releases.githubusercontent.com'}:
        raise ValueError('更新地址不属于受支持的 GitHub 下载域名')
    return parsed


class Redirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = allowed_url(newurl)
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result and parsed.hostname!='api.github.com':
            result.remove_header('Authorization')
        return result


class GitHubSource:
    def __init__(self):
        self.opener = build_opener(Redirect()); self.token = None

    def open(self, url, binary=False):
        parsed = allowed_url(url)
        headers = {'User-Agent':'Sevenova-ORBIT-OTA', 'Accept':'application/octet-stream' if binary else 'application/vnd.github+json',
                   'X-GitHub-Api-Version':'2022-11-28'}
        if self.token and parsed.hostname=='api.github.com':
            headers['Authorization'] = 'Bearer '+self.token
        try:
            return self.opener.open(Request(url, headers=headers), timeout=15 if binary else 5)
        except HTTPError as exc:
            if exc.code in (401,404) and self.token is None:
                # Reuse an existing login only; never launch a login flow or save tokens.
                self.token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN') or ''
                if not self.token and shutil.which('gh'):
                    result = subprocess.run(['gh','auth','token','--hostname','github.com'],
                                            capture_output=True,text=True,timeout=5,env=system_environment())
                    if result.returncode==0: self.token=result.stdout.strip()
                if self.token: return self.open(url, binary)
            raise ValueError('GitHub 更新源不可访问（HTTP '+str(exc.code)+'），请稍后重试或检查现有 GitHub 登录') from None
        except (OSError,URLError):
            raise ValueError('暂时无法连接 GitHub 更新源，请稍后重试') from None

    def metadata(self, url, limit=1024*1024):
        with self.open(url) as response:
            data = response.read(limit+1)
        if len(data)>limit: raise ValueError('更新元数据超过大小限制')
        return json.loads(data)

    def latest(self):
        return self.metadata(API+'/releases/latest')

    def asset(self, row, destination, event, progress):
        cancelled(event)
        digest = hashlib.sha256(); size = 0
        with self.open(row['url'], binary=True) as response, Path(destination).open('xb') as stream:
            while True:
                cancelled(event); chunk=response.read(64*1024)
                if not chunk: break
                size += len(chunk)
                if size>row['size']: raise ValueError('更新文件大小与发布信息不一致')
                digest.update(chunk); stream.write(chunk); progress(size,row['size'])
            stream.flush(); os.fsync(stream.fileno())
        if size!=row['size'] or 'sha256:'+digest.hexdigest()!=row['digest']:
            raise ValueError('更新文件 SHA-256 或大小校验失败，已停止安装')


def release_assets(release):
    if not isinstance(release,dict) or release.get('draft') is not False or release.get('prerelease') is not False:
        raise ValueError('只接受已正式发布的稳定版本')
    version_tuple(release.get('tag_name')); version=release['tag_name'].removeprefix('v')
    if release.get('html_url')!='https://github.com/'+REPOSITORY+'/releases/tag/'+release['tag_name']:
        raise ValueError('发布来源与指定仓库不一致')
    assets = release.get('assets')
    if not isinstance(assets,list) or any(not isinstance(a,dict) for a in assets): raise ValueError('版本尚未提供有效更新安装包')
    names=[a.get('name') for a in assets if isinstance(a,dict)]
    required=['manifest.json','SHA256SUMS',f'orbit-{version}-linux-x86_64.tar.gz']
    result={}
    for name in required:
        if names.count(name)!=1: raise ValueError('新版本正在发布或缺少唯一的 '+name)
        row=next(a for a in assets if a.get('name')==name)
        if (type(row.get('id')) is not int or row['id']<=0 or row.get('url')!=API+'/releases/assets/'+str(row['id'])
                or row.get('state')!='uploaded' or type(row.get('size')) is not int
                or not 0<row['size']<=(MAX_PACKAGE if name.endswith('.gz') else 1024*1024)
                or not isinstance(row.get('digest'),str) or not DIGEST.fullmatch(row['digest'])):
            raise ValueError('更新资产信息或 SHA-256 无效')
        result[name]=row
    return version,result


def validate_manifest(manifest, sums, version, asset):
    if not isinstance(manifest,dict) or manifest.get('version')!=version or manifest.get('platform')!='linux-x86_64':
        raise ValueError('更新清单的版本或平台不匹配')
    if platform.system()!='Linux' or platform.machine()!='x86_64': raise ValueError('当前更新包仅支持 Linux x86_64')
    minimum=manifest.get('minimum_glibc')
    if not isinstance(minimum,str) or not re.fullmatch(r'[0-9]+\.[0-9]+',minimum): raise ValueError('更新包缺少有效的 glibc 要求')
    local=platform.libc_ver()[1]
    if not re.fullmatch(r'[0-9]+\.[0-9]+',local) or tuple(map(int,local.split('.')))<tuple(map(int,minimum.split('.'))):
        raise ValueError('新版本需要 glibc '+minimum+' 或更高版本')
    rows=manifest.get('artifacts',[])
    if not isinstance(rows,list):raise ValueError('更新清单格式无效')
    matches=[r for r in rows if isinstance(r,dict) and r.get('file')==asset['name']]
    if len(matches)!=1 or matches[0].get('sha256')!=asset['digest'][7:] or matches[0].get('bytes')!=asset['size']:
        raise ValueError('更新清单与 GitHub 资产校验信息不一致')
    matches=[line.split() for line in sums.splitlines() if len(line.split())==2 and line.split()[1]==asset['name']]
    if len(matches)!=1 or matches[0][0]!=asset['digest'][7:]: raise ValueError('SHA256SUMS 与更新清单不一致')


def extract_package(archive_path, destination, event):
    if not hasattr(tarfile,'data_filter'): raise ValueError('源码 OTA 解包需要 Python 3.11.8+；发布应用已包含所需环境')
    with tarfile.open(archive_path) as archive:
        total=0; seen=set()
        for member in archive:
            cancelled(event); name=PurePosixPath(member.name)
            if (name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0]!='orbit'
                    or '\\' in member.name or member.name in seen or len(seen)>=20000
                    or not (member.isfile() or member.isdir() or member.issym() or member.islnk())):
                raise ValueError('更新包包含不安全或重复的路径')
            seen.add(member.name); total+=member.size
            if total>2*1024**3: raise ValueError('更新包展开后过大')
            archive.extract(member,destination,filter='data')
    root=Path(destination)/'orbit'
    for path in root.rglob('*'):
        if path.is_symlink() and not path.resolve(strict=True).is_relative_to(root.resolve()):
            raise ValueError('更新包链接指向应用目录之外')
    for name in ('orbit','orbit-bin'):
        path=root/name
        if path.is_symlink() or not path.is_file() or not os.access(path,os.X_OK):
            raise ValueError('更新包缺少可执行的 '+name)
    return root


def probe(binary, version, folder, event):
    env=system_environment(); env.update(QT_QPA_PLATFORM='offscreen',PYINSTALLER_RESET_ENVIRONMENT='1',ORBIT_SKIP_OTA='1',
        HOME=str(folder),XDG_DATA_HOME=str(folder/'data'),XDG_STATE_HOME=str(folder/'state'))
    for key in ('PYTHONPATH','PYTHONHOME','YOMK_PREFIX_PATH','CODEX_HOME'):
        env.pop(key,None)
    for switch in ('--version','--self-test'):
        output=folder/('probe'+switch+'.log')
        with output.open('wb') as stream:
            process=subprocess.Popen([str(binary),switch],env=env,cwd=folder,stdin=subprocess.DEVNULL,
                                     stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            deadline=time.monotonic()+45
            try:
                while process.poll() is None:
                    cancelled(event)
                    if output.stat().st_size>4*1024**2: raise ValueError('更新包自检输出异常，已停止验证')
                    if time.monotonic()>deadline: raise ValueError('更新包隔离自检超时')
                    event.wait(.1)
                if process.returncode: raise ValueError('更新包隔离自检失败，保留当前版本')
            finally:
                if process.poll() is None:
                    os.killpg(process.pid,signal.SIGTERM)
                    try:process.wait(timeout=2)
                    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=2)
        text=output.read_text(encoding='utf-8',errors='replace')
        if switch=='--version' and not text.strip().endswith(' '+version): raise ValueError('更新包实际版本与发布版本不一致')
        if switch=='--self-test':
            rows=[]
            for line in text.splitlines():
                try:rows.append(json.loads(line))
                except ValueError:pass
            if not any(isinstance(r,dict) and r.get('app_version')==version and r.get('model_called') is False for r in rows):
                raise ValueError('更新包未返回有效的离线自检结果')


def stage_release(folder, release, event, progress, source=None):
    source=source or GitHubSource(); folder=Path(folder)
    version,assets=release_assets(release); package=assets[f'orbit-{version}-linux-x86_64.tar.gz']
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    if shutil.disk_usage(folder).free<package['size']*6+100*1024**2: raise ValueError('磁盘空间不足，无法安全准备更新与回退版本')
    with tempfile.TemporaryDirectory(prefix='.incoming-',dir=folder) as temporary:
        temporary=Path(temporary)
        for name in ('manifest.json','SHA256SUMS'):
            source.asset(assets[name],temporary/name,event,lambda *_:None)
        validate_manifest(json.loads((temporary/'manifest.json').read_text()),(temporary/'SHA256SUMS').read_text(),version,package)
        progress('downloading',0,package['size'])
        archive=temporary/'package.tar.gz'
        source.asset(package,archive,event,lambda done,total:progress('downloading',done,total))
        cancelled(event); progress('verifying',package['size'],package['size'])
        root=extract_package(archive,temporary/'extracted',event)
        probe_home=temporary/'probe';probe_home.mkdir()
        probe(root/'orbit-bin',version,probe_home,event);cancelled(event)
        stamp=dict(version=version,sha256=package['digest'][7:])
        atomic_text(root/'ota-install.json',json.dumps(stamp))
        target=folder/'releases'/version
        target.parent.mkdir(exist_ok=True,mode=0o700)
        if target.exists():
            if target.is_symlink() or json.loads((target/'ota-install.json').read_text())!=stamp:
                raise ValueError('同版本更新目录已存在且校验不同，未覆盖已有文件')
            probe(target/'orbit-bin',version,probe_home,event)
        else:os.replace(root,target)
        return stamp
