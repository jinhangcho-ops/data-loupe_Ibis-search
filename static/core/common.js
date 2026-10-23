const $ = id => document.getElementById(id);
// 필요할 때만 스크립트를 불러온다: need(주소, …)는 차례대로 실행되는 Promise. 이미 불러왔거나 불러오는 중인 주소는 다시 불러오지 않는다.
// 서버는 nonce가 붙은 스크립트만 실행하게 하므로(CSP) 이 파일의 nonce를 붙인다.
const SCRIPT_NONCE = document.currentScript?.nonce ?? "";
const needed = new Map();
const need = (...urls) => Promise.all(urls.map(url => {
  if (!needed.has(url)) needed.set(url, new Promise((done, fail) => {
    const tag = Object.assign(document.createElement("script"), { src: url, nonce: SCRIPT_NONCE, async: false, onload: done });
    tag.onerror = () => { needed.delete(url); fail(new Error(`${url}을 불러오지 못했어요.`)); };
    document.head.append(tag);
  }));
  return needed.get(url);
}));
const CHART_JS = "/static/core/chart.umd.min.js";  // 차트 라이브러리는 첫 차트를 그릴 때 불러온다: typeof Chart === "undefined"이면 need(CHART_JS) 뒤에 다시 그린다.
document.querySelectorAll(".pages a").forEach(a => a.classList.toggle("on", a.pathname === location.pathname));  // 지금 화면의 전환 버튼을 누른 모양으로
const S = { schema: "", base: "", cols: [] };
async function send(url, body) {
  const res = await fetch(url, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) { const d = await res.json(), err = new Error(d.error); err.driver = d.driver; throw err; }  // driver: 드라이버가 없어 못 붙었을 때의 안내
  if (res.headers.get("X-Needs-Confirm")) {  // 서버가 처리 용량이 기준을 넘는다고 물으면 팝업으로 묻고, 확인하면 그 실행만 confirmed를 붙여 다시 보낸다.
    const d = await res.json(), ok = await confirmScan(d);
    if (d.blocked || !ok) throw new Error(d.blocked ? d.text : "취소했습니다.");
    return send(url, { ...body, confirmed: true });
  }
  return res;
}
// 처리 용량 확인 팝업: 내용과 [확인] [취소](알 수 없는데 차단으로 정했으면 이유와 [닫기]만). 확인하면 true.
function confirmScan(d) {
  return new Promise(done => {
    const pick = ok => () => { box.remove(); done(ok); };
    const buttons = d.blocked ? [el("button", { className: "btn", textContent: "닫기", onclick: pick(false) })]
      : [el("button", { className: "ghost", textContent: "취소", onclick: pick(false) }), el("button", { className: "btn p", textContent: "확인", onclick: pick(true) })];
    const box = el("div", { className: "modal", onclick: e => { if (e.target === box) pick(false)(); } },
      el("div", { className: "modal-box" }, el("h3", { textContent: d.blocked ? "실행하지 않습니다" : "처리 용량이 큽니다" }),
        el("p", { textContent: d.text, style: "white-space:pre-wrap; margin:10px 0 14px" }), el("div", { className: "row", style: "justify-content:flex-end" }, ...buttons)));
    document.body.append(box);
    box.querySelector("button:last-child").focus();
  });
}
const api = async (url, body) => (await send(url, body)).json();
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// value를 적어 두지 않으면 앞뒤·연속 공백이 줄어든 글자가 값이 된다("금액 " → "금액").
const fill = (sel, items, blank) => sel.innerHTML = (blank ? `<option value="">${blank}</option>` : "") +
  items.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
// 요소 만들기: 속성을 주고 자식(요소나 글자)을 붙인다.
const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids); return e; };
// SVG 요소 만들기(el의 SVG판): 속성(null은 건너뜀)과 자식을 준다. 카드 그림·흐름·생키가 함께 쓴다.
const svg = (tag, attrs = {}, ...kids) => {
  const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) e.setAttribute(k, v);
  e.append(...kids);
  return e;
};
// 칩: 이름 옆의 ×를 누르면 remove를 부른다.
const chip = (text, remove) => el("span", { className: "chip" }, text, el("button", { className: "ghost", textContent: "×", title: "빼기", onclick: remove }));
const download = (href, name) => {
  el("a", { href, download: name }).click();
  if (href.startsWith("blob:")) setTimeout(() => URL.revokeObjectURL(href), 60000);  // 내려받기가 시작된 뒤 메모리를 돌려준다
};
// 단추를 누르면 떠 있는 상자(.drop 안의 .pop)를 열고 닫는다. 밖을 누르면 닫는 일은 화면이 한다.
const toggleDrop = (button, pop) => { button.onclick = e => { e.stopPropagation(); pop.classList.toggle("hidden"); }; };
// 중첩 값(구조·목록·맵): 한 줄로 {name: 가람, age: 30}, [a, b]처럼 줄여 보인다. 빈 값은 null.
const isNested = v => v !== null && typeof v === "object";
const inline = v => Array.isArray(v) ? "[" + v.map(inline).join(", ") + "]"
  : isNested(v) ? "{" + Object.entries(v).map(([k, x]) => `${k}: ${inline(x)}`).join(", ") + "}" : v === null ? "null" : String(v);
// 값이 표에 보이는 글자와 전체 글자(잘렸을 때만, 마우스를 올리면 보인다). 숫자는 자릿수 쉼표를 넣고, 문자열은 최대 글자 수(#maxlen, 없는 화면은 40)에서 자른다.
// 중첩 값의 전체 글자는 들여쓴 JSON이다.
// 큰 정수는 서버가 글자("12345678901234567890")로 보내므로 숫자처럼 오른쪽에 맞춘다. 자릿수 쉼표로 소수나 큰 지수가 잘려 보이면 전체 값을 title로.
const bigInt = v => typeof v === "string" && /^-?\d{16,}$/.test(v);
const sameSource = (saved, now) => saved === now || (typeof saved === "string" && typeof now === "string" && !saved.includes("#") && now.split("#")[0] === saved);  // 연결 열쇠 비교(서버 use_counts.same_source와 같은 규칙: 폴더만 있는 예전 열쇠는 그 폴더의 어느 파일 연결과도 같다)
const numCol = (rows, c) => rows.some(r => typeof r[c] === "number" || bigInt(r[c]));  // 숫자(큰 정수 포함) 컬럼이면 머리글도 오른쪽에
function shown(v) {
  if (typeof v === "number") { const t = v.toLocaleString(); return [t, t.replace(/,/g, "") === String(v) ? "" : String(v)]; }
  const s = isNested(v) ? inline(v) : String(v ?? ""), max = +($("maxlen")?.value ?? 40);
  const full = isNested(v) ? JSON.stringify(v, null, 2) : s;
  return max > 0 && s.length > max ? [s.slice(0, max) + "…", full] : [s, isNested(v) && full !== s ? full : ""];
}
// 표 칸. 중첩 값 칸(.nest)은 누르면 들여쓴 전체를 칸 안에 펼치고, 다시 누르면 접는다.
function cell(v) {
  const [text, full] = shown(v), title = full ? ` title="${esc(full)}"` : "";
  if (isNested(v)) return `<td class="nest"${title} data-full="${esc(full || text)}">${esc(text)}</td>`;
  return typeof v === "number" ? `<td class="num"${title}>${text}</td>` : bigInt(v) ? `<td class="num">${esc(text)}</td>` : `<td${title}>${esc(text)}</td>`;
}
// 결과 표 {columns, rows}: 숫자 컬럼은 머리글도 오른쪽에. SQL 실행과 문장 보기의 결과가 함께 쓴다.
const resultTable = ({ columns, rows }) => !rows.length ? '<span class="empty">결과가 없습니다.</span>'
  : `<table><thead><tr>${columns.map(c => `<th class="${numCol(rows, c) ? "num" : ""}">${esc(c)}</th>`).join("")}</tr></thead><tbody>` +
    rows.map(r => "<tr>" + columns.map(c => cell(r[c])).join("") + "</tr>").join("") + "</tbody></table>";
