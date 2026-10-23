"""路径、默认参数、外部可执行文件的查找。"""
from __future__ import annotations

import json
import os
import shutil
import threading
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
WORK_DIR = os.path.join(ROOT, "workspace")
OUT_DIR = os.path.join(ROOT, "output")
MODEL_DIR = os.path.join(ROOT, "models")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

for d in (DATA_DIR, WORK_DIR, OUT_DIR, MODEL_DIR):
    os.makedirs(d, exist_ok=True)

_lock = threading.RLock()


def find_ffmpeg() -> str:
    """ffmpeg 所在目录。找不到返回空串，稍后报个明白话。"""
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    cands = [
        os.environ.get("FFMPEG_DIR", ""),
        os.path.join(ROOT, "bin"),
        r"C:\Users\Administrator\.workbuddy\binaries\ffmpeg",
        os.path.join(os.path.dirname(ROOT), "yt2cn", "bin"),
        "/usr/local/bin", "/opt/homebrew/bin",
    ]
    for c in cands:
        if c and os.path.isfile(os.path.join(c, exe)):
            return c
    w = shutil.which("ffmpeg")
    return os.path.dirname(w) if w else ""


DEFAULTS: dict = {
    "proxy": "http://127.0.0.1:33210",
    "ffmpeg_dir": find_ffmpeg(),
    "asr_model": "small",
    "source_lang": "en",
    "target_lang": "zh-CN",
    "engine": "google",              # google | llm
    "llm_base": "https://api.siliconflow.cn/v1",
    "llm_key": "",
    "llm_model": "Qwen/Qwen2.5-32B-Instruct",
    "glossary": "",
    "mode": "natural",               # natural | strict
    "zh_line": 18,                   # 每条字幕中文上限（字）
    "en_line": 42,                   # 每行英文上限（字符）
    "bilingual": True,
    "zh_first": False,
    "out_format": "srt",
}

LABELS = {
    "source_lang": {"en": "英语", "auto": "自动识别"},
    "target_lang": {"zh-CN": "简体中文", "zh-TW": "繁体中文",
                    "en": "英语", "ja": "日语", "ko": "韩语"},
}


def load_settings() -> dict:
    with _lock:
        data = dict(DEFAULTS)
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data.update(json.load(f) or {})
            except Exception:
                pass
        if not data.get("ffmpeg_dir"):
            data["ffmpeg_dir"] = find_ffmpeg()
        return data


def save_settings(patch: dict) -> dict:
    with _lock:
        data = load_settings()
        for k, v in (patch or {}).items():
            if k in DEFAULTS:
                data[k] = v
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data


def public_settings() -> dict:
    """给前端看的：密钥只回传"有没有填"，不回传明文。"""
    s = load_settings()
    out = dict(s)
    out["llm_key_set"] = bool(s.get("llm_key"))
    out["llm_key"] = ""
    out["ffmpeg_ok"] = bool(s.get("ffmpeg_dir"))
    return out


def probe_proxy(proxy: str = "", timeout: float = 6.0) -> dict:
    """测翻译通道到底通不通 —— 这地方不通，用户会以为是程序坏了。"""
    url = ("https://translate.googleapis.com/translate_a/single"
           "?client=gtx&sl=en&tl=zh-CN&dt=t&q=hello")
    tries = []
    if proxy:
        tries.append(proxy)
    tries.append("")
    for p in tries:
        try:
            if p:
                op = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": p, "https": p}))
            else:
                op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with op.open(url, timeout=timeout) as r:
                if r.status == 200 and r.read():
                    return {"ok": True, "via": p or "直连"}
        except Exception as e:
            err = f"{type(e).__name__}"
            continue
    return {"ok": False, "via": "", "error": "代理和直连都不通，检查网络或换个代理地址"}


def probe_llm(base: str, key: str, model: str, timeout: float = 12.0) -> dict:
    """测大模型通道。报错要说人话 —— "401" 对普通用户等于没说。"""
    if not base:
        return {"ok": False, "via": "", "error": "还没填接口地址"}
    if not key:
        return {"ok": False, "via": "", "error": "还没填 API Key"}
    if not model:
        return {"ok": False, "via": "", "error": "还没填模型名"}
    url = base.rstrip("/") + "/models"
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status == 200:
                return {"ok": True, "via": "大模型接口"}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "via": "", "error": "API Key 不对或被拒（401/403）"}
        if e.code == 404:
            return {"ok": False, "via": "",
                    "error": "接口地址不对，一般要写到 /v1（404）"}
        return {"ok": False, "via": "", "error": f"接口返回 {e.code}"}
    except Exception as e:
        return {"ok": False, "via": "", "error": f"连不上：{type(e).__name__}"}

    # 有些兼容层不实现 /models，退回发一条最短的对话请求
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1, "stream": False,
    }).encode("utf-8")
    try:
        req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status == 200:
                return {"ok": True, "via": "大模型接口"}
        return {"ok": False, "via": "", "error": "接口有响应但返回异常"}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "via": "", "error": "API Key 不对或被拒（401/403）"}
        if e.code == 404:
            return {"ok": False, "via": "", "error": "模型名可能不对（404）"}
        return {"ok": False, "via": "", "error": f"接口返回 {e.code}"}
    except Exception as e:
        return {"ok": False, "via": "", "error": f"连不上：{type(e).__name__}"}


def cleanup_work(max_age_hours: float = 24.0) -> None:
    """清掉过期的临时音频，别让工作区越滚越大。"""
    import time
    now = time.time()
    try:
        for name in os.listdir(WORK_DIR):
            p = os.path.join(WORK_DIR, name)
            try:
                if now - os.path.getmtime(p) > max_age_hours * 3600:
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        os.remove(p)
            except Exception:
                pass
    except Exception:
        pass
