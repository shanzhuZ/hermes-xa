---
name: image-asset-analysis
description: "独立图片资产：发现头像/封面/发文配图→下载写入HBase→MySQL索引→OCR/Vision结果回填。不替代01-04主流程；第一期手动或单独调用。"
version: 0.1.0
author: hermes-xa
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [account-intelligence, image, asset]
    related_skills: []
---

# 图片资产入库与分析

**语言**：用户中文输入时，全程简体中文。

本 Skill **独立于** `account-intelligence-collect` / expand / verify / report。  
**第一期不要改 01～04 步骤树或完成条件**；仅在明确收到「对某 task 跑图片入库」指令时执行。

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

1. 拿到 `task_id`（用户给出或当前会话活跃任务）。
2. 运行管线（推荐先只入库）：

```bash
python -m image_pipeline.run --task-id <taskId> --skip-analyze
```

3. 若任务内已有 OCR/Vision 工具输出，再跑分析回填：

```bash
python -m image_pipeline.run --task-id <taskId> --force-analyze
```

4. 仅诊断发现、不下载：

```bash
python -m image_pipeline.run --task-id <taskId> --discover-only
```

5. 下载失败重试：

```bash
python -m image_pipeline.run --task-id <taskId> --retry-failed --skip-analyze
```

6. 将命令 stdout 的 JSON 摘要原样返回给用户（字段见下）。

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

1. **禁止**修改 01～04 Skill 步骤、Hook 完成条件、步骤树状态机（除非用户明确要求接入）。
2. **禁止**把图片管线失败升级为整任务 `failed`（第一期）。
3. **禁止**在 Skill 或脚本里硬编码 HBase/MySQL 密码；只用 `.env`。
4. 原图只通过 Java `GET /api/images/{imageId}/bytes` 给前端，禁止前端直连 HBase。
5. 视频入库本期不做；发现阶段应跳过明显视频 URL。

## 与现有 OCR/Vision 关系

- 01～04 现有图片流可继续跑。
- 本模块优先**回填** `hermes_tool_outputs` 中已有 OCR/Vision 结果到 `collect_images`。
- 后续接入主流程时，再改为由本 Skill 主动调 OCR/Vision 并写库。

## 联调检查

- MySQL：`SELECT image_id, source_type, storage_status, analyze_status FROM collect_images WHERE task_id=...`
- 本地回退：`data/image_bytes/` 下是否有对应 `.bin`
- Java：`GET /api/tasks/{taskId}/images`、`GET /api/images/{imageId}/bytes`
