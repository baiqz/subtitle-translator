"""静态自检：语法、id 引用、接口对应、CSS 配平。

这几项几秒钟跑完，能抓到"页面打开一片空白"这类致命错，
比起无头浏览器截图，性价比高得多。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "static", "index.html")
APP = os.path.join(ROOT, "app.py")
NODE = r"C:\Users\Administrator\.workbuddy\binaries\node\versions\22.22.2-6\node.exe"

fails: list[str] = []
warns: list[str] = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


html = open(HTML, encoding="utf-8").read()

# ---------- 1. 抽内联脚本做语法检查 ----------
scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
check(len(scripts) >= 1, "没找到内联 <script>")
js = "\n".join(scripts)
tmp = os.path.join(tempfile.gettempdir(), "_st_check.js")
with open(tmp, "w", encoding="utf-8") as f:
    f.write(js)
node = NODE if os.path.isfile(NODE) else "node"
p = subprocess.run([node, "--check", tmp], capture_output=True)
check(p.returncode == 0, "JS 语法错误：\n" + p.stderr.decode("utf-8", "replace")[:800])

# ---------- 2. id 引用 ----------
ids = set(re.findall(r'\bid="([^"]+)"', html))
used = set(re.findall(r'\$\("([^"]+)"\)', js))
used |= set(re.findall(r'getElementById\("([^"]+)"\)', js))
missing = sorted(used - ids)
check(not missing, f"JS 引用了不存在的 id：{missing}")

# ---------- 3. 前端调的接口后端得真有 ----------
app_src = open(APP, encoding="utf-8").read()
routes = set(re.findall(r'@app\.(?:get|post)\("([^"]+)"', app_src))
apis = set(re.findall(r'["\'`](/api/[a-zA-Z0-9_\-/<>]+)', js))
norm_routes = {re.sub(r"<[^>]+>", "*", r) for r in routes}
for a in sorted(apis):
    a2 = re.sub(r"\$\{[^}]+\}", "*", a)
    a2 = re.sub(r"<[^>]*>", "*", a2)
    a2 = a2.rstrip("/")
    ok = any(re.fullmatch(r.replace("*", "[^/]+").rstrip("/"), a2) for r in norm_routes)
    if not ok:
        # 拼出来的路径（如 /api/jobs/ + id + /events）自己再核一次
        base = a2.split("/")[1:3]
        ok = any(r.startswith("/" + "/".join(base)) for r in norm_routes)
    check(ok, f"前端调了 {a}，后端没有对应路由")

# ---------- 4. CSS 配平 ----------
css = "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S))
check(css.count("{") == css.count("}"), f"CSS 大括号不配平：{css.count('{')} vs {css.count('}')}")

# ---------- 5. hidden 会不会被 display 抢戏 ----------
if re.search(r"\.(picked|res|empty)\s*\{[^}]*display\s*:", css):
    check("[hidden]{display:none!important}" in css.replace(" ", ""),
          "有 display:flex 的类同时要用 hidden，缺 [hidden] !important 规则")

# ---------- 6. 常用的坑：忘了解构 ----------
check("EventSource" in js, "没接 SSE，进度会不动")
check("onerror" in js, "SSE 没写 onerror 兜底，断线就卡住")

print("=" * 56)
print(f"id 引用 {len(used)} 个，接口 {len(apis)} 个，CSS {css.count('{')} 条规则")
if warns:
    for w in warns:
        print("  ! " + w)
if fails:
    print("FAIL")
    for f in fails:
        print("  x " + f)
    sys.exit(1)
print("PASS 静态自检全过")