document.addEventListener("click", e => {
  const td = e.target.closest("td.nest");
  if (!td) return;
  if (td.dataset.short === undefined) { td.dataset.short = td.textContent; td.replaceChildren(el("pre", { className: "nest-full", textContent: td.dataset.full })); }
  else { td.textContent = td.dataset.short; delete td.dataset.short; }
});
// 메시지 칸에 글자를 쓴다. ok가 true면 성공 색, 글자면 그 클래스(예: "hint"는 안내 색), 아니면 오류 색.
const say = (id, text, ok) => { $(id).textContent = text; $(id).className = ok === true ? "ok" : ok || "err"; };
const connMsg = (text, ok) => say("err-connect", text, ok);  // 연결 메시지(설정 저장·알림 오류도 여기에)
// 요청하는 동안 버튼을 잠그고 "…"로 바꿨다가, 실패해도 되돌린다. 너비는 그대로 두고, 잠긴 동안 다시 부르면 무시한다.
async function busy(button, fn) {
  if (button.disabled) return;
  const label = button.textContent;
  button.style.minWidth = button.offsetWidth + "px";
  button.disabled = true;
  button.textContent = "…";
  try { return await fn(); }
  finally { button.disabled = false; button.textContent = label; button.style.minWidth = ""; }
}

// 위쪽 줄의 점유율: PC 메모리·CPU와 이 프로그램의 메모리를 2초마다 읽는다(프로그램의 CPU·메모리 비율은 마우스를 올리면).
async function showUsage() {
  if (document.hidden) return;  // 숨은 창에서는 묻지 않는다(2초마다 서버를 깨우지 않게)
  try {
    const d = await api("/api/usage");
    $("usage").innerHTML = `<span>PC 메모리<b>${d.pc_memory}%</b></span><span>PC CPU<b>${d.pc_cpu}%</b></span><span>프로그램<b>${d.memory_mb.toLocaleString()} MB</b></span>`;
    $("usage").title = `프로그램: 메모리 ${d.memory}%, CPU ${d.cpu}% (화면 포함). DB 주소로 연결하면 연산은 DB 서버가 합니다`;
  } catch (e) {}
}
showUsage();
setInterval(showUsage, 2000);

// 전체 시간: [검색]을 누르면 0에서 다시 시작하고, 그 뒤의 집계·차트·저장 시간을 더해 간다.
// 위쪽 줄의 작업 알약에 도는 동안은 "○○ 중 · 예상 · 걸린 시간"과 [취소], 끝나면 "끝 · 걸린 시간"이 나온다.
// 작업 하나의 예상 = 기준 테이블 행 수 × 그 작업 종류·행 수 구간의 직전 행당 시간(나노초/행, 설정으로 기억). 그 구간에서 안 해 봤으면 예상에 더하지 않는다.
const rates = {};  // 설정 이름 → 행당 시간. 처음 쓸 때 저장된 값을 읽어 둔다.
// 행당 시간의 설정 이름: 작업 종류와 행 수의 구간(10배 단위, settings.TIME_BANDS 안으로 맞춘다)마다 따로다.
const rateName = (kind, rows) => `time_${kind}_${Math.min(SERVER.time.bands[1], Math.max(SERVER.time.bands[0], String(rows).length - 1))}`;
const secs = ms => (ms / 1000).toFixed(1) + "초";
let total = { guess: 0, done: 0 }, timer;  // 예상 합계와 끝난 작업의 시간 합계(ms)
const going = new Set();  // 지금 도는 작업들 { at: 시작 시각, kind }
// 작업 이름. 알림 제목은 subject로 조사(받침이 있으면 "이", 없으면 "가")를 붙인다.
const JOBS = { search: "검색", sql: "SQL 실행", summary: "집계", chart: "차트", metric: "지표", export: "저장", profile: "통계", preview: "미리보기", run: "전처리 실행", analyze: "분석", collect: "수집" };
const subject = word => word + ((word.charCodeAt(word.length - 1) - 0xac00) % 28 ? "이" : "가");
function showTiming() {
  const now = performance.now(), ms = total.done + [...going].reduce((sum, run) => sum + now - run.at, 0), last = [...going].at(-1);
  $("job").classList.remove("hidden");
  $("job").classList.toggle("idle", !going.size);  // 도는 작업이 없으면 옅게, [취소]는 숨김
  $("cancel").classList.toggle("hidden", !going.size);
  $("job-name").textContent = going.size ? `${JOBS[last.kind] ?? "작업"} 중` : "끝";
  $("timing").textContent = [total.guess && `예상 ${secs(total.guess)}`, `걸린 시간 ${secs(ms)}`].filter(Boolean).join(" · ");
}
function resetTiming() {
  total = { guess: 0, done: 0 };
  going.clear();
  clearInterval(timer);
  $("job").classList.add("hidden");
}
$("cancel").onclick = () => api("/api/cancel", {}).catch(() => {});  // 멈춘 작업이 제 오류 칸에 "취소했습니다."를 띄운다.
// table: 행 수로 예상할 테이블(기본은 지금 고른 기준 테이블). 테이블이 없으면 예상을 구하지 않는다.
async function timed(kind, fn, table = { schema: S.schema, base: S.base }) {
  if (kind === "search") resetTiming();
  const mine = total, run = { at: performance.now(), kind };  // 도는 사이 새로 검색하면 이 작업의 시간은 새 합계에 넣지 않는다.
  going.add(run);
  clearInterval(timer);
  timer = setInterval(showTiming, 100);
  try {
    const rows = $("count")?.checked !== false && !!table.base && (await api("/api/rowcount", table)).rows;  // 전체 행 수를 세지 않으면 예상도 구하지 않는다. (#count가 없는 화면은 늘 센다)
    const name = rows && rateName(kind, rows);  // 같은 구간에서 돌려 본 적이 없으면 예상에 더하지 않는다.
    if (rows && (rates[name] ??= +saved(name) || 0)) mine.guess += rates[name] * rows / 1e6;
    const out = await fn();
    if (rows) {
      rates[name] = Math.min(Math.max(Math.round((performance.now() - run.at) * 1e6 / rows), 1), SERVER.time.max);
      saveSetting({ [name]: rates[name] });
    }
    notifyDone(kind, (performance.now() - run.at) / 1000, rows);  // 성공한 작업만(오류·취소는 위에서 던져져 여기까지 오지 않는다)
    return out;
  } finally {
    if (going.delete(run)) {
      mine.done += performance.now() - run.at;
      if (!going.size) clearInterval(timer);
      showTiming();
    }
  }
}

