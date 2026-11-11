# 星序 ORBIT · 知序 LUMA

两个独立的原生桌面应用，共用维护代码，分别保存数据。

- **星序 / ORBIT**：工作、Demo、日常任务，Codex 聊天、Linear、Git 留存和日报。
- **知序 / LUMA**：学习任务、专注计时、课程生成与阅读、核心知识和随课答疑。

支持 Linux x86_64、Windows 10/11 x64。窗口和字体可在偏好设置中调整。

## 启动

源码需 Python 3.11+，安装 `requirements.txt` 后：

| | Linux | Windows |
|---|---|---|
| 星序 | `./run.sh` | `./run.ps1` |
| 知序 | `./run-luma.sh` | `./run-luma.ps1` |

[下载正式版本](https://github.com/SevenovaHangzhou/Personal_workstation_qyx/releases/latest)。0.10.0 起分别提供两款应用安装包；较早版本是合并版。

首次启动前请从旧版托盘退出。应用会备份原数据，再迁移到 `Sevenova/OrbitWork` 和 `Sevenova/Luma`；原空间及完整备份保留。重复启动不会重复迁移。星序的新日报只收集明确绑定其任务的聊天。

关闭窗口会驻留托盘，完整退出请使用托盘菜单。两款应用分别检查、校验更新，退出后切换，下次启动生效。

[拆分与数据恢复说明](docs/APP_SPLIT.md)
