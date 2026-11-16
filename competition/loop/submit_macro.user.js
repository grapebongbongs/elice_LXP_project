// ==UserScript==
// @name         엘리스 대회 제출 매크로 (제출 → 결과 대기 → 결과 문장 복사)
// @match        https://*.elice.io/*
// @grant        none
// ==/UserScript==
//
// 쓰는 법 (둘 중 하나)
//   A. 런박스가 열린 브라우저 탭에서 F12 → Console 에 이 파일 전체를 붙여 넣는다.
//   B. Tampermonkey 에 등록한다.
// 그다음 Console 에서:   eliceSubmit()          // 1회 제출하고 결과를 기다린다
//                        eliceSubmit({dry:true}) // 버튼만 찾아 보고 누르지 않는다 (처음엔 이것부터)
//
// 결과가 나오면 결과 문장이 클립보드에 복사되고 화면 오른쪽 아래에 표시된다.
// 그 문장을 런박스 터미널에서 기록한다:
//   python3 loop/record_submission.py "<붙여넣기>" --note "무엇을 바꿨는지"
//
// 주의 — 이 스크립트는 화면 구조를 스크린샷으로만 보고 썼다. 실제 화면에서 dry 모드로 먼저 확인할 것.
// 일부러 넣은 제한 (LOOP.md §4):
//   - 자동 반복 없음. 한 번 부르면 한 번만 제출한다. (최종 점수는 '마지막 제출'이라 무작정 돌리면 점수를 잃는다)
//   - 직전 제출 후 15분이 안 지났으면 누르지 않는다. (과도한 호출은 실격 사유)
//   - presubmit_check 를 통과했는지 묻는다.
(function () {
  const RESULT = /총점\s*\d+(?:\.\d+)?\s*\(\s*튜터\s*\d+(?:\.\d+)?\s*,\s*가드\s*\d+(?:\.\d+)?\s*\)[^\n]*/g;
  const ERROR = /(제출 오류|채점 오류|판정 오류가 많아|코드 또는 제출 파일에 문제)[^\n]*/;
  const MIN_GAP_MS = 15 * 60 * 1000;
  const WAIT_MS = 12 * 60 * 1000;
  const KEY = "eliceSubmitMacro.last";

  const texts = () => {
    const out = [document.body.innerText];
    for (const f of document.querySelectorAll("iframe")) {
      try { out.push(f.contentDocument.body.innerText); } catch (e) { /* 다른 출처의 iframe 은 읽을 수 없다 */ }
    }
    return out.join("\n");
  };
  const results = () => texts().match(RESULT) || [];
  const findSubmit = () =>
    [...document.querySelectorAll("button, [role=button]")].filter(
      (b) => b.innerText.trim() === "제출" && b.offsetParent !== null && !b.disabled
    );

  function toast(msg, color) {
    let el = document.getElementById("elice-submit-macro");
    if (!el) {
      el = document.createElement("div");
      el.id = "elice-submit-macro";
      el.style.cssText =
        "position:fixed;right:16px;bottom:16px;z-index:999999;max-width:520px;padding:12px 14px;border-radius:8px;" +
        "font:13px/1.5 sans-serif;color:#fff;box-shadow:0 4px 16px rgba(0,0,0,.4);white-space:pre-wrap";
      document.body.appendChild(el);
    }
    el.style.background = color || "#333";
    el.textContent = msg;
  }

  window.eliceSubmit = async function (opt = {}) {
    const btns = findSubmit();
    if (btns.length !== 1) {
      toast(`'제출' 버튼을 ${btns.length}개 찾았습니다. 1개여야 합니다. 화면 구조가 달라졌는지 확인하세요.`, "#b00020");
      return null;
    }
    if (opt.dry) {
      btns[0].style.outline = "3px solid #00c853";
      toast(`제출 버튼을 찾았습니다(초록 테두리). 지금 화면에서 읽히는 결과 문장 ${results().length}개.`, "#1b5e20");
      return btns[0];
    }
    const last = Number(localStorage.getItem(KEY) || 0);
    if (Date.now() - last < MIN_GAP_MS && !opt.force) {
      toast(`직전 제출 후 ${Math.round((Date.now() - last) / 60000)}분 지났습니다. 15분 간격을 지킵니다.`, "#b00020");
      return null;
    }
    if (!confirm("presubmit_check.py 가 '모두 통과'였고, 서버가 켜져 있나요?\n이 제출이 이 계정의 '마지막 제출'(최종 점수)이 됩니다.")) return null;

    const before = new Set(results());
    localStorage.setItem(KEY, String(Date.now()));
    btns[0].click();
    toast("제출했습니다. 결과를 기다립니다… (탭을 닫거나 다른 자료로 옮기지 마세요)", "#0d47a1");

    // 확인 창이 한 번 더 뜨는 경우: '제출'/'확인' 버튼이 새로 생기면 누른다
    await new Promise((r) => setTimeout(r, 1500));
    const dialog = document.querySelector("[role=dialog]");
    if (dialog) {
      const ok = [...dialog.querySelectorAll("button")].find((b) => /^(제출|확인)$/.test(b.innerText.trim()));
      if (ok) ok.click();
    }

    const t0 = Date.now();
    while (Date.now() - t0 < WAIT_MS) {
      await new Promise((r) => setTimeout(r, 5000));
      const fresh = results().filter((r) => !before.has(r));
      if (fresh.length) {
        const line = fresh[fresh.length - 1].trim();
        try { await navigator.clipboard.writeText(line); } catch (e) { /* 포커스가 없으면 복사 실패 — 화면 문구를 직접 복사 */ }
        toast(`결과 (${Math.round((Date.now() - t0) / 1000)}초):\n${line}\n\n클립보드에 복사했습니다. 터미널에서:\npython3 loop/record_submission.py "<붙여넣기>"`, "#1b5e20");
        console.log("[eliceSubmit]", line);
        return line;
      }
      const err = texts().match(ERROR);
      if (err && !before.has(err[0])) {
        toast(`오류 메시지: ${err[0]}\nLOOP.md §5 의 표를 보고 처리하세요. (이 결과는 기록하지 않습니다)`, "#b00020");
        return null;
      }
      toast(`결과 대기 중… ${Math.round((Date.now() - t0) / 1000)}초`, "#0d47a1");
    }
    toast("12분 동안 결과 문장을 찾지 못했습니다. 제출 기록 화면을 직접 확인하세요.", "#b00020");
    return null;
  };
  console.log("eliceSubmit() 준비됨. 먼저 eliceSubmit({dry:true}) 로 버튼을 확인하세요.");
})();
