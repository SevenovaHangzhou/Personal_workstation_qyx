# 星序 / ORBIT · Codex 插件 0.6.0

Linear My Issues → Codex 专用聊天开发 → 结果草稿 → 确认后回传原 issue。

包含设置卡片、任务聊天卡片和 23 个 MCP 工具。无需启动星序桌面版。支持 Linux / Windows，需先安装支持插件的 Codex CLI 和 Python 3.11+。

[本次更新说明](CHANGELOG.md)

## npm 安装（Linux / Windows）

需要 Node.js 18+（建议当前 LTS）、npm、Git，以及 Codex CLI 和 Python 3.11+。当前通过 GitHub 分支分发，无需 npm 账号：

```sh
npm install -g "git+https://github.com/SevenovaHangzhou/Personal_workstation_qyx.git#codex/orbit-plugin"
orbit-codex install
```

不想全局安装时，直接用 npx：

```sh
npx --yes --package="git+https://github.com/SevenovaHangzhou/Personal_workstation_qyx.git#codex/orbit-plugin" orbit-codex install
```

`npm install` 只安装 `orbit-codex` 命令；运行 `install` 才准备 Python 环境、下载图形依赖并配置 Codex。两种方式都把插件复制到稳定目录，npm 更新、卸载或 npx 缓存清理不影响已安装的插件与数据。API Key 在随后打开的本机设置卡片填写。

升级：重新执行全局安装命令，再运行 `orbit-codex install`。需要固定版本时，把 URL 的 `#codex/orbit-plugin` 换成已核对的完整提交 SHA。

所有下列安装参数都可追加，例如：

```sh
orbit-codex install --dry-run
orbit-codex install --headless
orbit-codex install --python "/absolute/path/to/python" --use-existing-python
```

`orbit-codex --version` 查看版本，`orbit-codex --help` 查看用法。若提示找不到命令，请把 npm 全局命令目录加入 PATH，或使用 npx；遇到全局目录权限问题时也可使用 npx，无需为插件配置使用 sudo。

包标识为 `@sevenova/orbit-codex`，**尚未发布到 npm registry**，目前使用上述 Git 地址，不使用 `npm install -g @sevenova/orbit-codex`。源码包的 `private: true` 防止误发布。npm 安装不自动运行 Python 或修改 Codex，也没有 npm 第三方运行依赖。

卸载 npm 命令：`npm uninstall -g @sevenova/orbit-codex`。若还要卸载 Codex 插件，使用文末的 Codex 卸载命令；本机任务和密钥仍保留。

## ZIP 安装与升级

下载完整插件 ZIP，解压后在该目录运行：

**Linux**

```sh
sh install.sh
```

**Windows**

运行 `install.cmd`，或在 PowerShell 中执行 `.\install.ps1`。`install.cmd` 仅为本次安装放行 PowerShell 脚本，不修改全局执行策略。

脚本自动准备独立 Python 环境、下载 PyQt5 与 cryptography、注册 `sevenova` marketplace 并安装 `orbit@sevenova`，无需 sudo，不修改系统 Python。完成后新建 Codex 聊天，说“打开星序设置”，在卡片内填写 Linear API Key。密钥请勿发到聊天中。

升级时运行同一命令。脚本保留本机配置和数据；下载、MCP 自检或安装失败会保留／恢复原分发目录。首次安装需要联网下载依赖，已有环境重复使用。安装后可以删除下载的 ZIP 与解压目录。

**源码安装**：在仓库根目录运行 `sh plugins/codex-orbit/install.sh`，Windows 运行 `.\plugins\codex-orbit\install.ps1`。

可选参数（追加到同一命令后）：

| 参数 | 用途 |
| --- | --- |
| `--dry-run` | 预览路径和步骤，不写文件、不下载 |
| `--headless` | 仅核心 MCP，不下载图形依赖；在终端隐藏输入密钥 |
| `--use-existing-python` | 使用已有 PyQt5 与 cryptography 的解释器，省去独立环境与下载 |
| `--python PATH` | 指定 Python 3.11+，通常作为独立环境的基础解释器 |
| `--credentials-from PATH` | 只读取星序桌面版已有凭据，不覆盖它 |
| `--data-dir PATH` | 指定插件数据目录，需为绝对路径 |
| `--codex PATH` | 指定 Codex CLI 可执行文件 |
| `--install-dir PATH` | 指定专用 marketplace 目录，需为空或已有 ORBIT 分发 |
| `--no-codex` | 仅准备安装文件与环境，稍后在 Codex 中安装 |
| `--daily-time HH:MM` | 指定每日收集时间，首次默认 00:00，升级保留已有设置 |
| `--no-schedule` | 跳过系统定时任务注册，不改变已有任务 |

默认分发目录是用户数据目录内的 `Sevenova/OrbitCodexPlugin/marketplace`，独立环境在相邻的 `marketplace-runtime`。插件任务和密钥另存于 Linux `~/.local/share/Sevenova/OrbitCodex` 或 Windows `%LOCALAPPDATA%\Sevenova\OrbitCodex`。升级不迁移这些数据。

## 标准 marketplace 安装

脚本使用 Codex 官方 marketplace 命令。若先用 `--no-codex` 准备好分发目录，也可以手动添加该目录，再在 Codex `/plugins` 中选择「星序 / ORBIT」安装：

