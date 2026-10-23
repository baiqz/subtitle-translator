# 字幕翻译工作台

把英文字幕或英文音频，翻成**读得顺的中文字幕**。

两种输入：字幕文件（SRT / VTT / ASS / TXT）直接翻；音频视频（MP3 / WAV / M4A / MP4 …）先在本地把英文识别成文字再翻。
全程本机运行，文件不上传任何地方。

**主页**：<https://baiqz.github.io/subtitle-translator/>

---

## 开始用

下载 [ZIP](https://github.com/baiqz/subtitle-translator/archive/refs/heads/main.zip) 解压，**双击 `start.bat`**。

第一次运行会自动装依赖、备好 ffmpeg（约 200MB，走清华源），浏览器随后自动打开 <http://127.0.0.1:8791>。之后启动只要几秒。

1. 拖一个字幕或音频进去
2. 选翻译通道（默认免费通道，开箱能用）
3. 点「开始翻译」
4. 出来三个文件：双语、纯中文、纯原文，挑需要的下

关掉黑窗口就停。

**Mac / Linux**

```bash
python3 -m pip install -r requirements.txt
python3 app.py
```

---

## 为什么它翻得比"逐条机翻"顺

问题不在翻译引擎，在喂进去的东西。手工字幕经常长这样：

```
1   So I've been thinking about
2   this for a while now, and
3   I think the answer is pretty
4   simple.
```

四条其实是一句话。逐条翻译，等于让翻译器连读四个没说完的句子 —— 代词指代和转折承接全丢了。

所以管线是：**拆句 → 拼回整句 → 整句翻译 → 按中文节奏断行**。上面四条会合成一条：
「所以我已经思考这个问题有一段时间了，我认为答案很简单。」

另外三个影响观感的决定：

- **中文主导切点**。英文按自己字符数切、中文按自己的标点切，两边的断点必然错位，时间也就分歪了（会出现只有半秒、谁都读不完的一条）。改成中文先切，英文按中文各段的比例跟。
- **留容差**。21 个字非按 18 字切不可，会切成 15+6 两条，不如一条折两行 —— 少切一刀反而更顺。
- **短条并给邻居**。短于 0.65 秒的条目自动合并，眼睛不用抢读。

给「严格对齐」模式留了后路：条数和时间一条不动，只把中文塞进去（要卡某个画面时用），代价是中文可能被切在词中间。

---

## 两条翻译通道

| | 免费通道 | AI 大模型 |
|---|---|---|
| 引擎 | Google 网页接口 | 任意 OpenAI 兼容接口 |
| 要 Key | 不用 | 要 |
| 网络 | 国内需代理 | 硅基流动 / DeepSeek 国内直连 |
| 效果 | 够用 | 更像人话 |

免费通道多行批量翻，1600 字符一批，57 句 1.7 秒；行数对不上就自动降级逐条并发。
术语表里的词用占位符锁住，翻完再换回去，不会被机翻乱改。

---

## 参数

| 参数 | 说明 | 默认 |
|---|---|---|
| 分段方式 | 自然分段（推荐）/ 严格对齐 | 自然分段 |
| 每行字数 | 中文每条上限、英文每行上限 | 18 / 42 |
| 输出格式 | SRT / VTT / ASS | SRT |
| 识别模型 | tiny / base / small / medium / large-v3 | small |
| 代理 | 免费通道要用 | 空 |

识别模型第一次用会自动下载（small 约 480MB），走 hf-mirror 直连 + 断点续传，只下一次。

---

## 常见问题

**翻译通道连不上？** 免费通道走 Google 接口，国内直连不通。右上角圆点能测通断。不想折腾代理就切「AI 大模型」。

**长音频要等多久？** 首次要下模型。13 秒的英文音频，识别加翻译约 8 秒（模型已加载时）。

**怎么保证不偷传文件？** 服务只监听 `127.0.0.1`，别的机器连不进来。语音识别在本机跑，只有待翻译的文字会发给翻译接口。

---

## 开发

```
app.py            Flask 服务：上传、任务、SSE 进度、下载
core/textseg.py   分句与双语同步切分（核心）
core/subtitle.py  SRT/VTT/ASS 解析与导出
core/translate.py 翻译通道、占位符保护、批量对齐
core/asr.py       ffmpeg 抽音 + faster-whisper 转录
core/pipeline.py  串起全流程
tools/bootstrap.py 环境自举：查依赖、备 ffmpeg
tests/            四层测试
```

跑测试（先启动服务）：

```bash
python tests/check_static.py     # 静态自检，不用服务
python tests/gen_speech.py       # 生成测试音频（只需一次）
python tests/test_api.py         # 接口层
python tests/test_audio.py       # 音频全链路
python tests/test_stress.py      # 分句与格式压力
node tests/ui_test.cjs           # 无头浏览器点击 + 截图
```

---

MIT License
