#!/usr/bin/env python3
"""从 output/qwe(1).py 生成 collect_step_demo_records 建表与入库 SQL。"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "output" / "qwe(1).py"
OUT = ROOT / "scripts" / "sql" / "020_collect_step_demo_records.sql"


def esc_sql(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "''")


def load_items():
    text = SRC.read_text(encoding="utf-8")
    items = []
    for m in re.finditer(r"^(d\d+)\s*=\s*(\{.*?\n\})\s*$", text, re.M | re.S):
        obj = eval(m.group(2), {"__builtins__": {}})
        items.append(obj)
    return items


def main():
    items = load_items()
    lines = [
        "-- 写报流程演示假数据（全局共用，按 step_key 查询，与 task_id 无关）",
        "CREATE TABLE IF NOT EXISTS collect_step_demo_records (",
        "    id              BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',",
        "    step_key        VARCHAR(64)   NOT NULL COMMENT '步骤键，如 step6_osint_es',",
        "    record_title    VARCHAR(256)  NOT NULL COMMENT '卡片/节点标题',",
        "    account_id      VARCHAR(128)  NULL     COMMENT '种子账号 ID，便于按账号过滤',",
        "    display_fields  JSON          NOT NULL COMMENT '展示字段 [{label,value}]',",
        "    sort_order      INT           NOT NULL DEFAULT 0 COMMENT '同 step_key 内排序',",
        "    enabled         TINYINT(1)    NOT NULL DEFAULT 1 COMMENT '是否启用',",
        "    created_at      DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',",
        "    updated_at      DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',",
        "    KEY idx_step_key (step_key),",
        "    KEY idx_step_account (step_key, account_id),",
        "    KEY idx_step_sort (step_key, sort_order, id)",
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='写报流程演示假数据';",
        "",
        "INSERT IGNORE INTO schema_migrations (version) VALUES ('020_collect_step_demo_records');",
        "",
        "-- 清空本批 step6 假数据后重灌（可按需注释）",
        "DELETE FROM collect_step_demo_records WHERE step_key IN ('step6_osint_es','step6_geo_verify','step6_rumor_sx','step6_relation_graph');",
        "",
    ]

    for obj in items:
        step_key = obj["step_key"]
        title = obj["title"]
        account_id = obj.get("account_id") or ""
        sort_order = int(obj.get("sort_order") or 0)
        fields_json = json.dumps(obj["display_fields"], ensure_ascii=False)
        lines.append(
            "INSERT INTO collect_step_demo_records "
            "(step_key, record_title, account_id, display_fields, sort_order) VALUES ("
            f"'{esc_sql(step_key)}', '{esc_sql(title)}', '{esc_sql(account_id)}', "
            f"'{esc_sql(fields_json)}', {sort_order});"
        )

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"written {OUT} records={len(items)}")


if __name__ == "__main__":
    main()
