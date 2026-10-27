// 대시보드 카드 종류(3단계)를 SVG·HTML로 직접 그린다: kpi, donut_kpi, gauge, summary, recent_rows, dual_trend, stripes, blocks, target_trend. Chart.js를 쓰지 않는다.
// 위쪽은 값 → 모양 계산(호·고리·칸·교차·다각형)이라 화면 없이 시험할 수 있다. 아래는 그 계산으로 SVG를 만드는 부분이다(dash.js의 windowValue·pickColor를 쓰므로 dash.js 다음에 불러 온다).
// 카드의 값은 data.by_unit.day(일 값, 기간 card.range로 묶음)나 data.values.scalar(날짜 컬럼 없이 구한 값)이고, 목표는 data.target.value(없으면 null)다.

const SVG_KINDS = ["kpi", "donut_kpi", "gauge", "summary", "recent_rows", "dual_trend", "stripes", "blocks", "target_trend", "table", "status", "sentence", "alerts", "map", "deviation", "funnel", "geomap"];
const TAU = Math.PI * 2, RING_GAP = 0.06;  // 게이지 조각 사이 간격(라디안)
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
// 12시 방향이 0, 시계 방향으로 커지는 각도의 점
const polar = (cx, cy, r, a) => [cx + r * Math.sin(a), cy - r * Math.cos(a)];
// 각도 a0에서 a1까지(시계 방향) 호의 SVG 경로. 한 바퀴 가까우면 둘로 나눈다.
function arcPath(cx, cy, r, a0, a1) {
  if (a1 - a0 >= TAU - 1e-6) return arcPath(cx, cy, r, a0, a0 + Math.PI) + " " + arcPath(cx, cy, r, a0 + Math.PI, a0 + TAU - 1e-4);
  const [x0, y0] = polar(cx, cy, r, a0), [x1, y1] = polar(cx, cy, r, a1);
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}
// 고리(circle)의 stroke-dasharray: 원주 2πr 중 비율만큼만 칠한다.
function ringDash(r, ratio) {
  const circ = TAU * r, len = circ * clamp(ratio, 0, 1);
  return { circ, len, dash: `${len.toFixed(2)} ${(circ - len).toFixed(2)}` };
}
// 항목 값들의 몫으로 나눈 호들(조각 사이 간격 RING_GAP). 값이 0 이하이거나 빈 항목은 뺀다. 가장 큰 항목에 max 표시(굵게 그린다).
function gaugeSegments(values, gap = RING_GAP) {
  const items = values.map((v, i) => ({ i, v })).filter(x => x.v != null && x.v > 0), total = items.reduce((a, x) => a + x.v, 0);
  if (!total) return [];
  const room = TAU - gap * items.length, top = Math.max(...items.map(x => x.v));
  let at = 0;
  return items.map(x => { const span = room * x.v / total, seg = { index: x.i, share: x.v / total, a0: at, a1: at + span, max: x.v === top }; at += span + gap; return seg; });
}
// 칸 채우기: 채운 칸 = round(비율 × n), 목표 칸(1~n)은 목표 비율로
function stripeCells(ratio, n, targetRatio) {
  return { filled: Math.round(clamp(ratio, 0, 1) * n), target: targetRatio == null ? null : clamp(Math.round(targetRatio * n), 1, n) };
}
// 블록: 한 칸 = size. 실제 칸, 목표까지 모자란 칸, 목표를 넘은 칸(실제 칸 안의 마지막 over 칸). 칸이 cap을 넘으면 같은 비율로 줄인다(scale = 줄인 배수).
function blockCells(value, target, size, cap = 200) {
  const actual = Math.floor(value / size), goal = target == null ? actual : Math.floor(target / size), scale = Math.max(1, Math.ceil(Math.max(actual, goal) / cap));
  return { actual: Math.floor(actual / scale), toTarget: Math.max(0, Math.floor(goal / scale) - Math.floor(actual / scale)), over: Math.max(0, Math.floor((actual - goal) / scale)), scale };
}
// 값이 목표선을 위에서 아래로 지나간 횟수(앞 값 ≥ 목표 > 지금 값). 빈 값은 건너뛴다.
function crossDown(values, target) {
  let n = 0, before = null;
  for (const v of values) { if (v == null) continue; if (before != null && before >= target && v < target) n++; before = v; }
  return n;
}
// 선(xs, vs)이 목표(target) 아래에 있는 구간과 목표선 사이의 다각형들 [[x, y], …]. 목표선을 지나는 곳은 선분 위에서 보간한다. yOf는 값 → 화면 y.
function belowPolygons(xs, vs, target, yOf) {
  const polys = [], ty = yOf(target);
  let run = null;
  const close = x => { if (run) { run.push([x, ty]); polys.push(run); run = null; } };
  for (let i = 0; i < vs.length; i++) {
    const v = vs[i];
    if (v == null) { close(xs[i - 1]); continue; }
    const prev = i > 0 ? vs[i - 1] : null;
    if (prev != null && (prev < target) !== (v < target)) {  // 목표선을 지나는 선분: 지나는 x
      const x = xs[i - 1] + (xs[i] - xs[i - 1]) * (target - prev) / (v - prev);
      if (v < target) run = [[x, ty]]; else close(x);
    }
    if (v < target) { if (!run) run = [[xs[i], ty]]; run.push([xs[i], yOf(v)]); }
  }
  close(xs[xs.length - 1]);
  return polys;
}
// 값들을 w×h 칸에 선으로 놓은 점들 "x,y x,y …"(빈 값은 건너뜀). 모두 같으면 가운데 줄.
function sparkPoints(values, w, h, pad = 2) {
  const real = values.filter(v => v != null), lo = Math.min(...real), hi = Math.max(...real), n = values.length;
  return values.map((v, i) => v == null ? null : `${(pad + (n < 2 ? (w - 2 * pad) / 2 : (w - 2 * pad) * i / (n - 1))).toFixed(1)},${(hi === lo ? h / 2 : h - pad - (h - 2 * pad) * (v - lo) / (hi - lo)).toFixed(1)}`).filter(Boolean).join(" ");
}
// 값 글자(서버 core/compare.format_value와 같은 모양): number는 쉼표·소수 둘째 자리, percent는 ×100 소수 둘째 자리 %, currency는 ₩ + 쉼표(음수는 -₩). unit은 뒤에 붙인다.
// 숫자가 아닌 값(string_agg·first·any_value 등)은 글자 그대로.
function showValue(v, format = "number", unit = "") {
  if (v == null) return "—";
  if (typeof v !== "number") return String(v) + unit;
  const text = format === "percent" ? roundText(v * 100, 2) + "%" : format === "currency" ? roundText(v, 0, "₩") : roundText(v, 2);
  return text + unit;
}
// compare.trimmed와 같은 규칙: 절댓값을 d자리에서 5를 올려 반올림(2.5 → 3), 쉼표, 끝의 0을 떼고, -0은 0. prefix(₩)는 부호 뒤에.
function roundText(x, d, prefix = "") {
  const a = Math.abs(x), s = String(a), r = s.includes("e") ? +a.toFixed(d) : Math.round(+`${s}e${d}`) / 10 ** d;  // 10진 글자 그대로 반올림(1.005 → 1.01, toFixed는 1.00)
  return (x < 0 && r ? "-" : "") + prefix + r.toLocaleString(undefined, { maximumFractionDigits: d });
}
// 목표와의 차이 글자: percent는 %p, 그 밖은 값 모양대로
const showGap = (v, format) => format === "percent" ? `${+(Math.abs(v) * 100).toFixed(1)}%p` : showValue(Math.abs(v), format);
// 어두운 색(목표를 넘은 부분): #rrggbb를 비율만큼 어둡게
function darken(hex, k = 0.35) {
  const h = hex6(hex);
  return "#" + [1, 3, 5].map(i => Math.round(parseInt(h.slice(i, i + 2), 16) * (1 - k)).toString(16).padStart(2, "0")).join("");
}

