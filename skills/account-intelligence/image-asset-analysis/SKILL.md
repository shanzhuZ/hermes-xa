---
name: image-asset-analysis
description: "图片资产：发现头像/封面/发文配图→下载写HBase→MySQL索引→OCR/Vision回填。01步骤6后、02步骤3后、04步骤7后必须执行；也可单独对某task重跑。"
version: 0.4.0
author: hermes-xa
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [account-intelligence, image, asset]
    related_skills: [account-intelligence-collect, account-expansion, account-intelligence-report]
---

# 图片资产入库与分析

**语言**：用户中文输入时，全程简体中文。

**01 接入**：`account-intelligence-collect` 在步骤 6 发文结束后、步骤 7 三节报告前，必须执行本管线（见 collect Skill 步骤 6.5）。  
**02 接入**：`account-expansion` 在步骤 3 主页+发文结束后、步骤 4 核查前，必须执行本管线（见 expansion Skill 步骤 3.5）。  
**04 接入**：`account-intelligence-report` 在步骤 7 发文结束后、步骤 8～10 分析前，必须执行本管线（见 report Skill 步骤 7.5）。  
本 Skill 仍可单独对某 `task_id` 重跑/补跑；**不要**改 01/02/04 步骤树完成条件；管线失败**禁止**把整任务标 `failed`。

## 目标

1. 从 MySQL `collect_profiles` / `collect_posts` 发现头像、封面、发文配图。
2. 下载原图写入 HBase（或本地回退目录），MySQL `collect_images` 保存索引与状态。
3. 尽量回填已有 OCR/Vision 工具输出；没有则标记 `analyze_status=skipped`，可稍后 `--force-analyze`。
4. 返回处理摘要 JSON，供主流程或人工确认。

## 启动前

1. 确认已执行迁移：`scripts/sql/012_collect_images.sql`
2. 确认 `.env` 中 MySQL 可用；HBase 走现成 HTTP 入库接口：
   - `HERMES_HBASE_INSERT_URL=http://192.168.3.171:6666/insertHbaseData`
   - `HERMES_HBASE_IMAGE_TABLE=collect_image_bytes`
   - `HERMES_HBASE_COLUMN_FAMILY=info`
   - Java 读图 ZK：`HERMES_HBASE_ZK` 必须指向 insert 实际落库集群
   - `HERMES_HBASE_ENABLED=1`；依赖只需 `requests`
   - 表需预先存在：`create 'collect_image_bytes', 'info'`
3. 工作目录在仓库 `scripts/` 下，或保证 `PYTHONPATH` 含 `scripts`

## 执行步骤（硬顺序）

1. 拿到 `task_id`（用户给出、Gateway `task:{uuid}`，或当前会话活跃任务）。
2. **01 主路径 / 推荐一条命令**（入库 + 分析回填）：

```bash
python -m image_pipeline.run --task-id <taskId> --force-analyze
```

3. 仅入库、暂不分析：

```bash
python -m image_pipeline.run --task-id <taskId> --skip-analyze
```

4. 仅诊断发现、不下载：

```bash
python -m image_pipeline.run --task-id <taskId> --discover-only
```

5. 下载失败重试：

```bash
python -m image_pipeline.run --task-id <taskId> --retry-failed --force-analyze
```

6. 将命令 stdout 的 JSON 摘要原样返回（01 主流程最多 1 句进度，勿写成报告）。

## 返回摘要字段

```json
{
  "taskId": "...",
  "discovered": 20,
  "stored": 18,
  "analyzed": 17,
  "storageFailed": 2,
  "analyzeFailed": 0,
  "profileImages": 4,
  "postImages": 16
}
```

## 铁律

1. **禁止**把图片管线失败升级为整任务 `failed`。
2. **禁止**在 Skill 或脚本里硬编码 HBase/MySQL 密码；只用 `.env`。
3. 原图只通过 Java `GET /api/images/{imageId}/bytes` 给前端，禁止前端直连 HBase。
4. 视频入库本期不做；发现阶段应跳过明显视频 URL。
5. 01 步骤树**不新增**「图片入库」节点；6.5 由 Agent 用 terminal 执行本管线即可。

## 与现有 OCR/Vision 关系

- 01 步骤 4 图片流（头像 OCR/Vision）继续跑。
- 本模块优先**回填** `hermes_tool_outputs` 中已有 OCR/Vision 结果到 `collect_images`。
- 无既有分析结果时标记 `analyze_status=skipped`，可用 `--force-analyze` 重试。

## 联调检查

- MySQL：`SELECT image_id, source_type, storage_status, analyze_status FROM collect_images WHERE task_id=...`
- 本地回退：`data/image_bytes/` 下是否有对应 `.bin`
- Java：`GET /api/tasks/{taskId}/images`、`GET /api/images/{imageId}/bytes`
