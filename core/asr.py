"""本地语音识别：ffmpeg 抽音 → faster-whisper 转文字带时间轴。

模型优先复用本机已下好的（上一版配音工具里下过 small 和 base，480MB 没必要再下一次）。
没找到才下载，走境内镜像直连 + 断点续传。
"""
from __future__ import annotations

import os
import re
import subprocess
import urllib.error
import urllib.request

from . import config

REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
}
FILES = ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt",
         "preprocessor_config.json"]
MIRRORS = ["https://hf-mirror.com/{repo}/resolve/main/{file}",
           "https://huggingface.co/{repo}/resolve/main/{file}"]

_MODELS: dict = {}


def search_dirs() -> list[str]:
    """本项目的 models/，以及隔壁配音工具已经下好的模型。"""
    return [
        config.MODEL_DIR,
        os.path.join(os.path.dirname(config.ROOT), "yt2cn", "models"),
        os.environ.get("WHISPER_MODELS", ""),
    ]


def _has(d: str) -> bool:
    return all(os.path.exists(os.path.join(d, f))
               for f in ("config.json", "model.bin", "tokenizer.json"))


def find_model(size: str) -> str | None:
    for root in search_dirs():
        if not root or not os.path.isdir(root):
            continue
        for name in (f"faster-whisper-{size}", size):
            p = os.path.join(root, name)
            if os.path.isdir(p) and _has(p):
                return p
    return None


def _direct():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _download(url: str, dst: str, cancel=None) -> bool:
    tmp = dst + ".part"
    have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
    headers = {"User-Agent": "Mozilla/5.0"}
    if have:
        headers["Range"] = f"bytes={have}-"
    try:
        r = _direct().open(urllib.request.Request(url, headers=headers), timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise
    with r:
        mode = "ab"
        if have and r.status != 206:
            have, mode = 0, "wb"
        with open(tmp, mode) as f:
            while True:
                if cancel:
                    cancel()
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                have += len(chunk)
    os.replace(tmp, dst)
    return True


def ensure_model(size: str, log=None, cancel=None) -> str:
    size = size if size in REPOS else "small"
    found = find_model(size)
    if found:
        if log:
            log(f"用本机已有的 {size} 模型：{found}")
        return found
    dst_dir = os.path.join(config.MODEL_DIR, f"faster-whisper-{size}")
    os.makedirs(dst_dir, exist_ok=True)
    if log:
        log(f"本机没有 {size} 模型，开始下载（只需一次，约 "
            f"{ {'tiny':'75MB','base':'145MB','small':'480MB','medium':'1.5GB','large-v3':'3GB'}.get(size,'?') }）")
    repo = REPOS[size]
    for f in FILES:
        dst = os.path.join(dst_dir, f)
        if os.path.exists(dst):
            continue
        ok = False
        for tpl in MIRRORS:
            try:
                if _download(tpl.format(repo=repo, file=f), dst, cancel):
                    ok = True
                    break
            except Exception:
                continue
        if not ok and f in ("config.json", "model.bin", "tokenizer.json"):
            raise RuntimeError(f"模型文件 {f} 下载失败，检查网络后重试")
    return dst_dir


def to_wav(src: str, dst: str, ffmpeg_dir: str = "", cancel=None) -> float:
    """任意音视频 → 16k 单声道 wav，返回时长（秒）。"""
    ff = os.path.join(ffmpeg_dir, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not ffmpeg_dir or not os.path.isfile(ff):
        raise RuntimeError("找不到 ffmpeg，音频没法解码。把 ffmpeg 目录填进设置里。")
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error",
           "-i", src, "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", dst]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0 or not os.path.exists(dst):
        msg = (p.stderr or b"").decode("utf-8", "replace")[-400:]
        raise RuntimeError(f"音频解码失败：{msg.strip() or 'ffmpeg 返回错误'}")
    return duration_of(dst)


def duration_of(wav: str) -> float:
    import wave
    try:
        with wave.open(wav, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return 0.0


def _load_audio(wav: str):
    """自己读 PCM，绕开 PyAV —— av 19 与 faster-whisper 1.2 的签名不兼容。"""
    import wave

    import numpy as np

    with wave.open(wav, "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise RuntimeError(f"不支持的采样位宽 {sw * 8}bit")
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        n = int(len(a) * 16000 / sr)
        a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)
    return np.ascontiguousarray(a)


def transcribe(wav: str, model_size: str = "small", language: str = "auto",
               log=None, cancel=None, on_progress=None) -> list[dict]:
    """返回 [{start, end, text}]，已按时间排序。"""
    from faster_whisper import WhisperModel

    size = model_size if model_size in REPOS else "small"
    model = _MODELS.get(size)
    if model is None:
        path = ensure_model(size, log=log, cancel=cancel)
        if log:
            log(f"加载 {size} 模型…")
        model = WhisperModel(path, device="cpu", compute_type="int8")
        _MODELS[size] = model

    audio = _load_audio(wav)
    total = len(audio) / 16000.0
    if log:
        log(f"开始识别，音频 {total/60:.1f} 分钟…")
    segments, info = model.transcribe(
        audio,
        language=None if language in ("auto", "") else language,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        condition_on_previous_text=False,
    )
    out: list[dict] = []
    for seg in segments:
        if cancel:
            cancel()
        text = (seg.text or "").strip()
        if text:
            out.append({"start": float(seg.start), "end": float(seg.end), "text": text})
        if on_progress and total > 0:
            on_progress(min(0.99, float(seg.end) / total))
    if log:
        log(f"识别完成：{len(out)} 条")
    return out
