// 화면 작업 임시 저장(초안): 상태가 바뀌고 DRAFT_QUIET ms 조용하면 서버(draft_app.py)에 보내고, 화면을 열 때 남은 초안이 있으면 팝업으로 묻는다. common.js의 el·api가 먼저 있어야 한다.
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
