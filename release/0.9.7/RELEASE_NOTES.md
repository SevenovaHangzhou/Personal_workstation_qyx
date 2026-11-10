# 星序 ORBIT 0.9.7

首个同时提供 Linux 与 Windows 原生应用的版本，两个分支同步最近改动。

- Windows 10 / 11 x64：新增便携包与无需管理员权限的安装包，包含开始菜单入口与可选桌面图标。
- Windows 使用原生文件锁、进程树管理与 DPAPI 凭据加密，支持托盘驻留、重复启动唤起已有窗口和退出后更新。
- 同步近期 Linux 改进：课程重新生成意见与阶段进度、原生教学图解、数据目录迁移和可选运行图谱。
- 修复日报摘要的 MCP 配置覆盖与错误诊断，以及跨团队 Linear 父子 issue 的同步阻断。
- Linux 继续提供 .deb 与便携包；两平台均内置 Python 环境，Codex CLI 与 Git 沿用本机配置。

下载：Linux 选择 .deb 或 linux-x86_64.tar.gz；Windows 选择 setup.exe 或 windows-x86_64.zip。校验值见 SHA256SUMS / WINDOWS-SHA256SUMS。

应用关闭窗口后驻留托盘，完整退出再启动可使用新版。旧安装保留为更新回退，用户数据不包含在发布包中。Windows 安装包未进行商业代码签名，系统可能提示未知发布者。
