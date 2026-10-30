"""Read-only repository inspection. Never clone, fetch, checkout or run a shell."""
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse, urlunparse


def remote_repository(location):
    if not isinstance(location, str) or not location.strip():
        raise ValueError("请填写 Git 仓库地址")
    location = location.strip()
    scp = re.fullmatch(r"git@([A-Za-z0-9.-]+):([A-Za-z0-9_./~-]+)", location)
    if scp:
        host, path = scp.groups()
        web_url = "https://" + host.lower() + "/" + path.removesuffix(".git")
    else:
        parsed = urlparse(location)
        if parsed.scheme not in ("https", "ssh") or not parsed.hostname or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("支持 HTTPS、ssh:// 或 git@host:owner/repo.git 地址，请勿包含密码或 token")
        if parsed.username and not (parsed.scheme == "ssh" and parsed.username == "git"):
            raise ValueError("仓库地址不能包含账号凭证")
        if not re.fullmatch(r"[A-Za-z0-9.-]+", parsed.hostname) or not re.fullmatch(r"/[A-Za-z0-9_./~-]+", parsed.path):
            raise ValueError("仓库地址格式不正确")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("仓库端口格式不正确") from exc
        host, path = parsed.hostname.lower(), parsed.path.lstrip("/")
        netloc = host + (":" + str(port) if parsed.scheme == "https" and port else "")
        web_url = urlunparse(("https", netloc, "/" + path.removesuffix(".git"), "", "", ""))
    if any(part in ("", ".", "..") for part in path.split("/")) or len(path.split("/")) < 2:
        raise ValueError("请填写完整的 owner/repository 路径")
    return {"kind": "remote", "location": location, "name": path.rstrip("/").split("/")[-1].removesuffix(".git"), "web_url": web_url}


def inspect_local(location):
    if not isinstance(location, str) or not location.strip():
        raise ValueError("请选择本地 Git 仓库文件夹")
    path = Path(location).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("仓库目录不存在")
    # Ignore inherited repository selectors so inspection targets the chosen folder.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    def git(*args):
        try:
            return subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(path), *args],
                                  env=env, text=True, capture_output=True, timeout=5, check=True).stdout.strip()
        except FileNotFoundError as exc:
            raise ValueError("系统未安装 Git") from exc
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ValueError("无法读取所选 Git 仓库，请检查目录与 Git 访问权限") from exc
    bare = git("rev-parse", "--is-bare-repository") == "true"
    root = Path(git("rev-parse", "--absolute-git-dir" if bare else "--show-toplevel")).resolve()
    return {"kind": "local", "location": str(root), "name": root.name}


def validate_repository(repository):
    if not isinstance(repository, dict) or repository.get("kind") not in ("local", "remote"):
        raise ValueError("仓库关联格式无效")
    if not isinstance(repository.get("name"), str) or not isinstance(repository.get("location"), str):
        raise ValueError("仓库关联格式无效")
    if repository["kind"] == "local":
        if not Path(repository["location"]).is_absolute():
            raise ValueError("本地仓库须使用绝对路径")
    else:
        expected = remote_repository(repository["location"])
        if repository.get("web_url") != expected["web_url"]:
            raise ValueError("远程仓库链接无效")
