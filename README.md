# 知序 / LUMA

原生桌面学习助手：学习任务、专注计时、课程生成与阅读、核心知识和随课答疑。

支持 Linux x86_64、Windows 10/11 x64，可调整窗口与字体。

## 启动

源码需要 Python 3.11+，先安装 `requirements.txt` 中的依赖。

- Linux：`./run-luma.sh`
- Windows：`./run-luma.ps1`

[下载应用](https://github.com/SevenovaHangzhou/Personal_workstation_qyx/releases/latest)，选择 `luma` 安装包。关闭窗口会驻留托盘，完整退出请使用托盘菜单。

## 维护

- `luma`：知序 / LUMA，学习与专注。
- [main 分支](https://github.com/SevenovaHangzhou/Personal_workstation_qyx/tree/main)：星序 / ORBIT，工作相关功能。

两条分支均支持 Linux 和 Windows。运行 `python scripts/build_product.py` 构建知序。

[分支约定](docs/BRANCHES.md) · [数据迁移与恢复](docs/APP_SPLIT.md)
