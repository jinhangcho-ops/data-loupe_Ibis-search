const $ = id => document.getElementById(id);
document.querySelectorAll(".pages a").forEach(a => a.classList.toggle("on", a.pathname === location.pathname));  // 지금 화면의 전환 버튼을 누른 모양으로
const S = { schema: "", base: "", cols: [] };
async function send(url, body) {
  const res = await fetch(url, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error((await res.json()).error);
  return res;
}
const api = async (url, body) => (await send(url, body)).json();
const esc = v => String(v ?? "").replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const fill = (sel, items, blank) => sel.innerHTML = (blank ? `<option value="">${blank}</option>` : "") +
  items.map(v => `<option>${esc(v)}</option>`).join("");
// 요소 만들기: 속성을 주고 자식(요소나 글자)을 붙인다.
const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids); return e; };
// 칩: 이름 옆의 ×를 누르면 remove를 부른다.
const chip = (text, remove) => el("span", { className: "chip" }, text, el("button", { className: "ghost", textContent: "×", title: "빼기", onclick: remove }));
const download = (href, name) => el("a", { href, download: name }).click();
// 값이 표에 보이는 글자와 전체 글자(잘렸을 때만, 마우스를 올리면 보인다). 숫자는 자릿수 쉼표를 넣고, 문자열은 최대 글자 수(#maxlen, 없는 화면은 40)에서 자른다.
function shown(v) {
  if (typeof v === "number") return [v.toLocaleString(), ""];
  const s = String(v ?? ""), max = +($("maxlen")?.value ?? 40);
  return max > 0 && s.length > max ? [s.slice(0, max) + "…", s] : [s, ""];
}
function cell(v) {
  const [text, full] = shown(v);
  return typeof v === "number" ? `<td class="num">${text}</td>`
    : full ? `<td title="${esc(full).replace(/"/g, "&quot;")}">${esc(text)}</td>` : `<td>${esc(text)}</td>`;
}
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

// 상단 표시: 메모리·CPU(PC 전체 | 이 프로그램)는 2초마다 읽는다.
async function showUsage() {
  try { const d = await api("/api/usage"); $("usage").innerHTML = `<span>메모리  전체 ${d.pc_memory}%</span> <span>| 프로그램 ${d.memory}% (${d.memory_mb.toLocaleString()}MB)</span><br><span>CPU  전체 ${d.pc_cpu}%</span> <span>| 프로그램 ${d.cpu}%</span>`; } catch (e) {}
}
showUsage();
setInterval(showUsage, 2000);

