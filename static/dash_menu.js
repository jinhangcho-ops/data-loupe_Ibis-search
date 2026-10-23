// 쿼리 화면 대시보드 탭의 [카드 추가] 메뉴: KPI·도넛 KPI·게이지·요약 문장·최근 행·두 계열 추이·칸 채우기·블록·목표 추이 카드를 지금 탐색의 테이블·조건으로 만든다.
// 종류마다 값(컬럼·집계)·날짜 컬럼(기간 기준, 하나뿐이면 자동)·목표(직접 숫자 또는 목표 파일의 이름)·모양을 정한다. 전체 끌어 놓기 편집은 5단계 몫이다.
// index.html의 S·state()·metrics·addCard·nextLayout·freshColors·board와 dash_cards.js의 SVG_KINDS가 있어야 한다.
const CARD_KINDS = { kpi: ["KPI(아이콘·큰 값·비교·스파크라인)", 1, 1], donut_kpi: ["도넛 KPI(가운데+꼭지점 칸)", 1, 5], gauge: ["게이지(항목 몫)", 1, 1], summary: ["요약 문장(목표 대비)", 1, 1],
                     recent_rows: ["최근 행(연결할 때만 보임)", 0, 0], dual_trend: ["두 계열 추이", 2, 2], stripes: ["칸 채우기", 1, 1], blocks: ["블록", 1, 1], target_trend: ["목표 추이", 1, 1] };
const CARD_AGGS = { count: "개수", sum: "합계", mean: "평균", min: "최소", max: "최대", nunique: "고유 개수" };
const colsOfType = names => [...S.cols].filter(c => names.includes(c[1])).map(c => c[0]);
const dateCols = () => colsOfType(["날짜", "날짜시간"]);

