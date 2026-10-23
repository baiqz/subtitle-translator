"""接口层验证：上传 → 建任务 → 进度 → 出文件。"""
from __future__ import annotations

import json
import os
import sys
import time

import os as _os
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    _os.environ.pop(_k, None)      # 本机请求不能被环境代理截走
import requests

BASE = "http://127.0.0.1:8791"

SRT = """1
00:00:00,320 --> 00:00:02,480
- So I've been thinking about

2
00:00:02,480 --> 00:00:04,120
this for a while now, and

3
00:00:04,120 --> 00:00:06,000
I think the answer is pretty

4
00:00:06,000 --> 00:00:07,800
simple. It's not about

5
00:00:07,800 --> 00:00:09,600
working harder.

6
00:00:09,600 --> 00:00:11,200
It's about the right thing.

7
00:00:11,200 --> 00:00:13,400
Right. Nobody tells you this.
"""

fails = []


def check(cond, msg):
    print(("  ok  " if cond else "  x   ") + msg)
    if not cond:
        fails.append(msg)


print("1) 通道检测")
r = requests.post(BASE + "/api/test", json={"proxy": "http://127.0.0.1:33210"}, timeout=30)
d = r.json()
check(d.get("ok") is True, f"翻译通道：{d}")

print("2) 建任务并跑完")
p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_input.srt")
open(p, "w", encoding="utf-8").write(SRT)
with open(p, "rb") as f:
    r = requests.post(BASE + "/api/jobs", files={"file": ("测试字幕A.srt", f, "text/plain")},
                      data={"mode": "natural", "engine": "google", "target_lang": "zh-CN",
                            "out_format": "srt", "bilingual": "1", "zh_first": "0",
                            "zh_line": "18", "en_line": "42",
                            "proxy": "http://127.0.0.1:33210"}, timeout=60)
check(r.status_code == 200, f"建任务 HTTP {r.status_code} {r.text[:200]}")
jid = r.json().get("id")
check(bool(jid), f"拿到任务 id：{jid}")

last = None
t0 = time.time()
while time.time() - t0 < 120:
    s = requests.get(f"{BASE}/api/jobs/{jid}", timeout=10).json()
    stage = f"{s['stage']} {int(s['progress']*100)}%"
    if stage != last:
        print("     ·", stage, s.get("message", ""))
        last = stage
    if s["status"] in ("done", "error", "cancelled"):
        break
    time.sleep(0.6)

check(s["status"] == "done", f"任务状态 {s['status']} {s.get('error','')}")
res = s.get("result") or {}
check(res.get("count", 0) > 0, f"产出条目 {res.get('count')}（原片 {res.get('source_count')}）")
check(len(res.get("files", [])) >= 2, f"文件 {[f['name'] for f in res.get('files', [])]}")

print("3) 下载并抽检")
fn = res["files"][0]["name"]
r = requests.get(f"{BASE}/api/jobs/{jid}/file/{requests.utils.quote(fn)}", timeout=30)
check(r.status_code == 200 and len(r.content) > 50, f"下载 {fn}，{len(r.content)} 字节")
body = r.content.decode("utf-8")
print("    ---- 前 12 行 ----")
print("    " + "\n    ".join(body.splitlines()[:12]))

zh_only = [x for x in res["cues"] if x["zh"].strip()]
check(len(zh_only) == len(res["cues"]), "每条都有译文")
check(all(any("\u4e00" <= c <= "\u9fff" for c in x["zh"]) for x in zh_only), "译文都是中文")

print("4) ZIP 打包")
z = requests.get(f"{BASE}/api/jobs/{jid}/zip", timeout=30)
check(z.status_code == 200 and z.content[:2] == b"PK", f"zip {len(z.content)} 字节")

print("5) 错误处理")
r = requests.post(BASE + "/api/jobs", files={"file": ("x.exe", b"MZ", "application/octet")}, timeout=20)
check(r.status_code == 400, f"拒收不支持的格式：{r.status_code}")
r = requests.get(BASE + "/api/jobs/nope", timeout=10)
check(r.status_code == 404, "不存在的任务返回 404")
r = requests.get(f"{BASE}/api/jobs/{jid}/file/..%2f..%2fapp.py", timeout=10)
check(r.status_code in (400, 404), f"目录穿越被挡：{r.status_code}")

os.remove(p)
print("=" * 50)
if fails:
    print("FAIL", len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("PASS 接口层全过")
