# 星序运行图谱（YomkGraph）

图谱是可选的本机只读观测入口；星序主界面仍是原生 Qt 应用。默认关闭，不启动 HTTP、不挂接观察器。使用星序现有 `framework.api`（本项目固定 YomkServerPy 1.5.6），没有升级框架或另外初始化主框架。

## 使用

从源码查看自己的真实业务进程：

```bash
cd /home/zlz/Desktop/ing_obj/Personal_workstation_qyx
./run.sh --graph --graph-port 18767
```

打开 **http://127.0.0.1:18767**，也可在托盘选择「查看运行图谱」。再次启动只会唤出已有窗口，不能给旧进程追加观测；需要先从旧版托盘完整退出，再带参数启动。普通业务调度仍按原有设置运行；关主窗口缩到托盘后观测也继续，完整退出才停止。

只想验证图谱、不接触用户数据或外部系统：

```bash
.venv/bin/python scripts/preview_graph.py --port 18767
```

这个入口启动**真实的星序服务**，使用自动清理的临时数据，关闭所有自动调度；执行本地任务查询、专注嵌套请求和 EventLoop 回调，不调用模型、不发送聊天、不写 Linear、不触发 OTA。它不是 YomkGraph 的 `--demo`。Ctrl+C 停止并回收线程。不能同时和另一个图谱进程占用相同端口；可换用 `18768`。`--port 0` / `--graph-port 0` 可用于测试时分配空闲端口。

HTTP 仅绑定 `127.0.0.1`，包含静态查看器与只读 `/api/snapshot`；没有业务调用、修改或文件读取接口。默认每两秒刷新，可搜索、查看功能目录、缩放、导出 JSON/SVG；「全局总览」恢复全部模块、基础服务、声明连线与功能展示。

## 图的含义

正常启动后由实时 `/YomkServerInfo/all` 读取 **9 个业务服务 + 5 个框架基础服务**。未被调用的已注册服务仍列出，不把历史演示端口 18765 当成星序。

| 分组 | 服务 |
| --- | --- |
| 任务与专注 | TaskService、FocusService |
| 外部集成 | BindingService、GitService、LinearService、CodexService |
| 日报与学习 | DailyService、LearningService |
| 应用维护 | UpdateService |
| 框架基础服务 | YomkServerInfo、YomkContext、YomkEventLoop、YomkLogger、YomkFunctionPool |

另有明确标为**未注册**的入口、回调及外部依赖边界，不计入上述 14 个注册服务。EmbeddedStation 当前本来就持有独立实例；图中只展示其边界，不伪装成 `framework.api` 的服务，也不合并其内部服务表。

| 证据 / 类型 | 含义 |
| --- | --- |
| 实线 `request` | 主框架实例实际处理过的同步请求；嵌套 request 自动归属到接收服务 |
| 实线 `direct-call` | 显式标注的 Python 调用，例如日报 → Linear writer、仓库保留作用域；不等同于注册函数 |
| 实线 `job` | 实际进入日报单日处理、课程生成、答疑、更新 worker 的整段作用域；与提交请求分开计时 |
| 虚线 `dependency` | 从源码明确声明的 SQLite、文件、Git、Linear、Codex、GitHub 关系；**没有运行证据和耗时** |

`calls` 是完成次数，`in_flight` 是在途数量；耗时是作用域的墙钟时间，包含嵌套等待，不能逐边相加当成 CPU 时间。框架非 eOk / 向外传播的异常计错；eOk 中的业务拒绝、worker 内部已捕获并保存的失败状态不自动变成框架失败。

## 调用归属与覆盖边界

- `@QtUI`：观测控制线程上经 `clients.call()` 进入的界面请求；直接使用客户端的命令行控制线程也可能显示此名称，可显式用 `caller('@Control')` 区分。
- `@UIWorker`：界面后台 QThread，或未处在服务作用域内的后台客户端调用。线程之间不自动继承提交者，不把它猜成某个业务服务。
- `@Boot`：Boot 初始化。观测在 `api.init(2)` 成功、Boot 和 Focus 等生产者启动前挂接；5 个内建服务的初始化早于观察器，仅通过 ServerInfo 列出。
- `@FocusClock`：专注投递时钟。`@FocusEventLoop → FocusService/tick`：专注实际回调；原来直接调用 `self.functions['/tick']` 的路径现在经过同一个框架 request，保留安装的锁、错误边界和存活检查。
- `@DailyWorker → DailyService/run-day`、`@CourseWorker → LearningService/generate-course`、`@TutorWorker → LearningService/answer-question`、`@UpdateWorker → UpdateService/prepare-update`：显式的实际异步作业。作用域内部客户端请求归属各自业务服务。
- 日报直接使用 `LinearService.writer()` 的公开方法由白名单代理观测，进入 writer 后使用 LinearService 调用方身份；不记录方法参数或返回值。仓库保留作用域显式观测，内部 Git/数据库步骤不被声称逐条采集。
- 未标注直接框架请求仍为 `@caller`；EventLoop/Monitor 不自动还原提交者。星序当前没有使用主框架 `api.async_request()` 的业务生产者；如果开发者新增此类代码，提交与执行不自动关联，回调需显式 `caller()`。
- 不观测全部 Python 函数、不扫描其他进程、不合并工作站独立框架，不自动发现 DDS / RPC 拓扑。没有连线不证明没有依赖。

