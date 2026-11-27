"""Prepare a native Codex automation; only the desktop host creates that job."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import uuid

from evaluation.report_archive import write_json
from platform_support import lock_file
from .scheduler import ScheduleError, clock, latest_boundary, local_boundary


def bootstrap_instructions(directory, enabled=True):
    """Read-only MCP onboarding; never create a job while discovering tools."""
    if not enabled:
        return ''
    root = Path(directory)/'schedule'
    try:
        registration = json.loads((root/'registration.json').read_text(encoding='utf-8')) if (root/'registration.json').exists() else {}
        if registration.get('backend') == 'codex-automation' and registration.get('automation_id'):
            return ''  # Also respect a native task the user later paused or deleted.
        if registration.get('registered') and not (root/'codex-request.json').exists():
            return ''  # An explicitly selected system scheduler remains authoritative.
    except (OSError, ValueError, AttributeError):
        return '星序日报注册记录无法读取。保留原文件，说明待修复，不自动新建日报任务。'
    reference = Path(__file__).resolve().parents[1]/'skills/orbit-setup/references/daily-automation.md'
    return (
        '星序安装后的每日自动日报尚未完成。当前启用了安装默认的自动创建功能。'
        '在本次 Codex 桌面会话中，自动执行 prepare_daily_automation → 宿主 automation_update 查重、创建并读回'
        ' → confirm_daily_automation；无需等待用户另说“打开星序设置”，也不等待 Linear 密钥。'
        'prepare 返回 state=waiting 时由另一个会话处理，不创建；已有 ID 时复用，保留时间、暂停和通知偏好。'
        '本会话完成后不重复执行；用户明确不要日报时遵循用户选择。'
        '缺少宿主工具时仅说明待 Codex 桌面完成，不改写自动化文件或数据库，不虚报成功。'
        '仅完成日报创建，不打开连接卡片或启动采集；随后继续用户当前任务。详细流程：'+str(reference)
    )


def prepare(scheduler, at=None, now=None, owner=None):
    scheduler.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (scheduler.root/'configure.lock').open('a+') as guard:
        try: lock_file(guard)
        except OSError: raise ScheduleError('另一个窗口正在更新定时设置，请稍后重试。') from None
        path = scheduler.root/'codex-request.json'
        previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        # Installer choices survive the first MCP launch, before confirmation.
        config = scheduler.config() if scheduler.config_path.exists() else dict(previous.get('config') or scheduler.config())
        if at is not None:
            config['time'] = clock(at)
        now = now or datetime.now().astimezone()
        if not config['first_boundary']:
            config['first_boundary'] = local_boundary(latest_boundary(now, config['time']).date()+timedelta(days=1), config['time']).date().isoformat()
        registration = scheduler.registration()
        command = scheduler.command()
        if not scheduler.explicit_paths and not registration.get('script') and previous.get('data_dir') == str(scheduler.directory):
            stable = previous.get('command', [])
            if (len(stable) == 5 and stable[2:] == ['--collect-daily', '--data-dir', str(scheduler.directory)]
                    and all(isinstance(v, str) for v in stable) and Path(stable[0]).is_absolute()
                    and Path(stable[0]).is_file() and Path(stable[1]).is_absolute() and Path(stable[1]).is_file()):
                command = stable
        request = dict(version=1, config=config, command=command, automation_id=registration.get('automation_id', ''),
            name='星序 · 每日更新日报', data_dir=str(scheduler.directory))
        lease_path = scheduler.root/'codex-claim.json'
        lease = json.loads(lease_path.read_text(encoding='utf-8')) if lease_path.exists() else {}
        if not request['automation_id'] and lease.get('expires_at', 0) > now.timestamp() and lease.get('owner') != owner:
            return dict(state='waiting', name=request['name'], retry_after=lease['expires_at'])
        # Repeated installs preserve the request token and the native job ID.
        request['token'] = previous.get('token', '') if all(previous.get(k) == v for k, v in request.items()) else str(uuid.uuid4())
        if owner and not request['automation_id']:
            if lease.get('owner') != owner:
                request['token'] = str(uuid.uuid4())
            write_json(lease_path, dict(owner=owner, expires_at=now.timestamp()+900))
        write_json(path, request)
        return dict(request, state='registered' if request['automation_id'] else 'ready')


def confirm(scheduler, token, automation_id, host_verified):
    if host_verified is not True or not isinstance(automation_id, str) or not automation_id.strip() or len(automation_id) > 256:
        raise ScheduleError('请先使用 Codex 宿主创建或更新自动化，并读回核对实际 ID。')
    scheduler.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (scheduler.root/'configure.lock').open('a+') as guard:
        try: lock_file(guard)
        except OSError: raise ScheduleError('另一个窗口正在更新定时设置，请稍后重试。') from None
        path = scheduler.root/'codex-request.json'
        request = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if not token or request.get('token') != token:
            raise ScheduleError('安装请求已变化，请重新读取后核对。')
        old = scheduler.registration()
        if old.get('backend') == 'codex-automation' and old.get('automation_id') != automation_id:
            raise ScheduleError('已有另一项 Codex 日报任务，请核对原 ID，避免重复创建。')
        # Keep the old timer until the new host job is confirmed. Its own rollback
        # preserves the old registration if disabling fails; retry the same ID.
        if old.get('registered') and old.get('backend') != 'codex-automation':
            scheduler._configure(None, False, datetime.now().astimezone())
        command = request['command']
        registration = dict(old, name=scheduler.name, python=command[0], script=command[1],
            backend='codex-automation', registered=True, automation_id=automation_id,
            codex_home=old.get('codex_home') or os.environ.get('CODEX_HOME', str(Path.home()/'.codex')),
            configured_at=datetime.now().astimezone().isoformat())
        # Native pause/resume controls execution. A stale local disabled flag
        # must not prevent collection when the user resumes the native job.
        config=dict(request['config'], enabled=True)
        write_json(scheduler.config_path, config)
        write_json(scheduler.registration_path, registration)
        return dict(config=config, registration=registration)
