"""本地服务：只听 127.0.0.1，文件不出本机。

任务走"建任务拿 id → SSE 看进度"的老路子，好处是刷新页面能接着看，
长音频识别十几分钟也不会因为页面一跳就白跑。
"""
from __future__ import annotations

import io
import json
import os
import time
import uuid
import zipfile

from flask import Flask, Response, jsonify, request, send_file, send_from_directory

from core import config, jobs as jobs_mod, pipeline

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024      # 1G 上限，长音频也够
app.config["JSON_AS_ASCII"] = False

ALLOWED = {".srt", ".vtt", ".ass", ".ssa", ".txt",
           ".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".opus", ".wma",
           ".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts"}


def _json_path(jid: str, name: str) -> str:
    """防目录穿越：只允许取 output/<jid>/ 下的文件名。"""
    base = os.path.join(config.OUT_DIR, jid)
    name = os.path.basename(name)
    return os.path.join(base, name)


@app.get("/")
def index():
    return send_from_directory(os.path.join(config.ROOT, "static"), "index.html")


@app.get("/api/settings")
def get_settings():
    return jsonify(config.public_settings())


@app.post("/api/settings")
def set_settings():
    patch = request.get_json(silent=True) or {}
    config.save_settings(patch)
    return jsonify(config.public_settings())


@app.get("/api/health")
def health():
    s = config.load_settings()
    return jsonify({
        "ok": True,
        "ffmpeg": bool(s.get("ffmpeg_dir")),
        "ffmpeg_dir": s.get("ffmpeg_dir", ""),
        "engine": s.get("engine", "google"),
    })


@app.post("/api/test")
def test_conn():
    """点一下就知道翻译通道通不通，省得跑完半个视频才发现没网。"""
    body = request.get_json(silent=True) or {}
    s = config.load_settings()
    engine = body.get("engine") or s.get("engine", "google")
    if engine == "llm":
        res = config.probe_llm(
            body.get("llm_base") or s.get("llm_base", ""),
            body.get("llm_key") or s.get("llm_key", ""),
            body.get("llm_model") or s.get("llm_model", ""),
        )
    else:
        res = config.probe_proxy(body.get("proxy", s.get("proxy", "")))
    res["engine"] = engine
    res["ffmpeg"] = bool(s.get("ffmpeg_dir"))
    return jsonify(res)


@app.post("/api/jobs")
def create_job():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "没收到文件"}), 400
    name = f.filename
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED:
        return jsonify({"error": f"不支持 {ext or '这种'} 格式"}), 400

    params = {}
    for k in ("mode", "engine", "source_lang", "target_lang", "out_format",
              "glossary", "llm_base", "llm_key", "llm_model", "proxy", "asr_model"):
        if k in request.form:
            params[k] = request.form[k]
    for k in ("zh_line", "en_line"):
        if k in request.form:
            try:
                params[k] = int(float(request.form[k]))
            except Exception:
                pass
    for k in ("bilingual", "zh_first"):
        if k in request.form:
            params[k] = request.form[k].lower() in ("1", "true", "yes", "on")

    job = jobs_mod.create({"filename": name, **params})
    work = os.path.join(config.WORK_DIR, job.id)
    os.makedirs(work, exist_ok=True)
    src = os.path.join(work, "input" + ext)
    f.save(src)
    size = os.path.getsize(src)
    if size == 0:
        return jsonify({"error": "文件是空的"}), 400
    job.log(f"收到文件：{name}（{size/1048576:.1f} MB）")

    pipeline.start(job, src, name, params)
    return jsonify({"id": job.id, "size": size})


@app.get("/api/jobs/<jid>")
def job_state(jid: str):
    job = jobs_mod.get(jid)
    if not job:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(job.snapshot())


