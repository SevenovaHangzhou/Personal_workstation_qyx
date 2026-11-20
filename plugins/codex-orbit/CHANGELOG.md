# Codex 插件版本说明

## 0.3.2 · 2026-11-20

- 增加 npm 全局安装和 npx 入口 `orbit-codex install`，可直接从插件 Git 分支安装。自动检测 Python 3.11+，支持指定解释器并传递原安装器参数。
- npm 包按准确文件清单携带插件和必要共享代码，不依赖完整桌面仓库；无第三方 npm 运行依赖、无自动安装钩子，安装与升级继续复用原有配置保留、MCP 自检和回退流程。
- 新增 npm 使用、升级、卸载和排查说明。当前仅提供 Git / 本地 tarball 分发，尚未发布 npm registry；Windows 仍待实机验证。

验证：46 项 Python 测试、7 项 Node 检查通过；已验证 npm tarball、全局命令、npx、配置保留、错误退出码，以及删除 npm 包和缓存后插件仍可启动。Linux 另在隔离 Codex 配置中实测 npm 命令安装、重复升级、版本读回和卸载，全程未写入实际 Linear。

## 0.3.1 · 2026-11-18

这次更新将本地 0.3.0 的任务聊天卡片纳入插件维护分支，并简化安装与升级。

- 拉取 My Issues 后，依据宿主提供的清单关联唯一同名聊天；重名或失效目录列为待核对，无聊天任务通过原生卡片选择项目后再创建。共提供 15 个 MCP 工具。
- Linux 使用 `install.sh`，Windows 使用 `install.cmd` / `install.ps1`。完整 ZIP 根目录提供入口；源码入口位于 `plugins/codex-orbit/`。
- 自动准备插件专用 Python 环境和 PyQt5，通过 Codex 官方 marketplace 命令安装，无需 sudo。已有环境可复用，无界面使用可加 `--headless`。
- 升级继承本机目录配置，保持任务与凭据数据；安装前做 MCP 握手和工具发现，失败保留或恢复旧分发，并尝试恢复 Codex 注册。
- 通过 JSON 核对 Codex 的实际版本、安装和启用状态；支持安装锁、只读预览及目标目录检查。

### 安装与升级

需要支持插件的 Codex CLI、Python 3.11+。在源码根目录运行：

```sh
# Linux
sh plugins/codex-orbit/install.sh
```

```powershell
# Windows
.\plugins\codex-orbit\install.cmd
```

重复运行即可升级。默认独立环境首次需要联网下载 PyQt5；已有 PyQt5 可加 `--use-existing-python`，仅核心 MCP 可加 `--headless`。完成后新建 Codex 聊天，说“打开星序设置”，在本机卡片内配置 Linear 连接。

详细参数、卸载和排查见[安装与使用](README.md)。当前源码依赖共享模块，需由安装器生成自包含分发，不能直接将未打包源码当作可运行的 Git marketplace。

### 验证与边界

45 项自动测试通过。Linux 使用 Codex CLI 0.169.0 在隔离数据空间实测首次安装、重复升级、配置保留、缓存中 MCP 启动、原生设置卡片及卸载；测试未写入实际 Linear。Windows 已提供源码和入口，尚未实机验证。

结果回传仍默认保存草稿，经授权后只追加原 issue 评论，不自动修改状态、负责人或父子关系。API Key 不进入安装参数、仓库或分发包。

## 0.2.0

提供 Linear 任务领取、Codex 项目聊天派发、结果草稿与回传，以及原生首次设置卡片。插件独立于星序桌面运行，使用独立本地数据空间。
