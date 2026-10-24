// 대시보드 공통 그리기: 카드의 저장된 값(data)을 Chart.js 설정(chartConfig)으로 바꾸고, 카드 모음(DashBoard)에 차트 종류·시간 단위·색을 고르는 줄을 더한다.
// 쿼리 화면의 대시보드 탭이 쓰고, 분석 화면은 분석 그림(anaConfig)을 가져다 쓴다(Chart.js, common.js, board.js가 먼저 불려 있어야 한다). 종류·단위·상한·기본 색은 서버(SERVER.dash)가 준다.
// 카드 모양은 dash_store.py: { id, kind, title, layout, colors, view, source, data }. data.by_unit은 시간별(일 값 + 서버가 따로 구한 값), data.values는 항목별·지표·분석 결과.

// ---------- 분석 그림 (분석 화면과 대시보드의 분석 카드가 함께 쓴다) ----------
// 집계 이름과 일 값을 다시 묶을 수 있는 집계는 서버(함수 사전)가 주는 목록이다: SERVER.agg_names, SERVER.merge
const aggName = id => SERVER.agg_names?.[id] ?? id;
const merges = agg => SERVER.merge.includes(agg);  // 아니면 일 값을 주·월·연으로 다시 묶을 수 없다(고유 개수·중앙값·분위수 …). 서버가 단위마다 따로 구한다
const UNIT_NAMES = { day: "일", week: "주", month: "월" };
// 숫자는 자릿수 쉼표와 소수 넷째 자리까지
const shownum = v => v == null ? "" : typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(v);
// HEX 색 검사(#rgb, #rrggbb)와 3자리 → 6자리
const HEX = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;
const hex6 = c => c.length === 4 ? "#" + [...c.slice(1)].map(h => h + h).join("") : c;
// 색 글자(#rgb, #rrggbb 또는 rgb)에 투명도를 준다(히트맵 칸, 영역형 라인).
function alpha(color, a) {
  const hex = hex6(color).match(/^#([0-9a-f]{6})$/i);
  const [r, g, b] = hex ? [0, 2, 4].map(i => parseInt(hex[1].slice(i, i + 2), 16)) : (color.match(/\d+/g) || [0, 0, 0]).map(Number);
  return `rgba(${r}, ${g}, ${b}, ${a})`;
}
// 분석 결과 하나를 그 색으로 그릴 Chart.js 설정.
function anaConfig(d, view, colors, options = {}) {
  const axis = { ticks: { color: colors.text }, grid: { color: colors.line } };
  const title = text => ({ display: true, text, color: colors.text });
  const main = colors.series[0], second = colors.series[1];
  const chart = (type, labels, datasets, scales, extra = {}) => ({ type, data: { labels, datasets },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false, labels: { color: colors.text } } }, scales, ...extra, ...options } });
  if (view === "hist") {
    const e = d.histogram.edges;
    return chart("bar", d.histogram.counts.map((_, i) => `${shownum(e[i])}–${shownum(e[i + 1])}`),
      [{ label: "행 수", data: d.histogram.counts, backgroundColor: main, barPercentage: 1, categoryPercentage: 0.96 }],
      { x: { ...axis, title: title(d.column) }, y: { ...axis, title: title("행 수") } });
  }
  if (view === "box") {
    const b = d.box, eps = ((b.high ?? b.q3) - (b.low ?? b.q1)) / 160 || 0.5;  // 중앙값 선의 반 두께(값 단위)
    return chart("bar", [d.column], [
      { label: "수염 (1.5×IQR 안의 최소~최대)", data: [[b.low, b.high]], backgroundColor: colors.text, barThickness: 2, grouped: false, order: 3 },
      { label: "Q1~Q3", data: [[b.q1, b.q3]], backgroundColor: alpha(main, 0.85), borderColor: main, borderWidth: 1, borderSkipped: false, barThickness: 56, grouped: false, order: 2 },  // order가 작을수록 위에 그린다
      { label: "중앙값", data: [[b.median - eps, b.median + eps]], backgroundColor: second, barThickness: 56, grouped: false, order: 1 }],
      { x: axis, y: { ...axis, grid: { display: false } } }, { indexAxis: "y", plugins: { legend: { display: true, labels: { color: colors.text } } } });
  }
  if (view === "values") {
    const v = d.values, labels = [...v.labels, ...(v.nulls ? ["(빈 값)"] : []), ...(v.other ? ["그 밖"] : [])];
    const data = [...v.counts, ...(v.nulls ? [v.nulls] : []), ...(v.other ? [v.other] : [])];
    return chart("bar", labels, [{ label: "행 수", data, backgroundColor: labels.map(l => l === "그 밖" || l === "(빈 값)" ? colors.line : main) }],
      { x: axis, y: { ...axis, title: title("행 수") } });
  }
  if (view === "heat") {
    const k = d.columns.length, ticks = { ...axis.ticks, autoSkip: false, callback: v => d.columns[v] ?? "" };
    const at = s => { s.ticks = d.columns.map((_, i) => ({ value: i })); };  // 눈금은 칸 가운데(0, 1, …)에만
    const cells = d.matrix.flatMap((row, i) => row.map((r, j) => ({ x: j, y: i, r })));
    const heat = { id: "heat", beforeDatasetsDraw(c) {
      const { ctx, scales: { x, y } } = c, w = Math.abs(x.getPixelForValue(1) - x.getPixelForValue(0)), h = Math.abs(y.getPixelForValue(1) - y.getPixelForValue(0));
      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.font = `${Math.max(9, Math.min(14, w / 4.5))}px "Segoe UI", "Malgun Gothic", sans-serif`;
      cells.forEach(({ x: j, y: i, r }) => {
        const cx = x.getPixelForValue(j), cy = y.getPixelForValue(i);
        ctx.fillStyle = r == null ? colors.line : alpha(r >= 0 ? main : second, 0.1 + 0.9 * Math.abs(r));
        ctx.fillRect(cx - w / 2 + 1, cy - h / 2 + 1, w - 2, h - 2);
        ctx.fillStyle = r != null && Math.abs(r) > 0.55 ? "#fff" : colors.ink;
        ctx.fillText(r == null ? "-" : (Math.abs(r) < 0.005 ? 0 : r).toFixed(2), cx, cy);  // -0.00으로 보이지 않게
      });
      ctx.restore();
    } };
    return { ...chart("scatter", undefined, [{ data: cells, pointRadius: 0, pointHitRadius: 18 }],
      { x: { type: "linear", min: -0.5, max: k - 0.5, ticks, afterBuildTicks: at, grid: { display: false } },
        y: { type: "linear", min: -0.5, max: k - 0.5, reverse: true, ticks, afterBuildTicks: at, grid: { display: false } } },
      { plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${d.columns[c.raw.y]} × ${d.columns[c.raw.x]}: ${c.raw.r == null ? "-" : c.raw.r.toFixed(3)}` } } } }),
      plugins: [heat] };
  }
  if (view === "scatter") return chart("scatter", undefined, [{ data: d.points.map(([x, y]) => ({ x, y })), backgroundColor: alpha(main, 0.55), pointRadius: 2.5 }],
    { x: { ...axis, title: title(d.x) }, y: { ...axis, title: title(d.y) } });
  if (view === "groups") return chart("bar", d.labels, [{ label: aggName(d.agg), data: d.values, backgroundColor: main }],
    { x: { ...axis, title: title(d.by) }, y: { ...axis, title: title(d.agg === "count" ? "개수" : `${d.value} ${aggName(d.agg)}`) } });
  return chart("line", d.labels, [{ label: aggName(d.agg), data: d.values, borderColor: main, backgroundColor: main, borderWidth: 2, pointRadius: d.labels.length > 60 ? 0 : 2, spanGaps: true }],
    { x: { ...axis, title: title(UNIT_NAMES[d.unit]) }, y: { ...axis, title: title(d.agg === "count" ? "개수" : `${d.value} ${aggName(d.agg)}`) } });
}

// ---------- 색 ----------
// 라벨의 번호: 카드가 그 라벨을 처음 본 차례(order). 새 라벨은 뒤에 붙여 카드에 남기므로 다시 구한 뒤에도 같은 라벨은 같은 번호(같은 색)다.
// 서버가 order를 200개까지만 받으므로(core/dash_store.py) 그 이상은 붙이지 않고 같은 번호(200)를 쓴다 — 색은 팔레트·기본 색을 돌려 쓴다.
const MAX_ORDER = 200;
const labelIndex = (order, name) => { const i = order.indexOf(name); return i >= 0 ? i : order.length >= MAX_ORDER ? MAX_ORDER : order.push(name) - 1; };
// 색 정하기: 카드가 지정한 색 → 대시보드 팔레트의 번호째 → 화면 모드의 기본 색. group은 "series"(계열) 또는 "parts"(도넛·파이의 조각).
// 카드에 colors가 없는 임시 카드(탐색 차트)는 계열 순서(index)로 정한다. 지정 색은 라이트·다크가 같다.
function pickColor(card, group, name, colors, index = 0) {
  const c = card.colors, order = c && c[group === "parts" ? "parts_order" : "order"], i = order ? labelIndex(order, name) : index;
  return c?.[group][name] ?? colors.palette?.[i] ?? colors.series[i % colors.series.length];
}

// ---------- 카드의 값 → 그릴 모양 ----------
// 일(YYYY-MM-DD)이 속한 주의 월요일, 월의 1일, 연의 1월 1일. 서버(analysis.trend의 주)와 같다.
const monday = day => { const [y, m, d] = day.split("-").map(Number), t = new Date(Date.UTC(y, m - 1, d)); t.setUTCDate(t.getUTCDate() - (t.getUTCDay() + 6) % 7); return t.toISOString().slice(0, 10); };
const BUCKET = { day: d => d, week: monday, month: d => d.slice(0, 7) + "-01", year: d => d.slice(0, 4) + "-01-01" };
// 일 값 계열 하나를 unit으로 다시 묶은 Map(라벨 → 값). sum=Σsum, count=Σcount, mean=Σsum/Σcount, min·max=min·max(빈 값은 건너뜀).
function rollup(s, days, unit) {
  const groups = new Map();
  days.forEach((day, i) => {
    const key = BUCKET[unit](day), g = groups.get(key) ?? { sum: null, count: 0, min: null, max: null };
    if (s.sum?.[i] != null) g.sum = (g.sum ?? 0) + s.sum[i];
    g.count += s.count?.[i] ?? 0;
    if (s.min?.[i] != null) g.min = g.min == null ? s.min[i] : Math.min(g.min, s.min[i]);
    if (s.max?.[i] != null) g.max = g.max == null ? s.max[i] : Math.max(g.max, s.max[i]);
    groups.set(key, g);
  });
  const value = { count: g => g.count, sum: g => g.sum, mean: g => g.count ? g.sum / g.count : null, min: g => g.min, max: g => g.max }[s.agg];
  return new Map([...groups].map(([key, g]) => [key, value(g)]));
}
// 시간별 값(data.by_unit)을 unit으로. 서버가 그 단위로 따로 구한 계열(고유 개수, 일 값을 자른 월·연)은 그 값을 쓰고, 나머지는 일 값을 다시 묶는다.
// range({start, end}: "YYYY-MM-DD", 없으면 열린 쪽)를 주면 그 기간의 칸만 남긴다(월·연 칸은 그 기간과 겹치는 것).
function byUnit(by, unit, range) {
  const day = by.day, server = new Map((unit === "day" ? [] : by[unit]?.series ?? []).map(s => [s.name, new Map(by[unit].labels.map((l, i) => [l, s.values[i]]))]));
  const maps = day.series.map(s => server.get(s.name) ?? (merges(s.agg) ? rollup(s, day.labels, unit) : unit === "day" ? new Map(day.labels.map((l, i) => [l, s.values[i]])) : new Map()));
  const from = range?.start ? BUCKET[unit](range.start) : "", to = range?.end ?? "9999";
  const labels = [...new Set(maps.flatMap(m => [...m.keys()]))].sort().filter(l => l >= from && l.slice(0, 10) <= to);
  return { keys: labels, labels: labels.map(l => unit === "month" ? l.slice(0, 7) : unit === "year" ? l.slice(0, 4) : l),
           datasets: day.series.map((s, i) => ({ label: s.name, values: labels.map(l => maps[i].get(l) ?? null) })) };
}
// ---------- 기간·비교 (기간 규칙은 서버 period.py, 문장은 compare.py. 여기서는 저장된 일 값으로 두 기간의 값만 구한다) ----------
const dayNumber = day => Date.UTC(+day.slice(0, 4), +day.slice(5, 7) - 1, +day.slice(8, 10)) / 86400000;
const shiftDay = (day, n) => new Date((dayNumber(day) + n) * 86400000).toISOString().slice(0, 10);
const daysBetween = (start, end) => dayNumber(end) - dayNumber(start) + 1;
// 일 값 계열 s의 from~to(포함, 일 글자) 구간 값: 계열의 집계(sum·count·mean·min·max)대로 한 값으로 묶는다. 구간에 일 값이 없으면 null.
function windowValue(s, days, from, to) {
  const keep = days.map((_, i) => i).filter(i => (!from || days[i] >= from) && (!to || days[i] <= to));
  if (!keep.length) return null;
  if (!merges(s.agg)) return keep.length === 1 ? s.values[keep[0]] ?? null : null;  // 고유 개수·중앙값 같은 집계는 여러 날을 다시 묶을 수 없다(하루 값만)
  const pick = a => a && keep.map(i => a[i]);
  return rollup({ agg: s.agg, sum: pick(s.sum), count: pick(s.count), min: pick(s.min), max: pick(s.max) }, keep.map(() => "2000-01-01"), "day").get("2000-01-01") ?? null;
}
// date의 값(그날 값이 없으면 그 전 마지막 값)
function pointValue(s, days, date) {
  for (let i = days.length - 1; i >= 0; i--) if (days[i] <= date) { const v = windowValue(s, days, days[i], days[i]); if (v != null) return v; }
  return null;
}
// 시간별 카드의 첫 계열을 두 기간(또는 두 시점)으로 비교할 값 {cur, prev, mode, label, better, partial}. /api/compare가 문장으로 바꾼다. 안 되는 카드(기간 없음, 고유 개수의 시점 비교·서버가 구한 값 없음)는 null. 고유 개수 계열은 서버의 window_nunique(현재·prev)를 쓴다.
// span: 기간 값과 직전 기간(prev: {start, end, text}) 값, point: 끝 날의 값과 기간 길이만큼 앞 날의 값. 직전이 저장된 일 값보다 앞서면 partial.
// 대시보드 기간이 비면 카드 자신의 기간 줄(날짜컬럼 시작 ~ 끝, 양쪽 날짜)을 보는 기간으로 삼고, 직전은 바로 앞 같은 일수로 본다. 기간 줄이 없으면 null.
function ownRange(card) {
  for (const line of card.source?.body?.periods ?? []) {
    const m = /^\S+\s+(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})$/.exec(line.trim());
    const real = d => !isNaN(Date.parse(d)) && new Date(d).toISOString().slice(0, 10) === d;  // 2026-02-30 같은 없는 날은 건너뛴다
    if (m && real(m[1]) && real(m[2]) && m[1] <= m[2]) { const n = daysBetween(m[1], m[2]); return { range: { start: m[1], end: m[2] }, prev: { start: shiftDay(m[1], -n), end: shiftDay(m[1], -1), text: `직전 ${n}일` } }; }
  }
  return null;
}
function compareItem(card, range, prev) {
  if (!range) { const own = ownRange(card); if (!own) return null; ({ range, prev } = own); }
  const by = card.data?.by_unit, s = by?.day?.series[0];
  if (!s || !range?.start || !range?.end) return null;
  const days = by.day.labels, mode = card.view.compare?.mode ?? "span", better = card.view.better ?? "up", n = daysBetween(range.start, range.end);
  if (!merges(s.agg)) {  // 고유 개수 같은 집계는 일 값을 묶을 수 없어, 서버가 두 기간으로 따로 구한 값(window_nunique와 그 prev)을 쓴다. 기간 비교만.
    const w = card.data?.values?.window_nunique, same = (a, b) => a && a.start === b?.start && a.end === b?.end;
    if (mode !== "span" || !prev?.start || !same(w, range) || !same(w.prev, prev) || w.values["0"] == null || w.prev.values["0"] == null) return null;
    return { cur: w.values["0"], prev: w.prev.values["0"], mode, label: prev.text, better, partial: prev.start < days[0] };
  }
  if (mode === "point") {
    const before = shiftDay(range.end, -n);
    return { cur: pointValue(s, days, range.end), prev: pointValue(s, days, before), mode, label: `${n}일 전`, better, partial: before < days[0] };
  }
  if (!prev?.start) return null;
  return { cur: windowValue(s, days, range.start, range.end), prev: windowValue(s, days, prev.start, prev.end), mode, label: prev.text, better, partial: prev.start < days[0] };
}
// 분석 카드가 어떤 그림인지(없으면 분석 종류에서)
const anaView = card => card.view.analysis_view || { distribution: "hist", correlation: "heat", scatter: "scatter", groups: "groups", trend: "trend" }[card.data?.values?.kind];
// 산점도·버블: 점은 data.points(서버가 구한 점·r·추세선), 버블의 [크기] 컬럼·추세선·r 보이기는 view.points·trend·show_r
const isPoints = card => ["scatter", "bubble"].includes(card.view?.type) && !!card.data?.points;
const pointGroups = p => p.groups.length ? p.groups : [p.y];
// 카드의 값을 그릴 모양 { labels, datasets: [{ label, values, type?, dash?, axis? }], time(날짜 X축), chartable(종류를 바꿀 수 있는지) }. 값이 아직 없으면 null.
function cardSeries(card) {
  if (card.norm) return { time: false, chartable: true, ...card.norm };  // 탐색 탭의 지금 차트처럼 이미 이 모양인 것
  if (isPoints(card)) return { labels: [], datasets: pointGroups(card.data.points).map(label => ({ label, values: [] })), time: false, chartable: true };
  const d = card.data ?? {}, view = card.view ?? {};
  if (d.by_unit?.day) return { ...byUnit(d.by_unit, view.unit || "day", card.range), time: true, chartable: true };
  if (card.kind === "analysis" && d.values) {
    const v = d.values, name = anaView(card);
    if (name === "groups" || name === "trend") return { labels: v.labels, datasets: [{ label: aggName(v.agg), values: v.values }], time: name === "trend", chartable: true };
    return { labels: [], datasets: [], time: false, chartable: false };
  }
  if (d.values?.series) return { labels: d.values.labels, datasets: d.values.series.map((s, i) => ({ label: s.name, values: s.values, ...(view.styles?.[i] ?? {}) })), time: false, chartable: true };
  return null;
}
// 이 종류로 바꿀 수 없는 이유(바꿀 수 있으면 빈 글자). 버튼은 숨기지 않고 누르면 이 이유를 보인다.
function typeReason(card, type) {
  const s = cardSeries(card), names = SERVER.dash.types;
  if (!s) return "아직 값이 없어서 종류를 바꿀 수 없어요. 연결한 뒤 [다시 구하기]를 누르세요.";
  if (!s.chartable) return "이 분석 그림은 종류를 바꿀 수 없어요. 그룹 비교와 시계열 추이만 바꿀 수 있어요.";
  if (type === "scatter" || type === "bubble") {  // X와 값이 숫자 컬럼일 때만(버블은 [크기]도)
    const d = card.data ?? {}, body = card.source?.body ?? {}, y = body.series?.[0]?.y ?? body.y;
    if (d.x_kind !== "num" || !(d.num_cols ?? []).includes(y)) return "산점도·버블은 X가 숫자 컬럼이고 값이 숫자 컬럼일 때만 쓸 수 있어요.";
    return type === "bubble" && !card.view.points?.size ? "버블은 [크기]에 숫자 컬럼을 먼저 고르세요." : "";
  }
  if (s.time && !SERVER.dash.time_types.includes(type)) return `날짜 X축 그림에는 ${SERVER.dash.time_types.map(t => names[t]).join("·")}만 쓸 수 있어요.`;
  if (type === "stacked" && s.datasets.length < 2) return "누적 막대는 계열이 2개 이상일 때만 쓸 수 있어요.";
  return "";
}
// 분석의 원래 그림(anaConfig)을 쓰는 카드인지(종류를 정하지 않았거나 바꿀 수 없는 분석 그림)
const usesAna = card => card.kind === "analysis" && !!card.data?.values && (!card.view.type || !cardSeries(card).chartable);
// 색을 고를 수 있는 이름들: 분석 그림은 주 색·보조 색, 도넛·파이는 조각 이름, 나머지는 계열 이름
function colorNames(card) {
  if (card.kind === "geomap") return { group: "parts", names: (card.data?.values?.regions ?? []).map(r => r.name).slice(0, 30) };  // 지도 카드: 영역마다 색을 따로 정할 수 있다(정하지 않으면 값에 따라 바탕 색의 진하기)
  const s = cardSeries(card);
  if (usesAna(card)) return { group: "series", names: ["주 색", "보조 색"] };
  if (!s) return { group: "series", names: [] };
  return ["doughnut", "pie"].includes(card.view.type) ? { group: "parts", names: s.labels } : { group: "series", names: s.datasets.map(d => d.label) };
}

// ---------- 차트 설정 ----------
// 산점도·버블 설정. 점 하나는 [x, y, (크기), (그룹)]. 버블 반지름 = 3 + 17·√((v−min)/(max−min)) px(면적 비례). 추세선 y = a + b·x와 r은 서버가 전체 행으로 구한 값.
function pointsConfig(card, colors, options) {
  const p = card.data.points, view = card.view, bubble = view.type === "bubble", axis = { ticks: { color: colors.text }, grid: { color: colors.line } };
  const [lo, hi] = p.size_range ?? [0, 0], radius = v => hi > lo ? 3 + 17 * Math.sqrt((v - lo) / (hi - lo)) : 10;
  const datasets = pointGroups(p).map((name, i) => {
    const c = pickColor(card, "series", name, colors, i), rows = p.points.filter(r => !p.groups.length || r[r.length - 1] === name);
    return bubble ? { label: name, data: rows.map(r => ({ x: r[0], y: r[1], r: radius(r[2]) })), backgroundColor: alpha(c, 0.55), borderColor: c }
                  : { label: name, data: rows.map(r => ({ x: r[0], y: r[1] })), backgroundColor: alpha(c, 0.7), pointRadius: 3 };
  });
  if (view.trend && p.b != null) {
    const xs = p.points.map(r => r[0]), lo2 = Math.min(...xs), hi2 = Math.max(...xs);
    datasets.push({ type: "line", label: "추세선", data: [lo2, hi2].map(x => ({ x, y: p.a + p.b * x })), borderColor: colors.text, borderDash: [6, 4], borderWidth: 2, pointRadius: 0 });
  }
  const title = view.show_r && p.r != null ? { display: true, text: `r = ${p.r.toFixed(3)} (${p.n.toLocaleString()}개)`, color: colors.text } : { display: false };
  return { type: bubble ? "bubble" : "scatter", data: { datasets },
           options: { maintainAspectRatio: false, plugins: { legend: { display: datasets.length > 1, labels: { color: colors.text } }, title },
                      scales: { x: { type: "linear", ...axis, title: { display: true, text: p.x, color: colors.text } },
                                y: { type: "linear", ...axis, ...(view.ranges?.y ?? {}), title: { display: true, text: p.y, color: colors.text } } }, ...options } };
}

// 카드 하나(종류 view.type, 값 data, 색 colors)를 그 색(colors: themeColors + 대시보드 palette)으로 그릴 Chart.js 설정.
// view.type이 비어 있으면 계열마다 정한 대로(막대·선 겹치기, 점선, 오른쪽 축). 도넛·파이는 첫 계열만 그린다.
function chartConfig(card, colors, options = {}) {
  const view = card.view ?? {}, type = view.type || "", ranges = view.ranges ?? { y: {}, y1: {} };
  if (usesAna(card)) {
    const main = [pickColor(card, "series", "주 색", colors, 0), pickColor(card, "series", "보조 색", colors, 1)];
    return anaConfig(card.data.values, anaView(card), { ...colors, series: [...main, ...colors.series.slice(2)] }, options);
  }
  if (isPoints(card)) return pointsConfig(card, colors, options);
  const s = cardSeries(card), axis = { ticks: { color: colors.text }, grid: { color: colors.line } };
  const pie = type === "doughnut" || type === "pie", hbar = type === "hbar", stacked = type === "stacked", right = !type && s.datasets.some(d => d.axis === "right");
  const color = (d, i) => pickColor(card, "series", d.label, colors, i);
  const line = ["line", "polyline", "area", "radar"].includes(type);
  const datasets = (pie ? s.datasets.slice(0, 1) : s.datasets).map((d, i) => {
    if (pie) {
      const parts = s.labels.map((l, j) => pickColor(card, "parts", l, colors, j));
      return { label: d.label, data: d.values, backgroundColor: parts, borderColor: colors.card, borderWidth: 2 };
    }
    const c = color(d, i), asLine = line || (!type && d.type === "line");
    if (!type) return { type: d.type || "bar", label: d.label, data: d.values, yAxisID: d.axis === "right" ? "y1" : "y", backgroundColor: c, borderColor: c,
                        borderDash: d.dash === "dashed" ? [6, 4] : [], borderWidth: asLine ? 2 : 0, pointRadius: 2, spanGaps: true, order: asLine ? 0 : 1 };
    if (!line) return { label: d.label, data: d.values, backgroundColor: c };
    return { label: d.label, data: d.values, borderColor: c, backgroundColor: type === "area" || type === "radar" ? alpha(c, 0x55 / 255) : c, fill: type === "area" || type === "radar",
             tension: type === "line" ? 0.4 : 0, borderWidth: 2, pointRadius: s.labels.length > 60 ? 0 : 2, spanGaps: true };
  });
  const legend = { display: pie || datasets.length > 1, labels: { color: colors.text } };
  const scales = pie ? {} : type === "radar" ? { r: { ticks: { color: colors.text, backdropColor: "transparent" }, grid: { color: colors.line }, pointLabels: { color: colors.text } } }
    : { x: { ...axis, ...(stacked ? { stacked: true } : {}), ...(hbar ? ranges.y : {}) }, y: { ...axis, ...(stacked ? { stacked: true } : {}), ...(hbar ? {} : ranges.y) },
        ...(right ? { y1: { ...axis, position: "right", grid: { drawOnChartArea: false }, ...ranges.y1 } } : {}) };
  return { type: { doughnut: "doughnut", pie: "pie", radar: "radar", polyline: "line", area: "line", line: "line", hbar: "bar", stacked: "bar" }[type] ?? "bar",
           data: { labels: s.labels, datasets },
           options: { maintainAspectRatio: false, ...(hbar ? { indexAxis: "y" } : {}), plugins: { legend }, scales, ...options } };
}

// 최신화가 읽은 날짜 범위(data.window: 직전 기간 시작 ~ 보는 기간 끝) 밖으로 보는 기간이 나갔는지. 나갔으면 그 기간으로 다시 읽어야 한다.
const outsideWindow = (card, range) => !!(range && card.data?.window && ((range.start && card.data.window.start && range.start < card.data.window.start) || (range.end && card.data.window.end && range.end > card.data.window.end)));
// 새 카드의 빈 색 설정(지정 색과 라벨이 처음 나온 순서)
const freshColors = () => ({ series: {}, parts: {}, order: [], parts_order: [] });
// 값이 아직 없는 카드(예전 파일에서 옮긴 것)는 안내만 보인다. SVG 카드는 일 값·값·컬럼 이름 중 하나라도 있으면 그린다.
const noValues = c => SVG_KINDS.includes(c.kind) ? !(c.data?.by_unit || c.data?.values || c.data?.columns) : !cardSeries(c);
const needsValues = c => (c.note = noValues(c) ? "아직 구한 값이 없어요. 연결한 뒤 [다시 구하기]를 누르세요." : undefined, c);

// ---------- 카드 모음 ----------
// board.js의 Board에 카드마다 차트 종류·시간 단위·색을 고르는 줄을 더한다. palette는 대시보드의 색 묶음(HEX 목록).
// 카드는 wrap()으로 감싸 쓴다(config를 붙이고 보이는 비율을 view.ratio에서 가져온다). 저장할 때는 plain()으로 서버 모양만 남긴다.
class DashBoard extends Board {
  constructor(grid, options) {
    super(grid, options);
    this.palette = [];
    this.range = null;  // 보는 기간 {start, end}(기준일로 자른 것, 열린 쪽은 없음). 시간별 카드는 이 기간의 일 값만 그린다.
    this.prev = null;  // 직전 기간 {start, end, text}(비교 문장용)
    this.compares = new Map();  // 카드 → 비교 문장 {text, tone, ...}
    this.dashId = "";  // 지금 열어 둔 저장본의 id(배치도의 그림 파일을 서버에서 받을 때 쓴다). 화면이 정한다.
    this.recompute = options.recompute;  // (카드들) → 그 카드들을 보는 기간으로 다시 구한다(연결이 있어야 한다). 화면이 정한다.
    this.connKey = "";  // 지금 연결의 열쇠(비밀번호·계정 없음). 있으면 다른 연결의 카드를 "이전 값"으로 보인다.
  }
  // 보는 기간과 직전 기간을 바꾸고 비교 문장을 다시 받아 그린다(서버 /api/compare).
  async setRange(range, prev) {
    this.range = range;
    this.prev = prev;
    const unique = c => SVG_KINDS.includes(c.kind) && c.data?.by_unit?.day?.series.some(s => !merges(s.agg));  // 고유 개수·중앙값 같은 집계는 기간으로 다시 구해야 한다
    const was = this.hadRange;  // 기간이 비었으면(카드의 원래 조건대로) 방금 전까지 기간이 걸려 있던 때만 다시 구한다
    this.hadRange = !!range;
    const dated = c => (range || was) && c.source?.date && !c.data?.by_unit && c.kind !== "recent_rows";
    const stale = this.cards.filter(c => dated(c) || unique(c) || outsideWindow(c, this.range));  // 날짜 X축이 아닌데 기간 기준 날짜 컬럼이 있는 카드는 그 기간으로 다시 구한다
    if (stale.length && this.recompute) await this.recompute(stale);  // 다시 구한 뒤 끝에 비교도 새로 받아 그린다
    else await this.recompare();
  }
  // 새 카드·다시 구할 때 서버에 알려 줄 보는 기간(없으면 undefined)
  ctx() {
    return this.range ? { range: this.range, prev: this.prev } : undefined;
  }
  async recompare() {
    this.cards.forEach(c => { c.range = this.range; });
    const items = [], who = [], seq = this.cmpSeq = (this.cmpSeq ?? 0) + 1;  // 기간을 연달아 바꾸면 늦게 온 옛 응답이 새 기간의 문장을 덮지 않게 번호로 가린다
    this.cards.forEach(c => { const item = compareItem(c, this.range, this.prev); if (item) { items.push(item); who.push(c); } });
    this.compares = new Map();
    if (items.length) {
      try {
        const got = (await api("/api/compare", { items })).results;
        if (seq !== this.cmpSeq) return;
        got.forEach((r, i) => this.compares.set(who[i], r));
      } catch (e) { if (seq !== this.cmpSeq) return; /* 문장을 못 받으면 문장 없이 그린다 */ }
    }
    this.draw();
  }
  wrap(card) {
    const paint = SVG_KINDS.includes(card.kind);  // SVG·HTML로 직접 그리는 카드는 config 대신 paint
    return Object.assign(card, { range: this.range, ratio: card.view?.ratio || (paint ? 4 / 3 : 16 / 9), paint: paint ? colors => paintCard(card, { ...colors, palette: this.palette }, this) : undefined,
                                 config: paint ? undefined : (colors, options) => chartConfig(card, { ...colors, palette: this.palette }, options) });
  }
  set(cards) {
    super.set(cards.map(c => this.wrap(c)));
    if (this.range) this.recompare();
  }
  // 저장하는 카드 모양 { id, kind, title, layout, colors, view, source, data }. 화면에서만 쓰는 항목(config, canvas, note…)은 뺀다.
  static plain({ id, kind, title, layout, colors, view, source, data }) {
    return { id, kind, title, layout, colors, view, source, data };
  }
  cardBox(card, colors) {
    const box = super.cardBox(card, colors);
    box.insertBefore(this.tools(card), box.querySelector(".bbody"));
    return box;
  }
  // 종류 버튼들(바꿀 수 없는 종류는 흐리게, 눌러도 이유만), 시간 단위, 색 편집
  // 카드의 "기간 기준 날짜 컬럼": 날짜 X축이 아닌 카드에 대시보드의 기간·기준일을 걸 컬럼. 후보는 테이블의 날짜 컬럼(서버가 data.date_cols로 줌). 바꾸면 그 기간으로 다시 구한다.
  dateSelect(card) {
    const cols = card.data?.date_cols ?? [];
    if (!cols.length) return "";
    return el("select", { className: "bunit", style: "flex-basis:auto", title: "기간 기준 날짜 컬럼: 대시보드의 기간·기준일을 이 컬럼으로 걸어 다시 구합니다", onchange: async e => {
      if (e.target.value) card.source.date = e.target.value; else delete card.source.date;
      this.changed?.();
      if (card.source.date) await this.recompute?.([card]);
      this.draw();
    } }, el("option", { value: "", textContent: "기간 기준: 없음" }), ...cols.map(c => el("option", { value: c, textContent: "기간 기준: " + c, selected: card.source.date === c })));
  }
  // SVG 카드의 설정 줄: 목표 값, 값 모양, 종류별 칸(고리 방식·칸 수·블록 크기·행 수)
  svgTools(card, msg, again) {
    const view = card.view, field = (label, input) => el("label", { className: "hint bset" }, label, input);
    const number = (key, label, width = 70, limit = {}) => field(label, el("input", { type: "number", ...limit, value: view[key] ?? "", style: `width:${width}px`, onchange: e => { if (e.target.value === "") delete view[key]; else view[key] = e.target.value = Math.min(limit.max ?? Infinity, Math.max(limit.min ?? -Infinity, +e.target.value)); again(); } }));  // 칸 수·행 수는 서버 범위(1~100)로 맞춘다
    const choice = (key, label, options) => field(label, el("select", { className: "bunit", style: "flex-basis:auto", onchange: e => { view[key] = e.target.value; again(); } }, ...options.map(([v, n]) => el("option", { value: v, textContent: n, selected: (view[key] || options[0][0]) === v }))));
    const named = view.target?.name;
    const target = named ? el("span", { className: "hint", textContent: `목표: ${named}${card.data?.target?.error ? " (" + card.data.target.error + ")" : ""}` })
      : field("목표", el("input", { type: "number", value: view.target?.value ?? "", style: "width:90px", onchange: e => { if (e.target.value === "") { delete view.target; card.data.target = null; } else { view.target = { value: +e.target.value }; card.data.target = { value: +e.target.value, label: "", error: "" }; } again(); } }));
    const tiles = field("지도 배경(OpenStreetMap)", el("input", { type: "checkbox", checked: !!view.geo?.tiles, title: "켜면 지금 보는 지도 범위가 타일 서버(tile.openstreetmap.org)로 요청돼요. 끄면 경계만 그려요(서버 요청 없음).",
      onchange: e => { view.geo = { ...view.geo, tiles: e.target.checked }; again(); } }));
    const kinds = { geomap: [choice("format", "값", [["number", "숫자"], ["percent", "%"], ["currency", "₩"]]), tiles, el("span", { className: "hint", textContent: view.geo?.tiles ? "지도 배경을 켜면 보는 범위가 tile.openstreetmap.org로 요청돼요." : "" })],
                    donut_kpi: [choice("ring", "고리", [["stack", "한 고리 누적"], ["double", "겹친 두 고리"], ["half", "반원 게이지"], ["ticks", "고리+목표 눈금"]])], stripes: [number("cells", "칸 수", 50, { min: 1, max: 100, step: 1 }), number("full", "가득 찬 값", 80)],
                    blocks: [number("size", "한 칸 크기", 80)], recent_rows: [number("rows", "행 수", 50, { min: 1, max: 100, step: 1 })] }[card.kind] ?? [];
    const plain = ["recent_rows", "gauge", "status", "alerts", "map", "deviation", "table", "sentence", "funnel", "geomap"].includes(card.kind);  // 목표·값 모양이 없는 종류
    const settings = plain ? [...kinds] : [target, choice("format", "값", [["number", "숫자"], ["percent", "%"], ["currency", "₩"]]), ...kinds];
    const rules = ["table", "sentence"].includes(card.kind) ? [el("button", { className: "btype" + (card.openRules ? " on" : ""), textContent: "색 규칙", title: "조건에 맞는 칸만 색·화살표를 입힙니다", onclick: () => { card.openRules = !card.openRules; this.draw(); } })] : [];
    const row = el("div", { className: "btools" }, this.dateSelect(card), ...settings, ...rules, el("button", { className: "btype" + (card.openColors ? " on" : ""), textContent: "색", onclick: () => { card.openColors = !card.openColors; this.draw(); } }));
    row.onclick = e => e.stopPropagation();
    return row;
  }
  tools(card) {
    const msg = el("div", { className: "hint btype-msg" });
    const again = () => { this.changed?.(); this.draw(); };
    const buttons = Object.entries(SERVER.dash.types).map(([id, name]) => el("button", { className: "btype" + (card.view.type === id ? " on" : "") + (typeReason(card, id) ? " off" : ""), textContent: name,
      onclick: async () => {
        const why = typeReason(card, id);
        if (why) return void (msg.textContent = why);
        const before = card.view.type;
        card.view.type = before === id ? "" : id;  // 한 번 더 누르면 계열마다 정한 대로
        try { await this.loadPoints(card); } catch (e) { card.view.type = before; return void (msg.textContent = e.message); }
        again();
      } }));
    const pointy = ["scatter", "bubble"].includes(card.view.type);
    const size = pointy || card.data?.x_kind === "num" ? el("select", { className: "bunit", title: "버블의 [크기] 컬럼(숫자)", onchange: async e => {
      card.view.points = { size: e.target.value };
      try { await this.loadPoints(card); } catch (err) { return void (msg.textContent = err.message); }
      again();
    } }, el("option", { value: "", textContent: "크기 없음" }), ...(card.data?.num_cols ?? []).map(c => el("option", { value: c, textContent: c, selected: card.view.points?.size === c }))) : "";
    const flags = pointy ? [["trend", "추세선"], ["show_r", "r"]].map(([k, name]) => el("button", { className: "btype" + (card.view[k] ? " on" : ""), textContent: name, title: k === "r" ? "상관계수 r 보이기" : "최소제곱 추세선 보이기",
      onclick: () => { card.view[k] = !card.view[k]; again(); } })) : [];
    const unit = card.data?.by_unit ? el("select", { className: "bunit", title: "시간 단위. 일 값을 다시 묶어 그립니다", onchange: e => { card.view.unit = e.target.value; again(); } },
      ...Object.entries(SERVER.dash.units).map(([id, name]) => el("option", { value: id, textContent: name, selected: (card.view.unit || "day") === id }))) : "";
    const paint = el("button", { className: "btype" + (card.openColors ? " on" : ""), textContent: "색", onclick: () => { card.openColors = !card.openColors; this.draw(); } });
    const tools = SVG_KINDS.includes(card.kind) ? this.svgTools(card, msg, again) : el("div", { className: "btools" }, ...buttons, size, ...flags, unit, !card.data?.by_unit ? this.dateSelect(card) : "", paint);
    tools.onclick = e => e.stopPropagation();  // 카드 고르기(Board의 box.onclick)로 번지지 않게
    const d = card.data ?? {}, first = d.by_unit?.day?.labels[0], range = this.range;
    const noDate = range && !d.by_unit && !card.source?.date && !["recent_rows", "status", "alerts", "map"].includes(card.kind) && !isPoints(card);  // 기간 기준 날짜 컬럼이 없으면 기간이 걸리지 않는다
    const applies = card.source?.date && !d.by_unit ? `기간 기준 날짜: ${card.source.date}` : noDate ? "기간·기준일은 날짜 X축 카드나 기간 기준 날짜 컬럼을 고른 카드에만 적용돼요" : "";
    const early = range?.start && first && range.start < first ? `저장된 일 값은 ${first}부터예요` : "";  // 보려는 기간이 저장된 값보다 앞서면
    const lostUnique = range && SVG_KINDS.includes(card.kind) && d.by_unit?.day?.series.some((s, i) => !merges(s.agg) && cardValue(card, i) == null) ? "이 집계(고유 개수·중앙값 …)는 기간을 바꾸면 연결해서 다시 구해야 해요" : "";
    const stale = this.connKey && card.source?.key && !sameSource(card.source.key, this.connKey) ? "이전 값(다른 연결)" : "";  // 지금 연결이 아닌 곳에서 구한 값
    const when = [stale, d.computed_at && "구한 시각 " + d.computed_at.replace("T", " "), d.note, early, applies, card.rangeNote, lostUnique].filter(Boolean).join(" · ");  // 값이 언제 것인지, 줄인 것
    const said = this.compares.get(card), timed = !!d.by_unit?.day && card.kind !== "dual_trend", mergeable = timed && merges(d.by_unit.day.series[0]?.agg);  // 고유 개수 같은 집계는 기간 비교만 되므로 방식 선택은 묶을 수 있는 집계에만, 좋은 방향은 둘 다
    const pick = (value, options, set) => el("select", { className: "bunit", style: "flex-basis:auto", onchange: e => { set(e.target.value); this.changed?.(); this.recompare(); } },
      ...options.map(([v, name]) => el("option", { value: v, textContent: name, selected: v === value })));
    const how = timed ? el("div", { className: "btools" }, ...(mergeable ? [pick(card.view.compare?.mode ?? "span", [["span", "기간 비교"], ["point", "시점 비교"]], v => { card.view.compare = { mode: v }; })] : []),
      pick(card.view.better ?? "up", [["up", "늘수록 좋음"], ["down", "줄수록 좋음"]], v => { card.view.better = v; })) : "";
    return el("div", { className: "bextra" }, tools, card.openRules ? ruleEditor(card, again) : "", msg, el("div", { className: "hint bwhen", textContent: when }),
      said && card.kind !== "kpi" ? el("div", { className: "bcmp " + said.tone, textContent: said.text, title: said.delta_text }) : "", how, card.openColors ? this.colorEditor(card) : "");
  }
  // 산점도·버블이면 서버에서 점을 받아 카드에 둔다(연결 필요). 다른 종류로 바꾸면 점은 버린다(카드 크기).
  async loadPoints(card) {
    const type = card.view.type;
    if (!["scatter", "bubble"].includes(type)) { delete card.data.points; return; }
    const body = card.source.body, size = type === "bubble" ? card.view.points?.size ?? "" : "";
    card.data.points = (await api("/api/chart/points", { ...body, y: body.series?.[0]?.y, size, source_key: card.source.key })).points;
  }
  // 이름마다 HEX 칸. 틀린 값은 칸 옆에 이유를 보이고 이전 색을 그대로 둔다.
  colorEditor(card) {
    const colors = { ...themeColors(document.documentElement.dataset.theme), palette: this.palette }, { group, names } = colorNames(card);
    const rows = names.map((name, i) => {
      // 지도 카드: 정하지 않은 영역은 모두 바탕 색(값에 따라 진하기만 다름)
      const now = card.kind === "geomap" && !card.colors[group]?.[name] ? card.view.geo?.color ?? colors.palette?.[0] ?? colors.series[0] : pickColor(card, group, name, colors, i), err = el("span", { className: "err" }), swatch = el("i", { className: "swatch" });
      swatch.style.background = now;
      const input = el("input", { value: now, title: "HEX 색(예: #11706f)" });
      input.onchange = () => {
        const v = input.value.trim();
        if (!HEX.test(v)) { input.value = now; err.textContent = `'${v}'은 HEX 색이 아니에요(예: #11706f). 이전 색을 그대로 둬요.`; return; }
        card.colors[group][name] = v;
        this.changed?.();
        this.draw();
      };
      return el("div", { className: "brow" }, swatch, el("span", { textContent: name, className: "bname" }), input, err);
    });
    const box = el("div", { className: "bcolors" }, ...rows, el("button", { className: "ghost", textContent: "지정 색 지우기", title: "이 카드의 지정 색을 지우고 팔레트·기본 색으로 돌아갑니다",
      onclick: () => { card.colors.series = {}; card.colors.parts = {}; this.changed?.(); this.draw(); } }));
    box.onclick = e => e.stopPropagation();
    return box;
  }
}

// 알림 줄(id)에 대시보드에 넣은 결과 글과 "대시보드에서 보기" 링크를 보인다(쿼리·전처리 화면). added: { id, name, cards }
function showAdded(id, added) {
  say(id, `대시보드 '${added.name}'에 넣었습니다(카드 ${added.cards}개). `, true);
  $(id).append(el("a", { href: "/dashboard?open=" + encodeURIComponent(added.id), textContent: "대시보드에서 보기" }));
}
