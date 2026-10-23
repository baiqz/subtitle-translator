"""字幕文件解析与导出：SRT / VTT / ASS 三种进，三种出。

解析时顺手做两件脏活：
1. 已经是双语的（上行英文下行中文）只留英文，不然翻两遍；
2. 手工字幕常见的连字符断词、speaker 前缀、样式标记，一并清掉。
"""
from __future__ import annotations

import re

from . import textseg

TIME_SRT = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")
TIME_SHORT = re.compile(r"(\d{1,2}):(\d{2})[,.](\d{1,3})")
ARROW = re.compile(r"\s*(?:-->|–>)\s*")
ASS_DLG = re.compile(r"^Dialogue:\s*(.*)$", re.I)


def _to_sec(h: int, m: int, s: int, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000.0


def parse_time(tok: str) -> float | None:
    tok = tok.strip().replace(",", ".")
    m = TIME_SRT.match(tok)
    if m:
        return _to_sec(*m.groups())
    m = TIME_SHORT.match(tok)
    if m:
        return _to_sec(0, *m.groups())
    if re.fullmatch(r"\d+(?:\.\d+)?", tok):
        return float(tok)
    return None


def fmt_srt(t: float) -> str:
    if t < 0:
        t = 0
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fmt_vtt(t: float) -> str:
    return fmt_srt(t).replace(",", ".")


def fmt_ass(t: float) -> str:
    if t < 0:
        t = 0
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


# ---------------------------------------------------------------- 解析

def detect_format(name: str, head: str) -> str:
    low = (name or "").lower()
    if low.endswith(".ass") or low.endswith(".ssa"):
        return "ass"
    if low.endswith(".vtt"):
        return "vtt"
    if low.endswith(".srt"):
        return "srt"
    h = (head or "")[:400]
    if "[Script Info]" in h or "[Events]" in h:
        return "ass"
    if h.lstrip("\ufeff \t\r\n").upper().startswith("WEBVTT"):
        return "vtt"
    return "srt"


def _clean_lines(lines: list[str]) -> tuple[str, str]:
    """洗掉样式标记、speaker 前缀、对话横线、连字符断词，返回 (说话人, 正文)。"""
    speaker = ""
    kept = []
    for ln in lines:
        t = textseg.strip_tags(ln).strip()
        if not t:
            continue
        # 行首的对话横线："- So I was..." 不是负号
        t = re.sub(r"^[-–—]{1,2}\s+(?=[A-Z0-9\"'“‘(\[])", "", t)
        sp, body = textseg.strip_speaker(t)
        if sp and not speaker:
            speaker = sp
        kept.append(body if sp else t)
    if not kept:
        return "", ""
    text = kept[0]
    for nxt in kept[1:]:
        text = textseg.join_fragments(text, nxt)
    return speaker, text.strip()


def _drop_existing_translation(lines: list[str]) -> list[str]:
    """已经是双语字幕的，只留非中文那半 —— 我们要自己翻。"""
    if len(lines) < 2:
        return lines
    cjk = [i for i, l in enumerate(lines) if textseg.is_cjk(l)]
    latin = [i for i, l in enumerate(lines) if not textseg.is_cjk(l) and textseg.strip_tags(l).strip()]
    if cjk and latin:
        return [lines[i] for i in latin]
    return lines


_CUE_HEAD = re.compile(
    r"\n+(\d+)[ \t]*\n(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}[ \t]*(?:-->|–>)[^\n]*)\n")


def parse_srt(content: str) -> list[dict]:
    text = content.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    # 手工字幕常常缺空行，不补的话下一条的时间码会被当成正文吞进去
    text = _CUE_HEAD.sub(r"\n\n\1\n\2\n", text)
    cues: list[dict] = []
    blocks = re.split(r"\n\s*\n", text)
    for blk in blocks:
        lines = [l for l in blk.split("\n") if l.strip()]
        if not lines:
            continue
        ti = -1
        for i, l in enumerate(lines[:3]):
            if ARROW.search(l) and TIME_SRT.search(l):
                ti = i
                break
        if ti < 0:
            continue
        stamps = ARROW.split(lines[ti])
        if len(stamps) < 2:
            continue
        a, b = parse_time(stamps[0]), parse_time(stamps[1])
        if a is None or b is None:
            continue
        body = _drop_existing_translation(lines[ti + 1:])
        sp, txt = _clean_lines(body)
        if not txt:
            continue
        cues.append({"start": a, "end": max(b, a + 0.2), "text": txt, "speaker": sp})
    return cues


def parse_vtt(content: str) -> list[dict]:
    text = content.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    lines = text.split("\n")
    cues: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if ARROW.search(line) and TIME_SRT.search(line):
            stamps = ARROW.split(line)
            a, b = parse_time(stamps[0]), parse_time(stamps[1])
            i += 1
            body = []
            while i < len(lines) and lines[i].strip():
                body.append(lines[i])
                i += 1
            if a is None or b is None:
                continue
            body = _drop_existing_translation(body)
            sp, txt = _clean_lines(body)
            if txt:
                cues.append({"start": a, "end": max(b, a + 0.2), "text": txt, "speaker": sp})
        else:
            i += 1
    return cues


def parse_ass(content: str) -> list[dict]:
    text = content.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    fields = ["layer", "start", "end", "style", "name", "ml", "mr", "mv", "effect", "text"]
    cues: list[dict] = []
    for line in text.split("\n"):
        m = ASS_DLG.match(line.strip())
        if not m:
            continue
        parts = m.group(1).split(",", len(fields) - 1)
        if len(parts) < len(fields):
            continue
        a, b = parse_time(parts[1]), parse_time(parts[2])
        if a is None or b is None:
            continue
        raw = parts[9].replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
        body = _drop_existing_translation([p for p in raw.split("\n") if p.strip()])
        sp, txt = _clean_lines(body)
        if txt:
            cues.append({"start": a, "end": max(b, a + 0.2), "text": txt,
                         "speaker": parts[4].strip() or sp})
    return cues


def parse_text(content: str, cps: float = 12.0) -> list[dict]:
    """纯文本：没有时间轴，按句分条并给个估计时长，够用就行。"""
    raw = re.sub(r"[ \t]+", " ", content.replace("\r\n", "\n")).strip()
    sents: list[str] = []
    for para in re.split(r"\n\s*\n|\n", raw):
        para = para.strip()
        if not para:
            continue
        sents.extend(textseg.split_sentences_in_text(para))
    cues, cursor = [], 0.6
    for s in sents:
        if not s.strip():
            continue
        dur = max(1.2, len(s) / cps)
        cues.append({"start": round(cursor, 3), "end": round(cursor + dur, 3),
                     "text": s.strip(), "speaker": ""})
        cursor += dur + 0.25
    return cues


def parse(content: str, filename: str = "") -> tuple[list[dict], str]:
    fmt = detect_format(filename, content)
    if fmt == "ass":
        cues = parse_ass(content)
    elif fmt == "vtt":
        cues = parse_vtt(content)
    else:
        cues = parse_srt(content)
    if not cues:
        cues = parse_text(content)
        fmt = "txt"
    cues.sort(key=lambda c: (c["start"], c["end"]))
    return cues, fmt


# ---------------------------------------------------------------- 导出

def _join_pair(en: str, zh: str, mode: str, zh_first: bool,
               en_wrap: int, zh_wrap: int) -> list[str]:
    en_txt = textseg.wrap_lines(en, en_wrap, 2) if en else ""
    zh_txt = textseg.wrap_lines(zh, zh_wrap, 2) if zh else ""
    if mode == "zh":
        return [zh_txt] if zh_txt else ([en_txt] if en_txt else [])
    if mode == "en":
        return [en_txt] if en_txt else []
    lines = []
    if zh_first:
        lines = [p for p in (zh_txt, en_txt) if p]
    else:
        lines = [p for p in (en_txt, zh_txt) if p]
    return lines


def render(cues: list[dict], fmt: str = "srt", mode: str = "bilingual",
           zh_first: bool = False, en_wrap: int = 44, zh_wrap: int = 16) -> str:
    fmt = (fmt or "srt").lower()
    if fmt == "ass":
        return _render_ass(cues, mode, zh_first, en_wrap, zh_wrap)
    out: list[str] = []
    if fmt == "vtt":
        out.append("WEBVTT\n")
    for i, c in enumerate(cues, 1):
        body = _join_pair(c.get("en", ""), c.get("zh", ""), mode, zh_first, en_wrap, zh_wrap)
        if not body:
            continue
        if fmt == "vtt":
            out.append(f"{fmt_vtt(c['start'])} --> {fmt_vtt(c['end'])}")
        else:
            out.append(str(i))
            out.append(f"{fmt_srt(c['start'])} --> {fmt_srt(c['end'])}")
        out.append("\n".join(body))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


_ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,54,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,1,2,20,20,36,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _render_ass(cues: list[dict], mode: str, zh_first: bool,
                en_wrap: int, zh_wrap: int) -> str:
    rows = [_ASS_HEAD]
    for c in cues:
        parts = _join_pair(c.get("en", ""), c.get("zh", ""), mode, zh_first, en_wrap, zh_wrap)
        if not parts:
            continue
        txt = "\\N".join(parts).replace("\n", "\\N")
        rows.append(f"Dialogue: 0,{fmt_ass(c['start'])},{fmt_ass(c['end'])},Default,,0,0,0,,{txt}\n")
    return "".join(rows)
