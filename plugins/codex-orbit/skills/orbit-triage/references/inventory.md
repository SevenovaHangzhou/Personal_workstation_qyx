# 只读查询

脚本使用 Python 3.11+ 和插件自带模块，源码、ZIP 与 npm 安装均可运行。用当前技能目录的绝对路径替换下例；优先使用插件 MCP 配置中的 Python 解释器。

```sh
python3 /absolute/plugin/skills/orbit-triage/scripts/issue_inventory.py --output /tmp/orbit-triage-snapshot.json
python3 /absolute/plugin/skills/orbit-triage/scripts/issue_inventory.py --issue ENG-123 --issue ENG-456
```

- 不传 `--output` 时输出 JSON。输出文件独占创建，不覆盖旧记录；后续查询用新文件名。全部查询成功后才写入快照。
- 沿用插件凭据优先级和根目录 `settings.json`、`ORBIT_DATA_DIR`／`ORBIT_CREDENTIALS_DIR`；可传 `--data-dir`／`--credentials-dir` 指定已知目录。不接受 API Key 参数，不搜索凭据文件。
- `as_of` 为 UTC 查询完成时间，用户报告换算为客户端时区；`viewer` 是当前账号。`total` 为全部未结束事项数；`selected_count`、`issues` 为本次选取范围；`missing_identifiers` 仅表示不在本次 My Issues 范围，不表示任务不存在。
- 每项含正文、优先级、期限、项目、parent 及全部 children／relations／inverseRelations／comments，各层独立分页。查询或分页异常会失败退出。
- `local_bindings` 仅读本插件已存在的 `codex.sqlite3`，按 viewer 和本次 issue UUID 过滤；这是 ORBIT 自己的关联库，不是 Codex 聊天数据库。脚本不创建或修改任务库。绑定读取失败写入 `warnings`；随后仍须用宿主核对真实聊天和内容。
- 脚本不排序、关单或改绑定。排序与证据冲突由技能结合聊天判断；不要把整份评论和工具日志直接展示给用户。
- 网络受限时按宿主审批路径执行相同只读命令。认证失败时停止报告，不无限重试或改用未授权凭据。