// 전체 시간: [검색]을 누르면 0에서 다시 시작하고, 그 뒤의 집계·차트·저장 시간을 더해 간다. 도는 동안은 "예상 | 경과", 끝나면 "예상 | 실제".
// 작업 하나의 예상 = 기준 테이블 행 수 × 그 작업 종류·행 수 구간의 직전 행당 시간(나노초/행, 설정으로 기억). 그 구간에서 안 해 봤으면 예상에 더하지 않는다.
const rates = {};  // 설정 이름 → 행당 시간. 처음 쓸 때 저장된 값을 읽어 둔다.
// 행당 시간의 설정 이름: 작업 종류와 행 수의 구간(10배 단위, settings.TIME_BANDS 안으로 맞춘다)마다 따로다.
const rateName = (kind, rows) => `time_${kind}_${Math.min(SERVER.time.bands[1], Math.max(SERVER.time.bands[0], String(rows).length - 1))}`;
const secs = ms => (ms / 1000).toFixed(1) + "초";
let total = { guess: 0, done: 0 }, timer;  // 예상 합계와 끝난 작업의 시간 합계(ms)
const going = new Set();  // 지금 도는 작업들의 시작 시각
function showTiming() {
  const now = performance.now(), ms = total.done + [...going].reduce((sum, run) => sum + now - run.at, 0);
  $("timing").textContent = `전체  예상 ${total.guess ? secs(total.guess) : "—"} | ${going.size ? "경과" : "실제"} ${secs(ms)}`;
  $("cancel").classList.toggle("hidden", !going.size);  // [취소]는 도는 작업이 있을 때만 보인다.
}
function resetTiming() {
  total = { guess: 0, done: 0 };
  going.clear();
  clearInterval(timer);
  $("timing").innerHTML = "&nbsp;";
  $("cancel").classList.add("hidden");
}
$("cancel").onclick = () => api("/api/cancel", {}).catch(() => {});  // 멈춘 작업이 제 오류 칸에 "취소했습니다."를 띄운다.
async function timed(kind, fn) {
  if (kind === "search") resetTiming();
  const mine = total, run = { at: performance.now() };  // 도는 사이 새로 검색하면 이 작업의 시간은 새 합계에 넣지 않는다.
  going.add(run);
  clearInterval(timer);
  timer = setInterval(showTiming, 100);
  try {
    const rows = $("count")?.checked !== false && (await api("/api/rowcount", { schema: S.schema, base: S.base })).rows;  // 전체 행 수를 세지 않으면 예상도 구하지 않는다. (#count가 없는 화면은 늘 센다)
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
const DONE = { search: "검색이", summary: "집계가", chart: "차트가", export: "저장이", profile: "통계가", preview: "미리보기가", run: "전처리 실행이", collect: "수집이" };
// 오래 걸린 작업이 끝나면 done인 보낼 곳마다 알린다. 데이터 값은 보내지 않는다. text: 알림의 본문(기본은 기준 테이블 이름)
function notifyDone(kind, seconds, rows, text = S.base) {
  if (seconds < notifyAfter) return;
  for (const [name, d] of Object.entries(dest.destinations)) {
    if (d.done) sendTo(name, { title: `${DONE[kind]} 끝났습니다`, text, ...(rows ? { rows } : {}), seconds: +seconds.toFixed(1) }).catch(e => connMsg(e.message));
  }
}
const destMsg = (text, ok) => say("msg-dest", text, ok);
// 목록에서 고른 보낼 곳(없으면 새로 만들기)을 폼에 채운다.
function pickDest() {
  const name = $("dest-list").value, saved = dest.destinations[name];
  $("dest-name").value = name;
  $("dest-done").checked = !!saved?.done;
  if (saved) $("dest-kind").value = saved.kind;
  showFields();
}
// 고른 종류의 칸을 서버의 표대로 그린다. 저장된 것이면 비밀이 아닌 칸은 값을, 비밀 칸은 안내 글자를 채운다.
function showFields() {
  const kind = $("dest-kind").value, spec = dest.kinds[kind], now = dest.destinations[$("dest-list").value];
  const saved = now?.kind === kind ? now : null, presets = spec.presets ?? [];
  $("dest-preset").replaceChildren(new Option("기본 틀", ""), ...presets.map((p, i) => new Option(p.label, i)));
  $("dest-preset").classList.toggle("hidden", !presets.length);
  $("dest-fields").replaceChildren(...spec.fields.map(f => {
    const input = f.options ? el("select", {}, ...f.options.map(([v, t]) => new Option(t, v)))
      : f.multiline ? el("textarea", { rows: 4 }) : el("input", { type: f.secret ? "password" : "text" });
    input.dataset.field = f.name;
    if (f.secret) input.placeholder = !saved ? "" : saved[f.name] ? "저장됨 (바꾸려면 새로 입력)" : "다시 입력 필요";
    else input.value = saved?.[f.name] ?? f.default ?? "";
    return el("div", {}, el("div", { className: "hint", textContent: f.label }), input, ...(f.hint ? [el("div", { className: "hint", textContent: f.hint })] : []));
  }));
}
function showDestinations(d, name = $("dest-list").value) {
  dest = d;
  fill($("dest-list"), Object.keys(d.destinations), "새로 만들기");
  $("dest-list").value = name in d.destinations ? name : "";
  $("dest-kind").replaceChildren(...Object.entries(d.kinds).map(([k, v]) => new Option(v.label, k)));
  pickDest();
  document.dispatchEvent(new Event("destinations"));  // 화면이 보낼 곳 목록을 쓰는 칸을 다시 채운다.
}
$("dest-list").onchange = pickDest;
$("dest-kind").onchange = showFields;
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
  const destination = { kind: $("dest-kind").value, done: $("dest-done").checked,
    ...Object.fromEntries([...$("dest-fields").querySelectorAll("[data-field]")].map(i => [i.dataset.field, i.value])) };  // 비밀 칸을 비우면 서버가 저장된 값을 그대로 둔다.
  try { showDestinations(await api("/api/destinations", { name, destination }), name); destMsg("저장했습니다", true); } catch (e) { destMsg(e.message); }
});
$("dest-del").onclick = () => busy($("dest-del"), async () => {
  if (!$("dest-list").value) return;
  try { showDestinations(await api("/api/destinations", { name: $("dest-list").value, destination: null }), ""); destMsg("삭제했습니다", true); } catch (e) { destMsg(e.message); }
});
$("dest-test").onclick = () => busy($("dest-test"), async () => {
  if (!$("dest-list").value) return destMsg("저장한 보낼 곳을 고른 뒤 시험하세요.");
  try { await sendTo($("dest-list").value, { title: "시험 메시지", text: "Ibis에서 보낸 시험 메시지입니다." }); destMsg("보냈습니다", true); } catch (e) { destMsg(e.message); }
});
api("/api/destinations").then(showDestinations).catch(() => {});