// 보낼 곳: 종류와 칸은 서버가 알려 주고(/api/destinations) 화면은 종류를 모른다.
let dest = { kinds: {}, destinations: {} };
let notifyAfter = +saved("notify_after") || 30;  // 이만큼(초) 이상 걸린 작업이 끝나면 알린다
const sendTo = (name, body) => api("/api/send", { name, ...body });
const takesImage = name => dest.kinds[dest.destinations[name].kind].image;
// 오래 걸린 작업이 끝나면 done인 보낼 곳마다 알린다. 데이터 값은 보내지 않는다. text: 알림의 본문(기본은 기준 테이블 이름)
function notifyDone(kind, seconds, rows, text = S.base) {
  if (seconds < notifyAfter) return;
  for (const [name, d] of Object.entries(dest.destinations)) {
    if (d.done) sendTo(name, { title: `${subject(JOBS[kind])} 끝났습니다`, text, ...(rows ? { rows } : {}), seconds: +seconds.toFixed(1) }).catch(e => connMsg(e.message));
  }
}
const destMsg = (text, ok) => say("msg-dest", text, ok);
// 목록에서 고른 보낼 곳(없으면 새로 만들기)을 폼에 채운다.
function pickDest() {
  const name = $("dest-list").value, saved = dest.destinations[name];
  $("dest-name").value = name;
  $("dest-done").checked = !!saved?.done;
  if (saved) $("dest-kind").value = saved.kind;
  $("dest-keep").value = saved?.keep ?? "saved";
  showFields();
}
// 비밀 칸의 안내: 보관 방식이 env면 환경변수 이름 칸(있는지 표시), 아니면 저장돼 있는지. state는 서버가 준 상태(true/false 또는 {env, set}).
function secretInput(input, keep, saved, state) {
  const same = saved && saved.keep === keep;
  if (keep === "env") {
    input.type = "text";
    input.value = same ? state?.env ?? "" : "";
    input.placeholder = "환경변수 이름 (예: IBIS_SLACK_TOKEN)";
    return same ? (state?.set ? "환경변수가 있습니다." : "지금 이 환경변수가 없습니다.") : "";
  }
  input.type = "password";
  input.placeholder = !same ? (keep === "session" ? "이번 실행 동안만 둡니다" : "") : state ? "저장됨 (바꾸려면 새로 입력)" : keep === "session" ? "다시 입력 필요 (프로그램을 다시 켰음)" : "다시 입력 필요";
  return "";
}
// 고른 종류의 칸을 서버의 표대로 그린다. 저장된 것이면 비밀이 아닌 칸은 값을, 비밀 칸은 보관 방식에 맞는 안내를 채운다.
function showFields() {
  const kind = $("dest-kind").value, spec = dest.kinds[kind], now = dest.destinations[$("dest-list").value];
  const saved = now?.kind === kind ? now : null, presets = spec.presets ?? [];
  $("dest-preset").replaceChildren(new Option("기본 틀", ""), ...presets.map((p, i) => new Option(p.label, i)));
  $("dest-preset").classList.toggle("hidden", !presets.length);
  $("dest-fields").replaceChildren(...spec.fields.map(f => {
    const input = f.options ? el("select", {}, ...f.options.map(([v, t]) => new Option(t, v)))
      : f.multiline ? el("textarea", { rows: 4 }) : el("input", { type: f.secret ? "password" : "text" });
    input.dataset.field = f.name;
    const note = f.secret ? secretInput(input, $("dest-keep").value, saved, saved?.[f.name]) : "";
    if (!f.secret) input.value = saved?.[f.name] ?? f.default ?? "";
    return el("div", {}, el("div", { className: "hint", textContent: f.label }), input, ...[f.hint, note].filter(Boolean).map(h => el("div", { className: "hint", textContent: h })));
  }));
}
function showDestinations(d, name = $("dest-list").value) {
  dest = d;
  fill($("dest-list"), Object.keys(d.destinations), "새로 만들기");
  $("dest-list").value = name in d.destinations ? name : "";
  $("dest-kind").replaceChildren(...Object.entries(d.kinds).map(([k, v]) => new Option(v.label, k)));
  $("dest-keep").replaceChildren(...Object.entries(d.keeps).map(([k, v]) => new Option("비밀 값: " + v, k)));
  pickDest();
  document.dispatchEvent(new Event("destinations"));  // 화면이 보낼 곳 목록을 쓰는 칸을 다시 채운다.
}
$("dest-list").onchange = pickDest;
$("dest-kind").onchange = $("dest-keep").onchange = showFields;
$("dest-preset").onchange = () => {
  const preset = dest.kinds[$("dest-kind").value].presets[$("dest-preset").value];
  for (const [k, v] of Object.entries(preset ?? {})) if (k !== "label") $("dest-fields").querySelector(`[data-field="${k}"]`).value = v;
  $("dest-preset").value = "";
};
$("dest-open").onclick = () => $("dest-panel").classList.remove("hidden");
$("dest-close").onclick = () => $("dest-panel").classList.add("hidden");
$("dest-panel").onclick = e => { if (e.target === $("dest-panel")) $("dest-panel").classList.add("hidden"); };
const [AFTER_LOW, AFTER_HIGH] = SERVER.sizes.notify_after;
Object.assign($("notify-after"), { value: notifyAfter, min: AFTER_LOW, max: AFTER_HIGH });
$("notify-after").onchange = () => {
  const n = Number($("notify-after").value), ok = Number.isInteger(n) && n >= AFTER_LOW && n <= AFTER_HIGH;
  destMsg(ok ? "" : `${AFTER_LOW}–${AFTER_HIGH} 사이의 정수를 넣으세요.`);
  if (ok) { notifyAfter = n; saveSetting({ notify_after: n }); }
};
$("dest-save").onclick = () => busy($("dest-save"), async () => {
  const name = $("dest-name").value.trim();
  const destination = { kind: $("dest-kind").value, done: $("dest-done").checked, keep: $("dest-keep").value,
    ...Object.fromEntries([...$("dest-fields").querySelectorAll("[data-field]")].map(i => [i.dataset.field, i.value])) };  // 비밀 칸을 비우면 서버가 저장된 값을 그대로 둔다.
  try { showDestinations(await api("/api/destinations", { name, destination }), name); destMsg("저장했습니다", true); } catch (e) { destMsg(e.message); }
});
$("dest-del").onclick = () => busy($("dest-del"), async () => {
  if (!$("dest-list").value) return;
  try { showDestinations(await api("/api/destinations", { name: $("dest-list").value, destination: null }), ""); destMsg("삭제했습니다", true); } catch (e) { destMsg(e.message); }
});
$("dest-test").onclick = () => busy($("dest-test"), async () => {
  if (!$("dest-list").value) return destMsg("저장한 보낼 곳을 고른 뒤 시험하세요.");
  try { await sendTo($("dest-list").value, { title: "시험 메시지", text: "Data Loupe에서 보낸 시험 메시지입니다." }); destMsg("보냈습니다", true); } catch (e) { destMsg(e.message); }
});
$("dest-check").onclick = () => busy($("dest-check"), async () => {
  if (!$("dest-list").value) return destMsg("저장한 보낼 곳을 고른 뒤 확인하세요.");
  try { destMsg((await api("/api/destinations/check", { name: $("dest-list").value })).message, true); } catch (e) { destMsg(e.message); }
});
api("/api/destinations").then(showDestinations).catch(() => {});

