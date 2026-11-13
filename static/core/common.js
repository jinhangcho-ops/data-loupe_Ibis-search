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
// 차트 섬(/chart iframe, web/lib/island.ts 규약)에 그릴 것을 보낸다: ready 전 것은 마지막 것만 두었다 보내고, size로 높이를 맞추고, error는 onError로.
// draw(msg)만 부르면 마지막 것을 지금 화면 모드로 다시 보낸다. draw.picture(모드, 형식)는 지금 그림을 그림 파일(data URL)로(섬의 png 답). 쿼리·분석 화면과 문장 결과가 쓴다.
function island(frame, onError) {
  let ready = false, last = null, wait = null;
  const post = () => last && ready && frame.contentWindow.postMessage({ type: "draw", ...last, theme: document.documentElement.dataset.theme === "dark" ? "dark" : "light" }, location.origin);
  const on = e => {
    if (!frame.isConnected) return removeEventListener("message", on);  // 지운 iframe
    if (e.origin !== location.origin || e.source !== frame.contentWindow) return;
    const m = e.data;
    if (m?.type === "ready") { ready = true; post(); }
    else if (m?.type === "size") frame.style.height = `${m.height}px`;
    else if (m?.type === "shown") draw.shown = { table: m.table, full: m.full };  // 그린 뒤 보이는 값과 모든 데이터 값(계열 그림만)
    else if (m?.type === "png") { wait?.ok(m.dataUrl); wait = null; }
    else if (m?.type === "error") { if (wait) { wait.no(new Error(m.message)); wait = null; } else onError(m.message); }
  };
  addEventListener("message", on);
  const draw = msg => { if (msg) last = msg; post(); };
  draw.picture = (theme, format, all) => new Promise((ok, no) => {
    if (!ready || !last) return no(new Error("먼저 차트를 그리세요."));
    if (wait) return no(new Error("앞 그림을 만드는 중이에요."));  // 대기 자리는 하나: 앞 요청의 답이 새 요청으로 가지 않게 새 요청을 거절한다
    wait = { ok, no };
    frame.contentWindow.postMessage({ type: "picture", theme, format, all }, location.origin);
  });
  draw.dispose = () => { removeEventListener("message", on); wait?.no(new Error("차트를 닫았어요.")); wait = null; };  // iframe을 지울 때 바로 푼다(메시지가 안 오면 리스너가 남는다)
  return draw;
}
// 자동 규칙 이름(보이는 글자)은 서버 viz_rules.AUTO_NAMES 하나(SERVER.dash.auto_names). 규칙 목록·기본 켬 여부도 서버(SERVER.dash.auto)가 정한다.
// [자동 규칙] 판: rules에 든 규칙마다 체크를 그린다(체크 = 켬). 바꾸면 끈 것만 담은 객체 {규칙: false}로 onChange. now는 지금 끈 것. 쿼리·분석 화면이 같이 쓴다.
function autoPanel(box, rules, now, onChange) {
  const off = { ...now };
  box.replaceChildren(...Object.keys(SERVER.dash.auto).filter(r => rules.includes(r)).map(r => {
    const input = el("input", { type: "checkbox", checked: off[r] !== false });
    input.onchange = () => { if (input.checked) delete off[r]; else off[r] = false; onChange({ ...off }); };
    return el("label", { className: "check" }, input, ` ${SERVER.dash.auto_names[r]}`);
  }));
}
// 차트 낱말·쿼리 화면 자동 규칙 기본값을 저장한다(POST /api/viz). 응답으로 SERVER.dash.viz를 갱신하고 돌려준다.
async function saveVizPrefs(prefs) {
  SERVER.dash.viz = await api("/api/viz", prefs);
  return SERVER.dash.viz;
}
// 집계 이름은 서버(함수 사전)가 주는 목록이다: SERVER.agg_names
const aggName = id => SERVER.agg_names?.[id] ?? id;
// 서버(dash_calc.series_name)와 같은 계열 이름: 개수 / 합계 (컬럼) / 옵션이 있으면 분위수 (컬럼) p=0.9 (order는 뺀다). 쿼리 차트가 쓴다
const seriesTitle = s => aggName(s.agg) + (s.agg === "count" || !s.y ? "" : ` (${s.y})`) +
  Object.entries(s.opts ?? {}).filter(([k]) => k !== "order").map(([k, v]) => ` ${k}=${[].concat(v).join(",")}`).join("");
