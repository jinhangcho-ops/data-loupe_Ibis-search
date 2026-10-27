// 지표 사전 화면(7단계): [지표] 탭의 #metric-catalog에 붙는다. 영역별 항목 목록(서버 GET /api/catalog), 쓸 수 있는지(POST /api/catalog/available),
// 고른 항목의 문장·빈칸·기간·맞춤, [계산](POST /api/catalog/compute), [내 지표로 저장](POST /api/catalog/save). 계산·검사는 모두 서버가 한다.
// 위쪽은 화면 없이 시험하는 계산(영역 묶기·값 보이기·본문 만들기·맞춤 구절)이고, 아래쪽 showMetricCatalog가 화면이다.
// common.js의 el·api·busy·chip, funcs.js의 Funcs, sentences.js의 sentTokens·sentVisible·sentDefaults·sentFits·sentPeriod가 먼저 있어야 한다.
// showMetricCatalog(box, env): env = SentenceView가 넘기는 { ctx(body·columns·tables), kinds, slotEl, periodInputs, refresh }
const MC_HINT = { north_star: "북극성 후보: 서비스가 잘 되는지 한눈에 보는 대표 지표예요.", counter: "카운터 지표 후보: 대표 지표가 좋아질 때 같이 봐야 할 짝 지표예요.", guardrail: "가드레일 후보: 나빠지면 다른 개선을 멈추고 살펴야 하는 선이에요." };
const MC_ANY = ["num", "date", "text", "bool", "json", "array", "struct", "other"];  // 종류가 any인 컬럼 빈칸에 ‘가공’ 고르기가 붙지 않게 풀어 쓴 목록
const MC_UNITS = { day: "일", week: "주", month: "월" };
// 영역마다 항목을 묶는다(항목이 없는 영역은 뺀다)
const mcGroups = cat => cat.areas.map(a => ({ ...a, entries: cat.entries.filter(e => e.area === a.id) })).filter(g => g.entries.length);
const mcNum = v => Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 });
// 값 글: 비율은 0~1 분수라 %로, 나머지는 단위를 붙인다. 없으면 "—"
const mcValue = (v, format, unit) => v == null ? "—" : format === "percent" ? `${mcNum(v * 100)}%` : `${mcNum(v)}${unit ? " " + unit : ""}`;
// 검색 body에 기간 줄을 더한 사본(기간이 없으면 그대로)
function mcBody(body, period) {
  return period ? { ...body, periods: [...(body.periods ?? []), `${period.column} ${period.start} ~ ${period.end}`] } : body;
}
// 서버로 보낼 빈칸: 지금 보이는 칸의 값만
const mcSlots = (e, slots) => Object.fromEntries(e.slots.filter(s => slots[s.name] != null && sentVisible(s, slots)).map(s => [s.name, slots[s.name]]));
// 맞춤 구절: 기간 단위 또는 공통 키(고르지 않았으면 null)
const mcAlign = a => a.kind === "period" ? { kind: "period", unit: a.unit } : a.kind === "key" && a.keys?.length ? { kind: "key", keys: a.keys } : null;

let mcCat = null, mcStale = true;  // 받아 둔 지표 사전. 내 지표를 저장·지운 뒤에는 mcStale로 다시 받게 한다(mcTemplate은 받아 둔 것을 계속 쓴다)
const mcCatalog = async () => { if (mcStale || !mcCat) { mcCat = await api("/api/catalog"); mcStale = false; } return mcCat; };  // 열 때마다 212KB를 다시 받지 않게
// 내 지표의 template("catalog:항목id")을 문장 틀 모양({text})으로(없으면 null): 내 지표 목록이 문장을 보일 때 쓴다
const mcTemplate = id => { const e = mcCat?.entries.find(x => "catalog:" + x.id === id); return e ? { text: e.sentence } : null; };