@app.get("/api/jobs/<jid>/events")
def job_events(jid: str):
    job = jobs_mod.get(jid)
    if not job:
        return jsonify({"error": "任务不存在"}), 404

    def gen():
        last_ver = -1
        beat = 0.0
        while True:
            snap = job.snapshot()
            if snap["ver"] != last_ver:
                last_ver = snap["ver"]
                yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
            else:
                beat += 0.4
                if beat >= 5:                     # 长时间没动静也让连接活着
                    beat = 0.0
                    yield ": ping\n\n"
            if job.status in ("done", "error", "cancelled"):
                yield "event: end\ndata: {}\n\n"
                return
            time.sleep(0.4)

    return Response(gen(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


@app.post("/api/jobs/<jid>/cancel")
def cancel_job(jid: str):
    job = jobs_mod.get(jid)
    if not job:
        return jsonify({"error": "任务不存在"}), 404
    job.cancel()
    return jsonify({"ok": True})


@app.get("/api/jobs/<jid>/file/<path:name>")
def download(jid: str, name: str):
    path = _json_path(jid, name)
    if not os.path.isfile(path):
        return jsonify({"error": "文件不存在"}), 404
    return send_file(path, as_attachment=True, download_name=name)


@app.get("/api/jobs/<jid>/zip")
def download_zip(jid: str):
    base = os.path.join(config.OUT_DIR, jid)
    if not os.path.isdir(base):
        return jsonify({"error": "还没有产出文件"}), 404
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(os.listdir(base)):
            p = os.path.join(base, name)
            if os.path.isfile(p):
                zf.write(p, name)
    mem.seek(0)
    stem = "字幕"
    job = jobs_mod.get(jid)
    if job:
        stem = pipeline.safe_stem(job.params.get("filename", "字幕"))
    return send_file(mem, as_attachment=True, download_name=f"{stem}-双语字幕.zip",
                     mimetype="application/zip")


@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": "文件太大（上限 1G）"}), 413


def _port_busy(port: int) -> bool:
    import socket
    s = socket.socket()
    s.settimeout(0.35)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def _kill_on_port(port: int) -> bool:
    """把上次没关干净的实例收掉 —— 双击没反应是最劝退的体验。"""
    import subprocess
    root = os.environ.get("SystemRoot", r"C:\Windows")
    netstat = os.path.join(root, "System32", "netstat.exe")
    tasklist = os.path.join(root, "System32", "tasklist.exe")
    taskkill = os.path.join(root, "System32", "taskkill.exe")
    if not os.path.isfile(netstat):
        return False
    try:
        out = subprocess.run([netstat, "-ano"], capture_output=True).stdout
        text = out.decode("gbk", "replace")
    except Exception:
        return False
    pids = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].endswith(f":{port}"):
            if parts[4].isdigit():
                pids.add(parts[4])
    killed = False
    for pid in pids:
        if int(pid) == os.getpid():
            continue
        try:
            info = subprocess.run([tasklist, "/FI", f"PID eq {pid}"],
                                  capture_output=True).stdout.decode("gbk", "replace")
            if "python" not in info.lower():
                continue
            subprocess.run([taskkill, "/F", "/PID", pid], capture_output=True)
            killed = True
        except Exception:
            continue
    if killed:
        time.sleep(0.9)
    return killed


def _free_port(port: int) -> int:
    if not _port_busy(port):
        return port
    if _kill_on_port(port) and not _port_busy(port):
        print(f"  （收掉了上次没关的实例，继续用 {port}）")
        return port
    for p in range(port + 1, port + 6):
        if not _port_busy(p):
            print(f"  端口 {port} 被别的程序占着，改用 {p}")
            return p
    return port


def main():
    port = int(os.environ.get("PORT", "8791"))
    config.cleanup_work()
    port = _free_port(port)
    banner = (f"\n  ==========================================\n"
              f"    字幕翻译工作台\n"
              f"    打开  http://127.0.0.1:{port}\n"
              f"    关掉这个窗口就停\n"
              f"  ==========================================\n")
    print(banner)
    if os.environ.get("NO_BROWSER") != "1":
        try:
            import threading
            import webbrowser
            threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
        except Exception:
            pass
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
