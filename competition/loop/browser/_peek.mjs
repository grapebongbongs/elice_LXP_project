// 진단용(읽기 전용): 엘리스 탭에서 지금 떠 있는 창·알림과 제출 관련 글자를 읽는다. 아무것도 누르지 않는다.
// 창이 최소화돼 화면 저장이 안 될 때 화면 대신 쓴다.
//   node loop/browser/_peek.mjs            요약
//   node loop/browser/_peek.mjs 제출 기록   해당 글자가 든 줄도 함께
const PORT = Number(process.env.RB_PORT || 9333);
const keys = process.argv.slice(2);
const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
const t = list.find((x) => x.type === "page" && x.url.includes(".elice.io") && !x.url.includes(".tunnel.elice.io"));
if (!t) {
  console.log("엘리스 탭 없음");
  process.exit(0);
}
const ws = new WebSocket(t.webSocketDebuggerUrl);
await new Promise((r) => (ws.onopen = r));
let id = 0;
const pend = new Map();
ws.onmessage = (e) => {
  const m = JSON.parse(e.data);
  if (m.id && pend.has(m.id)) {
    pend.get(m.id)(m);
    pend.delete(m.id);
  }
};
const send = (method, params = {}) =>
  new Promise((res) => {
    pend.set(++id, res);
    ws.send(JSON.stringify({ id, method, params }));
  });
const expression = `(() => {
  const NL = String.fromCharCode(10);
  const vis = (e) => e.offsetParent !== null || getComputedStyle(e).position === "fixed";
  const one = (s) => s.trim().split(NL).map((x) => x.trim()).filter(Boolean).join(" | ").slice(0, 500);
  const pick = (sel) => [...document.querySelectorAll(sel)].filter(vis).map((d) => one(d.innerText || "")).filter(Boolean);
  const keys = ${JSON.stringify(keys)};
  const lines = document.body.innerText.split(NL).map((x) => x.trim()).filter((l) => l && (l.includes("총점") || keys.some((k) => l.includes(k))));
  const sub = [...document.querySelectorAll("button")].filter((b) => vis(b) && (b.innerText || "").trim() === "제출");
  const group = sub.length ? [...sub[0].parentElement.querySelectorAll("button")].map((b) => (b.innerText || "").trim() || b.getAttribute("aria-label") || "[icon]") : [];
  return {
    visibility: document.visibilityState,
    dialogs: pick("[role=dialog],[role=alertdialog]"),
    menus: pick("[role=menu]"),
    alerts: pick("[role=alert],[role=status],.MuiSnackbar-root,.Toastify__toast"),
    submitButtons: sub.map((b) => ({ disabled: b.disabled, loading: b.className.includes("loading") || !!b.querySelector("[role=progressbar]") })),
    submitGroup: group,
    lines: [...new Set(lines)].slice(0, 12),
  };
})()`;
const ev = await send("Runtime.evaluate", { returnByValue: true, expression });
console.log(JSON.stringify(ev.result?.result?.value ?? ev.result, null, 1));
ws.close();
