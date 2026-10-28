// 문장 보기(6단계): 탐색 문장 틀(서버 sentences.py, GET /api/sentences)의 빈칸을 채워 묻고, 질문 묶음으로 모아 한꺼번에 실행하고, 내 지표를 만들고 고친다.
// 위쪽은 화면 없이 시험하는 계산(문장 조각·빈칸 보임·기본값·컬럼 맞춤·기간 빈칸·결과 차트·카드 만들기)이고, 아래쪽 SentenceView가 화면이다.
// 문장은 Python 규칙(서버)만으로 검사하고 만든다. 값이 밖으로 나가지 않는다. 화면은 나누거나 합치지 않고 /api/sentence/run이 돌려준 끝난 표를 그린다.
// common.js의 el·api·busy·chip·fill·cell·esc, funcs.js의 Funcs, dash_add.js의 addToDashboard·chooseDashboard가 먼저 있어야 한다.
const SENT_KINDS = { num: "숫자", date: "날짜", text: "글자" };
// 문장 글의 {빈칸}을 [{text} | {slot}] 조각으로
function sentTokens(text) {
  const out = [];
  String(text).split(/(\{[^{}]+\})/).forEach(part => { if (part) out.push(/^\{[^{}]+\}$/.test(part) ? { slot: part.slice(1, -1) } : { text: part }); });
  return out;
}
// 다른 빈칸의 값에 따라 나타나는 곁가지 칸(when)이 지금 보이는지
const sentVisible = (slot, slots) => Object.entries(slot.when ?? {}).every(([k, values]) => values.includes(slots[k]));
const sentFits = (kinds, kind) => kinds === "any" || kinds == null || [].concat(kinds).includes(kind);
// 컬럼 칩 색 이름: 숫자·날짜·글자, 나머지는 other
const sentChip = kind => (kind in SENT_KINDS ? kind : "other");
// 틀의 빈칸 기본값(기간은 따로 다룬다)
function sentDefaults(tpl) {
  const out = {};
  tpl.slots.forEach(s => {
    if (s.type === "text_match") out[s.name] = { match: s.default ?? s.options[0], text: "" };
    else if (s.default != null) out[s.name] = s.default;
    else if (["choice", "unit"].includes(s.type) && s.options.length) out[s.name] = s.options[0];
  });
  return out;
}
// 컬럼 빈칸 값에서 컬럼 이름
const sentColName = v => (v && typeof v === "object" ? v.column : v);
// 정렬 칸 값("컬럼 asc|desc")의 컬럼: names에서 가장 길게 맞는 이름(공백 있는 컬럼, 서버 flow.head_name과 같은 기준), 없으면 첫 낱말
const sentOrderCol = (v, names) => names.filter(c => v === c || v.startsWith(c + " ")).sort((a, b) => b.length - a.length)[0] ?? v.split(/\s+/)[0];
// 다른 테이블에서 열 때: 같은 이름 컬럼만 채우고 없는 컬럼의 칸은 비운다(질문 묶음 불러오기). columns = {컬럼: 종류}
function sentFitColumns(tpl, slots, columns) {
  const out = { ...slots }, gone = [];
  const has = c => c in columns;
  tpl.slots.forEach(s => {
    const v = out[s.name];
    if (v == null) return;
    if (s.type === "column" && !has(sentColName(v))) { delete out[s.name]; gone.push(s.name); }
    else if (s.type === "columns") { const keep = v.filter(has); if (keep.length !== v.length) { gone.push(s.name); if (keep.length) out[s.name] = keep; else delete out[s.name]; } }
    else if (s.type === "order" && !has(sentOrderCol(String(v), Object.keys(columns)))) { delete out[s.name]; gone.push(s.name); }
  });
  return { slots: out, gone };
}
// 기간 칸 값: 날짜 컬럼 + 읽은 기간({start, end}, 이번 주·달·올해면 to_date도). 읽지 못했거나 컬럼이 없으면 null
const sentPeriod = (column, span) => (column && span && !span.error && (span.start || span.end) ? { column, start: span.start ?? "", end: span.end ?? "", ...(span.to_date ? { to_date: span.to_date } : {}) } : null);
// 틀 목록 걸러내기: 번호·이름·문장에 들어 있는 글자
const sentFilter = (templates, query) => { const q = String(query ?? "").trim().toLowerCase(); return templates.filter(t => !q || `${t.no} ${t.name} ${t.text}`.toLowerCase().includes(q)); };
// {컬럼: 종류} ← [[이름, 화면 타입]] (Funcs.kindOf가 서버 타입 이름을 종류로 바꾼다)
const sentKinds = (cols, kindOf) => Object.fromEntries(cols.map(([name, type]) => [name, kindOf(type)]));
// 질문 묶음 저장 모양
const sentSetOf = (shared, period, items) => ({ shared, period, items: items.map(i => ({ template: i.template, slots: i.slots, period: i.period })) });
// 끝난 표 → Chart.js 설정: 첫 글자·날짜 컬럼이 라벨, 숫자 컬럼이 계열. 그릴 수 없으면 null. 라벨이 날짜처럼 보이면 선, 아니면 막대.
function sentChart(result, colors = []) {
  const { columns, rows } = result;
  if (!rows?.length || !columns?.length) return null;
  const isNum = c => rows.every(r => r[c] == null || typeof r[c] === "number");
  const nums = columns.filter(isNum), label = columns.find(c => !isNum(c)) ?? (nums.length > 1 ? columns[0] : null);
  const values = nums.filter(c => c !== label);
  if (!values.length) return null;
  const shown = rows.slice(0, 50), dateLike = shown.every(r => /^\d{4}-\d{2}/.test(String(r[label] ?? "")));
  return { type: dateLike ? "line" : "bar", data: { labels: shown.map(r => (label ? String(r[label] ?? "") : "")), datasets: values.map((c, i) => ({ label: c, data: shown.map(r => r[c]), backgroundColor: colors[i % (colors.length || 1)] ?? "#00968f", borderColor: colors[i % (colors.length || 1)] ?? "#00968f", tension: 0.3 })) },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: values.length > 1 } } }, truncated: rows.length > shown.length };
}
// 컴파일된 문장(/api/sentence/compile의 runs) → 대시보드 카드의 본문. 묶어 집계하는 요청 하나만 차트 카드로 만든다. 못 만들면 { reason }.
function sentCard(compiled, title) {
  if (!compiled.ok) return { reason: compiled.reason };
  if (compiled.combine || compiled.runs.length !== 1) return { reason: "여러 계산을 합쳐 구하는 문장이라 카드 하나로 바로 넣을 수 없어요." };
  const run = compiled.runs[0], spec = run.body.pivot;
  if (run.route !== "/api/pivot" || !spec?.rows?.length) return { reason: "묶어 집계하는 문장만 차트 카드로 넣을 수 있어요(행을 보는 문장은 제외)." };
  if (spec.rows.length > 2) return { reason: "세 컬럼 이상으로 묶는 문장이라 차트 카드(X·나누기 두 칸)로 그대로 넣을 수 없어요." };
  if (spec.having || spec.top || spec.subtotals || spec.where) return { reason: "묶음을 거르거나(조건·상위 N) 소계를 붙이는 문장이라 차트 카드로 그대로 넣을 수 없어요." };  // 차트 카드에는 이 설정을 담을 칸이 없다
  const { pivot, ...body } = run.body;
  return { kind: "chart", title, body: { ...body, x: spec.rows[0], split: spec.rows[1] ?? "", order: spec.order?.endsWith(" desc") ? "value" : "", series: [{ y: spec.value ?? null, agg: spec.how, ...(spec.opts ? { opts: spec.opts } : {}) }] } };
}

