#!/usr/bin/env node
// 개발 PC에서 런박스를 조작하는 도구 (Node 22+, 추가 설치 없음).
//
// 런박스에서 실제 작업(서버 실행·실험·LLM 호출)이 이뤄지고, 이 도구는 명령을 전달하고 결과 파일을 읽기만 한다.
//   - 쓰기(명령 실행): 전용 크롬 창에 런박스 편집기를 띄우고, 크롬 원격 디버깅(CDP)으로 터미널에 명령을 입력한다.
//   - 읽기(결과 확인): 런박스 편집기의 파일 읽기 주소로 /tmp/rb/<작업>.log 를 가져온다.
//
//   node loop/browser/rb.mjs launch              전용 크롬 창 띄우기 (프로필: .chrome-profile, 디버깅 포트 9333)
//   node loop/browser/rb.mjs status              열려 있는 탭 보기
//   node loop/browser/rb.mjs shot out.png        런박스 탭 화면 저장
//   node loop/browser/rb.mjs job <id> "<명령>"   런박스에서 명령 실행 → 끝날 때까지 기다려 로그 출력
//   node loop/browser/rb.mjs start <id> "<명령>" 실행만 걸어 두고 바로 돌아옴 (긴 작업)
//   node loop/browser/rb.mjs wait <id> [초]      걸어 둔 작업이 끝나길 기다려 로그 출력
//   node loop/browser/rb.mjs log <id> [줄수]     지금까지의 로그
//   node loop/browser/rb.mjs read <런박스 경로>  런박스 파일 읽기
//   node loop/browser/rb.mjs ledger              런박스 실험 장부의 새 줄을 experiments/ledger.jsonl 로 가져오기
//   node loop/browser/rb.mjs key F1 | type "글자" | click x y | eval "js"   (화면 조작용 저수준 명령)
//
// 런박스 주소: 환경변수 RUNBOX_URL, 없으면 loop/browser/.runbox_url, 없으면 submission.json 의 주소.
// 이 주소를 아는 사람은 누구나 런박스 터미널을 쓸 수 있으므로 저장소에 새로 적지 않는다.
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", "..");
const PORT = Number(process.env.RB_PORT || 9333);
const PROFILE = path.join(ROOT, ".chrome-profile");
const CHROME = process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const WORKDIR = process.env.RB_WORKDIR || "/home/elicer/competition";
const OUT = "/tmp/rb";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function runboxOrigin() {
  let u = process.env.RUNBOX_URL;
  const f = path.join(HERE, ".runbox_url");
  if (!u && fs.existsSync(f)) u = fs.readFileSync(f, "utf8").trim();
  if (!u) {
    const sub = JSON.parse(fs.readFileSync(path.join(ROOT, "submission.json"), "utf8"));
    u = sub.tutor_url;
  }
  return new URL(u).origin;
}

// ---------------------------------------------------------------- 읽기 통로
let _prefix;
async function resourcePrefix() {
  if (_prefix) return _prefix;
  const html = await (await fetch(runboxOrigin() + "/?folder=/home/elicer")).text();
  const m = html.match(/stable-[0-9a-f]+/);
  if (!m) throw new Error("런박스 편집기 페이지를 읽지 못했습니다 (런박스가 꺼져 있을 수 있음)");
  return (_prefix = `${runboxOrigin()}/${m[0]}/vscode-remote-resource?path=`);
}
async function readRemote(p) {
  const r = await fetch((await resourcePrefix()) + encodeURI(p), { cache: "no-store" });
  return r.status === 200 ? await r.text() : null;
}