// 메뉴 칸들을 root에 만든다. 값 줄은 종류의 최소~최대 개수까지 더하고 뺄 수 있다.
function initCardMenu(root) {
  const kind = el("select", {}, ...Object.entries(CARD_KINDS).map(([k, v]) => el("option", { value: k, textContent: v[0] })));
  const title = el("input", { placeholder: "카드 이름 (비우면 자동)" }), rows = el("div", { className: "cm-rows" }), err = el("div", { className: "err" });
  const group = el("select", { title: "게이지는 이 컬럼의 값마다 한 조각, 요약은 변화에 가장 크게 기여한 값을 찾는 데 씁니다" }), date = el("select", { title: "기간 기준 날짜 컬럼: 대시보드의 기간·기준일을 이 컬럼으로 건다. 날짜 컬럼이 하나뿐이면 자동으로 고른다" });
  const metric = el("select", { title: "지표 파일의 지표 하나를 값으로 쓴다(비율 지표는 분자·분모도 작게 보인다)" });
  const targetValue = el("input", { type: "number", placeholder: "목표 숫자", style: "width:110px" }), targetName = el("select", { title: "목표 파일에서 이름으로 찾는다" });
  const format = el("select", {}, el("option", { value: "number", textContent: "숫자" }), el("option", { value: "percent", textContent: "% (0~1 값)" }), el("option", { value: "currency", textContent: "₩ 금액" }));
  const ring = el("select", {}, ...[["stack", "한 고리 누적"], ["double", "겹친 두 고리"], ["half", "반원 게이지"], ["ticks", "고리+목표 눈금"]].map(([v, n]) => el("option", { value: v, textContent: n })));
  const cells = el("input", { type: "number", value: 10, min: 1, max: 100, style: "width:60px" }), size = el("input", { type: "number", placeholder: "자동", style: "width:90px" }), rowCount = el("input", { type: "number", value: 10, min: 1, max: 100, style: "width:60px" });
  const path = el("input", { placeholder: "목표 파일 경로(.xlsx·.csv·.yaml·.json·.md·.txt)" }), pathNote = el("div", { className: "hint cm-note" });
  const only = (label, input, ...kinds) => { const f = el("label", { className: "hint cm-field" }, label, input); f.dataset.kinds = kinds.join(" "); return f; };
  const fields = [only("값 모양", format, "kpi", "donut_kpi", "summary", "dual_trend", "stripes", "blocks", "target_trend", "gauge"), only("지표", metric, "kpi"), only("나눌 컬럼", group, "gauge", "summary"),
    only("날짜 컬럼", date, ...Object.keys(CARD_KINDS)), only("목표", el("span", {}, targetValue, " 또는 ", targetName), "kpi", "donut_kpi", "summary", "stripes", "blocks", "target_trend"),
    only("고리", ring, "donut_kpi"), only("칸 수", cells, "stripes"), only("한 칸 크기", size, "blocks"), only("행 수", rowCount, "recent_rows")];
  const options = (select, names, blank) => select.replaceChildren(...(blank ? [el("option", { value: "", textContent: blank })] : []), ...names.map(n => el("option", { value: n, textContent: n })));
  const makeRow = () => {
    const col = el("select", {}), agg = el("select", {}, ...Object.entries(CARD_AGGS).map(([k, v]) => el("option", { value: k, textContent: v })));
    options(col, S.cols.map(c => c[0]), "(컬럼 없음: 개수)");
    const row = el("div", { className: "cm-row" }, el("input", { placeholder: "이름(칸 제목)", className: "cm-label" }), col, agg, el("button", { className: "ghost", textContent: "×", onclick: () => { row.remove(); show(); } }));
    row.col = col; row.agg = agg;
    rows.append(row);
  };
  const show = () => {  // 종류에 맞게 칸을 보이고, 값 줄 수를 최소~최대에 맞춘다
    const [, low, high] = CARD_KINDS[kind.value];
    while (rows.children.length < low) makeRow();
    while (rows.children.length > high) rows.lastChild.remove();
    fields.forEach(f => f.classList.toggle("hidden", !f.dataset.kinds.split(" ").includes(kind.value)));
    add.classList.toggle("hidden", rows.children.length >= high);
    rows.classList.toggle("hidden", high === 0);
    [...rows.children].forEach(r => r.querySelector(".ghost").classList.toggle("hidden", rows.children.length <= low));
  };
  const add = el("button", { className: "btn", textContent: "+ 값 더하기", onclick: () => { makeRow(); show(); } });
  // 지금 탐색의 컬럼·지표·목표 파일로 선택지를 새로 채운다(메뉴를 열 때마다)
  async function refill() {
    const all = S.cols.map(c => c[0]), dates = dateCols();
    options(group, all, "(없음)");
    options(date, dates, "(없음: 기간을 걸지 않음)");
    if (dates.length === 1) date.value = dates[0];  // 하나뿐이면 자동
    options(metric, (typeof metrics === "object" && metrics?.metrics ? metrics.metrics.map(m => m.name) : []), "(쓰지 않음)");
    [...rows.children].forEach(r => { const now = r.col.value; options(r.col, all, "(컬럼 없음: 개수)"); r.col.value = all.includes(now) ? now : ""; });
    try {
      const t = await api("/api/targets");
      path.value = t.path;
      pathNote.textContent = t.error || (t.path ? `목표 ${t.rows.length}개${t.errors.length ? `, 읽지 못한 줄 ${t.errors.length}개(${t.errors[0].message})` : ""}` : "목표 파일을 정하면 이름으로 목표를 찾을 수 있어요");
      options(targetName, [...new Set(t.rows.map(r => r.name))], "(이름으로 찾지 않음)");
    } catch (e) { pathNote.textContent = e.message; }
    show();
  }
  const build = () => {
    if (!S.base) throw new Error("탐색에서 기준 테이블을 고르세요.");
    const k = kind.value, lines = [...rows.children].map(r => ({ label: r.querySelector(".cm-label").value.trim(), y: r.col.value || null, agg: r.col.value ? r.agg.value : r.agg.value === "nunique" ? "count" : r.agg.value }));
    const series = lines.map(l => ({ y: l.y, agg: l.y ? l.agg : "count" })), byMetric = k === "kpi" && metric.value;
    if (!byMetric && CARD_KINDS[k][1] && !series.length) throw new Error("값을 하나 이상 넣으세요.");
    if (k === "gauge" && !group.value) throw new Error("게이지는 나눌 컬럼을 고르세요.");
    const body = { ...state(), series: byMetric ? [] : series, ...(k === "gauge" ? { x: group.value } : {}), ...(byMetric ? { names: [metric.value] } : {}) };
    const view = { type: "", ratio: 4 / 3, format: format.value, icon: "chart", compare: { mode: "span" }, better: "up" };
    if (targetValue.value !== "") view.target = { value: +targetValue.value };
    else if (targetName.value) view.target = { name: targetName.value };
    if (k === "donut_kpi") { view.ring = ring.value; view.slots = lines.map((l, i) => ({ label: l.label || (series[i].y ? `${CARD_AGGS[series[i].agg]} ${series[i].y}` : "개수"), format: format.value })); }
    if (k === "stripes") view.cells = +cells.value || 10;
    if (k === "blocks" && size.value) view.size = +size.value;
    if (k === "recent_rows") view.rows = +rowCount.value || 10;
    if (k === "summary" && group.value) view.group = group.value;
    return { kind: k, title: title.value.trim() || `${CARD_KINDS[k][0].split("(")[0]} · ${S.base}`, layout: nextLayout(), colors: freshColors(), view, data: {},
             source: { schema: S.schema || "", table: S.base, body, ...(date.value && k !== "recent_rows" ? { date: date.value } : {}) } };
  };
  const go = el("button", { className: "btn p", textContent: "카드 추가", onclick: () => busy(go, async () => {
    try { await addCard(build(), "chart", { schema: S.schema, base: S.base }); err.textContent = ""; say("err-dash", `카드를 넣었습니다(${board.cards.length}개).`, true); }
    catch (e) { err.textContent = e.message; }
  }) });
  const save = el("button", { className: "btn", textContent: "저장", onclick: async () => {
    try { const t = await api("/api/targets", { path: path.value.trim() }); pathNote.textContent = t.error || `목표 ${t.rows.length}개를 읽었어요`; refill(); } catch (e) { pathNote.textContent = e.message; }
  } });
  kind.onchange = show;
  root.replaceChildren(el("div", { className: "row" }, kind), title, rows, add, ...fields, el("div", { className: "row" }, path, save), pathNote, err, go);
  return { refill };
}