// ---------- 화면 ----------
// 작은 확인 창(제목, 글, 확인 단추 이름) → true/false
// 지표를 쓰는 곳: 대시보드 카드("제목 (대시보드)")와 그 지표를 쓰는 지표 이름. 고치기·지우기가 함께 쓴다
async function sentUsedBy(id) {
  const used = await api("/api/my_metrics/used_by", { id });
  return { cards: used.cards.map(c => `${c.title} (${c.dashboard_name})`), metrics: used.dependents.map(x => x.name) };
}
function sentAsk(title, text, ok = "확인") {
  return new Promise(done => {
    const close = v => () => { box.remove(); done(v); };
    const box = el("div", { className: "modal", onclick: e => { if (e.target === box) close(false)(); } }, el("div", { className: "modal-box" }, el("h3", { textContent: title }),
      el("p", { textContent: text, style: "white-space:pre-wrap; margin:10px 0 14px" }), el("div", { className: "row", style: "justify-content:flex-end" },
        el("button", { className: "ghost", textContent: "취소", onclick: close(false) }), el("button", { className: "btn p", textContent: ok, onclick: close(true) }))));
    document.body.append(box);
  });
}
const sentHook = (box, env) => typeof showMetricCatalog === "function" && showMetricCatalog(box, env);  // 지표 사전(7단계, metric_catalog.js) 화면이 이 이름의 함수를 정의하면 #metric-catalog에 붙는다. env = 화면이 쓰는 함수들