只记录服务/功能/注册消息类型、Context 键名称及类型/Monitor 数量、EventLoop 状态及积压、FunctionPool 元信息，以及计数、耗时、失败和在途数。**不导出任务正文、聊天、凭据、Context 值、请求或响应载荷、异常正文或日志。** 消息类型来自注册元信息，不用它推测依赖。自省与资源查询有线程局部抑制，不进入业务统计；这些元信息不是整个系统的事务快照。

## 生命周期与故障

`observation.py` 是应用适配层，YomkGraph 的固定源码副本位于 `yomk_graph/`；来源、版本及 SHA-256 记录在 `vendor/yomkgraph-pin.json`。没有网络/CDN/npm 依赖，不依赖另一工程目录仍存在。上游目录未附独立 LICENSE 文件，本次仅本机整合和构建验证，未提交或对外发布。

主控线程在 init 后挂接 `PythonObserver`，全部业务服务初始化后开放 GraphServer。缺组件、端口占用、Boot 中途失败、HTTP 线程启动失败会进入原启动清理路径，不静默显示空图。当前验证的是 Python 源码框架；不承诺其他 Cython 构建能替换实例 request，挂接失败时会明确报告。

完整退出时：停止并排空 HTTP → 停止 Qt 定时器、取消并等待 QThread、交付已排队的完成回调 → 取消并等待更新/学习/日报/工作站 worker → 停专注投递并销毁/排空 EventLoop → 结算专注 → 排空框架异步池及回调 → 卸载观察器 → 注销业务服务与 Context → 原 `api.shutdown()`。不在回调线程停止自己，不让图谱调用 init/shutdown。HTTP 客户端有读超时，服务线程在卸载观察器前 join。

`MomoRuntime.close(final_shutdown=True)` 用于进程最终退出并排空请求池；普通测试/上下文 close 保留可复用的主框架。清理会尝试停止每种生产者；报告异常时仍继续其他清理。仍有活动线程/在途请求时保留服务与观察器，拒绝提前卸载；重复清理允许重试。框架先注销再执行 deinit，若 deinit 报错也不会把已删除服务滞留在重试清单。

## 构建与验证

现有已安装的 0.9.6 冻结包**不会随源码修改自动获得此能力**。`packaging/orbit.spec` 已纳入 SDK、HTTP 静态资源和固定来源记录；重新构建即可使用相同 `--graph` 参数。本次保持版本号、既有本机安装及用户数据，没有自动提交、推送、发布或切换桌面入口。

```bash
# 临时数据，有限时长的 HTTP/资源/14 服务自检，不调用模型或外部服务
QT_QPA_PLATFORM=offscreen .venv/bin/python main.py --self-test --graph --graph-port 0

# Linux 本机构建；输出必须不存在，避免覆盖已有发布物
. ./build/toolchain/env.sh  # 仅这台机器的本地工具链；其他机器安装构建依赖即可
.venv/bin/python scripts/build_release.py \
  --work-dir build/yomkgraph-validation-final \
  --output build/yomkgraph-validation-final-release

# 构建后的入口，同样可先做无外部副作用自检
QT_QPA_PLATFORM=offscreen build/yomkgraph-validation-final/dist/orbit/orbit \
  --self-test --graph --graph-port 0
# 实际用户空间运行前先完整退出旧版；端口与隔离预览区分
build/yomkgraph-validation-final/dist/orbit/orbit --graph --graph-port 18768
```

集成测试：

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -p test_observation.py -v
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -p test_release_entry.py -v
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

验证结果与复现证据见 [YOMKGRAPH_VALIDATION.md](YOMKGRAPH_VALIDATION.md)。当前验证范围为 Linux x86_64、Python 3.12、Qt5、实际 YomkServerPy 1.5.6；Windows/macOS、其他框架构建、大规模图性能及长期运行尚未验证。
