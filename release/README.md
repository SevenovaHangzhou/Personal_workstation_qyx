# 星序 ORBIT 发布

当前版本：**0.9.0** · Linux x86_64 · Ubuntu 22.04 构建（glibc 2.35+）。

[下载 0.9.0 应用](https://github.com/SevenovaHangzhou/Personal_workstation_qyx/releases/tag/v0.9.0)

- `sevenova-orbit_0.9.0_amd64.deb`：安装后从应用菜单启动，命令为 `orbit`。
- `orbit-0.9.0-linux-x86_64.tar.gz`：解压后运行 `orbit/orbit`，无需 Python 或虚拟环境。
- [SHA256SUMS](0.9.0/SHA256SUMS)、[构建清单](0.9.0/manifest.json)、[版本说明](0.9.0/RELEASE_NOTES.md)。

安装示例：

```bash
sudo apt install ./sevenova-orbit_0.9.0_amd64.deb
```

两个包复用现有用户数据 `~/.local/share/MomoLocal/Momo`；升级前先从旧版本托盘退出。
Codex CLI 与 Git 使用本机安装和配置，包中不包含聊天、课程生成记录、API key 或登录凭据。
卸载 `.deb` 不删除用户数据。启动日志写入 `~/.local/state/orbit/`（或 `XDG_STATE_HOME`），不写安装目录。

离线自检：`QT_QPA_PLATFORM=offscreen orbit --self-test`。仅使用临时数据，不调用模型。

## 本地构建

使用已有应用依赖和 Python 3.12，另安装 [构建依赖](../packaging/requirements-build.txt)：

```bash
.venv/bin/python -m pip install -r packaging/requirements-release.txt -r packaging/requirements-build.txt
.venv/bin/python scripts/build_release.py
```

还需要 `dpkg-deb` 和 Git。构建脚本不会下载依赖，会复用当前解释器的本地包；临时 ASCII 导入别名兼容中文工程目录。输出位于 `release/0.9.0/`，构建日志与截图位于 `build/release/`。已有发布文件不会被覆盖，可用 `--output` 指定新的空目录。

构建验证覆盖冻结应用、便携包解压、Debian 包解包后的启动与隔离自检；桌面托盘宿主和不同发行版仍需实机验证。
安装包上传 GitHub Release，源码库保留说明与校验清单，避免二进制反复进入 Git 历史。

打包资源布局参考 [PyInstaller 官方说明](https://pyinstaller.org/en/stable/runtime-information.html)。
