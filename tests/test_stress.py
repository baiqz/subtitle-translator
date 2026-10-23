"""压力与格式回归：60 句批量翻译的对齐稳定性 + 三种字幕格式往返。"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import subtitle, textseg
from core.translate import Translator

LINES = [
    "Most people think learning is about discipline.",
    "It is not.",
    "Discipline is what you fall back on when the interesting part is gone.",
    "And the interesting part is always gone by Tuesday.",
    "So here is what actually works: make the first step embarrassingly small.",
    "Not small enough to be easy — small enough to be stupid.",
    "Two minutes. One paragraph. One push-up.",
    "You are not trying to make progress. You are trying to stay in the room.",
    "There is a difference, and it matters more than you think.",
    "I learned this the hard way in my second year of college.",
    "I had a plan. It was a beautiful plan. Color-coded, laminated.",
    "It lasted eleven days.",
    "On day twelve I missed one session, and then the whole thing collapsed like a cheap tent.",
    "What I did not understand then is that habits are not built by repetition alone.",
    "They are built by returning.",
    "Missing once is an accident. Missing twice is the start of a new identity.",
    "\"But I don't have time,\" you say.",
    "You do. You have exactly as much time as everyone else who has ever done anything.",
    "What you do not have is a reason that survives contact with a bad mood.",
    "So find that reason before the bad mood arrives.",
    "Write it down. Say it out loud. Tell someone who will ask you about it.",
    "Because your future self is not going to be more motivated than you are right now.",
    "Your future self is going to be tired, and busy, and slightly annoyed.",
    "Plan for that person, not for the hero you imagine yourself to be.",
    "Here is a test.",
    "Take the thing you want to do and cut it in half.",
    "Then cut it in half again.",
    "If it still feels like a chore, cut it once more.",
    "At some point it becomes so small that refusing it looks ridiculous.",
    "That is the point.",
    "Ridiculous is sustainable. Heroic is not.",
    "I have watched people run marathons off two-minute starts.",
    "I have watched people write books one paragraph at a time on the train.",
    "I have also watched brilliant people burn out in three weeks because they aimed at the ceiling on day one.",
    "The ceiling will still be there in a year.",
    "Start on the floor.",
    "Now, some of you are thinking this is obvious advice.",
    "It is obvious. It is also almost never done.",
    "Knowing and doing are separated by a gap that information cannot cross.",
    "Only action crosses it, and action needs to be small enough to survive.",
    "Let me tell you what happened on day thirteen.",
    "I sat down, opened the book, and read one page.",
    "One page. That was the entire victory.",
    "But the next day I read two, and nobody had to convince me.",
    "Momentum is not a metaphor. It is a physical property of a nervous system that has stopped resisting.",
    "The hardest rep is the one that restarts the set.",
    "So if you have already missed a day, this is your signal.",
    "Not tomorrow. Not Monday. Now, and only for two minutes.",
    "You will feel silly. Good. Silly is cheap.",
    "Regret is expensive.",
    "That is all I have.",
    "Thank you for staying with me to the end.",
    "Go do the small thing.",
    "You know which one.",
    "It has been sitting there the whole time.",
    "You were waiting for permission.",
    "This is it.",
]

fails = []


def check(cond, msg):
    print(("  ok  " if cond else "  x   ") + msg)
    if not cond:
        fails.append(msg)


print("1) 批量翻译对齐（%d 句）" % len(LINES))
t = Translator(engine="google", proxy="http://127.0.0.1:33210")
t0 = time.time()
out = t.translate(LINES, "en", "zh-CN")
dt = time.time() - t0
check(len(out) == len(LINES), f"条数一致 {len(out)}/{len(LINES)}")
empty = [i for i, s in enumerate(out) if not s.strip()]
check(not empty, f"没有空译（空的有 {len(empty)} 条：{empty[:5]}）")
bad = [i for i, s in enumerate(out) if not any("\u4e00" <= c <= "\u9fff" for c in s) and len(s.strip()) > 2]
check(not bad, f"都是中文（可疑：{[(i, out[i]) for i in bad[:3]]}）")
print(f"    用时 {dt:.1f}s，{dt/len(LINES)*1000:.0f} ms/句，批次 {t.stats}")

# 逐条抽查前 8 条和最后 2 条，看有没有串行
for i in list(range(6)) + [len(LINES) - 2, len(LINES) - 1]:
    print(f"    {i:2d} {LINES[i][:52]}")
    print(f"       → {out[i]}")

check(out[1].strip() != out[0].strip(), "相邻两条不是同一句（没串行）")
check("哪" in out[-4], f"倒数第 4 句（You know which one）对得上：「{out[-4]}」")
print("2) 格式往返")
cues = [{"start": 0.32, "end": 2.48, "text": "So I've been thinking about", "speaker": ""},
        {"start": 2.48, "end": 4.12, "text": "this for a while now.", "speaker": ""}]
pairs = [{"start": 0.32, "end": 2.48, "en": cues[0]["text"], "zh": "所以我想了很久"},
         {"start": 2.48, "end": 4.12, "en": cues[1]["text"], "zh": "这件事。"}]
for fmt in ("srt", "vtt", "ass"):
    body = subtitle.render(pairs, fmt, "bilingual")
    name = f"x.{fmt}"
    back, got = subtitle.parse(body, name)
    check(len(back) == 2, f"{fmt.upper()} 往返条数 {len(back)}")
    check(abs(back[0]["start"] - 0.32) < 0.02, f"{fmt.upper()} 起始时间 {back[0]['start']}")
    check("thinking" in back[0]["text"], f"{fmt.upper()} 原文保留")
    if fmt == "ass":
        check("所以我想了很久" in body, "ASS 中文进了正文")
    else:
        check("所以我想了很久" in body and "thinking" in body, f"{fmt.upper()} 双语都在")

print("3) 脏数据容忍")
weird = """1
00:00:01,000 --> 00:00:03,000
<i>He said,</i> "go
2
00:00:03,000 --> 00:00:05,000
away." Then he left.

3
00:00:05,000 --> 00:00:07,000
JOHN: Right.

4
00:00:07,000 --> 00:00:09,000
中文行混进来的旧字幕
"""
cs, fmt = subtitle.parse(weird, "w.srt")
check(len(cs) == 4, f"解析出 {len(cs)} 条（应为 4）")
check(not any("<i>" in c["text"] for c in cs), "HTML 标签去掉了")
sents = textseg.sentences_from_cues(cs)
check(len(sents) >= 1, f"分句 {len(sents)} 句")
print("    分句结果：", [s["en"] for s in sents])

print("=" * 54)
if fails:
    print("FAIL", len(fails))
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("PASS 压力与格式全过")
