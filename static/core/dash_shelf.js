// 차트 칸 끌어 놓기: 컬럼 칩(숫자·날짜·글자 색 구분)을 [X축]·[값]·[색(나누기)]·[필터] 칸에 끌어 놓거나(누르기 대체: 칩을 누르고 칸을 누른다) 칸에 놓는다.
// 쿼리 화면의 차트 탭과 대시보드 카드 편집(오른쪽 패널)이 함께 쓴다. 놓을 수 없는 것은 이유 문장을 돌려준다(dropCheck, 화면 없이 계산해 node 테스트가 본다).
// 컬럼 종류는 /api/columns의 한글 타입이다: 정수·실수(숫자), 날짜·날짜시간, 문자, 참/거짓.
const SHELVES = { x: "X축", y: "값", split: "색(나누기)", filter: "필터" };
const SHELF_ROWS = "(행 개수)";  // 값 칸에 놓으면 개수를 세는 특별 칩
const shelfKind = type => ["정수", "실수"].includes(type) ? "num" : ["날짜", "날짜시간"].includes(type) ? "date" : type === "참/거짓" ? "bool" : "text";

// 칸에 놓을 수 있는지: {ok, reason}. state = {x, series: [{y, agg}], split, conditions}. SHELF_ROWS는 type이 없어도 값 칸에 놓을 수 있다.
function dropCheck(shelf, column, type, state) {
  const kind = column === SHELF_ROWS ? "rows" : shelfKind(type);
  if (!SHELVES[shelf]) return { ok: false, reason: "놓을 수 없는 칸이에요." };
  if (kind === "rows" && shelf !== "y") return { ok: false, reason: "(행 개수)는 [값] 칸에만 놓을 수 있어요." };
  if (shelf === "y") {
    if (kind === "text" || kind === "bool") return { ok: false, reason: `‘${column}’은 문자 컬럼이라 합계·평균을 낼 수 없어요. 개수를 세려면 [값]에 (행 개수)를 놓고, 이 컬럼으로 나누려면 [색(나누기)]나 [X축]에 놓으세요.` };
    if (kind === "date") return { ok: false, reason: `‘${column}’은 날짜 컬럼이라 값(합계·평균)이 될 수 없어요. [X축]에 놓으면 시간별로 볼 수 있어요.` };
    if ((state.series ?? []).some(s => (kind === "rows" ? !s.y : s.y === column))) return { ok: false, reason: `‘${column === SHELF_ROWS ? "행 개수" : column}’은 이미 값에 있어요. 칩을 눌러 합계·평균·개수를 바꾸세요.` };
    if ((state.series ?? []).length >= 5) return { ok: false, reason: "값은 5개까지 넣을 수 있어요." };
  }
  if (shelf === "split") {
    if (kind === "num") return { ok: false, reason: `‘${column}’은 숫자 컬럼이라 값의 종류가 너무 많아 색으로 나누기 어려워요. 문자 컬럼을 고르거나 구간을 나눈 컬럼을 만드세요.` };
    if (kind === "date") return { ok: false, reason: `‘${column}’은 날짜 컬럼이라 색으로 나누면 값이 너무 많아져요. [X축]에 놓으세요.` };
    if (column === state.x) return { ok: false, reason: "X축과 같은 컬럼으로는 나눌 수 없어요." };
  }
  if (shelf === "x" && column === state.split) return { ok: false, reason: "색(나누기)과 같은 컬럼을 X축에 놓을 수 없어요. 나누기를 먼저 비우세요." };
  return { ok: true, reason: "" };
}

// 놓은 결과의 새 상태(dropCheck를 통과한 뒤에). 필터는 조건 한 줄 `컬럼 연산자 값`을 conditions에 더한다.
function applyDrop(shelf, column, type, state, value = "", op = "=") {
  const next = { ...state, series: [...(state.series ?? [])], conditions: [...(state.conditions ?? [])] };
  if (shelf === "x") next.x = column;
  else if (shelf === "split") next.split = column;
  else if (shelf === "y") next.series.push(column === SHELF_ROWS ? { y: null, agg: "count" } : { y: column, agg: shelfKind(type) === "num" ? "sum" : "count" });
  else if (shelf === "filter") next.conditions.push(`${column} ${op} ${value}`);
  return next;
}

// 값 칸의 집계를 바꾼다((행 개수) 칩은 바꾸지 않는다). 집계 이름은 서버가 주는 함수 사전의 집계(SERVER.dash.aggs) 중에서 고른다.
function setAgg(series, i, agg) {
  return series.map((x, j) => (j === i && x.y ? { y: x.y, agg } : x));
}

// 칸의 현재 내용을 칩 글자로
const shelfChips = state => ({
  x: state.x ? [state.x] : [],
  y: (state.series ?? []).map(s => (s.y ? `${s.agg} (${s.y})` : "행 개수")),
  split: state.split ? [state.split] : [],
  filter: (state.conditions ?? []).map(c => c.line ?? (c.any ? c.any.map(x => x.line ?? x).join(" or ") : c)),  // 서버 모양 조건({line, quotes}·{any})도 글자로
});