// SentenceView(host, ctx): ctx = { body(): 지금 검색 body, columns(): [[이름, 화면 타입]], tables(): 테이블 이름들, scope?(): {base, schema}(없으면 쿼리 화면의 S), only?: "metric"(탭 없이 지표 화면만: 대시보드) }
function SentenceView(host, ctx) {
  let cat = null, explore = [], metricTpl = [];  // 문장 틀: 처음 열 때 /api/sentences로 한 번 받는다
  let tab = ctx.only ?? "explore", items = [], seq = 0, sets = {}, current = "", shared = { on: false, column: "", text: "", span: null }, source = null;  // source: 지금 연결 {key, label}
  const scope = () => ctx.scope?.() ?? S;  // 기준 테이블·스키마: 쿼리 화면은 S, 대시보드는 고른 테이블
  const inScope = m => sameSource(m.scope?.key, source?.key) && (m.scope.schema ?? "") === (scope().schema ?? "") && (m.scope.table ?? "") === (scope().base ?? "");  // 이 연결·스키마·테이블의 지표인가(서버 my_metrics.scope_id와 같은 기준)
  const kinds = () => sentKinds(ctx.columns(), Funcs.kindOf);
  const colList = () => ctx.columns().map(c => [c[0], Funcs.kindOf(c[1])]);
  const tabs = el("div", { className: "stabs" }, ...[["explore", "탐색"], ["metric", "지표"]].map(([k, t]) => { const b = el("button", { className: "tab", textContent: t, onclick: () => { tab = k; show(); } }); b.dataset.tab = k; return b; }));
  const body = el("div", { className: "sbody" });
  host.replaceChildren(...(ctx.only ? [] : [tabs]), body);

  // ----- 빈칸 하나 -----
  // 컬럼 고르기 칸: 눌러서 고르거나(select) 위쪽 컬럼 칩을 끌어다 놓는다
  function dropTo(node, on) {
    node.addEventListener("dragover", e => { e.preventDefault(); node.classList.add("over"); });
    node.addEventListener("dragleave", () => node.classList.remove("over"));
    node.addEventListener("drop", e => { e.preventDefault(); node.classList.remove("over"); const c = e.dataTransfer.getData("text/column"); if (c) on(c); });
  }
  function colSelect(s, value, on) {
    const opts = colList().filter(([, k]) => sentFits(s.kinds, k)).map(([c]) => c), box = el("span", { className: "slot drop", title: "컬럼 칩을 끌어다 놓거나 눌러서 고르세요" });
    const sel = el("select", {}, el("option", { value: "", textContent: s.label }), ...opts.map(c => el("option", { value: c, textContent: c })));
    sel.value = opts.includes(value) ? value : "";
    sel.onchange = () => on(sel.value);
    dropTo(box, c => { if (opts.includes(c)) { sel.value = c; on(c); } });
    box.append(sel);
    return box;
  }
  function toggle(options, value, on) {
    const box = el("span", { className: "seg" });
    const draw = v => box.replaceChildren(...options.map(o => el("button", { className: "seg-b" + (o === v ? " on" : ""), textContent: o, onclick: () => { on(o); draw(o); } })));
    draw(value);
    return box;
  }
  function slotEl(s, item, redo) {
    const slots = item.slots, set = v => { if (v === "" || v == null || (Array.isArray(v) && !v.length)) delete slots[s.name]; else slots[s.name] = v; redo(); };
    const v = slots[s.name], type = s.type;
    let node;
    if (type === "column") {
      const name = sentColName(v), prep = v && typeof v === "object" ? v.prep : "";
      const main = colSelect(s, name, c => set(c ? (prep ? { column: c, prep, ...(v?.n ? { n: v.n } : {}) } : c) : ""));
      node = el("span", { className: "slot-group" }, main);
      if (name && ["any", "text"].includes(s.kinds)) {
        const p = el("select", { className: "prep", title: "컬럼을 다듬어서 써요 (새 컬럼으로 계산해요)" }, el("option", { value: "", textContent: "그대로" }), ...Object.entries(cat.preps).map(([k, t]) => el("option", { value: k, textContent: t })));
        p.value = prep ?? "";
        p.onchange = () => set(p.value ? { column: name, prep: p.value, ...(p.value === "first_n" ? { n: 3 } : {}) } : name);
        node.append(p);
        if (prep === "first_n") node.append(el("input", { className: "num", type: "number", min: 1, value: v.n ?? 3, title: "글자 수", onchange: e => set({ ...v, n: +e.target.value }) }));
      }
    } else if (type === "columns") {
      const list = v ?? [], add = colSelect({ ...s, label: "+ 컬럼" }, "", c => c && !list.includes(c) && set([...list, c]));
      node = el("span", { className: "slot-group" }, ...list.map(c => chip(c, () => set(list.filter(x => x !== c)))), add);
    } else if (type === "order") {
      const text = String(v ?? ""), col = sentOrderCol(text, ctx.columns().map(c => c[0])), dir = text.slice(col.length).trim() || "asc";
      node = el("span", { className: "slot-group" }, colSelect({ ...s, kinds: "any" }, col, c => set(c ? `${c} ${dir}` : "")), toggle(["asc", "desc"], dir, d => col && set(`${col} ${d}`)));
    } else if (type === "agg") {
      const picker = Funcs.picker({ ids: Funcs.cat.screens.sentence?.length ? Funcs.cat.screens.sentence.filter(i => Funcs.cat.byId[i].aggregate) : Funcs.cat.screens.chart.filter(i => Funcs.cat.byId[i].aggregate), columns: colList, onchange: p => { slots[s.name] = p.get(); redo(); } });
      if (v) picker.set(v); else slots[s.name] = picker.get();
      node = picker.el;
    } else if (type === "transform") {
      const ids = Funcs.cat.funcs.filter(f => !(f.aggregate || f.window || f.table || f.raw)).map(f => f.id);
      const func = el("select", {}, el("option", { value: "", textContent: "다듬을 함수" }), ...ids.map(i => el("option", { value: i, textContent: Funcs.cat.byId[i].label })));
      func.value = v?.func ?? "";
      node = el("span", { className: "slot-group" }, func);
      const draw = () => {
        node.replaceChildren(func);
        if (!func.value) return;
        const fields = Funcs.argFields(Funcs.cat.byId[func.value], { columns: colList, skip: ["x", "where"], onchange: () => { slots[s.name] = { func: func.value, args: fields.read() }; redo(); } });
        fields.write(v?.args);
        node.append(fields.el);
      };
      func.onchange = () => { slots[s.name] = func.value ? { func: func.value, args: {} } : undefined; if (!func.value) delete slots[s.name]; draw(); redo(); };
      draw();
    } else if (["choice", "unit"].includes(type)) node = toggle(s.options, v ?? s.options[0], o => { slots[s.name] = o; redo(true); });
    else if (type === "text_match") {
      const m = v ?? { match: s.options[0], text: "" };
      node = el("span", { className: "slot-group" }, toggle(s.options, m.match, o => { slots[s.name] = { ...(slots[s.name] ?? m), match: o }; redo(); }),
        el("input", { value: m.text, placeholder: "글자", onchange: e => { slots[s.name] = { ...(slots[s.name] ?? m), text: e.target.value }; redo(); } }));
    } else if (["n", "number"].includes(type)) node = el("input", { className: "num", type: "number", min: type === "n" ? 1 : undefined, step: type === "n" ? 1 : "any", value: v ?? "", placeholder: s.label, onchange: e => set(e.target.value === "" ? "" : Number(e.target.value)) });
    else if (type === "values") node = el("input", { value: (v ?? []).join(", "), placeholder: "값1, 값2", onchange: e => set(e.target.value.split(",").map(x => x.trim()).filter(Boolean)) });
    else if (type === "table") { const sel = el("select", {}, el("option", { value: "", textContent: s.label }), ...ctx.tables().map(t => el("option", { value: t, textContent: t }))); sel.value = v ?? ""; sel.onchange = () => set(sel.value); node = sel; }
    else if (type === "metric") node = metricSelect(v, set);
    else node = el("input", { value: v ?? "", placeholder: type === "cond" ? "예: 금액 >= 1000 / 상태 ends 완료" : s.label, className: type === "cond" ? "wide" : "", onchange: e => set(e.target.value.trim()) });
    const wrap = el("span", { className: "slot-wrap", title: s.label }, node);
    wrap.dataset.slot = s.name;
    return wrap;
  }
  let metricList = [];
  function metricSelect(value, on) {
    const sel = el("select", {}, el("option", { value: "", textContent: "지표 고르기" }), ...metricList.map(m => el("option", { value: m.id, textContent: m.text, disabled: !m.usable, title: m.usable ? "" : "다른 테이블의 지표예요. [지표] 탭에서 이 테이블에 맞춰 복사하세요." })));
    sel.value = value ?? ""; sel.onchange = () => on(sel.value);
    return sel;
  }
  async function loadMetrics() {
    try {
      const d = await api("/api/my_metrics");
      source = d.source;
      metricList = [...d.metrics.map(m => ({ id: m.id, text: m.text, usable: inScope(m) })), ...d.legacy.map(m => ({ id: m.name, text: `${m.name} (지표 파일)`, usable: true }))];
      return d;
    } catch { metricList = []; return null; }
  }

  // ----- 문장 하나 -----
  // 문장 검사·실행에 보낼 빈칸들: 묶음의 공유 기간이나 이 문장의 기간을 {기간} 칸에 넣는다
  function withPeriod(item) {
    const slots = { ...item.slots }, own = shared.on ? sentPeriod(shared.column, shared.span) : sentPeriod(item.period?.column, item.period?.span);
    if (own) slots["기간"] = own;
    return slots;
  }
  function sentenceCard(item) {
    const tpl = cat.templates.find(t => t.id === item.template), card = el("div", { className: "scard" });
    const issues = el("div", { className: "sissues" }), out = el("div", { className: "sresult" }), line = el("div", { className: "sline" }), preview = el("div", { className: "hint spreview" });
    let timer = 0, bad = true;
    function recheck() {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        try {
          const d = await api("/api/sentence/check", { template: item.template, slots: withPeriod(item), columns: kinds() });
          bad = !d.ok;
          preview.textContent = d.text;
          const shown = item.touched ? d.issues : [];  // 막 고른 문장에는 "칸을 채워 주세요"를 늘어놓지 않는다. 칸을 하나라도 고치면 보인다([실행]은 그동안 꺼져 있다)
          issues.replaceChildren(...shown.map(i => el("div", { className: "sissue " + i.level, textContent: i.text })));
          line.querySelectorAll("[data-slot]").forEach(n => n.classList.toggle("bad", shown.some(i => i.level === "bad" && i.slot === n.dataset.slot)));
        } catch (e) { bad = true; issues.replaceChildren(el("div", { className: "sissue bad", textContent: e.message })); }
        const periodBad = (shared.on ? shared.text && shared.span?.error : item.period?.text && item.period?.span?.error);
        if (periodBad) bad = true;
        card.querySelector(".srun").disabled = bad;
      }, 200);
    }
    const draw = () => {
      line.replaceChildren(...sentTokens(tpl.text).map(t => t.text ? el("span", { className: "stext", textContent: t.text }) : (() => { const s = tpl.slots.find(x => x.name === t.slot); return s ? slotEl(s, item, redo) : el("span", { textContent: `{${t.slot}}` }); })()),
        ...tpl.slots.filter(s => !new RegExp(`\\{${s.name}\\}`).test(tpl.text) && sentVisible(s, item.slots)).map(s => slotEl(s, item, redo)));
      line.querySelectorAll("[data-slot]").forEach(n => { const s = tpl.slots.find(x => x.name === n.dataset.slot); if (s && !sentVisible(s, item.slots)) n.classList.add("hidden"); });
    };
    function redo(rebuild) { item.touched = true; if (rebuild || tpl.slots.some(s => Object.keys(s.when ?? {}).length)) draw(); recheck(); }
    const run = el("button", { className: "btn srun", textContent: "실행", disabled: true, onclick: e => busy(e.currentTarget, () => runOne(item, out)).finally(recheck) });  // 실행 중에 칸을 고쳤으면 busy가 켠 단추를 다시 맞춘다
    card.append(el("div", { className: "shead" }, el("b", { textContent: `${tpl.no}. ${tpl.name}` }), el("span", { className: "grow" }), run,
      el("button", { className: "ghost", textContent: "×", title: "이 문장 빼기", onclick: () => { items = items.filter(i => i !== item); showExplore(); } })),
    line, sentPeriodRow(item, () => recheck()), preview, issues, out);
    draw(); recheck();
    item.recheck = recheck;
    if (item.result) showResult(out, item.result);  // 다시 그릴 때 구한 결과를 남긴다
    return card;
  }
  // 기간 한 줄: 날짜 컬럼 + 자유로 쓴 기간(읽은 결과를 바로 보인다). 묶음 공유일 때는 보이지 않는다.
  function sentPeriodRow(item, again) {
    item.period ??= { column: colList().find(c => c[1] === "date")?.[0] ?? "", text: "", span: null };
    const row = el("div", { className: "speriod" }), p = item.period;
    const draw = () => {
      if (shared.on) { row.replaceChildren(el("span", { className: "hint", textContent: "기간은 묶음에서 정한 기간을 함께 써요." })); return; }
      row.replaceChildren(periodInputs(p, again));
    };
    item.drawPeriod = draw;
    draw();
    return row;
  }
  function periodInputs(p, again) {
    const col = pickCol("date", p.column, v => { p.column = v; again(); }), fb = el("span", { className: "hint pfb" }), input = el("input", { value: p.text, placeholder: "기간 (예: 최근 7일, 이번 달, 2026-01 ~ 2026-03)", title: "비우면 기간 없이 구해요" });
    const live = async () => {
      p.text = input.value.trim();
      if (!p.text) { p.span = null; p.pending = false; fb.textContent = ""; fb.classList.remove("err"); again(); return; }
      const text = p.text;
      try { const d = await api("/api/period/parse", { text }); if (text !== p.text) return; p.span = d.span; p.pending = false; fb.textContent = d.span.error || `${d.span.label}${d.span.days ? ` (${d.span.days}일)` : ""}`; fb.classList.toggle("err", !!d.span.error); } catch (e) { if (text !== p.text) return; p.pending = false; fb.textContent = e.message; fb.classList.add("err"); }
      again();
    };
    input.oninput = (() => { let t = 0; return () => { p.pending = true; clearTimeout(t); t = setTimeout(live, 250); }; })();  // pending: 읽는 동안 [실행]·[계산]이 예전 기간으로 구하지 않게
    if (p.text) { p.pending = true; live(); }  // 저장한 묶음을 불러온 때도 읽기 전에는 실행하지 않는다
    return el("span", { className: "slot-group" }, el("span", { className: "stext", textContent: "기간" }), col, input, fb);
  }
  const pickCol = (kind, value, on, label = "날짜 컬럼") => {
    const opts = colList().filter(([, k]) => !kind || k === kind).map(c => c[0]);
    const sel = el("select", { title: label }, el("option", { value: "", textContent: label }), ...opts.map(c => el("option", { value: c, textContent: c })));
    sel.value = opts.includes(value) ? value : ""; sel.onchange = () => on(sel.value);
    return sel;
  };

  // ----- 실행과 결과 -----
  async function compileRun(item) {
    return { template: item.template, slots: withPeriod(item), body: ctx.body(), columns: kinds() };
  }
  async function runOne(item, out) {
    if ((shared.on ? shared : item.period)?.pending) { out.replaceChildren(el("div", { className: "sissue bad", textContent: "기간을 읽는 중이에요. 잠시 뒤 다시 누르세요." })); return; }
    out.replaceChildren(el("span", { className: "hint", textContent: "구하는 중…" }));
    try {
      const d = await api("/api/sentence/run", await compileRun(item));
      showResult(out, d);
      item.result = d;
    } catch (e) { out.replaceChildren(el("div", { className: "sissue bad", textContent: e.message })); }
  }
  function showResult(out, d) {
    out.chartObj?.destroy();  // 이전 결과의 차트
    out.chartObj = null;
    const colors = [1, 2, 3, 4, 5, 6, 7, 8].map(i => getComputedStyle(document.documentElement).getPropertyValue("--c" + i).trim()), cfg = sentChart(d, colors);
    const table = el("div", { className: "table-wrap" });
    resultTable(table, d);
    const box = el("div", { className: "schart hidden" }, el("canvas"));
    let chartObj = null;
    const switchTo = view => {
      switcher?.querySelectorAll(".seg-b").forEach(b => b.classList.toggle("on", b.dataset.view === view));
      table.classList.toggle("hidden", view === "chart"); box.classList.toggle("hidden", view !== "chart");
      if (view === "chart" && !chartObj && cfg) {
        if (typeof Chart === "undefined") need(CHART_JS).then(() => switchTo(view));  // 첫 차트
        else chartObj = out.chartObj = new Chart(box.querySelector("canvas"), cfg);
      }
    };
    const switcher = cfg ? el("div", { className: "seg" }, ...[["table", "표"], ["chart", "차트"]].map(([k, t]) => { const b = el("button", { className: "seg-b" + (k === "table" ? " on" : ""), textContent: t, onclick: () => switchTo(k) }); b.dataset.view = k; return b; })) : "";
    out.replaceChildren(d.text ? el("div", { className: "stext-result", textContent: d.text }) : "", switcher,
      table, box, ...d.notes.map(n => el("div", { className: "hint", textContent: n })),
      d.more ? el("div", { className: "hint warn", textContent: `결과가 더 있어요. 처음 ${d.rows.length.toLocaleString()}행만 보여요. 기간이나 조건을 좁혀 보세요.` } ) : "",
      cfg?.truncated ? el("div", { className: "hint", textContent: "차트는 앞 50개만 그려요." }) : "");
  }

  // ----- 탐색 탭 -----
  function newItem(tid) {
    const tpl = cat.templates.find(t => t.id === tid), item = { id: ++seq, template: tid, slots: sentDefaults(tpl), period: null };
    return item;
  }
  async function loadSets() { try { sets = (await api("/api/question_sets")).sets; } catch { sets = {}; } }
  function chipsBar() {
    return el("div", { className: "scols" }, el("span", { className: "hint", textContent: "컬럼을 문장의 빈칸에 끌어다 놓아도 돼요" }),
      ...ctx.columns().map(([c, t]) => { const k = sentChip(Funcs.kindOf(t)); const n = el("span", { className: `ccol ${k}`, textContent: c, draggable: true, title: t }); n.addEventListener("dragstart", e => e.dataTransfer.setData("text/column", c)); return n; }));
  }
  function setBar() {
    const sel = el("select", {}, el("option", { value: "", textContent: "저장한 질문 묶음 고르기" }), ...Object.keys(sets).map(n => el("option", { value: n, textContent: n })));
    sel.value = current in sets ? current : "";
    const name = el("input", { placeholder: "묶음 이름", value: current });
    const err = el("span", { className: "err" });
    sel.onchange = () => {
      const s = sets[sel.value];
      if (!s) return;
      current = sel.value; name.value = current;
      const cols = kinds(), notes = [];
      items = s.items.map(i => { const r = sentFitColumns(cat.templates.find(t => t.id === i.template), i.slots ?? {}, cols); if (r.gone.length) notes.push(`${cat.templates.find(t => t.id === i.template).name}: 이 테이블에 없는 컬럼이라 ${r.gone.join(", ")} 칸을 비웠어요.`); return { id: ++seq, template: i.template, slots: r.slots, period: i.period ? { ...i.period, span: null } : null, touched: true }; });
      shared = { on: !!s.shared, column: s.period?.column ?? "", text: s.period?.text ?? "", span: null };
      showExplore(); if (notes.length) body.querySelector(".sset-note").textContent = notes.join(" / ");
    };
    const save = el("button", { className: "btn", textContent: "저장", onclick: e => busy(e.currentTarget, async () => {
      try { const n = name.value.trim() || current; sets = (await api("/api/question_sets", { name: n, set: sentSetOf(shared.on, { column: shared.column, text: shared.text }, items.map(i => ({ ...i, period: i.period && { column: i.period.column, text: i.period.text } }))) })).sets; current = n; err.textContent = ""; ctx.saved?.(); showExplore(); } catch (er) { err.textContent = er.message; }
    }) });
    const del = el("button", { className: "btn", textContent: "삭제", onclick: e => busy(e.currentTarget, async () => { if (!sel.value) return; try { sets = (await api("/api/question_sets", { name: sel.value, set: null })).sets; current = ""; showExplore(); } catch (er) { err.textContent = er.message; } }) });
    const share = el("input", { type: "checkbox", checked: shared.on, style: "width:auto" });
    share.onchange = () => { shared.on = share.checked; showExplore(); };
    const sharedRow = shared.on ? periodInputs(shared, () => items.forEach(i => i.recheck?.())) : "";
    return el("div", { className: "sset" }, el("div", { className: "row" }, sel, name, save, del, err), el("div", { className: "hint sset-note" }),
      el("label", { className: "hint", title: "끄면 문장마다 기간을 따로 정해요" }, share, " 모든 문장에 같은 기간 쓰기"), sharedRow);
  }
  function showExplore() {
    const list = el("div", { className: "stpl" }), filter = el("input", { placeholder: "문장 찾기 (번호나 낱말)" });
    const draw = () => list.replaceChildren(...sentFilter(explore, filter.value).map(t => el("button", { className: "tpl", title: t.text, onclick: () => { items.push(newItem(t.id)); showExplore(); } }, el("b", { textContent: t.no }), " " + t.name)));
    filter.oninput = draw; draw();
    const cards = el("div", { className: "scards" }, ...(items.length ? items.map(sentenceCard) : [el("div", { className: "hint", textContent: "왼쪽에서 문장을 고르면 여기에 질문이 생겨요. 여러 질문을 모아 한꺼번에 실행할 수 있어요." })]));
    const all = el("button", { className: "btn p", textContent: "모두 실행", disabled: !items.length, onclick: e => busy(e.currentTarget, async () => { for (const c of cards.querySelectorAll(".srun")) if (!c.disabled) c.click(); }) });
    const dash = el("button", { className: "btn", textContent: "대시보드에 모두 추가", disabled: !items.length, onclick: e => busy(e.currentTarget, () => addAll(note)) });
    const note = el("div", { className: "sadd-note" });
    // 묶음 공유 기간이 읽힌 결과를 문장 검사에 알린다
    body.replaceChildren(el("div", { className: "sgrid" }, el("div", { className: "sleft" }, filter, list), el("div", { className: "sright" }, setBar(), chipsBar(), cards, el("div", { className: "row" }, all, dash), note)));
  }
  async function addAll(note) {
    if (shared.pending || items.some(i => i.period?.pending)) { note.textContent = "기간을 읽는 중이에요. 잠시 뒤 다시 누르세요."; return; }
    const done = [], skipped = [];
    let pick = null, table = { schema: scope().schema, base: scope().base };
    for (const item of items) {
      const compiled = await api("/api/sentence/compile", await compileRun(item)).catch(e => ({ ok: false, reason: e.message })), tpl = cat.templates.find(t => t.id === item.template);
      const card = sentCard(compiled, `${tpl.name}: ${compiled.text ?? tpl.text}`.slice(0, 100));
      if (card.reason) { skipped.push(`${tpl.name}: ${card.reason}`); continue; }
      try {
        pick ??= await chooseDashboard();
        if (!pick) { note.textContent = "취소했어요."; return; }
        const query = card.body, added = await addToDashboard({ kind: "chart", title: card.title, colors: { series: {}, parts: {}, order: [], parts_order: [] }, source: { schema: query.schema || "", table: query.base, body: query }, data: {}, view: { type: "", ratio: 16 / 9 } }, "chart", table, pick);
        if (added) { done.push(tpl.name); pick = { id: added.id, name: added.name }; note.dataset.added = added.id; }
      } catch (e) { skipped.push(`${tpl.name}: ${e.message}`); }
    }
    note.replaceChildren(el("div", { className: done.length ? "ok" : "err", textContent: done.length ? `대시보드에 ${done.length}개를 넣었어요.` : "넣은 문장이 없어요." }), ...skipped.map(t => el("div", { className: "hint", textContent: "넣지 못함, " + t })),
      done.length ? el("a", { href: "/dashboard?open=" + encodeURIComponent(note.dataset.added), textContent: "대시보드에서 보기" }) : "");
  }

  // ----- 지표 탭 -----
  async function showMetric() {
    const d = await loadMetrics();
    if (!d) { body.replaceChildren(el("div", { className: "err", textContent: "내 지표를 불러오지 못했어요." })); return; }
    const catalog = el("div", { id: "metric-catalog", className: "mcatalog" });  // 지표 사전(7단계) 화면이 여기에 붙는다
    const mine = el("div", { className: "mlist" }), form = el("div", { className: "mform" }), msg = el("div", { className: "err" });
    body.replaceChildren(el("div", { className: "sgrid" }, el("div", { className: "sleft" }, el("h2", { textContent: "지표 만들기" }), ...metricTpl.map(t => el("button", { className: "tpl", title: t.text, onclick: () => editMetric(null, t.id) }, el("b", { textContent: t.no }), " " + t.name))),
      el("div", { className: "sright" }, el("h2", { textContent: "내 지표" }), mine, form, msg, el("h2", { textContent: "지표 사전" }), catalog)));
    await sentHook(catalog, { ctx, kinds, slotEl, periodInputs, scope, refresh: showMetric });
    let status = {};
    try { status = (await api("/api/my_metrics/status", { scope: { ...scopeNow() }, columns: kinds() })).status; } catch { /* 구조 확인은 없어도 목록은 보인다 */ }
    const ids = new Set(d.metrics.map(m => m.id)), names = Object.fromEntries(d.metrics.map(m => [m.id, m.name]));
    const row = m => {
      const st = status[m.id], tpl = cat.templates.find(t => t.id === m.template) ?? (typeof mcTemplate === "function" ? mcTemplate(m.template) : null), usable = inScope(m);
      return el("div", { className: "mrow" + (usable ? "" : " other") },
        el("div", { className: "row" }, el("b", { textContent: m.text }), st && !st.usable ? el("span", { className: "fbadge warn", textContent: "쓸 수 없음", title: st.reason }) : "", el("span", { className: "grow" }),
          usable ? (tpl?.slots ? el("button", { className: "btn", textContent: "고치기", onclick: () => editMetric(m) }) : "")  /* 지표 사전 항목은 사전에서 새로 저장한다 */ : el("button", { className: "btn", textContent: "이 테이블에 맞춰 복사", onclick: () => copyMetric(m) }),
          el("button", { className: "ghost", textContent: "삭제", onclick: e => busy(e.currentTarget, () => removeMetric(m)) })),
        el("div", { className: "hint", textContent: tpl ? sentRender(tpl, m.slots, names) : m.template }),
        st && !st.usable ? el("div", { className: "err", textContent: st.reason }) : "",
        (m.deps ?? []).length ? el("div", { className: "chips open" }, el("span", { className: "hint", textContent: "쓰는 지표" }), ...m.deps.map(id => ids.has(id) ? el("span", { className: "dep ok", textContent: d.metrics.find(x => x.id === id).name })
          : el("span", { className: "dep miss", textContent: "없는 지표", title: "눌러서 새로 만들기", onclick: () => editMetric(null, "m02") }))) : "");
    };
    const mineOnes = d.metrics.filter(inScope), others = d.metrics.filter(m => !inScope(m));
    mine.replaceChildren(...(mineOnes.length ? mineOnes.map(row) : [el("div", { className: "hint", textContent: "이 테이블의 지표가 아직 없어요. 왼쪽에서 골라 만들어 보세요." })]),
      ...(others.length ? [el("h2", { textContent: "다른 테이블의 지표 (복사해서 써요)" }), ...others.map(row)] : []));
    async function removeMetric(m) {
      try {
        const used = await sentUsedBy(m.id);
        if (used.metrics.length) { msg.textContent = `‘${m.name}’을(를) 쓰는 지표가 있어 지울 수 없어요: ${used.metrics.join(", ")}`; return; }  // 서버도 거부한다
        if (used.cards.length && !await sentAsk("지우면 쓰는 곳에서 구할 수 없어요", `이 지표를 쓰는 카드: ${used.cards.join(", ")}\n지운 지표는 되돌릴 수 없어요.`, "지우기")) return;
        await api("/api/my_metrics/delete", { id: m.id }); mcStale = true; msg.textContent = ""; showMetric();
      } catch (e) { msg.textContent = e.message; }
    }
    function scopeNow() { return { table: scope().base, schema: scope().schema || "" }; }
    async function copyMetric(m, mapping = {}) {
      try { await api("/api/my_metrics/copy", { id: m.id, scope: scopeNow(), mapping, columns: kinds() }); msg.textContent = ""; showMetric(); }
      catch (e) {
        const need = (e.message.split("골라 주세요:")[1] ?? "").split(",").map(s => s.trim()).filter(Boolean);
        if (!need.length) { msg.textContent = e.message; return; }
        const sel = Object.fromEntries(need.map(c => [c, pickCol(null, mapping[c], v => { mapping[c] = v; }, "이 테이블의 컬럼")]));
        form.replaceChildren(el("h3", { textContent: "컬럼 짝 고르기" }), el("div", { className: "hint", textContent: e.message.split(":")[0] + ": 원래 컬럼에 맞는 이 테이블의 컬럼을 고르세요." }),
          ...need.map(c => el("div", { className: "row" }, el("span", { textContent: c, style: "min-width:120px" }), sel[c])), el("button", { className: "btn p", textContent: "복사", onclick: () => copyMetric(m, mapping) }));
        msg.textContent = "";
      }
    }
    function editMetric(m, tid) {
      const tpl = cat.templates.find(t => t.id === (m?.template ?? tid)), item = { id: 0, template: tpl.id, slots: m ? JSON.parse(JSON.stringify(m.slots)) : sentDefaults(tpl) };
      const name = el("input", { value: m?.name ?? "", placeholder: "지표 이름" }), unit = el("input", { value: m?.unit ?? "", placeholder: "단위 (예: 원, %)" });
      const better = el("select", {}, ...d.better.map(b => el("option", { value: b, textContent: ({ "": "좋고 나쁨 없음", up: "클수록 좋음", down: "작을수록 좋음" })[b] ?? b })));
      better.value = m?.better ?? "";
      const issues = el("div", { className: "sissues" }), line = el("div", { className: "sline" });
      let timer = 0;
      const def = () => ({ name: name.value.trim(), scope: scopeNow(), template: tpl.id, slots: item.slots, unit: unit.value.trim(), better: better.value });
      const check = () => { clearTimeout(timer); timer = setTimeout(async () => {
        try {
          const v = await api("/api/my_metrics/validate", { definition: def(), columns: kinds(), id: m?.id });
          issues.replaceChildren(v.ok ? el("div", { className: "sissue info", textContent: v.deps.length ? `다른 지표 ${v.deps.length}개를 함께 써요.` : "문제 없어요." }) : el("div", { className: "sissue bad", textContent: v.problem }));
          save.disabled = !v.ok;
        } catch (e) { issues.replaceChildren(el("div", { className: "sissue bad", textContent: e.message })); save.disabled = true; }
      }, 200); };
      const redo = () => { draw(); check(); };
      const draw = () => line.replaceChildren(...sentTokens(tpl.text).map(t => t.text ? el("span", { className: "stext", textContent: t.text }) : (() => { const s = tpl.slots.find(x => x.name === t.slot); return s ? slotEl(s, item, redo) : el("span", { textContent: `{${t.slot}}` }); })()));
      const save = el("button", { className: "btn p", textContent: m ? "저장" : "만들기", disabled: true, onclick: e => busy(e.currentTarget, async () => {
        try {
          if (m) {
            const used = await sentUsedBy(m.id), names = [...used.cards, ...used.metrics];
            if (names.length && !await sentAsk("정의를 고치면 영향이 있어요", `이 지표를 쓰는 곳: ${names.join(", ")}\n고친 뒤 카드는 ‘정의가 바뀌었어요’로 표시돼요.`, "저장")) return;
            await api("/api/my_metrics/update", { id: m.id, patch: def(), columns: kinds() });
          } else await api("/api/my_metrics", { definition: def(), columns: kinds() });
          form.replaceChildren(); msg.textContent = ""; showMetric();
        } catch (er) { issues.replaceChildren(el("div", { className: "sissue bad", textContent: er.message })); }
      }) });
      form.replaceChildren(el("h3", { textContent: (m ? "지표 고치기: " : "새 지표: ") + tpl.name }), el("div", { className: "row" }, name, unit, better), line, issues,
        el("div", { className: "hint", textContent: `이 지표는 ${scopeNow().table || "(테이블 없음)"} 테이블에서만 써요 (${source?.label ?? "연결 없음"}).` }),
        el("div", { className: "row" }, save, el("button", { className: "ghost", textContent: "닫기", onclick: () => form.replaceChildren() })));
      name.oninput = unit.oninput = better.onchange = check;
      draw(); check();
    }
  }
  // 서버 sentence_run.render와 같은 규칙: 집계 칸은 이름(합계)으로, 빈칸 바로 뒤 조사는 값의 받침에 맞춘다(한글로 끝나지 않으면 틀 그대로)
  const PAIRS = { "으로": ["으로", "로"], "로": ["으로", "로"], "을": ["을", "를"], "를": ["을", "를"], "은": ["은", "는"], "는": ["은", "는"], "과": ["과", "와"], "와": ["과", "와"], "이(가)": ["이", "가"], "이": ["이", "가"], "가": ["이", "가"] };
  const spoken = c => /[0-9]/.test(c) ? "영일이삼사오육칠팔구"[c] : /[LMNR]/i.test(c) ? { l: "엘", m: "엠", n: "엔", r: "알" }[c.toLowerCase()] : c === "%" ? "트" : c;  // 서버 compare.reading
  const josa = (word, p) => { const c = spoken(word.slice(-1)), f = c >= "가" && c <= "힣" ? (c.charCodeAt(0) - 0xAC00) % 28 : 0; const [has, plain] = PAIRS[p]; return f === 0 || (has === "으로" && f === 8) ? plain : has; };
  const sentRender = (tpl, slots, names = {}) => tpl.text.replace(/\{([^{}]+)\}(이\(가\)|으로|로|을|를|은|는|과|와|이(?=\s)|가(?=\s))?/g, (all, n, p) => {
    const v = slots?.[n], type = tpl.slots?.find(s => s.name === n)?.type, agg = type === "agg" ? (typeof v === "object" ? v?.agg : v) : null;
    const text = v == null ? "" : agg && SERVER.agg_names?.[agg] ? SERVER.agg_names[agg] : type === "unit" && cat?.units?.[v] ? cat.units[v] : names[v] ? names[v] : typeof v === "object" ? (v.column ?? v.func ?? v.text ?? JSON.stringify(v)) : Array.isArray(v) ? v.join(", ") : String(v);
    return !text ? all : p ? text + josa(text, p) : text; });

  async function show() {
    tabs.querySelectorAll(".tab").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
    if (!scope().base) { body.replaceChildren(el("div", { className: "hint", textContent: "먼저 연결하고 테이블을 고르세요." })); return; }
    if (!cat) {
      try { cat = await api("/api/sentences"); } catch (e) { body.replaceChildren(el("div", { className: "err", textContent: e.message })); return; }
      explore = cat.templates.filter(t => t.kind === "explore"); metricTpl = cat.templates.filter(t => t.kind === "metric");
    }
    try { await Funcs.load(); await Funcs.loadSupport(scope().base, scope().schema); } catch { /* 함수 사전을 못 받아도 문장은 쓴다 */ }
    await loadSets(); await loadMetrics();
    if (tab === "explore") showExplore(); else showMetric();
  }
  // 임시 저장(초안)에 담는 작성 중인 문장(고른 틀·빈칸·기간). 구한 결과는 담지 않는다.
  const draft = () => ({ items: items.map(i => ({ template: i.template, slots: i.slots, period: i.period && { column: i.period.column, text: i.period.text } })), shared: { on: shared.on, column: shared.column, text: shared.text } });
  const setDraft = d => { items = d.items.map(i => ({ id: ++seq, ...i, touched: true })); shared = { ...d.shared, span: null }; };
  return { show, items: () => items, draft, setDraft };
}