const UNIT_NAMES = { day: "일", week: "주", month: "월" };
// 알림 줄(id)에 대시보드에 넣은 결과 글과 "대시보드에서 보기" 링크를 보인다(쿼리·전처리 화면). added: { id, name, cards }
function showAdded(id, added) {
  say(id, `‘${added.name}’ 대시보드에 넣었어요. 지금 카드는 ${added.cards}개예요. `, true);
  $(id).append(el("a", { href: "/dashboard?open=" + encodeURIComponent(added.id), textContent: "대시보드에서 보기" }));
}
document.querySelectorAll(".rail a").forEach(a => a.classList.toggle("on", a.pathname === location.pathname));  // 지금 화면의 메뉴를 고른 모양으로
const S = { schema: "", base: "", cols: [] };
async function send(url, body) {
  const res = await fetch(url, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) { const d = await res.json(), err = new Error(d.error); err.driver = d.driver; throw err; }  // driver: 드라이버가 없어 못 붙었을 때의 안내
  if (res.headers.get("X-Needs-Confirm")) {  // 서버가 처리 용량이 기준을 넘는다고 물으면 팝업으로 묻고, 확인하면 그 실행만 confirmed를 붙여 다시 보낸다.
    const d = await res.json(), ok = await confirmScan(d);
    if (d.blocked || !ok) throw new Error(d.blocked ? d.text : "취소했어요.");
    return send(url, { ...body, confirmed: true });
  }
  return res;
}
// 처리 용량 확인 팝업: 내용과 [확인] [취소](알 수 없는데 차단으로 정했으면 이유와 [닫기]만). 확인하면 true.
function confirmScan(d) {
  return new Promise(done => {
    const pick = ok => () => { box.remove(); done(ok); };
    const buttons = d.blocked ? [el("button", { className: "btn", textContent: "닫기", onclick: pick(false) })]
      : [el("button", { className: "ghost", textContent: "취소", onclick: pick(false) }), el("button", { className: "btn p", textContent: "그대로 실행", onclick: pick(true) })];
    const box = el("div", { className: "modal", onclick: e => { if (e.target === box) pick(false)(); } },
      el("div", { className: "modal-box" }, el("h3", { id: "scan-title", textContent: d.blocked ? "실행하지 않았어요" : "읽을 데이터가 많아요" }),
        el("p", { textContent: d.text, style: "white-space:pre-wrap; margin:10px 0 14px" }), el("div", { className: "row", style: "justify-content:flex-end" }, ...buttons)));
    box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true"); box.setAttribute("aria-labelledby", "scan-title");
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
// 해시·UUID 컬럼 제외(설정 hash_exclude, 서버 /api/diag/hashed). 테이블마다 한 번 묻고 기억한다. restored는 [다시 넣기]로 되살린 이름
const HASHED = {};   // {schema|base: {on, columns, restored: []}}
async function hashedOf(schema, base) {
  const key = `${schema}|${base}`;
  if (!HASHED[key]) HASHED[key] = { ...(await api("/api/diag/hashed", { schema, base })), restored: [] };
  return HASHED[key];
}
function hashedOut(names, h) {   // web/lib/hashed.ts withoutHashed와 같은 규칙
  if (!h?.on) return { names, hidden: [] };
  const hide = new Set(h.columns.filter(n => !h.restored.includes(n)));
  return { names: names.filter(n => !hide.has(n)), hidden: names.filter(n => hide.has(n)) };
}
function hashedNote(box, h, hidden, redraw) {   // 안내 + [다시 넣기]
  box.replaceChildren(...(hidden.length ? [el("span", { className: "hint", textContent: `ID·해시로 보이는 컬럼 ${hidden.join(", ")}을(를) 목록에서 뺐어요. ` }),
    el("button", { className: "ghost", textContent: "다시 넣기", onclick: () => { h.restored.push(...hidden); redraw(); } })] : []));
}
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
// 큰 정수는 서버가 글자("12345678901234567890")로 보내므로 숫자처럼 오른쪽에 맞춘다. 자릿수 쉼표로 소수나 큰 지수가 잘려 보이면 전체 값을 title로.
const bigInt = v => typeof v === "string" && /^-?\d{16,}$/.test(v);
const sameSource = (saved, now) => saved === now || (typeof saved === "string" && typeof now === "string" && ((!saved.includes("#") && now.split("#")[0] === saved) || (saved.endsWith("://:/") && now.startsWith(saved + "?"))));  // 연결 열쇠 비교(서버 use_counts.same_source와 같은 규칙: 폴더만 있는 예전 열쇠는 그 폴더의 어느 파일 연결과도, query 없던 Databricks·Athena 예전 열쇠는 같은 종류의 지금 열쇠와 같다)
const numCol = (rows, c) => rows.some(r => typeof r[c] === "number" || bigInt(r[c]));  // 숫자(큰 정수 포함) 컬럼이면 머리글도 오른쪽에
// 값이 표에 보이는 글자와 전체 글자(잘렸을 때만, 마우스를 올리면 보인다). 숫자는 자릿수 쉼표를 넣고, 문자열은 최대 글자 수(#maxlen, 없는 화면은 40)에서 자른다.
// max를 주면 그 값을 쓴다(표 하나를 그리는 동안 칸마다 #maxlen을 읽지 않게 한 번만 읽어 넘긴다).
// 중첩 값의 전체 글자는 들여쓴 JSON이다.
const maxLen = () => +($("maxlen")?.value ?? 40);
const numFormat = new Intl.NumberFormat();  // v.toLocaleString()과 같은 결과를 내되 한 번 만든 서식을 계속 쓴다(1만 행 찾기에서 칸마다 서식을 만들면 느리다)
function shown(v, max = maxLen()) {
  if (typeof v === "number") { const t = numFormat.format(v); return [t, t.replace(/,/g, "") === String(v) ? "" : String(v)]; }
  const s = isNested(v) ? inline(v) : String(v ?? "");
  const full = isNested(v) ? JSON.stringify(v, null, 2) : s;
  return max > 0 && s.length > max ? [s.slice(0, max) + "…", full] : [s, isNested(v) && full !== s ? full : ""];
}
// 글자를 HTML로 바꾸되 want(소문자)와 맞는 첫 부분은 <mark>로 강조한다.
const lit = (text, want) => {
  const low = text.toLowerCase(), at = want && typeof want === "string" && low.length === text.length ? low.indexOf(want) : -1;  // 소문자로 바꾸면 길이가 달라지는 글자(İ)는 위치가 어긋나므로 강조하지 않는다
  return at < 0 ? esc(text) : esc(text.slice(0, at)) + `<mark>${esc(text.slice(at, at + want.length))}</mark>` + esc(text.slice(at + want.length));
};
// 표 칸. 중첩 값 칸(.nest)은 누르면 들여쓴 전체를 칸 안에 펼치고, 다시 누르면 접는다. want가 있으면 맞는 글자를 강조한다.
function cell(v, want = "", max) {
  const [text, full] = shown(v, max), title = full ? ` title="${esc(full)}"` : "", t = lit(text, want);
  if (isNested(v)) return `<td class="nest" tabindex="0"${title} data-full="${esc(full || text)}">${t}</td>`;
  return typeof v === "number" ? `<td class="num"${title}>${t}</td>` : bigInt(v) ? `<td class="num">${t}</td>` : `<td${title}>${t}</td>`;
}
// 큰 표(TABLE_MIN행 넘음)는 보이는 행과 위아래 TABLE_OVER행만 tr로 만들고, 나머지는 높이만 같은 빈 줄(.vpad)로 채워
// 스크롤 막대와 위치가 전체 행 기준으로 맞게 한다. 칸은 한 줄(white-space:nowrap, height:40px)이라 줄 높이가 같고,
// 펼친 중첩 값 줄만 더 크다: 그 늘어난 높이는 g.extra(줄 번호 -> 늘어난 px)에 적어 둔다.
// 칸 너비는 보이는 줄에 따라 흔들리지 않게, 컬럼마다 가장 긴 글자의 줄을 안 보이는 크기 재기 줄(.vsize)로 늘 함께 그린다.
const TABLE_MIN = 200, TABLE_OVER = 20, GRIDS = new Set();  // 표 컬럼 너비 최소·여유와 그려 둔 표 목록
const gridAt = (g, k) => g.view ? g.view[k] : k;                  // 보이는 목록의 k번째 줄이 가리키는 원래 행 번호
const gridN = g => g.view ? g.view.length : g.rows.length;
const gridTop = (g, k) => { let y = k * g.H; for (const [p, e] of g.extra) if (p < k) y += e; return y; };  // k번째 줄의 위 끝(px)
const gridPos = (g, y) => { let k = Math.max(0, Math.floor(y / g.H)); while (k > 0 && gridTop(g, k) > y) k--; return k; };  // y(px)에 놓인 줄
const gridPad = (g, h) => h > 0 ? `<tr class="vpad" aria-hidden="true" style="height:${h}px"><td colspan="${g.columns.length + (g.selectable ? 1 : 0)}" style="height:${h}px;padding:0;border:0"></td></tr>` : "";
function gridTrs(g, a, b, max) {
  let s = "";
  for (let k = a; k < b; k++) {
    const i = gridAt(g, k), on = g.selected?.has(i);  // 고르기 칸(selectable): 고른 행은 원래 행 번호로 기억해 다시 그려도(가상 스크롤·찾기) 체크가 남는다
    s += `<tr${g.virtual ? ` data-p="${k}" aria-rowindex="${i + 2}"` : ""}${on ? ' class="picked"' : ""}>${g.selectable ? `<td class="pick"><label class="check"><input type="checkbox" data-i="${i}"${on ? " checked" : ""} aria-label="${i + 1}번째 행 고르기"></label></td>` : ""}${g.row(g.rows[i], g.want, max)}</tr>`;
  }
  return s;
}
// 컬럼마다 가장 긴 글자가 든 행(글자 폭 기준, ASCII 밖 글자는 2)을 모아 크기 재기 줄로 만든다.
const textWidth = s => { let n = 0; for (const ch of s) n += ch.codePointAt(0) > 127 ? 2 : 1; return n; };
function gridSizer(g, max) {
  const cap = max > 0 ? 2 * (max + 1) : Infinity, best = new Set();
  for (const c of g.columns) {
    let at = 0, most = -1;
    for (let i = 0; i < g.rows.length && most < cap; i++) {
      const v = g.rows[i][c], n = typeof v === "string" ? textWidth(max > 0 ? v.slice(0, max + 1) : v) : typeof v === "number" ? String(v).length * 1.3 : isNested(v) ? textWidth(max > 0 ? inline(v).slice(0, max + 1) : inline(v)) : 0;
      if (n > most) { most = n; at = i; }
    }
    best.add(at);
  }
  return [...best].map(i => `<tr class="vsize" aria-hidden="true">${g.selectable ? '<td class="pick"></td>' : ""}${g.row(g.rows[i], "", max)}</tr>`).join("");
}
function gridScroller(host) {
  for (let p = host; p && p !== document.documentElement; p = p.parentElement) if (/auto|scroll/.test(getComputedStyle(p).overflowY)) return p;
  return null;
}
function expandCell(td) { td.dataset.short = td.textContent; td.replaceChildren(el("pre", { className: "nest-full", textContent: td.dataset.full })); }
function collapseCell(td) { td.textContent = td.dataset.short; delete td.dataset.short; }
// 보이는 줄을 다시 그린다. force가 없으면 지금 그린 범위가 보이는 줄을 덮고 있는 한 그대로 둔다.
function gridDraw(g, force) {
  force ||= g.stale; g.stale = false;  // 가려진 채 바뀐 것은 보일 때 강제로 그린다
  const N = gridN(g), max = maxLen(), body = g.body ??= g.host.querySelector("tbody");
  let a = 0, b = N;
  if (g.virtual) {
    if (g.a >= 0 && g.host.getClientRects?.().length === 0) { g.stale = true; return; }  // 가려진 탭은 보일 때(크기가 바뀔 때) 그린다
    const sc = gridScroller(g.host), vh = sc ? sc.clientHeight : innerHeight;
    const y0 = (sc ? sc.getBoundingClientRect().top : 0) - body.getBoundingClientRect().top;
    const lo = Math.min(N, gridPos(g, y0)), hi = Math.min(N, gridPos(g, y0 + vh) + 1);
    if (!force && (g.a === 0 || lo - 5 >= g.a) && (g.b === N || hi + 5 <= g.b)) return;
    a = Math.max(0, lo - TABLE_OVER); b = Math.min(N, hi + TABLE_OVER);
  }
  g.a = a; g.b = b;
  const f = document.activeElement, at = f?.matches?.("td.nest") && body.contains(f) && f.closest("tr[data-p]"), ci = at && f.cellIndex;  // 다시 그려도 키보드 초점(펼칠 칸)을 잃지 않게
  body.innerHTML = g.virtual ? (g.sizerMax === max ? g.sizer : g.sizer = gridSizer(g, g.sizerMax = max)) + gridPad(g, gridTop(g, a)) + gridTrs(g, a, b, max) + gridPad(g, gridTop(g, N) - gridTop(g, b)) : gridTrs(g, 0, N, max);
  if (at) body.querySelector(`tr[data-p="${at.dataset.p}"]`)?.cells[ci]?.focus({ preventScroll: true });
  const trs = g.virtual || g.open.size ? [...body.querySelectorAll(g.virtual ? "tr[data-p]" : "tr")] : [];
  if (g.virtual && !g.measured && trs.length > 1) {  // 줄 간격은 처음 그린 줄들의 위치 차이로 잰다
    const H = (trs.at(-1).offsetTop - trs[0].offsetTop) / (trs.length - 1);
    if (H > 0) { g.measured = true; if (Math.abs(H - g.H) > 0.5) { g.H = H; return gridDraw(g, true); } }
  }
  if (g.open.size) trs.forEach((tr, k) => {  // 펼쳐 둔 중첩 값은 다시 그려도 펼친 채로
    const p = g.virtual ? +tr.dataset.p : k, s = g.open.get(gridAt(g, p));
    if (!s) return;
    s.forEach(c => tr.cells[c] && tr.cells[c].dataset.short === undefined && expandCell(tr.cells[c]));
    if (g.virtual) g.extra.set(p, tr.offsetHeight - g.H);
  });
}
// 결과 안에서 찾기: 칸에 보이는 글자(숫자는 쉼표 포함)에 want가 든 행만 남기고 맞는 글자를 강조한다. 안 보이는 행도 데이터에서 찾는다.
function gridFind(g, want) {
  want = want.trim().toLowerCase();
  const max = maxLen();
  g.want = want; g.extra.clear(); g.pickAt = -1;  // Shift 범위의 기준점은 보이는 줄 위치라, 보이는 줄이 바뀌면 버린다
  g.view = want ? g.rows.flatMap((r, i) => g.columns.some(c => (g.text?.(r, c, max) ?? shown(r[c], max)[0]).toLowerCase().includes(want)) ? [i] : []) : null;  // 칸마다 따로 본다(칸 사이를 건너 일치하지 않게)
  gridDraw(g, true);
}
// 표 하나를 host에 그린다. spec = { columns, rows, head: <th>들의 HTML, row(r, want, max): <td>들의 HTML, text?(r, c, max): 찾을 글(없으면 칸에 보이는 글),
//   selectable?: 첫 열에 행 고르기 칸(g.selected = 원래 행 번호 Set, 바뀌면 host에서 rowselect 이벤트), addCol?(): 머리글 끝 [+] 칸을 누르면 부른다 }.
// 행이 TABLE_MIN 이하면 예전처럼 전부 그린 HTML 그대로, 넘으면 보이는 행만 그리고 aria-rowcount/rowindex로 전체 행 수를 알린다.
// selectable이면 칸 번호(cellIndex)가 하나 밀린다: 칸 → 컬럼은 g.columns[cellIndex - 1].
function mountTable(host, spec) {
  if (!spec.rows.length) { host._grid = null; host.innerHTML = '<span class="empty">결과가 없어요.</span>'; return; }
  const g = host._grid = { ...spec, host, view: null, want: "", open: new Map(), extra: new Map(), H: 40, a: -1, b: -1, virtual: spec.rows.length > TABLE_MIN, selected: new Set(), pickAt: -1 };
  const head = (spec.selectable ? '<th class="pick" aria-label="행 고르기"></th>' : "") + spec.head + (spec.addCol ? '<th class="addcol"><button type="button" class="ghost" title="새 컬럼 만들기" aria-label="새 컬럼 만들기">+</button></th>' : "");
  if (!g.virtual) { host.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${gridTrs(g, 0, spec.rows.length, maxLen())}</tbody></table>`; return; }
  host.innerHTML = `<table aria-rowcount="${spec.rows.length + 1}"><thead><tr aria-rowindex="1">${head}</tr></thead><tbody></tbody></table>`;
  GRIDS.add(g);
  gridDraw(g, true);
  gridWatch?.observe(host);
}
let gridPending = false;
const gridSchedule = () => { if (!gridPending) { gridPending = true; (globalThis.requestAnimationFrame ?? (f => setTimeout(f, 16)))(gridRedraw); } };  // 스크롤마다가 아니라 화면 갱신마다 한 번
function gridRedraw() {
  gridPending = false;
  for (const g of GRIDS) {
    if (g.host._grid === g && g.host.isConnected && g.body?.isConnected !== false) { gridDraw(g); continue; }  // host를 비웠거나(떨어진 tbody) 화면에서 빠진 표는 버린다
    GRIDS.delete(g);
    if (![...GRIDS].some(x => x.host === g.host)) gridWatch?.unobserve(g.host);  // 같은 host에 새로 그린 표가 있으면 계속 지켜본다
  }
}
const gridWatch = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(gridSchedule);  // 가려진 탭이 보이게 되면 다시 그린다
document.addEventListener("scroll", gridSchedule, { capture: true, passive: true });
globalThis.addEventListener?.("resize", gridSchedule);
// 결과 표 {columns, rows}: 숫자 컬럼은 머리글도 오른쪽에. SQL 실행과 문장 보기의 결과가 함께 쓴다.
// sort를 주면(null 포함) 머리글을 눌러 정렬할 수 있게 data-sort·tabindex·화살표를 넣는다. 주지 않으면(undefined) 서버가 쪽을 나누는 표라 그대로.
const resultTable = (host, { columns, rows }, sort) => mountTable(host, { columns, rows,
  head: columns.map(c => {
    const mark = sortMark(sort, c);
    return `<th class="${numCol(rows, c) ? "num" : ""}"${sort === undefined ? "" : ` data-sort="${esc(c)}" tabindex="0"${mark.aria}`}>${esc(c)}${mark.arrow}</th>`;
  }).join(""),
  row: (r, want, max) => columns.map(c => cell(r[c], want, max)).join("") });
// 표 머리글 정렬(화면에서 받아 둔 결과를 정렬): 오름 → 내림 → 원래 순서
const SORT_COLLATOR = new Intl.Collator("ko", { numeric: true });  // numeric: 글자로 온 숫자("10")가 "9" 뒤에 오게
const SORT_DATE = /^\d{4}-\d\d-\d\d([ T]\d\d:\d\d(:\d\d(\.\d+)?)?)?$/;
const nextSort = (sort, column) => sort?.column !== column ? { column, dir: "asc" } : sort.dir === "asc" ? { column, dir: "desc" } : null;
const sortMark = (sort, column) => sort?.column !== column ? { arrow: "", aria: "" }
  : sort.dir === "asc" ? { arrow: " ↑", aria: ' aria-sort="ascending"' } : { arrow: " ↓", aria: ' aria-sort="descending"' };
const sortNote = (shown, total) => total === null ? `일부 행만 정렬했어요(상한 때문에 받은 ${shown.toLocaleString()}개만).`
  : `일부 행만 정렬했어요(전체 ${total.toLocaleString()}개 중 받은 ${shown.toLocaleString()}개).`;
function sortCompare(a, b) {  // 큰 정수 글자가 섞이면 BigInt로, 소수가 있으면 Number로 비교한다
  if (typeof a === "number" && typeof b === "number") return a - b;
  const x = typeof a === "number" && !Number.isInteger(a) ? null : BigInt(a), y = typeof b === "number" && !Number.isInteger(b) ? null : BigInt(b);
  if (x === null || y === null) return Number(a) - Number(b);
  return x < y ? -1 : x > y ? 1 : 0;
}
// 행 객체 배열을 column 값으로 정렬한 새 배열. 빈 값(null·undefined)은 방향과 상관없이 뒤에, pinned(r, i)인 행은 맨 끝에 원래 차례로. 같은 값은 원래 차례.
function sortRows(rows, column, dir, pinned = () => false) {
  const idx = rows.map((_, i) => i), fixed = idx.filter(i => pinned(rows[i], i)), rest = idx.filter(i => !fixed.includes(i));
  const empty = rest.filter(i => rows[i][column] == null), live = rest.filter(i => rows[i][column] != null), vals = live.map(i => rows[i][column]);
  const kind = vals.every(v => typeof v === "number" || bigInt(v)) ? "num" : vals.every(v => typeof v === "string" && SORT_DATE.test(v)) ? "date" : "text";
  const key = v => kind === "date" ? v.replace("T", " ") : typeof v === "object" ? JSON.stringify(v) : String(v);
  const cmp = (a, b) => kind === "num" ? sortCompare(a, b) : kind === "date" ? (a < b ? -1 : a > b ? 1 : 0) : SORT_COLLATOR.compare(a, b);
  const sign = dir === "desc" ? -1 : 1;
  live.sort((i, j) => sign * cmp(kind === "num" ? rows[i][column] : key(rows[i][column]), kind === "num" ? rows[j][column] : key(rows[j][column])) || i - j);
  return [...live, ...empty, ...fixed].map(i => rows[i]);
}
// 받아 둔 표 data = {columns, rows}를 host에 그리고 머리글을 누르면 화면에서 정렬해 다시 그린다. draw(host, 보일 data, sort)가 그리는 함수.
// opts: pinned(r, i)(정렬하지 않는 맨 끝 행), cut({total}: 상한으로 잘린 표면 정렬 안내), slice(rows)(정렬한 뒤 보일 행만 자르기). host._shown은 자르기 전 화면 차례(내보내기용).
function sortedTable(host, data, draw, opts = {}) {
  if (host._sort && !data.columns.includes(host._sort.column)) host._sort = null;
  host._sort ??= null;
  host._sortDraw = () => {
    const rows = host._sort ? sortRows(data.rows, host._sort.column, host._sort.dir, opts.pinned) : data.rows;
    host._shown = { ...data, rows };
    draw(host, { ...data, rows: opts.slice ? opts.slice(rows) : rows }, host._sort);
    if (host._sort && opts.cut) host.insertAdjacentHTML("beforeend", `<div class="hint sort-note">${esc(sortNote(data.rows.filter((r, i) => !opts.pinned?.(r, i)).length, opts.cut.total))}</div>`);
  };
  host._sortDraw();
}
function sortClick(e) {  // 머리글(th[data-sort])을 누르거나 Enter·Space
  if (e.type === "keydown" && (!["Enter", " "].includes(e.key) || e.ctrlKey || e.metaKey || e.altKey)) return;
  const th = e.target.closest?.("th[data-sort]");
  let host = th?.parentElement;
  while (host && !host._sortDraw) host = host.parentElement;
  if (!host) return;
  e.preventDefault();
  host._sort = nextSort(host._sort, th.dataset.sort);
  host._sortDraw();
}
// 고르기 칸: 보이는 k번째 줄을 고르거나(on) 뺀다. shift면 마지막으로 누른 줄부터 k까지 같이. 다시 그리고 rowselect를 낸다.
function gridPick(g, k, on, shift) {
  const [a, b] = shift && g.pickAt >= 0 ? [Math.min(g.pickAt, k), Math.max(g.pickAt, k)] : [k, k];
  for (let p = a; p <= b; p++) on ? g.selected.add(gridAt(g, p)) : g.selected.delete(gridAt(g, p));
  g.pickAt = k;
  gridDraw(g, true);
  g.host.dispatchEvent(new CustomEvent("rowselect", { bubbles: true, detail: g }));
}
const toggleNest = e => {
  const td = e.target.closest("td.nest");
  if (!td) return;
  const open = td.dataset.short === undefined;
  open ? expandCell(td) : collapseCell(td);
  const g = td.closest("table")?.parentNode?._grid;  // 큰 표는 어느 칸을 펼쳤는지 기억해 다시 그려도 펼친 채로 둔다
  if (!g) return;
  const tr = td.parentNode, p = g.virtual ? +tr.dataset.p : tr.sectionRowIndex, i = gridAt(g, p), s = g.open.get(i) ?? new Set();
  open ? s.add(td.cellIndex) : s.delete(td.cellIndex);
  s.size ? g.open.set(i, s) : g.open.delete(i);
  if (g.virtual) s.size ? g.extra.set(p, tr.offsetHeight - g.H) : g.extra.delete(p);
};
document.addEventListener("click", toggleNest);
document.addEventListener("click", sortClick);
document.addEventListener("keydown", sortClick);
document.addEventListener("click", e => {  // 표의 고르기 칸(누르면 그 행, Shift면 범위)과 머리글 끝 [+](addCol)
  const box = e.target.closest?.("td.pick input"), add = e.target.closest?.("th.addcol button"), g = (box ?? add)?.closest("table")?.parentNode?._grid;
  if (!g) return;
  if (add) { g.addCol?.(); return; }
  const tr = box.closest("tr");
  gridPick(g, g.virtual ? +tr.dataset.p : tr.sectionRowIndex, box.checked, e.shiftKey);
  g.host.querySelector(`td.pick input[data-i="${box.dataset.i}"]`)?.focus({ preventScroll: true });  // 다시 그려도 키보드 초점을 잃지 않게
});
document.addEventListener("keydown", e => { if ((e.key === "Enter" || e.key === " ") && !e.ctrlKey && !e.metaKey && !e.altKey && e.target.matches?.("td.nest")) { e.preventDefault(); toggleNest(e); } });  // 키보드로도 펼치고 접는다
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
  if (document.hidden || $("usage").hidden) return;  // 접혀 있거나 숨은 창에서는 묻지 않는다(2초마다 서버를 깨우지 않게)
  try {
    const d = await api("/api/usage");
    $("usage").innerHTML = `<span>메모리<b>${d.pc_memory}%</b></span><span>CPU<b>${d.pc_cpu}%</b></span><span>앱<b>${d.memory_mb.toLocaleString()}MB</b></span>`;
    $("usage").title = `PC 전체의 메모리와 CPU 사용률, 이 앱이 쓰는 메모리예요. 이 앱만 보면 메모리 ${d.memory}%, CPU ${d.cpu}%를 써요. 데이터베이스에 연결하면 계산은 데이터베이스 서버가 해요.`;
  } catch (e) {}
}
showUsage();
// 메모리·CPU·앱 MB는 기본으로 접어 두고 "상태" 알약을 누르면 펼친다(펼침은 이 브라우저에 기억).
const usageOpen = () => { try { return localStorage.getItem("loupe.usageOpen") === "1"; } catch (e) { return false; } };
function foldUsage(open) {
  $("usage").hidden = !open;
  $("usage-pill").setAttribute("aria-expanded", String(open));
  $("usage-pill").classList.toggle("on", open);
  try { localStorage.setItem("loupe.usageOpen", open ? "1" : "0"); } catch (e) {}
  if (open) showUsage();
}
$("usage").before(el("button", { id: "usage-pill", className: "pill", textContent: "상태", title: "메모리와 CPU 사용률을 보거나 숨겨요" }));
$("usage-pill").onclick = () => foldUsage($("usage").hidden);
foldUsage(usageOpen());
setInterval(showUsage, 5000);

// 전체 시간: [검색]을 누르면 0에서 다시 시작하고, 그 뒤의 집계·차트·저장 시간을 더해 간다.
// 위쪽 줄의 작업 알약에 도는 동안은 "○○ 중 · 예상 · 걸린 시간"과 [취소], 끝나면 "완료 · 걸린 시간"이 나온다.
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
  $("job-name").textContent = going.size ? `${JOBS[last.kind] ?? "작업"} 중` : "완료";
  $("timing").textContent = [total.guess && `예상 ${secs(total.guess)}`, `걸린 시간 ${secs(ms)}`].filter(Boolean).join(", ");
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
    if (d.done) sendTo(name, { title: `${subject(JOBS[kind])} 끝났어요`, text, ...(rows ? { rows } : {}), seconds: +seconds.toFixed(1) }).catch(e => connMsg(e.message));
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
    return same ? (state?.set ? "환경변수를 찾았어요." : "이 환경변수가 아직 없어요.") : "";
  }
  input.type = "password";
  input.placeholder = !same ? (keep === "session" ? "앱을 끄면 지워져요" : "") : state ? "저장돼 있어요. 바꾸려면 새로 입력하세요" : keep === "session" ? "앱을 다시 켜서 새로 입력해야 해요" : "다시 입력해 주세요";
  return "";
}
// 고른 종류의 칸을 서버의 표대로 그린다. 저장된 것이면 비밀이 아닌 칸은 값을, 비밀 칸은 보관 방식에 맞는 안내를 채운다.
function showFields() {
  const kind = $("dest-kind").value, spec = dest.kinds[kind], now = dest.destinations[$("dest-list").value];
  const saved = now?.kind === kind ? now : null, presets = spec.presets ?? [];
  $("dest-preset").replaceChildren(new Option("미리 만든 틀로 채우기", ""), ...presets.map((p, i) => new Option(p.label, i)));
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
  $("dest-keep").replaceChildren(...Object.entries(d.keeps).map(([k, v]) => new Option(v, k)));
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
// 설치한 기능만 보인다: 켜지지 않은 기능의 메뉴·선택지(data-feature)는 숨기고, 그 기능의 화면에 직접 들어오면 위에 안내 줄을 띄운다.
function applyFeatures() {
  const on = SERVER.features.on;
  document.querySelectorAll("[data-feature]").forEach(e => e.classList.toggle("hidden", !on.includes(e.dataset.feature)));
  const here = SERVER.features.all.find(f => f.path === location.pathname);
  if (here && !on.includes(here.id)) document.querySelector(".topbar").after(el("div", { className: "nofeature", role: "alert", textContent: "이 기능은 설치하지 않았어요. [설정]에서 추가할 수 있어요." }));
}
applyFeatures();
// 설정 창: 기능을 고르고 [적용]하면 서버가 설치 프로그램을 받아 실행하고, 앱은 닫혔다가 설치가 끝나면 다시 열린다(개발 실행에서는 서버가 안내만 한다).
$("feat-list").replaceChildren(...SERVER.features.all.map(f => el("label", { style: "display:block; margin:6px 0" },
  el("input", { type: "checkbox", value: f.id, checked: SERVER.features.on.includes(f.id), style: "width:auto" }), ` ${f.name}`)));
const SET_TABS = ["feat", "update", "calc", "memory", "sample", "date", "viz", "dest"];  // 설정 창(status.html)의 분류(data-set) 차례
/** [SPECIFICATION] plans/phase-16.md#openSettings
 *  설정 창을 열고 tab 분류만 보이게 한다(보낼 곳이면 Slack 봇 상태도 새로 읽는다). */
function openSettings(tab) {
  if (!SET_TABS.includes(tab)) throw new Error(`없는 설정 분류: ${tab}`);
  $("feat-panel").classList.remove("hidden");
  document.querySelectorAll("#feat-panel .set-nav [data-set]").forEach(t => t.setAttribute("aria-selected", String(t.dataset.set === tab)));
  document.querySelectorAll("#feat-panel .set-page").forEach(p => p.classList.toggle("hidden", p.dataset.set !== tab));
  if (tab === "dest") api("/api/bot").then(showBot).catch(() => {});
  if (tab === "memory") api("/api/memory").then(showMemory).catch(e => say("mem-msg", e.message));
}
// 설정 [메모리]: 앱이 붙잡고 있는 것(서버 core/memory.py에 등록된 것)을 체크 목록으로. 올라온 라이브러리는 내릴 수 없어 이름만 알린다.
function showMemory(d) {
  $("mem-now").textContent = `${d.mb.toLocaleString()}MB`;
  $("mem-list").replaceChildren(...(d.items.length ? d.items.map(i => el("label", { className: "check", style: "display:block; margin:8px 0" },
    el("input", { type: "checkbox", value: i.id }), ` ${i.label}`, el("div", { className: "hint", style: "margin-left:22px", textContent: i.detail })))
    : [el("div", { className: "empty", textContent: "지금 붙잡고 있는 데이터가 없어요." })]));
  $("mem-all").disabled = $("mem-drop").disabled = !d.items.length;
  $("mem-mods").textContent = d.modules.length ? `올라온 계산 라이브러리: ${d.modules.join(", ")}. 이것은 앱이 켜져 있는 동안 내릴 수 없어요. 앱을 껐다 켜면 내려가요.` : "";
}
$("mem-all").onclick = () => $("mem-list").querySelectorAll("input").forEach(i => i.checked = true);
$("mem-drop").onclick = () => busy($("mem-drop"), async () => {
  const ids = [...$("mem-list").querySelectorAll("input:checked")].map(i => i.value);
  if (!ids.length) return say("mem-msg", "비울 것을 골라 주세요.");
  try {
    const d = await api("/api/memory/drop", { ids });
    showMemory(d);
    say("mem-msg", `비웠어요. 앱 메모리 ${d.before.toLocaleString()}MB → ${d.mb.toLocaleString()}MB`, "hint");
    if (ids.includes("connection")) location.reload();  // 연결을 놓았으면 화면도 연결 전 상태로
  } catch (e) { say("mem-msg", e.message); }
});
$("feat-open").onclick = () => openSettings("feat");
document.querySelectorAll("#feat-panel .set-nav [data-set]").forEach(b => b.onclick = () => openSettings(b.dataset.set));
$("feat-close").onclick = () => $("feat-panel").classList.add("hidden");
$("feat-panel").onclick = e => { if (e.target === $("feat-panel")) $("feat-panel").classList.add("hidden"); };
$("feat-apply").onclick = () => busy($("feat-apply"), async () => {
  say("feat-msg", "설치 파일을 내려받고 있어요. 잠시 기다려 주세요.", "hint");
  try { say("feat-msg", (await api("/api/features/install", { features: [...$("feat-list").querySelectorAll("input:checked")].map(i => i.value) })).text, true); }
  catch (e) { say("feat-msg", e.message); }
});
// 릴리스 노트(CHANGELOG의 한 절) → 요소들: "### 제목"은 묶음 머리, "- 줄"은 목록("이름: 설명"이면 이름을 굵게), "없음"뿐인 묶음은 뺀다.
// 글자로만 넣는다(노트의 HTML은 실행하지 않는다).
function updNotes(text) {
  const out = [];
  let list = null;
  for (const line of text.split(/\r?\n/).map(l => l.trim()).filter(l => l && l !== "없음")) {
    const head = line.match(/^#{2,3} (.+)/), item = line.match(/^- (.+)/);
    if (head) { list = el("ul"); out.push(el("section", {}, el("h4", { textContent: head[1] }), list)); continue; }
    if (!item) { out.push(el("p", { textContent: line })); continue; }
    const cut = item[1].indexOf(": "), li = cut > 0 ? el("li", {}, el("b", { textContent: item[1].slice(0, cut) }), item[1].slice(cut + 1)) : el("li", { textContent: item[1] });
    list ? list.append(li) : out.push(el("ul", {}, li));
  }
  return out.filter(e => e.tagName !== "SECTION" || e.lastChild.children.length);
}
// 새 버전 알림: 앱을 열 때 한 번 묻고(서버가 실행마다 한 번만 GitHub에 묻는다), 있으면 위쪽 줄에 [업데이트]. 누르면 지금 고른 기능 그대로 새 버전을 설치한다.
$("app-version").textContent = SERVER.features.version ? `v${SERVER.features.version}` : "개발 실행(설치 파일이 아님)";  // features.ini의 버전(설치 프로그램이 씀)
api("/api/update").then(u => {
  $("upd-check").checked = u.check;
  if (!u.newer) return;
  $("upd-text").textContent = `v${u.latest} 새 버전이 나왔어요`;
  $("upd").classList.remove("hidden");
  $("upd-title").textContent = `v${u.latest}에서 바뀐 점`;
  $("upd-now").textContent = `지금 v${u.current}`;
  $("upd-notes").replaceChildren(...updNotes(u.notes || "릴리스 노트를 받지 못했어요. GitHub Releases에서 볼 수 있어요."));
  $("upd-open").onclick = () => $("upd-panel").classList.remove("hidden");
  $("upd-close").onclick = () => $("upd-panel").classList.add("hidden");
  $("upd-panel").onclick = e => { if (e.target === $("upd-panel")) $("upd-panel").classList.add("hidden"); };
  $("upd-go").onclick = () => busy($("upd-go"), async () => {
    say("upd-msg", `v${u.latest} 설치 파일을 내려받고 있어요. 잠시 기다려 주세요.`, "hint");
    try { say("upd-msg", (await api("/api/features/install", { features: SERVER.features.on, version: u.latest })).text, true); }
    catch (e) { say("upd-msg", e.message); }
  });
}).catch(() => {});
$("upd-check").onchange = () => saveSetting({ update_check: $("upd-check").checked ? "on" : "off" });
const [AFTER_LOW, AFTER_HIGH] = SERVER.sizes.notify_after;
Object.assign($("notify-after"), { value: notifyAfter, min: AFTER_LOW, max: AFTER_HIGH });
$("notify-after").onchange = () => {
  const n = Number($("notify-after").value), ok = Number.isInteger(n) && n >= AFTER_LOW && n <= AFTER_HIGH;
  destMsg(ok ? "" : `${AFTER_LOW}에서 ${AFTER_HIGH} 사이의 정수로 넣어 주세요.`);
  if (ok) { notifyAfter = n; saveSetting({ notify_after: n }); }
};
$("dest-save").onclick = () => busy($("dest-save"), async () => {
  const name = $("dest-name").value.trim();
  const destination = { kind: $("dest-kind").value, done: $("dest-done").checked, keep: $("dest-keep").value,
    ...Object.fromEntries([...$("dest-fields").querySelectorAll("[data-field]")].map(i => [i.dataset.field, i.value])) };  // 비밀 칸을 비우면 서버가 저장된 값을 그대로 둔다.
  try { showDestinations(await api("/api/destinations", { name, destination }), name); destMsg("저장했어요.", true); } catch (e) { destMsg(e.message); }
});
$("dest-del").onclick = () => busy($("dest-del"), async () => {
  if (!$("dest-list").value) return;
  try { showDestinations(await api("/api/destinations", { name: $("dest-list").value, destination: null }), ""); destMsg("삭제했어요.", true); } catch (e) { destMsg(e.message); }
});
$("dest-test").onclick = () => busy($("dest-test"), async () => {
  if (!$("dest-list").value) return destMsg("먼저 위 목록에서 저장한 보낼 곳을 고르세요.");
  try { await sendTo($("dest-list").value, { title: "시험 메시지", text: "Data Loupe에서 보낸 시험 메시지예요." }); destMsg("보냈어요. 받은 곳에서 확인해 보세요.", true); } catch (e) { destMsg(e.message); }
});
$("dest-check").onclick = () => busy($("dest-check"), async () => {
  if (!$("dest-list").value) return destMsg("먼저 위 목록에서 저장한 보낼 곳을 고르세요.");
  try { destMsg((await api("/api/destinations/check", { name: $("dest-list").value })).message, true); } catch (e) { destMsg(e.message); }
});
api("/api/destinations").then(showDestinations).catch(() => {});

// Slack 봇(받기): 설정 저장, 연동 확인, 켜기·끄기. 토큰 칸을 비우고 저장하면 같은 보관 방식으로 둔 값을 그대로 쓴다.
const botMsg = (text, ok) => say("msg-bot", text, ok);
// keep: 칸에 보일 보관 방식(고르는 칸을 바꾸면 그것, 아니면 저장한 것). 저장한 방식과 같을 때만 저장된 상태를 보인다.
function showBot(d, keep = d.keep) {
  $("bot-keep").replaceChildren(...Object.entries(d.keeps).map(([k, v]) => new Option(v, k)));
  $("bot-keep").value = keep;
  [["bot-token", d.bot_token], ["bot-app", d.app_token]].forEach(([id, state]) => { $(id).title = secretInput($(id), keep, d.saved ? { keep: d.keep } : null, state); });
  $("bot-users").value = d.users.join(", ");
  $("bot-channels").value = d.channels.join(", ");
  $("bot-state").textContent = d.running ? `켜짐${d.team ? ` (${d.team})` : ""}` : "꺼짐";
  $("bot-log").textContent = d.log.length ? ["최근 요청", ...d.log.map(r => `${r.at.slice(11)} ${r.user} ${r.allowed ? "" : "(거부됨) "}/ibis ${r.text}${r.error ? ` (실패: ${r.error})` : ""}`)].join("\n") : "";
}
$("bot-keep").onchange = () => api("/api/bot").then(d => showBot(d, $("bot-keep").value)).catch(e => botMsg(e.message));
$("bot-save").onclick = () => busy($("bot-save"), async () => {
  try {
    showBot(await api("/api/bot", { keep: $("bot-keep").value, bot_token: $("bot-token").value, app_token: $("bot-app").value, users: $("bot-users").value, channels: $("bot-channels").value }));
    $("bot-token").value = $("bot-keep").value === "env" ? $("bot-token").value : "";
    $("bot-app").value = $("bot-keep").value === "env" ? $("bot-app").value : "";
    botMsg("저장했어요.", true);
  } catch (e) { botMsg(e.message); }
});
[["bot-check", "/api/bot/check"], ["bot-start", "/api/bot/start"], ["bot-stop", "/api/bot/stop"]].forEach(([id, url]) => $(id).onclick = () => busy($(id), async () => {
  try {
    const d = await api(url, {});
    if (d.message) botMsg(d.message, true); else { showBot(d); botMsg(d.running ? "봇을 켰어요. Slack에서 /ibis 도움 을 보내 보세요." : "봇을 껐어요.", true); }
  } catch (e) { botMsg(e.message); }
}));
const PANEL_PARAM = "panel", PANELS = ["feat", "dest"];  // Next 대시보드 왼쪽 메뉴의 [설정](/?panel=feat)이나 다른 곳의 /?panel=… 로 설정 창의 그 분류를 연다
const askedPanel = new URLSearchParams(location.search).get(PANEL_PARAM); if (PANELS.includes(askedPanel)) openSettings(askedPanel);

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
  const label = c => recentName(c.info) + ` (${c.count}번 사용)`;
  $("recent-conn").replaceChildren(new Option("최근 연결에서 고르기", ""), ...recentConnections.map((c, i) => new Option(label(c), i)));
  $("recent-conn").classList.toggle("hidden", !recentConnections.length);
  // 첫 화면 오른쪽의 최근 연결 목록(home.html): 최근에 쓴 순으로, 날짜마다 머리글을 두고 줄마다 시각·이름·폴더·횟수를 보인다. 누르면 연결 칸을 펴고 그 연결로 칸을 채운다
  const byLast = recentConnections.map((c, i) => [c, i]).sort((a, b) => String(b[0].last ?? "").localeCompare(String(a[0].last ?? "")));
  const days = Map.groupBy(byLast, ([c]) => String(c.last ?? "").slice(0, 10));  // last는 이 PC 시각의 ISO 글자(없으면 빈 글자: 예전 기록)
  $("recent-list")?.replaceChildren(...[...days].flatMap(([day, list]) => [el("div", { className: "recent-day", textContent: recentDay(day) }),
    ...list.map(([c, i]) => el("button", { type: "button", className: "btn", title: label(c), onclick: () => { openConnect(); pickRecent(i); } },
      el("b", { textContent: recentName(c.info) }), el("span", { className: "hint", textContent: [String(c.last ?? "").slice(11, 16), c.info.kind === "folder" && recentName(c.info) !== c.info.path ? c.info.path : "", `${c.count}번 사용`].filter(Boolean).join(" · ") })))]));
  $("recent-none")?.classList.toggle("hidden", !!recentConnections.length);
}
// 연결을 알아볼 이름: 주소는 주소와 아이디, 폴더는 고른 파일 이름(RECENT_NAMES개까지, 나머지는 개수), 고른 파일이 없으면 폴더 경로
const RECENT_NAMES = 2;
function recentName(info) {
  if (info.kind === "url") return info.url + (info.user ? `, ${info.user}` : "");
  const files = (info.file ? [info.file] : info.files ?? []).map(f => String(f).split(/[\\/]/).pop());
  return files.length ? files.slice(0, RECENT_NAMES).join(", ") + (files.length > RECENT_NAMES ? ` 외 ${files.length - RECENT_NAMES}개` : "") : info.path;
}
// 날짜 머리글: 오늘·어제는 말로, 그 밖은 날짜 그대로, 시각을 모르면(예전 기록) "날짜 모름"
function recentDay(day) {
  const iso = back => { const d = new Date(); d.setDate(d.getDate() - back); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
  return !day ? "날짜 모름" : day === iso(0) ? "오늘" : day === iso(1) ? "어제" : day;
}
// 최근 연결 i번을 연결 칸에 채운다(비밀번호는 저장하지 않아 다시 넣는다). 고르는 칸과 첫 화면 목록이 같이 쓴다.
function pickRecent(i) {
  const c = recentConnections[i];
  if (!c) throw new Error(`없는 최근 연결: ${i}`);
  fillConnection(c.info);
  $(c.info.kind === "url" ? "password" : "find").focus();
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
      if (hasTables) openConnect("이 화면을 쓰려면 먼저 데이터를 연결하세요.");
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
  $("flat-count").textContent = `${flatBoxes().length}개 중 ${flatBoxes().filter(b => b.checked).length}개 고름` + (offList.size ? `, 목록에 안 보이는 ${offList.size}개도 고름` : "");
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
$("path").onkeydown = e => { if (e.key === "Enter" && !e.ctrlKey && !e.metaKey) $("find").click(); };
$("conn-close").onclick = closeConnect;
$("conn-panel").onclick = e => { if (e.target === $("conn-panel") && connClosable()) closeConnect(); };
$("crumb").classList.add("link");
$("crumb").title = "눌러서 연결을 바꿔요";
$("crumb").onclick = () => openConnect();
$("recent-conn").onchange = () => { if ($("recent-conn").value !== "") pickRecent($("recent-conn").value); $("recent-conn").value = ""; };
$("file").onchange = showFlat;
$("flat-list").onchange = showFlat;
$("flat-all").onclick = () => { flatBoxes().forEach(b => b.checked = true); showFlat(); };
}
const setMsg = (text, ok) => say("err-settings", text, ok);  // 앱 설정 저장 오류([설정] 창)
// 메모리 한도(MB, 0 = 제한 없음): 목록에 있는 값이거나 GB로 직접 넣은 값. 저장만 하고 다음 [연결]부터 서버가 쓴다.
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
  else setMsg(`메모리 한도는 ${low / 1000}GB에서 ${high / 1000}GB 사이로 넣어 주세요.`);
};
// 계산 코어 수(0 = 전부): 저장만 하고 다음 [연결]부터 서버가 쓴다. 고를 수 있는 값은 1, 2, 4, 8, …과 이 PC의 코어 수.
const cores = SERVER.sizes.threads[1], coreSteps = [...new Set([1, 2, 4, 8, 16, 32, 64].filter(n => n < cores).concat(cores))];
$("threads").innerHTML = `<option value="0">모두 (${cores}개)</option>` + coreSteps.filter(n => n < cores).map(n => `<option value="${n}">${n}개</option>`).join("");
$("threads").value = [...$("threads").options].some(o => o.value === String(saved("threads") || 0)) ? String(saved("threads") || 0) : "0";
$("threads").onchange = () => saveSetting({ threads: +$("threads").value });
// 자세히: 처리 용량을 알 수 없을 때(scan_unknown: run·block)와 표본이 너무 적게 오는 기준(sample_too_few, 화면은 %, 비우면 기본 20%).
$("scan-unknown").value = saved("scan_unknown") || "run";
$("scan-unknown").onchange = () => saveSetting({ scan_unknown: $("scan-unknown").value });
const [FEW_LOW, FEW_HIGH] = SERVER.floats.sample_too_few;
if (saved("sample_too_few")) $("too-few").value = Math.round(saved("sample_too_few") * 100);
$("too-few").onchange = () => {
  const text = $("too-few").value.trim(), n = Number(text) / 100;
  if (!text) { saveSetting({ sample_too_few: 0 }); setMsg(""); }
  else if (n >= FEW_LOW - 1e-9 && n <= FEW_HIGH + 1e-9) { saveSetting({ sample_too_few: n }); setMsg(""); }
  else setMsg(`최소 표본 비율은 ${Math.round(FEW_LOW * 100)}에서 ${Math.round(FEW_HIGH * 100)} 사이로 넣어 주세요. 비우면 20%예요.`);
};
// PC에서 구할 통계의 최대 행 수(pc_stat_rows, 비우면 서버 기본값). 범위·기본값은 서버(SERVER.sizes·SERVER.defaults)에서
const [PCR_LOW, PCR_HIGH] = SERVER.sizes.pc_stat_rows;
Object.assign($("pc-stat-rows"), { min: PCR_LOW, max: PCR_HIGH, placeholder: SERVER.defaults.pc_stat_rows.toLocaleString() });
if (saved("pc_stat_rows")) $("pc-stat-rows").value = saved("pc_stat_rows");
$("pc-stat-rows").onchange = () => {
  const text = $("pc-stat-rows").value.trim(), n = Number(text);
  if (!text) { saveSetting({ pc_stat_rows: 0 }); setMsg(""); }
  else if (Number.isInteger(n) && n >= PCR_LOW && n <= PCR_HIGH) { saveSetting({ pc_stat_rows: n }); setMsg(""); }
  else setMsg(`PC에서 구할 통계의 최대 행 수는 ${PCR_LOW.toLocaleString()}에서 ${PCR_HIGH.toLocaleString()} 사이의 정수로 넣어 주세요. 비우면 ${SERVER.defaults.pc_stat_rows.toLocaleString()}행이에요.`);
};
// 워드클라우드 글 속 단어로 받는 최대 행 수(cloud_rows, 비우면 서버 기본값). 범위·기본값은 서버(SERVER.sizes·SERVER.defaults)에서
const [CLR_LOW, CLR_HIGH] = SERVER.sizes.cloud_rows;
Object.assign($("cloud-rows"), { min: CLR_LOW, max: CLR_HIGH, placeholder: SERVER.defaults.cloud_rows.toLocaleString() });
if (saved("cloud_rows")) $("cloud-rows").value = saved("cloud_rows");
$("cloud-rows").onchange = () => {
  const text = $("cloud-rows").value.trim(), n = Number(text);
  if (!text) { saveSetting({ cloud_rows: 0 }); setMsg(""); }
  else if (Number.isInteger(n) && n >= CLR_LOW && n <= CLR_HIGH) { saveSetting({ cloud_rows: n }); setMsg(""); }
  else setMsg(`워드클라우드 단어 분석 최대 행 수는 ${CLR_LOW.toLocaleString()}에서 ${CLR_HIGH.toLocaleString()} 사이의 정수로 넣어 주세요. 비우면 ${SERVER.defaults.cloud_rows.toLocaleString()}행이에요.`);
};
// 표본 설정(sampling.config): [칸 id, 설정 이름, 화면 배율(서버 값 × 배율 = 화면 값)]. 범위·기본값은 SERVER.sizes·SERVER.floats·SERVER.defaults
const SAMPLE_FIELDS = [["sample-conf", "sample_conf", 1], ["sample-e-ratio", "sample_e_ratio", 100], ["sample-e-mean", "sample_e_mean", 100], ["pilot-rows", "pilot_rows", 1],
  ["strata-h", "strata_h", 1], ["strata-min", "strata_min", 1], ["hash-buckets", "hash_buckets", 1], ["key-over", "key_over", 1]];
for (const [id, name, scale] of SAMPLE_FIELDS) {
  const whole = name in SERVER.sizes, [lo, hi] = whole ? SERVER.sizes[name] : SERVER.floats[name];
  Object.assign($(id), { min: lo * scale, max: hi * scale, placeholder: (SERVER.defaults[name] * scale).toLocaleString() });
  if (saved(name)) $(id).value = +saved(name) * scale;
  $(id).onchange = () => {
    const text = $(id).value.trim(), n = Number(text) / scale;
    if (!text) { saveSetting({ [name]: 0 }); setMsg(""); }
    else if ((!whole || Number.isInteger(n)) && n >= lo - 1e-9 && n <= hi + 1e-9) { saveSetting({ [name]: n }); setMsg(""); }
    else setMsg(`${$(id).closest(".opt").querySelector("span").textContent}은 ${(lo * scale).toLocaleString()}에서 ${(hi * scale).toLocaleString()} 사이로 넣어 주세요. 비우면 ${(SERVER.defaults[name] * scale).toLocaleString()}이에요.`);
  };
}
// ID·해시 컬럼을 묶기·집계 목록에서 뺄지(hash_exclude, 기본 on)
$("hash-exclude").checked = (saved("hash_exclude") || SERVER.defaults.hash_exclude) === "on";
$("hash-exclude").onchange = () => saveSetting({ hash_exclude: $("hash-exclude").checked ? "on" : "off" });
// 문자 검색에서 특수문자 무시(ignore_marks, 기본 off). 서버(parse.like_one)가 설정을 읽는다
$("ignore-marks").checked = (saved("ignore_marks") || SERVER.defaults.ignore_marks) === "on";
$("ignore-marks").onchange = () => saveSetting({ ignore_marks: $("ignore-marks").checked ? "on" : "off" });
for (const [id, name] of [["pilot-first", "pilot_first"]]) {
  $(id).value = saved(name) || SERVER.defaults[name];
  $(id).onchange = () => saveSetting({ [name]: $(id).value });
}
// 날짜 설정(timezone·week_start·fiscal_start_month): 지표·차트의 날·주·월·연 경계. 시간대를 비우면 PC 시간대(설정에서 빼고, 서버가 예전 쿠키를 지운다).
$("timezone").value = saved("timezone") || "";
$("timezone").onchange = () => {
  const zone = $("timezone").value.trim();
  api("/api/settings", { timezone: zone || 0 }).then(() => setMsg(""), () => setMsg("시간대는 Asia/Seoul 같은 이름이나 +09:00 같은 오프셋으로 넣어 주세요."));
};
$("week-start").value = saved("week_start") || "mon";
$("week-start").onchange = () => saveSetting({ week_start: $("week-start").value });
$("fiscal-start").innerHTML = Array.from({ length: 12 }, (_, i) => `<option value="${i + 1}">${i + 1}월 1일</option>`).join("");
$("fiscal-start").value = saved("fiscal_start_month") || "1";
$("fiscal-start").onchange = () => saveSetting({ fiscal_start_month: +$("fiscal-start").value });
// 달력 설정(weekend·work_hours·bucket_minutes·ewma_span): 식 함수가 인자를 비우면 읽는다. 기본값(placeholder)은 서버(SERVER.calendar)에서. 틀린 값은 서버 문장을 그대로 보인다.
const CAL = SERVER.calendar, calSave = changes => api("/api/settings", changes).then(() => setMsg(""), e => setMsg(e.message));
const weekendBoxes = [...$("weekend").querySelectorAll("input")];
for (const b of weekendBoxes) b.checked = (saved("weekend") || CAL.weekend).split("_").includes(b.value);
$("weekend").onchange = () => calSave({ weekend: weekendBoxes.filter(b => b.checked).map(b => b.value).join("_") || 0 });
const [WORK_START, WORK_END] = (saved("work_hours") || CAL.work_hours.join("_")).split("_");
Object.assign($("work-start"), { value: saved("work_hours") ? WORK_START : "", placeholder: CAL.work_hours[0] });
Object.assign($("work-end"), { value: saved("work_hours") ? WORK_END : "", placeholder: CAL.work_hours[1] });
$("work-start").onchange = $("work-end").onchange = () => {
  const [start, end] = [$("work-start").value.trim(), $("work-end").value.trim()];
  calSave({ work_hours: start && end ? `${start}_${end}` : 0 });
};
for (const [id, name] of [["bucket-minutes", "bucket_minutes"], ["ewma-span", "ewma_span"]]) {
  Object.assign($(id), { value: saved(name) || "", placeholder: CAL[name] });
  $(id).onchange = () => calSave({ [name]: Number($(id).value.trim()) });  // 비우면 0 = 기본값
}
// [차트 낱말]·[차트 자동 규칙 기본값]: 상수(SERVER.dash.viz)를 받는 화면에서만 보인다. 저장이 틀리면 서버 문장을 칸에 보이고 저장된 값으로 다시 그린다.
const WORD_TITLES = { good: "긍정", bad: "부정", other: "기타", null: "결측" };
let editing = null;  // 눌러 고치는 중인 낱말 { key, word }
const saveViz = async changes => {
  try { await saveVizPrefs({ ...SERVER.dash.viz, ...changes }); say("err-viz", "", true); } catch (e) { say("err-viz", e.message); }
  editing = null;
  showViz();
};
function showViz() {
  const { words, query_auto } = SERVER.dash.viz;
  $("viz-words").replaceChildren(...Object.entries(WORD_TITLES).map(([key, title]) => {
    const input = el("input", { placeholder: "낱말 더하기", style: "width:140px; flex:none", value: editing?.key === key ? editing.word : "" });
    const add = () => {
      const word = input.value;
      const list = editing?.key === key ? words[key].map(w => w === editing.word ? word : w) : [...words[key], word];
      saveViz({ words: { ...words, [key]: list } });
    };
    input.onkeydown = e => { if (e.key === "Enter" && !e.isComposing) add(); };
    return el("div", { className: "opt" }, el("span", { textContent: title }), ...words[key].map(w => {
      const c = chip(w, () => saveViz({ words: { ...words, [key]: words[key].filter(x => x !== w) } }));
      c.title = "눌러서 고쳐요";
      c.onclick = e => { if (e.target.tagName !== "BUTTON") { editing = { key, word: w }; showViz(); } };
      return c;
    }), input, el("button", { className: "ghost", textContent: editing?.key === key ? "고치기" : "더하기", onclick: add }));
  }));
  autoPanel($("viz-auto"), Object.keys(SERVER.dash.auto), query_auto, off => saveViz({ query_auto: off }));
}
if (SERVER.dash?.viz) { $("viz-prefs").classList.remove("hidden"); $("set-tab-viz").classList.remove("hidden"); showViz(); }
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
      connMsg("연결을 끊었어요. 쓸 파일을 고르고 [연결]을 누르세요.", "hint");
    } else connMsg("연결은 그대로 두고 화면만 비웠어요. 테이블을 다시 고르세요.", "hint");
    for (const k in HASHED) delete HASHED[k];  // 연결이 바뀌면 해시 컬럼 기억도 비운다
    if (typeof clearScreen === "function") clearScreen();
  } catch (e) { connMsg(e.message); }
});
// 경로(폴더나 파일 하나)에서 체크한 종류의 파일을 찾는다. 하위 폴더까지 찾을지는 체크로. 파일이 하나뿐이면 바로 고른 상태로 둔다.
// [폴더 고르기]: 폴더 고르기 대화상자에서 고른 폴더를 칸에 넣고 바로 찾는다. 취소하면(path 없음) 그대로 둔다.
if (hasConnect) $("pick-folder").onclick = () => busy($("pick-folder"), async () => {
  const d = await api("/api/pick", { kind: "folder" });
  if (d.path) { $("path").value = d.path; $("find").click(); }
});
// 파일 목록에 보일 이름: 경로 칸의 폴더 아래 부분만(안쪽 폴더의 파일은 "안쪽\이름"). 그 폴더 아래가 아니면 파일 이름만. 값(전체 경로)은 바꾸지 않고 title로 보인다.
function underPath(full) {
  const root = $("path").value.trim().replace(/[\\/]+$/, "");
  const same = s => s.toLowerCase().replaceAll("\\", "/");  // 대소문자와 \ · / 차이는 같은 경로로 본다
  const under = root && same(full).startsWith(same(root)) && /[\\/]/.test(full[root.length] ?? "");
  return under ? full.slice(root.length + 1) : full.split(/[\\/]/).pop();
}
const LIST_MAX = 2000;  // 화면에 올리는 파일 수의 상한(수만 개를 한 번에 그리면 화면이 멎는다)
async function listFiles() {
  const kinds = [...$("kinds").querySelectorAll("input:checked")].map(b => b.value);
  const d = await api("/api/files", { path: $("path").value.trim(), kinds, deep: $("deep").checked });
  const files = d.files.slice(0, LIST_MAX), flat = d.flat.slice(0, LIST_MAX);
  fill($("file"), files);
  [...$("file").options].forEach(o => { o.title = o.value; o.text = underPath(o.value); });
  if (d.flat.length) $("file").append(new Option(`표 형식 파일에서 여러 개 고르기 (${d.flat.length}개)`, FLAT));
  offList.clear();
  checks($("flat-list"), flat);  // 처음에는 아무것도 고르지 않은 상태. 쓸 파일만 체크한다.
  flatBoxes().forEach(b => { b.parentElement.title = b.value; b.nextSibling.textContent = " " + underPath(b.value); });
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
  document.body.dataset.conn = info?.kind ?? "";  // 연결 종류(url·folder): 주소 연결에서만 쓰는 칸을 CSS로 가린다
  connMsg("연결했어요.", true);
  closeConnect();
  if (!hasTables) return showCrumb();
  for (const k in HASHED) delete HASHED[k];
  clearScreen();  // 이전 연결에서 고른 테이블과 조건은 새 연결과 맞지 않으므로 비운다.
  $("schema").classList.toggle("hidden", !schemas.length);
  fill($("schema"), schemas, "스키마(폴더)");
  $("after-connect").classList.remove("hidden");
  await loadTables();
  // (Phase 19) 사용자 스키마가 하나뿐이고 처음 고른 스키마에서 테이블이 보이면 스키마 칸은 몰라도 된다(값은 그대로). 시스템 카탈로그(SQL 표준·PostgreSQL 호환)는 세지 않는다.
  if (schemas.filter(s => !/^(information_schema|pg_catalog)$/i.test(s)).length <= 1 && $("base").options.length > 1) $("schema").classList.add("hidden");
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
const DRIVER_STATUS = { installed: "설치됨", missing: "설치 안 됨", program_needed: "프로그램 필요" };
function copyCommand(text, label = "복사") {
  const b = el("button", { className: "ghost", textContent: label, title: text, onclick: async () => {
    try { await navigator.clipboard.writeText(text); b.textContent = "복사됨"; } catch (e) { b.textContent = "직접 골라 복사하세요"; }
    setTimeout(() => b.textContent = label, 1500);
  } });
  return b;
}
// 받는 곳: 기본 브라우저로 여는 링크(pywebview는 바깥 주소를 브라우저로 연다)와 주소 복사. 앱이 내려받거나 실행하지 않는다
const driverGet = d => d.url ? [el("a", { href: d.url, target: "_blank", rel: "noopener noreferrer", className: "btn", textContent: "받는 곳 열기", title: d.url }), " ", copyCommand(d.url, "주소 복사")] : [el("span", { className: "hint", textContent: "따로 받을 것이 없어요" })];
const driverExtra = d => [d.program && `따로 설치할 프로그램: ${d.program.name} (${d.program.url})`, d.after, d.notice].filter(Boolean);
function showDriverHint(d) {
  $("driver-hint").replaceChildren(el("div", { className: "drv-guide" }, el("div", { textContent: d.explanation }),
    el("div", { className: "row", style: "align-items:center" }, ...driverGet(d)),
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
        el("td", {}, ...driverGet(d)))));
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
  fill($("base"), tables, "테이블 고르기");
  if (virtual.length) $("base").append(el("optgroup", { label: "가상 테이블" }, ...virtual.map(v => new Option(v, v))));
  if ([...$("base").options].some(o => o.value === now && now)) $("base").value = now;
  showCrumb();
  document.dispatchEvent(new Event("tables"));  // 화면이 테이블 목록을 따로 쓰면(SQL 탭) 다시 그린다
}
if (hasTables) $("schema").onchange = loadTables;

// 위쪽 줄의 연결 이름: "파일 / 테이블"(주소면 호스트/DB). 연결이 없으면 "연결 안 됨". 연결 칸이 없는 화면(수집)은 서버에 남은 연결(/api/state)을 읽어 같은 자리에 보이고, 누르면 첫 화면으로 간다.
let connName = null;
function connLabel(info) {
  if (!info) return null;
  if (info.kind === "url") {
    try { const u = new URL(info.url); return (u.host + u.pathname).replace(/\/$/, "") || info.url; } catch (e) { return info.url; }
  }
  return (info.files ? `파일 ${info.files.length}개` : (info.file || "").split(/[\\/]/).pop()) || info.path;
}
// 마지막으로 고른 테이블(연결 이름·스키마·이름): 분석 화면이 열릴 때 쿼리에서 고른 테이블을 이어받는다.
const LAST_TABLE = "loupe.lastTable";
function lastTable() { try { return JSON.parse(localStorage.getItem(LAST_TABLE)); } catch (e) { return null; } }
function showCrumb() {
  if (connName && S.base) try { localStorage.setItem(LAST_TABLE, JSON.stringify({ conn: connName, schema: S.schema || "", base: S.base })); } catch (e) {}
  $("crumb").classList.toggle("on", !!connName);
  $("crumb-text").innerHTML = connName ? esc(connName) + (S.base ? ` <em>/</em> ${esc(S.base)}` : "") : "연결 안 됨";
}
if (hasTables) $("base").addEventListener("change", () => setTimeout(showCrumb));  // 화면 스크립트가 S.base를 바꾼 뒤에
showCrumb();
if (!hasConnect) {
  $("crumb").classList.add("link");
  $("crumb").title = "눌러서 첫 화면에서 연결을 바꿔요";
  $("crumb").onclick = () => { location.href = "/"; };
  api("/api/state").then(s => { if (s.connected) { connName = connLabel(s.opened); showCrumb(); } });
}

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

// (Phase 19) 쉬운 보기: html.easy면 [data-adv] 묶음 안의 .adv 칸을 접고 묶음마다 [더 많은 설정]을 단다. 기본은 켬(설정 easy).
function showEasy(on) {
  document.documentElement.classList.toggle("easy", on);
  $("easy").setAttribute("aria-pressed", String(on));
  $("easy-text").textContent = on ? "쉬운 보기 켜짐" : "쉬운 보기 꺼짐";
}
$("easy").onclick = () => {
  const on = !document.documentElement.classList.contains("easy");
  showEasy(on);
  saveSetting({ easy: on ? "on" : "off" });
  // 무엇이 바뀌었는지 알린다: 접을 칸이 없는 화면(첫 화면·연동)이나 이미 펼쳐 둔 묶음뿐이면 눌러도 달라 보이지 않는다
  const folded = document.querySelectorAll("[data-adv]:not(.adv-shown) .adv").length, lines = document.querySelectorAll(".easy-only").length;
  flash(!on ? "쉬운 보기를 껐어요. 모든 설정을 펼쳐 보여요." : folded || lines ? `쉬운 보기를 켰어요. 이 화면에서 설정 ${folded}개를 [더 많은 설정] 뒤로 접고 설명 ${lines}줄을 보여요.`
    : "쉬운 보기를 켰어요. 이 화면에는 접을 설정이 없어요.", EASY_NOTE_MS);
};
const EASY_NOTE_MS = 4000;  // 쉬운 보기 알림을 보일 시간
showEasy(document.documentElement.classList.contains("easy"));
// 묶음(box)을 펼친다. 값이 들어 있는 접힌 칸을 화면이 채웠을 때 부른다.
function showAdv(box) {
  box.classList.add("adv-shown");
  const b = box.querySelector(":scope .adv-more");
  if (b) b.textContent = "접기 ▴";
}
// 펼쳐 둔 묶음 기억: 설정 adv_open(JSON을 퍼센트 인코딩한 글자, 서버가 쿠키로 돌려준다. 실행마다 포트가 바뀌어 창의 저장소로는 기억할 수 없다).
// 열쇠는 화면 주소 + 묶음 id. id 없는 묶음은 기억하지 않는다(순서로 기억하면 화면에 묶음을 더하거나 뺀 뒤 엉뚱한 묶음이 펼쳐진다).
let advOpen = [];
try { advOpen = JSON.parse(saved("adv_open") || "[]"); } catch (e) { }
const advKey = box => location.pathname + "#" + box.id;
// 목록 정리 후 바뀌었으면 저장: 이 화면에 더는 없는 묶음(빼거나 id를 바꿈)은 버리고, 글자 상한(settings.ADV_OPEN_MAX)을 넘으면 먼저 펼친 것부터 버린다.
function keepAdv(list) {
  const here = location.pathname + "#";
  const kept = list.filter(k => !k.startsWith(here) || document.getElementById(k.slice(here.length))?.hasAttribute("data-adv"));
  while (kept.length && encodeURIComponent(JSON.stringify(kept)).length > SERVER.adv_open_max) kept.shift();
  if (JSON.stringify(kept) !== JSON.stringify(advOpen)) api("/api/settings", { adv_open: encodeURIComponent(JSON.stringify(kept)) }).catch(() => {});  // 못 저장해도 지금 화면은 그대로
  advOpen = kept;
}
// 아직 단추가 없는 [data-adv] 묶음에 [더 많은 설정]을 단다(묶음 안의 [data-adv-slot]이 있으면 그 자리에, 없으면 맨 끝). 접힌 칸에 기본값과 다른 값이 있으면 펼쳐 둔다. 화면이 칸을 새로 그린 뒤 다시 불러도 된다.
function advMore(root = document) {
  if (root === document) keepAdv(advOpen);
  root.querySelectorAll("[data-adv]").forEach(box => {
    if (!box.querySelector(".adv")) return;
    let b = [...box.querySelectorAll(".adv-more")].find(x => x.closest("[data-adv]") === box);
    if (!b) {
      b = el("button", { type: "button", className: "adv-more", textContent: "더 많은 설정 ▾", title: "처음에는 몰라도 되는 설정이에요" });
      b.onclick = () => {
        const open = !box.classList.contains("adv-shown");
        open ? showAdv(box) : (box.classList.remove("adv-shown"), b.textContent = "더 많은 설정 ▾");
        if (box.id) keepAdv(advOpen.filter(k => k !== advKey(box)).concat(open ? [advKey(box)] : []));
      };
      (box.querySelector(":scope [data-adv-slot]") || box).append(b);
      if (box.id && advOpen.includes(advKey(box))) showAdv(box);  // 사람이 [더 많은 설정]으로 펼쳐 둔 묶음은 다음에도 펼친다
    }
    // 화면 HTML에 있는 칸(id가 있음)만 본다. 스크립트가 만든 칸은 처음 값(default)이 비어 있어 늘 바뀐 것으로 보인다. 체크·라디오를 되살리는 화면은 showAdv를 직접 부른다.
    const changed = f => f.id && f.type !== "checkbox" && f.type !== "radio"
      && (f.tagName === "SELECT" ? [...f.options].some(o => o.selected !== o.defaultSelected) && f.selectedIndex > 0 : f.value !== f.defaultValue);
    if ([...box.querySelectorAll(".adv input, .adv select, .adv textarea, input.adv, select.adv, textarea.adv")].some(changed)) showAdv(box);
  });
}
// (Phase 20) [data-hint] 요소의 title 글(자기 것이 없으면 안쪽 첫 title)을 바로 아래 .hint.easy-only 줄로도 보인다(쉬운 보기에서만 보임, common.css). 두 번 불러도 한 줄.
function titleHints(root = document) {
  root.querySelectorAll("[data-hint]").forEach(x => {
    if (!x.nextElementSibling?.classList.contains("easy-only")) x.after(el("div", { className: "hint easy-only", textContent: x.title || x.querySelector("[title]").title }));
  });
}
// 컬럼 타입을 쉬운 말로(원래 이름은 화면이 title로 둔다): int64 → 숫자, string → 글자, timestamp → 날짜·시각.
function typeWord(type) {
  const t = String(type || "").replace(/^!/, "").toLowerCase();
  if (/^(u?int\d|float|decimal|double|real|number|정수|실수)/.test(t)) return "숫자";  // 정수·실수·문자: 서버 type_name이 준 한국어 이름
  if (/^(string|varchar|text|char|문자)/.test(t)) return "글자";
  if (t.startsWith("timestamp")) return "날짜·시각";
  if (t === "date") return "날짜";
  if (t === "time") return "시각";
  if (t === "boolean") return "예/아니오";
  if (/^array/.test(t)) return "목록";
  if (/^(struct|map|json)/.test(t)) return "묶음";
  return type;
}
advMore();
titleHints();

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
menuList.setAttribute("role", "listbox");
menuList.id = "menu-list";
function closeMenu() {
  if (menuFor) { menuFor.setAttribute("aria-expanded", "false"); menuFor.removeAttribute("aria-activedescendant"); }
  menuBox.classList.add("hidden");
  menuFor = null;
}
// 목록을 가진 칸(owner)을 스크린리더에 알린다: 열림·목록 연결, 칸이 입력 칸이면 combobox, 항목은 option(가리킨 항목은 pointMenu가 aria-activedescendant로). 항목을 채운 뒤에 부른다.
function ownMenu(owner) {
  if (menuFor && menuFor !== owner) { menuFor.setAttribute("aria-expanded", "false"); menuFor.removeAttribute("aria-activedescendant"); }
  menuFor = owner;
  if (owner.tagName === "INPUT") { owner.setAttribute("role", "combobox"); owner.setAttribute("aria-autocomplete", "list"); }
  owner.setAttribute("aria-expanded", "true");
  owner.setAttribute("aria-controls", "menu-list");
  [...menuList.children].forEach((o, i) => { o.setAttribute("role", "option"); o.id = "menu-opt-" + i; });
}
function pointMenu(item, scroll) {
  menuAt?.classList.remove("on");
  menuAt = item || null;
  menuAt?.classList.add("on");
  if (scroll) menuAt?.scrollIntoView({ block: "nearest" });
  if (menuAt) menuFor?.setAttribute("aria-activedescendant", menuAt.id); else menuFor?.removeAttribute("aria-activedescendant");
}
function openMenu(sel) {
  if (sel.disabled || !sel.options.length) return;
  const find = sel.options.length > MENU_FIND;
  menuList.replaceChildren(...[...sel.options].map((o, i) => Object.assign(document.createElement("div"), { textContent: o.text, className: i === sel.selectedIndex ? "now" : "" })));
  ownMenu(sel);
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
  const want = inp.value.trim().toLowerCase(), hits = [...document.getElementById(inp.dataset.list)?.children ?? []].map(o => ({ v: o.value, hint: o.label && o.label !== o.value ? o.label : "" })).filter(({ v }) => v.toLowerCase().includes(want) && v !== inp.value);
  if (inp.disabled || !hits.length) return menuFor === inp ? closeMenu() : undefined;
  menuList.replaceChildren(...hits.map(({ v, hint }) => { const d = Object.assign(document.createElement("div"), { textContent: v }); if (hint) d.setAttribute("data-hint", hint); return d; }));  // 라벨(타입 힌트)은 CSS로 덧붙여 textContent가 값 그대로이게
  ownMenu(inp);
  menuFind.classList.add("hidden");
  menuList.style.maxHeight = "";
  placeMenu(inp);
  pointMenu(null);
}
function quietFocus(a) { comboQuiet = true; try { a.focus(); } finally { comboQuiet = false; } }  // Ctrl+S 같은 단축키가 값을 반영하려 blur→focus 할 때 목록·말풍선이 다시 뜨지 않게
const comboOn = e => !comboQuiet && !e.isComposing && e.target.matches?.("input[data-list]") && showCombo(e.target);  // compositionend: 한글 조합이 끝나는 글자도 걸러야 한다
document.addEventListener("focusin", comboOn);
document.addEventListener("click", comboOn);
document.addEventListener("input", comboOn);
document.addEventListener("compositionend", comboOn);
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
    if ([" ", "Enter", "ArrowDown", "ArrowUp", "F4"].includes(e.key) && !e.ctrlKey && !e.metaKey && e.target.matches("select")) { e.preventDefault(); openMenu(e.target); }
    return;
  }
  if (e.isComposing) return;  // 한글을 조합하는 동안의 키는 입력기가 쓴다.
  const items = menuItems(), step = { ArrowDown: 1, ArrowUp: -1 }[e.key];
  if (e.key === "Tab") return closeMenu();
  if (menuFor.tagName === "INPUT") {  // 입력 칸의 목록: 글자(Space 포함)는 칸이 받고, ↑↓로 가리킨 항목이 있을 때만 Enter로 고른다
    if (e.key === "Escape") closeMenu();
    else if (e.key === "Enter" && menuAt && !e.ctrlKey && !e.metaKey) { pickMenu(menuAt); e.stopPropagation(); }  // 칸의 Enter 동작(추가 등)은 하지 않는다
    else if (step) pointMenu(items[Math.min(Math.max(items.indexOf(menuAt) + step, 0), items.length - 1)], true);
    else return;
    return e.preventDefault();
  }
  if (e.key === "Escape") { const sel = menuFor; closeMenu(); sel.focus(); }
  else if ((e.key === "Enter" && !e.ctrlKey && !e.metaKey) || (e.key === " " && e.target !== menuFind)) pickMenu(menuAt);
  else if (step) pointMenu(items[Math.min(Math.max(items.indexOf(menuAt) + step, 0), items.length - 1)], true);
  else return;
  e.preventDefault();
}, true);
// Esc로 마지막에 뜬 창(.modal)을 닫는다: 창 배경을 누른 click을 보내 창마다의 바깥 누르기 닫기를 그대로 쓴다(그런 처리가 없는 질문 창은 남는다).
// 위 목록이 Esc로 닫혔으면(preventDefault) 창은 두고, 창이 닫혔으면 preventDefault로 다른 Esc 처리(대시보드 보기 끝내기)를 막는다.
document.addEventListener("keydown", e => {
  const top = e.key === "Escape" && !e.defaultPrevented && !e.isComposing && [...document.querySelectorAll(".modal:not(.hidden)")].pop();
  if (!top) return;
  top.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  if (!top.isConnected || top.classList.contains("hidden")) e.preventDefault();
});
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
let tipMouseAt = 0;  // 마우스로 눌러 생긴 포커스는 말풍선을 띄우지 않는다(팝오버를 가린다)
document.addEventListener("mouseover", e => { const h = tipHost(e); if (h) showTip(h, 350); else if (tipFor && (!tipFor.isConnected || !tipFor.contains(e.target))) hideTip(); });  // 보이던 요소가 다시 그려져 사라지면 mouseout이 없다
document.addEventListener("focusin", e => { const h = tipHost(e); if (h && !comboQuiet && Date.now() - tipMouseAt > 400) showTip(h, 0); });
document.addEventListener("mouseout", e => { if (tipFor && (!tipFor.isConnected || !tipFor.contains(e.relatedTarget))) hideTip(); });
document.addEventListener("focusout", hideTip);
document.addEventListener("mousedown", () => { tipMouseAt = Date.now(); hideTip(); });
document.addEventListener("keydown", hideTip, true);
document.addEventListener("scroll", hideTip, true);
addEventListener("blur", hideTip);

// 잠깐 보이는 안내("되돌렸어요" 등). 화면 아래 가운데에 1.5초.
const toastBox = document.body.appendChild(Object.assign(document.createElement("div"), { className: "toast hidden", role: "status" }));
let toastTimer = 0;
function flash(text, ms = 1500) {  // 실패 안내는 읽을 시간이 더 필요해 부르는 쪽이 길게(약 4초) 준다
  toastBox.textContent = text;
  toastBox.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastBox.classList.add("hidden"), ms);
}

// (Phase 17) 찾기 칸: 입력이 이어지는 동안은 보내지 않고 마지막 입력 뒤 TYPE_WAIT ms 또는 Enter에서 찾는다. 늦게 온 앞 응답은 버린다.
const TYPE_WAIT = 300;
function findAsTyped(input, find, show) {
  // [SPECIFICATION] plans/phase-17.md#findAsTyped
  let timer = 0, seq = 0;
  const go = () => {
    clearTimeout(timer);
    const mine = ++seq;
    new Promise(r => r(find(input.value.trim())))  // find를 바로(같은 차례에) 부르고, 던진 오류도 받는다
      .then(d => { if (mine === seq) show(d, null); }, e => { if (mine === seq) show(null, e); });  // 늦게 온 앞 응답은 버린다
  };
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(go, TYPE_WAIT); });  // 입력이 이어지면 다시 기다린다
  input.addEventListener("keydown", e => { if (e.key !== "Enter" || e.isComposing) return; e.preventDefault(); go(); });  // 한글 조합 중 Enter는 무시
}

// 우클릭 메뉴(쿼리 표의 칸·머리글, 전처리 컬럼 줄): ctxMenu(e, items, fallbackRect). items는 [[라벨, 실행|null, 안내], …](실행이 없으면 흐리게, 안내는 말풍선).
// 열린 목록(.drop .pop) 모양, ↑↓로 흐리지 않은 항목 사이를 옮기고, 화면 밖으로 나가지 않게 둔다. 키보드(메뉴 키·Shift+F10)로 열면 좌표가 0이라 fallbackRect 아래에.
// items가 없으면 열린 메뉴만 닫고 브라우저 기본 메뉴를 둔다. ctxBox는 열린 메뉴, ctxFrom은 연 요소(닫으면 초점을 돌려준다).
let ctxBox = null, ctxFrom = null;
function closeCtx() {  // 메뉴 안에 초점이 있었으면(키보드로 쓰던 중) 연 요소로 초점을 돌려준다. 바깥을 눌러 닫을 때는 누른 곳이 초점을 가져간다
  const box = ctxBox;
  if (!box) return;
  ctxBox = null;  // 지우기 전에 비운다: 초점이 든 상자를 지우면 focusout이 다시 closeCtx를 부른다
  const back = box.contains(document.activeElement);
  box.remove();
  if (back) ctxFrom?.focus?.();
}
function ctxMenu(e, items, fallbackRect) {
  closeCtx();
  if (!items?.length) return;
  e.preventDefault();
  ctxFrom = e.target;
  const pop = el("div", { className: "pop ctx-pop" }), box = el("div", { className: "drop" }, pop);
  pop.setAttribute("role", "menu");
  const live = () => [...pop.children].filter(b => !b.disabled);
  pop.onkeydown = k => { const b = live(), i = b.indexOf(document.activeElement), step = { ArrowDown: 1, ArrowUp: -1 }[k.key]; if (step && b.length) { k.preventDefault(); b[(i + step + b.length) % b.length].focus(); } };
  for (const [label, run, why] of items) {
    const b = el("button", { type: "button", textContent: label, title: run ? label : why || label, disabled: !run, onclick: () => { closeCtx(); run(); } });
    b.setAttribute("role", "menuitem"); b.setAttribute("aria-label", label);
    pop.append(b);
  }
  pop.addEventListener("focusout", f => { if (!pop.contains(f.relatedTarget)) closeCtx(); });  // Tab 등으로 초점이 메뉴 밖으로 나가면 닫는다
  box.style.cssText = "position:fixed;z-index:300;left:0;top:0";  // 먼저 띄워서 잰다(블록인 .drop을 그냥 재면 화면 너비라 왼쪽 끝에 붙었다, B1)
  document.body.append(box); ctxBox = box;
  const r = box.getBoundingClientRect(), [x, y] = e.clientX || e.clientY || !fallbackRect ? [e.clientX, e.clientY] : [fallbackRect.left, fallbackRect.bottom];
  box.style.left = `${Math.max(0, Math.min(x, innerWidth - r.width))}px`; box.style.top = `${Math.max(0, Math.min(y, innerHeight - r.height))}px`;  // 화면 밖으로 나가지 않게
  live()[0]?.focus();
}
document.addEventListener("mousedown", e => { if (ctxBox && !ctxBox.contains(e.target)) closeCtx(); });
document.addEventListener("keydown", e => { if (e.key === "Escape" && ctxBox) { e.stopPropagation(); closeCtx(); } }, true);
