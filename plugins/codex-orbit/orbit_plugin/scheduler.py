"""Per-user OS scheduling. One stable job per data space, no passwords or API keys."""
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from evaluation.report_archive import write_json
from process_environment import codex_environment, find_codex


class ScheduleError(ValueError):
    pass


def clock(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
        raise ScheduleError('时间需为 24 小时制 HH:MM，例如 00:00 或 23:30。')
    return value


def local_boundary(day, at):
    hour, minute = map(int, clock(at).split(':'))
    # Interpret every calendar day in the OS timezone, including DST changes.
    return datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute).astimezone()


def latest_boundary(now, at):
    now = now.astimezone()
    boundary = local_boundary(now.date(), at)
    return boundary if boundary.timestamp() <= now.timestamp() else local_boundary(now.date()-timedelta(days=1), at)


def unit_quote(value, executable=False):
    if any(ord(c) < 32 for c in str(value)):
        raise ScheduleError('调度路径不能包含控制字符。')
    value = str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    if executable:
        value = value.replace('$', '$$')
    return '"' + value + '"'


def environment():
    env = codex_environment()
    if sys.platform.startswith('linux'):
        runtime = Path('/run/user') / str(os.getuid())
        if (runtime/'bus').exists():
            env.setdefault('XDG_RUNTIME_DIR', str(runtime))
            env.setdefault('DBUS_SESSION_BUS_ADDRESS', 'unix:path=' + str(runtime/'bus'))
    return env


