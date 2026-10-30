# 融合实施任务单

用户已确认：Momo 主工程、最新工作站固定依赖、同进程、Python 3.11+、单机、系统 Codex CLI/现有配置与认证、工作事项与 issue 一一对应，允许晚间同步前草稿、自设并发、关窗托盘继续执行。

| 编号 | 任务与验收 | 状态 | 主要文件 |
| --- | --- | --- | --- |
| T01 | 核对最新版双方 Yomk 架构，锁定源码并记录哈希 | 已完成 | `WORKSTATION_INTEGRATION.md`, `vendor/work_station/PINNED_SOURCE.json` |
| T02 | 工作 issue 一一映射和晚间创建、其他类别禁止新增、历史保留与原子分类转换 | 已完成 | `model.py`, `services/TaskService.py`, `tests/test_issue_scope.py` |
| T03 | 原生 issue 输入、工作专属入口、明确解除确认 | 已完成 | `main.py`, `codex_ui.py`, `tests/test_station_ui.py` |
| T04 | Python 3.11+、真实框架源码运行库、可选解释器启动 | 已完成；实测 3.12.14 | `framework.py`, `runtime/python`, `run.sh` |
| T05 | 同进程服务装配、命名域隔离、数据目录与资源所有权 | 已完成 | `station_backend.py`, `runtime.py` |
| T06 | 原生项目、任务项目关联、队列、执行详情与待处理 | 已完成 | `station_ui.py`, `main.py` |
| T07 | 本机 Codex 子进程、幂等、可设并发、同仓库互斥、权限预览 | 已完成；模型输出用受控进程验证 | `station_executor.py`, `tests/test_station.py` |
| T08 | 停止、补充/重试、验收版本校验、异常退出后先核实 | 已完成；旧会话/checkpoint 恢复未接入 | `station_backend.py`, `station_ui.py` |
| T09 | 托盘生命周期、退出回收、测试、原生截图、打包与说明 | 已完成；系统托盘真实桌面体验待实机验收 | `main.py`, `VERIFICATION.md` |
| T10 | 默认使用当前系统 Codex，不要求额外登录 | 已完成；移除登录校验、目录覆盖、provider 强制设置，保留现有认证环境 | `station_executor.py`, `station_backend.py` |
| T11 | 上游完整 Secretary/PM/DAG 的单机 Codex 迁移 | 后续独立阶段；本轮未装配 | 需逐服务移除控制器/模型 API 依赖后另行验收 |
| T12 | DAG checkpoint 恢复、可视化、自动 worktree、自动聊天绑定 | 后续体验阶段；未实现 | 不用简单重试冒充恢复 |

本轮不修改原工作站仓库，不下发真实模型任务，不更改系统 Codex 配置或登录。T10 已按用户最新要求完成，无待提供的登录目录。

0.6.0 日报与自动进度的 R01–R15 细分任务、验收和规则见 `DAILY_PROGRESS_PLAN.md`。本轮已额外实施数据锁前移、轻量刷新、笔记保护、增量缓存与队列维护互斥，评估见 `OPTIMIZATION_REVIEW.md`。
