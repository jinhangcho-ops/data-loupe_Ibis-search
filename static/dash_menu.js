// 대시보드 화면의 [카드 추가] 메뉴: KPI·도넛 KPI·게이지·요약 문장·최근 행·두 계열 추이·칸 채우기·블록·목표 추이·표·문장·상태·경고 목록·배치도·편차 표 카드를 지금 탐색의 테이블·조건으로 만든다.
// 테이블은 메뉴에서 고른다(연결돼 있어야 한다). 탐색의 지금 검색이 필요한 차트·지표 카드는 쿼리 화면의 [대시보드에 추가]로 만든다.
// 종류마다 값(컬럼·집계)·날짜 컬럼(기간 기준, 하나뿐이면 자동)·목표(직접 숫자 또는 목표 파일의 이름)·모양, 표·문장은 조건 색 규칙, 상태·경고·배치도는 엔티티 컬럼과 상태 규칙·연결선을 정한다. 전체 끌어 놓기 편집은 5단계 몫이다.
// host = { add(card): 카드를 값과 함께 대시보드에 넣는다(실패하면 오류), layout(): 새 카드의 자리, metricNames(): 지표 이름들 }. dash_rules.js, dash.js(freshColors)가 먼저 불려 있어야 한다.
const CARD_KINDS = { kpi: ["KPI(아이콘·큰 값·비교·스파크라인)", 1, 1], donut_kpi: ["도넛 KPI(가운데+꼭지점 칸)", 1, 5], gauge: ["게이지(항목 몫)", 1, 1], summary: ["요약 문장(목표 대비)", 1, 1],
                     recent_rows: ["최근 행(연결할 때만 보임)", 0, 0], dual_trend: ["두 계열 추이", 2, 2], stripes: ["칸 채우기", 1, 1], blocks: ["블록", 1, 1], target_trend: ["목표 추이", 1, 1],
                     table: ["표(집계 또는 원래 행)", 0, 5], sentence: ["문장(빈칸에 값·비교·기준일)", 0, 0], status: ["상태(정상·주의·이상)", 0, 0], alerts: ["경고 목록(N분째·영향)", 0, 0],
                     map: ["배치도(끌어 옮김)", 0, 0], deviation: ["편차 표(엔티티×시간대)", 1, 1] };
const WATCH = ["status", "alerts", "map"];  // 엔티티별 최근 행을 상태 규칙으로 보는 카드
let menuCols = [], menuBase = "";  // 메뉴에서 고른 테이블과 그 컬럼 [[이름, 한글 타입]]
const colsOfType = names => menuCols.filter(c => names.includes(c[1])).map(c => c[0]);
const dateCols = () => colsOfType(["날짜", "날짜시간"]);
// 서버(dash_calc.series_name)와 같은 계열 이름: 개수 / 합계 (컬럼)
const seriesTitle = s => aggName(s.agg) + (s.agg === "count" || !s.y ? "" : ` (${s.y})`);

