// 순수 계산만. DOM·SERVER·Chart에 기대지 않는다(web/lib/calc.ts에 같은 본문의 사본이 있다).
// 카드 값 계산(옛 dash.js에서 옮김): 숫자 글자, 색 글자, 라벨 번호, 일 값 다시 묶기, 날짜 계산, 직전 기간, 카드 모양 판정. 분석 화면이 <script src>로 불러 온다(web/lib/calc.ts에 같은 본문의 TS 사본).

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

// ---------- 색 ----------
// 라벨의 번호: 카드가 그 라벨을 처음 본 차례(order). 새 라벨은 뒤에 붙여 카드에 남기므로 다시 구한 뒤에도 같은 라벨은 같은 번호(같은 색)다.
// 서버가 order를 200개까지만 받으므로(core/dash_store.py) 그 이상은 붙이지 않고 같은 번호(200)를 쓴다 — 색은 팔레트·기본 색을 돌려 쓴다.
const MAX_ORDER = 200;
const labelIndex = (order, name) => { const i = order.indexOf(name); return i >= 0 ? i : order.length >= MAX_ORDER ? MAX_ORDER : order.push(name) - 1; };

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
  const value = { count: g => g.count, sum: g => g.sum, mean: g => g.count && g.sum != null ? g.sum / g.count : null, min: g => g.min, max: g => g.max }[s.agg];
  return new Map([...groups].map(([key, g]) => [key, value(g)]));
}
// ---------- 기간·비교 (기간 규칙은 서버 period.py, 문장은 compare.py. 여기서는 저장된 일 값으로 두 기간의 값만 구한다) ----------
const dayNumber = day => Date.UTC(+day.slice(0, 4), +day.slice(5, 7) - 1, +day.slice(8, 10)) / 86400000;
const shiftDay = (day, n) => new Date((dayNumber(day) + n) * 86400000).toISOString().slice(0, 10);
const daysBetween = (start, end) => dayNumber(end) - dayNumber(start) + 1;
const zeroWhenEmpty = agg => agg === "count" || agg === "sum";  // 행이 없는 구간을 0으로 말할 수 있는 집계
// 날짜 기간의 직전 기간 {start, end, text}: 달 단위로 꼭 맞으면(1일 ~ 그달 끝) 직전 같은 개월 수, 아니면 바로 앞 같은 일수(서버 period.previous·previous_text와 같은 규칙)
function previousSpan(start, end) {
  const [y1, m1] = start.split("-").map(Number), [y2, m2] = end.split("-").map(Number);
  if (start.endsWith("-01") && shiftDay(end, 1).endsWith("-01")) {
    const months = (y2 - y1) * 12 + m2 - m1 + 1, back = new Date(Date.UTC(y1, m1 - 1 - months, 1)).toISOString().slice(0, 10);
    const named = months === 1 || (months === 3 && m1 % 3 === 1) || (months === 12 && m1 === 1) ? { 1: "지난달", 3: "직전 분기", 12: "작년" }[months] : `직전 ${months}개월`;  // 서버 period.month_name
    return { start: back, end: shiftDay(start, -1), text: named };
  }
  const n = daysBetween(start, end);
  return { start: shiftDay(start, -n), end: shiftDay(start, -1), text: `직전 ${n}일` };
}
// 카드 자신의 기간 줄(날짜컬럼 시작 ~ 끝)과 그 직전 기간. 기간 줄이 없으면 null.
function ownRange(card) {
  for (const line of card.source?.body?.periods ?? []) {
    const m = /^.+?\s+(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})$/.exec(line.trim());  // 날짜를 끝에서 읽어 공백 있는 컬럼 이름도 된다(dash_calc.OWN_PERIOD와 같은 규칙)
    const real = d => !isNaN(Date.parse(d)) && new Date(d).toISOString().slice(0, 10) === d;  // 2026-02-30 같은 없는 날은 건너뛴다
    if (m && real(m[1]) && real(m[2]) && m[1] <= m[2]) return { range: { start: m[1], end: m[2] }, prev: previousSpan(m[1], m[2]) };
  }
  return null;
}
// 분석 카드가 어떤 그림인지(없으면 분석 종류에서)
const anaView = card => card.view.analysis_view || { distribution: "hist", correlation: "heat", scatter: "scatter", groups: "groups", trend: "trend" }[card.data?.values?.kind];
// 산점도·버블: 점은 data.points(서버가 구한 점·r·추세선), 버블의 [크기] 컬럼·추세선·r 보이기는 view.points·trend·show_r
const isPoints = card => ["scatter", "bubble"].includes(card.view?.type) && !!card.data?.points;
const pointGroups = p => p.groups.length ? p.groups : [p.y];
// 최신화가 읽은 날짜 범위(data.window: 직전 기간 시작 ~ 보는 기간 끝) 밖으로 보는 기간이 나갔는지. 나갔으면 그 기간으로 다시 읽어야 한다.
const outsideWindow = (card, range) => !!(range && card.data?.window && ((range.start && card.data.window.start && range.start < card.data.window.start) || (range.end && card.data.window.end && range.end > card.data.window.end)));
