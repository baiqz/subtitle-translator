const https = require('https');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const BASE = 'https://baiqz.github.io/subtitle-translator/';
const DOCS = path.join(__dirname, 'docs');

function get(url, redirects = 0) {
  return new Promise((resolve) => {
    https.get(url, { headers: { 'User-Agent': 'verify' } }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location && redirects < 5) {
        res.resume();
        return resolve(get(new URL(res.headers.location, url).href, redirects + 1));
      }
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, buf: Buffer.concat(chunks) }));
    }).on('error', (e) => resolve({ status: 0, error: e.message }));
  });
}

const norm = (buf) => Buffer.from(buf.toString('utf8').replace(/\r\n/g, '\n'), 'utf8');
const sha = (buf) => crypto.createHash('sha256').update(buf).digest('hex').slice(0, 12);

const TARGETS = ['index.html', 'preview.png', 'mobile.png'];

(async () => {
  const deadline = Date.now() + 180000;
  let round = 0;
  while (Date.now() < deadline) {
    round++;
    const results = [];
    let allOk = true;
    for (const name of TARGETS) {
      const local = fs.readFileSync(path.join(DOCS, name));
      const r = await get(BASE + name + '?r=' + round);
      if (r.status !== 200) { allOk = false; results.push(`${name}: HTTP ${r.status}`); continue; }
      const isText = /\.(html|css|js|txt)$/.test(name);
      const lh = sha(isText ? norm(local) : local);
      const rh = sha(isText ? norm(r.buf) : r.buf);
      const ok = lh === rh;
      if (!ok) allOk = false;
      results.push(`${name}: ${ok ? 'OK' : 'DIFF'} local=${lh} live=${rh} (${r.buf.length}B)`);
    }
    console.log(`[第 ${round} 轮] ` + results.join(' | '));
    if (allOk) {
      console.log('\n上线确认：线上内容与本地一致');
      process.exit(0);
    }
    await new Promise((r) => setTimeout(r, 8000));
  }
  console.log('\n超时：线上内容仍未与本地一致');
  process.exit(1);
})();
