// 쿼리 화면의 [대시보드에 추가]: 어느 대시보드에 넣을지(저장한 것 또는 새 대시보드)를 묻고, 카드의 값을 서버가 지금 연결에서 구해 그 대시보드에 더한다.
// 끝나면 "대시보드에서 보기" 링크(/dashboard?open=id)를 알린다. 대시보드를 보고 고치는 곳은 대시보드 화면 하나다(common.js가 먼저 불려 있어야 한다).

// 대시보드 고르는 작은 창. { id, name }(id가 비면 새 대시보드, name이 그 이름) 또는 취소면 null.
function chooseDashboard() {
  return new Promise(async (resolve, reject) => {
    let found;
    try { found = (await api("/api/dash")).dashboards; } catch (e) { return reject(e); }
    const pick = el("select", {}, el("option", { value: "", textContent: "새 대시보드" }), ...found.map(d => el("option", { value: d.id, textContent: `${d.name} (카드 ${d.cards}개)` })));
    const name = el("input", { placeholder: "새 대시보드 이름" }), err = el("div", { className: "err" });
    const close = value => { modal.remove(); resolve(value); };
    pick.onchange = () => name.classList.toggle("hidden", pick.value !== "");
    const ok = el("button", { className: "btn p", textContent: "넣기", onclick: () => {
      if (!pick.value && !name.value.trim()) return void (err.textContent = "새 대시보드의 이름을 쓰세요.");
      close({ id: pick.value, name: name.value.trim() });
    } });
    const modal = el("div", { className: "modal" }, el("div", { className: "modal-box" }, el("div", { className: "head" }, el("h3", { textContent: "어느 대시보드에 넣을까요?" })), pick, name, err,
      el("div", { className: "row" }, ok, el("button", { className: "btn", textContent: "취소", onclick: () => close(null) })),
      el("div", { className: "hint", textContent: "카드에는 구한 값이 들어가 이 PC·이 계정에서만 풀 수 있게 암호화해 저장합니다." })));
    document.body.append(modal);
    (found.length ? pick : name).focus();
  });
}
// card(값은 아직 없음)를 고른 대시보드에 넣는다. 서버가 값을 구해(연결 필요) 저장한다. 취소하면 null, 되면 {id, name, cards}.
// picked({id, name})를 주면 대시보드를 다시 묻지 않는다(여러 카드를 한꺼번에 넣을 때).
async function addToDashboard(card, kind, table, picked) {
  const pick = picked ?? await chooseDashboard();
  if (!pick) return null;
  let d = await timed(kind, () => api("/api/dash/card/compute", { card }), table);
  card.source = { ...card.source, ...d.source };
  if (!card.source.date && !d.data.by_unit && d.data.date_cols?.length === 1 && card.kind !== "recent_rows") {  // 날짜 컬럼이 하나뿐이면 기간 기준으로 자동으로 채운다
    card.source.date = d.data.date_cols[0];
    d = await api("/api/dash/card/compute", { card: { ...card, data: {} } });
  }
  card.data = d.data;
  return (await api("/api/dash/card/add", { dashboard: pick.id, name: pick.name, card })).dashboard;
}
// 알림 줄(id)에 결과 글과 "대시보드에서 보기" 링크를 보인다.
function showAdded(id, added) {
  say(id, `대시보드 '${added.name}'에 넣었습니다(카드 ${added.cards}개). `, true);
  $(id).append(el("a", { href: "/dashboard?open=" + encodeURIComponent(added.id), textContent: "대시보드에서 보기" }));
}