// ---------------------------------------------------------------- CDP
async function targets() {
  const r = await fetch(`http://127.0.0.1:${PORT}/json/list`);
  return (await r.json()).filter((t) => t.type === "page");
}
// 엘리스 학습 화면 탭(제출 버튼이 있는 곳). 런박스 편집기(…tunnel.elice.io)는 제외한다.
const isEliceTab = (t) => {
  try {
    const h = new URL(t.url).hostname;
    return h.endsWith(".elice.io") && !h.endsWith(".tunnel.elice.io");
  } catch {
    return false;
  }
};
async function connect(target) {
  const origin = runboxOrigin();
  let t = target;
  if (!t) {
    let list;
    try {
      list = await targets();
    } catch {
      throw new Error("전용 크롬 창이 없습니다. 먼저: node loop/browser/rb.mjs launch");
    }
    t = list.find((x) => x.url.startsWith(origin));
    if (!t) {
      // 런박스 편집기 탭이 닫혀 있으면 뒤쪽 탭으로 다시 연다 (엘리스 화면이 앞에 그대로 있도록)
      const ver = await (await fetch(`http://127.0.0.1:${PORT}/json/version`)).json();
      const b = await rawSocket(ver.webSocketDebuggerUrl);
      await b.send("Target.createTarget", { url: origin + "/?folder=/home/elicer/competition", background: true });
      b.close();
      for (let i = 0; i < 40 && !t; i++) {
        await sleep(500);
        t = (await targets()).find((x) => x.url.startsWith(origin));
      }
      if (!t) throw new Error("런박스 탭을 열지 못했습니다.");
      await sleep(8000); // 편집기가 다 뜰 때까지
    }
  }
  const c = await rawSocket(t.webSocketDebuggerUrl);
  // 뒤쪽 탭이어도 키 입력이 들어가도록 포커스가 있는 것처럼 동작시킨다
  await c.send("Emulation.setFocusEmulationEnabled", { enabled: true }).catch(() => {});
  return c;
}
async function rawSocket(url) {
  const ws = new WebSocket(url);
  await new Promise((res, rej) => {
    ws.onopen = res;
    ws.onerror = () => rej(new Error("크롬 디버깅 연결 실패"));
  });
  let id = 0;
  const pending = new Map();
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { res, rej } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? rej(new Error(msg.error.message)) : res(msg.result);
    }
  };
  const send = (method, params = {}) =>
    new Promise((res, rej) => {
      pending.set(++id, { res, rej });
      ws.send(JSON.stringify({ id, method, params }));
    });
  return { send, close: () => ws.close() };
}

