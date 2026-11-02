// 순수 계산만. DOM·SERVER·Chart에 기대지 않는다(web/도 가져다 쓴다).
// 대시보드 카드(3단계)의 값 → 모양 계산(dashboard/dash_cards.js에서 옮김): 목표 비교, 호·고리·게이지 조각·칸·블록·교차·다각형·선 점, 값 글자, 어두운 색, 편차 칸, 배치도 처음 자리. hex6은 lib/dash_calc.js.

const TAU = Math.PI * 2, RING_GAP = 0.06;  // 게이지 조각 사이 간격(라디안)
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const slack = t => Math.max(Math.abs(t) * 1e-9, 1e-12), overTarget = (v, t) => v > t + slack(t), underTarget = (v, t) => v < t - slack(t);  // 합계의 부동소수 꼬리(1.1 + 2.2)는 목표와 같은 값으로(서버 summary_text와 같은 기준)
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
  for (const v of values) { if (v == null) continue; if (before != null && !underTarget(before, target) && underTarget(v, target)) n++; before = v; }
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
    if (prev != null && underTarget(prev, target) !== underTarget(v, target)) {  // 목표선을 지나는 선분: 지나는 x
      const x = xs[i - 1] + (xs[i] - xs[i - 1]) * (target - prev) / (v - prev);
      if (underTarget(v, target)) run = [[x, ty]]; else close(x);
    }
    if (underTarget(v, target)) { if (!run) run = [[xs[i], ty]]; run.push([xs[i], yOf(v)]); }
  }
  close(xs[xs.length - 1]);
  return polys;
}
// 값들을 w×h 칸에 선으로 놓은 점들 "x,y x,y …"(빈 값은 건너뜀). 모두 같으면 가운데 줄.
function sparkPoints(values, w, h, pad = 2) {
  const real = values.filter(v => v != null), lo = Math.min(...real), hi = Math.max(...real), n = values.length;
  return values.map((v, i) => v == null ? null : `${(pad + (n < 2 ? (w - 2 * pad) / 2 : (w - 2 * pad) * i / (n - 1))).toFixed(1)},${(hi === lo ? h / 2 : h - pad - (h - 2 * pad) * (v - lo) / (hi - lo)).toFixed(1)}`).filter(Boolean).join(" ");
}
// 점들("x,y x,y …")을 부드러운 곡선 경로로: 점과 점 사이 가운데를 지나는 2차 곡선(꺾인 곳이 뾰족하지 않게, 큰 차트의 곡선과 비슷하게)
function smoothPath(points) {
  const p = points.split(" ").map(s => s.split(",").map(Number));
  let d = `M${p[0][0]},${p[0][1]}`;
  for (let i = 1; i < p.length - 1; i++) d += ` Q${p[i][0]},${p[i][1]} ${((p[i][0] + p[i + 1][0]) / 2).toFixed(1)},${((p[i][1] + p[i + 1][1]) / 2).toFixed(1)}`;
  return d + ` L${p.at(-1)[0]},${p.at(-1)[1]}`;
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
// deviation: 기준(baseline, 없으면 값들의 평균) 대비 ±. 기준 넘는 칸(허용 폭 threshold, 없으면 기준의 20%)을 over로
function deviationCells(values, baseline, threshold) {
  const real = values.filter(v => v != null), base = baseline ?? (real.length ? real.reduce((a, b) => a + b, 0) / real.length : null), limit = threshold ?? (base == null ? 0 : Math.abs(base) * 0.2);
  return { base, cells: values.map(v => v == null || base == null ? { v, dev: null, over: false } : { v, dev: v - base, over: Math.abs(v - base) > limit }) };
}
// 배치도의 처음 배치: 종류별로 한 줄씩, 한 줄 안에서 고르게(0~1 비율)
function autoGrid(entities) {
  const kinds = [...new Set(entities.map(e => e.kind || ""))], out = {};
  kinds.forEach((k, row) => { const same = entities.filter(e => (e.kind || "") === k); same.forEach((e, j) => { out[e.name] = [(j + 1) / (same.length + 1), (row + 1) / (kinds.length + 1)]; }); });
  return out;
}
