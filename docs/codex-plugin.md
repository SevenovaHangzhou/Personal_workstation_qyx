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

本次不提供 DeepSeek 适配、后台定时调度、任务状态自动转换、附件上传或公共目录发布。旧 `orbit_mcp.py` 的大范围 `sync_once` 不暴露给 Codex 插件，结果回传只作用于准确的原 issue。

协议与格式参考：[OpenAI 插件打包](https://developers.openai.com/plugins/build/plugins)、[本地 MCP 启动规范](https://developers.openai.com/plugins/deploy/desktop-only)。

### 拉取后的任务聊天卡片

拉取后由宿主提供完整聊天清单，`sync_issue_chats` 自动绑定唯一同名聊天，保留已有绑定；重名和失效目录进入待核对列表。`open_chat_card` 展示待创建任务及本机项目选择，`get_chat_plan` 返回用户选择。创建仍通过 Codex 的原生 `create_thread`，随后绑定真实聊天 ID。卡片不直接控制宿主，不默认开展开发，也不修改 Linear 状态。