// ---------- 카드의 값 ----------
// 기간(card.range)의 계열 i 값: 일 값이 있으면 기간으로 묶고, 없으면 날짜 컬럼 없이 구한 값
function cardValue(card, i = 0) {
  const by = card.data?.by_unit?.day;
  if (by && by.series[i] && !merges(by.series[i].agg) && card.range && by.labels.some(d => (!card.range.start || d >= card.range.start) && (!card.range.end || d <= card.range.end)) && windowValue(by.series[i], by.labels, card.range.start, card.range.end) == null) {
    const w = card.data.values?.window_nunique;  // 고유 개수는 일 값을 묶을 수 없어, 서버가 이 기간으로 따로 구한 값만 쓴다(없으면 —)
    return w && (w.start ?? null) === (card.range.start ?? null) && (w.end ?? null) === (card.range.end ?? null) ? w.values[i] ?? null : null;
  }
  if (by) return by.series[i] ? windowValue(by.series[i], by.labels, card.range?.start, card.range?.end, loadedRange(card)) : null;
  return card.data?.values?.scalar?.[i] ?? null;
}
// 기간 안의 날마다 값 [{ day, v }]. card.view.window(보는 구간)가 있으면 그 구간만. 개수·합계는 읽어 온 범위 안의 빈 날을 0으로 채운다(분석의 시계열과 같은 규칙).
function cardDays(card, i = 0) {
  const by = card.data?.by_unit?.day, s = by?.series[i], win = card.view?.window, from = win?.from ?? card.range?.start, to = win?.to ?? card.range?.end;
  if (!s) return [];
  let days = by.labels.filter(d => (!from || d >= from) && (!to || d <= to));
  const loaded = loadedRange(card);
  if (zeroWhenEmpty(s.agg) && days.length) {  // 보는 기간(없으면 있는 날)의 처음~끝 중 읽어 온 범위 안의 날 모두
    const lo = [from ?? days[0], loaded.start].filter(Boolean).sort().pop(), hi = [to ?? days[days.length - 1], loaded.end].filter(Boolean).sort()[0];
    days = [];
    for (let d = lo; d <= hi; d = shiftDay(d, 1)) days.push(d);
  }
  return days.map(day => ({ day, v: windowValue(s, by.labels, day, day, loaded) }));
}
const cardTarget = card => card.data?.target?.value ?? card.view?.target?.value ?? null;
const cardFormat = card => card.view?.format || card.data?.values?.format || "number";