// Slack 봇(받기): 설정 저장, 연동 확인, 켜기·끄기. 토큰 칸을 비우고 저장하면 같은 보관 방식으로 둔 값을 그대로 쓴다.
const botMsg = (text, ok) => say("msg-bot", text, ok);
// keep: 칸에 보일 보관 방식(고르는 칸을 바꾸면 그것, 아니면 저장한 것). 저장한 방식과 같을 때만 저장된 상태를 보인다.
function showBot(d, keep = d.keep) {
  $("bot-keep").replaceChildren(...Object.entries(d.keeps).map(([k, v]) => new Option("토큰: " + v, k)));
  $("bot-keep").value = keep;
  [["bot-token", d.bot_token], ["bot-app", d.app_token]].forEach(([id, state]) => { $(id).title = secretInput($(id), keep, d.saved ? { keep: d.keep } : null, state); });
  $("bot-users").value = d.users.join(", ");
  $("bot-channels").value = d.channels.join(", ");
  $("bot-state").textContent = d.running ? `켜짐${d.team ? " · " + d.team : ""}` : "꺼짐";
  $("bot-log").textContent = d.log.length ? ["최근 요청:", ...d.log.map(r => `${r.at.slice(11)} ${r.user} ${r.allowed ? "" : "(거부) "}/ibis ${r.text}`)].join("\n") : "";
}
$("bot-keep").onchange = () => api("/api/bot").then(d => showBot(d, $("bot-keep").value)).catch(e => botMsg(e.message));
$("bot-save").onclick = () => busy($("bot-save"), async () => {
  try {
    showBot(await api("/api/bot", { keep: $("bot-keep").value, bot_token: $("bot-token").value, app_token: $("bot-app").value, users: $("bot-users").value, channels: $("bot-channels").value }));
    $("bot-token").value = $("bot-keep").value === "env" ? $("bot-token").value : "";
    $("bot-app").value = $("bot-keep").value === "env" ? $("bot-app").value : "";
    botMsg("저장했습니다", true);
  } catch (e) { botMsg(e.message); }
});
[["bot-check", "/api/bot/check"], ["bot-start", "/api/bot/start"], ["bot-stop", "/api/bot/stop"]].forEach(([id, url]) => $(id).onclick = () => busy($(id), async () => {
  try {
    const d = await api(url, {});
    if (d.message) botMsg(d.message, true); else { showBot(d); botMsg(d.running ? "봇을 켰습니다. Slack에서 /ibis 도움 을 보내 보세요." : "봇을 멈췄습니다.", true); }
  } catch (e) { botMsg(e.message); }
}));
$("dest-open").addEventListener("click", () => api("/api/bot").then(showBot).catch(() => {}));  // 열 때마다 켜짐 상태·최근 요청을 새로

// 1. 연결
document.querySelectorAll("input[name=kind]").forEach(r => r.onchange = () => {
  const folder = r.value === "folder" && r.checked;
  $("folder-box").classList.toggle("hidden", !folder);
  $("url-box").classList.toggle("hidden", folder);
});
// 연결 칸 채우기(비밀번호는 빼고): 종류 라디오와 경로 또는 주소·아이디
function fillConnection(info) {
  document.querySelector(`input[name=kind][value=${info.kind === "url" ? "url" : "folder"}]`).click();
  if (info.kind === "folder") $("path").value = info.path;
  if (info.kind === "url") {
    $("url").value = info.url;
    $("user").value = info.user;
  }
}
// 최근 연결: 연결에 성공했던 폴더·주소를 많이 쓴 순으로. 고르면 칸을 채운다(비밀번호는 저장하지 않아 다시 넣는다).
let recentConnections = [];
async function showRecentConnections() {
  try { recentConnections = (await api("/api/connections")).connections; } catch (e) { return; }
  const label = c => (c.info.kind === "url" ? `${c.info.url}${c.info.user ? ` (${c.info.user})` : ""}` : c.info.path) + ` · ${c.count}회`;
  $("recent-conn").replaceChildren(new Option("최근 연결 (많이 쓴 순)", ""), ...recentConnections.map((c, i) => new Option(label(c), i)));
  $("recent-conn").classList.toggle("hidden", !recentConnections.length);
}
// 연결 카드(connect.html): 화면 가운데에 띄운다. 연결이 없으면 닫을 수 없다(첫 화면은 고르기를 그만둘 수 있어 닫힌다).
// forText: 카드 제목 옆의 글(예: "쿼리로 들어갑니다")
const connClosable = () => !!connected || typeof afterConnect === "function";
function openConnect(forText = "") {
  $("conn-for").textContent = forText;
  $("conn-close").classList.toggle("hidden", !connClosable());
  $("conn-panel").classList.remove("hidden");
}
const closeConnect = () => $("conn-panel").classList.add("hidden");
// 쿼리·분석 화면은 따로 열리지만 연결은 서버에 남아 있다. 화면이 열린 뒤 화면 스크립트의 끝에서 부른다.
// 연결돼 있으면 방금 [연결]에 성공했을 때와 같은 상태로 만든다. 아니면 지난번 연결을 칸에 채워 두고, 연결이 필요한 화면이면 연결 카드를 띄운다.
async function resume() {
  try {
    const state = await api("/api/state");
    if (!state.connected) {
      const { recent } = await api("/api/recent");
      if (recent) fillConnection(recent);
      if (hasTables) openConnect("먼저 데이터에 연결하세요");
      return;
    }
    const { opened, schemas } = state;
    fillConnection(opened);
    if (opened.kind === "folder") {
      await listFiles();
      if (opened.files) { $("file").value = FLAT; restoreFlat(opened.files); }
      else $("file").value = opened.file;
      showFlat();
    }
    await showConnected(!!opened.files, schemas, opened);
  } catch (e) { connMsg(e.message); }
}
// 체크 목록: box 안에 값마다 체크 칸을 만든다. 모두 체크하지 않은 상태로 시작한다.
function checks(box, values) {
  box.replaceChildren(...values.map(v => {
    const label = document.createElement("label"), input = Object.assign(document.createElement("input"), { type: "checkbox", value: v });
    label.append(input, " " + v);
    return label;
  }));
}

