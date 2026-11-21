# 星序 / ORBIT

本分支维护 **Codex 插件 0.6.0**：[安装与使用](plugins/codex-orbit/README.md) · [版本说明](plugins/codex-orbit/CHANGELOG.md)。源码安装：Linux 运行 `sh plugins/codex-orbit/install.sh`，Windows 运行 `plugins\codex-orbit\install.cmd`。需要 Codex CLI 与 Python 3.11+。

npm 安装并启用插件：

```sh
npm install -g "git+https://github.com/SevenovaHangzhou/Personal_workstation_qyx.git#codex/orbit-plugin"
orbit-codex install
```

需要 Node.js 18+、Git、Codex CLI、Python 3.11+。安装后新建 Codex 聊天，说“打开星序设置”。当前从 Git 分支安装，尚未发布 npm registry。[npx 与更多参数](plugins/codex-orbit/README.md#npm-安装linux--windows)

新增：默认每天 00:00 自动整理绑定聊天日报，支持时间设置、授权 LAN 共享与离线副本。[日志统计标准](plugins/codex-orbit/日志统计标准.md) · [管理员使用](plugins/codex-orbit/README.md#局域网日志与管理员读取)。

完整验证后自动完成对应 issue，读回核对后归档任务会话；未完成或有阻碍时保留。[任务收尾](plugins/codex-orbit/README.md#完成任务并归档会话)。

原生桌面工作助手：管理工作、Demo 和日常任务，关联 Codex 与 Linear，记录日报、周报和 Git 留存。

支持 Linux x86_64、Windows 10/11 x64，可调整窗口与字体。

## 启动

源码需要 Python 3.11+，先安装 `requirements.txt` 中的依赖。

- Linux：`./run.sh`
- Windows：`./run.ps1`

[下载应用](https://github.com/SevenovaHangzhou/Personal_workstation_qyx/releases/latest)。关闭窗口会驻留托盘，完整退出请使用托盘菜单。

## 维护

- `main`：星序 / ORBIT，工作相关功能。
- [luma 分支](https://github.com/SevenovaHangzhou/Personal_workstation_qyx/tree/luma)：知序 / LUMA，学习与专注。
- [evaluator 分支](https://github.com/SevenovaHangzhou/Personal_workstation_qyx/tree/evaluator)：星序评估，收集授权日志和项目进度。

各产品均支持 Linux 和 Windows。运行 `python scripts/build_product.py` 构建当前分支的产品。

[Linear 使用手册](docs/LINEAR_GUIDE.md) · [同事协作](docs/COLLABORATION.md) · [分支约定](docs/BRANCHES.md) · [数据迁移与恢复](docs/APP_SPLIT.md)

Codex 本地插件：[安装与使用](plugins/codex-orbit/README.md)，支持 My Issues 领取、项目聊天派发和开发结果回传。