```sh
codex plugin marketplace add /absolute/path/to/marketplace
codex plugin add orbit@sevenova
```

此手动路径不会注册系统定时任务；需要时，在本机设置卡片中打开「日报定时收集」并保存。

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

## 完成任务并归档会话

完成整个 issue 并验证通过后，Codex 自动保存 `validated` 结果，确认没有剩余事项、阻碍或下一步，再调用 `complete_task`。插件检查最新结果、唯一会话绑定、issue 范围及负责人、全部子 issue 状态和团队自动关闭规则，仅将原 issue 改为团队的完成状态，并独立读回核对。

只有核对成功，Codex 才通过宿主 `set_thread_archived` 归档该任务会话，再从已归档清单确认。失败记录保留，可通过 `list_completions` 继续；重试不会重复发表结果评论或重新关闭已被用户打开的 issue。未验证、部分完成、普通答复结束和日报摘要均不触发。

有多个完成状态时需明确选择；可能连带关闭未完成父 issue 时暂停。CLI 等缺少宿主归档能力的环境保留“issue 已完成，会话待归档”，不会改写 Codex 数据库或移动聊天文件。归档可在 Codex 恢复，不删除会话。

## 自动日报

安装器完成 Codex 安装后，自动注册每天 **00:00（本机时间，午夜 12 点）** 的日志收集任务。Linux 使用用户级 systemd timer，Windows 使用当前用户的任务计划程序；无需保持 Codex 窗口开启。升级保留已设置的时间和停用状态，同一数据空间只有一个定时任务。

只读取插件任务明确绑定的 Codex 聊天，并整理该周期的结果、测试证据与阻碍。收集、分组、去重与文件生成由代码完成，文本摘要通过系统当前 Codex 配置执行；不更改登录或模型，不上传原始聊天到 LAN，不自动评论或修改 Linear。

说“打开星序日报定时设置”，或在连接设置中点击「日报定时收集」，可更改时间、停用或检查补跑。安装时也可指定 `orbit-codex install --daily-time 23:30`。关机时无法执行；恢复已登录的用户会话后由系统补触发，收集器补齐漏跑日期，失败摘要下一次再试。Windows 首版只在当前用户已登录时运行，不收集登录密码。

日报位于插件数据目录 `daily/reports/周期结束日期-HHMM/任务UUID.md` 与 `.json`，保存实际时间区间、消息数、摘要、证据和待处理原因。默认 00:00 的日报归入刚结束的一天。没有绑定、记录缺失和没有新消息分别标记，不能当作零工作量。日报按任务拆分，LAN 仅公开当前勾选任务的摘要文件。

`--no-schedule` 跳过系统任务注册，`--no-codex` 仅准备分发也不注册；仅执行 npm 安装尚未运行 `orbit-codex install` 时不会修改系统。卸载 Codex 插件前请先在卡片停用定时任务。

## 局域网日志与管理员读取

在 Codex 中说“打开星序局域网日志”，或从设置卡片进入。先保存显示名、勾选要共享的任务并启用共享，再为每位管理员创建独立邀请。共享默认关闭，发现设备不会自动获得读取权限；邀请含只读令牌，请通过私密渠道交给管理员，不发到聊天或 issue。

管理员在「管理员 · 日志库」选择已发现的设备，导入邀请并核对证书指纹，再点击「收集副本」。文件通过 TLS/TCP 直传到本机 Shared Store，只下载变更内容。员工离线后仍可查看最后成功收集的版本，界面显示收集时间。需要网络允许 UDP 45874 和卡片显示的 TCP 端口；广播不可达时可手填局域网地址。

每次保存结果自动导出 Markdown／JSON 工作日志；只共享勾选任务的历史和后续结构化结果，包含未发布草稿。原始聊天、密钥和仓库文件不共享。撤销授权阻止后续读取，无法远程收回旧副本。关闭卡片后已启用的节点继续运行；停用请取消「启用」并保存。更新插件前请先停用，升级后再启用，让后台节点加载新代码。

[日志统计标准](日志统计标准.md)规定字段、去重、时间窗口、进度口径和共享范围。目录：`journals/` 是个人结果日志，`shared-store/设备UUID/快照版本/` 是管理员副本。已有评估端兼容读取记录和 Markdown 报告；新版文件接口使用 `orbit-shared-store/v1`。

LAN 服务需要 `cryptography`，默认安装器会补全。`--headless` 用户若需后台共享，先在所用 Python 环境安装 `plugins/codex-orbit/requirements-lan.txt`，再通过有桌面的卡片明确配置授权。Linux 已做本机双节点传输测试，Windows 使用相同协议和原生入口，仍需实机联调。

## 卸载与排查

```sh
codex plugin remove orbit@sevenova
codex plugin marketplace remove sevenova
```

卸载保留本机分发、数据和密钥。安装失败先检查 Codex / Python 版本与网络，再运行同一命令。安装成功后看不到工具时，新建 Codex 会话并运行 `codex plugin list --marketplace sevenova --json` 核对版本与启用状态。Windows 的源码和入口已覆盖，实际 Windows 桌面运行仍需在 Windows 验证。

从源码构建 ZIP：`python3 scripts/build_codex_plugin.py --output /absolute/new/output`，输出完整 marketplace、根目录安装入口、ZIP 和 SHA-256。
