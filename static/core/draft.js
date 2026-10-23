// 화면 작업 임시 저장(초안): 상태가 바뀌고 DRAFT_QUIET ms 조용하면 서버(draft_app.py)에 보내고, 화면을 열 때 남은 초안이 있으면 팝업으로 묻는다. 같은 파일 아래에 화면 편집 되돌리기(Undo)와 단축키(Shortcuts)도 있다. common.js의 el·api·flash가 먼저 있어야 한다.
const DRAFT_QUIET = 2000;
// 보낼 때인가(순수 논리): m = { sent: 마지막에 보낸 글, seen: 지난번에 본 글, at: 그 글이 처음 보인 때 }, snap = 지금 상태의 JSON 글(저장할 것이 없으면 null).
// 바뀐 뒤 quiet ms 동안 그대로이고 이미 보낸 것과 다를 때만 true(그때 m.sent를 snap으로 한다).
function draftTick(m, now, snap, quiet = DRAFT_QUIET) {
  if (snap !== m.seen) { m.seen = snap; m.at = now; }
  if (snap === null || snap === m.sent || now - m.at < quiet) return false;
  m.sent = snap;
  return true;
}
const draftTime = iso => { const d = new Date(iso); return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };
// screen: "search"·"prep"·"dashboard". get(): 저장할 상태 객체(없으면 null). restore(state): 초안으로 화면을 채운다(안 되면 던진다).
// ask()를 부를 때까지는 저장하지 않는다(남은 초안을 덮어쓰지 않으려고). clear(): 단계가 끝났을 때 초안을 지운다.
function Draft(screen, get, restore) {
  const url = "/api/draft?screen=" + screen, m = { sent: "", seen: "", at: 0 };
  let on = false, has = false, chain = Promise.resolve();  // has: 지울 초안이 있을 수 있나, chain: 보내는 중인 요청(지우기가 그 뒤에 가도록)
  const snapNow = () => { const v = get(); return v == null ? null : JSON.stringify(v); };
  setInterval(() => {
    if (!on) return;
    const v = get(), snap = v == null ? null : JSON.stringify(v);
    if (!draftTick(m, Date.now(), snap)) return;
    has = true;
    chain = chain.then(() => api(url, { state: v })).catch(() => { m.sent = ""; });
  }, 1000);
  async function clear() {
    m.sent = m.seen = snapNow();  // 지금 상태는 이미 끝낸 것이라 바뀌기 전에는 다시 만들지 않는다
    if (!has) return;
    has = false;
    await chain;
    try { await fetch(url, { method: "DELETE" }); } catch { /* 못 지워도 다음에 팝업이 한 번 더 뜰 뿐이다 */ }
  }
  async function ask() {
    on = false;
    try {
      const d = await api(url);
      if (d.draft) await popup(d);
    } catch { /* 초안을 못 읽어도 화면은 그대로 쓴다 */ }
    on = true;
  }
  function popup(d) {
    return new Promise(done => {
      const note = el("p", { style: "white-space:pre-wrap; margin:10px 0 14px" }), conn = d.draft.connection?.name;
      note.textContent = `이전에 편집하던 내용이 남아 있어요 (${draftTime(d.draft.saved)}${conn ? `, 연결: ${conn}` : ""})`
        + (d.same ? "" : `\n\n지금과 다른 연결의 내용이에요. 그 연결로 바꾼 뒤 이어서 할 수 있어요.`);
      const end = () => { box.remove(); done(); };
      const shut = el("button", { className: "btn", textContent: "닫기", onclick: end });
      const row = el("div", { className: "row", style: "justify-content:flex-end" });
      const go = el("button", { className: "btn p", textContent: "이어서 하기", onclick: async () => {
        try { await restore(d.draft.state); has = true; m.sent = m.seen = snapNow(); end(); }
        catch (e) { note.textContent = e.message; go.replaceWith(shut); shut.focus(); }  // 컬럼이 없어졌다 같은 이유를 그대로 보인다
      } });
      const drop = el("button", { className: "ghost", textContent: "버리기", onclick: async () => { has = true; await clear(); end(); } });
      row.append(drop, d.same ? go : shut);
      const box = el("div", { className: "modal" }, el("div", { className: "modal-box" }, el("h3", { textContent: "작업하던 내용이 있어요" }), note, row));
      document.body.append(box);
      (d.same ? go : shut).focus();
    });
  }
  return { ask, clear };
}