// 테이블 파일(Parquet·CSV·JSON·Excel)은 여러 개를 골라 연다(파일마다 테이블 하나). 파일 목록에서 이 항목을 고르면 체크 목록이 나온다.
const FLAT = "*flat";  // 파일 이름에 쓸 수 없는 글자로 시작해 실제 경로와 겹치지 않는다.
const flatBoxes = () => [...document.querySelectorAll("#flat-list input")];
function showFlat() {
  $("flat").classList.toggle("hidden", $("file").value !== FLAT);
  $("flat-count").textContent = `${flatBoxes().filter(b => b.checked).length} / ${flatBoxes().length}개 선택` + (offList.size ? ` · 고른 파일 중 ${offList.size}개는 목록 밖` : "");
}
// 고른 파일 중 화면 목록(LIST_MAX개까지)에 안 그린 것. 연결할 때 함께 보낸다.
const offList = new Set();
const pickedFiles = () => [...flatBoxes().filter(b => b.checked).map(b => b.value), ...offList];
function restoreFlat(files) {
  const drawn = new Set(flatBoxes().map(b => b.value));
  flatBoxes().forEach(b => b.checked = files.includes(b.value));
  offList.clear();
  files.filter(f => !drawn.has(f)).forEach(f => offList.add(f));
  showFlat();
}
// 연결 칸(connect.html)이 있는 화면에서만 연결 칸의 버튼을 단다. 수집 화면처럼 연결 칸이 없는 화면도 common.js를 쓴다.
const hasConnect = !!$("connect");
const hasTables = !!$("base");  // 테이블을 고르는 화면(쿼리·분석). 첫 화면은 연결 카드만 있다.
const KIND_LABELS = { duckdb: "DuckDB", parquet: "Parquet", csv: "CSV", json: "JSON", excel: "Excel", geojson: "GeoJSON" };  // 찾을 파일 종류(서버의 source.KINDS)의 이름
if (hasConnect) {
showRecentConnections();
$("kinds").replaceChildren(...SERVER.file_kinds.map(k => el("label", {}, el("input", { type: "checkbox", value: k, checked: true }), KIND_LABELS[k] ?? k)));
$("path").onkeydown = e => { if (e.key === "Enter") $("find").click(); };
$("conn-close").onclick = closeConnect;
$("conn-panel").onclick = e => { if (e.target === $("conn-panel") && connClosable()) closeConnect(); };
$("crumb").classList.add("link");
$("crumb").title = "누르면 연결을 바꿉니다";
$("crumb").onclick = () => openConnect();
$("recent-conn").onchange = () => {
  const c = recentConnections[$("recent-conn").value];
  if (c) { fillConnection(c.info); $(c.info.kind === "url" ? "password" : "find").focus(); }
  $("recent-conn").value = "";
};
$("file").onchange = showFlat;
// 작업 메모리 상한(MB, 0 = 제한 없음): 목록에 있는 값이거나 GB로 직접 넣은 값. 저장만 하고 다음 [연결]부터 서버가 쓴다.
const memory = saved("memory") || "0";
$("memory").value = [...$("memory").options].some(o => o.value === memory) ? memory : "custom";
if ($("memory").value === "custom") $("memory-gb").value = memory / 1000;
$("memory-gb").classList.toggle("hidden", $("memory").value !== "custom");
$("memory").onchange = () => {
  $("memory-gb").classList.toggle("hidden", $("memory").value !== "custom");
  if ($("memory").value !== "custom") saveSetting({ memory: +$("memory").value });
};
$("memory-gb").onchange = () => {
  const mb = Math.round(Number($("memory-gb").value) * 1000), [low, high] = SERVER.sizes.memory;
  if (mb >= low && mb <= high) saveSetting({ memory: mb });
  else connMsg(`작업 메모리 상한은 ${low / 1000}GB 이상 ${high / 1000}GB 이하로 넣으세요.`);
};
// 계산 코어 수(0 = 전부): 저장만 하고 다음 [연결]부터 서버가 쓴다. 고를 수 있는 값은 1, 2, 4, 8, …과 이 PC의 코어 수.
const cores = SERVER.sizes.threads[1], coreSteps = [...new Set([1, 2, 4, 8, 16, 32, 64].filter(n => n < cores).concat(cores))];
$("threads").innerHTML = `<option value="0">전부 ${cores}개 (기본)</option>` + coreSteps.filter(n => n < cores).map(n => `<option value="${n}">${n}개</option>`).join("");
$("threads").value = [...$("threads").options].some(o => o.value === String(saved("threads") || 0)) ? String(saved("threads") || 0) : "0";
$("threads").onchange = () => saveSetting({ threads: +$("threads").value });
// 자세히: 처리 용량을 알 수 없을 때(scan_unknown: run·block)와 표본이 너무 적게 오는 기준(sample_too_few, 화면은 %, 비우면 기본 20%).
$("scan-unknown").value = saved("scan_unknown") || "run";
$("scan-unknown").onchange = () => saveSetting({ scan_unknown: $("scan-unknown").value });
const [FEW_LOW, FEW_HIGH] = SERVER.floats.sample_too_few;
if (saved("sample_too_few")) $("too-few").value = Math.round(saved("sample_too_few") * 100);
$("too-few").onchange = () => {
  const text = $("too-few").value.trim(), n = Number(text) / 100;
  if (!text) { saveSetting({ sample_too_few: 0 }); connMsg(""); }
  else if (n >= FEW_LOW - 1e-9 && n <= FEW_HIGH + 1e-9) { saveSetting({ sample_too_few: n }); connMsg(""); }
  else connMsg(`표본 기준은 ${Math.round(FEW_LOW * 100)}% 이상 ${Math.round(FEW_HIGH * 100)}% 이하로 넣으세요(비우면 20%).`);
};
$("flat-list").onchange = showFlat;
$("flat-all").onclick = () => { flatBoxes().forEach(b => b.checked = true); showFlat(); };
}
let connected = null;  // 지금 연결의 종류: "flat"(테이블 파일), "other"(DuckDB 파일·DB 주소), 없으면 null
// [모두 해제]: 체크를 모두 푼다. 테이블 파일로 연결돼 있었으면 그 연결을 끊고 화면을 연결 전으로 되돌린다(파일을 다시 골라 새로 연결).
// DuckDB 파일이나 DB 주소로 연결돼 있었으면 연결은 그대로 두고 화면에 올라온 것만 비운다.
if (hasConnect) $("flat-none").onclick = () => busy($("flat-none"), async () => {
  flatBoxes().forEach(b => b.checked = false);
  offList.clear();
  showFlat();
  if (!connected) return;
  try {
    if (connected === "flat") {
      await api("/api/disconnect", {});
      connected = connName = null;
      $("after-connect")?.classList.add("hidden");
      connMsg("연결을 해제했습니다. 쓸 파일을 고르고 [연결]을 누르세요.", "hint");
    } else connMsg("연결은 그대로 두고 화면만 비웠습니다. 테이블을 다시 고르세요.", "hint");
    if (typeof clearScreen === "function") clearScreen();
  } catch (e) { connMsg(e.message); }
});
// 경로(폴더나 파일 하나)에서 체크한 종류의 파일을 찾는다. 하위 폴더까지 찾을지는 체크로. 파일이 하나뿐이면 바로 고른 상태로 둔다.
const LIST_MAX = 2000;  // 화면에 올리는 파일 수의 상한(수만 개를 한 번에 그리면 화면이 멎는다)
async function listFiles() {
  const kinds = [...$("kinds").querySelectorAll("input:checked")].map(b => b.value);
  const d = await api("/api/files", { path: $("path").value.trim(), kinds, deep: $("deep").checked });
  const files = d.files.slice(0, LIST_MAX), flat = d.flat.slice(0, LIST_MAX);
  fill($("file"), files);
  if (d.flat.length) $("file").append(new Option(`테이블 파일 고르기: Parquet·CSV·JSON·Excel·GeoJSON (${d.flat.length}개)`, FLAT));
  offList.clear();
  checks($("flat-list"), flat);  // 처음에는 아무것도 고르지 않은 상태. 쓸 파일만 체크한다.
  if (!d.files.length && d.flat.length === 1) { $("file").value = FLAT; flatBoxes()[0].checked = true; }
  $("file").classList.remove("hidden");
  showFlat();
  const more = Math.max(d.files.length, d.flat.length) > LIST_MAX;
  connMsg(more ? `파일이 많아 앞의 ${LIST_MAX.toLocaleString()}개만 보여요. 경로나 종류를 좁혀 주세요.` : "", "hint");
}
if (hasConnect) $("find").onclick = () => busy($("find"), async () => {
  try { await listFiles(); } catch (e) { connMsg(e.message); }
});
// 연결에 성공한 상태로 만든다([연결]을 누른 뒤와 화면을 새로 연 뒤가 같다). flat: 테이블 파일(Parquet·CSV·JSON·Excel) 연결인가
// 테이블을 고르는 화면(쿼리·분석)에서만 스키마·테이블 칸을 채운다. 첫 화면은 연결만 하고 afterConnect로 넘어간다.
async function showConnected(flat, schemas, info) {
  connected = flat ? "flat" : "other";
  connName = connLabel(info);
  connMsg("연결됨", true);
  closeConnect();
  if (!hasTables) return showCrumb();
  clearScreen();  // 이전 연결에서 고른 테이블과 조건은 새 연결과 맞지 않으므로 비운다.
  $("schema").classList.toggle("hidden", !schemas.length);
  fill($("schema"), schemas, "기본 스키마");
  $("after-connect").classList.remove("hidden");
  await loadTables();
  if (typeof afterTables === "function") afterTables();  // 쿼리·분석: 남은 초안이 있으면 묻는다(기다리지 않는다)
}
if (hasConnect) $("connect").onclick = () => busy($("connect"), async () => {
  const kind = document.querySelector("input[name=kind]:checked").value;
  const body = kind === "folder"
    ? { kind, path: $("path").value, ...($("file").value === FLAT ? { files: pickedFiles() } : { file: $("file").value }) }
    : { kind, url: $("url").value, user: $("user").value, password: $("password").value };
  $("driver-hint").classList.add("hidden");
  try {
    const { schemas } = await api("/api/connect", body);
    showRecentConnections();  // 쓴 횟수가 바뀌었다
    await showConnected(!!body.files, schemas, body);
    if (typeof startRefresh === "function") startRefresh();  // 연결에 성공하면 어느 화면에서나 대시보드 최신화를 시작한다(dash_refresh.js)
    if (typeof afterConnect === "function") afterConnect();  // 첫 화면: 고른 화면으로 넘어간다
  } catch (e) { connMsg(e.message); if (e.driver) showDriverHint(e.driver); }
});
// 드라이버 안내(drivers.py의 entry): 설명, 설치 명령과 [복사], 따로 설치할 프로그램, exe 안내. 연결이 드라이버 때문에 실패한 자리와 드라이버 표가 함께 쓴다.
const DRIVER_STATUS = { installed: "설치됨", missing: "없음", program_needed: "프로그램 필요" };
function copyCommand(text) {
  const b = el("button", { className: "ghost", textContent: "복사", onclick: async () => {
    try { await navigator.clipboard.writeText(text); b.textContent = "복사됨"; } catch (e) { b.textContent = "직접 선택해 복사"; }
    setTimeout(() => b.textContent = "복사", 1500);
  } });
  return b;
}
const driverExtra = d => [d.program && `따로 설치할 프로그램: ${d.program.name} (${d.program.url})`, d.after, d.notice].filter(Boolean);
function showDriverHint(d) {
  $("driver-hint").replaceChildren(el("div", { className: "drv-guide" }, el("div", { textContent: d.explanation }),
    el("div", { className: "row", style: "align-items:center" }, el("code", { textContent: d.command }), copyCommand(d.command)),
    ...driverExtra(d).map(t => el("div", { className: "hint", textContent: t, style: "margin:0; user-select:text" }))));
  $("driver-hint").classList.remove("hidden");
}
if (hasConnect) {
  $("drv-close").onclick = () => $("drivers-panel").classList.add("hidden");
  $("drivers-panel").onclick = e => { if (e.target === $("drivers-panel")) $("drivers-panel").classList.add("hidden"); };
  $("drv-open").onclick = () => busy($("drv-open"), async () => {
    try {
      const { drivers } = await api("/api/drivers");
      $("drv-rows").replaceChildren(...drivers.map(d => el("tr", {}, el("td", { textContent: d.group }), el("td", { textContent: d.name, title: "주소 예: " + d.address }),
        el("td", { className: d.status, textContent: DRIVER_STATUS[d.status] }),
        el("td", {}, d.explanation, ...[`주소 예: ${d.address}`, ...driverExtra(d)].map(t => el("div", { className: "hint", textContent: t, style: "margin:2px 0 0; user-select:text" }))),
        el("td", {}, el("code", { textContent: d.command }), " ", copyCommand(d.command)))));
      say("err-drivers", "", true);
      $("drivers-panel").classList.remove("hidden");
    } catch (e) { connMsg(e.message); }
  });
}

