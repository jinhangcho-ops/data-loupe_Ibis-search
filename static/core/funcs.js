// 함수 사전(sql_funcs.py)을 쓰는 화면 쪽 공통 부분. 모든 화면의 집계·계산 칸이 같은 목록(GET /api/funcs)에서 고른다.
// - Funcs.load(): 사전을 받는다(주소에 버전이 있어 브라우저가 버전마다 한 번만 받는다). 작은 목록(SERVER.agg_names·SERVER.merge)은 받기 전에도 쓴다.
// - Funcs.loadSupport(base, schema): 지금 연결이 각 함수를 하는지(ok·substitute·local·unavailable)와 안내 글. 고르는 칸이 이것으로 안내를 붙이거나 막는다.
// - Funcs.picker({ids, columns, onchange, repFirst}): 집계 고르는 칸 + 그 집계의 인자 칸(구분자·정렬 기준·N·분위수 P …) + 대표 값 고르기. 인자 칸은 사전의 args로 그린다.
// - Funcs.argFields(f, {columns, skip}): 함수 인자 칸들. Funcs.panel({columns, ids, onpick}): 찾기 패널(분류·이름·설명 검색, 예시 누르면 칸에 넣기, 안 맞는 컬럼 종류는 흐리게+이유).
// 서버 타입 이름(정수·실수·문자·날짜 …)과 사전의 종류(num·text·date …)는 KIND_OF로 잇는다. common.js의 el·api가 먼저 불려 있어야 한다.
const Funcs = (() => {
  const KIND_OF = { 정수: "num", 실수: "num", 문자: "text", 날짜: "date", 날짜시간: "date", "참/거짓": "bool", json: "json" };
  const REP_GROUP = "겹치는 값 중 하나 고르기", STATUS_MARK = { substitute: " · 대체 식", local: " · 이 PC에서 계산" };
  const kindOf = type => KIND_OF[type] ?? (["num", "text", "date", "bool", "json", "array", "struct"].includes(type) ? type : null) ?? (String(type).startsWith("목록") ? "array" : String(type).startsWith("구조") ? "struct" : "other");
  const fits = (kinds, kind) => kinds === "any" || [].concat(kinds).includes(kind);
  let cat = null, loading = null, support = {}, supportKey = "";

  async function load() {
    loading ??= api(`/api/funcs?v=${SERVER.funcs_ver ?? ""}`).then(c => { cat = c; cat.byId = Object.fromEntries(c.funcs.map(f => [f.id, f])); return cat; })
      .catch(e => { loading = null; throw e; });  // 실패한 약속을 붙들어 두면 다시 시도할 수 없다
    return loading;
  }
  const get = id => cat?.byId[cat.aliases[id] ?? id];
  // 지금 연결의 지원 상태. 테이블이 바뀌면 다시 묻는다(같은 테이블은 한 번만). 못 받으면 비워 두고 안내만 빠진다.
  async function loadSupport(base, schema = "") {
    if (!base) { support = {}; supportKey = ""; return support; }
    const key = `${schema}|${base}`;
    if (key === supportKey) return support;
    try { support = (await api("/api/funcs/support", { base, schema })).support; supportKey = key; } catch { support = {}; supportKey = ""; }
    return support;
  }
  const statusOf = id => support[cat?.aliases[id] ?? id] ?? { status: "ok", note: "" };

  // 함수가 필요로 하는 컬럼 종류가 지금 컬럼들에 있는지. 없으면 이유 글, 있으면 "".
  function misfit(f, cols) {
    const kinds = new Set(cols.map(c => kindOf(c[1])));
    for (const a of f.args) {
      if (a.kind !== "column" || a.optional || a.kinds === "any") continue;
      if (![].concat(a.kinds).some(k => kinds.has(k))) return `${[].concat(a.kinds).map(k => KIND_NAMES[k] ?? k).join("·")} 컬럼이 없어요`;
    }
    return "";
  }
  const KIND_NAMES = { num: "숫자", text: "글자", date: "날짜", bool: "참거짓", json: "JSON", array: "목록", struct: "구조" };

  // ---- 인자 칸 ----
  // 사전의 args 가운데 skip에 든 것(보통 x·where)을 뺀 칸들. 읽기 read(): {이름: 값}(비운 칸 제외), 쓰기 write(값들).
  function argFields(f, { columns = () => [], skip = ["where"], onchange = () => {} } = {}) {
    const inputs = {}, root = el("span", { className: "fp-args" });
    const colOptions = spec => columns().filter(c => fits(spec.kinds ?? "any", kindOf(c[1]))).map(c => el("option", { value: c[0], textContent: c[0] }));
    for (const a of f.args.filter(a => !skip.includes(a.name) && !["cases", "dict"].includes(a.kind))) {
      const title = a.label + (a.default != null && a.default !== "" ? ` (기본 ${Array.isArray(a.default) ? a.default.join(", ") : a.default})` : "");
      let box;
      if (a.kind === "column") {
        const select = el("select", { title }, el("option", { value: "", textContent: a.optional ? `(${a.label} 없음)` : a.label }), ...colOptions(a));
        inputs[a.name] = { node: select, read: () => select.value || undefined, write: v => { select.value = v ?? ""; } };
        box = select;
      } else if (a.kind === "order") {
        const select = el("select", { title }, el("option", { value: "", textContent: a.label }), ...columns().map(c => el("option", { value: c[0], textContent: c[0] })));
        const dir = el("select", { title: "방향" }, el("option", { value: "asc", textContent: "작은 것부터" }), el("option", { value: "desc", textContent: "큰 것부터" }));
        inputs[a.name] = { node: select, read: () => select.value ? [`${select.value} ${dir.value}`] : undefined,
          write: v => { const [col, d] = String(v?.[0] ?? "").split(" "); select.value = col ?? ""; dir.value = d ?? "asc"; } };
        box = el("span", { className: "fp-pair" }, select, dir);
        dir.onchange = onchange;
      } else if (a.kind === "choice") {
        const select = el("select", { title }, ...a.options.map(o => el("option", { value: o, textContent: o })));
        select.value = a.default ?? a.options[0];
        inputs[a.name] = { node: select, read: () => select.value === (a.default ?? a.options[0]) ? undefined : select.value, write: v => { select.value = v ?? a.default ?? a.options[0]; } };  // 기본값이면 보내지 않는다
        box = select;
      } else {
        const number = a.kind === "number" || a.kind === "int", list = a.kind === "list" || a.kind === "columns";
        const input = el("input", { title, placeholder: a.label + (a.default != null && a.default !== "" ? ` (${[].concat(a.default).join(", ")})` : ""), className: "fp-in" });
        if (number) Object.assign(input, { type: "number", step: a.kind === "int" ? "1" : "any" });
        inputs[a.name] = { node: input, read: () => {
          const v = input.value.trim();
          if (v === "") return undefined;
          if (number) return Number(v);
          if (!list) return v;
          const parts = v.split(",").map(x => x.trim()).filter(Boolean);
          return a.kind === "columns" || parts.some(x => Number.isNaN(Number(x))) ? parts : parts.map(Number);
        }, write: v => { input.value = Array.isArray(v) ? v.join(", ") : v ?? ""; } };
        box = input;
      }
      box.addEventListener("change", onchange);
      box.addEventListener("input", onchange);
      root.append(box);
    }
    const read = () => Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, i.read()]).filter(([, v]) => v !== undefined && v !== ""));
    const write = values => Object.entries(inputs).forEach(([k, i]) => i.write(values?.[k]));
    return { el: root, read, write, names: Object.keys(inputs), inputs };
  }

  // ---- 집계 고르기 ----
  // ids: 고를 수 있는 함수 id(사전의 화면별 목록). columns(): [[이름, 한글 타입]]. repFirst: 대표 값 고르기를 맨 위에(중복 제거).
  // 돌려주는 값: { el, get(): {agg, opts?}, set({agg, opts}), yMode(): required·optional·none, refresh(), select }
  function picker({ ids, columns = () => [], onchange = () => {}, repFirst = false, only = null }) {
    const reps = Object.fromEntries(Object.entries(cat.representative).map(([k, v]) => [v.func, v.label]));
    const select = el("select", { title: "집계 방식" }), note = el("span", { className: "hint fp-note" }), argBox = el("span");
    const root = el("span", { className: "fp" }, select, argBox, note);
    let fields = null;
    const pool = ids.filter(i => !only || only.includes(i));
    const shown = pool.filter(i => !(i in cat.aliases));
    const groupOf = id => reps[id] ? REP_GROUP : cat.categories.find(c => c.id === cat.byId[id].category)?.label ?? "";
    function build() {
      const groups = new Map();
      for (const id of shown) {
        const f = cat.byId[id], st = statusOf(id), g = groupOf(id);
        const option = el("option", { value: id, textContent: (reps[id] ?? f.label) + (STATUS_MARK[st.status] ?? ""), disabled: st.status === "unavailable", title: st.note || f.help });
        if (!groups.has(g)) groups.set(g, el("optgroup", { label: g }));
        groups.get(g).append(option);
      }
      const order = [...groups.keys()].sort((a, b) => (a === REP_GROUP ? (repFirst ? -1 : 1) : 0) - (b === REP_GROUP ? (repFirst ? -1 : 1) : 0));
      const now = select.value;
      select.replaceChildren(...order.map(g => groups.get(g)));
      if (now) select.value = now;
      if (!select.value) select.selectedIndex = 0;
    }
    function draw() {  // 고른 함수의 인자 칸을 새로 그린다
      const f = cat.byId[select.value], before = fields?.read();
      fields = argFields(f, { columns, skip: ["x", "where"], onchange: () => onchange(api_) });
      if (before && fields.names.some(n => n in before)) fields.write(before);
      argBox.replaceChildren(fields.el);
      const st = statusOf(f.id);
      note.textContent = st.note || "";
      note.title = f.help;
      note.classList.toggle("warn", st.status === "unavailable");
    }
    const api_ = {
      el: root, select,
      get: () => ({ agg: select.value, ...(Object.keys(fields.read()).length ? { opts: fields.read() } : {}) }),
      set: ({ agg, opts } = {}) => { select.value = cat.aliases[agg] && !shown.includes(agg) ? cat.aliases[agg] : agg ?? select.value; if (!select.value) select.selectedIndex = 0; draw(); fields.write(opts); },
      yMode: () => { const x = cat.byId[select.value].args.find(a => a.name === "x"); return !x ? "none" : x.optional ? "optional" : "required"; },
      refresh: () => { build(); draw(); },
      // 컬럼 목록 [[이름, 한글 타입]] 중 이 집계의 첫 컬럼 칸(x)이 받는 종류만
      xColumns: cols => { const x = cat.byId[select.value].args.find(a => a.name === "x"); return x ? cols.filter(c => fits(x.kinds ?? "any", kindOf(c[1]))) : []; },
    };
    select.onchange = () => { draw(); onchange(api_); };
    build(); draw();
    return api_;
  }

  // ---- 찾기 패널 ----
  // 분류·이름·설명으로 찾고, 예시를 누르면 onpick(f)을 부른다. 지금 컬럼에 맞는 종류가 없는 함수나 이 연결에서 못 쓰는 함수는 흐리게 + 이유(마우스를 올리면).
  function panel({ ids = null, columns = () => [], onpick }) {
    const search = el("input", { placeholder: "함수 찾기 (이름·설명·예: 중앙값, 날짜, split)", className: "fp-in" });
    const kind = el("select", { title: "분류" }, el("option", { value: "", textContent: "모든 분류" }), ...cat.categories.map(c => el("option", { value: c.id, textContent: c.label })));
    const list = el("div", { className: "fpanel-list" });
    const draw = () => {
      const q = search.value.trim().toLowerCase();
      const items = cat.funcs.filter(f => (!ids || ids.includes(f.id)) && (!kind.value || f.category === kind.value) &&
        (!q || [f.id, f.label, f.help, f.example].some(t => t.toLowerCase().includes(q))));
      list.replaceChildren(...items.map(f => {
        const why = misfit(f, columns()) || (statusOf(f.id).status === "unavailable" ? statusOf(f.id).note : ""), st = statusOf(f.id);
        return el("button", { className: "fpanel-item" + (why ? " dim" : ""), title: why || st.note || f.help, onclick: () => onpick(f) },
          el("b", { textContent: f.label }), el("code", { textContent: f.example }), el("span", { className: "hint", textContent: why || (st.note ? `${f.help} · ${st.note}` : f.help) }));
      }));
      if (!items.length) list.append(el("span", { className: "hint", textContent: "맞는 함수가 없어요." }));
    };
    search.oninput = kind.onchange = draw;
    draw();
    return { el: el("div", { className: "fpanel" }, el("div", { className: "row" }, search, kind), list), refresh: draw };
  }

  // 칸 차례의 낱말들(계산 줄의 본문): 첫 컬럼(x), 그 뒤 숫자·글자 칸. 값이 없는 뒤쪽 칸은 뺀다.
  function words(f, x, opts) {
    const specs = f.args.filter(a => !["x", "where", "order", "group"].includes(a.name)), vals = specs.map(a => opts[a.name]);
    let end = vals.length;
    while (end > 0 && vals[end - 1] === undefined) end--;
    return [...(x ? [x] : []), ...specs.slice(0, end).map((a, i) => String(vals[i] ?? a.default ?? ""))];
  }
  return { load, get, words, loadSupport, statusOf, misfit, kindOf, argFields, picker, panel, get cat() { return cat; } };
})();
