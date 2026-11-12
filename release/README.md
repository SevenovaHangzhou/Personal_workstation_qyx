# 应用下载 · 0.10.1

[下载最新 Release](https://github.com/SevenovaHangzhou/Personal_workstation_qyx/releases/latest) · [版本说明](0.10.1/RELEASE_NOTES.md) · [Linear 使用手册](../docs/LINEAR_GUIDE.md)

| 产品 | Linux x86_64 | Windows 10/11 x64 |
|---|---|---|
| 星序 / ORBIT | `sevenova-orbit_0.10.1_amd64.deb` / `orbit-0.10.1-linux-x86_64.tar.gz` | `orbit-0.10.1-windows-x86_64-setup.exe` / `.zip` |
| 星序评估 / EVALUATOR | `sevenova-orbit-evaluator_0.10.1_amd64.deb` / `orbit-evaluator-0.10.1-linux-x86_64.tar.gz` | `orbit-evaluator-0.10.1-windows-x86_64-setup.exe` / `.zip` |
| 知序 / LUMA | `sevenova-luma_0.10.1_amd64.deb` / `luma-0.10.1-linux-x86_64.tar.gz` | `luma-0.10.1-windows-x86_64-setup.exe` / `.zip` |

安装包内置 Python 环境。Linux 需要 glibc 2.35+；Windows 安装无需管理员权限。便携包解压后运行 `orbit` / `luma` / `orbit-evaluator`（Windows 为 `.exe`）。

升级前从旧版托盘完整退出。首次启动会备份并拆分原数据，分别存入 `Sevenova/OrbitWork` 和 `Sevenova/Luma`；卸载不删除数据。[迁移与恢复](../docs/APP_SPLIT.md)

管理员版使用独立安装目录和 `Sevenova/OrbitEvaluator` 数据空间，当前手动更新。

每款产品附平台清单和 SHA-256 校验文件。包中不含用户数据或凭据，Codex CLI 与 Git 沿用本机配置。Windows 安装包未进行商业代码签名。

## 源码构建

安装当前系统的构建依赖后，在 `main` 或 `luma` 分支运行 `python scripts/build_product.py`。输出位于 `release/0.10.1/`；构建会检查冻结应用与解包后的离线启动，Windows 另检查安装和卸载。[分支与构建约定](../docs/BRANCHES.md)
