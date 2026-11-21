# Codex 插件实现

`plugins/codex-orbit` 将星序的 Linear 链路封装为 Codex 本地插件。桌面 Yomk 应用和现有 DS Bot MCP 不受影响。插件调用现有 `linear_support` 与设备凭据实现，不启动 Qt/Yomk 桌面事件循环。

0.3.0 增加任务聊天卡片：`sync_issue_chats` 绑定本机同名聊天，`open_chat_card` 展示待建任务与项目选择，`get_chat_plan` 读取用户选择；工具总数 15，插件清单升级为 Agent Plugins v1。

0.2.0 增加原生设置卡片：插件 `onboardingSkill` 引导调用 `open_settings`，首次业务工具缺少凭据时也会尝试打开一次。MCP 初始化和工具发现不弹窗；已配置不自动弹窗。独立进程运行 PyQt5 卡片，QLocalServer 加进程锁避免多开，并将重复请求转为窗口激活。卡片通过只读 viewer 查询测试账号，验证成功后保存插件自己的凭据及可选默认目录。网络检查在线程中运行，密钥不通过 MCP、命令行或 IPC 传递；原始错误不展示。独立卡片关闭后不影响 Codex。

凭据优先级：`LINEAR_API_KEY` → 插件本地凭据 → 显式配置的桌面版来源。移除插件凭据会恢复共享来源，桌面版密钥不改动。配置卡片不读写 issue、聊天或任务状态。设置保存后，同一 MCP 进程下一次工具调用立即读取。卡片依赖独立 `requirements-ui.txt`，核心 MCP 无 PyQt5 时仍可在终端设置后使用。

运行契约：

1. `list_my_issues` 仅返回当前账号未结束的事项，附已领取任务 ID。
2. `start_task` 再次核对 issue UUID、当前账号和负责人，关联已有目录。首次领取不改远端或执行仓库命令；已有任务更换目录/会话须明确 `replace_context=true`。工作由当前 Codex 按用户要求继续。
   请求专用聊天时，宿主 `list_projects` → `prepare_chat` 以规范路径匹配本机项目 → 宿主 `create_thread` → `bind_project_chat` 保存真实 ID。派发预留写入数据库，重复调用不会再返回创建指令；结果未知时先人工／宿主查找原聊天并恢复绑定。新仓库没有注册时明确返回 `project_registration_required`，不写 Codex 私有数据库。此自动流程依赖桌面宿主的 `codex-app-tools`；Codex CLI 仍可使用当前会话模式。
3. `draft_result` 保存不可变结果及当时的 issue、工作目录和会话快照。相同任务、上下文版本和结果去重。
4. `preview_result` 返回目标、完整正文和哈希；`publish_result` 要求该哈希及本次回传授权。正文哈希用于内容一致性，不是认证机制。
5. 回传前重查 issue 和账号，使用本地持久化 UUID 创建评论，再调用现有 `ensure_comment` 读回比对正文与归属。超时、进程退出或重试不会换 ID；远端评论被他人修改时不会覆盖。

插件数据放在独立 `OrbitCodex/codex.sqlite3`，SQLite 事务保护多会话本地修改。网络请求在事务外执行。发布中断可能远端已成功、本地仍为草稿，下一次按原 ID 核对后补记。

`scripts/build_codex_plugin.py` 用显式文件名单打包共享 Python 源码，排除桌面依赖、真实任务、API Key、缓存和历史日志。分发目录本身就是一个可注册的本地 marketplace。

### 安装链路（0.3.1）

官方入口使用 `codex plugin marketplace add <目录或 Git 来源>`，随后通过插件浏览器或 `codex plugin add orbit@sevenova` 安装。源码依赖的共享模块在 Codex 缓存之外，因此先生成自包含分发；不将整个桌面项目塞入插件缓存，也不声称未打包源码能直接从 Git 安装。

