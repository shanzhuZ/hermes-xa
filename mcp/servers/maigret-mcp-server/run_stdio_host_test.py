"""从带 PIPE 的父进程启动 test_stdio_host（模拟 Hermes MCP stdio）。"""
import subprocess
import sys
from pathlib import Path

host = Path(__file__).resolve().parent / "test_stdio_host.py"
flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
proc = subprocess.Popen(
    [sys.executable, "-u", str(host)],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    creationflags=flags,
)
out, err = proc.communicate(timeout=200)
print(out.decode("utf-8", errors="replace"))
if err:
    print("STDERR:", err.decode("utf-8", errors="replace")[-800:])
