"""Device-local credentials, kept outside task JSON and the application bundle."""
import hashlib
import os
import stat
import tempfile
from pathlib import Path


class CredentialStore:
    def __init__(self, data_directory):
        self.directory = Path(data_directory) / ".credentials"
        self.path = self.directory / "linear.key"
        self.service = "momo-linear-" + hashlib.sha256(str(Path(data_directory).resolve()).encode()).hexdigest()[:20]

    def keyring(self):
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            return None
        try:
            import keyring
            selected = keyring.get_keyring()
            if selected.priority > 0 and type(selected).__module__ in {"keyring.backends.libsecret", "keyring.backends.SecretService"}:
                return keyring
        except Exception:
            pass
        return None

    def load(self):
        backend = self.keyring()
        if backend:
            try:
                value = backend.get_password(self.service, "linear")
                if value:
                    return value, "keyring"
            except Exception:
                pass
        if not self.path.exists() and not self.path.is_symlink():
            return "", ""
        if self.directory.is_symlink():
            raise OSError("凭据目录不能是符号链接")
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                    raise OSError("凭据文件须仅允许当前用户访问")
                value = stream.read(4097).strip()
                if not value or len(value) > 4096 or any(c.isspace() for c in value):
                    raise OSError("本机凭据格式无效，请重新配置")
                return value, "file"
        except UnicodeError:
            raise OSError("本机凭据格式无效，请重新配置") from None

    def save(self, value):
        if not value or len(value) > 4096 or any(c.isspace() for c in value):
            raise ValueError("密钥格式无效")
        backend = self.keyring()
        if backend:
            try:
                backend.set_password(self.service, "linear", value)
                self.path.unlink(missing_ok=True)
                return "keyring"
            except Exception:
                pass
        if self.directory.is_symlink() or self.path.is_symlink():
            raise OSError("凭据路径不能是符号链接")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        fd, temporary = tempfile.mkstemp(prefix="linear-", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return "file"

    def clear(self):
        if self.directory.is_symlink():
            raise OSError("凭据目录不能是符号链接")
        backend = self.keyring()
        if backend:
            try:
                if backend.get_password(self.service, "linear"):
                    backend.delete_password(self.service, "linear")
            except Exception:
                raise OSError("无法移除系统密钥环中的凭据，请检查密钥环是否已解锁") from None
        self.path.unlink(missing_ok=True)
