# 课程本地沙箱

按照 8 月 22 日 VIP 直播代码搭建，保留课程源码和沙箱目录结构。

```text
aicoding/
├── agent/                 原课程后端代码
├── scripts/               启动、安装和检查脚本
├── ui/                    原课程 Vue 前端
├── data/                  本机 SQLite 数据
├── logs/                  服务日志
├── .venv/                 后端 Python 环境
├── .env                   本机配置
└── ai_workspace/
    ├── projects/          需要 Agent 操作的项目
    ├── skills/            课程技能
    ├── policies/          工作区规则
    ├── reviews/           审查产物
    ├── runtimes/
    │   └── python/default/.venv/  沙箱项目的共享 Python 环境
    ├── tmp/
    ├── logs/
    └── .secrets/           Git askpass 辅助脚本
```

## 使用

双击 `scripts/start_all.cmd` 启动后端和前端。前端地址为
http://127.0.0.1:3000，后端健康检查为 http://127.0.0.1:12024/health。
控制台按 Ctrl+C 停止服务。也可分别使用 `start_backend.cmd`、`start_ui.cmd`。
启动前确认 12024 和 3000 端口空闲。

双击 `scripts/check_sandbox.cmd` 检查目录、工具、虚拟环境、文件读写、
路径越界拒绝、规则目录保护和命令限制。检查不调用模型或远端仓库。

将需要操作的项目放入 `ai_workspace/projects/`。文件工具使用
`/projects/<项目名>`，命令默认在 `projects/` 中执行。

`.env` 中填写自己的 `DASHSCOPE_API_KEY`；需要 Gitee 功能时填写
`GITEE_TOKEN`，需要联网搜索时填写 `ZHIPU_API_KEY`。
模型通过 `QWEN_MODEL` 配置，默认是 `qwen3.8-flash`，接口地址由 `QWEN_BASE_URL` 配置。
这些配置缺失不影响独立沙箱检查，但相应 Agent 功能需要有效凭据。

## 重建环境

使用 Python 3.12、Node.js 22.12 以上以及 Git。在 PowerShell 中运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

脚本安装后端依赖，创建两套虚拟环境，初始化工作区，补充课程技能，
安装前端依赖并执行检查。可用 `-PythonExe` 指定 Python。
移动目录后重新运行此脚本以更新路径；原有密钥配置会保留。

## 本机适配

启动脚本改为相对路径，默认沙箱位置改为项目内的 `ai_workspace`。
补充依赖清单与安装后的版本锁定清单。原生 `execute()` 补上课程已有的
命令白名单检查，与 `run()` 一致。

这是课程的 Windows 本地工作区执行层，命令以当前 Windows 用户权限运行，
Python 虚拟环境用于依赖隔离，不提供容器或操作系统级隔离。
只运行可信项目代码。