// 2. 테이블
// 가상 테이블(쿼리 화면만, body의 data-virtual="1")은 "가상 테이블" 묶음으로 뒤에 붙인다. 고른 테이블이 목록에 남아 있으면 그대로 둔다.
async function loadTables() {
  S.schema = $("schema").value;
  const withVirtual = document.body.dataset.virtual === "1";
  const { tables, virtual = [] } = await api("/api/tables?schema=" + encodeURIComponent(S.schema) + (withVirtual ? "&virtual=1" : ""));
  const now = $("base").value;
  fill($("base"), tables, "기준 테이블 선택");
  if (virtual.length) $("base").append(el("optgroup", { label: "가상 테이블" }, ...virtual.map(v => new Option(v, v))));
  if ([...$("base").options].some(o => o.value === now && now)) $("base").value = now;
  showCrumb();
  document.dispatchEvent(new Event("tables"));  // 화면이 테이블 목록을 따로 쓰면(SQL 탭) 다시 그린다
}
if (hasTables) $("schema").onchange = loadTables;

// 위쪽 줄의 연결 이름: "파일 / 테이블"(주소면 호스트/DB). 연결이 없으면 "연결 안 됨". 연결 칸이 없는 화면(수집)은 숨긴다.
let connName = null;
function connLabel(info) {
  if (!info) return null;
  if (info.kind === "url") {
    try { const u = new URL(info.url); return (u.host + u.pathname).replace(/\/$/, "") || info.url; } catch (e) { return info.url; }
  }
  return (info.files ? `파일 ${info.files.length}개` : (info.file || "").split(/[\\/]/).pop()) || info.path;
}
function showCrumb() {
  $("crumb").classList.toggle("hidden", !hasConnect);
  $("crumb").classList.toggle("on", !!connName);
  $("crumb-text").innerHTML = connName ? esc(connName) + (S.base ? ` <em>/</em> ${esc(S.base)}` : "") : "연결 안 됨";
}
if (hasTables) $("base").addEventListener("change", () => setTimeout(showCrumb));  // 화면 스크립트가 S.base를 바꾼 뒤에
showCrumb();