const KEYS = {
  Enter: { vk: 13, text: "\r" },
  Escape: { vk: 27 },
  Tab: { vk: 9 },
  F1: { vk: 112 },
  ArrowDown: { vk: 40 },
  ArrowUp: { vk: 38 },
  Backspace: { vk: 8 },
};
async function key(c, name, modifiers = 0) {
  const k = KEYS[name];
  if (!k) throw new Error("모르는 키: " + name);
  const base = { key: name, code: name, windowsVirtualKeyCode: k.vk, nativeVirtualKeyCode: k.vk, modifiers };
  await c.send("Input.dispatchKeyEvent", { type: k.text ? "keyDown" : "rawKeyDown", ...base, ...(k.text ? { text: k.text } : {}) });
  await c.send("Input.dispatchKeyEvent", { type: "keyUp", ...base });
}
const type = (c, text) => c.send("Input.insertText", { text });
async function click(c, x, y) {
  for (const t of ["mousePressed", "mouseReleased"])
    await c.send("Input.dispatchMouseEvent", { type: t, x, y, button: "left", clickCount: 1 });
}
async function shot(c, file) {
  const { data } = await c.send("Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(file, Buffer.from(data, "base64"));
}
async function evalJs(c, expression) {
  const r = await c.send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
  return r.result?.value;
}

// 명령 팔레트로 터미널에 포커스를 준 뒤 한 줄을 입력한다
async function termLine(c, line) {
  // Escape 를 먼저 보내지 않는다: 터미널에 포커스가 있으면 셸이 ESC+첫 글자를 단축키로 먹어 첫 글자가 사라진다.
  await key(c, "F1");
  await sleep(500);
  await type(c, "Terminal: Focus Terminal");
  await sleep(700);
  await key(c, "Enter");
  await sleep(1200);
  await type(c, line);
  await sleep(200);
  await key(c, "Enter");
}

// ---------------------------------------------------------------- 작업
function jobLine(id, cmd) {
  if (!/^[A-Za-z0-9_.-]+$/.test(id)) throw new Error("작업 id 는 영문·숫자·_.- 만");
  // Git Bash 는 /tmp/… 같은 인자를 윈도우 경로로 바꿔 버린다. 그렇게 바뀐 명령은 런박스에 보내지 않는다.
  if (/[A-Za-z]:[\\/](Users|Program Files)/.test(cmd))
    throw new Error("명령 안의 경로가 윈도우 경로로 바뀌었습니다. MSYS_NO_PATHCONV=1 을 붙여 다시 실행하세요.");
  const script = `cd ${WORKDIR} 2>/dev/null || cd /home/elicer/competition\n${cmd}\n`;
  const b64 = Buffer.from(script, "utf8").toString("base64");
  return (
    `mkdir -p ${OUT} && rm -f ${OUT}/${id}.rc ${OUT}/${id}.sh && echo ${b64} | base64 -d > ${OUT}/${id}.sh && ` +
    `(nohup bash ${OUT}/${id}.sh > ${OUT}/${id}.log 2>&1; echo $? > ${OUT}/${id}.rc) > /dev/null 2>&1 &`
  );
}
async function waitJob(id, seconds) {
  const t0 = Date.now();
  while (Date.now() - t0 < seconds * 1000) {
    const rc = await readRemote(`${OUT}/${id}.rc`);
    if (rc !== null) return Number(rc.trim());
    await sleep(3000);
  }
  return null;
}
async function printLog(id, lines = 60) {
  const log = (await readRemote(`${OUT}/${id}.log`)) ?? "(로그 없음)";
  const arr = log.replace(/\s+$/, "").split("\n");
  console.log(arr.slice(-lines).join("\n"));
}

// ---------------------------------------------------------------- main
const [cmd, ...args] = process.argv.slice(2);
try {
  if (cmd === "launch") {
    const url = runboxOrigin() + "/?folder=/home/elicer/competition";
    let up = false;
    try {
      up = (await targets()).length >= 0;
    } catch {}
    if (!up) {
      fs.mkdirSync(PROFILE, { recursive: true });
      spawn(
        CHROME,
        [
          `--remote-debugging-port=${PORT}`,
          `--user-data-dir=${PROFILE}`,
          "--no-first-run",
          "--no-default-browser-check",
          "--window-size=1500,950",
          "--disable-backgrounding-occluded-windows",
          "--disable-renderer-backgrounding",
          "--disable-background-timer-throttling",
          ...(args.includes("--headless") ? ["--headless=new"] : []),
          url,
        ],
        { detached: true, stdio: "ignore" }
      ).unref();
      for (let i = 0; i < 40; i++) {
        await sleep(500);
        try {
          await targets();
          up = true;
          break;
        } catch {}
      }
      if (!up) throw new Error("크롬이 뜨지 않았습니다");
      console.log("전용 크롬 창을 띄웠습니다.");
    } else {
      const has = (await targets()).some((t) => t.url.startsWith(runboxOrigin()));
      if (!has) await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(url)}`, { method: "PUT" });
      console.log("전용 크롬 창이 이미 떠 있습니다.");
    }
  } else if (cmd === "status") {
    for (const t of await targets()) console.log(`${t.title}  |  ${t.url.replace(runboxOrigin(), "<런박스>")}`);
  } else if (cmd === "open") {
    // 전용 크롬 창에 새 탭을 열고 창을 앞으로 가져온다 (로그인 등 사람이 직접 해야 하는 일을 위해)
    const r = await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(args[0])}`, { method: "PUT" });
    const t = await r.json();
    const c = await connect(t);
    try {
      const { windowId } = await c.send("Browser.getWindowForTarget");
      await c.send("Browser.setWindowBounds", { windowId, bounds: { windowState: "minimized" } });
      await sleep(300);
      await c.send("Browser.setWindowBounds", { windowId, bounds: { windowState: "normal" } });
      await c.send("Page.bringToFront");
    } finally {
      c.close();
    }
    console.log("새 탭을 열었습니다: " + args[0]);
  } else if (cmd === "elice") {
    // 엘리스 탭마다: 로그인 화면인지, 화면에 보이는 '제출' 버튼이 몇 개인지
    const list = (await targets()).filter(isEliceTab);
    if (!list.length) console.log("엘리스 탭이 없습니다.");
    for (const t of list) {
      const c = await connect(t);
      try {
        const info = await evalJs(
          c,
          `(() => {
            const b = [...document.querySelectorAll("button,[role=button]")].filter((x) => x.innerText.trim() === "제출" && x.offsetParent !== null);
            return { title: document.title, host: location.hostname, path: location.pathname, submitButtons: b.length,
                     enabled: b.filter((x) => !x.disabled).length, loginForm: !!document.querySelector("input[type=password]"),
                     iframes: document.querySelectorAll("iframe").length };
          })()`
        );
        console.log(JSON.stringify(info));
      } finally {
        c.close();
      }
    }
  } else if (cmd === "eshot" || cmd === "eclick" || cmd === "etext") {
    // 엘리스 탭 조작: 화면 저장 / 글자가 정확히 같은 버튼 하나 누르기 / 특정 글자가 든 줄 읽기
    const t = (await targets()).find(isEliceTab);
    if (!t) throw new Error("엘리스 탭이 없습니다. 전용 크롬 창에서 대회 화면을 열어 주세요.");
    const c = await connect(t);
    try {
      if (cmd === "eshot") await shot(c, args[0] || "elice.png");
      else if (cmd === "etext") {
        const text = (await evalJs(c, "document.body.innerText")) || "";
        const key = args[0] || "총점";
        for (const line of text.split(String.fromCharCode(10))) if (line.includes(key)) console.log(line.trim());
      } else {
        const want = JSON.stringify(args[0]);
        const r = await evalJs(
          c,
          `(() => {
            const b = [...document.querySelectorAll("button,[role=button],[role=menuitem],[role=tab],a,li")]
              .filter((x) => x.innerText && x.innerText.trim() === ${want} && x.offsetParent !== null);
            if (b.length !== 1) return { n: b.length };
            const q = b[0].getBoundingClientRect();
            return { n: 1, x: q.x + q.width / 2, y: q.y + q.height / 2, disabled: !!b[0].disabled };
          })()`
        );
        if (r.n !== 1) throw new Error(`'${args[0]}' 버튼이 ${r.n}개입니다 (정확히 1개일 때만 누릅니다).`);
        if (r.disabled) throw new Error(`'${args[0]}' 버튼이 비활성 상태입니다.`);
        await c.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: r.x, y: r.y });
        await click(c, r.x, r.y);
        console.log(`'${args[0]}' 을 눌렀습니다.`);
      }
    } finally {
      c.close();
    }
  } else if (cmd === "submit" || cmd === "result") {
    // submit: 엘리스 화면의 '제출' 버튼을 누르고 "채점 결과" 창이 뜰 때까지 기다려 결과 문장을 출력한다.
    // result: 누르지 않고, '제출' 옆 기록 버튼으로 마지막 결과만 다시 읽는다.
    // 15분 간격은 loop/browser/.last_submit 로 지킨다(LOOP.md §4). 자동 반복은 없다: 한 번 부르면 한 번 제출.
    const stamp = path.join(HERE, ".last_submit");
    const last = fs.existsSync(stamp) ? Number(fs.readFileSync(stamp, "utf8")) : 0;
    const waited = Math.floor((Date.now() - last) / 60000);
    if (cmd === "submit" && waited < 15 && !args.includes("--force"))
      throw new Error(`직전 제출 후 ${waited}분 지났습니다. 15분 간격을 지킵니다.`);
    const t = (await targets()).find(isEliceTab);
    if (!t) throw new Error("엘리스 탭이 없습니다. 전용 크롬 창에서 로그인하고 대회 화면을 열어 주세요.");
    const c = await connect(t);
    const spot = (pick) =>
      evalJs(
        c,
        `(() => {
          const vis = (e) => e.offsetParent !== null;
          const sub = [...document.querySelectorAll("button")].filter((b) => vis(b) && (b.innerText || "").trim() === "제출");
          const dlg = [...document.querySelectorAll("[role=dialog],[role=alertdialog]")].find((d) => (d.innerText || "").includes("채점"));
          const at = (b) => { const q = b.getBoundingClientRect(); return { x: q.x + q.width / 2, y: q.y + q.height / 2, disabled: !!b.disabled }; };
          const pick = ${JSON.stringify(pick)};
          if (pick === "submit") return sub.length === 1 ? at(sub[0]) : { n: sub.length };
          if (pick === "history") {
            const o = sub.length === 1 ? [...sub[0].parentElement.querySelectorAll("button")].filter((b) => b !== sub[0] && !(b.innerText || "").trim()) : [];
            return o.length === 1 ? at(o[0]) : null;
          }
          if (pick === "close") {
            const b = dlg && [...dlg.querySelectorAll("button")].find((x) => (x.innerText || "").trim() === "닫기");
            return b ? at(b) : null;
          }
          const ta = dlg && dlg.querySelector("textarea");
          return dlg ? { text: ta ? ta.value : dlg.innerText } : null;
        })()`
      );
    const press = async (p) => {
      await c.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: p.x, y: p.y });
      await click(c, p.x, p.y);
    };
    const closeDialog = async () => {
      const p = await spot("close");
      if (p) {
        await press(p);
        await sleep(800);
      }
    };
    try {
      await closeDialog();
      if (cmd === "submit") {
        // 탭이 뒤에 있거나 창이 최소화돼 화면을 그리지 않는 상태에서는 클릭이 먹지 않는다
        // (2026-11-17: 크롬을 새로 띄운 뒤 누른 클릭이 무시돼, 8분 동안 채점 요청이 한 건도 오지 않았다).
        // 창을 되살리고 탭을 앞으로 가져온 뒤, 화면이 실제로 그려지는지(화면 저장이 5초 안에 끝나는지) 확인하고 나서만 누른다.
        try {
          const { windowId, bounds } = await c.send("Browser.getWindowForTarget");
          if (bounds && bounds.windowState === "minimized")
            await c.send("Browser.setWindowBounds", { windowId, bounds: { windowState: "normal" } });
        } catch {}
        await c.send("Page.bringToFront").catch(() => {});
        await sleep(800);
        const visible = await evalJs(c, "document.visibilityState");
        const drawn = await Promise.race([
          c.send("Page.captureScreenshot", { format: "jpeg", quality: 10 }).then(() => true, () => false),
          sleep(5000).then(() => false),
        ]);
        if (visible !== "visible" || !drawn)
          throw new Error(`엘리스 탭이 화면에 그려지지 않는 상태입니다 (visibility=${visible}, 화면 저장=${drawn}). 누르지 않았습니다.`);
        const b = await spot("submit");
        if (!b || b.x === undefined) throw new Error(`'제출' 버튼이 ${b ? b.n : 0}개입니다 (정확히 1개일 때만 누릅니다).`);
        if (b.disabled) throw new Error("'제출' 버튼이 비활성입니다 (채점이 진행 중일 수 있습니다).");
        fs.writeFileSync(stamp, String(Date.now()));
        await press(b);
        console.error("제출했습니다. 결과를 기다립니다…");
        // 누른 것이 실제로 먹었는지 확인한다: 채점이 시작되면 '제출' 버튼이 비활성·로딩으로 바뀌거나 글자가 바뀌고, 끝나면 채점 창이 뜬다.
        // 30초 동안 아무 표시가 없으면 기다리지 않고 알린다(다시 누르기 전에 서버 로그로 채점 요청이 왔는지 반드시 확인한다).
        let started = false;
        for (let i = 0; i < 15 && !started; i++) {
          await sleep(2000);
          started = await evalJs(
            c,
            `(() => {
              const vis = (e) => e.offsetParent !== null;
              const sub = [...document.querySelectorAll("button")].filter((x) => vis(x) && (x.innerText || "").trim() === "제출");
              const dlg = [...document.querySelectorAll("[role=dialog],[role=alertdialog]")].some((d) => (d.innerText || "").includes("채점"));
              return dlg || sub.length !== 1 || sub.some((x) => x.disabled || x.className.includes("loading") || !!x.querySelector("[role=progressbar]"));
            })()`
          );
        }
        if (!started)
          throw new Error("NOT_STARTED: 제출을 눌렀지만 30초 동안 채점이 시작된 표시가 없습니다(버튼이 그대로입니다). 서버 로그로 채점 요청이 왔는지 확인하세요.");
      } else {
        const h = await spot("history");
        if (!h) throw new Error("제출 기록 버튼을 찾지 못했습니다 (이 화면에서 제출한 적이 없으면 보이지 않습니다).");
        await press(h);
      }
      const NL = String.fromCharCode(10);
      const t0 = Date.now();
      let text = null;
      while (Date.now() - t0 < (cmd === "submit" ? 8 * 60000 : 15000)) {
        await sleep(cmd === "submit" ? 5000 : 1500);
        const d = await spot("dialog");
        if (d && d.text && (d.text.includes("Message") || d.text.includes("총점") || d.text.includes("오류"))) {
          text = d.text;
          break;
        }
      }
      if (!text) throw new Error("결과 창을 찾지 못했습니다. 화면을 직접 확인하세요.");
      const msg = text.includes("=== Message ===") ? text.split("=== Message ===")[1] : text.split(NL).slice(-4).join(" ");
      console.log(msg.trim().split(NL).join(" "));
      await closeDialog();
    } finally {
      c.close();
    }
  } else if (cmd === "read") {
    const s = await readRemote(args[0]);
    if (s === null) {
      console.error("없음: " + args[0] + "  (Git Bash 에서는 MSYS_NO_PATHCONV=1 을 붙여야 경로가 바뀌지 않습니다)");
      process.exitCode = 1;
    } else process.stdout.write(s);
  } else if (cmd === "ledger") {
    // 런박스 실험 장부(/tmp/rb/ledger.jsonl)에서 새 줄만 가져와 이 PC 의 experiments/ledger.jsonl 에 붙인다
    const remote = (await readRemote(`${OUT}/ledger.jsonl`)) ?? "";
    const local = path.join(ROOT, "experiments", "ledger.jsonl");
    const NL = String.fromCharCode(10);
    const have = new Set(fs.existsSync(local) ? fs.readFileSync(local, "utf8").split(NL).filter(Boolean) : []);
    const fresh = remote.split(NL).filter((l) => l.trim() && !have.has(l));
    if (fresh.length) {
      fs.mkdirSync(path.dirname(local), { recursive: true });
      fs.appendFileSync(local, fresh.join(NL) + NL);
    }
    for (const l of fresh) {
      const e = JSON.parse(l);
      console.log(`+ ${e.name} @${e.commit} 총점 ${e.total} (튜터 ${e.tutor}, 가드 ${e.guard}) holdout ${e.holdout_total} max ${e.max_ms}ms`);
    }
    console.log(`장부 ${fresh.length}줄 추가`);
  } else if (cmd === "log") {
    await printLog(args[0], Number(args[1] || 60));
  } else if (cmd === "wait") {
    const rc = await waitJob(args[0], Number(args[1] || 600));
    await printLog(args[0]);
    console.log(rc === null ? "\n[아직 실행 중]" : `\n[종료 코드 ${rc}]`);
    process.exitCode = rc === 0 ? 0 : rc === null ? 3 : 1;
  } else {
    const c = await connect();
    try {
      if (cmd === "shot") await shot(c, args[0] || "runbox.png");
      else if (cmd === "key") await key(c, args[0], Number(args[1] || 0));
      else if (cmd === "type") await type(c, args[0]);
      else if (cmd === "click") await click(c, Number(args[0]), Number(args[1]));
      else if (cmd === "eval") console.log(JSON.stringify(await evalJs(c, args[0])));
      else if (cmd === "term") await termLine(c, args[0]);
      else if (cmd === "job" || cmd === "start") {
        const [id, command] = args;
        await termLine(c, jobLine(id, command));
        // 새로 만든 터미널은 셸이 뜨기 전에 Enter 가 사라질 수 있다 → 스크립트 파일이 안 생겼으면 Enter 를 한 번 더
        let ok = false;
        for (let attempt = 0; attempt < 2 && !ok; attempt++) {
          for (let i = 0; i < 4 && !ok; i++) {
            await sleep(1500);
            ok = (await readRemote(`${OUT}/${id}.sh`)) !== null;
          }
          if (!ok && attempt === 0) await key(c, "Enter");
        }
        if (!ok) throw new Error("명령이 터미널에 들어가지 않았습니다. rb.mjs shot 으로 화면을 확인하세요.");
        if (cmd === "job") {
          const rc = await waitJob(id, Number(args[2] || 300));
          await printLog(id);
          console.log(rc === null ? "\n[아직 실행 중 — rb.mjs wait 로 이어서 확인]" : `\n[종료 코드 ${rc}]`);
          process.exitCode = rc === 0 ? 0 : rc === null ? 3 : 1;
        } else console.log(`작업 ${id} 을 걸어 두었습니다.`);
      } else {
        console.error("명령: launch | status | open | elice | submit | result | eshot | eclick | etext | shot | job | start | wait | log | ledger | read | term | key | type | click | eval");
        process.exitCode = 2;
      }
    } finally {
      c.close();
    }
  }
} catch (e) {
  console.error("오류: " + e.message);
  process.exitCode = 1;
}
