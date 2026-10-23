/**
 * 无头 Edge + CDP 跑一遍真实界面：断言可见性、走完整翻译流程、截图。
 * 零依赖，Node 22 自带 WebSocket 和 fetch。
 */
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const PORT = 9333;
const URL = process.env.APP_URL || "http://127.0.0.1:8791/";
const OUT = path.join(__dirname, "..", "screenshots");
const SRT = path.join(__dirname, "_input_ui.srt");

if (!fs.existsSync(OUT)) fs.mkdirSync(OUT, { recursive: true });
fs.writeFileSync(SRT, `1
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
working harder. Right.

6
00:00:09,600 --> 00:00:12,400
Nobody tells you this at twenty-five.
`, "utf8");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fails = [];
function check(cond, msg) {
  console.log((cond ? "  ok  " : "  x   ") + msg);
  if (!cond) fails.push(msg);
}

class CDP {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.waiting = new Map();
    this.events = [];
    ws.addEventListener("message", (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && this.waiting.has(m.id)) {
        const { res, rej } = this.waiting.get(m.id);
        this.waiting.delete(m.id);
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
      } else if (m.method) {
        this.events.push(m);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      this.waiting.set(id, { res, rej });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.waiting.has(id)) {
          this.waiting.delete(id);
          rej(new Error("CDP timeout: " + method));
        }
      }, 30000);
    });
  }
  async eval(expr) {
    const r = await this.send("Runtime.evaluate", {
      expression: expr, returnByValue: true, awaitPromise: true,
    });
    if (r.exceptionDetails) {
      throw new Error("JS 异常: " + JSON.stringify(r.exceptionDetails).slice(0, 300));
    }
    return r.result.value;
  }
  async shot(name) {
    const r = await this.send("Page.captureScreenshot", { format: "png" });
    const p = path.join(OUT, name);
    fs.writeFileSync(p, Buffer.from(r.data, "base64"));
    console.log("    截图 " + p);
    return p;
  }
}