// 설정(화면 모드, 차트 비율, 그림 저장 모드, 따옴표 해석, 전체 행 수 세기, 조건 입력 방식, 끌어서 조절한 크기, 직전 실행의 행당 시간): 바꾸면 서버가 설정 파일에 저장해 다음 실행 때도 쓴다.
const saveSetting = changes => api("/api/settings", changes).catch(e => connMsg(e.message));
const ICONS = {  // 화면 모드 버튼: 지금이 밝으면 달(어둡게로), 어두우면 해(밝게로)
  moon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
  sun: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
};
function showTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("theme").innerHTML = theme === "dark" ? ICONS.sun : ICONS.moon;
  $("theme").title = theme === "dark" ? "밝은 화면으로" : "어두운 화면으로";
}
$("theme").onclick = () => {
  const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  showTheme(theme);
  saveSetting({ theme });
};
showTheme(document.documentElement.dataset.theme);

// 크기 조절 모드: [크기 조절]을 켰을 때만 경계가 보이고 잡힌다. 켤 때마다 꺼진 상태로 시작한다.
function showResizing(on) {
  document.documentElement.classList.toggle("resizing", on);
  $("resize").classList.toggle("on", on);
}
$("resize").onclick = () => showResizing(!document.documentElement.classList.contains("resizing"));
showResizing(false);
// 경계를 끌면 그 크기의 CSS 변수를 바꾸고, 놓으면 저장한다. 더블클릭하면 기본 크기로 돌아간다(0을 저장).
const sized = { panel: () => document.querySelector(".work:not(.hidden) .side") };  // 보이는 화면의 패널  // 화면이 크기를 바꿀 상자를 더한다
document.querySelectorAll("[data-size]").forEach(grip => {
  const name = grip.dataset.size, [min, max, axis] = SIZES[name], root = document.documentElement.style;
  const at = e => axis === "x" ? e.clientX : e.clientY;
  grip.onpointerdown = e => {
    const start = at(e), box = sized[name]().getBoundingClientRect(), from = axis === "x" ? box.width : box.height;
    const sign = axis === "x" && box.left >= grip.getBoundingClientRect().left ? -1 : 1;  // 경계 오른쪽의 상자(오른쪽 패널)는 왼쪽으로 끌면 넓어진다
    let size = 0;
    grip.setPointerCapture(e.pointerId);
    grip.classList.add("on");
    document.body.style.userSelect = "none";
    grip.onpointermove = m => { size = Math.round(Math.min(max, Math.max(min, from + sign * (at(m) - start)))); root.setProperty("--" + name, size + "px"); };
    grip.onpointerup = () => {
      grip.onpointermove = grip.onpointerup = null;
      grip.classList.remove("on");
      document.body.style.userSelect = "";
      if (size) saveSetting({ [name]: size });
    };
  };
  grip.ondblclick = () => { root.removeProperty("--" + name); saveSetting({ [name]: 0 }); };
});

