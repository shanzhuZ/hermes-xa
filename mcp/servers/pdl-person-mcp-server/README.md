# 全球人员信息检索-PDL MCP

上游：[People Data Labs Person Search API](https://docs.peopledatalabs.com/docs/person-search-api)  
封装模式：自研 FastMCP（HTTP API）

## 工具

| Hermes 工具名 | 用途 |
|---------------|------|
| `mcp_pdl_person_search_person` | 按姓名/社交链接/邮箱/电话等检索全球人员档案 |

常用参数：`social_link`、`first_name`/`last_name`、`input_email`、`input_phone`、`company_name`、`max_num`（默认 1）。

示例：`social_link=www.twitter.com/elonmusk`

## 依赖

```powershell
D:/environment/python/python.exe -m pip install -r D:/hermes-xa/mcp/servers/pdl-person-mcp-server/requirements.txt
```

## 配置

`mcp/mcp_servers.yaml` → `pdl_person`：

| 环境变量 | 含义 |
|----------|------|
| `PDL_API_KEY` | People Data Labs API Key（必填） |
| `PDL_PROXY` / `HTTPS_PROXY` | 可选代理 |
| `PDL_TIMEOUT` | 请求超时秒数，默认 60 |
| `PDL_MAX_SIZE` | max_num 上限，默认 25 |

`.env` 增加：

```env
PDL_API_KEY=你的密钥
```

## 运维

```powershell
cd D:\hermes-xa\mcp
powershell -ExecutionPolicy Bypass -File .\install.ps1
# Hermes 会话内：/reload-mcp
# 或：hermes mcp test pdl_person
```

## 说明

- MCP 只返回 PDL 结构化结果；业务流程写在 Skill，不在本服务写画像模板。
- 与 Maigret / user-scanner / ES 社工库互补：PDL 偏商业人员档案（邮箱、经历、社交聚合）。
