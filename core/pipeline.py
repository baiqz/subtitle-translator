"""全流程：输入 → 分句 → 翻译 → 重新断行 → 导出。

两种成片方式：
- natural（默认）：按句群翻译，再按中文节奏重新切条。原文碎成"半句话"的字幕也会被拼回来，
  出来的字幕一条是一个完整句子，读着才像人话。条数会跟原片不一样，这是刻意的。
- strict：一条不动，时间轴和条数完全照原片，只把中文按比例塞回去。需要跟画面硬对齐时用。
"""
from __future__ import annotations

import os
import re
import time

from . import asr, config, jobs as jobs_mod, subtitle, textseg
from .translate import make_translator

SUB_EXT = {".srt", ".vtt", ".ass", ".ssa", ".txt"}
MEDIA_EXT = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".opus", ".wma",
             ".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts"}

BATCH = 40


def decode_text(raw: bytes) -> str:
    """字幕文件编码五花八门，GBK 的老字幕一抓一把。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    for enc in ("utf-8", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def safe_stem(name: str) -> str:
    base = os.path.basename(name or "subtitle")
    base = os.path.splitext(base)[0]
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .")
    return (base or "subtitle")[:60]


def _alloc(start: float, end: float, weights: list[float]) -> list[tuple[float, float]]:
    """把一段总时长按内容量分给若干条，首尾对齐，不留缝。"""
    n = len(weights)
    if n == 0:
        return []
    if n == 1:
        return [(round(start, 3), round(max(end, start + 0.2), 3))]
    total = sum(weights) or 1.0
    dur = max(0.2, end - start)
    out, cur = [], start
    for w in weights:
        span = dur * w / total
        out.append([round(cur, 3), round(cur + span, 3)])
        cur += span
    out[-1][1] = round(max(end, out[-1][0] + 0.2), 3)
    return [(a, b) for a, b in out]


def _natural_cues(sentences: list[dict], zh_list: list[str],
                  zh_line: int, en_line: int) -> list[dict]:
    out: list[dict] = []
    for s, z in zip(sentences, zh_list):
        # 一条字幕能占两行，中文容量就得按两行算（zh_line 是每行字数）。
        # 之前只给单行容量，中文被多切一刀，切点比例就跟英文错开了 —— 明明
        # 「And I learned that the hard way,」却配上「我了解到，相信我，」。
        pairs = textseg.split_pair(s["en"], z, zh_line * 2, en_line * 2)
        # 时间按中文分：观众读的是中文，按英文长度分会让中文多的那条来不及看
        weights = [max(1, textseg.count_chars(zz)) if zz.strip()
                   else max(1, len(e.strip())) for e, zz in pairs]
        for (e, zz), (a, b) in zip(pairs, _alloc(s["start"], s["end"], weights)):
            if not (e.strip() or zz.strip()):
                continue
            out.append({"start": a, "end": b, "en": e.strip(), "zh": zz.strip()})
    return _merge_short(out, zh_line, en_line)


def _merge_short(cues: list[dict], zh_line: int, en_line: int,
                 min_dur: float = 0.65) -> list[dict]:
    """一条字幕只有半秒，神仙也读不完 —— 跟邻居并了，内容不超上限就并。"""
    merged: list[dict] = []
    for c in cues:
        if merged:
            last = merged[-1]
            too_short = (last["end"] - last["start"] < min_dur
                         or c["end"] - c["start"] < min_dur)
            if too_short:
                en_len = len(last["en"]) + len(c["en"]) + 1
                zh_len = len(last["zh"]) + len(c["zh"])
                if en_len <= en_line * 2 and zh_len <= zh_line * 2.2:
                    last["end"] = c["end"]
                    last["en"] = textseg.join_fragments(last["en"], c["en"])
                    last["zh"] = last["zh"] + c["zh"]
                    continue
        merged.append(dict(c))
    return merged


def _strict_cues(cues: list[dict], sentences: list[dict], zh_list: list[str]) -> list[dict]:
    bucket: dict[int, list[str]] = {}
    for s, z in zip(sentences, zh_list):
        src = [i for i in s["src"] if 0 <= i < len(cues)]
        srcw = s.get("srcw") or []
        weights = [max(1, w) for w in srcw] if len(srcw) == len(s["src"]) else None
        if weights:
            weights = [w for w, i in zip(weights, s["src"]) if 0 <= i < len(cues)]
        if not weights:
            weights = [max(1, textseg.count_chars(cues[i]["text"])) for i in src]
        for i, part in zip(src, textseg.distribute_zh(z, weights)):
            if part:
                bucket.setdefault(i, []).append(part)
    out: list[dict] = []
    for i, c in enumerate(cues):
        out.append({"start": c["start"], "end": c["end"],
                    "en": c["text"], "zh": "".join(bucket.get(i, [])).strip()})
    return out


def run(job: "jobs_mod.Job", src_path: str, filename: str, params: dict) -> dict:
    t0 = time.time()
    settings = config.load_settings()
    # 空串不算"改了"—— 前端密钥框留空表示沿用已保存的，别把 key 覆盖没了
    p = dict(settings)
    for k, v in (params or {}).items():
        if v is None or v == "":
            continue
        p[k] = v
    warnings: list[str] = []

    zh_line = int(p.get("zh_line") or 18)
    en_line = int(p.get("en_line") or 42)
    mode = p.get("mode") or "natural"
    bilingual = bool(p.get("bilingual", True))
    zh_first = bool(p.get("zh_first", False))
    fmt = (p.get("out_format") or "srt").lower()
    target = p.get("target_lang") or "zh-CN"
    source = p.get("source_lang") or "auto"

    ext = os.path.splitext(filename or "")[1].lower()
    job.check_cancel()

    # ---------------- 1. 拿字幕
    if ext in SUB_EXT:
        job.stage_to("解析字幕", 0.05, "读取字幕文件…")
        with open(src_path, "rb") as f:
            raw = f.read()
        text = decode_text(raw)
        cues, ifmt = subtitle.parse(text, filename)
        if not cues:
            raise RuntimeError("这个文件里没找到能用的字幕条目，确认一下是不是字幕文件？")
        job.log(f"解析到 {len(cues)} 条字幕（{ifmt.upper()} 格式）")
        info_in = {"type": "subtitle", "format": ifmt, "cues": len(cues)}
        if source == "auto":
            head = " ".join(c["text"] for c in cues[:40])
            source = "zh" if textseg.is_cjk(head) else "en"
    else:
        job.stage_to("解码音频", 0.04, "抽取音轨…")
        work = os.path.join(config.WORK_DIR, job.id)
        os.makedirs(work, exist_ok=True)
        wav = os.path.join(work, "audio.wav")
        dur = asr.to_wav(src_path, wav, p.get("ffmpeg_dir", ""), job.check_cancel)
        job.log(f"音频时长 {dur/60:.1f} 分钟")

        job.stage_to("语音识别", 0.08, "本地识别中，第一次跑要加载模型…")
        cues = asr.transcribe(
            wav, p.get("asr_model", "small"), source,
            log=job.log, cancel=job.check_cancel,
            on_progress=lambda v: job.set_progress(0.08 + v * 0.42,
                                                   f"语音识别 {v*100:.0f}%"))
        if not cues:
            raise RuntimeError("没识别出语音内容，音频可能是纯音乐或静音。")
        info_in = {"type": "audio", "format": ext.lstrip("."), "cues": len(cues),
                   "duration": round(dur, 1)}
        if source == "auto":
            source = "en"

    job.check_cancel()

    # ---------------- 2. 分句
    job.stage_to("整理分句", 0.52, "把碎片字幕拼回完整句子…")
    sentences = textseg.sentences_from_cues(cues)
    if not sentences:
        raise RuntimeError("分句之后没有内容了，检查一下输入。")
    merged = len(cues) - len(sentences)
    job.log(f"{len(cues)} 条 → {len(sentences)} 句"
            + (f"（合并了 {merged} 条碎行）" if merged > 0 else ""))

    # ---------------- 3. 翻译
    engine = p.get("engine") or "google"
    tr = make_translator({
        "engine": engine,
        "proxy": p.get("proxy", ""),
        "llm_base": p.get("llm_base", ""),
        "llm_key": p.get("llm_key", ""),
        "llm_model": p.get("llm_model", ""),
        "glossary": p.get("glossary", ""),
    }, log=job.log)
    job.stage_to("翻译", 0.55, f"翻译 {len(sentences)} 句…")
    zh_list: list[str] = []
    total = len(sentences)
    for i in range(0, total, BATCH):
        job.check_cancel()
        chunk = [s["en"] for s in sentences[i:i + BATCH]]
        got = tr.translate(chunk, source, target)
        zh_list.extend(got)
        done = min(total, i + len(chunk))
        job.set_progress(0.55 + 0.34 * done / max(1, total),
                         f"翻译 {done}/{total}")
    if len(zh_list) < total:
        zh_list.extend([""] * (total - len(zh_list)))

    empty = sum(1 for z in zh_list if not z.strip())
    if empty:
        warnings.append(f"{empty} 句没翻出内容（可能是空行或网络抖动），已保留英文")
    if tr.stats.get("fallbacks"):
        warnings.append(f"有 {tr.stats['fallbacks']} 批翻译对不齐，已改用逐条翻译")
    if tr.stats.get("term_lost"):
        warnings.append(f"{tr.stats['term_lost']} 句的术语占位符没还原回来，术语可能没生效")

    job.check_cancel()

    # ---------------- 4. 组装
    job.stage_to("排布字幕", 0.92, "按中文节奏断行…")
    if mode == "strict":
        out_cues = _strict_cues(cues, sentences, zh_list)
    else:
        out_cues = _natural_cues(sentences, zh_list, zh_line, en_line)
    out_cues = [c for c in out_cues if c["en"].strip() or c["zh"].strip()]

    # ---------------- 5. 导出
    out_dir = os.path.join(config.OUT_DIR, job.id)
    os.makedirs(out_dir, exist_ok=True)
    stem = safe_stem(filename)
    made = []
    plans = []
    if bilingual:
        plans.append(("bilingual", "双语", "bilingual"))
    plans.append(("zh", "中文", target.split("-")[0]))
    if bilingual:
        plans.append(("en", "原文", "en"))

    for kind, label, tag in plans:
        body = subtitle.render(out_cues, fmt, kind, zh_first, en_line, zh_line)
        name = f"{stem}.{tag}.{fmt}"
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        made.append({"name": name, "label": label, "size": os.path.getsize(path)})

    if mode != "strict" and out_cues:
        warnings.append(f"自然分句模式：条目数 {len(cues)} → {len(out_cues)}，"
                        f"时间轴仍与原片对齐")

    elapsed = round(time.time() - t0, 1)
    job.log(f"完成，用时 {elapsed}s")
    preview = [{"start": c["start"], "end": c["end"], "en": c["en"], "zh": c["zh"]}
               for c in out_cues[:400]]
    # Google 没连上时实际走的是备用通道，标签得说实话
    real_engine = tr.engine
    if real_engine == "google" and tr.stats.get("mymemory"):
        real_engine = "mymemory"
    label = {"llm": "AI 大模型", "mymemory": "备用通道"}.get(real_engine, "免费通道")
    return {
        "files": made,
        "cues": preview,
        "count": len(out_cues),
        "source_count": len(cues),
        "sentence_count": len(sentences),
        "input": info_in,
        "elapsed": elapsed,
        "engine": real_engine,
        "engine_label": label,
        "mode": mode,
        "warnings": warnings,
        "source_lang": source,
        "target_lang": target,
    }


def start(job: "jobs_mod.Job", src_path: str, filename: str, params: dict):
    """在后台线程里跑，异常都收进任务状态，别让线程静默死掉。"""
    import threading

    def work():
        try:
            job.status = "running"
            result = run(job, src_path, filename, params)
            job.finish(result)
        except jobs_mod.Cancelled:
            pass
        except Exception as e:
            import traceback
            job.log(f"出错：{e}")
            traceback.print_exc()
            job.fail(f"{type(e).__name__}: {e}")

    th = threading.Thread(target=work, daemon=True)
    th.start()
    return th