// 메뉴 칸들을 root에 만든다. 값 줄은 종류의 최소~최대 개수까지 더하고 뺄 수 있다.
function initCardMenu(root, host) {
  const table = el("select", { title: "카드가 읽을 테이블(연결돼 있어야 합니다)" });
  const kind = el("select", {}, ...Object.entries(CARD_KINDS).map(([k, v]) => el("option", { value: k, textContent: v[0] })));
  const title = el("input", { placeholder: "카드 이름 (비우면 자동)" }), rows = el("div", { className: "cm-rows" }), err = el("div", { className: "err" });
  const group = el("select", { title: "게이지는 이 컬럼의 값마다 한 조각, 요약은 변화에 가장 크게 기여한 값을 찾는 데 씁니다" }), date = el("select", { title: "기간 기준 날짜 컬럼: 대시보드의 기간·기준일을 이 컬럼으로 건다. 날짜 컬럼이 하나뿐이면 자동으로 고른다" });
  const metric = el("select", { title: "지표 파일의 지표 하나를 값으로 쓴다(비율 지표는 분자·분모도 작게 보인다)" });
  const targetValue = el("input", { type: "number", placeholder: "목표 숫자", style: "width:110px" }), targetName = el("select", { title: "목표 파일에서 이름으로 찾는다" });
  const format = el("select", {}, el("option", { value: "number", textContent: "숫자" }), el("option", { value: "percent", textContent: "% (0~1 값)" }), el("option", { value: "currency", textContent: "₩ 금액" }));
  const ring = el("select", {}, ...[["stack", "한 고리 누적"], ["double", "겹친 두 고리"], ["half", "반원 게이지"], ["ticks", "고리+목표 눈금"]].map(([v, n]) => el("option", { value: v, textContent: n })));
  const cells = el("input", { type: "number", value: 10, min: 1, max: 100, style: "width:60px" }), size = el("input", { type: "number", placeholder: "자동", style: "width:90px" }), rowCount = el("input", { type: "number", value: 10, min: 1, max: 100, style: "width:60px" });
  const groupCols = el("input", { placeholder: "예: 매장, 날짜 (쉼표, 3개까지)" }), total = el("input", { type: "checkbox" });
  const entity = el("select", { title: "서버·장비·매장처럼 상태를 보는 대상의 이름 컬럼" }), timeCol = el("select", { title: "마지막 데이터 시각 컬럼(끊김·신선도 판정)" }), kindCol = el("select", { title: "배치도에서 종류별로 줄을 나눌 컬럼" }), linkCol = el("select", { title: "이 엔티티가 영향을 주는 대상(연결선 도착)의 이름 컬럼" });
  const stale = el("input", { type: "number", min: 1, placeholder: "예: 30", style: "width:80px" }), bg = el("select", {}, ...[["rack", "서버실 칸"], ["network", "네트워크"], ["image", "그림 파일"]].map(([v, n]) => el("option", { value: v, textContent: n }))), imagePath = el("input", { placeholder: "그림 파일 경로(.png·.jpg·.svg, 8MB까지). 카드를 저장하면 보입니다" });
  const bucket = el("select", {}, ...[["hour", "시간"], ["day", "일"], ["week", "주"], ["month", "월"]].map(([v, n]) => el("option", { value: v, textContent: n }))), baseline = el("input", { type: "number", placeholder: "비우면 엔티티 평균", style: "width:130px" }), threshold = el("input", { type: "number", placeholder: "비우면 기준의 20%", style: "width:130px" });
  const template = el("textarea", { rows: 2, placeholder: "예: 오늘 매출은 {매출}이고 어제보다 {비교}예요 ({기준일} 기준)", style: "width:100%;font-size:12px" });
  const condRules = lineEditor("", parseRuleLine, "색 규칙(한 줄에 하나): 컬럼 | 식 | #색 | 화살표 | 칸·줄\n예: 상태 | *지연* | #e03131 | 없음 | 줄");
  const stateRules = lineEditor("", parseStateLine, "상태 규칙(한 줄에 하나): 컬럼 연산자 값 단계\n예: cpu >= 90 이상 / 상태 = DOWN 주의");
  const linkLines = lineEditor("", parseLinkLine, "연결선(한 줄에 하나): 출발 -> 도착\n예: 라우터1 -> 서버1");
  const blankLines = lineEditor("", parseBlankLine, "빈칸(한 줄에 하나): 이름 = 종류(값·직전·비교·기준일) [컬럼 [집계 [모양]]]\n예: 매출 = 값 amt sum 금액 / 비교 = 비교 amt sum / 기준일 = 기준일");
  const path = el("input", { placeholder: "목표 파일 경로(.xlsx·.csv·.yaml·.json·.md·.txt)" }), pathNote = el("div", { className: "hint cm-note" });
  const only = (label, input, ...kinds) => { const f = el("label", { className: "hint cm-field" }, label, input); f.dataset.kinds = kinds.join(" "); return f; };
  const block = (input, ...kinds) => { const f = el("div", { className: "cm-block" }, input); f.dataset.kinds = kinds.join(" "); return f; };
  const withDate = Object.keys(CARD_KINDS).filter(k => k !== "recent_rows" && !WATCH.includes(k));
  const fields = [only("값 모양", format, "kpi", "donut_kpi", "summary", "dual_trend", "stripes", "blocks", "target_trend", "gauge", "table", "deviation"), only("지표", metric, "kpi"), only("나눌 컬럼", group, "gauge", "summary"),
    only("날짜 컬럼", date, ...withDate), only("목표", el("span", {}, targetValue, " 또는 ", targetName), "kpi", "donut_kpi", "summary", "stripes", "blocks", "target_trend"),
    only("고리", ring, "donut_kpi"), only("칸 수", cells, "stripes"), only("한 칸 크기", size, "blocks"), only("행 수", rowCount, "recent_rows", "table"),
    only("묶음 컬럼", groupCols, "table"), only("합계 줄", total, "table"), block(condRules.root, "table", "sentence"),
    only("엔티티 컬럼", entity, ...WATCH, "deviation"), only("시각 컬럼", timeCol, ...WATCH), only("종류 컬럼", kindCol, ...WATCH), only("연결 대상 컬럼", linkCol, ...WATCH), only("끊김(분)", stale, ...WATCH),
    block(stateRules.root, ...WATCH), block(linkLines.root, ...WATCH), only("바탕", bg, "map"), only("그림 경로", imagePath, "map"),
    only("시간대 묶음", bucket, "deviation"), only("기준", baseline, "deviation"), only("허용 폭", threshold, "deviation"), block(template, "sentence"), block(blankLines.root, "sentence")];
  const options = (select, names, blank) => select.replaceChildren(...(blank ? [el("option", { value: "", textContent: blank })] : []), ...names.map(n => el("option", { value: n, textContent: n })));
  const makeRow = () => {
    if (!Funcs.cat) return;  // 함수 사전을 받은 뒤에(받으면 show가 다시 줄을 채운다)
    const col = el("select", {}), cat = Funcs.cat;
    const picker = Funcs.picker({ ids: cat.screens.dash.filter(i => cat.byId[i].aggregate), columns: () => menuCols, onchange: () => syncRow(row) });  // 카드 계열의 집계: 함수 사전의 집계 전부 + 인자 칸
    const row = el("div", { className: "cm-row" }, el("input", { placeholder: "이름(칸 제목)", className: "cm-label" }), col, picker.el, el("button", { className: "ghost", textContent: "×", onclick: () => { row.remove(); show(); } }));
    row.col = col; row.picker = picker;
    rows.append(row);
    syncRow(row);
  };
  // 줄의 컬럼 칸: 고른 집계의 첫 컬럼 칸이 받는 종류만 보이고, 컬럼이 필요 없는 집계(조건 개수 …)이면 숨긴다. 고른 컬럼은 남아 있으면 그대로.
  const syncRow = r => {
    const now = r.col.value, names = r.picker.xColumns(menuCols).map(c => c[0]);
    options(r.col, names, "(컬럼 없음: 개수)");
    r.col.value = names.includes(now) ? now : "";
    r.col.classList.toggle("hidden", r.picker.yMode() === "none");
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
    try {  // 연결의 테이블 목록(연결이 없으면 이유를 보인다)
      const names = (await api("/api/tables?schema=")).tables, now = table.value;
      options(table, names, "(테이블을 고르세요)");
      table.value = names.includes(now) ? now : "";
    } catch (e) { err.textContent = e.message; }
    const all = menuCols.map(c => c[0]), dates = dateCols();
    options(group, all, "(없음)");
    options(date, dates, "(없음: 기간을 걸지 않음)");
    if (dates.length === 1) date.value = dates[0];  // 하나뿐이면 자동
    options(entity, all, "(고르세요)");
    [timeCol, kindCol, linkCol].forEach(s => options(s, all, "(없음)"));
    if (dates.length === 1) timeCol.value = dates[0];
    options(metric, host.metricNames(), "(쓰지 않음)");
    [...rows.children].forEach(syncRow);
    try {
      const t = await api("/api/targets");
      path.value = t.path;
      pathNote.textContent = t.error || (t.path ? `목표 ${t.rows.length}개${t.errors.length ? `, 읽지 못한 줄 ${t.errors.length}개(${t.errors[0].message})` : ""}` : "목표 파일을 정하면 이름으로 목표를 찾을 수 있어요");
      options(targetName, [...new Set(t.rows.map(r => r.name))], "(이름으로 찾지 않음)");
    } catch (e) { pathNote.textContent = e.message; }
    show();
  }
  // 한 줄 글 칸을 읽어 규칙·연결선·빈칸을 얻는다. 틀린 줄이 있으면 그 줄과 이유로 오류를 낸다(칸 아래에도 보인다).
  const lines = (editor, key) => {
    const found = editor.read();
    if (found.some(f => f.error)) throw new Error(`${found.find(f => f.error).line} → ${found.find(f => f.error).error}`);
    return found.map(f => f[key]);
  };
  const build = () => {
    if (!menuBase) throw new Error("테이블을 고르세요.");
    const k = kind.value, values = [...rows.children].map(r => {  // 컬럼이 꼭 필요한 집계인데 컬럼을 안 고르면 개수
      const y = r.col.value || null, { agg, opts } = r.picker.get(), needs = r.picker.yMode() === "required";
      return { label: r.querySelector(".cm-label").value.trim(), y, ...(needs && !y ? { agg: "count" } : { agg, ...(opts ? { opts } : {}) }) };
    });
    const series = values.map(({ y, agg, opts }) => ({ y, agg, ...(opts ? { opts } : {}) })), byMetric = k === "kpi" && metric.value;
    if (!byMetric && CARD_KINDS[k][1] && !series.length) throw new Error("값을 하나 이상 넣으세요.");
    if (k === "gauge" && !group.value) throw new Error("게이지는 나눌 컬럼을 고르세요.");
    const body = { schema: "", base: menuBase, joins: [], periods: [], conditions: [], calcs: [], sorts: [], columns: [], series: byMetric ? [] : series, ...(k === "gauge" ? { x: group.value } : {}), ...(byMetric ? { names: [metric.value] } : {}) };
    const view = { type: "", ratio: 4 / 3, format: format.value, icon: "chart", compare: { mode: "span" }, better: "up" };
    if (targetValue.value !== "") view.target = { value: +targetValue.value };
    else if (targetName.value) view.target = { name: targetName.value };
    if (k === "donut_kpi") { view.ring = ring.value; view.slots = values.map((l, i) => ({ label: l.label || seriesTitle(series[i]), format: format.value })); }
    if (k === "stripes") view.cells = +cells.value || 10;
    if (k === "blocks" && size.value) view.size = +size.value;
    if (k === "recent_rows" || k === "table") view.rows = +rowCount.value || 10;
    if (k === "summary" && group.value) view.group = group.value;
    if (k === "table") {
      view.group_cols = groupCols.value.split(",").map(c => c.trim()).filter(Boolean);
      view.total = total.checked;
      view.formats = Object.fromEntries(series.map(s => [seriesTitle(s), format.value]));
      view.cond_rules = lines(condRules, "rule");
    }
    if (k === "sentence") {
      if (!template.value.trim()) throw new Error("문장 틀을 쓰세요. 예: 오늘 매출은 {매출}이에요");
      view.template = template.value.trim();
      view.blanks = lines(blankLines, "blank");
      view.cond_rules = lines(condRules, "rule");
    }
    if (WATCH.includes(k)) {
      if (!entity.value) throw new Error("엔티티(대상 이름) 컬럼을 고르세요.");
      Object.assign(view, { entity: entity.value, rules: lines(stateRules, "rule"), links: lines(linkLines, "link"), ratio: 400 / 260 });
      if (timeCol.value) view.time_col = timeCol.value;
      if (kindCol.value) view.kind_col = kindCol.value;
      if (linkCol.value) view.link_col = linkCol.value;
      if (stale.value) view.stale = +stale.value;
      if (k === "map") { view.bg = bg.value; if (bg.value === "image") view.image_path = imagePath.value.trim(); }
    }
    if (k === "deviation") {
      if (!entity.value || !date.value) throw new Error("편차 표는 엔티티 컬럼과 날짜(시각) 컬럼을 고르세요.");
      Object.assign(view, { entity: entity.value, bucket: bucket.value });
      if (baseline.value !== "") view.baseline = +baseline.value;
      if (threshold.value !== "") view.threshold = +threshold.value;
    }
    return { kind: k, title: title.value.trim() || `${CARD_KINDS[k][0].split("(")[0]} · ${menuBase}`, layout: host.layout(), colors: freshColors(), view, data: {},
             source: { schema: "", table: menuBase, body, ...(date.value && withDate.includes(k) ? { date: date.value } : {}) } };
  };
  const go = el("button", { className: "btn p", textContent: "카드 추가", onclick: () => busy(go, async () => {
    try { await host.add(build()); err.textContent = ""; }
    catch (e) { err.textContent = e.message; }
  }) });
  const save = el("button", { className: "btn", textContent: "저장", onclick: async () => {
    try { const t = await api("/api/targets", { path: path.value.trim() }); pathNote.textContent = t.error || `목표 ${t.rows.length}개를 읽었어요`; refill(); } catch (e) { pathNote.textContent = e.message; }
  } });
  kind.onchange = show;
  table.onchange = async () => {  // 테이블을 고르면 그 컬럼을 받아 선택지를 다시 채운다
    menuBase = table.value;
    menuCols = [];
    if (menuBase) { try { menuCols = (await api("/api/columns", { schema: "", base: menuBase })).columns; err.textContent = ""; } catch (e) { err.textContent = e.message; } }
    [...rows.children].forEach(r => r.remove());
    refill();
    Funcs.loadSupport(menuBase).then(() => [...rows.children].forEach(r => r.picker.refresh()));  // 이 연결이 못 하는 집계는 안내를 붙이거나 막는다
  };
  Funcs.load().then(show);  // 함수 사전을 받으면 값 줄을 채운다
  root.replaceChildren(el("div", { className: "row" }, table), el("div", { className: "row" }, kind), title, rows, add, ...fields, el("div", { className: "row" }, path, save), pathNote, err, go);
  return { refill };
}