// 1. 연결
document.querySelectorAll("input[name=kind]").forEach(r => r.onchange = () => {
  const folder = r.value === "folder" && r.checked;
  $("folder-box").classList.toggle("hidden", !folder);
  $("url-box").classList.toggle("hidden", folder);
});
// 연결 칸 채우기(비밀번호는 빼고): 종류 라디오와 경로 또는 주소·아이디
function fillConnection(info) {
  if (info.kind === "folder") $("path").value = info.path;
  if (info.kind === "url") {
    document.querySelector("input[name=kind][value=url]").click();
    $("url").value = info.url;
    $("user").value = info.user;
  }
}
// 두 화면은 따로 열리지만 연결은 서버에 남아 있다. 화면이 열린 뒤 화면 스크립트의 끝에서 부른다.
// 연결돼 있으면 방금 [연결]에 성공했을 때와 같은 상태로 만든다. 아니면 지난번 연결을 칸에 채워 둔다(연결은 [연결]을 눌러야 한다).
async function resume() {
  try {
    const state = await api("/api/state");
    if (!state.connected) {
      const { recent } = await api("/api/recent");
      if (recent) fillConnection(recent);
      return;
    }
    const { opened, schemas } = state;
    fillConnection(opened);
    if (opened.kind === "folder") {
      await listFiles();
      if (opened.files) { $("file").value = FLAT; flatBoxes().forEach(b => b.checked = opened.files.includes(b.value)); }
      else $("file").value = opened.file;
      showFlat();
    }
    await showConnected(!!opened.files, schemas);
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

// Parquet·CSV는 여러 개를 골라 연다(파일마다 테이블 하나). 파일 목록에서 이 항목을 고르면 체크 목록이 나온다.
const FLAT = "*flat";  // 파일 이름에 쓸 수 없는 글자로 시작해 실제 경로와 겹치지 않는다.
const flatBoxes = () => [...document.querySelectorAll("#flat-list input")];
function showFlat() {
  $("flat").classList.toggle("hidden", $("file").value !== FLAT);
  $("flat-count").textContent = `${flatBoxes().filter(b => b.checked).length} / ${flatBoxes().length}개 선택`;
}
// 연결 칸(connect.html)이 있는 화면에서만 연결 칸의 버튼을 단다. 수집 화면처럼 연결 칸이 없는 화면도 common.js를 쓴다.
const hasConnect = !!$("connect");
if (hasConnect) {
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
$("flat-list").onchange = showFlat;
$("flat-all").onclick = () => { flatBoxes().forEach(b => b.checked = true); showFlat(); };
}
let connected = null;  // 지금 연결의 종류: "flat"(Parquet·CSV 파일), "other"(DuckDB 파일·DB 주소), 없으면 null
// [모두 해제]: 체크를 모두 푼다. Parquet·CSV로 연결돼 있었으면 그 연결을 끊고 화면을 연결 전으로 되돌린다(파일을 다시 골라 새로 연결).
// DuckDB 파일이나 DB 주소로 연결돼 있었으면 연결은 그대로 두고 화면에 올라온 것만 비운다.
if (hasConnect) $("flat-none").onclick = () => busy($("flat-none"), async () => {
  flatBoxes().forEach(b => b.checked = false);
  showFlat();
  if (!connected) return;
  try {
    if (connected === "flat") {
      await api("/api/disconnect", {});
      connected = null;
      $("after-connect").classList.add("hidden");
      connMsg("연결을 해제했습니다. 쓸 파일을 고르고 [연결]을 누르세요.", "hint");
    } else connMsg("연결은 그대로 두고 화면만 비웠습니다. 테이블을 다시 고르세요.", "hint");
    clearScreen();
  } catch (e) { connMsg(e.message); }
});
async function listFiles() {
  const d = await api("/api/files", { path: $("path").value });
  fill($("file"), d.files);
  if (d.flat.length) $("file").append(new Option(`Parquet·CSV 파일 고르기 (${d.flat.length}개)`, FLAT));
  checks($("flat-list"), d.flat);  // 처음에는 아무것도 고르지 않은 상태. 쓸 파일만 체크한다.
  $("file").classList.remove("hidden");
  showFlat();
  connMsg("");
}
if (hasConnect) $("find").onclick = () => busy($("find"), async () => {
  try { await listFiles(); } catch (e) { connMsg(e.message); }
});
// 연결에 성공한 상태로 만든다([연결]을 누른 뒤와 화면을 새로 연 뒤가 같다). flat: Parquet·CSV 파일 연결인가
async function showConnected(flat, schemas) {
  connected = flat ? "flat" : "other";
  clearScreen();  // 이전 연결에서 고른 테이블과 조건은 새 연결과 맞지 않으므로 비운다.
  connMsg("연결됨", true);
  $("schema").classList.toggle("hidden", !schemas.length);
  fill($("schema"), schemas, "기본 스키마");
  $("after-connect").classList.remove("hidden");
  await loadTables();
}
if (hasConnect) $("connect").onclick = () => busy($("connect"), async () => {
  const kind = document.querySelector("input[name=kind]:checked").value;
  const body = kind === "folder"
    ? { kind, path: $("path").value, ...($("file").value === FLAT ? { files: flatBoxes().filter(b => b.checked).map(b => b.value) } : { file: $("file").value }) }
    : { kind, url: $("url").value, user: $("user").value, password: $("password").value };
  try {
    const { schemas } = await api("/api/connect", body);
    await showConnected(!!body.files, schemas);
  } catch (e) { connMsg(e.message); }
});

// 2. 테이블
async function loadTables() {
  S.schema = $("schema").value;
  const { tables } = await api("/api/tables?schema=" + encodeURIComponent(S.schema));
  fill($("base"), tables, "기준 테이블 선택");
}
if (hasConnect) $("schema").onchange = loadTables;

// 설정(화면 모드, 차트 비율, 그림 저장 모드, 따옴표 해석, 전체 행 수 세기, 조건 입력 방식, 끌어서 조절한 크기, 직전 실행의 행당 시간): 바꾸면 서버가 설정 파일에 저장해 다음 실행 때도 쓴다.
const saveSetting = changes => api("/api/settings", changes).catch(e => connMsg(e.message));
function showTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("theme").textContent = theme === "dark" ? "라이트 모드" : "다크 모드";
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
  $("resize").textContent = on ? "크기 조절 끝" : "크기 조절";
}
$("resize").onclick = () => showResizing(!document.documentElement.classList.contains("resizing"));
showResizing(false);
// 경계를 끌면 그 크기의 CSS 변수를 바꾸고, 놓으면 저장한다. 더블클릭하면 기본 크기로 돌아간다(0을 저장).
const sized = { panel: () => document.querySelector(".panel") };
document.querySelectorAll("[data-size]").forEach(grip => {
  const name = grip.dataset.size, [min, max, axis] = SIZES[name], root = document.documentElement.style;
  const at = e => axis === "x" ? e.clientX : e.clientY;
  grip.onpointerdown = e => {
    const start = at(e), box = sized[name]().getBoundingClientRect(), from = axis === "x" ? box.width : box.height;
    let size = 0;
    grip.setPointerCapture(e.pointerId);
    grip.classList.add("on");
    document.body.style.userSelect = "none";
    grip.onpointermove = m => { size = Math.round(Math.min(max, Math.max(min, from + at(m) - start))); root.setProperty("--" + name, size + "px"); };
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
  menuBox.classList.remove("hidden");
  // 칸 아래에 펼치되, 아래가 좁고 위가 더 넓으면 위로 펼친다. 목록은 300px까지만 커지고 넘으면 스크롤된다.
  const r = sel.getBoundingClientRect(), below = innerHeight - r.bottom - 8, above = r.top - 8, up = below < Math.min(menuBox.offsetHeight, 160) && above > below;
  Object.assign(menuBox.style, { left: r.left + "px", minWidth: r.width + "px", top: up ? "" : r.bottom + 2 + "px", bottom: up ? innerHeight - r.top + 2 + "px" : "" });
  menuList.style.maxHeight = Math.min(300, (up ? above : below) - (menuBox.offsetHeight - menuList.offsetHeight)) + "px";
  pointMenu(menuList.children[sel.selectedIndex], true);
  if (find) menuFind.focus();
}
function pickMenu(item) {
  const sel = menuFor, i = [...menuList.children].indexOf(item);
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
