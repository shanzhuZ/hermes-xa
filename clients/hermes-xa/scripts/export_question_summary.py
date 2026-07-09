# -*- coding: utf-8 -*-
import json
import os
import subprocess
import xml.etree.ElementTree as ET

QID = "8cc1b56d969e4224984ca5862b6d7169"
MYSQL = r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql.exe"


def query(sql):
    env = os.environ.copy()
    env["MYSQL_PWD"] = "123456"
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
    root = ET.fromstring(proc.stdout)
    rows = []
    for row_el in root.findall("row"):
        row = {}
        for field in row_el.findall("field"):
            row[field.attrib.get("name")] = field.text
        rows.append(row)
    return rows


def main():
    question = query(
        "SELECT question_id, session_id, user_question, platform, account, task_status, "
        "error_message, DATE_FORMAT(asked_at, '%Y-%m-%d %H:%i:%s') asked_at, "
        "DATE_FORMAT(completed_at, '%Y-%m-%d %H:%i:%s') completed_at "
        f"FROM hermes_persona_questions WHERE question_id = '{QID}'"
    )[0]

    report_rows = query(
        "SELECT question_id, session_id, LEFT(assistant_report, 300) assistant_report_preview, "
        "model, DATE_FORMAT(reported_at, '%Y-%m-%d %H:%i:%s') reported_at "
        f"FROM hermes_persona_reports WHERE question_id = '{QID}'"
    )

    tools = query(
        "SELECT id, mcp_server, tool_name, status, duration_ms, "
        "DATE_FORMAT(executed_at, '%Y-%m-%d %H:%i:%s') executed_at "
        f"FROM hermes_persona_tool_outputs WHERE question_id = '{QID}' ORDER BY id"
    )

    twitter_profile = query(
        "SELECT screen_name, display_name, followers_count, following_count, tweets_count, "
        "location, verified, account_created_at "
        f"FROM twitter_persona_profiles WHERE question_id = '{QID}'"
    )

    maigret_scan = query(f"SELECT * FROM maigret_persona_scans WHERE question_id = '{QID}'")
    maigret_accounts = query(
        "SELECT id, sitename, account_url, platform_username "
        f"FROM maigret_persona_accounts WHERE question_id = '{QID}'"
    )

    vision = query(
        "SELECT id, success, LEFT(analysis_text, 200) analysis_preview, "
        "DATE_FORMAT(collected_at, '%Y-%m-%d %H:%i:%s') collected_at "
        f"FROM vision_persona_results WHERE question_id = '{QID}'"
    )

    tweets = query(
        "SELECT id, tweet_id, tweet_date, theme, post_type, likes, retweets, replies, "
        "LEFT(text, 120) text_preview "
        f"FROM twitter_persona_tweets WHERE question_id = '{QID}' ORDER BY id LIMIT 5"
    )
    tweet_count = len(query(f"SELECT id FROM twitter_persona_tweets WHERE question_id = '{QID}'"))

    result = {
        "questionId": QID,
        "summary": {
            "question": 1,
            "report": len(report_rows),
            "toolOutputs": len(tools),
            "twitter": {"profiles": len(twitter_profile), "tweets": tweet_count},
            "youtube": {"channels": 0, "videos": 0},
            "maigret": {"scans": len(maigret_scan), "accounts": len(maigret_accounts)},
            "ocr": 0,
            "vision": len(vision),
        },
        "question": question,
        "report": report_rows[0] if report_rows else None,
        "toolOutputs": tools,
        "profile": {"twitter": twitter_profile},
        "data": {
            "maigret_scan": maigret_scan,
            "maigret_accounts": maigret_accounts,
            "vision_results": vision,
            "twitter_tweets_sample": tweets,
            "twitter_tweets_total": tweet_count,
        },
    }

    out = r"d:\javaProject\javaProject\hermes\question_summary.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(out)


if __name__ == "__main__":
    main()