// 선반 화면. host = { columns(): [[이름, 한글 타입]], state(): 지금 상태, change(next, why): 새 상태를 받는다(why는 어느 칸·어떤 컬럼인지 설명), say(text): 이유·결과 글 }.
// 돌려주는 값: { el, refresh() }. 칩을 끌어 놓거나(HTML5 drag & drop), 칩을 눌러 고른 뒤 칸을 누르면(누르기 대체) 같은 일을 한다.
function shelfPanel(host) {
  let held = null;  // 누르기 대체: 눌러서 고른 칩 { name, type }
  let pending = null;  // 필터 칸에 놓았고 값을 기다리는 컬럼 { name, type }
  const palette = el("div", { className: "shelf-cols" }), shelves = el("div", { className: "shelf-slots" }), msg = el("div", { className: "hint shelf-msg" });
  const KIND_CLASS = { num: "n", date: "d", bool: "t", text: "t" };
  const drop = async (shelf, name, type) => {
    const state = host.state(), check = dropCheck(shelf, name, type, state);
    if (!check.ok) return void host.say(check.reason);
    if (shelf === "filter") {  // 조건의 연산자와 값은 칸 안에서 받는다
      pending = { name, type };
      host.say(`‘${name}’의 조건을 정하세요(예: = 서울).`);
      return refresh();
    }
    host.say("");
    host.change(applyDrop(shelf, name, type, state), `${SHELVES[shelf]}에 ${name === SHELF_ROWS ? "행 개수" : name}`);
  };
  const chip = (name, type) => {
    const c = el("span", { className: "shelf-chip " + (name === SHELF_ROWS ? "n" : KIND_CLASS[shelfKind(type)]), textContent: name, draggable: true, title: name === SHELF_ROWS ? "개수를 셉니다" : type });
    c.ondragstart = e => { e.dataTransfer.setData("text/plain", JSON.stringify({ name, type })); e.dataTransfer.effectAllowed = "copy"; };
    c.onclick = () => { held = held?.name === name ? null : { name, type }; refresh(); host.say(held ? `‘${name}’을(를) 놓을 칸을 누르세요.` : ""); };
    if (held?.name === name) c.classList.add("held");
    return c;
  };
  // 필터 칸의 조건 입력: 컬럼 이름 + 연산자 + 값
  function filterForm() {
    const op = el("select", {}, ...["=", "!=", ">", ">=", "<", "<="].map(o => el("option", { value: o, textContent: o }))), value = el("input", { placeholder: "값", style: "width:90px" });
    const go = () => {
      if (!value.value.trim()) return void host.say("조건의 값을 적어 주세요.");
      const name = pending.name, next = applyDrop("filter", name, pending.type, host.state(), value.value.trim(), op.value);
      pending = null;
      host.say("");
      host.change(next, `필터에 ${name}`);
    };
    value.onkeydown = e => { if (e.key === "Enter" && !e.ctrlKey && !e.metaKey) go(); };
    value.onclick = op.onclick = e => e.stopPropagation();
    return el("span", { className: "shelf-form" }, el("b", { textContent: pending.name }), op, value, el("button", { className: "btn", textContent: "넣기", onclick: e => { e.stopPropagation(); go(); } }),
      el("button", { className: "ghost", textContent: "×", onclick: e => { e.stopPropagation(); pending = null; host.say(""); refresh(); } }));
  }
  function refresh() {
    palette.replaceChildren(chip(SHELF_ROWS, ""), ...host.columns().map(([n, t]) => chip(n, t)));
    const chips = shelfChips(host.state());
    shelves.replaceChildren(...Object.entries(SHELVES).map(([key, name]) => {
      const slot = el("div", { className: "shelf-slot" }, el("b", { textContent: name }), ...chips[key].map((text, i) => {
        const row = key === "y" ? host.state().series[i] : null, c = el("span", { className: "shelf-on", textContent: row?.y ? ` (${row.y})` : text });
        if (row?.y) {  // 값 칩: 집계를 고르는 칸 + 컬럼 이름
          const pick = el("select", { title: "집계(함수 사전의 집계 전부)" }, ...(SERVER.dash.aggs ?? []).map(a => el("option", { value: a, textContent: SERVER.dash.agg_names?.[a] ?? a, selected: a === row.agg })));
          pick.onclick = e => e.stopPropagation();
          pick.onchange = e => { const s = host.state(); host.change({ ...s, series: setAgg(s.series, i, e.target.value) }, "값 집계 바꾸기"); };
          c.prepend(pick);
        }
        const x = el("button", { className: "ghost", textContent: "×", title: "빼기", onclick: e => {
          e.stopPropagation();
          const s = host.state(), next = { ...s, series: [...(s.series ?? [])], conditions: [...(s.conditions ?? [])] };
          if (key === "x") next.x = ""; else if (key === "split") next.split = ""; else if (key === "y") next.series.splice(i, 1); else next.conditions.splice(i, 1);
          host.change(next, `${name}에서 빼기`);
        } });
        return el("span", { className: "shelf-wrap" }, c, x);
      }), key === "filter" && pending ? filterForm() : "", chips[key].length || (key === "filter" && pending) ? "" : el("span", { className: "hint", textContent: "여기에 놓기" }));
      slot.ondragover = e => { e.preventDefault(); slot.classList.add("over"); };
      slot.ondragleave = () => slot.classList.remove("over");
      slot.ondrop = e => {
        e.preventDefault();
        slot.classList.remove("over");
        let item = null;
        try { item = JSON.parse(e.dataTransfer.getData("text/plain")); } catch { /* 칩이 아닌 것을 놓음 */ }
        if (item?.name) drop(key, item.name, item.type);
      };
      slot.onclick = () => { if (held) { const h = held; held = null; drop(key, h.name, h.type); refresh(); } };
      return slot;
    }));
  }
  msg.hidden = true;
  const root = el("div", { className: "shelf" }, palette, shelves);
  refresh();
  return { el: root, refresh, drop, now: () => host.state() };
}
