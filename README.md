# AI Coding：多智能体编程与代码交付平台

**让智能体理解需求、分析仓库、生成方案，并在人工确认后编写代码、执行验证和交付结果。**

AI Coding 是基于 Python、FastAPI、LangGraph、DeepAgents 和 Vue 3 构建的 AI 编程智能体平台。核心能力包括仓库分析、任务规划、人工确认、受控代码修改、测试验证、代码审查，以及经授权后的 Git 提交和 Gitee Pull Request 创建。模型与工具的执行过程通过 SSE 实时展示，便于跟踪任务计划、工具结果和最终答复。

项目同时提供动态 Skill 导入、MongoDB 会话状态与长期记忆、Langfuse 链路追踪，以及订单 CSV 统计和销售经营看板，适合用于 AI 编程助手、企业研发提效、智能体工程化学习和业务分析工具扩展。

## 核心功能

| 功能 | 具体能力 |
| --- | --- |
| 智能体自主编码 | 理解需求、分析仓库、拆解任务、生成方案，人工确认后修改代码并验证结果 |
| 多智能体协作 | 主智能体编排任务，分析与代码审查子智能体协同处理仓库上下文和变更 |
| 本地执行与工具治理 | Windows / Linux 本地沙箱，文件读写、搜索和命令执行，配套路径校验、执行限制及异常恢复 |
| 动态技能管理 | 在前端选择本地 Skill 文件后自动导入，也支持通过公开网络地址下载导入 |
| 历史会话与长期记忆 | MongoDB 保存会话 Checkpoint 和跨会话仓库记忆，SQLite 保存当前业务记录 |
| 实时过程展示 | 通过 SSE 展示文本增量、工具调用、任务计划、完成状态和异常信息 |
| 调用追踪与效果评估 | 接入 Langfuse，关联模型与工具调用、Token 用量、耗时、用户反馈及评测结果 |
| 研发交付闭环 | 集成 Gitee 仓库同步、分支、提交、推送、PR 创建和代码审查流程 |
| 销售经营看板 | 上传订单 CSV，匹配字段、筛选日期，展示销售趋势、品类结构、退款分析及经营建议 |

## 按场景查找功能

| 你想做什么 | 从哪里开始 |
| --- | --- |
| 本地启动、配置模型和默认仓库 | 阅读下方“本地启动”与“个人配置与默认仓库” |
| 了解沙箱权限和执行边界 | [本地沙箱说明](README.sandbox.md) |
| 上传或通过网络导入技能 | [动态 Skill 使用说明](docs/SKILLS.md) |
| 分析订单、销售额、品类或退款 | [CSV 销售统计与看板说明](docs/CSV_ANALYTICS.md) |
| 配置联网搜索与网页检索 | [网络搜索工具说明](docs/WEB_SEARCH.md) |
| 了解回答评测和评分口径 | [回答评测标准](docs/EVALUATION_STANDARD.md) |
| 配置链路观测 | 阅读下方“Langfuse 调用追踪” |
| 在 Linux 上部署服务 | [Linux 部署说明](deploy/README.md) |
| 了解消息队列集群与 MCP 数据源接入 | 阅读下方“项目定位与扩展架构” |

**检索关键词：**AI 编程助手、智能体自主编码、多智能体协作、代码生成、代码审查、任务规划、人机确认、断点恢复、动态技能、会话记忆、本地沙箱、销售看板、退款分析、LangGraph、DeepAgents、LangChain、MongoDB、SQLite、Langfuse、MCP、SSE。

## 项目定位与扩展架构

AI Coding 以智能体自主编码与研发任务交付为核心，覆盖仓库分析、需求理解、方案规划、人工确认、代码修改、测试验证及经授权后的提交与 PR 创建。动态 Skill、MCP 工具和销售看板用于扩展任务能力与业务场景。

**存储方案：**采用 SQLite 与 MongoDB 分层持久化，兼顾本地运行和生产环境使用。生产环境使用 MongoDB 保存会话历史、LangGraph Checkpoint 与跨会话记忆，支持状态查询和人工确认后的恢复；当前 Thread、Run 和审查记录仍保存在 SQLite，具体职责见下文“MongoDB 记忆存储”。这里不表示所有数据均可通过一个开关在两种数据库之间切换。

