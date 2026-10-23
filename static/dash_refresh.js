// 대시보드 최신화 진행 보기: 연결에 성공한 화면(common.js의 연결 경로)이 startRefresh()를 부르면 서버의 작업 스레드가 주기가 지난 카드를 차례로 다시 구하고,
// 이 파일이 상태를 읽어 팝업(dash_refresh.html)과 위쪽 줄의 알약(#rf-pill)에 보인다. 팝업은 닫아도 되고(알약을 누르면 다시 열림) 사용자의 작업을 막지 않는다.
// 끝나면 document에 "dash-refreshed" 이벤트를 보낸다(새 값을 보이는 화면이 다시 그린다). 화면을 새로 열었을 때 이미 돌고 있으면 알약으로 이어서 보인다.
// connect.html과 status.html이 있는 화면에서 common.js 다음에 불러 온다.
let refreshTimer = null;
const REFRESH_STATE = { ok: "끝", failed: "실패", skipped: "건너뜀", waiting: "기다림" };
async function startRefresh() {
  try {
    const st = await api("/api/dash/refresh/start", {});
    renderRefresh(st);
    if (st.running) { $("refresh-pop").classList.remove("hidden"); pollRefresh(); }
    else if (st.total) finishRefresh(st);
  } catch (e) { /* 저장한 대시보드가 없거나 풀 수 없어도 연결은 그대로 쓴다 */ }
}
async function pollRefresh() {
  clearTimeout(refreshTimer);
  const st = await api("/api/dash/refresh/status");
  renderRefresh(st);
  if (st.running) refreshTimer = setTimeout(pollRefresh, 800);
  else finishRefresh(st);
}
function renderRefresh(st) {
  const count = state => st.results.filter(r => r.state === state).length;
  const text = st.running ? `대시보드 최신화 진행 중… (${st.done}/${st.total})` : `대시보드 최신화 끝 (${count("ok")}개 끝 · ${count("failed")}개 실패 · ${count("skipped")}개 건너뜀)`;
  $("rf-title").textContent = text;
  $("rf-pill").classList.toggle("hidden", !st.running);
  $("rf-pill-text").textContent = `최신화 ${st.done}/${st.total}`;
  $("rf-list").replaceChildren(...st.results.map(r => el("li", { className: r.state + (st.running && r.title === st.current && r.state === "waiting" ? " now" : "") },
    el("b", { textContent: REFRESH_STATE[r.state] ?? r.state }), el("span", { textContent: r.title + (r.message ? ` — ${r.message}` : "") }))));
  $("rf-bg").textContent = st.running ? "뒤에서 계속" : "닫기";
  $("rf-stop").disabled = !st.running;
}
function finishRefresh(st) {
  $("rf-pill").classList.add("hidden");
  document.dispatchEvent(new CustomEvent("dash-refreshed", { detail: st }));
}
$("rf-bg").onclick = () => $("refresh-pop").classList.add("hidden");
$("rf-pill").onclick = () => $("refresh-pop").classList.remove("hidden");
$("rf-stop").onclick = async () => renderRefresh(await api("/api/dash/refresh/stop", {}));
api("/api/dash/refresh/status").then(st => { if (st.running) { renderRefresh(st); pollRefresh(); } }).catch(() => {});  // 다른 화면에서 시작해 아직 돌고 있으면 이어서
