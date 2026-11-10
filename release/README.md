# 星序 ORBIT 发布

当前版本：**0.9.8**。

[下载安装包](https://github.com/SevenovaHangzhou/Personal_workstation_qyx/releases/latest) · [版本说明](0.9.8/RELEASE_NOTES.md)

| 系统 | 安装版 | 便携版 |
| --- | --- | --- |
| Linux x86_64，glibc 2.35+ | `sevenova-orbit_0.9.8_amd64.deb` | `orbit-0.9.8-linux-x86_64.tar.gz` |
| Windows 10 / 11 x64 | `orbit-0.9.8-windows-x86_64-setup.exe` | `orbit-0.9.8-windows-x86_64.zip` |

Linux 安装：`sudo apt install ./sevenova-orbit_0.9.8_amd64.deb`；便携包解压后运行 `orbit/orbit`。
Windows 双击安装包，或解压便携包运行 `orbit.exe`。安装无需管理员权限，可选桌面快捷方式。

均内置 Python 环境。Codex CLI 与 Git 沿用本机配置；升级前从旧版托盘退出。
数据位于 Linux `~/.local/share/Sevenova/Orbit`、Windows `%LOCALAPPDATA%/Sevenova/Orbit`；旧 Linux 数据自动迁移并保留兼容路径。
包中不包含用户数据、聊天或登录凭据。卸载不删除用户数据。

[OTA 更新](../OTA.md)在完整退出后切换，下次启动生效；原安装保留。Windows 凭据由 DPAPI 按当前系统用户加密，安装包尚未商业签名。

## 构建

Linux（Python 3.12、Git、dpkg-deb）：

```bash
python -m pip install -r packaging/requirements-release.txt -r packaging/requirements-build.txt
python scripts/build_release.py
```

Windows x64（Python 3.12、Git、Inno Setup 6）：

```powershell
python -m pip install -r requirements.txt pyinstaller==6.22.3
python scripts/build_windows.py
```

输出位于 `release/0.9.8/`。构建脚本验证冻结应用、解压后启动及隔离数据自检；Windows 还验证静默安装和卸载。GitHub Actions 在两个原生系统构建并校验 hash 后发布，源码库仅保留说明与校验清单。
