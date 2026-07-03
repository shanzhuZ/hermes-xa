"""模拟 Hermes stdio 托管的 MCP 进程内调用 collect_accounts。"""
import json
import os
import sys
import time

from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MAIGRET_REPORTS_DIR", r"C:/Users/zhr/AppData/Local/hermes/maigret-reports")
os.environ.setdefault("MAIGRET_PROXY", "http://127.0.0.1:7897")
os.environ.setdefault("MAIGRET_MAX_TIMEOUT", "180")

from server import _collect_accounts_impl

t0 = time.time()
r = _collect_accounts_impl(username="whyyoutouzhele", top_sites=8, timeout=120)
print(
    json.dumps(
        {
            "found": r.get("summary", {}).get("found_count"),
            "timed_out": r.get("timed_out"),
            "elapsed": round(time.time() - t0, 1),
            "stdout_log_size": len(
                open(
                    os.path.join(r["report_dir"], "_maigret_stdout.log"),
                    encoding="utf-8",
                    errors="replace",
                ).read()
                if r.get("report_dir")
                and os.path.exists(os.path.join(r["report_dir"], "_maigret_stdout.log"))
                else ""
            ),
        },
        ensure_ascii=False,
    )
)