// 고르는 칸(select)의 목록을 직접 그린다. 값과 change 이벤트는 select가 그대로 맡고, 펼쳐지는 목록만 바꾼다.
// 누르거나 Space·Enter·↑↓로 펼치고, 목록에서는 ↑↓로 옮기고 Enter로 고르고 Esc로 닫는다.
// 항목이 많으면 맨 위에 찾는 칸이 나오고, 친 글자가 들어 있는 항목만 남는다(입력 칸으로 받으므로 한글도 된다).
const menuBox = document.body.appendChild(Object.assign(document.createElement("div"), { className: "menu hidden", innerHTML: '<input placeholder="찾기"><div></div>' }));
const [menuFind, menuList] = menuBox.children;
const MENU_FIND = 8;  // 항목이 이보다 많을 때만 찾는 칸을 보인다.
let menuFor = null, menuAt = null;  // 목록을 펼친 select와, 지금 가리키는 항목
const menuItems = () => [...menuList.children].filter(item => !item.classList.contains("hidden"));
function closeMenu() { menuBox.classList.add("hidden"); menuFor = null; }
function pointMenu(item, scroll) {
  menuAt?.classList.remove("on");
  menuAt = item || null;
  menuAt?.classList.add("on");
  if (scroll) menuAt?.scrollIntoView({ block: "nearest" });
}
function openMenu(sel) {
  if (sel.disabled || !sel.options.length) return;
  const find = sel.options.length > MENU_FIND;
  menuFor = sel;
  menuList.replaceChildren(...[...sel.options].map((o, i) => Object.assign(document.createElement("div"), { textContent: o.text, className: i === sel.selectedIndex ? "now" : "" })));
  menuFind.value = "";
  menuFind.classList.toggle("hidden", !find);
  menuList.style.maxHeight = "";
  placeMenu(sel);
  pointMenu(menuList.children[sel.selectedIndex], true);
  if (find) menuFind.focus();
}
// 칸 아래에 펼치되, 아래가 좁고 위가 더 넓으면 위로 펼친다. 목록은 300px까지만 커지고 넘으면 스크롤된다.
function placeMenu(anchor) {
  menuBox.classList.remove("hidden");
  const r = anchor.getBoundingClientRect(), below = innerHeight - r.bottom - 8, above = r.top - 8, up = below < Math.min(menuBox.offsetHeight, 160) && above > below;
  Object.assign(menuBox.style, { left: r.left + "px", minWidth: r.width + "px", top: up ? "" : r.bottom + 2 + "px", bottom: up ? innerHeight - r.top + 2 + "px" : "" });
  menuList.style.maxHeight = Math.min(300, (up ? above : below) - (menuBox.offsetHeight - menuList.offsetHeight)) + "px";
}
// 입력 칸의 data-list="아이디"가 가리키는 <datalist>의 값을 같은 목록으로 보인다(<input list>의 목록은 별도 창이라 창만 공유하면 안 보인다).
// 친 글자가 들어 있는 값만 남고, 고르면 칸에 들어간다. datalist 요소는 값을 담는 데만 쓴다.
let comboQuiet = false;  // 고른 뒤 칸에 커서를 돌려줄 때 목록이 다시 뜨지 않게
function showCombo(inp) {
  const want = inp.value.trim().toLowerCase(), hits = [...document.getElementById(inp.dataset.list)?.children ?? []].map(o => o.value).filter(v => v.toLowerCase().includes(want) && v !== inp.value);
  if (inp.disabled || !hits.length) return menuFor === inp ? closeMenu() : undefined;
  menuFor = inp;
  menuList.replaceChildren(...hits.map(v => Object.assign(document.createElement("div"), { textContent: v })));
  menuFind.classList.add("hidden");
  placeMenu(inp);
  pointMenu(null);
}
const comboOn = e => !comboQuiet && !e.isComposing && e.target.matches?.("input[data-list]") && showCombo(e.target);
document.addEventListener("focusin", comboOn);
document.addEventListener("click", comboOn);
document.addEventListener("input", comboOn);
function pickMenu(item) {
  const sel = menuFor, i = [...menuList.children].indexOf(item);
  if (sel.tagName === "INPUT") {
    if (i < 0) return;
    sel.value = item.textContent;
    closeMenu();
    comboQuiet = true;
    sel.focus();
    sel.dispatchEvent(new Event("input", { bubbles: true }));
    comboQuiet = false;
    return;
  }
  closeMenu();
  sel.focus();
  if (i < 0 || i === sel.selectedIndex) return;
  sel.selectedIndex = i;
  sel.dispatchEvent(new Event("change", { bubbles: true }));
}
menuFind.oninput = () => {
  const want = menuFind.value.trim().toLowerCase();
  [...menuList.children].forEach(item => item.classList.toggle("hidden", !item.textContent.toLowerCase().includes(want)));
  pointMenu(menuItems()[0], true);
};
document.addEventListener("mousedown", e => {
  const sel = e.target.closest("select");
  if (sel) { e.preventDefault(); sel.focus(); menuFor === sel ? closeMenu() : openMenu(sel); }  // preventDefault: 창 엔진의 목록이 뜨지 않게
  else if (!menuBox.contains(e.target)) closeMenu();
});
menuList.onmousemove = e => { if (e.target.parentElement === menuList) pointMenu(e.target); };
menuList.onclick = e => { if (e.target.parentElement === menuList) pickMenu(e.target); };
document.addEventListener("keydown", e => {
  if (!menuFor) {
    if ([" ", "Enter", "ArrowDown", "ArrowUp", "F4"].includes(e.key) && e.target.matches("select")) { e.preventDefault(); openMenu(e.target); }
    return;
  }
  if (e.isComposing) return;  // 한글을 조합하는 동안의 키는 입력기가 쓴다.
  const items = menuItems(), step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
  if (e.key === "Tab") return closeMenu();
  if (menuFor.tagName === "INPUT") {  // 입력 칸의 목록: 글자(Space 포함)는 칸이 받고, ↑↓로 가리킨 항목이 있을 때만 Enter로 고른다
    if (e.key === "Escape") closeMenu();
    else if (e.key === "Enter" && menuAt) { pickMenu(menuAt); e.stopPropagation(); }  // 칸의 Enter 동작(추가 등)은 하지 않는다
    else if (step) pointMenu(items[Math.min(Math.max(items.indexOf(menuAt) + step, 0), items.length - 1)], true);
    else return;
    return e.preventDefault();
  }
  if (e.key === "Escape") { const sel = menuFor; closeMenu(); sel.focus(); }
  else if (e.key === "Enter" || (e.key === " " && e.target !== menuFind)) pickMenu(menuAt);
  else if (step) pointMenu(items[Math.min(Math.max(items.indexOf(menuAt) + step, 0), items.length - 1)], true);
  else return;
  e.preventDefault();
}, true);
// 목록 밖이 스크롤되거나 창 크기가 바뀌면 칸과 어긋나므로 닫는다.
document.addEventListener("scroll", e => { if (!menuBox.contains(e.target)) closeMenu(); }, true);
addEventListener("resize", closeMenu);
addEventListener("blur", closeMenu);

// title 힌트를 페이지 안 말풍선으로 그린다(창 엔진의 툴팁은 별도 창이라 창만 공유하면 보이지 않는다). 마우스를 올리거나 키보드 포커스가 가면 보인다.
// 보이는 동안 title은 data-tip으로 옮겨 네이티브 툴팁이 같이 뜨지 않게 하고, 벗어나면 되돌린다. 이벤트 위임이라 나중에 만든 요소도 된다.
// 글자 없는 단추처럼 title이 이름 노릇을 하던 요소는 옮기는 동안 aria-label을 달아 이름이 사라지지 않게 한다.
const tipBox = document.body.appendChild(Object.assign(document.createElement("div"), { className: "tip hidden", role: "tooltip" }));
let tipFor = null, tipTimer = 0;
function hideTip() {
  clearTimeout(tipTimer);
  tipBox.classList.add("hidden");
  const host = tipFor;
  tipFor = null;
  if (!host) return;
  if (host.dataset.tipAria !== undefined) { host.removeAttribute("aria-label"); delete host.dataset.tipAria; }
  if (!host.hasAttribute("title")) host.setAttribute("title", host.dataset.tip);  // 그 사이 코드가 새 title을 달았으면 그것을 둔다. setAttribute: SVG 요소에는 .title 속성이 없다
  delete host.dataset.tip;
}
function showTip(host, wait) {
  if (host === tipFor) return;
  hideTip();
  if (!host.getAttribute("title")) return;
  tipFor = host;
  host.dataset.tip = host.getAttribute("title");
  host.removeAttribute("title");
  if (!host.hasAttribute("aria-label") && !host.textContent.trim() && !host.labels?.length) { host.setAttribute("aria-label", host.dataset.tip); host.dataset.tipAria = ""; }
  tipTimer = setTimeout(() => {
    if (tipFor !== host || !host.isConnected) return;
    tipBox.textContent = host.dataset.tip;
    tipBox.classList.remove("hidden");
    const r = host.getBoundingClientRect(), w = tipBox.offsetWidth, h = tipBox.offsetHeight;
    const top = r.bottom + 6 + h <= innerHeight - 4 ? r.bottom + 6 : Math.max(4, r.top - 6 - h);  // 아래에 자리가 없으면 위에
    Object.assign(tipBox.style, { left: Math.min(Math.max(4, r.left), Math.max(4, innerWidth - w - 4)) + "px", top: top + "px" });
  }, wait);
}
const tipHost = e => e.target.closest?.("[title]") ?? null;
document.addEventListener("mouseover", e => { const h = tipHost(e); if (h) showTip(h, 350); });
document.addEventListener("focusin", e => { const h = tipHost(e); if (h) showTip(h, 0); });
document.addEventListener("mouseout", e => { if (tipFor && !tipFor.contains(e.relatedTarget)) hideTip(); });
document.addEventListener("focusout", hideTip);
document.addEventListener("mousedown", hideTip);
document.addEventListener("keydown", hideTip, true);
document.addEventListener("scroll", hideTip, true);
addEventListener("blur", hideTip);

// 잠깐 보이는 안내("되돌렸어요" 등). 화면 아래 가운데에 1.5초.
const toastBox = document.body.appendChild(Object.assign(document.createElement("div"), { className: "toast hidden", role: "status" }));
let toastTimer = 0;
function flash(text) {
  toastBox.textContent = text;
  toastBox.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastBox.classList.add("hidden"), 1500);
}