// ---------- 화면 편집 되돌리기·단축키 ----------
// Undo(get, restore): get()은 지금 화면의 전체 상태(JSON으로 바꿀 수 있는 값, 아직 없으면 null), restore(state)는 그 상태로 화면을 채운다(async 가능, 던지면 되돌리기를 취소한다).
// 상태가 바뀌고 quiet ms 동안 그대로이면 바뀌기 전 상태를 쌓는다(글자를 치는 동안은 한 번으로 묶인다). 복원 중에 생긴 변화는 쌓지 않는다.
// 아직 쌓이기 전의 편집은 undo()가 그 편집만 취소한다. poll ms마다 비교하며, 0이면 tick()을 직접 부른다(시험용).
function Undo(get, restore, { max = 50, quiet = 600, poll = 500 } = {}) {
  let base = null, seen = null, at = 0, back = [], fwd = [], busy = false;  // base: 쌓은 마지막 상태, seen·at: 지금 보이는 상태와 그것이 처음 보인 때
  const snap = () => { const v = get(); return v == null ? null : JSON.stringify(v); };
  const push = (list, s) => { list.push(s); if (list.length > max) list.shift(); };
  function tick(now = Date.now()) {
    const s = busy ? null : snap();
    if (s === null) return;
    if (base === null) { base = seen = s; return; }
    if (s !== seen) { seen = s; at = now; }
    if (s === base || now - at < quiet) return;
    push(back, base);
    base = s;
    fwd = [];
  }
  async function move(from, to) {
    const s = busy ? null : snap();
    if (s === null) return false;
    if (from === fwd && s !== base) return false;  // 쌓기 전의 새 편집이 있으면 다시 하기는 없다
    const popped = from !== back || s === base;
    const target = popped ? from.pop() : base;  // 쌓기 전의 편집은 그 편집만 취소
    if (target === undefined) return false;
    push(to, s);
    busy = true;
    try { await restore(JSON.parse(target)); }
    catch { to.pop(); if (popped) from.push(target); return false; }
    finally { busy = false; }
    base = seen = snap() ?? target;
    return true;
  }
  if (poll) setInterval(tick, poll);
  return { tick, undo: () => move(back, fwd), redo: () => move(fwd, back), reset() { base = seen = snap(); back = []; fwd = []; }, size: () => [back.length, fwd.length] };
}
// 글자를 쓰는 칸(커서가 있으면 창 엔진의 글자 되돌리기에 맡긴다). 체크·라디오·단추 같은 칸은 화면 되돌리기를 쓴다.
const isTyping = t => !!t && (t.isContentEditable || t.tagName === "TEXTAREA" || (t.tagName === "INPUT" && /^(|text|search|number|email|url|tel|password|date|datetime-local|month|week|time)$/.test(t.type)));
// 단추를 눌러 준다(없거나 꺼졌거나 숨겨졌으면 false).
const pressBtn = b => !b || b.disabled || b.closest(".hidden") ? false : (b.click(), true);
// Shortcuts({ undo, save, run, find, active }): Ctrl+Z 되돌리기, Ctrl+Y·Ctrl+Shift+Z 다시 하기, Ctrl+S 저장, Ctrl+Enter 실행, Ctrl+F 찾기.
// save·run은 눌렀으면(할 수 있으면) true를 돌려주는 함수, find는 찾기 칸을 돌려주는 함수. 없는 것은 건드리지 않는다. 안 맞으면 창 엔진의 기본 동작이 그대로 간다.
// 한글 조합 중, 이미 다른 곳에서 처리한 키(SQL 칸의 Ctrl+Enter), 모달·펼친 목록이 있을 때, active()가 false(다른 탭)일 때는 무시한다.
function Shortcuts({ undo, save, run, find, active = () => true }) {
  document.addEventListener("keydown", e => {
    if (e.defaultPrevented || e.isComposing || e.altKey || !(e.ctrlKey || e.metaKey) || !active() || document.querySelector(".modal:not(.hidden), .menu:not(.hidden)")) return;
    const k = e.key.toLowerCase();
    if ((k === "z" || k === "y") && undo && !isTyping(e.target)) {
      const redo = k === "y" || e.shiftKey;
      e.preventDefault();
      (redo ? undo.redo() : undo.undo()).then(ok => ok && flash(redo ? "다시 했어요" : "되돌렸어요"));
    } else if (k === "s" && !e.shiftKey && save) { e.preventDefault(); save(); }
    else if (k === "enter" && run && run()) e.preventDefault();
    else if (k === "f" && !e.shiftKey && find) {
      const box = find();
      if (box && !box.disabled && !box.closest(".hidden")) { e.preventDefault(); box.focus(); box.select?.(); }
    }
  });
}
