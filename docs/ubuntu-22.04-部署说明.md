# Ubuntu 22.04 部署说明（分支 `deploy/ubuntu-22.04`）

约定根目录：**`/opt/hermes-xa`**  
端口与 Windows 一致：Java **4377**，Hermes Gateway API **8642**。  
本分支默认 **关闭 HBase**（`HERMES_HBASE_ENABLED=0`），MySQL/ES 连接在上线时改 `.env` 与 `application.yml`。

Windows 主分支请继续用 `backup/2026-08-19-local`，不要把本分支路径合回 Windows。

---

## 1. 拉代码

```bash
sudo mkdir -p /opt/hermes-xa
sudo chown "$USER:$USER" /opt/hermes-xa
git clone -b deploy/ubuntu-22.04 https://github.com/shanzhuZ/hermes-xa.git /opt/hermes-xa
cd /opt/hermes-xa
```

## 2. 系统依赖

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip nodejs npm \
  tesseract-ocr tesseract-ocr-chi-sim ffmpeg build-essential
# Java 8 / Maven 若已装可跳过
java -version
mvn -version
node -v
```

## 3. Python venv 与 MCP 包

```bash
cd /opt/hermes-xa
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
# Hermes Agent：按官方 Linux 安装方式装进该 venv（install.ps1 仅 Windows）
# 示例（以你们实际官方文档为准）：
# pip install hermes-agent
# 或克隆 hermes-agent 后 pip install -e .

pip install mcp-server-weibo weixin_search_mcp "user-scanner[mcp]" \
  -r mcp/servers/pdl-person-mcp-server/requirements.txt \
  -r mcp/servers/user-scanner-mcp-server/requirements.txt
# 其余自研 MCP 按各目录 requirements.txt 补装（maigret/twitter/ocr/bilibili/weixin-read 等）
```

Node 热榜等走 `npx -y`，首次启动会拉包。也可预先：

```bash
sudo npm install -g mcp-trends-hub reddit-mcp-buddy firecrawl-mcp \
  @playwright/mcp @apify/actors-mcp-server
```

YouTube MCP 需已构建 `mcp/servers/youtube-mcp-server/dist/stdio-main.js`；若没有：

```bash
cd /opt/hermes-xa/mcp/servers/youtube-mcp-server
npm install && npm run build
```

## 4. 环境变量

从 Windows 拷 `.env` 到 `/opt/hermes-xa/.env`，至少改这些：

```bash
HERMES_IMAGE_LOCAL_DIR=/opt/hermes-xa/data/image_bytes
HERMES_VIDEO_LOCAL_DIR=/opt/hermes-xa/data/video_bytes
HERMES_HBASE_ENABLED=0
# MySQL / ES 改成这台机器实际地址后再开业务
```

Twitter Cookie 放到：`/opt/hermes-xa/mcp/data/twitter-cookies.json`

Gateway API（Java 调 Agent）在 `.env` 打开：

```bash
API_SERVER_ENABLED=true
API_SERVER_HOST=127.0.0.1
API_SERVER_PORT=8642
API_SERVER_KEY=至少16位
```

## 5. 合并 MCP 配置（路径已是 Linux）

```bash
chmod +x /opt/hermes-xa/mcp/install.sh /opt/hermes-xa/scripts/hermes.sh
python3 /opt/hermes-xa/scripts/sync_mcp_servers.py
```

`config.yaml` 里 Hook 与 MCP 均指向 `/opt/hermes-xa/.venv/bin/python`。  
若 conda 不用 venv：把 YAML 里该路径改成 `which python` 的结果，并设 `HERMES_PYTHON`。

## 6. systemd（不设开机自启）

```bash
mkdir -p /opt/hermes-xa/logs /opt/hermes-xa/data/image_bytes /opt/hermes-xa/data/video_bytes
sudo cp /opt/hermes-xa/deploy/ubuntu-22.04/*.service /etc/systemd/system/
# 确认 User= 为实际用户（当前示例 root1）
sudo systemctl daemon-reload
sudo systemctl start hermes-gateway.service
sudo systemctl start hermes-java.service
sudo systemctl status hermes-gateway.service hermes-java.service --no-pager
```

`WantedBy=` 为空，`enable` 也不会开机拉起。手动：`systemctl start/stop`。

Java 也可先不写 unit，改用：

```bash
chmod +x /opt/hermes-xa/clients/hermes-xa/start.sh
/opt/hermes-xa/clients/hermes-xa/start.sh
```

需先确认 `pom.xml` 打出的 jar 名；若不是 `target/hermes-xa.jar`，改 `hermes-java.service` 的 `ExecStart`。

## 7. MySQL / ES（后做）

建表：按序号执行 `scripts/sql/001_*.sql` …（不要跑已删除的 `022_collect_pdl_hits`）。  
改 `.env` 的 `HERMES_DB_*`、`ES_*`，以及 `clients/hermes-xa/src/main/resources/application.yml` 数据源后重新打包 Java。

## 8. 自检

```bash
ss -lntp | grep -E '4377|8642'
curl -sS -m 3 http://127.0.0.1:4377/ || true
HERMES_HOME=/opt/hermes-xa /opt/hermes-xa/scripts/hermes.sh mcp list
```

HBase 以后再开：`.env` 设 `HERMES_HBASE_ENABLED=1` 并确认 ZK / 入库 HTTP。