分发 ZIP 根目录提供 `install.sh` / `install.cmd` / `install.ps1` / `INSTALL.md`，调用同一安装器。源码入口位于 `plugins/codex-orbit/`。默认创建插件专用 venv 并安装 PyQt5；`--headless` 仅安装 stdlib 核心，`--use-existing-python` 复用已具备图形依赖的解释器。安装器使用 JSON 接口读取 Codex 状态，检查实际版本、installed 与 enabled，避免按英文终端输出误判成功。仅显式 `--enable-plugins` 才修改旧客户端功能标记。

顺序：校验源与目标目录 → 检查 Codex 能力 → 准备新分发与独立环境 → 继承旧 `settings.json` → 配置解释器 → 临时数据空间内做 MCP 握手及工具发现 → 原目录改名保留 → 切换新目录 → Codex 注册/安装/核验。成功后删除临时旧分发；失败恢复旧文件并尝试恢复原注册。运行中的 Codex 会话需要新建聊天才能加载新缓存。依赖环境保存在相邻目录，与版本切换分开；安装失败可重试。

默认目标沿用 `Sevenova/OrbitCodexPlugin/marketplace`，避免替换上层目录而丢失原配置。并发由进程文件锁排他；拒绝覆盖非 ORBIT 目录、源码、用户根目录及指定数据目录。ZIP 解压目录始终复制到稳定目标，删除下载目录不会破坏安装。安装器只继承目录配置，不读取/复制密钥或任务数据库。MCP 自检清除凭据环境变量，在隔离临时目录中运行，无 Linear 请求。

不提供 DeepSeek 适配、附件上传或公共目录发布。旧 `orbit_mcp.py` 的大范围 `sync_once` 不暴露给 Codex 插件，结果回传只作用于准确的原 issue。定时摘要与已验证任务收尾分别使用下述独立流程。