(async () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "stui-"));
  const proc = spawn(EDGE, [
    "--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
    "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--window-size=1440,900", "about:blank",
  ], { stdio: "ignore" });

  let ver = null;
  for (let i = 0; i < 40; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      ver = await r.json();
      break;
    } catch (e) { await sleep(250); }
  }
  if (!ver) { console.log("Edge 起不来"); process.exit(1); }

  const ws = new WebSocket(ver.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r));
  const b = new CDP(ws);

  const { targetId } = await b.send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await b.send("Target.attachToTarget", { targetId, flatten: true });

  // 会话级 CDP：把 sessionId 挂在每条消息上
  const sws = ws;
  const send2 = (method, params = {}) => new Promise((res, rej) => {
    const id = 10000 + Math.floor(Math.random() * 100000);
    const onMsg = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id === id) {
        sws.removeEventListener("message", onMsg);
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
      }
    };
    sws.addEventListener("message", onMsg);
    sws.send(JSON.stringify({ id, method, params, sessionId }));
    setTimeout(() => rej(new Error("timeout " + method)), 30000);
  });

  const cdp = {
    send: send2,
    async eval(expr) {
      const r = await send2("Runtime.evaluate", {
        expression: expr, returnByValue: true, awaitPromise: true,
      });
      if (r.exceptionDetails) throw new Error("JS 异常: " + JSON.stringify(r.exceptionDetails).slice(0, 400));
      return r.result.value;
    },
    async shot(name) {
      const r = await send2("Page.captureScreenshot", { format: "png" });
      const p = path.join(OUT, name);
      fs.writeFileSync(p, Buffer.from(r.data, "base64"));
      console.log("    截图 " + p);
    },
  };

  const errors = [];
  sws.addEventListener("message", (ev) => {
    const m = JSON.parse(ev.data);
    if (m.method === "Runtime.exceptionThrown") {
      errors.push(JSON.stringify(m.params.exceptionDetails.exception || m.params).slice(0, 300));
    }
  });

  await send2("Runtime.enable");
  await send2("Page.enable");
  await send2("DOM.enable");
  await send2("Emulation.setDeviceMetricsOverride", {
    width: 1440, height: 900, deviceScaleFactor: 1, mobile: false,
  });
  await send2("Page.navigate", { url: URL });
  await sleep(2200);

  console.log("1) 初始状态");
  let v = await cdp.eval(`(() => {
    const g = (id) => { const e = document.getElementById(id); return e ? e.offsetHeight : -1; };
    return {empty: g("emptyState"), prog: g("progressBox"), res: g("resultBox"),
            picked: g("picked"), start: document.getElementById("btnStart").disabled,
            title: document.title};
  })()`);
  check(v.title.includes("字幕"), "标题正确：" + v.title);
  check(v.empty > 50, "空状态可见（高 " + v.empty + "px）");
  check(v.prog === 0, "进度区隐藏");
  check(v.res === 0, "结果区隐藏");
  check(v.picked === 0, "已选文件区隐藏（[hidden] 生效）");
  check(v.start === true, "没选文件时按钮禁用");
  await cdp.shot("1-empty.png");

  console.log("2) 交互");
  v = await cdp.eval(`(() => {
    const seg = document.getElementById("engineSeg");
    seg.querySelector('[data-v="llm"]').click();
    const box = document.getElementById("llmBox");
    const h1 = box.offsetHeight;
    seg.querySelector('[data-v="google"]').click();
    const h2 = box.offsetHeight;
    document.querySelector('#modeOpt [data-v="strict"]').click();
    const strictOn = document.querySelector('#modeOpt [data-v="strict"]').classList.contains("on");
    document.querySelector('#modeOpt [data-v="natural"]').click();
    document.getElementById("swBilingual").click();
    const rowHidden = document.getElementById("rowZhFirst").offsetHeight;
    document.getElementById("swBilingual").click();
    return {h1, h2, strictOn, rowHidden};
  })()`);
  check(v.h1 > 100, "切到 AI 大模型时配置区展开（高 " + v.h1 + "px）");
  check(v.h2 === 0, "切回免费通道时收起");
  check(v.strictOn === true, "分句方式可切换");
  check(v.rowHidden === 0, "关掉双语时「中文在上」跟着隐藏");

  console.log("3) 选文件");
  const doc = await send2("DOM.getDocument", {});
  const node = await send2("DOM.querySelector", { nodeId: doc.root.nodeId, selector: "#fileInput" });
  await send2("DOM.setFileInputFiles", { files: [SRT], nodeId: node.nodeId });
  await sleep(500);
  v = await cdp.eval(`(() => {
    const p = document.getElementById("picked");
    return {h: p.offsetHeight, name: document.getElementById("pickName").textContent,
            dis: document.getElementById("btnStart").disabled,
            dropHidden: document.getElementById("dropZone").offsetHeight};
  })()`);
  check(v.h > 30, "选中文件后显示文件名（高 " + v.h + "px）");
  check(v.name.includes("_input_ui"), "文件名正确：" + v.name);
  check(v.dis === false, "按钮可点了");
  check(v.dropHidden === 0, "拖拽区让位");
  await cdp.shot("2-picked.png");

  console.log("4) 跑流程");
  await cdp.eval(`document.getElementById("btnStart").click()`);
  await sleep(700);
  v = await cdp.eval(`({stage: document.getElementById("progStage").textContent,
                       h: document.getElementById("progressBox").offsetHeight,
                       done: document.getElementById("resultBox").offsetHeight})`);
  // 小字幕文件一秒钟就跑完了，进度区一闪而过很正常
  check(v.h > 100 || v.done > 100, "进度区出现或已直接完成（进度高 " + v.h + "px）");
  console.log("    阶段：" + v.stage);

  let done = false;
  for (let i = 0; i < 150; i++) {
    await sleep(600);
    const s = await cdp.eval(`(() => {
      const r = document.getElementById("resultBox");
      const e = document.getElementById("errorBox");
      return {done: r.offsetHeight > 100, err: e.offsetHeight > 50,
              msg: document.getElementById("errMsg").textContent};
    })()`);
    if (s.err) { check(false, "任务出错：" + s.msg); break; }
    if (s.done) { done = true; break; }
  }
  check(done, "跑完了，结果区出现");

  if (done) {
    v = await cdp.eval(`(() => {
      const rows = document.querySelectorAll("#tbody tr");
      const links = [...document.querySelectorAll("#dlList a")].map(a => a.textContent);
      const firstZh = rows.length ? rows[0].querySelector(".c-zh").textContent : "";
      const firstEn = rows.length ? rows[0].querySelector(".c-en").textContent : "";
      return {rows: rows.length, links, firstZh, firstEn,
              count: document.getElementById("statCount").textContent,
              tblH: document.getElementById("tblWrap").offsetHeight,
              warn: document.getElementById("warnBox").offsetHeight};
    })()`);
    check(v.rows > 0, `预览表 ${v.rows} 行，计数显示 ${v.count}`);
    check(v.links.length >= 2, "下载按钮：" + v.links.join(" / "));
    check(/[\u4e00-\u9fff]/.test(v.firstZh), "第一行有中文：" + v.firstZh);
    check(v.firstEn.length > 3, "第一行有原文：" + v.firstEn);
    check(v.tblH > 200, "表格区可见（高 " + v.tblH + "px）");
    await cdp.shot("3-result.png");
    await cdp.eval(`document.getElementById("tblWrap").scrollTop = 320`);
    await sleep(300);
    await cdp.shot("4-scrolled.png");
  }

  check(errors.length === 0, "页面无 JS 异常" + (errors.length ? "：" + errors[0] : ""));

  // 窄屏
  await send2("Emulation.setDeviceMetricsOverride", {
    width: 430, height: 900, deviceScaleFactor: 1, mobile: true,
  });
  await sleep(600);
  const narrow = await cdp.eval(`(() => {
    const s = document.querySelector("aside");
    const m = document.querySelector(".main");
    return {h: s.getBoundingClientRect().height, w: m.getBoundingClientRect().width,
            docW: document.documentElement.scrollWidth};
  })()`);
  check(narrow.docW <= 440, "窄屏没有横向溢出（文档宽 " + narrow.docW + "px）");
  await cdp.shot("5-mobile.png");

  ws.close();
  proc.kill();
  console.log("=".repeat(50));
  if (fails.length) {
    console.log("FAIL " + fails.length);
    fails.forEach((f) => console.log("  - " + f));
    process.exit(1);
  }
  console.log("PASS 界面全过");
})().catch((e) => {
  console.error("崩了:", e.message);
  process.exit(1);
});
