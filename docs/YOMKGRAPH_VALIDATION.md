# YomkGraph 星序集成验证 · 2026-11-02

本轮验证实际星序主框架 `runtime/python/YomkApi.py`、YomkServerPy **1.5.6**，Python 3.12.16、Linux x86_64、Qt5。没有升级到其他工程的框架版本。已有未提交修改保留；本次未提交、推送或发布，也未切换已安装应用的启动入口。

| 验证 | 结果与证据 |
| --- | --- |
| 全部应用回归 | **304/304 通过**；`/tmp/orbit-yomkgraph/all-tests-final.log` |
| 观测集成测试 | 13 项真实框架独立子进程场景，包含于完整回归；`tests/test_observation.py` |
| 发布入口 | 4 项通过，包括新增 `--self-test --graph --graph-port 0`；`tests/test_release_entry.py` |
| 浏览器 | Chrome + Playwright，5 组实际星序检查通过，0 pageerror；`/tmp/orbit-yomkgraph/repro-browser/browser-result.json` |
| 服务清单 | 实时 ServerInfo 14 个注册服务：9 业务、5 基础；另外 10 个显式边界节点不冒充注册服务 |
| 图谱查询不污染 | 连续 HTTP `/api/snapshot` 与资源查询前后业务 edge 统计不变 |
| 隐私 | 任务正文、请求样本和 Context 值哨兵均不出现在 JSON；Context 键类型与 Monitor 数量可见 |
| 回调和嵌套 | Qt 客户端、后台客户端、FocusClock → EventLoop、FocusEventLoop → FocusService/tick、FocusService → TaskService |
| 直接路径与异步 | 日报 writer 的假远端返回下实际 wrapper；真实空日报单日 pipeline；可取消的假课程 runner，在途作业与已返回的提交请求分开 |
| 失败与回收 | 依赖缺失、Boot 失败、端口占用、HTTP 启动失败、停止/注销异常、重复 close；在途 HTTP 等待完成后卸载；最终退出排空主框架异步请求及回调 |
| Qt 完成回调 | 主线程等待 QThread，并在服务/观察器仍可用时交付完成信号；不提前清空服务表 |
| 图谱默认关闭 | 不导入 SDK，不开放监听，原功能回归可用 |
| 本地实际预览退出 | SIGTERM 后 HTTP 关闭、进程退出码 0；仅停止本轮自己创建的临时数据进程 |
| 本地冻结构建 | `scripts/build_release.py` 完整通过；冻结应用、便携包解包、Debian 解包均通过普通自检和图谱 14 服务/静态资源/不污染统计自检 |

构建目录：`build/yomkgraph-validation-final/`，包与 SHA256SUMS：`build/yomkgraph-validation-final-release/`。此为未发布的本地开发验证构建，沿用 0.9.6 版本号；manifest 标明 source_dirty 和观测源码哈希，不能当成 GitHub 正式版本。既有 `release/0.9.6` 及本机安装未覆盖。

冻结包证据：`graph-self-test.log`、`portable-graph-self-test.log`、`debian-graph-self-test.log`。图谱静态资源使用 importlib.resources，在冻结包中实际通过 HTTP 回读验证，非仅断言 spec 字符串存在。

浏览器默认全局图、筛选重置、未调用服务功能目录、资源检查、实线嵌套/回调证据均通过。默认展开全部功能时图较长，可取消「功能与消息类型」获得更紧凑的总图，再点击服务或切换「功能目录」。截图与 JSON：`/tmp/orbit-yomkgraph/live-orbit.png`、`overview.png`、`live-snapshot.json`。这些临时路径是验证证据，不是运行依赖。

复现浏览器检查（先启动隔离预览）：

```bash
.venv/bin/python scripts/preview_graph.py --port 18767
# 另一终端，使用自己已有的 Playwright 和 Chrome；脚本不下载依赖
PLAYWRIGHT_MODULE=/path/to/playwright-core \
CHROME_BIN=/path/to/google-chrome \
ORBIT_GRAPH_URL=http://127.0.0.1:18767 \
node scripts/check_graph_browser.cjs
```

`ORBIT_GRAPH_OUTPUT` 可指定截图与 JSON 输出目录，默认 `build/graph-browser`。脚本要求真实星序 14 服务清单，不接受示例图充数。

本轮隔离验证没有调用真实 Linear 写入、Codex/模型、聊天发送、Git 远端或 OTA 激活，没有读取真实任务/聊天正文或修改用户数据库。外部依赖均为声明；writer wrapper 用 fake 返回验证，课程 worker 用可控 runner 验证。未验证 Windows/macOS、其他 Yomk 构建、高负载性能、完整生产长时间执行、工作站独立框架服务表合并或 DDS/RPC 自动发现。主应用仍为原生 Qt，只有选启的只读观测工具使用本地浏览器。
