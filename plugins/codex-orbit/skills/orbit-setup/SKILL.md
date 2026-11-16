---
name: orbit-setup
description: 打开星序 Codex 插件的本机设置卡片，配置或更换 Linear 连接与默认工作目录。用于首次设置、修改密钥和检查连接。
---

调用星序 ORBIT 的 `open_settings` 打开本机原生卡片。让用户在卡片中填写 Linear API Key、测试账号并保存；密钥不要发到聊天，也不作为工具参数、命令参数或代码内容。

卡片独立于 Codex 运行，打开后不要阻塞等待或循环查询。告知用户保存后可直接继续原任务，插件会立即读取新配置。用户要求继续时再用 `status` 核对，执行其原有请求。

若返回 `ui_unavailable`，根据原因说明需要本地可见桌面或当前 Python 中的 `requirements-ui.txt` 依赖。可使用返回的不含密钥的 `local_command` 从本机终端打开。CLI 用户也能用 `scripts/mcp.py --setup` 隐藏输入并验证密钥。不要改用聊天收集密钥。

已配置的用户可以随时重新打开卡片；更换或移除只影响插件自己的密钥，桌面版凭据保留。
