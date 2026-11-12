# 星序 / ORBIT

原生桌面工作助手：管理工作、Demo 和日常任务，关联 Codex 与 Linear，记录日报、周报和 Git 留存。

支持 Linux x86_64、Windows 10/11 x64，可调整窗口与字体。

此为 `evaluator` 开发分支：增加员工名册、设备与 Linear 对应、日志共享、离线副本及进度台账。[使用手册](docs/EVALUATOR_GUIDE.md)。

## 启动

源码需要 Python 3.11+，先安装 `requirements.txt` 中的依赖。

- 评估开发版：Linux `./run-evaluator.sh`，Windows `./run-evaluator.ps1`（默认独立数据空间）。
- Linux 工作版：`./run.sh`
- Windows：`./run.ps1`

[下载应用](https://github.com/SevenovaHangzhou/Personal_workstation_qyx/releases/latest)。关闭窗口会驻留托盘，完整退出请使用托盘菜单。

## 维护

- `main`：星序 / ORBIT，工作相关功能。
- [luma 分支](https://github.com/SevenovaHangzhou/Personal_workstation_qyx/tree/luma)：知序 / LUMA，学习与专注。

两条分支均支持 Linux 和 Windows。运行 `python scripts/build_product.py` 构建当前分支的产品。

[Linear 使用手册](docs/LINEAR_GUIDE.md) · [分支约定](docs/BRANCHES.md) · [数据迁移与恢复](docs/APP_SPLIT.md)
