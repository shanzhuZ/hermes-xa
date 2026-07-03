# hermes-xa

账号智能分析平台的 Hermes Agent 部署与业务配置仓库。

## 仓库内容

| 目录/文件 | 说明 |
|-----------|------|
| `docs/` | 平台设计、Clarify 交互指南、五类业务流程 |
| `mcp/` | MCP Server 源码与 `mcp_servers.yaml` 连接配置 |
| `skills/` | Skill（含后续 `account-intelligence` 业务 Skill） |
| `hooks/` | Hook 脚本 |
| `config.yaml` | Hermes 主配置（模型、MCP、toolsets） |
| `SOUL.md` | Agent 人格与全局约束 |
| `install.ps1` | Hermes 官方安装脚本 |

> `hermes-agent/` 核心程序通过 `install.ps1` 安装，不纳入本仓库（体积过大）。克隆后需重新执行安装。

## 快速开始

```powershell
# 1. 克隆
git clone https://github.com/shanzhuZ/hermes-xa.git
cd hermes-xa

# 2. 安装 Hermes Agent（若尚未安装）
.\install.ps1 -NonInteractive -SkipSetup
hermes setup

# 3. 配置环境变量
copy .env.example .env
# 编辑 .env，填写 API 密钥、WEIBO_COOKIE 等

# 4. 合并 MCP 配置
.\mcp\install.ps1

# 5. 启动
hermes chat
# 或 hermes gateway（启用 API Server 后供 Java 对接）
```

## 文档

- [平台总览与框架设计](docs/平台总览与框架设计.md)
- [Clarify 深度交互与 Skill 集成指南](docs/clarify深度交互与Skill集成指南.md)
- [业务流程 workflows](docs/workflows/)

## 环境要求

- Windows 10+
- Python 3.10+、Node.js（部分 MCP）
- 内网 LLM：`http://172.200.200.4:7000/v1`（可在 `config.yaml` 修改）