**集群扩展方案：**将用户请求接入、Agent Worker 与 SSE 推送解耦。请求接入层将任务发布到消息队列，Worker 消费任务并执行模型与工具调用，再将文本增量、工具执行状态和最终结果写入事件通道，由 SSE 服务订阅并推送给对应客户端。消息队列、请求接入服务和 SSE 服务可根据公司现有技术栈选型。

集群落地时，通过 `thread_id`、`run_id` 和事件序号关联任务与回复，并配套消费幂等、会话执行互斥、失败重试、取消传播和断线重连后的事件补发；业务记录及任务工作区也需要共享存储。当前版本使用进程内工作线程与 Asyncio Queue 桥接 SSE，外部消息队列与跨节点调度属于扩展设计。

**MCP 数据接入：**销售看板可通过 MCP 工具接口扩展对接企业数据服务，将数据获取与指标统计、图表展示及经营建议分离。当前已交付 CSV 上传、字段映射、销售与退款统计及看板展示；具体企业 MCP 数据源应由接入方配置与实现。看板是可扩展业务能力，平台的核心仍是智能体分析、编写、修改和验证代码。

## 本地启动

本项目集成了课程代码中的 Windows 本地沙箱，相关实现位于项目根目录，Agent 工作区为 `ai_workspace`。

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
`LANGFUSE_BASE_URL`，重启后端后生效。观测服务地址通过 `LANGFUSE_BASE_URL` 指定。
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

远程服务器的 MongoDB 容器只发布到服务器 `127.0.0.1:27017`。
本地开发先运行 `scripts/start_mongodb_tunnel.cmd user@your-server`，按提示输入 SSH 密码，保持窗口打开；
然后启动 `main.py`。SSH 密码没有保存在脚本中。

本机 `.env` 使用 `MONGODB_URI=mongodb://127.0.0.1:37017`，MongoDB 账号和认证库仍按前述配置。
隧道命令为：

```powershell
ssh -N -L 127.0.0.1:37017:127.0.0.1:27017 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 user@your-server
```

若提示本机 37017 已被占用，请检查是否已有该隧道运行，不要重复启动。

## Linux 服务

部署访问地址通过 `DEPLOY_PUBLIC_URL` 配置，推荐服务器目录为 `/opt/aicoding`。
网页登录信息保存在本机被 Git 忽略的 `data/linux-deployment/access.txt`。
部署方式、日志和停止命令见 [Linux 部署说明](deploy/README.md)。
服务器直接连接 Docker 内网的 MongoDB，不依赖本机 SSH 隧道。

## 个人配置与默认仓库

界面统一使用 **AI Coding**。在本机 `.env` 设置 `DEFAULT_REPO_URL`（完整 Gitee URL 或 `owner/repo`），前端通过 `/dashboard/api/options` 获取默认仓库；也可直接在对话框中输入目标仓库。未配置时不会自动使用任何个人仓库。
`DASHBOARD_USER_LOGIN` 控制本地 Dashboard 展示的用户标识，并不提供登录鉴权。`GIT_AUTHOR_NAME`、`GIT_AUTHOR_EMAIL`、`GIT_COMMITTER_NAME`、`GIT_COMMITTER_EMAIL` 用于 Git 提交身份。

工作区、数据与日志路径默认依据项目所在目录确定，也可通过 `.env` 覆盖。复制到其他 PyCharm 目录时，显式传入 `scripts/install-to-pycharm.ps1 -Target <目标目录>` 或 `scripts/apply-qwen.ps1 -Target <目标目录>`。
首次 Linux 打包使用 `scripts/package_linux.py`，源码默认取当前项目；可用进程环境变量 `PACKAGE_SOURCE_ROOT` 指定其他源码目录。`DEPLOY_PUBLIC_URL`、`DEPLOY_WEB_USER` 配置首次部署的访问地址与登录名；更新部署不会轮换现有登录账号。
