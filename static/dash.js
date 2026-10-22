// 대시보드 공통 그리기: 카드의 저장된 값(data)을 Chart.js 설정(chartConfig)으로 바꾸고, 카드 모음(DashBoard)에 차트 종류·시간 단위·색을 고르는 줄을 더한다.
// 쿼리 화면의 대시보드 탭이 쓰고, 분석 화면은 분석 그림(anaConfig)을 가져다 쓴다(Chart.js, common.js, board.js가 먼저 불려 있어야 한다). 종류·단위·상한·기본 색은 서버(SERVER.dash)가 준다.
// 카드 모양은 dash_store.py: { id, kind, title, layout, colors, view, source, data }. data.by_unit은 시간별(일 값 + 서버가 따로 구한 값), data.values는 항목별·지표·분석 결과.

// ---------- 분석 그림 (분석 화면과 대시보드의 분석 카드가 함께 쓴다) ----------
const AGG_NAMES = { count: "개수", sum: "합계", mean: "평균" };
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
  if (view === "groups") return chart("bar", d.labels, [{ label: AGG_NAMES[d.agg], data: d.values, backgroundColor: main }],
    { x: { ...axis, title: title(d.by) }, y: { ...axis, title: title(d.agg === "count" ? "개수" : `${d.value} ${AGG_NAMES[d.agg]}`) } });
  return chart("line", d.labels, [{ label: AGG_NAMES[d.agg], data: d.values, borderColor: main, backgroundColor: main, borderWidth: 2, pointRadius: d.labels.length > 60 ? 0 : 2, spanGaps: true }],
    { x: { ...axis, title: title(UNIT_NAMES[d.unit]) }, y: { ...axis, title: title(d.agg === "count" ? "개수" : `${d.value} ${AGG_NAMES[d.agg]}`) } });
}

