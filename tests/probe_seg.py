import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import subtitle, textseg

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
Mr. Smith said the govern-

7
00:00:11,200 --> 00:00:12,900
ment doesn't care. He said,

8
00:00:12,900 --> 00:00:14,500
"We'll see."

9
00:00:14,500 --> 00:00:16,000
<i>That's</i> the thing.

10
00:00:16,000 --> 00:00:17,200
JOHN: Right.
"""

cues, fmt = subtitle.parse(SRT, "a.srt")
print("fmt", fmt, "cues", len(cues))
for c in cues:
    print(f"  {c['start']:6.2f}-{c['end']:6.2f} sp={c['speaker']!r} {c['text']!r}")

sents = textseg.sentences_from_cues(cues)
print("\n--- sentences:", len(sents))
for s in sents:
    print(f"  {s['start']:6.2f}-{s['end']:6.2f} src={s['src']}")
    print(f"     {s['en']}")

print("\n--- cut_text")
for t in ["我从没想过自己最后会走到这一步，但回头看，每一次走错的路其实都有它的道理。",
          "你知道吗？这件事其实没那么复杂，只是我们总喜欢把它想得很复杂而已。",
          "他说政府根本不在乎这件事，我们走着瞧吧。"]:
    print(" ", textseg.cut_text(t, 16))
