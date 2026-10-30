"""Local daily refs plus staged/worktree patches and untracked files, without commits."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from process_environment import system_environment


class SnapshotError(RuntimeError):
    pass


def git(root, *args):
    env = {k: v for k,v in system_environment().items() if not k.startswith('GIT_')}
    env['GIT_OPTIONAL_LOCKS'] = '0'
    try:
        result = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
                                 '-C', str(root), *args], env=env, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise SnapshotError('Git 操作失败或超时') from None
    if result.returncode:
        raise SnapshotError('Git 操作未成功：'+' '.join(args[:2]))
    return result.stdout


def file_digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def daily_snapshot(root, folder, day, task, cancel):
    root = Path(root).resolve(); folder = Path(folder)
    if git(root, 'rev-parse', '--is-bare-repository').strip() == b'true':
        raise SnapshotError('裸仓库没有工作文件，无法留存未提交修改')
    root = Path(os.fsdecode(git(root, 'rev-parse', '--show-toplevel').strip())).resolve()
    status = git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    if not status:
        return {'state': 'clean', 'root': str(root)}
    try:
        head = git(root, 'rev-parse', '--verify', 'HEAD').decode().strip()
    except SnapshotError:
        raise SnapshotError('仓库尚无初始提交；请先手动创建初始提交，再生成每日分支') from None
    # git diff cannot archive dirty submodule contents. Report this explicitly.
    modules = git(root, 'submodule', 'status', '--recursive')
    for line in modules.decode(errors='replace').splitlines():
        path = line[1:].split(' ', 2)[1]
        if git(root/path, 'status', '--porcelain', '-z'):
            raise SnapshotError('子模块存在未保存修改，请为子模块单独关联任务并留存：'+path)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = Path(tempfile.mkdtemp(prefix='.snapshot-', dir=folder))
    try:
        staged = git(root, 'diff', '--binary', '--full-index', '--no-ext-diff', '--no-textconv', '--cached', head)
        working = git(root, 'diff', '--binary', '--full-index', '--no-ext-diff', '--no-textconv')
        (temp/'staged.patch').write_bytes(staged)
        (temp/'working.patch').write_bytes(working)
        names = [os.fsdecode(p) for p in git(root, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0') if p]
        identities = []
        with tarfile.open(temp/'untracked.tar', 'w') as archive:
            for name in names:
                if cancel.is_set(): raise InterruptedError('已停止 Git 留存')
                path = root/name
                if Path(name).is_absolute() or '..' in Path(name).parts:
                    raise SnapshotError('Git 返回了无效文件路径')
                if not path.parent.resolve().is_relative_to(root):
                    raise SnapshotError('未跟踪文件的父目录指向仓库外部，留存未完成')
                # No symlink dereferencing. Ignored files never enter ls-files results.
                if path.is_symlink():
                    digest = 'symlink:'+os.readlink(path)
                elif path.is_file():
                    digest = file_digest(path)
                else:
                    raise SnapshotError('未跟踪项不是普通文件或符号链接：'+name)
                archive.add(path, arcname=name, recursive=False)
                member = archive.getmember(name)
                if path.is_file() and not path.is_symlink():
                    with path.open('rb') as stream:
                        # The later re-check detects changes concurrent with archiving.
                        if member.size != path.stat().st_size: raise SnapshotError('未跟踪文件大小变化，请重试')
                identities.append([name, digest])
        with tarfile.open(temp/'untracked.tar', 'r') as archive:
            for name, expected in identities:
                member = archive.getmember(name)
                if expected.startswith('symlink:'):
                    actual = 'symlink:'+member.linkname
                else:
                    h = hashlib.sha256()
                    with archive.extractfile(member) as stream:
                        for chunk in iter(lambda:stream.read(1024*1024), b''): h.update(chunk)
                    actual = h.hexdigest()
                if actual != expected: raise SnapshotError('归档内容在读取期间发生变化，请重试')
        fingerprint = hashlib.sha256(head.encode()+staged+working+json.dumps(identities, ensure_ascii=False).encode()).hexdigest()
        label = next((b['label'] for b in task['bindings'] if b['kind']=='linear' and not b.get('demo')), 'pending')
        label = re.sub('[^A-Za-z0-9_-]', '-', label)
        branch = 'momo/daily/'+day+'/'+label+'-'+task['id'][:8]
        # Re-check a running editor/CLI did not change tracked files during capture.
        if head != git(root,'rev-parse','HEAD').decode().strip() or status != git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all') or staged != git(root,'diff','--binary','--full-index','--no-ext-diff','--no-textconv','--cached',head) or working != git(root,'diff','--binary','--full-index','--no-ext-diff','--no-textconv'):
            raise SnapshotError('留存过程中代码发生变化，本次未确认为成功；请稍后重试')
        for name, digest in identities:
            path = root/name
            current = 'symlink:'+os.readlink(path) if path.is_symlink() else file_digest(path)
            if digest != current:
                raise SnapshotError('未跟踪文件在留存期间发生变化，请稍后重试')
        if cancel.is_set(): raise InterruptedError('已停止 Git 留存')
        ref = 'refs/heads/'+branch
        try:
            existing = git(root,'show-ref','--verify','--hash',ref).decode().strip()
        except SnapshotError:
            existing = ''
        if existing and existing != head:
            raise SnapshotError('同名每日分支已指向其他提交；保留分支，请人工核实')
        if not existing:
            git(root, 'update-ref', ref, head, '0'*len(head))
        manifest = {'version':1, 'captured_at':datetime.now(timezone.utc).isoformat(), 'day':day, 'task_id':task['id'], 'issue':label, 'root':str(root),
                    'base_commit':head, 'branch':branch, 'fingerprint':fingerprint,
                    'untracked':identities,
                    'restore':'在独立副本切换至 base_commit，补丁非空时先 git apply --index staged.patch，再 git apply working.patch；检查后提取 untracked.tar。当前工作目录和索引从未切换或重置。'}
        (temp/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
        target = folder/(day+'-'+task['id'][:8]+'-'+fingerprint[:16])
        if target.exists():
            shutil.rmtree(temp)
        else:
            temp.rename(target)
        return {'state':'saved','root':str(root),'branch':branch,'path':str(target),'base_commit':head}
    finally:
        if temp.exists(): shutil.rmtree(temp)