def run(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=30, env=environment(), **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
    except (OSError, subprocess.TimeoutExpired):
        raise ScheduleError('无法连接系统任务调度器，请在已登录的桌面中重试。') from None
    if result.returncode:
        raise ScheduleError('系统任务调度未完成，请检查当前用户的调度权限；插件和原日志已保留。')
    return result.stdout


class Scheduler:
    def __init__(self, directory, *, python=None, script=None, platform=None, config_home=None, execute=None):
        self.directory = Path(directory).resolve()
        self.root = self.directory/'schedule'
        self.config_path = self.root/'config.json'
        self.registration_path = self.root/'registration.json'
        self.platform = platform or sys.platform
        self.config_home = Path(config_home or os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')
        self.execute = execute or run
        self.python = str(python or sys.executable)
        self.script = str(script or Path(__file__).resolve().parents[1]/'scripts/mcp.py')
        self.explicit_paths = python is not None or script is not None
        self.name = 'orbit-codex-daily-' + hashlib.sha256(str(self.directory).encode()).hexdigest()[:16]

    def config(self):
        if not self.config_path.exists():
            return dict(version=1, time='00:00', enabled=True, first_boundary='', source='bound_codex')
        value = json.loads(self.config_path.read_text(encoding='utf-8'))
        if (set(value) != {'version','time','enabled','first_boundary','source'} or value['version'] != 1 or
                type(value['enabled']) is not bool or value['source'] != 'bound_codex'):
            raise ScheduleError('日报定时设置无效，原文件保留。')
        clock(value['time'])
        datetime.fromisoformat(value['first_boundary'])
        return value

    def registration(self):
        return json.loads(self.registration_path.read_text(encoding='utf-8')) if self.registration_path.exists() else {}

    def status(self):
        value = self.config()
        registration = self.registration()
        last = self.directory/'daily/state.json'
        return dict(config=value, registration=registration,
                    last_run=json.loads(last.read_text(encoding='utf-8')) if last.exists() else None)

    def command(self):
        old = self.registration()
        python = self.python if self.explicit_paths else old.get('python', self.python)
        script = self.script if self.explicit_paths else old.get('script', self.script)
        if not Path(python).is_absolute() or not Path(script).is_absolute() or not Path(script).is_file():
            raise ScheduleError('定时任务需使用已安装插件的稳定绝对路径，请重新运行安装器。')
        return [python, script, '--collect-daily', '--data-dir', str(self.directory)]

    def configure(self, at=None, enabled=None, now=None):
        from platform_support import lock_file
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (self.root/'configure.lock').open('a+') as guard:
            try: lock_file(guard)
            except OSError: raise ScheduleError('另一个窗口正在更新定时设置，请稍后重试。') from None
            return self._configure(at,enabled,now)

    def _configure(self, at, enabled, now):
        if self.registration().get('backend') == 'codex-automation':
            raise ScheduleError('此日报由 Codex 自动化管理，请在 Codex 中修改时间或暂停；不会另建系统任务。')
        old = self.config()
        value = dict(old, time=clock(at) if at is not None else old['time'],
                     enabled=enabled if enabled is not None else old['enabled'])
        if type(value['enabled']) is not bool:
            raise ScheduleError('启用状态无效。')
        now = now or datetime.now().astimezone()
        if not value['first_boundary'] or (old['time']!=value['time'] and not (self.directory/'daily/state.json').exists()):
            value['first_boundary'] = local_boundary(latest_boundary(now,value['time']).date()+timedelta(days=1), value['time']).date().isoformat()
        command = self.command()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        registration = dict(name=self.name, python=command[0], script=command[1],
                            codex_home=os.environ.get('CODEX_HOME', str(Path.home()/'.codex')),
                            codex_binary=find_codex() or self.registration().get('codex_binary',''),
                            configured_at=now.isoformat(), registered=False)
        previous={path:path.read_bytes() if path.exists() else None for path in (self.config_path,self.registration_path)}
        had_registration=self.registration_path.exists()
        was_enabled=had_registration and old['enabled']
        write_json(self.config_path, value)
        write_json(self.registration_path, registration)
        try:
            if self.platform.startswith('linux'):
                backend = self._linux(value, command, was_enabled)
            elif self.platform == 'win32':
                backend = self._windows(value, command, had_registration)
            else:
                raise ScheduleError('自动日报目前支持 Linux 和 Windows。')
        except BaseException:
            from evaluation.mesh import _private_write
            for path,raw in previous.items():
                if raw is None: path.unlink(missing_ok=True)
                else: _private_write(path,raw)
            raise
        registration.update(backend=backend,registered=True)
        write_json(self.registration_path,registration)
        return dict(config=value, registration=registration)

    def _linux(self, value, command, was_enabled=False):
        units = self.config_home/'systemd/user'
        units.mkdir(parents=True, exist_ok=True)
        service, timer = units/(self.name+'.service'), units/(self.name+'.timer')
        old = {path: path.read_bytes() if path.exists() else None for path in (service,timer)}
        if any(path.is_symlink() for path in (service,timer)):
            raise ScheduleError('同名调度文件是链接，未覆盖。')
        from evaluation.mesh import _private_write
        service_body = ('# Managed by ORBIT Codex\n[Unit]\nDescription=ORBIT bound-chat daily journal\n'
            '[Service]\nType=oneshot\nExecStart=' + ' '.join(unit_quote(arg,True) for arg in command) +
            '\nWorkingDirectory=' + str(self.directory).replace('%','%%') + '\nTimeoutStartSec=2h\nUMask=0077\n'
            'Environment=' + unit_quote('PATH='+environment().get('PATH',os.defpath)) + '\n')
        timer_body = ('# Managed by ORBIT Codex\n[Unit]\nDescription=ORBIT daily journal timer\n'
            '[Timer]\nOnCalendar=*-*-* '+value['time']+':00\nPersistent=true\nAccuracySec=1s\n'
            'Unit='+self.name+'.service\n[Install]\nWantedBy=timers.target\n')
        for path, previous in old.items():
            if previous is not None and not previous.startswith(b'# Managed by ORBIT Codex\n'):
                raise ScheduleError('同名调度文件不属于星序，未覆盖。')
        try:
            _private_write(service, service_body.encode('utf-8'))
            _private_write(timer, timer_body.encode('utf-8'))
            self.execute(['systemctl','--user','daemon-reload'])
            if value['enabled']:
                self.execute(['systemctl','--user','enable','--now',timer.name])
                self.execute(['systemctl','--user','restart',timer.name])
                self.execute(['systemctl','--user','is-active',timer.name])
            else:
                self.execute(['systemctl','--user','disable','--now',timer.name])
        except BaseException:
            for path, previous in old.items():
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    _private_write(path,previous)
            try:
                self.execute(['systemctl','--user','daemon-reload'])
                self.execute(['systemctl','--user','restart',timer.name] if was_enabled else ['systemctl','--user','disable','--now',timer.name])
            except ScheduleError:
                pass
            raise
        return 'systemd-user'

    def _windows(self, value, command, had_registration=False):
        ns = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
        ET.register_namespace('',ns)
        def child(parent,name,text=None,**attributes):
            node=ET.SubElement(parent,'{'+ns+'}'+name,attributes)
            if text is not None: node.text=text
            return node
        task=ET.Element('{'+ns+'}Task',version='1.2')
        previous_xml=self.execute(['schtasks.exe','/Query','/TN',self.name,'/XML']) if had_registration else None
        if previous_xml:
            previous_task=ET.fromstring(previous_xml)
            description=previous_task.find('.//{'+ns+'}Description')
            if description is None or not (description.text or '').startswith('Managed by ORBIT Codex:'):
                raise ScheduleError('同名 Windows 任务不属于星序，未覆盖。')
            if not value['enabled']:
                self.execute(['schtasks.exe','/Change','/TN',self.name,'/Disable'])
                return 'windows-task-scheduler'
        info=child(task,'RegistrationInfo')
        child(info,'Description','Managed by ORBIT Codex: bound-chat daily journal')
        triggers=child(task,'Triggers'); calendar=child(triggers,'CalendarTrigger')
        child(calendar,'StartBoundary',value['first_boundary']+'T'+value['time']+':00')
        child(calendar,'Enabled','true' if value['enabled'] else 'false');child(child(calendar,'ScheduleByDay'),'DaysInterval','1')
        principals=child(task,'Principals');principal=child(principals,'Principal',id='Author')
        user=self.execute(['whoami.exe','/user','/fo','csv','/nh'])
        match=re.search(r'S-1-\d+(?:-\d+)+',user)
        if not match: raise ScheduleError('无法确认当前 Windows 用户，未注册定时任务。')
        child(principal,'UserId',match[0]);child(principal,'LogonType','InteractiveToken');child(principal,'RunLevel','LeastPrivilege')
        settings=child(task,'Settings')
        for name,text in [('MultipleInstancesPolicy','IgnoreNew'),('DisallowStartIfOnBatteries','false'),
                          ('StopIfGoingOnBatteries','false'),('StartWhenAvailable','true'),('ExecutionTimeLimit','PT2H'),
                          ('Enabled','true' if value['enabled'] else 'false')]:
            child(settings,name,text)
        action=child(child(task,'Actions',Context='Author'),'Exec')
        child(action,'Command',command[0]);child(action,'Arguments',subprocess.list2cmdline(command[1:]))
        child(action,'WorkingDirectory',str(self.directory))
        fd, temporary=tempfile.mkstemp(suffix='.xml',prefix='orbit-schedule-',dir=self.root)
        created=False
        try:
            with os.fdopen(fd,'wb') as stream: stream.write(ET.tostring(task,encoding='utf-16',xml_declaration=True))
            self.execute(['schtasks.exe','/Create','/TN',self.name,'/XML',temporary]+(['/F'] if had_registration else []))
            created=True
            actual=self.execute(['schtasks.exe','/Query','/TN',self.name,'/XML'])
            parsed=ET.fromstring(actual)
            if (parsed.find('.//{'+ns+'}Command').text != command[0] or
                    parsed.find('.//{'+ns+'}Arguments').text != subprocess.list2cmdline(command[1:])):
                raise ScheduleError('Windows 未确认正确的定时任务入口。')
        except BaseException:
            if created:
                try:
                    if previous_xml:
                        Path(temporary).write_bytes(previous_xml.encode('utf-16'))
                        self.execute(['schtasks.exe','/Create','/TN',self.name,'/XML',temporary,'/F'])
                    else:
                        self.execute(['schtasks.exe','/Delete','/TN',self.name,'/F'])
                except ScheduleError:
                    pass
            raise
        finally:
            Path(temporary).unlink(missing_ok=True)
        return 'windows-task-scheduler'


def open_card(orbit):
    from .settings import desktop_environment
    from platform_support import process_options
    env=desktop_environment()
    command=[sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/mcp.py'),'--schedule','--data-dir',str(orbit.directory)]
    if env.get('QT_QPA_PLATFORM') in ('offscreen','minimal') or (sys.platform.startswith('linux') and not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY'))):
        return dict(state='ui_unavailable',local_command=command)
    subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                     close_fds=True,env=env,**process_options())
    return dict(state='opening',message='已打开日报定时卡片，可修改每天的收集时间。')
