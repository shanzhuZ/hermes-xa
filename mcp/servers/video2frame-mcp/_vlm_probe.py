import time, requests, sys
from pathlib import Path
sys.path.insert(0, r"D:/hermes-xa/mcp/servers/video2frame-mcp")
from config import vlm_config
cfg = vlm_config()
print("api", cfg["api_url"], "model", cfg["model"], "concurrency", cfg["concurrency"])
# tiny ping without image
t0=time.time()
try:
    r = requests.post(cfg["api_url"], json={
        "model": cfg["model"],
        "messages":[{"role":"user","content":"ping, reply ok"}],
        "max_tokens": 8,
        "stream": False,
    }, timeout=30)
    print("text ping", r.status_code, round(time.time()-t0,2), "s", r.text[:200])
except Exception as e:
    print("text ping FAIL", round(time.time()-t0,2), e)

# one real frame
fp = Path(r"D:/hermes-xa/data/video_bytes/test_video_001/vid_6f3e42c7682b72a313b8d295/frames/frame_0000_t0.00s.jpg")
print("frame exists", fp.exists(), "size", fp.stat().st_size if fp.exists() else None)
if fp.exists():
    from analyze import analyze_single_frame
    t1=time.time()
    out = analyze_single_frame(str(fp))
    print("frame analyze", round(time.time()-t1,2), "s", {k:out.get(k) for k in ["success","latency_sec","error","description"] if k in out or True})
    if out.get("description"):
        print("desc", (out["description"] or "")[:120])
