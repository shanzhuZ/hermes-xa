# 🎬 Bilibili MCP Server

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)
[![Stars](https://img.shields.io/github/stars/your-github-username/bilibili-mcp-server?style=social)](https://github.com/your-github-username/bilibili-mcp-server)

一个基于 Bilibili 公开 API 的 MCP Server，让 AI Agent 能搜索视频并读取公开视频与 UP 主信息。  
An MCP server powered by public Bilibili APIs so AI agents can search videos and read public video and creator metadata.

<!-- demo gif here -->

## Features

- No login, no cookie, public APIs only
- Built with Python and FastMCP
- Ready for Claude Desktop and Cursor
- Includes basic error handling for timeout, HTTP failures, and Bilibili API errors

## Installation

### Option 1: pip

```bash
git clone https://github.com/12qw3er4ty5u-collab/bilibili-mcp-server.git
cd bilibili-mcp-server
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

macOS / Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Run:

```bash
python server.py
```

### Option 2: uv

```bash
git clone https://github.com/12qw3er4ty5u-collab/bilibili-mcp-server.git
cd bilibili-mcp-server
uv venv
```

Windows:

```bash
.venv\Scripts\activate
uv pip install -r requirements.txt
copy .env.example .env
```

macOS / Linux:

```bash
source .venv/bin/activate
uv pip install -r requirements.txt
cp .env.example .env
```

Run:

```bash
uv run server.py
```

## Configuration

### Claude Desktop

Add the following snippet to your Claude Desktop MCP config:

```json
{
  "mcpServers": {
    "bilibili-mcp-server": {
      "command": "python",
      "args": ["server.py"],
      "cwd": "./bilibili-mcp-server"
    }
  }
}
```

If you prefer `uv`:

```json
{
  "mcpServers": {
    "bilibili-mcp-server": {
      "command": "uv",
      "args": ["run", "server.py"],
      "cwd": "./bilibili-mcp-server"
    }
  }
}
```

### Cursor

Add the following snippet to your Cursor MCP config:

```json
{
  "mcpServers": {
    "bilibili-mcp-server": {
      "command": "python",
      "args": ["server.py"],
      "cwd": "./bilibili-mcp-server"
    }
  }
}
```

If you prefer `uv`:

```json
{
  "mcpServers": {
    "bilibili-mcp-server": {
      "command": "uv",
      "args": ["run", "server.py"],
      "cwd": "./bilibili-mcp-server"
    }
  }
}
```

## Tools

| Tool | Description | Parameters | Returns |
| --- | --- | --- | --- |
| `search_videos` | Search public Bilibili videos by keyword. / 按关键词搜索公开视频。 | `keyword: str`, `limit: int = 10` | Video list with title, link, play count, and `bvid` |
| `get_video_info` | Get one video's public details by `bvid`. / 按 `bvid` 获取视频详情。 | `bvid: str` | Title, description, uploader, play count, and link |
| `get_user_info` | Get a creator's public profile by `uid`. / 按 `uid` 获取 UP 主信息。 | `uid: int` | Name, follower count, bio, and profile URL |

## Public APIs Used

- Search: `https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword=...`
- Video info: `https://api.bilibili.com/x/web-interface/view?bvid=...`
- User info: `https://api.bilibili.com/x/web-interface/card?mid=...`

This project does not use login, cookies, or private endpoints.  
本项目不使用登录、Cookie 或任何私有接口。

## Contributing

Issues and pull requests are welcome.  
欢迎提交 Issue 和 Pull Request。

## License

MIT License. See [LICENSE](./LICENSE) for details.
