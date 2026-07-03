#!/usr/bin/env node
/**
 * Hermes stdio 入口：上游仓库已迁 HTTP/Smithery，本地用 stdio 接入 Hermes MCP 客户端。
 */
import 'dotenv/config';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import createServer from './index.js';

function configureWindowsUtf8(): void {
  if (process.platform !== 'win32') return;
  process.env.PYTHONUTF8 = '1';
  for (const stream of [process.stdout, process.stderr]) {
    const reconf = (stream as NodeJS.WriteStream & { reconfigure?: (o: object) => void }).reconfigure;
    if (reconf) {
      try {
        reconf.call(stream, { encoding: 'utf8', errors: 'replace' });
      } catch {
        /* ignore */
      }
    }
  }
}

configureWindowsUtf8();

const apiKey = process.env.YOUTUBE_API_KEY?.trim();
if (!apiKey) {
  process.stderr.write('缺少环境变量 YOUTUBE_API_KEY（YouTube Data API v3）\n');
  process.exit(1);
}

const server = createServer({
  config: {
    youtubeApiKey: apiKey,
    port: process.env.PORT || '3000',
  },
});

const transport = new StdioServerTransport();
await server.connect(transport);
