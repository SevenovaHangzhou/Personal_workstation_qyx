# 本地项目迁移 · 0.6.1

正式维护目录：`/home/kkozia/桌面/ing_obj/Personal_workstation_qyx/`。

## 启动

```bash
cd /home/kkozia/桌面/ing_obj/Personal_workstation_qyx
./run.sh
```

源码、固定 Work Station/Yomk 依赖及现有 Python 3.12 虚拟环境均从本机组装，不需要下载、解压或重新登录 Codex。系统 Codex 配置和认证保持沿用。

应用标识仍为 MomoLocal/Momo，任务、凭据、日报和后台记录继续使用原来的 Qt 数据目录，通常是 `/home/kkozia/.local/share/MomoLocal/Momo/`。已有数据没有复制进源码目录；自定义数据空间仍可使用 `./run.sh --data-dir /原来的数据路径`。

## 本轮改动

- 移除项目空间和本机执行页，清除侧栏、托盘与详情中的项目关联、新建项目、执行提交和重试入口。
- 保留「需要我处理」，展示已有待处理记录并允许验收、查看日志和产物。后台 Yomk 服务、历史记录和日报/Git 维护互斥保留。
- 「规划下一步」取代「创建 N 个子任务」，显式前置依赖生成自上而下的原生 DAG，支持串行、并行与汇合；仅用于本地规划。

迁移使用空目录预检查、分阶段复制及源码逐文件校验；虚拟环境启动脚本更新到新路径。`LOCAL_MIGRATION.json` 在目标目录保存来源、版本、文件校验和与环境信息，不包含用户凭据或聊天原文。

## 验证状态

- 迁移前：完整 108 项测试通过（25.228 秒）。
- 在目标目录使用迁入的独立 .venv 再跑全部 108 项测试，通过（25.271 秒）。覆盖真实 Yomk 服务、临时 SQLite/Git 与 Qt offscreen 界面。
- 新路径 run.sh 未设置 MOMO_PYTHON，正确选用目标目录 .venv；使用隔离测试数据启动原生窗口并截图，退出码 0。
- check_env.py 确认从新目录加载真实 YomkServerPy 1.5.6，Python 3.12.14 / Qt 5.15.14 / PyQt 5.15.11。
- 确认新进程默认数据位置仍是原来的 MomoLocal/Momo，已有 tasks.json 可见。验证没有打开或改写用户真实数据空间，也没有发送真实 Linear 写入。
- 迁移发现 PyQt5 wheel 的默认插件前缀不能正确保留中文路径，已由 qt_runtime.py 在 QApplication 构造前显式注册当前环境的 Unicode 插件路径。无需换目录或重装依赖。

旧入口 `/home/kkozia/Documents/Codex/2026-10-27/new-chat/outputs/momo-yomkserverpy-linux-0.5.1/momo-desktop/run.sh` 转发到新目录，传递全部启动参数。旧代码副本保留作为迁移备份，后续维护以新目录为准。

当前环境没有可用图形桌面，启动与控件验证采用 Qt offscreen；桌面托盘宿主仍需实际桌面使用验证。
