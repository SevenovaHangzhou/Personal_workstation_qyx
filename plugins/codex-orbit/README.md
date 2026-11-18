# 星序 / ORBIT · Codex 插件 0.3.1

Linear My Issues → Codex 专用聊天开发 → 结果草稿 → 确认后回传原 issue。

包含设置卡片、任务聊天卡片和 15 个 MCP 工具。无需启动星序桌面版。支持 Linux / Windows，需先安装支持插件的 Codex CLI 和 Python 3.11+。

[本次更新说明](CHANGELOG.md)

## 安装与升级

下载完整插件 ZIP，解压后在该目录运行：

**Linux**

```sh
sh install.sh
```

**Windows**

运行 `install.cmd`，或在 PowerShell 中执行 `.\install.ps1`。`install.cmd` 仅为本次安装放行 PowerShell 脚本，不修改全局执行策略。

脚本自动准备独立 Python 环境、下载 PyQt5、注册 `sevenova` marketplace 并安装 `orbit@sevenova`，无需 sudo，不修改系统 Python。完成后新建 Codex 聊天，说“打开星序设置”，在卡片内填写 Linear API Key。密钥请勿发到聊天中。

升级时运行同一命令。脚本保留本机配置和数据；下载、MCP 自检或安装失败会保留／恢复原分发目录。首次安装需要联网下载依赖，已有环境重复使用。安装后可以删除下载的 ZIP 与解压目录。

**源码安装**：在仓库根目录运行 `sh plugins/codex-orbit/install.sh`，Windows 运行 `.\plugins\codex-orbit\install.ps1`。

可选参数（追加到同一命令后）：

| 参数 | 用途 |
| --- | --- |
| `--dry-run` | 预览路径和步骤，不写文件、不下载 |
| `--headless` | 仅核心 MCP，不下载图形依赖；在终端隐藏输入密钥 |
| `--use-existing-python` | 使用已有 PyQt5 的解释器，省去独立环境与下载 |
| `--python PATH` | 指定 Python 3.11+，通常作为独立环境的基础解释器 |
| `--credentials-from PATH` | 只读取星序桌面版已有凭据，不覆盖它 |
| `--data-dir PATH` | 指定插件数据目录，需为绝对路径 |
| `--codex PATH` | 指定 Codex CLI 可执行文件 |
| `--install-dir PATH` | 指定专用 marketplace 目录，需为空或已有 ORBIT 分发 |
| `--no-codex` | 仅准备安装文件与环境，稍后在 Codex 中安装 |

默认分发目录是用户数据目录内的 `Sevenova/OrbitCodexPlugin/marketplace`，独立环境在相邻的 `marketplace-runtime`。插件任务和密钥另存于 Linux `~/.local/share/Sevenova/OrbitCodex` 或 Windows `%LOCALAPPDATA%\Sevenova\OrbitCodex`。升级不迁移这些数据。

## 标准 marketplace 安装

脚本使用 Codex 官方 marketplace 命令。若先用 `--no-codex` 准备好分发目录，也可以手动添加该目录，再在 Codex `/plugins` 中选择「星序 / ORBIT」安装：

```sh
codex plugin marketplace add /absolute/path/to/marketplace
codex plugin add orbit@sevenova
```

官方还支持 Git 仓库来源。当前源码分支的插件依赖项目共享模块，**需要先运行上述安装脚本打包**，不能直接把未打包源码当作可运行的 marketplace。插件尚未发布到公共插件目录。[官方规范](https://developers.openai.com/plugins/build/plugins)

## 使用

新建 Codex 聊天，使用 `$orbit-work` 或直接说：

- “拉取我的 Linear 任务。”
- “在当前项目处理 ENG-123。”
- “为 ENG-123 在对应项目中新建专用聊天。”
- “整理开发结果和测试证据，回传到原 issue。”

拉取后整理宿主提供的聊天清单：唯一同名聊天自动绑定，重名或失效目录待核对；无聊天任务通过卡片选择项目后再创建。新仓库需先加入 Codex 本地项目。专用聊天依赖桌面宿主的 `codex-app-tools`，CLI 可在当前会话开发。结果默认保存草稿，取得回传授权才发布评论。

设置卡片可测试账号、修改插件密钥和默认工作目录。凭据优先级为 `LINEAR_API_KEY` → 插件凭据 → 指定桌面版凭据；保存后下一次工具调用生效。`--headless` 用户可运行：

```sh
python3 /absolute/path/to/marketplace/plugins/codex-orbit/scripts/mcp.py --setup
```

## 卸载与排查

```sh
codex plugin remove orbit@sevenova
codex plugin marketplace remove sevenova
```

卸载保留本机分发、数据和密钥。安装失败先检查 Codex / Python 版本与网络，再运行同一命令。安装成功后看不到工具时，新建 Codex 会话并运行 `codex plugin list --marketplace sevenova --json` 核对版本与启用状态。Windows 的源码和入口已覆盖，实际 Windows 桌面运行仍需在 Windows 验证。

从源码构建 ZIP：`python3 scripts/build_codex_plugin.py --output /absolute/new/output`，输出完整 marketplace、根目录安装入口、ZIP 和 SHA-256。
