# user-scanner MCP

上游：[kaifcodec/user-scanner](https://github.com/kaifcodec/user-scanner)  
封装模式：pip 包 + 薄启动器（与 OCR/Twitter 同类）

## 工具

| Hermes 工具名 | 用途 |
|---------------|------|
| `mcp_user_scanner_scan_username` | 用户名跨平台 OSINT（465+ 向量） |
| `mcp_user_scanner_scan_email` | 邮箱注册探测与关联 |
| `mcp_user_scanner_list_available_modules` | 列出可用站点/分类模块 |

常用参数：`cross_scan`、`category` / `module`、`proxies`、`allow_loud`（默认 false，勿对目标乱开）。

**hermes-xa 启动器默认行为：**

- 工具未传 `proxies` 时，自动把 `USER_SCANNER_PROXY`（或 `HTTPS_PROXY`）注入上游 `set_proxy_manager`（上游**不读**系统 `HTTP_PROXY`）
- 工具未传 `cross_scan` 时，默认 `true`（`USER_SCANNER_CROSS_SCAN=0` 可关）

## 依赖

```powershell
D:/environment/python/python.exe -m pip install "user-scanner[mcp]"
```

## 配置

`mcp/mcp_servers.yaml` → `user_scanner`：

| 环境变量 | 含义 |
|----------|------|
| `USER_SCANNER_PROXY` / `HTTPS_PROXY` | 扫描代理（注入到工具 proxies） |
| `USER_SCANNER_CROSS_SCAN` | 默认 `1`：未传参时开 cross_scan |
| `PYTHONUTF8` | Windows UTF-8 |

全量扫描耗时长，`supports_parallel_tool_calls: false`，`timeout: 600`。

## 运维

```powershell
cd D:\hermes-xa\mcp
powershell -ExecutionPolicy Bypass -File .\install.ps1
# Hermes 会话内：/reload-mcp
# 或：hermes mcp test user_scanner
```

## 说明

- MCP 只返回结构化扫描结果；业务流程写在 Skill，不在本启动器写画像模板。
- 与 Maigret 互补：Maigret 偏用户名存在性；user-scanner 偏邮箱+元数据+cross-scan。
