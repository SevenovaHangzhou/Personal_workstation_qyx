# 星序 / ORBIT · Codex 插件 0.2.0

Linear My Issues → 当前 Codex 会话开发 → 结果草稿 → 回传原 issue。

原生本地插件，包含任务工作流、首次设置引导和 12 个 MCP 工具。无需启动星序桌面版，由 Codex 完成开发和总结。需要支持插件的新版 Codex、Python 3.11+；设置卡片使用 PyQt5，Linux/Windows 共用同一源码。

## 安装

将完整 ZIP 解压到固定位置。在该目录运行（不要只移动 `plugins/`）：

```bash
python3 -m pip install -r plugins/codex-orbit/requirements-ui.txt
python3 plugins/codex-orbit/scripts/configure.py
codex plugin marketplace add .
codex plugin add orbit@sevenova
```

`configure.py` 必须用准备给插件使用的 Python 3.11+ 运行；Windows 可以将 `python3` 换成 `py -3.11` 或实际 Python 路径。它把该解释器写入 MCP 启动配置。已有 PyQt5 时可省略依赖安装。

在插件中运行首次设置，或对 Codex 说“打开星序设置”，即可显示原生小卡片。卡片中填写 Linear API Key、测试连接、确认账号并保存，可选默认工作目录。首次实际拉取任务缺少密钥时，也会尝试打开一次；已配置后不反复弹出。卡片独立运行，保存后立即生效，Codex 可继续处理其他事情。重复打开会激活已有卡片。

密钥不进入聊天、MCP 参数、命令参数或日志。更换／移除只影响插件自己的密钥。环境变量 `LINEAR_API_KEY` 优先于本地配置；卡片会显示其来源并禁用密钥更换，避免保存后实际不生效。无可见桌面的 CLI 用户仍可用 `python3 plugins/codex-orbit/scripts/mcp.py --setup` 在终端隐藏输入。

已有桌面版凭据时，第一步可改为：

```bash
python3 plugins/codex-orbit/scripts/configure.py --credentials-from /absolute/path/to/orbit-data
```

插件只读取已有桌面凭据；卡片中另存的新密钥优先于共享来源，不覆盖桌面版。若密钥环后端需要 `keyring`，请使用桌面版对应 Python 环境。修改启动配置后，重新安装插件刷新缓存。

重开 Codex 会话，使用 `$orbit-work`，或在插件列表选择「星序 / ORBIT」。例如：

- “拉取我的 Linear 任务。”
- “在当前项目处理 ENG-123。”
- “为 ENG-123 在对应的 Codex 项目中新建专用聊天并开始开发。”
- “整理这个任务的代码变动和测试结果，回传到 Linear。”

首次只拉取供选择。请求新聊天时，通过 Codex 桌面宿主的 `codex-app-tools` 按目录复用已有项目、创建 issue 专用聊天并保存关联；再次派发返回已有聊天。新仓库首次需在 Codex 添加为本地项目，当前接口不支持自动添加侧栏项目。CLI 未提供这些宿主工具时可在当前会话开发。不新建嵌套 CLI，也不后台批量领取。结果默认保存草稿；有用户回传授权后才发布。

## 数据与边界

- Linux 默认 `~/.local/share/Sevenova/OrbitCodex`；Windows 默认 `%LOCALAPPDATA%\Sevenova\OrbitCodex`。可在配置时指定 `--data-dir`。
- 使用独立 SQLite 数据空间，多会话共享领取关联与结果账本。保留本地任务、目录、可选会话 ID、结果快照及发布状态；不读取桌面版任务库或扫描聊天。
- 远端仅拉取 My Issues / issue 详情、追加结果评论并读回核对，不更改 issue 状态、负责人或父子关系，不上传文件与原始聊天。
- 网络中断后保留草稿，用相同结果 ID 重试。卸载插件不会删除数据或密钥。
- 本版为本地 marketplace 分发包，尚未提交公共插件目录。Windows 安装流程已提供，实际 Windows 运行需在 Windows 验证。

从源码构建：在项目根目录运行 `python3 scripts/build_codex_plugin.py --output /absolute/new/output`，生成完整分发目录、ZIP 和 SHA-256。
