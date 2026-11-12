# 产品分支维护

| 分支 | 产品 | Linux 启动 | Windows 启动 |
|---|---|---|---|
| `main` | 星序 / ORBIT：工作、Demo、日常、Codex、Linear、日志、Git | `./run.sh` | `./run.ps1` |
| `evaluator` | 星序评估开发版：员工授权日志、离线副本、证据问答与进度台账 | `./run-evaluator.sh` | `./run-evaluator.ps1` |
| `luma` | 知序 / LUMA：学习、专注、课程、核心知识、答疑 | `./run-luma.sh` | `./run-luma.ps1` |

`main`、`luma` 均维护 Linux 与 Windows 支持；`evaluator` 提供两端启动源码，仍需 Windows 真机联调。现有 `windows` 分支和历史标签保留。

在产品所属分支开发与提交。两条分支保留共同历史和兼容模块；公共修复按需用 `git cherry-pick <提交>` 移植，避免整分支合并把另一产品的默认入口与说明覆盖回来。

`python scripts/build_product.py` 按所在维护分支选择产品，再调用当前系统的原生打包器；命令行参数继续传给原打包器，例如 `--output`、`--work-dir`。底层 `build_release.py` / `build_windows.py` 仍支持显式的 `ORBIT_APP_PROFILE`。

现有 `v*` 正式发布流程继续要求两款产品的完整资产，供已发布版本的 OTA 使用。源码分支独立不等同于发布渠道独立；以后拆分正式版本时，应同时调整标签、资产验证与 OTA 版本发现。

数据仍分别保存在 `Sevenova/OrbitWork` 与 `Sevenova/Luma`，切换源码分支不迁移或清空应用数据。

评估开发入口默认使用 `Sevenova/OrbitEvaluator`，关闭正式 OTA 自动检查与退出切换。显式指定 `--data-dir` 才使用选定工作空间；不要在该空间已有实例运行时启动。0.10.1 起随正式 Release 附带独立管理员安装包，manifest 中记录本分支固定提交；当前手动更新，不进入工作版 OTA。
