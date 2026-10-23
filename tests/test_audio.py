"""音频链路：mp3 → 本地识别 → 翻译 → 双语字幕。"""
from __future__ import annotations

import os
import sys
import time

import os as _os
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    _os.environ.pop(_k, None)      # 本机请求不能被环境代理截走
import requests

BASE = "http://127.0.0.1:8791"
MP3 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_speech.mp3")

if not os.path.exists(MP3):
    print("缺少测试音频，先跑 tests/gen_speech.py")
    sys.exit(2)

print("上传", MP3, os.path.getsize(MP3) // 1024, "KB")
with open(MP3, "rb") as f:
    r = requests.post(BASE + "/api/jobs", files={"file": ("英文演讲.mp3", f, "audio/mpeg")},
                      data={"mode": "natural", "engine": "google", "asr_model": "small",
                            "source_lang": "en", "target_lang": "zh-CN",
                            "out_format": "srt", "bilingual": "1",
                            "proxy": "http://127.0.0.1:33210"}, timeout=60)
jid = r.json().get("id")
print("任务", jid)

last = None
t0 = time.time()
while time.time() - t0 < 600:
    s = requests.get(f"{BASE}/api/jobs/{jid}", timeout=10).json()
    tag = f"{s['stage']} {int(s['progress']*100)}%"
    if tag != last:
        print("   ", tag, "|", s.get("message", ""))
        last = tag
    if s["status"] in ("done", "error", "cancelled"):
        break
    time.sleep(1.0)

assert s["status"] == "done", f"{s['status']} {s.get('error')} {s.get('logs')[-5:]}"
res = s["result"]
print(f"\n用时 {res['elapsed']}s，识别 {res['source_count']} 条 → 出 {res['count']} 条")
for c in res["cues"]:
    print(f"  {c['start']:6.2f}-{c['end']:6.2f}  {c['en']}")
    print(f"                    {c['zh']}")
print("warnings:", res["warnings"])
print("files:", [f["name"] for f in res["files"]])
