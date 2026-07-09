# -*- coding: utf-8 -*-
import json
import os
import subprocess
import xml.etree.ElementTree as ET

QUESTION_ID = "8cc1b56d969e4224984ca5862b6d7169"
MYSQL = r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe"

TABLES = [
    "hermes_persona_questions",
    "hermes_persona_reports",
    "hermes_persona_tool_outputs",
    "maigret_persona_accounts",
    "maigret_persona_scans",
    "ocr_persona_results",
    "twitter_persona_profiles",
    "twitter_persona_tweets",
    "vision_persona_results",
    "youtube_persona_channels",
    "youtube_persona_videos",
]


def query_table(table):
    env = os.environ.copy()
    env["MYSQL_PWD"] = "123456"
    sql = f"SELECT * FROM {table} WHERE question_id = '{QUESTION_ID}'"
    proc = subprocess.run(
        [
            MYSQL,
            "--host=127.0.0.1",
            "--user=root",
            "--database=hermes",
            "--default-character-set=utf8mb4",
            "--xml",
            "-e",
            sql,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    text = proc.stdout.strip()
    if not text:
        return []
    root = ET.fromstring(text)
    rows = []
    for row_el in root.findall("row"):
        row = {}
        for field in row_el.findall("field"):
            name = field.attrib.get("name")
            row[name] = field.text
        rows.append(row)
    return rows


def main():
    tables = {table: query_table(table) for table in TABLES}

    categorized = {
        "questionId": QUESTION_ID,
        "summary": {
            "question": len(tables["hermes_persona_questions"]),
            "report": len(tables["hermes_persona_reports"]),
            "toolOutputs": len(tables["hermes_persona_tool_outputs"]),
            "twitter": {
                "profiles": len(tables["twitter_persona_profiles"]),
                "tweets": len(tables["twitter_persona_tweets"]),
            },
            "youtube": {
                "channels": len(tables["youtube_persona_channels"]),
                "videos": len(tables["youtube_persona_videos"]),
            },
            "maigret": {
                "scans": len(tables["maigret_persona_scans"]),
                "accounts": len(tables["maigret_persona_accounts"]),
            },
            "ocr": len(tables["ocr_persona_results"]),
            "vision": len(tables["vision_persona_results"]),
        },
        "question": tables["hermes_persona_questions"],
        "report": tables["hermes_persona_reports"],
        "toolOutputs": tables["hermes_persona_tool_outputs"],
        "profile": {
            "twitter": tables["twitter_persona_profiles"],
            "youtube": tables["youtube_persona_channels"],
        },
        "data": {
            "twitter_tweets": tables["twitter_persona_tweets"],
            "youtube_videos": tables["youtube_persona_videos"],
            "maigret_scans": tables["maigret_persona_scans"],
            "maigret_accounts": tables["maigret_persona_accounts"],
            "ocr_results": tables["ocr_persona_results"],
            "vision_results": tables["vision_persona_results"],
        },
    }

    out_path = r"d:\javaProject\javaProject\hermes\question_data_export.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(categorized, f, ensure_ascii=False, indent=2)

    def trunc(obj, max_len=400):
        if isinstance(obj, dict):
            return {k: trunc(v, max_len) for k, v in obj.items()}
        if isinstance(obj, list):
            return [trunc(i, max_len) for i in obj]
        if isinstance(obj, str) and len(obj) > max_len:
            return obj[:max_len] + f"...(truncated,len={len(obj)})"
        return obj

    preview_path = r"d:\javaProject\javaProject\hermes\question_data_export_preview.json"
    with open(preview_path, "w", encoding="utf-8") as f:
        json.dump(trunc(categorized), f, ensure_ascii=False, indent=2)
    print(out_path)


if __name__ == "__main__":
    main()
