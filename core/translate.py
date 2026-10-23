"""翻译引擎。

两条通道：
- google：免费公开端点，不用 key，开箱能用。按行批量送，靠返回里的 \\n 对齐；
  万一对不齐就降级成逐条并发，宁可慢一点也不能串行。
- llm：OpenAI 兼容接口（硅基流动 / DeepSeek / 本地 Ollama 都行），
  一次送一批带编号的句子，要求逐条返回。质量上一档，代价是要配 key。

两条通道都会经过同一层后处理：口语缩写先归一化再翻（不然 gonna / kinda
会被机翻得莫名其妙），翻完再规范中文标点。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

GOOGLE_URL = ("https://translate.googleapis.com/translate_a/single"
              "?client=gtx&sl={sl}&tl={tl}&dt=t&q={q}")

# 备用通道：国内可直连、免 key，按天有额度。质量接近 Google，但只能逐条问。
MYMEMORY_URL = "https://api.mymemory.translated.net/get?q={q}&langpair={pair}"
_MYMEMORY_BAD = ("MYMEMORY WARNING", "QUERY LENGTH LIMIT", "USED ALL AVAILABLE")

# 口语缩写先展开：机翻对 gonna / wanna 的处理很不稳定
_SPOKEN = {
    r"\bgonna\b": "going to", r"\bwanna\b": "want to", r"\bgotta\b": "have to",
    r"\bkinda\b": "kind of", r"\bsorta\b": "sort of", r"\bdunno\b": "do not know",
    r"\bcuz\b": "because", r"\b'?cause\b": "because", r"\blemme\b": "let me",
    r"\bgimme\b": "give me", r"\bya\b": "you", r"\by'know\b": "you know",
    r"\bain't\b": "is not", r"\bc'mon\b": "come on",
}

# 中文标点规范化（只动明显是英文标点误用的地方）
_ZH_FIX = [
    (re.compile(r"(?<=[\u4e00-\u9fff])\s*,\s*"), "，"),
    (re.compile(r"(?<=[\u4e00-\u9fff])\s*;\s*"), "；"),
    (re.compile(r"(?<=[\u4e00-\u9fff])\s*:\s*"), "："),
    (re.compile(r"(?<=[\u4e00-\u9fff])\s*\?\s*"), "？"),
    (re.compile(r"(?<=[\u4e00-\u9fff])\s*!\s*"), "！"),
    (re.compile(r"(?<=[\u4e00-\u9fff])\s+"), ""),      # 中文之间不留空格
    (re.compile(r"(?<=[\u4e00-\u9fff])(?=[A-Za-z0-9])"), " "),   # 中英之间补空格
    (re.compile(r"(?<=[A-Za-z0-9])(?=[\u4e00-\u9fff])"), " "),
    (re.compile(r"…{2,}|\.{3,}"), "……"),
    (re.compile(r"[ \t]{2,}"), " "),
]

# 整句匹配才用的口语短句。机翻见到孤零零的 "Right." 会译成"正确的"，
# 字幕里得是"对"。只在整条内容就是这几个词时才命中，误伤面极小。
_IDIOM = {
    "right": "对。", "yeah": "嗯。", "yep": "嗯。", "yup": "嗯。", "nope": "不。",
    "no": "不。", "yes": "是。", "okay": "好。", "ok": "好。", "sure": "当然。",
    "exactly": "没错。", "of course": "当然。", "come on": "拜托。",
    "no way": "不可能吧。", "thank you": "谢谢。", "thanks": "谢了。",
    "sorry": "对不起。", "i'm sorry": "对不起。", "please": "求你了。",
    "hello": "你好。", "hi": "嗨。", "hey": "嘿。", "bye": "再见。",
    "good morning": "早上好。", "good night": "晚安。", "wow": "哇。",
    "oh my god": "天啊。", "what": "什么？", "why": "为什么？",
    "really": "真的？", "are you serious": "你认真的吗？",
    "let's go": "走吧。", "i know": "我知道。", "i don't know": "我不知道。",
    "me too": "我也是。", "never mind": "算了。", "shut up": "闭嘴。",
    "watch out": "小心。", "hold on": "等一下。", "hurry up": "快点。",
}


def _idiom(text: str) -> str:
    key = re.sub(r"[^a-z' ]", "", (text or "").lower()).strip()
    key = re.sub(r"\s+", " ", key)
    return _IDIOM.get(key, "") if len(key) <= 18 else ""


def normalize_source(text: str) -> str:
    out = text or ""
    for pat, rep in _SPOKEN.items():
        out = re.sub(pat, rep, out, flags=re.I)
    return re.sub(r"\s+", " ", out).strip()


def polish(text: str, target: str = "zh") -> str:
    """清掉机翻常见的手尾：多余空格、半角标点、乱七八糟的省略号。"""
    out = (text or "").strip()
    if not out:
        return ""
    for pat, rep in _ZH_FIX:
        out = pat.sub(rep, out)
    out = re.sub(r"^[\s,，。;；:：]+", "", out)
    out = re.sub(r"\s+([，。；：！？、])", r"\1", out)
    return out.strip()


# ---------------------------------------------------------------- 术语保护

_PUA = "\ue000{}\ue001"


def _protect(text: str, terms: list[tuple[str, str]]) -> tuple[str, list[tuple[str, str]]]:
    """用私用区占位符把术语藏起来，翻完再换回去。长词优先，避免被短词抢先。"""
    hits: list[tuple[str, str]] = []
    if not terms or not text:
        return text, hits
    for idx, (src, dst) in enumerate(terms):
        if not src:
            continue
        token = _PUA.format(idx)
        if src in text:
            text = text.replace(src, token)
            hits.append((token, dst))
    return text, hits


def _restore(text: str, hits: list[tuple[str, str]]) -> tuple[str, bool]:
    lost = False
    for token, dst in hits:
        if token in text:
            text = text.replace(token, dst)
        else:
            lost = True
    return text, lost


def parse_glossary(raw: str) -> list[tuple[str, str]]:
    """术语表：一行一条，用 => 或 tab 或逗号分隔。没给译法就当"别翻"。"""
    terms: list[tuple[str, str]] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\s*(?:=>|->|\t|=|\|)\s*", line, maxsplit=1)
        if len(parts) == 1:
            parts = [p.strip() for p in line.split(",", 1)]
        src = parts[0].strip()
        dst = parts[1].strip() if len(parts) > 1 and parts[1].strip() else src
        if src:
            terms.append((src, dst))
    terms.sort(key=lambda p: -len(p[0]))
    return terms


# ---------------------------------------------------------------- 引擎

class TranslateError(RuntimeError):
    pass


class Translator:
    def __init__(self, engine: str = "google", proxy: str = "", timeout: float = 25.0,
                 llm_base: str = "", llm_key: str = "", llm_model: str = "",
                 glossary: str = "", log=None):
        self.engine = (engine or "google").lower()
        self.proxy = (proxy or "").strip()
        self.timeout = timeout
        self.llm_base = (llm_base or "").rstrip("/")
        self.llm_key = (llm_key or "").strip()
        self.llm_model = (llm_model or "").strip()
        self.terms = parse_glossary(glossary)
        self.log = log or (lambda m: None)
        self._lock = __import__("threading").Lock()
        self.stats = {"batches": 0, "fallbacks": 0, "term_lost": 0, "mymemory": 0}

    # ---- HTTP
    def _openers(self):
        """代理优先、直连兜底 —— 换台机器也不用手改配置。"""
        out = []
        if self.proxy:
            out.append(urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})))
        out.append(urllib.request.build_opener(urllib.request.ProxyHandler({})))
        return out

    def _fetch(self, url: str, data: bytes | None = None, headers: dict | None = None,
               tries: int = 3, timeout: float | None = None) -> bytes:
        timeout = timeout or self.timeout
        last: Exception | None = None
        for opener in self._openers():
            for attempt in range(tries):
                try:
                    req = urllib.request.Request(url, data=data, headers=headers or {})
                    with opener.open(req, timeout=timeout) as r:
                        return r.read()
                except urllib.error.HTTPError as e:
                    last = e
                    if e.code in (400, 401, 403, 404):
                        break                      # 换个出口也没用，直接抛
                    time.sleep(0.6 * (attempt + 1))
                except Exception as e:             # 网络层问题，值得再试
                    last = e
                    time.sleep(0.6 * (attempt + 1))
        raise TranslateError(f"请求翻译服务失败：{type(last).__name__}: {last}")

    # ---- Google
    def _google_one_batch(self, batch: list[str], sl: str, tl: str) -> list[str] | None:
        q = "\n".join(b.replace("\n", " ") for b in batch)
        url = GOOGLE_URL.format(sl=sl, tl=tl, q=urllib.parse.quote(q))
        raw = self._fetch(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
            joined = "".join(seg[0] for seg in (data[0] or []) if seg and seg[0])
        except Exception:
            return None
        lines = [ln for ln in joined.split("\n")]
        # 尾部的空行是 Google 补的换行，不算行
        while lines and not lines[-1].strip():
            lines.pop()
        if len(lines) == len(batch):
            return lines
        return None

    def _google(self, texts: list[str], sl: str, tl: str, workers: int = 6) -> list[str]:
        # 先花几秒确认通道通不通。被墙时逐批重试要白等十几分钟，
        # 不如干脆点，直接换免代理的备用通道。
        try:
            self._fetch(GOOGLE_URL.format(sl=sl, tl=tl, q="hi"),
                        headers={"User-Agent": "Mozilla/5.0"}, tries=1, timeout=6)
        except TranslateError:
            with self._lock:
                self.stats["fallbacks"] += 1
            self.log("免费通道连不上，自动改用备用通道（免代理，有每日额度）")
            return self._mymemory(texts, "en" if sl == "auto" else sl, tl)

        results: list[str | None] = [None] * len(texts)
        # 按总字符数切批，避免 URL 过长被截断
        batches: list[list[int]] = []
        cur: list[int] = []
        cur_len = 0
        for i, t in enumerate(texts):
            n = len(t) + 1
            if cur and cur_len + n > 1600:
                batches.append(cur)
                cur, cur_len = [], 0
            cur.append(i)
            cur_len += n
        if cur:
            batches.append(cur)

        self.stats["batches"] = len(batches)

        def work(idxs: list[int]):
            payload = [texts[i] for i in idxs]
            try:
                got = self._google_one_batch(payload, sl, tl)
            except TranslateError:
                got = None
            if got is not None:
                for i, g in zip(idxs, got):
                    results[i] = g
                return
            with self._lock:
                self.stats["fallbacks"] += 1
            # 行对不齐：这一批拆成单条重来，慢但不会串行
            for i in idxs:
                try:
                    single = self._google_one_batch([texts[i]], sl, tl)
                    results[i] = single[0] if single else ""
                except TranslateError:
                    results[i] = ""

        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(batches) or 1))) as ex:
            list(ex.map(work, batches))
        return [r if r is not None else "" for r in results]

    # ---- MyMemory（国内直连，免 key，按天额度）
    def _mymemory_one(self, text: str, sl: str, tl: str) -> str:
        if not text.strip():
            return ""
        url = MYMEMORY_URL.format(
            q=urllib.parse.quote(text),
            pair=urllib.parse.quote(f"{sl}|{tl}"))
        raw = self._fetch(url, headers={"User-Agent": "Mozilla/5.0"}, tries=2, timeout=15)
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return ""
        if str(data.get("responseStatus", "")) != "200":
            return ""
        out = ((data.get("responseData") or {}).get("translatedText") or "").strip()
        if any(bad in out.upper() for bad in _MYMEMORY_BAD):
            return ""                     # 额度用完了，别把警告文字当译文
        return out

    def _mymemory(self, texts: list[str], sl: str, tl: str, workers: int = 3) -> list[str]:
        if not texts:
            return []
        results: list[str] = [""] * len(texts)

        def work(i: int) -> None:
            for attempt in range(2):
                try:
                    results[i] = self._mymemory_one(texts[i], sl, tl)
                    return
                except TranslateError:
                    time.sleep(0.8 * (attempt + 1))

        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(texts)))) as ex:
            list(ex.map(work, range(len(texts))))
        with self._lock:
            self.stats["mymemory"] = self.stats.get("mymemory", 0) + sum(
                1 for r in results if r)
        return results

    # ---- LLM
    _SYS = (
        "你是专业的影视字幕译者，把{src}字幕翻译成{tgt}。要求：\n"
        "1. 像人说话，不要书面腔，不要逐字硬译；语气、玩笑、脏话、口癖都要保留。\n"
        "2. 每条输入对应一条输出，不合并、不拆分、不解释、不加编号。\n"
        "3. 保持原意与信息量，可以调整语序；专有名词按下文术语表。\n"
        "4. 输出严格的 JSON 数组，元素是字符串，数量和顺序与输入完全一致。\n"
        "只输出 JSON，不要代码块标记。"
    )

    def _llm_batch(self, batch: list[str], sl: str, tl: str) -> list[str] | None:
        sys_prompt = self._SYS.format(src=sl, tgt=tl)
        if self.terms:
            sys_prompt += "\n术语对照：" + "；".join(f"{a}→{b}" for a, b in self.terms[:60])
        user = json.dumps({"lines": batch}, ensure_ascii=False)
        body = json.dumps({
            "model": self.llm_model,
            "messages": [{"role": "system", "content": sys_prompt},
                         {"role": "user", "content": user}],
            "temperature": 0.3,
            "stream": False,
        }, ensure_ascii=False).encode("utf-8")
        raw = self._fetch(f"{self.llm_base}/chat/completions", data=body, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.llm_key}",
        })
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
            content = data["choices"][0]["message"]["content"]
        except Exception as e:
            raise TranslateError(f"LLM 返回格式异常：{e}")
        content = content.strip()
        content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.M).strip()
        try:
            arr = json.loads(content)
        except Exception:
            m = re.search(r"\[.*\]", content, re.S)
            if not m:
                return None
            try:
                arr = json.loads(m.group(0))
            except Exception:
                return None
        if not isinstance(arr, list) or len(arr) != len(batch):
            return None
        return [str(x) for x in arr]

    def _llm(self, texts: list[str], sl: str, tl: str) -> list[str]:
        if not (self.llm_base and self.llm_key and self.llm_model):
            raise TranslateError("还没填 LLM 接口地址 / 密钥 / 模型名")
        results: list[str | None] = [None] * len(texts)
        batches: list[list[int]] = []
        cur: list[int] = []
        cur_len = 0
        for i, t in enumerate(texts):
            if cur and (cur_len + len(t) > 2600 or len(cur) >= 24):
                batches.append(cur)
                cur, cur_len = [], 0
            cur.append(i)
            cur_len += len(t)
        if cur:
            batches.append(cur)
        for idxs in batches:
            payload = [texts[i] for i in idxs]
            got = None
            for _ in range(2):
                got = self._llm_batch(payload, sl, tl)
                if got is not None:
                    break
            if got is None:
                with self._lock:
                    self.stats["fallbacks"] += 1
                # 拆小一点再来一次，还不行就用免费通道兜底这一批
                got = []
                for t in payload:
                    one = self._llm_batch([t], sl, tl)
                    got.append(one[0] if one else "")
                if any(not g for g in got):
                    google = self._google(payload, sl, tl)
                    got = [g or gg for g, gg in zip(got, google)]
            for i, g in zip(idxs, got):
                results[i] = g
        return [r or "" for r in results]

    # ---- 对外
    def translate(self, texts: list[str], source: str = "en", target: str = "zh-CN",
                  on_progress=None) -> list[str]:
        if not texts:
            return []
        sl = "auto" if source in ("auto", "") else source
        tl = target or "zh-CN"
        prepared: list[str] = []
        hitmap: list[list[tuple[str, str]]] = []
        for t in texts:
            src = normalize_source(t)
            if self.terms and sl == "en":
                src, hits = _protect(src, self.terms)
            else:
                hits = []
            prepared.append(src)
            hitmap.append(hits)

        if self.engine == "llm":
            raw = self._llm(prepared, sl, tl)
        elif self.engine == "mymemory":
            raw = self._mymemory(prepared, "en" if sl == "auto" else sl, tl)
        else:
            raw = self._google(prepared, sl, tl)

        out: list[str] = []
        for i, txt in enumerate(raw):
            if tl.startswith("zh") and sl == "en":
                fixed = _idiom(texts[i])
                if fixed:
                    out.append(fixed)
                    continue
            if not txt and prepared[i]:
                out.append("")
                continue
            txt, lost = _restore(txt, hitmap[i]) if hitmap[i] else (txt, False)
            if lost:
                with self._lock:
                    self.stats["term_lost"] += 1
            out.append(polish(txt, "zh") if tl.startswith("zh") else polish_ascii(txt))
        if on_progress:
            on_progress(1.0)
        return out


def polish_ascii(text: str) -> str:
    """英文/日文等目标的轻量清理。"""
    out = re.sub(r"\s{2,}", " ", (text or "").strip())
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    return out


def make_translator(settings: dict, log=None) -> Translator:
    return Translator(
        engine=settings.get("engine", "google"),
        proxy=settings.get("proxy", ""),
        llm_base=settings.get("llm_base", ""),
        llm_key=settings.get("llm_key", ""),
        llm_model=settings.get("llm_model", ""),
        glossary=settings.get("glossary", ""),
        log=log,
    )