// ---------- 색 ----------
// 라벨의 번호: 카드가 그 라벨을 처음 본 차례(order). 새 라벨은 뒤에 붙여 카드에 남기므로 다시 구한 뒤에도 같은 라벨은 같은 번호(같은 색)다.
const labelIndex = (order, name) => { const i = order.indexOf(name); return i < 0 ? order.push(name) - 1 : i; };
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
function byUnit(by, unit) {
  const day = by.day, server = new Map((unit === "day" ? [] : by[unit]?.series ?? []).map(s => [s.name, new Map(by[unit].labels.map((l, i) => [l, s.values[i]]))]));
  const maps = day.series.map(s => server.get(s.name) ?? (s.agg !== "nunique" ? rollup(s, day.labels, unit) : unit === "day" ? new Map(day.labels.map((l, i) => [l, s.values[i]])) : new Map()));
  const labels = [...new Set(maps.flatMap(m => [...m.keys()]))].sort();
  return { keys: labels, labels: labels.map(l => unit === "month" ? l.slice(0, 7) : unit === "year" ? l.slice(0, 4) : l),
           datasets: day.series.map((s, i) => ({ label: s.name, values: labels.map(l => maps[i].get(l) ?? null) })) };
}
// 분석 카드가 어떤 그림인지(없으면 분석 종류에서)
const anaView = card => card.view.analysis_view || { distribution: "hist", correlation: "heat", scatter: "scatter", groups: "groups", trend: "trend" }[card.data?.values?.kind];
// 카드의 값을 그릴 모양 { labels, datasets: [{ label, values, type?, dash?, axis? }], time(날짜 X축), chartable(종류를 바꿀 수 있는지) }. 값이 아직 없으면 null.
function cardSeries(card) {
  if (card.norm) return { time: false, chartable: true, ...card.norm };  // 탐색 탭의 지금 차트처럼 이미 이 모양인 것
  const d = card.data ?? {}, view = card.view ?? {};
  if (d.by_unit?.day) return { ...byUnit(d.by_unit, view.unit || "day"), time: true, chartable: true };
  if (card.kind === "analysis" && d.values) {
    const v = d.values, name = anaView(card);
    if (name === "groups" || name === "trend") return { labels: v.labels, datasets: [{ label: AGG_NAMES[v.agg], values: v.values }], time: name === "trend", chartable: true };
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
  if (s.time && !SERVER.dash.time_types.includes(type)) return `날짜 X축 그림에는 ${SERVER.dash.time_types.map(t => names[t]).join("·")}만 쓸 수 있어요.`;
  if (type === "stacked" && s.datasets.length < 2) return "누적 막대는 계열이 2개 이상일 때만 쓸 수 있어요.";
  return "";
}
// 분석의 원래 그림(anaConfig)을 쓰는 카드인지(종류를 정하지 않았거나 바꿀 수 없는 분석 그림)
const usesAna = card => card.kind === "analysis" && !!card.data?.values && (!card.view.type || !cardSeries(card).chartable);
// 색을 고를 수 있는 이름들: 분석 그림은 주 색·보조 색, 도넛·파이는 조각 이름, 나머지는 계열 이름
function colorNames(card) {
  const s = cardSeries(card);
  if (usesAna(card)) return { group: "series", names: ["주 색", "보조 색"] };
  if (!s) return { group: "series", names: [] };
  return ["doughnut", "pie"].includes(card.view.type) ? { group: "parts", names: s.labels } : { group: "series", names: s.datasets.map(d => d.label) };
}

// ---------- 차트 설정 ----------
// 카드 하나(종류 view.type, 값 data, 색 colors)를 그 색(colors: themeColors + 대시보드 palette)으로 그릴 Chart.js 설정.
// view.type이 비어 있으면 계열마다 정한 대로(막대·선 겹치기, 점선, 오른쪽 축). 도넛·파이는 첫 계열만 그린다.
function chartConfig(card, colors, options = {}) {
  const view = card.view ?? {}, type = view.type || "", ranges = view.ranges ?? { y: {}, y1: {} };
  if (usesAna(card)) {
    const main = [pickColor(card, "series", "주 색", colors, 0), pickColor(card, "series", "보조 색", colors, 1)];
    return anaConfig(card.data.values, anaView(card), { ...colors, series: [...main, ...colors.series.slice(2)] }, options);
  }
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

// ---------- 카드 모음 ----------
// board.js의 Board에 카드마다 차트 종류·시간 단위·색을 고르는 줄을 더한다. palette는 대시보드의 색 묶음(HEX 목록).
// 카드는 wrap()으로 감싸 쓴다(config를 붙이고 보이는 비율을 view.ratio에서 가져온다). 저장할 때는 plain()으로 서버 모양만 남긴다.
class DashBoard extends Board {
  constructor(grid, options) {
    super(grid, options);
    this.palette = [];
  }
  wrap(card) {
    return Object.assign(card, { ratio: card.view?.ratio || 16 / 9, config: (colors, options) => chartConfig(card, { ...colors, palette: this.palette }, options) });
  }
  set(cards) {
    super.set(cards.map(c => this.wrap(c)));
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
  tools(card) {
    const msg = el("div", { className: "hint btype-msg" });
    const again = () => { this.changed?.(); this.draw(); };
    const buttons = Object.entries(SERVER.dash.types).map(([id, name]) => el("button", { className: "btype" + (card.view.type === id ? " on" : "") + (typeReason(card, id) ? " off" : ""), textContent: name,
      onclick: () => {
        const why = typeReason(card, id);
        if (why) return void (msg.textContent = why);
        card.view.type = card.view.type === id ? "" : id;  // 한 번 더 누르면 계열마다 정한 대로
        again();
      } }));
    const unit = card.data?.by_unit ? el("select", { className: "bunit", title: "시간 단위. 일 값을 다시 묶어 그립니다", onchange: e => { card.view.unit = e.target.value; again(); } },
      ...Object.entries(SERVER.dash.units).map(([id, name]) => el("option", { value: id, textContent: name, selected: (card.view.unit || "day") === id }))) : "";
    const paint = el("button", { className: "btype" + (card.openColors ? " on" : ""), textContent: "색", onclick: () => { card.openColors = !card.openColors; this.draw(); } });
    const tools = el("div", { className: "btools" }, ...buttons, unit, paint);
    tools.onclick = e => e.stopPropagation();  // 카드 고르기(Board의 box.onclick)로 번지지 않게
    const d = card.data ?? {}, when = [d.computed_at && "구한 시각 " + d.computed_at.replace("T", " "), d.note].filter(Boolean).join(" · ");  // 값이 언제 것인지, 줄인 것
    return el("div", { className: "bextra" }, tools, msg, el("div", { className: "hint bwhen", textContent: when }), card.openColors ? this.colorEditor(card) : "");
  }
  // 이름마다 HEX 칸. 틀린 값은 칸 옆에 이유를 보이고 이전 색을 그대로 둔다.
  colorEditor(card) {
    const colors = { ...themeColors(document.documentElement.dataset.theme), palette: this.palette }, { group, names } = colorNames(card);
    const rows = names.map((name, i) => {
      const now = pickColor(card, group, name, colors, i), err = el("span", { className: "err" }), swatch = el("i", { className: "swatch" });
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
