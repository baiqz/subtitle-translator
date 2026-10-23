import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import jobs, pipeline, config

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
It's about working on the right

7
00:00:11,200 --> 00:00:12,900
thing. And I learned that the

8
00:00:12,900 --> 00:00:14,500
hard way, believe me.

9
00:00:14,500 --> 00:00:16,000
Right.

10
00:00:16,000 --> 00:00:18,400
You know what? Nobody tells you

11
00:00:18,400 --> 00:00:20,600
this when you're twenty-five.
"""

path = os.path.join(config.WORK_DIR, "probe.srt")
with open(path, "w", encoding="utf-8") as f:
    f.write(SRT)

for mode in ("natural", "strict"):
    job = jobs.create({"mode": mode})
    t = time.time()
    res = pipeline.run(job, path, "probe.srt",
                       {"mode": mode, "engine": "google",
                        "proxy": "http://127.0.0.1:33210",
                        "target_lang": "zh-CN", "out_format": "srt", "bilingual": True})
    print("="*70)
    print(f"MODE={mode}  {time.time()-t:.1f}s  cues={res['count']} (src {res['source_count']})")
    print("files:", [f["name"] for f in res["files"]])
    print("warnings:", res["warnings"])
    for c in res["cues"]:
        print(f"  {c['start']:6.2f}-{c['end']:6.2f}")
        print(f"    EN {c['en']}")
        print(f"    ZH {c['zh']}")
    if res["files"]:
        p = os.path.join(config.OUT_DIR, job.id, res["files"][0]["name"])
        print("---- file head ----")
        print(open(p, encoding="utf-8").read()[:700])
