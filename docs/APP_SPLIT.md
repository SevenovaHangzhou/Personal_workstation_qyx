# 星序与知序拆分

## 边界

| | 星序 / ORBIT | 知序 / LUMA |
|---|---|---|
| 分类 | 工作、Demo、日常 | 学习 |
| 后台 | Task、Binding、Git、Linear、Codex、Daily、Update | Task、Focus、Learning、Update |
| 学习 | 无课程、专注服务 | 课程生成、改进、作答、阅读、续读、核心知识、答疑 |
| 聊天 | 新建、绑定、打开任务 Codex | 仅随课答疑；旧任务聊天关联作为历史保留 |
| 日报 | 22:00，只收集绑定到本应用任务的聊天；只有工作任务上传 Linear | 无日报、Linear、Git、工作站服务 |
| 数据目录 | Sevenova/OrbitWork | Sevenova/Luma |
| 桌面 ID | com.sevenova.Orbit | com.sevenova.Luma |

两者是独立进程、托盘、窗口激活地址、偏好设置和数据锁，可同时运行。源码在 `main`（星序）与 `luma`（知序）分支独立维护，保留共同历史；每条产品分支都支持 Linux 和 Windows。详见 [分支约定](BRANCHES.md)。`main.py` 兼容工作版启动，正式入口是 `orbit.py`、`luma.py`。旧 combined 配置仅供导入式回归工具使用，不作为交付应用。

## 迁移与恢复

首次默认启动检查旧 `Sevenova/Orbit` 或 `MomoLocal/Momo`。持有旧应用数据锁时拒绝迁移，请先从托盘退出旧版。不会自动停止正在执行的任务。

1. 锁住旧空间，完整复制到 `Sevenova/Backups/combined-<事务 ID>`，SQLite 使用备份接口包含已提交 WAL；生成 SHA-256 文件清单。
2. 在暂存目录按分类拆分，校验 ID、父子关系和 DAG。任务文字、状态、人工进度、聊天关联、课程、答案、续读和答疑历史保留。跨软件依赖需要人工确认。
3. 工作版保留 Linear 凭据身份与同步操作账本，避免重复创建 issue 或评论。知序不复制 Linear 凭据。课程内部绝对日志路径调整到知序。
4. 工作版历史日报仅保留其任务与聊天；完整原始日报和旧的生成日志在备份与原空间保留。课程及原始聊天数据不发送到网络。
5. 先写恢复账本，再发布两个独立目录。发布中断后下次启动继续；已存在的未知目标不会覆盖或合并。原空间不删除。

`--data-dir` 是显式指定的独立空间，不自动拆分其中混合数据；发现另一软件的任务分类时拒绝启动并保留文件。

回退前先完整退出两个软件。完整原空间与备份均保留，可用旧版 `--data-dir <原空间>` 打开。迁移后两边新增的数据不能自动合并回旧空间，不要覆盖它们。备份包含私有工作资料与凭据，仅保存在本机，不打包、不上传。

## 打包与 OTA

`python scripts/build_product.py` 构建当前分支维护的产品；底层也可通过 `ORBIT_APP_PROFILE=work` 或 `study` 选择 `scripts/build_release.py` / `scripts/build_windows.py` 的产品。发布清单含 app_id、平台、版本与摘要；客户端拒绝另一产品的清单。Linux 分别生成 deb / tar.gz，Windows 分别生成 setup.exe / zip。CI 对两个产品构建及离线探测，上传的验证材料仅截图，不上传运行日志或用户数据。
