"""生成测试用英文音频（免 key，走 edge-tts）。只在跑 test_audio.py 前需要。"""
from __future__ import annotations

import asyncio
import os

TEXT = """So I have been thinking about this for a while now, and I think the answer is pretty simple.
It is not about working harder. It is about working on the right thing.
And I learned that the hard way, believe me.
Nobody tells you this when you are twenty five."""

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_speech.mp3")


async def main() -> None:
    import edge_tts
    await edge_tts.Communicate(TEXT, "en-US-AriaNeural").save(OUT)


if __name__ == "__main__":
    asyncio.run(main())
    print(f"已生成 {OUT}（{os.path.getsize(OUT) // 1024} KB）")