async function showMetricCatalog(box, env) {
  const { ctx, kinds, slotEl, periodInputs, refresh } = env, scope = env.scope ?? (() => S);  // scope: 기준 테이블·스키마(대시보드는 고른 테이블)
  let cat, av;
  try { cat = await mcCatalog(); av = (await api("/api/catalog/available", { columns: kinds() })).availability; } catch (err) { box.replaceChildren(el("div", { className: "err", textContent: err.message })); return; }
  const detail = el("div", { className: "mdetail" }), list = el("div", { className: "mcat" });
  let picked = "";

  const hintLine = e => e.hint ? el("div", { className: "mhint", textContent: MC_HINT[e.hint] }) : "";
  const item = e => {
    const a = av[e.id];
    return el("button", { className: "mitem" + (a.usable ? "" : " off") + (picked === e.id ? " on" : ""), disabled: !a.usable, onclick: () => pick(e) },
      el("b", { textContent: e.name }), e.abbr ? el("span", { className: "hint", textContent: " " + e.abbr }) : "", el("div", { className: "hint", textContent: e.help }),
      a.usable ? (a.note ? el("div", { className: "hint", textContent: a.note }) : "") : el("div", { className: "why", textContent: a.reason }));
  };
  const drawList = () => list.replaceChildren(...mcGroups(cat).flatMap(g => [el("h3", { textContent: g.name }), ...g.entries.map(item)]));

  // 고른 항목 하나: 문장·빈칸·기간·맞춤·[계산]·[내 지표로 저장]
  function pick(e) {
    picked = e.id; drawList();
    const st = { slots: { ...sentDefaults(e), ...av[e.id].suggest }, period: { column: ctx.columns().find(c => Funcs.kindOf(c[1]) === "date")?.[0] ?? "", text: "", span: null },
      align: { kind: "", unit: "month", keys: null }, ad: { cols: {} } };
    const line = el("div", { className: "sline" }), extra = el("div", { className: "sline" }), out = el("div", { className: "sresult" }), msg = el("div", { className: "err" });
    const tableSlot = e.slots.find(s => s.type === "table");
    const wide = s => s.kinds === "any" ? { ...s, kinds: MC_ANY } : s;
    // 다른 테이블(광고 테이블)의 컬럼은 그 테이블의 스키마에서 고른다
    const other = s => {
      const names = Object.entries(st.ad.cols).filter(([, k]) => sentFits(s.kinds, k)).map(([c]) => c), v = st.slots[s.name];
      const node = names.length ? el("select", {}, el("option", { value: "", textContent: s.label }), ...names.map(c => el("option", { value: c, textContent: c })))
        : el("input", { value: v ?? "", placeholder: s.label });
      node.value = v ?? "";
      node.onchange = () => { if (node.value) st.slots[s.name] = node.value; else delete st.slots[s.name]; };
      const wrap = el("span", { className: "slot-wrap", title: s.label }, node);
      wrap.dataset.slot = s.name;
      return wrap;
    };
    const cell = s => s.other ? other(s) : slotEl(wide(s), st, redo);
    const drawLine = () => {
      const used = new Set();
      line.replaceChildren(...sentTokens(e.sentence).map(t => {
        const s = t.slot && e.slots.find(x => x.name === t.slot);
        if (s) used.add(s.name);
        return t.text ? el("span", { className: "stext", textContent: t.text }) : s ? cell(s) : el("span", { textContent: `{${t.slot}}` });
      }));
      extra.replaceChildren(...e.slots.filter(s => !used.has(s.name) && sentVisible(s, st.slots)).map(s => el("span", { className: "mextra" }, el("span", { className: "stext", textContent: s.label }), cell(s))));
    };
    async function redo() {
      out.replaceChildren();
      const t = tableSlot && st.slots[tableSlot.name];
      if (t && st.ad.name !== t) {
        const ad = st.ad = { name: t, cols: {} };
        try { ad.cols = (await api("/api/table_columns", { table: t, ...(scope().schema ? { schema: scope().schema } : {}) })).columns ?? {}; } catch { /* 컬럼을 못 받으면 직접 적는다 */ }
      }
      drawLine();
    }
    // 다른 테이블이 필요한 지표: 기간 단위 또는 공통 키로 맞춘다
    const alignRow = el("div", { className: "speriod" });
    const drawAlign = () => {
      const a = st.align, mode = (kind, text) => el("button", { className: "seg-b" + (a.kind === kind ? " on" : ""), textContent: text, onclick: () => { a.kind = kind; drawAlign(); } });
      const unit = el("select", {}, ...Object.entries(MC_UNITS).map(([k, t]) => el("option", { value: k, textContent: t + " 단위" })));
      unit.value = a.unit; unit.onchange = () => { a.unit = unit.value; };
      alignRow.replaceChildren(el("span", { className: "stext", textContent: "두 테이블을" }), el("span", { className: "seg" }, mode("period", "기간 단위로 맞춰"), mode("key", "공통 키로 맞춰")),
        a.kind === "period" ? unit : "", a.kind === "key" ? el("button", { className: "btn", textContent: "공통 키 찾기", onclick: ev => busy(ev.currentTarget, findKeys) }) : "",
        a.kind === "key" && a.keys ? el("span", { className: "hint", textContent: a.keys.map(k => `${k[0]} = ${k[1]}`).join(", ") }) : "");
    };
    async function findKeys() {
      const t = tableSlot && st.slots[tableSlot.name];
      if (!t) { msg.textContent = "먼저 문장에서 광고 테이블을 고르세요."; return; }
      msg.textContent = "";
      try {
        const d = await api("/api/join_keys", { a: { table: scope().base, ...(scope().schema ? { schema: scope().schema } : {}) }, b: { table: t, ...(scope().schema ? { schema: scope().schema } : {}) } });
        if (!d.candidates.length) { msg.textContent = "공통 키 후보가 없어요. 기간 단위로 맞추세요."; return; }
        const choose = c => { st.align.keys = c.keys; drawAlign(); keys.replaceChildren(); };
        keys.replaceChildren(...d.candidates.map(c => el("button", { className: "tpl", title: (c.reasons ?? []).join("\n"), onclick: () => choose(c) }, el("b", { textContent: c.keys.map(k => `${k[0]} = ${k[1]}`).join(", ") }), ` 점수 ${c.score}, ${c.basis}`)));
      } catch (err) { msg.textContent = err.message; }
    }
    const keys = el("div", { className: "scols" });

    const period = () => { const p = st.period; return p.text && p.span?.error ? null : (sentPeriod(p.column, p.span)); };
    async function compute() {
      msg.textContent = ""; out.replaceChildren();
      if (st.period.pending) { msg.textContent = "기간을 읽는 중이에요. 잠시 뒤 다시 누르세요."; return; }
      if (st.period.text && st.period.span?.error) { msg.textContent = st.period.span.error; return; }
      if (e.cross && !mcAlign(st.align)) { msg.textContent = "다른 테이블과 맞추는 방법(기간 단위 또는 공통 키)을 골라 주세요."; return; }
      try {
        const d = await api("/api/catalog/compute", { id: e.id, slots: mcSlots(e, st.slots), body: mcBody(ctx.body(), period()), align: e.cross ? mcAlign(st.align) : undefined });
        out.replaceChildren(result(d));
      } catch (err) { msg.textContent = err.message; }
    }
    async function save() {
      msg.textContent = "";
      if (e.cross && !mcAlign(st.align)) { msg.textContent = "저장하려면 다른 테이블과 맞추는 방법을 골라 주세요."; return; }
      try {
        await api("/api/catalog/save", { id: e.id, slots: mcSlots(e, st.slots), scope: { table: scope().base, schema: scope().schema || "" }, columns: kinds(), align: e.cross ? mcAlign(st.align) : undefined });
        mcStale = true;
        refresh();  // 내 지표 목록을 다시 그린다(사전 화면도 새로 그려진다)
      } catch (err) { msg.textContent = err.message; }
    }
    // 계산 결과: 값(비율은 %), 짝 절대량, 중앙값·P90·P99, 알림, 가드레일 안내, 표
    function result(d) {
      const rows = d.rows ?? [], cols = d.columns ?? [];
      return el("div", { className: "mresult" },
        el("div", { className: "mval" }, mcValue(d.value, d.format, d.unit), d.label ? el("span", { className: "hint", textContent: " " + d.label }) : ""),
        d.text ? el("div", { className: "hint", textContent: d.text }) : "",
        d.pair?.length ? el("div", { className: "mpair" }, el("span", { className: "hint", textContent: "함께 보는 절대량" }), ...d.pair.map(p => el("span", { className: "chip" }, `${p.name} ${p.value == null ? "—" : mcNum(p.value)}`))) : "",
        d.spread ? el("div", { className: "mpair" }, el("span", { className: "hint", textContent: `분포(${mcNum(d.spread.n)}개)` }), ...[["median", "중앙값"], ["p90", "P90"], ["p99", "P99"]].map(([k, t]) => el("span", { className: "chip" }, `${t} ${mcValue(d.spread[k], d.format, d.unit)}`))) : "",
        ...(d.notes ?? []).map(t => el("div", { className: "hint", textContent: t })),
        hintLine(e),
        rows.length ? el("table", { className: "mtable" }, el("thead", {}, el("tr", {}, ...cols.map(c => el("th", { textContent: c.label })))),
          el("tbody", {}, ...rows.slice(0, 12).map(r => el("tr", {}, ...cols.map(c => el("td", { textContent: typeof r[c.key] === "number" ? mcNum(r[c.key]) : String(r[c.key] ?? "") })))))) : "",
        rows.length > 12 ? el("div", { className: "hint", textContent: `앞의 12줄만 보여요(전체 ${rows.length}줄).` }) : "");
    }
    drawLine();
    if (e.cross) drawAlign();
    detail.replaceChildren(el("h3", { textContent: e.name + (e.abbr ? ` (${e.abbr})` : "") }), el("div", { className: "hint", textContent: e.help }), el("div", { className: "hint", textContent: "식: " + e.formula }), hintLine(e),
      line, extra, el("div", { className: "speriod" }, periodInputs(st.period, () => { out.replaceChildren(); })), e.cross ? alignRow : "", keys,
      el("div", { className: "row" }, el("button", { className: "btn p", textContent: "계산하기", onclick: ev => busy(ev.currentTarget, compute) }), el("button", { className: "btn", textContent: "내 지표로 저장", onclick: ev => busy(ev.currentTarget, save) })),
      msg, out);
    redo();
    detail.scrollIntoView?.({ block: "nearest" });
  }
  drawList();
  box.replaceChildren(detail, list);
}
