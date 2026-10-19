# Skill 管理

页面右上角的 **Skill 管理** 提供本地和网络导入。原有代码实施、仓库分析、代码审查、方案确认、工具和中间件继续使用。

## 本地文件

选择文件后立即上传，支持多选：

- `.zip` / `.skill`：压缩包中的每个 Skill 目录包含 `SKILL.md`，可带 `scripts/`、`references/`、`assets/` 等相对路径资源。
- `.md`：单独的 `SKILL.md`，只导入说明文件。

最小格式：

```markdown
---
name: explain-python
description: 当用户要求解释 Python 代码时，说明输入、输出、控制流程和关键边界。
---

# Python 代码解释

先读取用户指定的源码，再结合调用方解释数据流；没有读取到的实现要明确说明。
```

名称使用小写字母、数字和单个连字符，最多 64 字符。导入时以 `name` 创建目录；文件中的其他元数据及正文会原样保留。

```text
explain-python/
├── SKILL.md
└── references/
    └── examples.md
```

支持根目录直接包含 `SKILL.md`、外层包装目录和多个 Skill 的集合包。不支持嵌套 Skill 目录。

默认拒绝覆盖同名 Skill；确认需要替换时，勾选“覆盖同名技能”。导入失败会保留原有版本。导入不运行包内脚本，不给 Agent 额外工具或执行权限。

## 网络导入

填写公开的 HTTP/HTTPS `.zip`、`.skill` 或 `SKILL.md` 下载链接，点击“下载并导入”。GitHub/Gitee 仓库首页不是下载文件，需要先取得归档或原始文件链接。

下载会检查目标地址和重定向，不支持本机、内网、账号密码 URL。单个上传/下载文件最多 10 MB，压缩包展开最多 40 MB、500 个条目。不接受符号链接、路径穿越或加密压缩包。

## 生效方式

技能存放在原有工作区的 `skills/<name>/SKILL.md`，Agent 路径仍为 `/skills/<name>/SKILL.md`。每轮任务开始刷新技能索引；名称和描述先进入索引，完整说明与资源按需读取。新导入的技能从下一轮任务开始可用，不修改正在执行的任务计划。

## API

- `GET /dashboard/api/skills`：已安装技能列表、格式状态。
- `POST /dashboard/api/skills/upload?filename=example.zip&replace=false`：请求体为文件原始二进制，Content-Type 为 `application/octet-stream`。
- `POST /dashboard/api/skills/import-url`：JSON `{"url":"https://example.com/skill.zip","replace":false}`。

管理接口沿用现有 Dashboard 的访问保护。Linux 部署仍通过现有 Nginx 登录保护；本地服务仍绑定回环地址。Skill 管理不会成为 Agent 可自动调用的安装工具。
