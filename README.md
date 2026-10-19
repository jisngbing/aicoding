# aicoding

本项目已接入 8 月 22 日 VIP 直播代码中的 Windows 本地沙箱。
课程代码直接集成在项目根目录，Agent 工作区为 `ai_workspace`。

```text
aicoding/
├── start.cmd                   项目启动入口
├── check-sandbox.cmd           沙箱自检入口
├── setup.ps1                   环境安装入口
├── agent/                      Agent 和本地沙箱实现
├── scripts/                    课程脚本及本机适配
├── ui/                         Vue 前端
├── data/                       SQLite 数据
├── logs/                       服务日志
├── .venv/                      后端 Python 环境
├── .env                        模型与本机路径配置
└── ai_workspace/
    ├── projects/
    ├── skills/
    ├── policies/
    ├── reviews/
    ├── runtimes/python/default/.venv/
    ├── tmp/
    ├── logs/
    └── .secrets/
```

双击根目录的 `start.cmd` 启动项目，访问 http://127.0.0.1:3000/。
在 PyCharm 中也可以运行根目录 `main.py`，解释器选择 `.venv/Scripts/python.exe`。
后端健康检查地址为 http://127.0.0.1:12024/health。
启动控制台按 Ctrl+C 停止服务。已有服务运行时无需重复启动。

双击 `check-sandbox.cmd` 检查沙箱。
模型 API Key 和 Gitee Token 在根目录 `.env` 中配置。
需要 Agent 操作的项目放入 `ai_workspace/projects/`。

重建环境时，在项目根目录的 PowerShell 中运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

详细配置与本地沙箱的运行边界见 [沙箱说明](README.sandbox.md)。

## Langfuse 调用追踪

在根目录 `.env` 配置 `LANGFUSE_SECRET_KEY`、`LANGFUSE_PUBLIC_KEY` 和
`LANGFUSE_BASE_URL`，重启后端后生效。当前服务地址为 http://124.220.188.160:3000。
`LANGFUSE_TRACING_ENABLED=false` 可以关闭采集，`LANGFUSE_TRACING_ENVIRONMENT`
用于区分环境，默认为 `development`。

DeepAgent 的输入输出、模型调用、工具调用、子 Agent、耗时、模型返回的 token 用量和
执行异常会通过官方 LangChain 回调自动采集。按 `thread_id` 聚合为 Langfuse Session，
每轮携带 `run_id` 和 `task_kind` 元数据。意图分类及最终答复补充调用也会单独追踪。
采集在后台批量发送，正常退出后端时会刷新待发送记录；凭据在发送前脱敏。
配置缺失或采集初始化失败不影响任务执行，发送失败可在后端日志中排查。

无需调用付费模型即可验证 SDK 认证、工具调用及服务端落库：

```powershell
.\.venv\Scripts\python.exe scripts/check_langfuse.py
```

运行后可在 Langfuse 查看 `aicoding.sdk-check` 测试追踪。

## MongoDB 记忆存储

仓库长期记忆和聊天 checkpoint 使用 MongoDB。任务列表、运行状态和审查发现仍保存在
`data/store.sqlite`。旧的两个 SQLite 记忆文件仅作为迁移源保留。

在本机 `.env` 配置（支持本地 MongoDB 和远程 MongoDB）：

```dotenv
MONGODB_URI=mongodb://127.0.0.1:27017
MONGODB_USERNAME=
MONGODB_PASSWORD=
MONGODB_AUTH_SOURCE=admin
MONGODB_DATABASE=aicoding
MONGODB_MEMORY_COLLECTION=memories
MONGODB_CHECKPOINT_COLLECTION=checkpoints
MONGODB_WRITES_COLLECTION=checkpoint_writes
MONGODB_TIMEOUT_MS=5000
```

认证信息只放 `.env`。连接失败会明确报错，不会切换回 SQLite。
这次仅使用普通文档读写，不依赖 Atlas 向量搜索。

安装依赖、验证连接及无模型 API 的工具循环/历史恢复：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe scripts/check_mongodb.py
```

停止旧服务后，迁移已有数据（不删除源文件，目标已存在记忆不会覆盖，可重复运行）：

```powershell
.\.venv\Scripts\python.exe scripts/migrate_memory_to_mongodb.py
```

也可以通过 `--checkpoint-source`、`--memory-source` 指定旧数据库路径。
checkpoint 迁移同时保留快照、父节点关联和工具/消息增量写入，迁移后再启动 `main.py`。

### 当前服务器通过 SSH 隧道连接

`8.160.114.12` 的 MongoDB 容器只发布到服务器 `127.0.0.1:27017`。
本地开发先运行 `scripts/start_mongodb_tunnel.cmd`，按提示输入 SSH 密码，保持窗口打开；
然后启动 `main.py`。SSH 密码没有保存在脚本中。

本机 `.env` 使用 `MONGODB_URI=mongodb://127.0.0.1:37017`，MongoDB 账号和认证库仍按前述配置。
隧道命令为：

```powershell
ssh -N -L 127.0.0.1:37017:127.0.0.1:27017 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 root@8.160.114.12
```

若提示本机 37017 已被占用，请检查是否已有该隧道运行，不要重复启动。

## Linux 服务

已部署到 `http://8.160.114.12/`，服务器目录 `/opt/aicoding`。
网页登录信息保存在本机被 Git 忽略的 `data/linux-deployment/access.txt`。
部署方式、日志和停止命令见 [Linux 部署说明](deploy/README.md)。
服务器直接连接 Docker 内网的 MongoDB，不依赖本机 SSH 隧道。
