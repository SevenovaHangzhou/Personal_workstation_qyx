#!/usr/bin/env python3
"""Install a dedicated Linux user service without sudo or Nginx changes."""
import argparse
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import ssl
import subprocess
import sys
import time
import venv


def replace_text(path, value):
    pending = path.with_name(path.name + '.next')
    pending.write_text(value, encoding='utf-8')
    pending.replace(path)


def point(root, release):
    pending = root / 'current.next'
    pending.unlink(missing_ok=True)
    pending.symlink_to(release)
    pending.replace(root / 'current')


def healthy(host, port, cert):
    context = ssl.create_default_context(cafile=str(cert))
    connection = http.client.HTTPSConnection(host, port, timeout=2, context=context)
    try:
        connection.request('GET', '/health')
        response = connection.getresponse()
        data = json.loads(response.read(4096))
        return response.status == 200 and data == {'protocol': 'orbit-collaboration/v1', 'status': 'ok'}
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--host', required=True, help='Explicit LAN interface IP')
    parser.add_argument('--port', type=int, default=18765)
    parser.add_argument('--no-start', action='store_true')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit('Python 3.11+ required')
    if sys.platform != 'linux':
        raise SystemExit('This installer targets Linux user services')
    address = ipaddress.ip_address(args.host)
    if not address.is_private or address.is_unspecified or address.is_multicast:
        raise SystemExit('Use an explicit LAN interface address')
    if not 1024 <= args.port <= 65535:
        raise SystemExit('Use an unprivileged port in 1024..65535')
    root = args.root.expanduser().resolve()
    if any(ord(c) < 32 or c == '\\' for c in str(root)):
        raise SystemExit('Service root contains unsupported control characters')
    source = Path(__file__).resolve().parent
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    for name, digest in manifest['files'].items():
        path = source / name
        if not path.resolve().is_relative_to(source) or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise SystemExit('Package checksum mismatch: ' + name)
    units = Path.home() / '.config/systemd/user'
    target = units / 'orbit-knowledge.service'
    if target.exists() and 'ORBIT LAN project knowledge service' not in target.read_text(encoding='utf-8'):
        raise SystemExit('Refusing to replace an unrelated user unit')
    os.umask(0o077)
    root.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (root / 'install.lock').open('w') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        install(args, root, source, manifest, units, target)


def install(args, root, source, manifest, units, target):
    config = root / 'settings.json'
    settings = dict(host=args.host, port=args.port)
    if config.exists() and json.loads(config.read_text(encoding='utf-8')) != settings:
        raise SystemExit('Existing service settings differ; review settings before upgrading')
    release = root / 'releases' / manifest['id']
    release.parent.mkdir(exist_ok=True)
    if not release.exists():
        pending = release.with_name(release.name + '.next')
        if pending.exists():
            shutil.rmtree(pending)  # Only an incomplete package copy; never service data.
        shutil.copytree(source, pending)
        pending.rename(release)
    for name, digest in manifest['files'].items():
        if hashlib.sha256((release / name).read_bytes()).hexdigest() != digest:
            raise SystemExit('Installed release was modified: ' + name)
    environment = root / '.venv'
    if not (environment / 'bin/python').exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / 'bin/python'
    subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(release / 'requirements-server.txt')], check=True)
    data = root / 'data'
    data.mkdir(exist_ok=True, mode=0o700)
    join = data / 'join-code'
    if not join.exists():
        replace_text(join, secrets.token_urlsafe(32) + '\n')
        join.chmod(0o600)
    cert, key = root / 'server.pem', root / 'server.key'
    if not cert.exists() and not key.exists():
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-days', '825', '-subj', '/CN=ORBIT-LAN-Knowledge',
                        '-addext', 'subjectAltName=IP:' + args.host, '-keyout', str(key), '-out', str(cert)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        key.chmod(0o600)
    if not cert.is_file() or not key.is_file():
        raise SystemExit('Incomplete TLS certificate pair; existing files were preserved')
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text(encoding='utf-8'))).hexdigest()

    def quote(path):
        return '"' + str(path).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'

    unit = '''[Unit]
Description=ORBIT LAN project knowledge service
After=network.target

[Service]
Type=simple
UMask=0077
WorkingDirectory={cwd}
ExecStart={python} -m collaboration.server --folder {data} --host {host} --port {port} --cert {cert} --key {key}
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
'''.format(cwd=str(root / 'current').replace('%', '%%'), python=quote(python), data=quote(data), host=args.host, port=args.port, cert=quote(cert), key=quote(key))
    previous = (root / 'current').readlink() if (root / 'current').is_symlink() else None
    if (root / 'current').exists() and previous is None:
        raise SystemExit('Refusing to replace a non-symlink current directory')
    old_unit = target.read_text(encoding='utf-8') if target.exists() else None
    if old_unit:
        replace_text(root / 'previous-service.unit', old_unit)
    units.mkdir(parents=True, exist_ok=True)
    enabled = subprocess.run(['systemctl', '--user', 'is-enabled', 'orbit-knowledge.service'], capture_output=True).returncode == 0
    active = subprocess.run(['systemctl', '--user', 'is-active', 'orbit-knowledge.service'], capture_output=True).returncode == 0
    try:
        point(root, release)
        replace_text(target, unit)
        if not args.no_start:
            subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
            subprocess.run(['systemctl', '--user', 'enable', 'orbit-knowledge.service'], check=True)
            subprocess.run(['systemctl', '--user', 'restart', 'orbit-knowledge.service'], check=True)
            deadline = time.monotonic() + 12
            while not healthy(args.host, args.port, cert):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Service health check failed; restoring previous program')
                time.sleep(.4)
        replace_text(config, json.dumps(settings, indent=2) + '\n')
    except BaseException:
        if not args.no_start:
            subprocess.run(['systemctl', '--user', 'stop', 'orbit-knowledge.service'], check=False)
        if previous is not None:
            point(root, previous)
        else:
            (root / 'current').unlink(missing_ok=True)
        if old_unit is not None:
            replace_text(target, old_unit)
        else:
            target.unlink(missing_ok=True)
        if not args.no_start:
            if not enabled:
                subprocess.run(['systemctl', '--user', 'disable', 'orbit-knowledge.service'], check=False)
            subprocess.run(['systemctl', '--user', 'daemon-reload'], check=False)
            if active:
                subprocess.run(['systemctl', '--user', 'start', 'orbit-knowledge.service'], check=False)
        raise
    print(json.dumps(dict(url='https://' + args.host + ':' + str(args.port), sha256=fingerprint,
                         join_code_file=str(join), service='orbit-knowledge.service', data=str(data), release=manifest['id'],
                         health='not-started' if args.no_start else 'ok'), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
