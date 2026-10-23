"""分句引擎：把"碎片字幕"还原成完整句子，再把译文按中文节奏断开。

为什么要有这一层：字幕机翻读着别扭，八成不是引擎不行，而是喂进去的东西太碎。
一条字幕半句话，等于让人一口气翻译几十个"话没说完"的句子——
代词不知道指谁，转折不知道承接什么，翻出来当然像天书。

流程：拆句（一条里塞了三句的拆开）→ 合句（三条拼一句的合起来）→ 翻 → 按中文节奏断行。
全部是纯函数，不碰 IO，方便单测。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- 基础判定

END_PUNCT = ".!?…。！？"
TRAILING = "\"'”’)）]】»"
CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"

# 以这些词结尾的句点不是句号
_ABBREV = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "mt", "vs", "etc", "inc",
    "ltd", "co", "corp", "dept", "est", "fig", "no", "vol", "al", "approx",
    "gen", "col", "capt", "lt", "sgt", "rev", "hon", "gov", "sen", "rep",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec", "mon", "tue", "wed", "thu", "fri", "sat", "sun",
}

_TAG_RE = re.compile(r"<[^>]{1,40}>|\{\\[^{}]{0,40}\}")
_SPEAKER_RE = re.compile(r"^\s*(?:[-–—]\s*)?([A-Z][A-Z0-9 .'’\-]{1,18}):\s+(?=\S)")
_CJK_CHAR = re.compile(f"[{CJK}]")
_LATIN_CHAR = re.compile(r"[A-Za-z]")


def is_cjk(text: str) -> bool:
    """按字符占比判断是不是中文文本。"""
    letters = [c for c in text if _CJK_CHAR.match(c) or _LATIN_CHAR.match(c)]
    if not letters:
        return False
    cjk = sum(1 for c in letters if _CJK_CHAR.match(c))
    return cjk / len(letters) > 0.3


def strip_tags(text: str) -> str:
    """去掉 <i>、{\\an8} 这类样式标记，只留字。"""
    return _TAG_RE.sub("", text or "")


def count_chars(text: str) -> int:
    """显示宽度：CJK 记 1，西文按 0.5 记 —— 用来估时间分配。"""
    text = strip_tags(text)
    w = 0.0
    for c in text:
        w += 1.0 if _CJK_CHAR.match(c) else 0.55
    return max(1, int(round(w)))


def strip_speaker(text: str) -> tuple[str, str]:
    """切掉 "- JOHN:" 这类说话人前缀，返回 (说话人, 正文)。"""
    m = _SPEAKER_RE.match(text)
    if not m:
        return "", text
    name = m.group(1).strip()
    # 全大写的才当人名，避免把 "Note:" "Anyway:" 误伤
    if name.isupper() and len(name) <= 20:
        return name, text[m.end():].strip()
    return "", text


# ---------------------------------------------------------------- 拼接

def join_fragments(prev: str, nxt: str) -> str:
    """把两条碎片拼回去，处理断词连字符与中日韩免空格。"""
    prev = prev.rstrip()
    nxt = nxt.lstrip()
    if not prev:
        return nxt
    if not nxt:
        return prev
    # 上一行以连字符结尾且下一行小写开头 = 单词被拆断，直接粘
    if prev.endswith(("-", "‐", "–")) and nxt[:1].islower():
        return prev[:-1] + nxt
    if prev.endswith("—") or prev.endswith("-"):
        return prev + nxt if _CJK_CHAR.match(nxt[0]) else prev + nxt
    # 中文之间不加空格
    if _CJK_CHAR.match(prev[-1]) or _CJK_CHAR.match(nxt[0]):
        return prev + nxt
    return prev + " " + nxt


# ---------------------------------------------------------------- 判断句子完了没

def _last_word(text: str) -> str:
    toks = re.findall(r"[A-Za-z][A-Za-z.']*", text)
    return toks[-1].lower() if toks else ""


def is_sentence_end(text: str) -> bool:
    """这句话说完了吗？要躲开 Mr. / U.S. / 3 p.m. 这类假句号。"""
    t = (text or "").strip()
    if not t:
        return False
    # 剥掉结尾的引号括号再判断
    core = t
    while core and core[-1] in TRAILING:
        core = core[:-1]
    if not core:
        return False
    last = core[-1]
    if last in "!?…。！？":
        return True
    if last != ".":
        return False
    word = _last_word(core)
    bare = word.rstrip(".")
    if bare in _ABBREV or word in _ABBREV:
        return False
    if len(bare) == 1 and bare.isalpha():          # "J." 缩写
        return False
    if re.search(r"\d\.$", core):                  # "3." 编号 / 小数点
        return False
    if re.search(r"(?:\b[a-z]\.){2,}$", core):     # "e.g." "u.s."
        return False
    return True


def _open_quotes(text: str) -> int:
    """未闭合的引号/括号个数（>0 说明话还没说完）。"""
    n = 0
    n += max(0, text.count('"') % 2)
    n += max(0, text.count("“") - text.count("”"))
    n += max(0, text.count("（") - text.count("）"))
    n += max(0, text.count("(") - text.count(")"))
    return n


# ---------------------------------------------------------------- 第一步：拆

_SENT_SPLIT = re.compile(r"(?<=[.!?…。！？])[\"'”’)]*\s+(?=[\"'“‘(\[]?[A-Z0-9\u4e00-\u9fff])")


def split_sentences_in_text(text: str) -> list[str]:
    """一条字幕里塞了好几句时，按句末标点切开（躲开缩写）。

    注意不能用 re.split：这条正则里 `["'”’)]*` 会把句末引号一起吃掉，
    `away." Then` 切开就变成 `away.` —— 引号丢了，"话还没说完"的判断跟着失效，
    整段话会被并成一句。改成按位置切，引号留在左段。
    """
    text = text.strip()
    if not text:
        return []
    cuts = [m.end() for m in _SENT_SPLIT.finditer(text)]
    if not cuts:
        return [text]
    parts: list[str] = []
    prev = 0
    for c in cuts:
        seg = text[prev:c].strip()
        if seg:
            parts.append(seg)
        prev = c
    tail = text[prev:].strip()
    if tail:
        parts.append(tail)
    # 复查：被切开的左半段若以缩写结尾，合回去
    out: list[str] = []
    for p in parts:
        if out and not is_sentence_end(out[-1]):
            out[-1] = join_fragments(out[-1], p)
        else:
            out.append(p)
    return out


def explode_cues(cues: list[dict]) -> list[dict]:
    """把"一条多句"摊平，时间按字符占比分。合并碎片前必须走过这一步。"""
    out: list[dict] = []
    for i, c in enumerate(cues):
        text = c.get("text", "")
        orig = c.get("_orig", i)
        parts = split_sentences_in_text(text)
        if len(parts) <= 1:
            out.append({**c, "_orig": orig})
            continue
        dur = max(0.2, float(c["end"]) - float(c["start"]))
        weights = [count_chars(p) for p in parts]
        total = sum(weights) or 1
        cursor = float(c["start"])
        for p, w in zip(parts, weights):
            span = dur * w / total
            out.append({**c, "_orig": orig, "start": round(cursor, 3),
                        "end": round(min(float(c["end"]), cursor + span), 3),
                        "text": p.strip()})
            cursor += span
    return out


# ---------------------------------------------------------------- 第二步：合

def build_sentences(cues: list[dict], max_dur: float = 14.0,
                    max_chars: int = 220, gap: float = 1.8) -> list[dict]:
    """把碎行按语义合成完整句子。

    断句依据：句末标点、引号闭合、时间空档、长度上限。四条里任一条触发就收句。
    返回 [{start, end, en, src:[原 cue 下标...]}]，src 用于「严格模式」回填。
    """
    sentences: list[dict] = []
    buf: list[dict] = []
    text = ""

    def flush():
        nonlocal buf, text
        if not buf:
            return
        body = text.strip()
        if body:
            sentences.append({
                "start": round(float(buf[0]["start"]), 3),
                "end": round(float(buf[-1]["end"]), 3),
                "en": body,
                "src": [b.get("_orig", b["_i"]) for b in buf],
                # 每片在原条里实际占多少字 —— 严格模式回填时按这个分，
                # 用整条原文的长度会把"这一条里其实不属于本句"的部分也算进去
                "srcw": [count_chars(b.get("text", "")) for b in buf],
            })
        buf, text = [], ""

    for i, cue in enumerate(cues):
        body = (cue.get("text") or "").strip()
        if not body:
            continue
        c = {**cue, "_i": i}
        # 空档太大：上句一定说完了
        if buf and float(c["start"]) - float(buf[-1]["end"]) > gap:
            flush()
        # 会超长：收掉再开新的
        if buf and (float(c["end"]) - float(buf[0]["start"]) > max_dur
                    or count_chars(text) + count_chars(body) > max_chars):
            if is_sentence_end(text):
                flush()
            elif float(c["end"]) - float(buf[0]["start"]) > max_dur * 1.5:
                flush()
        text = join_fragments(text, body) if buf else body
        buf.append(c)
        if is_sentence_end(text) and _open_quotes(text) == 0:
            flush()
        elif is_sentence_end(text) and count_chars(text) > max_chars * 0.6:
            flush()
    flush()
    return sentences


def sentences_from_cues(cues: list[dict], **kw) -> list[dict]:
    """拆 + 合，一步到位。"""
    return build_sentences(explode_cues(cues), **kw)


# ---------------------------------------------------------------- 第三步：断行

# 中文行首的禁则字符（不能出现在行首）
_NO_HEAD = set("，。；：、！？）》」』”’,.;:!?)]}%…—")
# 断行时优先在这些标点后面切
_STRONG_BREAK = set("。！？；")
_WEAK_BREAK = set("，、：")
# 在这些词前面切，读起来最像人说话。双字连词最可靠；单字虚词只在
# 后面不跟标点时才用 —— "一段时间了，"这种，"了"是句末语气，不能在它前面断。
_SOFT_HEADS_2 = (
    "所以", "因为", "但是", "可是", "不过", "而且", "然后", "如果", "虽然",
    "就是", "只是", "还有", "于是", "其实", "而是", "并且", "以及", "或者",
    "要是", "哪怕", "既然", "即使", "除了", "关于", "对于", "为了", "已经",
    "可能", "应该", "需要", "怎么", "什么", "为什么", "这就是",
)
_SOFT_HEADS_1 = ("但", "而", "就", "都", "也", "又", "还", "把", "被", "让",
                 "给", "对", "从", "向", "以", "这", "那", "谁", "哪", "啥")
_SOFT_HEADS = _SOFT_HEADS_2 + _SOFT_HEADS_1


def _is_word_char(c: str) -> bool:
    return bool(c) and bool(re.match(r"[A-Za-z0-9]", c))


def _best_cut(s: str, max_len: int) -> int:
    """在 [0.55*max_len, max_len] 里找一个最像话的切点。返回切点下标。"""
    lo = max(2, int(max_len * 0.55))
    hi = min(len(s), max_len)
    if hi <= lo:
        return hi

    best = -1
    for i in range(lo, hi):
        if s[i - 1] in _STRONG_BREAK:
            best = i                     # 强标点后面切，最自然
    if best > 0:
        return best

    weak = -1
    for i in range(lo, hi):
        if s[i - 1] in _WEAK_BREAK:
            weak = i
    if weak > 0:
        return weak

    # 双字连词优先
    soft = -1
    for head in _SOFT_HEADS_2:
        p = s.rfind(head, lo, hi)
        if p > 0:
            soft = max(soft, p)
    if soft > 0:
        return soft

    # 单字虚词：后面紧跟标点就跳过（那是句末语气词，切了就断气）
    for head in _SOFT_HEADS_1:
        p = s.rfind(head, lo, hi)
        if p > 0 and p + 1 < len(s) and s[p + 1] not in _NO_HEAD:
            soft = max(soft, p)
    if soft > 0:
        return soft

    cut = hi
    # 别把英文单词 / 数字切开
    while cut > lo and _is_word_char(s[cut - 1]) and _is_word_char(s[cut]):
        cut -= 1
    return cut if cut > lo else hi


def cut_text(text: str, max_len: int = 16, tolerance: float = 1.25) -> list[str]:
    """把一句译文按中文节奏切成若干条，每条尽量不超过 max_len 字。

    留 25% 的容忍度：21 个字非按 18 切不可，就变成 15+6 两条，
    时间分配也被拉歪，不如一条折两行显示 —— 少切一刀，读着反而顺。
    """
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    if count_chars(text) <= max_len * tolerance:
        return [text]
    out: list[str] = []
    rest = text
    guard = 0
    while count_chars(rest) > max_len and guard < 200:
        guard += 1
        cut = _best_cut(rest, max_len)
        head, tail = rest[:cut].strip(), rest[cut:].strip()
        if not head:                       # 理论上不会发生，防死循环
            cut = max_len
            head, tail = rest[:cut], rest[cut:]
        # 行首禁则：把标点挪到上一块
        while tail and tail[0] in _NO_HEAD and len(head) < max_len + 3:
            head += tail[0]
            tail = tail[1:].lstrip()
        if head:
            out.append(head)
        rest = tail
    if rest:
        # 尾巴太短就和上一块合并（多几个字也比孤零零强）
        if out and count_chars(rest) <= 3 and count_chars(out[-1]) + count_chars(rest) <= max_len + 4:
            out[-1] += rest
        else:
            out.append(rest)
    return out


def wrap_lines(text: str, max_len: int, max_lines: int = 2) -> str:
    """把一条字幕折成最多 max_lines 行，行间用 \\n。

    中文按标点断；英文按词断，并且**均衡**折行 —— 98 个字符折成 42+56
    这种长短腿看着比居中难看得多。
    """
    text = (text or "").strip()
    if not text or max_lines <= 1:
        return text
    if _CJK_CHAR.search(text):
        if count_chars(text) <= max_len:
            return text
        pieces = cut_text(text, max_len)
        if len(pieces) <= max_lines:
            return "\n".join(pieces)
        n = len(pieces)
        per = (n + max_lines - 1) // max_lines
        lines = ["".join(pieces[i:i + per]) for i in range(0, n, per)]
        return "\n".join(lines[:max_lines])
    # 拉丁文：均衡折行
    if len(text) <= max_len:
        return text
    words = text.split()
    n = min(max_lines, max(1, -(-len(text) // max_len)))
    per = len(text) / n
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}" if cur else w
        if cur and len(cand) > per and len(lines) < n - 1:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return "\n".join(lines[:max_lines])


def cut_english(text: str, max_chars: int = 44, tolerance: float = 1.15) -> list[str]:
    """英文字幕按词折行，别把单词劈成两半。

    同样留点余地：87 个字符硬按 84 切成两条，第二条就只剩半句，
    中文还得跟着切，语序一错位整条字幕就对不上了。
    """
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= max_chars * tolerance:
        return [text] if text else []
    words = text.split(" ")
    out, cur = [], ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= max_chars:
            cur += " " + w
        else:
            out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------- 严格模式：把整句译文切回原条数

def distribute_zh(zh: str, weights: list[float]) -> list[str]:
    """按各原条目的内容量，把整句译文切回同样条数。

    严格模式用。切点尽量落在标点/虚词处，做不到就按字符比例硬分。
    """
    n = len(weights)
    zh = (zh or "").strip()
    if n <= 1 or not zh:
        return [zh] + [""] * (n - 1)
    total = sum(weights) or 1.0
    out: list[str] = []
    rest = zh
    acc = 0.0
    for k, w in enumerate(weights[:-1]):
        acc += w
        target = max(2, int(round(len(zh) * acc / total)))
        target = min(target, len(rest) - (n - 1 - k))     # 给后面留够字
        if target <= 0:
            out.append("")
            rest = rest.lstrip()
            continue
        if len(rest) <= target:
            out.append(rest)
            rest = ""
            continue
        # 在 target 附近找标点
        win_lo = max(1, target - 4)
        win_hi = min(len(rest), target + 4)
        cut = -1
        for i in range(win_lo, win_hi):
            if rest[i - 1] in _STRONG_BREAK or rest[i - 1] in _WEAK_BREAK:
                cut = i
        if cut < 0:
            for head in _SOFT_HEADS:
                p = rest.rfind(head, win_lo, win_hi)
                if p > 0 and p + 1 < len(rest) and rest[p + 1] not in _NO_HEAD:
                    cut = max(cut, p)
        if cut < 0:
            cut = target
            while cut > 1 and _is_word_char(rest[cut - 1]) and _is_word_char(rest[cut]):
                cut -= 1
        head, tail = rest[:cut].strip(), rest[cut:].strip()
        while tail and tail[0] in _NO_HEAD:
            head += tail[0]
            tail = tail[1:].lstrip()
        out.append(head)
        rest = tail
    out.append(rest.strip())
    return out


# ---------------------------------------------------------------- 中英同步切分

def split_by_ratio(text: str, k: int, kind: str = "zh") -> list[str]:
    """把一段文字切成恰好 k 段，切点吸附到标点（中文）或空格（英文）。

    双语字幕要一行英文一行中文，两边的断点必须同步，所以不能各切各的。
    """
    text = (text or "").strip()
    if k <= 1 or not text:
        return [text] if text else [""] * max(1, k)
    n = len(text)
    if n <= k:                                   # 字比段还少，逐字分
        return [text[i:i + 1] for i in range(n)] + [""] * (k - n)

    cuts: list[int] = []
    prev = 0
    for i in range(1, k):
        target = n * i / k
        lo = max(prev + 1, int(target) - 5)
        hi = min(n - (k - i), int(target) + 5)
        best = -1
        if hi > lo:
            for j in range(lo, hi + 1):
                if kind == "zh":
                    if text[j - 1] in _STRONG_BREAK or text[j - 1] in _WEAK_BREAK:
                        best = j
                else:
                    if text[j - 1] == " ":
                        best = j
        if best <= prev and kind == "zh" and hi > lo:
            # 附近没有标点，退一步找虚词边界，总比把"答案"劈成"答/案"强
            for head in _SOFT_HEADS:
                p = text.rfind(head, lo, hi)
                if p > prev and p > best:
                    best = p
        cut = best if best > prev else int(round(target))
        cut = min(max(cut, prev + 1), n - (k - i))
        cuts.append(cut)
        prev = cut

    bounds = [0] + cuts + [n]
    return [text[a:b].strip() for a, b in zip(bounds, bounds[1:])]


def split_by_ratios(text: str, ratios: list[float], kind: str = "en") -> list[str]:
    """按给定的累计比例切段（比 split_by_ratio 更听话，比例由中文那边定）。"""
    text = (text or "").strip()
    k = len(ratios) + 1
    if not text:
        return [""] * k
    n = len(text)
    if k <= 1:
        return [text]
    if n <= k:
        return [text[i:i + 1] for i in range(n)] + [""] * (k - n)

    cuts: list[int] = []
    prev = 0
    for i, r in enumerate(ratios):
        target = n * max(0.0, min(1.0, r))
        win = 12 if kind == "en" else 6      # 英文宁可偏一点也要切在逗号上
        lo = max(prev + 1, int(target) - win)
        hi = min(n - (k - 1 - i), int(target) + win)
        best = -1
        if hi > lo and kind == "en":
            # 英文优先切在逗号/从句边界，其次才是任意空格 —— 否则会出现
            # "…I think the | answer is pretty" 这种把 the answer 拆开的断法
            for j in range(lo, hi + 1):
                if text[j - 1] in ",;:—–":
                    best = j
            if best <= prev:
                for j in range(lo, hi + 1):
                    if text[j - 1] == " ":
                        best = j
        elif hi > lo:
            for j in range(lo, hi + 1):
                if text[j - 1] in _WEAK_BREAK or text[j - 1] in _STRONG_BREAK:
                    best = j
        cut = best if best > prev else int(round(target))
        cut = min(max(cut, prev + 1), n - (k - 1 - i))
        cuts.append(cut)
        prev = cut
    bounds = [0] + cuts + [n]
    return [text[a:b].strip() for a, b in zip(bounds, bounds[1:])]


def split_pair(en: str, zh: str, zh_len: int = 18, en_len: int = 84) -> list[tuple[str, str]]:
    """一个句子的英文和中文，同步切成同样条数，供双语字幕逐条显示。

    **中文说了算**：先按中文的自然停顿切段，再让英文跟着中文的比例走。
    反过来（各切各的）会出大问题 —— 英文按字符切在 84，中文按标点切在 16，
    两条字幕的时间就分歪了，右边那句中文只有半秒，谁都读不完。
    """
    en = (en or "").strip()
    zh = (zh or "").strip()
    if not zh:
        es = cut_english(en, en_len) if en else []
        return [(e, "") for e in es] or [("", "")]
    zs = cut_text(zh, zh_len)
    es0 = cut_english(en, en_len) if en else []
    k = max(len(zs), len(es0), 1)
    if len(zs) < k:
        zs = split_by_ratio(zh, k, "zh")
    if en:
        lens = [max(1, len(z)) for z in zs]
        total = sum(lens) or 1
        acc, ratios = 0, []
        for l in lens[:-1]:
            acc += l
            ratios.append(acc / total)
        es = split_by_ratios(en, ratios, "en")
    else:
        es = [""] * k
    zs = (zs + [""] * k)[:k]
    es = (es + [""] * k)[:k]
    return list(zip([e.strip() for e in es], [z.strip() for z in zs]))