// ---------- SVG 만들기 ----------
const stext = (x, y, text, attrs = {}) => svg("text", { x, y, ...attrs }, text);
const ICON_PATHS = { chart: "M3 20h18M6 16v-5M11 16V7M16 16v-8", up: "M4 16l6-6 4 4 6-8M15 6h5v5", user: "M12 12a4 4 0 100-8 4 4 0 000 8zM5 20c0-4 3-6 7-6s7 2 7 6", cart: "M3 4h3l2 11h10l2-8H7M10 20h.01M17 20h.01", coin: "M12 3a9 9 0 100 18 9 9 0 000-18zM9 12h6M12 8v8", clock: "M12 3a9 9 0 100 18 9 9 0 000-18zM12 7v5l3 2" };
const iconSvg = name => svg("svg", { viewBox: "0 0 24 24", width: 22, height: 22, fill: "none", stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round" }, svg("path", { d: ICON_PATHS[name] ?? ICON_PATHS.chart }));
const box = (cls, ...kids) => el("div", { className: "cbox " + cls }, ...kids);
const frame = (w, h, ...kids) => svg("svg", { viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: "xMidYMid meet", style: "width:100%;height:100%" }, ...kids);

// kpi: 아이콘 · 큰 값 · 비교 문장 · 스파크라인(일 값 선) · 비율이면 짝 절대량
function paintKpi(card, colors, board) {
  const v = cardValue(card), fmt = cardFormat(card), said = board?.compares.get(card), days = cardDays(card).map(d => d.v), pair = card.data?.pair;
  const color = pickColor(card, "series", card.data?.by_unit?.day?.series[0]?.name ?? card.data?.values?.names?.[0] ?? card.title, colors, 0);
  const spark = days.filter(x => x != null).length > 1 ? svg("svg", { viewBox: "0 0 120 30", class: "kpi-spark", preserveAspectRatio: "none" }, svg("polyline", { points: sparkPoints(days, 120, 30), fill: "none", stroke: color, "stroke-width": 1.8 })) : "";
  return box("kpi", el("div", { className: "kpi-top" }, el("span", { className: "kpi-icon", style: `color:${color}` }, iconSvg(card.view?.icon)), el("span", { className: "hint", textContent: card.title })),
    el("div", { className: "kpi-val", textContent: showValue(v, fmt, card.view?.unit_text) }), said ? el("div", { className: "bcmp " + said.tone, textContent: said.text }) : "", spark,
    pair ? el("div", { className: "hint kpi-pair", textContent: `${pair.num_label} ${showValue(pair.num)} · ${pair.den_label} ${showValue(pair.den)}` }) : "");
}

// donut_kpi: 가운데 칸(0)과 네 꼭지점 칸(1~4), 고리 4방식(view.ring). 목표(target)를 넘은 부분은 진한 색.
function paintDonut(card, colors) {
  const slots = card.view?.slots?.length ? card.view.slots : (card.data?.by_unit?.day?.series ?? []).map(s => ({ label: s.name })), ring = card.view?.ring || "stack";
  const vals = slots.map((_, i) => cardValue(card, i)), target = cardTarget(card), main = vals[0] ?? 0, color = i => slots[i]?.color || pickColor(card, "series", slots[i]?.label ?? String(i), colors, i);
  const W = 260, H = 200, cx = W / 2, cy = 100, r = 62, sw = 16, track = colors.line, c0 = color(0), over = darken(c0), fmt = i => showValue(vals[i], slots[i]?.format || cardFormat(card), slots[i]?.unit_text);
  const kids = [];
  const circle = (rad, ratio, stroke, width = sw, off = 0) => svg("circle", { cx, cy, r: rad, fill: "none", stroke, "stroke-width": width, "stroke-dasharray": ringDash(rad, ratio).dash, "stroke-dashoffset": -ringDash(rad, off).len, transform: `rotate(-90 ${cx} ${cy})` });
  const base = (rad, width = sw) => svg("circle", { cx, cy, r: rad, fill: "none", stroke: track, "stroke-width": width });
  if (ring === "half") {  // 반원 게이지 + 목표 점
    const ratio = target ? clamp(main / target, 0, 1) : clamp(main, 0, 1), a = -Math.PI / 2 + Math.PI * ratio;
    kids.push(svg("path", { d: arcPath(cx, cy + 20, r, -Math.PI / 2, Math.PI / 2), fill: "none", stroke: track, "stroke-width": sw, "stroke-linecap": "round" }),
      svg("path", { d: arcPath(cx, cy + 20, r, -Math.PI / 2, Math.max(a, -Math.PI / 2 + 0.001)), fill: "none", stroke: target && main > target ? over : c0, "stroke-width": sw, "stroke-linecap": "round" }));
    if (target) { const [x, y] = polar(cx, cy + 20, r, Math.PI / 2); kids.push(svg("circle", { cx: x, cy: y, r: 5, fill: colors.ink })); }
  } else if (ring === "double") {  // 겹친 두 고리: 바깥 = 칸0, 안 = 칸1 (목표 대비)
    const full = target || Math.max(main, vals[1] ?? 0, 1);
    kids.push(base(r), circle(r, main / full, main > full ? over : c0), base(r - sw - 4, sw - 4), circle(r - sw - 4, (vals[1] ?? 0) / full, color(1), sw - 4));
  } else {  // stack(실제·모자람·넘은 부분), ticks(고리 + 목표 눈금)
    const full = ring === "ticks" ? Math.max(target ?? 0, main) || 1 : target || Math.max(main, 1);
    kids.push(base(r));
    if (ring === "stack") {
      kids.push(circle(r, Math.min(main, full) / full, c0));
      if (target && main < target) kids.push(circle(r, (target - main) / full, colors.text, sw, main / full));  // 모자람
      if (target && main > target) kids.push(circle(r, (main - target) / full, over, sw, 1 - (main - target) / full));  // 넘은 부분
    } else {
      kids.push(circle(r, main / full, target && main > target ? over : c0));
      if (target) { const a = TAU * clamp(target / full, 0, 1), [x0, y0] = polar(cx, cy, r - sw / 2 - 3, a), [x1, y1] = polar(cx, cy, r + sw / 2 + 3, a); kids.push(svg("line", { x1: x0, y1: y0, x2: x1, y2: y1, stroke: colors.ink, "stroke-width": 2.5 })); }
    }
  }
  kids.push(stext(cx, cy + 6, fmt(0), { "text-anchor": "middle", "font-size": 22, "font-weight": 700, fill: colors.ink }), stext(cx, cy + 24, slots[0]?.label ?? "", { "text-anchor": "middle", "font-size": 11, fill: colors.text }));
  [[8, 18, "start"], [W - 8, 18, "end"], [8, H - 22, "start"], [W - 8, H - 22, "end"]].forEach(([x, y, anchor], j) => {  // 네 꼭지점 칸
    if (slots[j + 1]) kids.push(stext(x, y, slots[j + 1].label, { "text-anchor": anchor, "font-size": 11, fill: colors.text }), stext(x, y + 14, fmt(j + 1), { "text-anchor": anchor, "font-size": 14, "font-weight": 600, fill: colors.ink }));
  });
  return frame(W, H, ...kids);
}

// gauge: 항목 몫으로 나눈 호(간격 0.06rad), 가장 큰 항목은 굵게
function paintGauge(card, colors) {
  const v = card.data?.values, labels = v?.labels ?? [], vals = v?.series?.[0]?.values ?? [], segs = gaugeSegments(vals), total = vals.filter(x => x > 0).reduce((a, b) => a + b, 0);
  const W = 260, H = 200, cx = 90, cy = 100, r = 62;
  const arcs = segs.map(s => svg("path", { d: arcPath(cx, cy, r, s.a0, s.a1), fill: "none", stroke: pickColor(card, "parts", labels[s.index], colors, s.index), "stroke-width": s.max ? 24 : 14 }, svg("title", {}, `${labels[s.index]}: ${showValue(vals[s.index], cardFormat(card))} (${+(s.share * 100).toFixed(1)}%)`)));
  const legend = segs.slice(0, 8).map((s, k) => svg("g", {}, svg("rect", { x: 170, y: 40 + k * 18, width: 10, height: 10, rx: 2, fill: pickColor(card, "parts", labels[s.index], colors, s.index) }),
    stext(185, 49 + k * 18, `${labels[s.index]} ${+(s.share * 100).toFixed(1)}%`, { "font-size": 11, fill: colors.text })));
  return frame(W, H, ...arcs, stext(cx, cy + 6, showValue(total, cardFormat(card)), { "text-anchor": "middle", "font-size": 16, "font-weight": 700, fill: colors.ink }), ...legend);
}

// summary: 요약 문장(서버의 summary_text) + 목표 대비 막대
function paintSummary(card, colors) {
  const d = card.data ?? {}, v = cardValue(card), target = cardTarget(card), fmt = cardFormat(card), ratio = target && v != null ? v / target : null;  // 값이 없으면 막대·"목표 대비 0%"를 그리지 않는다
  return box("summary", el("div", { className: "sum-text", textContent: d.text || "—" }), d.target?.error ? el("div", { className: "err", textContent: d.target.error }) : "",
    ratio != null ? el("div", { className: "sum-bar", title: `${showValue(v, fmt)} / ${showValue(target, fmt)}` }, el("i", { style: `width:${clamp(ratio, 0, 1) * 100}%;background:${ratio > 1 ? darken(colors.series[0]) : colors.series[0]}` }),
      el("span", { className: "hint", textContent: `목표 대비 ${+(ratio * 100).toFixed(1)}%` })) : target && v == null ? el("span", { className: "hint", textContent: "목표 대비 —" }) : "");
}

// stripes: 칸 수 N, 채운 칸 = round(비율 × N), 목표 칸 강조. 비율 = 값 / 가득 찬 값(view.full, 없으면 목표, 비율 값이면 1)
function paintStripes(card, colors) {
  const n = card.view?.cells || 10, v = cardValue(card) ?? 0, target = cardTarget(card), fmt = cardFormat(card), full = card.view?.full || target || (fmt === "percent" ? 1 : Math.max(v, 1));
  const cells = stripeCells(v / full, n, target == null ? null : target / full), W = 260, gap = 4, w = (W - gap * (n - 1)) / n, color = pickColor(card, "series", card.title, colors, 0);
  const rects = Array.from({ length: n }, (_, i) => svg("rect", { x: i * (w + gap), y: 20, width: w, height: 40, rx: 3, fill: i < cells.filled ? color : colors.line, stroke: cells.target === i + 1 ? colors.ink : "none", "stroke-width": 2 }));
  return frame(W, 90, ...rects, stext(0, 82, `${showValue(v, fmt, card.view?.unit_text)}${target != null ? ` / 목표 ${showValue(target, fmt)}` : ""}`, { "font-size": 12, fill: colors.text }));
}

// blocks: 칸 하나 = view.size. 실제(채움) · 목표까지(빗금 윤곽) · 목표를 넘은 칸(진한 색)
function paintBlocks(card, colors) {
  const v = cardValue(card) ?? 0, target = cardTarget(card), fmt = cardFormat(card), size = card.view?.size || Math.max(+(Math.max(v, target ?? 0) / 20).toPrecision(1), 1e-9);
  const b = blockCells(v, target, size), per = 20, W = 260, cell = W / per - 3, color = pickColor(card, "series", card.title, colors, 0), total = b.actual + b.toTarget;
  const rects = Array.from({ length: total }, (_, i) => svg("rect", { x: (i % per) * (cell + 3), y: Math.floor(i / per) * (cell + 3), width: cell, height: cell, rx: 2,
    fill: i < b.actual - b.over ? color : i < b.actual ? darken(color) : "none", stroke: i < b.actual ? "none" : color, "stroke-dasharray": i < b.actual ? null : "3 2" }));
  const H = Math.ceil(total / per) * (cell + 3) + 28;
  return frame(W, H, ...rects, stext(0, H - 8, `칸 하나 ${showValue(size * b.scale, fmt)} · ${showValue(v, fmt)}${target != null ? ` / 목표 ${showValue(target, fmt)}` : ""}`, { "font-size": 11, fill: colors.text }));
}

// target_trend: 일 값 선 + 목표 가로 점선 + 목표 아래 구간 채움 + "목표보다 1.7%p 낮아요 · 기간 중 N번 내려감"
function paintTargetTrend(card, colors) {
  const days = cardDays(card), vs = days.map(d => d.v), target = cardTarget(card), fmt = cardFormat(card), W = 300, H = 160, pad = 12, color = pickColor(card, "series", card.title, colors, 0);
  const real = vs.filter(x => x != null);
  if (!real.length) return box("summary", el("div", { className: "sum-text", textContent: "이 기간의 값이 없어요" }));
  const lo = Math.min(...real, target ?? Infinity), hi = Math.max(...real, target ?? -Infinity), yOf = v => H - 24 - (H - 24 - pad) * (hi === lo ? 0.5 : (v - lo) / (hi - lo));
  const xs = vs.map((_, i) => pad + (W - 2 * pad) * (vs.length < 2 ? 0.5 : i / (vs.length - 1))), kids = [];
  if (target != null) belowPolygons(xs, vs, target, yOf).forEach(p => kids.push(svg("polygon", { points: p.map(q => q.map(n => n.toFixed(1)).join(",")).join(" "), fill: colors.series[1], opacity: 0.3 })));
  kids.push(svg("polyline", { points: vs.map((v, i) => v == null ? null : `${xs[i].toFixed(1)},${yOf(v).toFixed(1)}`).filter(Boolean).join(" "), fill: "none", stroke: color, "stroke-width": 2 }));
  if (target != null) kids.push(svg("line", { x1: pad, x2: W - pad, y1: yOf(target), y2: yOf(target), stroke: colors.ink, "stroke-dasharray": "6 4" }));
  const now = cardValue(card), says = target == null ? (card.data?.target?.error || "목표가 없어요") : now == null ? "—" : `목표보다 ${showGap(now - target, fmt)} ${now < target ? "낮아요" : now > target ? "높아요" : "같아요"} · 기간 중 ${crossDown(vs, target)}번 내려감`;
  return frame(W, H, ...kids, stext(pad, H - 6, says, { "font-size": 11, fill: colors.text }));
}

// dual_trend: 두 계열 선, 마우스를 올리면 그날 값 상자, 오늘 이후는 빗금, 아래 구간 막대를 끌어 보는 범위(view.window)를 정한다
function paintDual(card, colors, board) {
  const a = cardDays({ ...card, view: { ...card.view, window: null } }, 0), whole = a.map(d => d.day), win = card.view?.window, W = 320, H = 190, pad = 14, top = 10, plot = 118, brushY = 150;
  if (!whole.length) return box("summary", el("div", { className: "sum-text", textContent: "이 기간의 값이 없어요" }));
  const shown = cardDays(card, 0).map(d => d.day), from = shown[0], to = shown[shown.length - 1];
  const rows = [cardDays(card, 0), cardDays(card, 1)], all = rows.flat().map(d => d.v).filter(v => v != null), lo = Math.min(...all), hi = Math.max(...all);
  const xOf = i => pad + (W - 2 * pad) * (shown.length < 2 ? 0.5 : i / (shown.length - 1)), yOf = v => top + plot - plot * (hi === lo ? 0.5 : (v - lo) / (hi - lo));
  const names = [0, 1].map(i => card.data?.by_unit?.day?.series[i]?.name ?? `계열 ${i + 1}`), cols = names.map((n, i) => pickColor(card, "series", n, colors, i)), today = new Date().toLocaleDateString("sv");
  const kids = [svg("defs", {}, svg("pattern", { id: "hatch" + card.id, width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, svg("line", { x1: 0, y1: 0, x2: 0, y2: 6, stroke: colors.text, "stroke-width": 1.5 })))];
  const future = shown.findIndex(d => d > today);
  if (future >= 0) kids.push(svg("rect", { x: xOf(Math.max(0, future - 1)), y: top, width: W - pad - xOf(Math.max(0, future - 1)), height: plot, fill: `url(#hatch${card.id})`, opacity: 0.35 }));  // 오늘 이후
  rows.forEach((r, k) => kids.push(svg("polyline", { points: r.map((d, i) => d.v == null ? null : `${xOf(i).toFixed(1)},${yOf(d.v).toFixed(1)}`).filter(Boolean).join(" "), fill: "none", stroke: cols[k], "stroke-width": 2 })));
  const tip = svg("g", { visibility: "hidden" }, svg("rect", { width: 150, height: 52, rx: 6, fill: colors.card, stroke: colors.line }), stext(8, 15, "", { "font-size": 11, fill: colors.text }), stext(8, 30, "", { "font-size": 11, fill: cols[0] }), stext(8, 45, "", { "font-size": 11, fill: cols[1] }));
  const over = svg("rect", { x: pad, y: top, width: W - 2 * pad, height: plot, fill: "transparent" });
  over.onpointermove = e => {
    const p = over.getBoundingClientRect(), i = clamp(Math.round((e.clientX - p.left) / p.width * (shown.length - 1)), 0, shown.length - 1), t = tip.querySelectorAll("text");
    t[0].textContent = shown[i]; t[1].textContent = `${names[0]} ${showValue(rows[0][i]?.v, cardFormat(card))}`; t[2].textContent = `${names[1]} ${showValue(rows[1][i]?.v, cardFormat(card))}`;
    tip.setAttribute("transform", `translate(${clamp(xOf(i) + 8, 0, W - 152)} ${top})`); tip.setAttribute("visibility", "visible");
  };
  over.onpointerleave = () => tip.setAttribute("visibility", "hidden");
  const bx = i => pad + (W - 2 * pad) * (whole.length < 2 ? 0.5 : i / (whole.length - 1)), bars = a.map((d, i) => svg("rect", { x: bx(i) - 1, y: brushY + 28 - (d.v == null ? 0 : 28 * (d.v - lo) / ((hi - lo) || 1)), width: 2, height: d.v == null ? 0 : 28 * (d.v - lo) / ((hi - lo) || 1) + 1, fill: colors.text }));
  const i0 = whole.indexOf(from), i1 = whole.indexOf(to), sel = svg("rect", { x: bx(i0), y: brushY - 2, width: Math.max(4, bx(i1) - bx(i0)), height: 34, fill: colors.series[0], opacity: 0.25, stroke: colors.series[0] });
  const strip = svg("rect", { x: pad, y: brushY - 2, width: W - 2 * pad, height: 34, fill: "transparent", style: "cursor:col-resize" });
  strip.onpointerdown = e => {  // 아래 구간 막대를 끌어 보는 범위를 고른다
    const p = strip.getBoundingClientRect(), at = ev => clamp(Math.round((ev.clientX - p.left) / p.width * (whole.length - 1)), 0, whole.length - 1), start = at(e);
    strip.setPointerCapture(e.pointerId);
    const move = ev => { const k = at(ev); sel.setAttribute("x", bx(Math.min(start, k))); sel.setAttribute("width", Math.max(4, bx(Math.max(start, k)) - bx(Math.min(start, k)))); };
    const up = ev => { strip.removeEventListener("pointermove", move); strip.removeEventListener("pointerup", up); const k = at(ev);
      card.view.window = start === k ? null : { from: whole[Math.min(start, k)], to: whole[Math.max(start, k)] }; if (!card.view.window) delete card.view.window; board?.changed?.(); board?.draw(); };
    strip.addEventListener("pointermove", move); strip.addEventListener("pointerup", up);
  };
  kids.push(over, tip, ...bars, sel, strip, stext(pad, H - 2, names.join(" · ") + (win ? "  (구간 선택됨, 한 번 눌러 해제)" : ""), { "font-size": 10, fill: colors.text }));
  return frame(W, H, ...kids);
}

// recent_rows: 연결돼 있을 때만 /api/query로 앞 N행을 읽어 보인다. 행 값은 저장하지 않는다(카드에는 컬럼 이름만).
function paintRecent(card, colors, board) {
  const holder = box("recent"), key = card.source?.key;
  if (!board?.connKey || (key && !sameSource(key, board.connKey))) { holder.append(el("div", { className: "hint", textContent: board?.connKey ? "이 카드는 다른 연결의 것이라 지금은 보이지 않아요" : "연결하면 보입니다" })); return holder; }
  holder.append(el("div", { className: "hint", textContent: "읽는 중…" }));
  api("/api/query", { ...card.source.body, limit: card.view?.rows || 10, count: false }).then(d => {
    const { columns, rows } = d.rows;
    holder.replaceChildren(el("table", {}, el("thead", {}, el("tr", {}, ...columns.map(c => el("th", { textContent: c })))),
      el("tbody", {}, ...rows.map(r => el("tr", {}, ...columns.map(c => el("td", { textContent: r[c] == null ? "" : String(r[c]) })))))));
  }).catch(e => holder.replaceChildren(el("div", { className: "err", textContent: e.message })));
  return holder;
}

// ---------- 표·상태·문장·경고·배치도·편차 (3-2b·3-4) ----------
const LEVEL_NAMES = { ok: "정상", warn: "주의", bad: "이상" };
const cssVar = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const levelColor = level => level === "bad" ? cssVar("--err") : level === "warn" ? "#f08c00" : cssVar("--ok");
// 행 값 읽기: 연결돼 있을 때만 /api/query로 앞 N행(저장하지 않는다). show(컬럼 이름들, 행들[[…]])를 부른다.
function loadRows(card, board, holder, show, limit) {
  const key = card.source?.key;
  if (!board?.connKey || (key && !sameSource(key, board.connKey))) return void holder.append(el("div", { className: "hint", textContent: board?.connKey ? "이 카드는 다른 연결의 것이라 지금은 보이지 않아요" : "연결하면 보입니다" }));
  holder.append(el("div", { className: "hint", textContent: "읽는 중…" }));
  api("/api/query", { ...card.source.body, limit, count: false }).then(d => show(d.rows.columns, d.rows.rows.map(r => d.rows.columns.map(c => r[c]))))
    .catch(e => holder.replaceChildren(el("div", { className: "err", textContent: e.message })));
}
// 칸 글자와 조건 색·화살표를 입힌 td. rule이 맞으면 배경색(칸), arrow는 규칙 화살표 또는 직전 값 비교 ▲▼
function styledCell(text, rule, arrow, align) {
  const td = el("td", { textContent: text, className: align });
  if (rule) td.style.background = alpha(rule.color, 0.28);
  if (arrow) td.append(el("span", { className: "carrow " + arrow.tone, textContent: " " + arrow.text, style: arrow.color ? `color:${arrow.color}` : "" }));
  return td;
}
// table: 집계된 값(또는 원래 행) 표. 컬럼별 모양, 머리글 정렬, 합계 줄, 직전 기간 대비 ▲▼, 조건 색 규칙(맞는 칸만 칠함)
function renderTable(holder, card, columns, rows, prev) {
  const view = card.view, names = columns.map(c => c.name ?? c), kinds = Object.fromEntries(names.map((n, i) => [n, kindOfValues(rows.map(r => r[i]))]));
  const compiled = compileRules(view.cond_rules, kinds), groups = columns.filter(c => c.kind === "group").length, sort = card.sortBy;
  const order = rows.map((_, i) => i);
  if (sort) order.sort((a, b) => { const [x, y] = [rows[a][sort.i], rows[b][sort.i]]; return (x == null) - (y == null) || (x > y ? 1 : x < y ? -1 : 0) * sort.dir; });
  const head = el("tr", {}, ...names.map((n, i) => el("th", { textContent: n + (sort?.i === i ? (sort.dir > 0 ? " ▲" : " ▼") : ""), style: "cursor:pointer", title: "눌러 정렬", onclick: () => { card.sortBy = { i, dir: sort?.i === i ? -sort.dir : 1 }; renderTable(holder, card, columns, rows, prev); } })));
  const body = order.map(r => {
    const row = rows[r], hits = names.map((n, i) => matchRule(compiled, n, row[i])), whole = hits.find(h => h?.scope === "row");
    const key = row.slice(0, groups).map(String).join("\x01"), old = prev?.[key];
    const tr = el("tr", {}, ...row.map((v, i) => {
      const fmt = view.formats?.[names[i]] ?? "number", before = columns[i].kind === "value" ? old?.[i - groups] : null, num = typeof v === "number";
      const arrow = hits[i] && ruleArrow(hits[i], v, before, view.better) || (num && before != null && !hits[i]?.arrow ? ruleArrow({ arrow: "auto", color: "" }, v, before, view.better) : null);
      return styledCell(num ? showValue(v, fmt) : v == null ? "" : String(v), hits[i]?.scope === "cell" ? hits[i] : null, arrow, num ? "num" : "");
    }));
    if (whole) tr.style.background = alpha(whole.color, 0.22);
    return tr;
  });
  const total = view.total ? el("tr", { className: "ctotal" }, ...columns.map((c, i) => el("td", { className: "num", textContent: i < groups ? (i === 0 ? "합계" : "") : ["sum", "count"].includes(c.agg) ? showValue(rows.reduce((a, r) => a + (r[i] ?? 0), 0), view.formats?.[names[i]] ?? "number") : "—" }))) : "";
  holder.replaceChildren(el("table", {}, el("thead", {}, head), el("tbody", {}, ...body, total)));
}
function paintTable(card, colors, board) {
  const holder = box("table"), v = card.data?.values;
  if (v) renderTable(holder, card, v.columns, v.rows, v.prev);
  else loadRows(card, board, holder, (names, rows) => renderTable(holder, card, names.map(n => ({ name: n, kind: "value" })), rows, null), card.view?.rows || 20);
  return holder;
}
// status: 정상·주의·이상 배지와 수 + 데이터 신선도 문장
function paintStatus(card) {
  const v = card.data?.values, counts = v?.counts ?? {};
  return box("status", el("div", { className: "badges" }, ...["ok", "warn", "bad"].map(l => el("span", { className: "badge", style: `border-color:${levelColor(l)};color:${levelColor(l)}` }, el("b", { textContent: String(counts[l] ?? 0) }), " " + LEVEL_NAMES[l]))),
    v?.fresh ? el("div", { className: "hint", textContent: v.fresh }) : "", card.data?.note ? el("div", { className: "hint", textContent: card.data.note }) : "");
}
// alerts: 나쁜 엔티티 목록(이상 먼저, 오래된 순) "이름 · 이상 · 12분째 · 이유 · 영향: …"
function paintAlerts(card) {
  const bad = (card.data?.values?.entities ?? []).filter(e => e.level !== "ok").sort((a, b) => (b.level === "bad") - (a.level === "bad") || String(a.since).localeCompare(String(b.since)));
  if (!bad.length) return box("alerts", el("div", { className: "hint", textContent: "경고가 없어요" }));
  return box("alerts", ...bad.map(e => el("div", { className: "alert lv-" + e.level }, el("b", { textContent: e.name }), el("span", { className: "badge", style: `border-color:${levelColor(e.level)};color:${levelColor(e.level)}`, textContent: LEVEL_NAMES[e.level] }),
    el("span", { textContent: e.since_text }), el("span", { className: "hint", textContent: e.reasons.join(", ") }), e.impact.length ? el("span", { className: "hint", textContent: "영향: " + e.impact.join(", ") }) : "")));
}
// 처음 배치: 종류별로 한 줄씩, 한 줄 안에서 고르게(0~1 비율)
function autoGrid(entities) {
  const kinds = [...new Set(entities.map(e => e.kind || ""))], out = {};
  kinds.forEach((k, row) => { const same = entities.filter(e => (e.kind || "") === k); same.forEach((e, j) => { out[e.name] = [(j + 1) / (same.length + 1), (row + 1) / (kinds.length + 1)]; }); });
  return out;
}
// 화면 좌표(clientX·Y)를 SVG 안 좌표로(끌기용)
function svgPoint(svgEl, clientX, clientY) {
  const p = svgEl.createSVGPoint();
  [p.x, p.y] = [clientX, clientY];
  return p.matrixTransform(svgEl.getScreenCTM().inverse());
}
// map: 배경(서버실 칸·네트워크·그림) 위에 엔티티를 상태 색 점으로. 끌어 옮기면 위치를 0~1 비율로 카드(view.positions)에 저장한다. 연결선은 view.links와 영향받는 대상
function paintMap(card, colors, board) {
  const v = card.data?.values, entities = v?.entities ?? [], W = 400, H = 260, view = card.view, bg = view.bg || "rack";
  const pos = { ...autoGrid(entities), ...(view.positions ?? {}) }, root = svg("svg", { viewBox: `0 0 ${W} ${H}`, style: "width:100%;height:100%;touch-action:none" });
  const back = svg("g", {}), lines = svg("g", {}), dots = svg("g", {});
  if (bg === "rack") for (let i = 0; i < 6; i++) back.append(svg("rect", { x: 20 + i * 62, y: 14, width: 52, height: H - 28, rx: 4, fill: "none", stroke: colors.line, "stroke-width": 1.5 }));
  else if (bg === "network") for (let i = 1; i < 10; i++) back.append(svg("line", { x1: i * 40, y1: 0, x2: i * 40, y2: H, stroke: colors.line, "stroke-dasharray": "2 6" }), svg("line", { x1: 0, y1: i * 26, x2: W, y2: i * 26, stroke: colors.line, "stroke-dasharray": "2 6" }));
  else if (card.imageUrl) back.append(svg("image", { href: card.imageUrl, x: 0, y: 0, width: W, height: H, preserveAspectRatio: "xMidYMid meet" }));
  else back.append(stext(W / 2, H / 2, board?.dashId ? "그림을 읽는 중…" : "카드를 저장하면 그림이 보여요", { "text-anchor": "middle", "font-size": 12, fill: colors.text }));
  if (bg === "image" && !card.imageUrl && board?.dashId && !card.imageTried) {  // 그림은 저장된 카드의 경로로 서버에서 받아 blob 주소로 쓴다
    card.imageTried = true;
    fetch(`/api/dash/image?dash=${encodeURIComponent(board.dashId)}&card=${encodeURIComponent(card.id)}`).then(async r => {
      if (!r.ok) throw new Error((await r.json()).error);
      card.imageUrl = URL.createObjectURL(await r.blob());
      board.draw();
    }).catch(e => { card.imageTried = false; back.replaceChildren(stext(W / 2, H / 2, e.message, { "text-anchor": "middle", "font-size": 11, fill: levelColor("bad") })); });
  }
  const links = [...(view.links ?? []), ...entities.flatMap(e => (e.impact ?? []).map(t => [e.name, t]))];
  // 점 요소는 한 번만 만들고 끄는 동안에는 위치만 옮긴다(다시 만들면 포인터 캡처가 풀려 끌기가 끊긴다)
  const spots = entities.map(e => {
    const circle = svg("circle", { r: e.level === "ok" ? 8 : 10, fill: levelColor(e.level), stroke: colors.card, "stroke-width": 2 }, svg("title", {}, `${e.name} · ${LEVEL_NAMES[e.level]}${e.since_text ? " · " + e.since_text : ""}${e.reasons.length ? " · " + e.reasons.join(", ") : ""}`));
    const name = stext(0, 0, e.name, { "text-anchor": "middle", "font-size": 10, fill: colors.ink }), g = svg("g", { style: "cursor:grab" }, circle, name);
    g.onpointerdown = ev => {
      try { g.setPointerCapture(ev.pointerId); } catch (err) { /* 포인터를 못 잡아도 끌기는 된다 */ }
      const move = m => { const p = svgPoint(root, m.clientX, m.clientY); pos[e.name] = [clamp(p.x / W, 0, 1), clamp(p.y / H, 0, 1)]; draw(); };
      const up = () => { g.removeEventListener("pointermove", move); g.removeEventListener("pointerup", up); view.positions = Object.fromEntries(Object.entries(pos).map(([k, p]) => [k, p.map(n => +n.toFixed(3))])); board?.changed?.(); };
      g.addEventListener("pointermove", move); g.addEventListener("pointerup", up);
    };
    return { e, circle, name, g };
  });
  dots.replaceChildren(...spots.map(x => x.g));
  const draw = () => {
    lines.replaceChildren(...links.filter(([a, b]) => pos[a] && pos[b]).map(([a, b]) => svg("line", { x1: pos[a][0] * W, y1: pos[a][1] * H, x2: pos[b][0] * W, y2: pos[b][1] * H, stroke: colors.text, "stroke-width": 1.2, opacity: 0.6 })));
    spots.forEach(({ e, circle, name }) => {
      const [x, y] = [pos[e.name][0] * W, pos[e.name][1] * H];
      circle.setAttribute("cx", x); circle.setAttribute("cy", y); name.setAttribute("x", x); name.setAttribute("y", y + 22);
    });
  };
  draw();
  root.append(back, lines, dots);
  return entities.length ? root : box("alerts", el("div", { className: "hint", textContent: "엔티티가 없어요" }));
}
// deviation: 엔티티 × 시간대 표. 칸 = 값, 기준(view.baseline, 없으면 그 엔티티 평균) 대비 ±. 기준 넘는 칸(허용 폭 view.threshold, 없으면 기준의 20%)을 강조
function deviationCells(values, baseline, threshold) {
  const real = values.filter(v => v != null), base = baseline ?? (real.length ? real.reduce((a, b) => a + b, 0) / real.length : null), limit = threshold ?? (base == null ? 0 : Math.abs(base) * 0.2);
  return { base, cells: values.map(v => v == null || base == null ? { v, dev: null, over: false } : { v, dev: v - base, over: Math.abs(v - base) > limit }) };
}
function paintDeviation(card, colors) {
  const v = card.data?.values, view = card.view, fmt = cardFormat(card);
  if (!v?.entities.length) return box("alerts", el("div", { className: "hint", textContent: "값이 없어요" }));
  const label = b => String(b).replace(" 00:00:00", "").slice(0, view.bucket === "hour" || !view.bucket ? 16 : 10);
  return box("deviation", el("table", {}, el("thead", {}, el("tr", {}, el("th", { textContent: "" }), el("th", { textContent: "기준" }), ...v.buckets.map(b => el("th", { textContent: label(b) })))),
    el("tbody", {}, ...v.entities.map(e => { const d = deviationCells(e.values, view.baseline, view.threshold);
      return el("tr", {}, el("th", { textContent: e.name }), el("td", { className: "num", textContent: showValue(d.base, fmt) }), ...d.cells.map(c => {
        const td = el("td", { className: "num", textContent: c.v == null ? "" : showValue(c.v, fmt) }, c.dev == null ? "" : el("div", { className: "hint", textContent: (c.dev > 0 ? "+" : c.dev < 0 ? "−" : "±") + showValue(Math.abs(c.dev), fmt) }));
        if (c.over) td.style.background = alpha(c.dev > 0 ? cssVar("--err") : colors.series[0], 0.3);
        return td; })); }))));
}
// sentence: 사용자가 쓴 문장 틀의 {빈칸}에 값·직전·비교·기준일을 넣는다. 계산 못 한 빈칸은 —(이유는 마우스를 올리면). 빈칸에도 조건 색·화살표 규칙(규칙의 컬럼 = 빈칸 이름)
function paintSentence(card, colors) {
  const blanks = card.data?.values?.blanks ?? {}, view = card.view, compiled = compileRules(view.cond_rules, Object.fromEntries(Object.entries(blanks).map(([n, b]) => [n, b.kind === "value" || b.kind === "prev" ? "num" : "word"])));
  const spans = templateParts(view.template).map(p => {
    if (p.text != null) return document.createTextNode(p.text);
    const b = blanks[p.blank];
    if (!b) return el("span", { className: "blank err", textContent: "—", title: `‘${p.blank}’ 빈칸이 정의돼 있지 않아요` });
    if (b.error) return el("span", { className: "blank err", textContent: "—", title: b.error });
    const value = b.kind === "value" ? b.cur : b.kind === "prev" ? b.prev : b.text, rule = matchRule(compiled, p.blank, value);
    const arrow = rule && ruleArrow(rule, b.cur, b.prev, view.better), span = el("span", { className: "blank", textContent: typeof value === "number" || value == null ? showValue(value, b.format) : String(value) });
    if (rule) span.style.cssText = `background:${alpha(rule.color, 0.28)};border-radius:4px;padding:0 4px`;
    if (arrow) span.append(el("span", { className: "carrow " + arrow.tone, textContent: " " + arrow.text, style: arrow.color ? `color:${arrow.color}` : "" }));
    return span;
  });
  return box("summary", el("div", { className: "sum-text" }, ...spans));
}

// funnel: 흐름(퍼널) 생키 + 한 줄 설명 + 단계별 표(funnel.js). 색: 막대는 팔레트 첫 색, 다음 단계 띠는 초록, 이탈 띠는 주황(카드 색 설정 "단계 막대"·"다음 단계"·"이탈"로 바꿈)
function paintFunnel(card, colors) {
  const d = card.data.values, steps = d.steps || [], pick = (name, fallback) => card.colors?.series?.[name] ?? fallback;
  const chart = el("div", { style: "flex:1; min-height:140px" }, sankeySvg(steps, { bar: pick("단계 막대", colors.palette?.[0] ?? FUNNEL_COLORS.bar), next: pick("다음 단계", FUNNEL_COLORS.next), drop: pick("이탈", FUNNEL_COLORS.drop), text: colors.text }));
  const rows = funnelRows(steps);
  const table = el("table", {}, el("thead", {}, el("tr", {}, ...FUNNEL_COLUMNS.map(c => el("th", { textContent: c })))), el("tbody", {}, ...rows.map(r => el("tr", {}, ...FUNNEL_COLUMNS.map((c, i) => el("td", { className: i ? "num" : "", textContent: typeof r[c] === "number" ? r[c].toLocaleString() : r[c] }))))));
  return box("table", el("div", { className: "hint", textContent: `${funnelSentence(steps)} (${windowText(d.window)})` }), chart, table);
}

// geomap: 지도 카드. 그리는 코드(static/geo/geomap.js)는 지도 카드가 처음 그려질 때 불러온다.
const GEO_JS = "/static/geo/geomap.js";
function paintGeo(card, colors, board) {
  if (typeof paintGeoMap === "undefined") {
    need(GEO_JS).then(() => board?.draw()).catch(() => { card.geoFailed = true; });
    return box("summary", el("div", { className: "sum-text", textContent: card.geoFailed ? "지도 그리기 코드를 불러오지 못했어요" : "지도를 불러오는 중…" }));
  }
  return paintGeoMap(card, colors, board);
}

const PAINTERS = { kpi: paintKpi, donut_kpi: paintDonut, gauge: paintGauge, summary: paintSummary, recent_rows: paintRecent, dual_trend: paintDual, stripes: paintStripes, blocks: paintBlocks, target_trend: paintTargetTrend,
                   table: paintTable, funnel: paintFunnel, status: paintStatus, sentence: paintSentence, alerts: paintAlerts, map: paintMap, deviation: paintDeviation, geomap: paintGeo };
// 카드 하나를 그린 요소(없는 값이면 안내). board는 비교 문장·연결 열쇠·다시 그리기를 위해 받는다.
const paintCard = (card, colors, board) => card.data && (card.data.by_unit || card.data.values || card.data.columns) ? PAINTERS[card.kind](card, colors, board) : box("summary", el("div", { className: "sum-text", textContent: "아직 구한 값이 없어요" }));
