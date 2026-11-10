"""Default data identity and a locked, same-filesystem legacy migration."""
from platform_support import lock_file, WINDOWS
import hashlib
import json
import os
from pathlib import Path

MIGRATION_MARKER = '.orbit-data-migration.json'


def credential_identity(directory):
    """Keep existing keyring entries reachable after the directory moves."""
    directory=Path(directory)
    marker=directory/MIGRATION_MARKER
    if marker.is_file() and not marker.is_symlink():
        record=json.loads(marker.read_text(encoding='utf-8'))
        origin=record.get('credential_path',record.get('legacy_path'))
        if isinstance(origin,str) and Path(origin).is_absolute():
            return 'momo-linear-'+hashlib.sha256(origin.encode()).hexdigest()[:20]
    return 'momo-linear-'+hashlib.sha256(str(directory.resolve()).encode()).hexdigest()[:20]


def default_data_directory(data_home):
    """Never move an active space or merge two independent spaces.

    The compatibility symlink preserves stored absolute artifact paths and old
    launchers. The application's existing momo.lock inode moves with the data.
    A process-wide migration lock also serializes first launches of this version.
    """
    home=Path(data_home).absolute(); target=home/'Sevenova/Orbit'; legacy=home/'MomoLocal/Momo'
    if WINDOWS:
        # Windows has not shipped a previous binary. Retain any source user's
        # legacy space rather than requiring administrator symlink privileges.
        return legacy if legacy.is_dir() and not target.exists() else target
    home.mkdir(parents=True,exist_ok=True)
    with (home/'.orbit-data-migration.lock').open('a+') as guard:
        lock_file(guard,blocking=True)
        if legacy.is_symlink():
            if legacy.resolve()!=target.resolve() or not target.is_dir():
                raise RuntimeError('旧数据目录指向自定义位置，请使用 --data-dir 指定；未自动移动')
            return target
        if target.exists():
            if not target.is_dir() or target.is_symlink(): raise RuntimeError('星序数据目录无效，未修改已有文件')
            if legacy.exists(): raise RuntimeError('发现两个独立数据空间，未自动合并；请使用 --data-dir 选择')
            marker=target/MIGRATION_MARKER
            if marker.is_file() and json.loads(marker.read_text()).get('legacy_path')==str(legacy):
                legacy.parent.mkdir(parents=True,exist_ok=True)
                legacy.symlink_to(target,target_is_directory=True)
            return target
        if not legacy.exists():
            return target
        if not legacy.is_dir(): raise RuntimeError('旧数据路径不是目录，未修改已有文件')
        lock=legacy/'momo.lock'
        if lock.is_symlink(): raise RuntimeError('旧数据锁路径异常，未迁移')
        with lock.open('a+') as owner:
            try: lock_file(owner)
            except BlockingIOError:
                # Return exactly the old path so second-launch activation can
                # still reach the running old version's existing socket.
                return legacy
            target.parent.mkdir(parents=True,exist_ok=True)
            record={'version':1,'legacy_path':str(legacy),'data_path':str(target),'credential_path':str(legacy.resolve()),
                    'method':'rename-with-compatibility-symlink'}
            marker=legacy/MIGRATION_MARKER; temporary=legacy/(MIGRATION_MARKER+'.tmp')
            with temporary.open('w',encoding='utf-8') as stream:
                json.dump(record,stream,ensure_ascii=False,indent=2); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary,marker)
            # os.rename fails rather than copying across filesystems, leaving
            # the original data intact. Keep the source lock until alias exists.
            os.rename(legacy,target)
            try: legacy.symlink_to(target,target_is_directory=True)
            except OSError:
                os.rename(target,legacy)
                raise
            return target