协议与格式参考：[OpenAI 插件打包](https://developers.openai.com/plugins/build/plugins)、[本地 MCP 启动规范](https://developers.openai.com/plugins/deploy/desktop-only)。

### 拉取后的任务聊天卡片

拉取后由宿主提供完整聊天清单，`sync_issue_chats` 自动绑定唯一同名聊天，保留已有绑定；重名和失效目录进入待核对列表。`open_chat_card` 展示待创建任务及本机项目选择，`get_chat_plan` 返回用户选择。创建仍通过 Codex 的原生 `create_thread`，随后绑定真实聊天 ID。卡片不直接控制宿主，不默认开展开发，也不修改 Linear 状态。

### npm 入口（0.3.2）

仓库根目录的 `package.json` 提供 `@sevenova/orbit-codex` 包及 `orbit-codex` 命令。`files` 是准确文件清单：包含 Node 启动器、Python 构建脚本、完整插件及允许的共享模块，排除设备设置、密钥、桌面 runtime 与历史数据。包不使用 npm install/prepare 生命周期脚本，没有 npm 运行依赖。`private: true` 表明目前仅从 Git / 本地 tarball 安装，防止意外发布到公共 registry。

`npm install -g <Git URL#ref>` 安装命令；`orbit-codex install` 检测可用 Python 3.11+（Windows 优先尝试 py -3），用参数数组启动原 Python 安装器，不拼 shell 命令。用户指定 `--python` 失败时明确报错，不偷偷切换解释器。安装退出码向外传播。npx 与全局安装都使用原安装器的固定目标，因此可安全清理 npm 包缓存；不向 npm 或 GitHub 传递 Linear 密钥。

npm 模式仍要求本机具备 Python 和 Codex；它提供分发与命令入口，不将应用改为 Node 或网页。打包通过 `npm pack`，维护者可用 `npm test` 检查命令入口，用 Python 测试验证 tarball 清单和安装链路。官方依据：[npm bin 与文件清单](https://docs.npmjs.com/cli/v11/configuring-npm/package-json/)、[Git 来源安装](https://docs.npmjs.com/cli/v11/commands/npm-install/)、[npm exec / npx](https://docs.npmjs.com/cli/v11/commands/npm-exec/)。

## LAN 工作日志（0.4.0）

插件复用 `evaluation.mesh` 的 UDP 发现和只读授权协议，`evaluation.shared_store` 增加 TLS/TCP 直接文件流与不可变本地快照。`orbit_plugin.journals` 只从结果账本生成结构化文件；`orbit_plugin.lan` 在单独、可停用的后台进程提供服务，stdio MCP 重连不会重复占用端口。默认关闭，不以同网段或设备名称授予权限。

`open_lan` 打开本机原生卡片，邀请只在卡片中处理；`lan_status` 返回发现与清单元数据，`collect_logs` 从已有授权联系人增量拉取，`read_shared_log` 读取当前已验证快照并返回历史收集时间。公网地址、指纹不符、任意路径、越界文件、未授权读取均拒绝。没有服务器端写文件或执行命令接口。

[日志统计标准](../plugins/codex-orbit/日志统计标准.md)是统计与共享格式约定。结果草稿导出失败不丢失原 SQLite 记录，卡片重新导出可修复。旧评估端仍可读取授权结果记录和完整 Markdown 报告，无需新建一套管理员身份系统。

## 每日定时收集（0.5.0）

安装器在 Codex 注册成功后，通过稳定分发路径自动注册当前用户的系统任务。默认当地 00:00；升级保留时间与停用状态。Linux 使用 `systemd --user` + Persistent timer，Windows 使用 InteractiveToken 每日任务 + StartWhenAvailable，均不需要密码。`--no-codex` 或 `--no-schedule` 不注册。

收集器用代码读取明确绑定的聊天用户／助手消息、筛选周期和去重，只把必要文本交给当前系统 Codex 做摘要。摘要子进程关闭插件、MCP、网络检索和执行工具，复用登录，不选择其他模型。原始聊天不写入 LAN 共享目录；摘要缓存是内容寻址的私有文件。

`daily/state.json` 记录实际截止时间、未完成周期和窗口，调整 HH:MM 后从上次截止时间继续，不留下空档；任务／周期已成功结果不重复生成。`daily/reports/日期-HHMM/任务UUID` 保存 Markdown 与 JSON，LAN 根据当前共享任务清单导出。系统关机不能运行；用户会话恢复后补跑，Windows 仍需实机验证。

调度依据：[systemd.timer](https://www.freedesktop.org/software/systemd/man/256/systemd.timer.html)、[Windows 每日任务 XML](https://learn.microsoft.com/en-us/windows/win32/taskschd/daily-trigger-example--xml-)。

## 已验证任务收尾（0.6.0）

`complete_task` 使用最新的不可变 `validated` 结果与内容哈希，要求完整范围已完成、验证证据非空、剩余／阻碍／下一步为空，并核对当前唯一会话绑定。通过 Linear API 分页读取所有子孙 issue（包括归档项），核对账号、团队、负责人、任务范围和父级自动关闭规则；团队只有一个完成状态时自动选择，否则由用户指定。

写入前以结果 UUID 保存 `task_completions` 操作和远端快照，只修改 `stateId`。再独立读回 issue、归属、范围及子任务状态，成功后返回 `issue_verified` 与绑定会话的归档请求。网络结果不明确时保留 prepared，重试先读远端；已核验的 issue 若被重新打开则停止旧收尾，不覆盖用户调整。

`orbit-work` 随后调用宿主 `set_thread_archived`，从 `list_archived_threads` 核对真实会话 ID 后调用 `confirm_chat_archived` 记录 archived。该回执代表宿主核验，不是插件自行访问 Codex 数据库。`list_completions` 仅返回历史元数据，不能直接取得归档令牌；继续收尾须先重新核对远端。缺少宿主能力时保留待归档，不声称成功。每日收集器不调用此流程。

API 依据：[Linear issueUpdate](https://linear.app/developers/graphql)、[父子状态自动规则](https://linear.app/docs/parent-and-sub-issues)、[官方 GraphQL schema](https://github.com/linear/linear/blob/master/packages/sdk/src/schema.graphql)。会话使用当前 Codex 桌面宿主工具，不直接使用 app-server 的文件归档或私有数据库。
