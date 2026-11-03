// 대시보드의 순수 계산: static/core/lib/{dash_calc,dash_grid,dash_rules,dash_svg}.js를 TS로 옮긴 사본. 함수 본문은 그대로 두고 타입만 붙였다.
// 옛 화면(static/dashboard)이 사라지면 원본 lib도 지운다(그때까지 원본을 고치면 여기도 같이 고친다 — 두 벌인 동안의 약속).
import type { Card, CondRule, DaySeries, Layout, PrevSpan } from "./types";

// ======================= lib/dash_calc.js =======================
// 숫자는 자릿수 쉼표와 소수 넷째 자리까지
export const shownum = (v: unknown) => v == null ? "" : typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(v);
// HEX 색 검사(#rgb, #rrggbb)와 3자리 → 6자리
export const HEX = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i;
export const hex6 = (c: string) => c.length === 4 ? "#" + [...c.slice(1)].map(h => h + h).join("") : c;
// 색 글자(#rgb, #rrggbb 또는 rgb)에 투명도를 준다(히트맵 칸, 영역형 라인).
export function alpha(color: string, a: number) {
  const hex = hex6(color).match(/^#([0-9a-f]{6})$/i);
  const [r, g, b] = hex ? [0, 2, 4].map(i => parseInt(hex[1].slice(i, i + 2), 16)) : (color.match(/\d+/g) || [0, 0, 0]).map(Number);
  return `rgba(${r}, ${g}, ${b}, ${a})`;
}

// ---------- 색 ----------
// 라벨의 번호: 카드가 그 라벨을 처음 본 차례(order). 새 라벨은 뒤에 붙여 카드에 남기므로 다시 구한 뒤에도 같은 라벨은 같은 번호(같은 색)다.
// 서버가 order를 200개까지만 받으므로(core/dash_store.py) 그 이상은 붙이지 않고 같은 번호(200)를 쓴다 — 색은 팔레트·기본 색을 돌려 쓴다.
export const MAX_ORDER = 200;
export const labelIndex = (order: string[], name: string) => { const i = order.indexOf(name); return i >= 0 ? i : order.length >= MAX_ORDER ? MAX_ORDER : order.push(name) - 1; };

// ---------- 카드의 값 → 그릴 모양 ----------
// 일(YYYY-MM-DD)이 속한 주의 월요일, 월의 1일, 연의 1월 1일. 서버(analysis.trend의 주)와 같다.
export const monday = (day: string) => { const [y, m, d] = day.split("-").map(Number), t = new Date(Date.UTC(y, m - 1, d)); t.setUTCDate(t.getUTCDate() - (t.getUTCDay() + 6) % 7); return t.toISOString().slice(0, 10); };
// 주 시작 요일·회계연도 첫 달(서버 설정 week_start·fiscal_start_month, sql_funcs.period_start와 같은 규칙). 상수를 받으면 setCalendar가 바꾼다
const CAL = { sunday: false, fiscal: 1 };
export const setCalendar = (weekStart?: string, fiscalStart?: number) => { CAL.sunday = weekStart === "sun"; CAL.fiscal = fiscalStart && fiscalStart >= 1 && fiscalStart <= 12 ? fiscalStart : 1; };
const weekStart = (day: string) => CAL.sunday ? shiftDay(monday(shiftDay(day, 1)), -1) : monday(day);  // 일요일 시작 = 다음 날이 속한 월요일 주의 전날
const fiscalYear = (day: string) => { const y = +day.slice(0, 4) - (+day.slice(5, 7) < CAL.fiscal ? 1 : 0); return `${y}-${String(CAL.fiscal).padStart(2, "0")}-01`; };
export const BUCKET: Record<string, (d: string) => string> = { day: d => d, week: d => weekStart(d), month: d => d.slice(0, 7) + "-01", year: d => fiscalYear(d) };
type Group = { sum: number | null; count: number; min: number | null; max: number | null };
// 일 값 계열 하나를 unit으로 다시 묶은 Map(라벨 → 값). sum=Σsum, count=Σcount, mean=Σsum/Σcount, min·max=min·max(빈 값은 건너뜀).
export function rollup(s: DaySeries, days: string[], unit: string) {
  const groups = new Map<string, Group>();
  days.forEach((day, i) => {
    const key = BUCKET[unit](day), g = groups.get(key) ?? { sum: null, count: 0, min: null, max: null };
    if (s.sum?.[i] != null) g.sum = (g.sum ?? 0) + s.sum[i]!;
    g.count += s.count?.[i] ?? 0;
    if (s.min?.[i] != null) g.min = g.min == null ? s.min[i] : Math.min(g.min, s.min[i]!);
    if (s.max?.[i] != null) g.max = g.max == null ? s.max[i] : Math.max(g.max, s.max[i]!);
    groups.set(key, g);
  });
  const value = ({ count: g => g.count, sum: g => g.sum, mean: g => g.count && g.sum != null ? g.sum / g.count : null, min: g => g.min, max: g => g.max } as Record<string, (g: Group) => number | null>)[s.agg];
  return new Map<string, number | null>([...groups].map(([key, g]) => [key, value(g)]));
}
// ---------- 기간·비교 (기간 규칙은 서버 period.py, 문장은 compare.py. 여기서는 저장된 일 값으로 두 기간의 값만 구한다) ----------
export const dayNumber = (day: string) => Date.UTC(+day.slice(0, 4), +day.slice(5, 7) - 1, +day.slice(8, 10)) / 86400000;
export const shiftDay = (day: string, n: number) => new Date((dayNumber(day) + n) * 86400000).toISOString().slice(0, 10);
export const daysBetween = (start: string, end: string) => dayNumber(end) - dayNumber(start) + 1;
export const zeroWhenEmpty = (agg: string) => agg === "count" || agg === "sum";  // 행이 없는 구간을 0으로 말할 수 있는 집계
// 날짜 기간의 직전 기간 {start, end, text}: 달 단위로 꼭 맞으면(1일 ~ 그달 끝) 직전 같은 개월 수, 아니면 바로 앞 같은 일수(서버 period.previous·previous_text와 같은 규칙)
export function previousSpan(start: string, end: string): PrevSpan {
  const [y1, m1] = start.split("-").map(Number), [y2, m2] = end.split("-").map(Number);
  if (start.endsWith("-01") && shiftDay(end, 1).endsWith("-01")) {
    const months = (y2 - y1) * 12 + m2 - m1 + 1, back = new Date(Date.UTC(y1, m1 - 1 - months, 1)).toISOString().slice(0, 10);
    const named = months === 1 || (months === 3 && m1 % 3 === 1) || (months === 12 && m1 === 1) ? ({ 1: "지난달", 3: "직전 분기", 12: "작년" } as Record<number, string>)[months] : `직전 ${months}개월`;  // 서버 period.month_name
    return { start: back, end: shiftDay(start, -1), text: named };
  }
  const n = daysBetween(start, end);
  return { start: shiftDay(start, -n), end: shiftDay(start, -1), text: `직전 ${n}일` };
}
// 같은 길이 기간을 같은 요일(7의 배수 일수) 앞이나 1년 앞(yoy)으로 옮긴 것(서버 period.shifted_span의 날짜 기간과 같은 규칙)
export function shiftedSpan(start: string, end: string, mode: "same_weekday" | "yoy"): PrevSpan {
  const y1 = +start.slice(0, 4), [y2, m2] = end.split("-").map(Number), months = start.endsWith("-01") && shiftDay(end, 1).endsWith("-01");
  if (mode === "yoy" && months) return { start: `${String(y1 - 1).padStart(4, "0")}-${start.slice(5)}`, end: new Date(Date.UTC(y2 - 1, m2, 0)).toISOString().slice(0, 10), text: "작년 같은 기간" };  // 1일은 어느 해에나 있다. 끝은 그 해의 달 끝(2024-02-29)
  const back = mode === "yoy" ? 364 : Math.ceil(daysBetween(start, end) / 7) * 7;
  return { start: shiftDay(start, -back), end: shiftDay(end, -back), text: mode === "yoy" ? "작년 같은 요일 기간" : `${back / 7}주 전 같은 요일 기간` };
}
// 카드 자신의 기간 줄(날짜컬럼 시작 ~ 끝)과 견줄 기간(mode: 대시보드 비교 기준 compare_mode, 서버 dash_calc.own_range와 같다). 기간 줄이 없으면 null.
export function ownRange(card: Card, mode: "prev" | "same_weekday" | "yoy" = "prev") {
  for (const line of card.source?.body?.periods ?? []) {
    const m = /^.+?\s+(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})$/.exec(line.trim());  // 날짜를 끝에서 읽어 공백 있는 컬럼 이름도 된다(dash_calc.OWN_PERIOD와 같은 규칙)
    const real = (d: string) => !isNaN(Date.parse(d)) && new Date(d).toISOString().slice(0, 10) === d;  // 2026-02-30 같은 없는 날은 건너뛴다
    if (m && real(m[1]) && real(m[2]) && m[1] <= m[2]) return { range: { start: m[1], end: m[2] }, prev: mode === "prev" ? previousSpan(m[1], m[2]) : shiftedSpan(m[1], m[2], mode) };
  }
  return null;
}
// 분석 카드가 어떤 그림인지(없으면 분석 종류에서)
export const anaView = (card: Card) => card.view.analysis_view || ({ distribution: "hist", correlation: "heat", scatter: "scatter", groups: "groups", trend: "trend" } as Record<string, string>)[card.data?.values?.kind as string];
// 산점도·버블: 점은 data.points(서버가 구한 점·r·추세선), 버블의 [크기] 컬럼·추세선·r 보이기는 view.points·trend·show_r
export const isPoints = (card: Card) => ["scatter", "bubble"].includes(card.view?.type) && !!card.data?.points;
export const pointGroups = (p: { groups: string[]; y: string }) => p.groups.length ? p.groups : [p.y];
// 최신화가 읽은 날짜 범위(data.window: 직전 기간 시작 ~ 보는 기간 끝) 밖으로 보는 기간이 나갔는지. 나갔으면 그 기간으로 다시 읽어야 한다.
export const outsideWindow = (card: Card, range?: { start?: string; end?: string } | null) => !!(range && card.data?.window && ((range.start && card.data.window.start && range.start < card.data.window.start) || (range.end && card.data.window.end && range.end > card.data.window.end)));

// ======================= lib/dash_grid.js =======================
// 대시보드 격자 배치의 계산(화면 요소 없이): 12칸 × 행 높이 40px의 격자, 카드 이동·크기 바꾸기, 세로 압축.
// 카드 자리(layout) = {x, y, w, h}: x는 칸(0~11), y는 행, w·h는 2~12. 서버(dash_store.check_layout)와 같은 범위다.
export const GRID_COLS = 12, GRID_ROW = 40, GRID_GAP = 10, GRID_MIN = 2, GRID_MAX = 12;
export type GridItem = { id: string; layout: Layout };
export type GridBox = { left: number; top: number; col: number; row: number; gap?: number };

const toInt = (v: unknown, fallback: number) => (Number.isFinite(+(v as number)) ? Math.round(+(v as number)) : fallback);
const clampInt = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

// 범위 안으로 맞춘 자리: w·h는 2~12, x는 0 ~ 12 - w, y는 0 이상(999까지)
export function clampLayout(l?: Partial<Layout> | null): Layout {
  const w = clampInt(toInt(l?.w, 6), GRID_MIN, GRID_MAX), h = clampInt(toInt(l?.h, 6), GRID_MIN, GRID_MAX);
  return { x: clampInt(toInt(l?.x, 0), 0, GRID_COLS - w), y: clampInt(toInt(l?.y, 0), 0, 999), w, h };
}

// 두 자리가 겹치는지(맞닿기만 하면 겹침이 아니다)
export const overlaps = (a: Layout, b: Layout) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

// 세로 압축: 움직인 카드(movedId)를 먼저 놓고, 나머지는 (y, x) 순으로 돌며 이미 놓인 카드와 겹치지 않는 가장 작은 y에 놓는다(x는 그대로).
// items: [{id, layout}]. 돌려주는 값: {id: 새 자리}. movedId가 없으면 모두 (y, x) 순으로 위로 붙인다.
export function compactLayout(items: GridItem[], movedId?: string) {
  const placed: Layout[] = [], out: Record<string, Layout> = {};
  const put = (id: string, l: Layout) => { out[id] = l; placed.push(l); };
  const moved = items.find(i => i.id === movedId);
  if (moved) put(moved.id, clampLayout(moved.layout));
  const rest = items.filter(i => i !== moved).map(i => ({ id: i.id, layout: clampLayout(i.layout) })).sort((a, b) => a.layout.y - b.layout.y || a.layout.x - b.layout.x);
  for (const { id, layout } of rest) {
    let y = 0;
    while (placed.some(p => overlaps({ ...layout, y }, p))) {
      y = Math.min(...placed.filter(p => overlaps({ ...layout, y }, p)).map(p => p.y + p.h));  // 겹치는 카드의 바로 아래로 건너뛴다
    }
    put(id, { ...layout, y });
  }
  return out;
}

// 포인터가 가리키는 칸: 격자 왼쪽 위(left, top)에서 칸 간격(col·row는 칸 하나 + 간격의 길이)으로 나눠 반올림한다. 카드 안에서 잡은 자리(grabX·grabY)만큼은 뺀다.
export function cellAt(px: number, py: number, { left, top, col, row }: GridBox, grabX = 0, grabY = 0) {
  return { x: Math.round((px - left - grabX) / col), y: Math.round((py - top - grabY) / row) };
}

// 카드를 cell 자리로 옮긴 뒤의 모든 카드 자리
export function moveCard(items: GridItem[], id: string, cell: { x: number; y: number }) {
  const item = items.find(i => i.id === id);
  if (!item) return compactLayout(items);
  const once = compactLayout(items.map(i => (i === item ? { id, layout: { ...i.layout, x: cell.x, y: cell.y } } : i)), id);
  return compactLayout(items.map(i => ({ id: i.id, layout: once[i.id] })));  // 놓은 카드도 위로 붙인다(빈 아래쪽에 뜨지 않게, docs: 빈 곳은 위로 메워진다)
}

// 카드 크기를 w·h(2~12)로 바꾼 뒤의 모든 카드 자리. 오른쪽 끝을 넘으면 x를 당긴다.
export function resizeCard(items: GridItem[], id: string, w: number, h: number) {
  const item = items.find(i => i.id === id);
  if (!item) return compactLayout(items);
  const next = clampLayout({ ...item.layout, w, h });
  return compactLayout(items.map(i => (i === item ? { id, layout: next } : i)), id);
}

// 손잡이를 끌 때의 새 크기: 포인터가 카드 왼쪽 위에서 얼마나 떨어졌는지(dx, dy 픽셀)를 칸으로
export function sizeAt(dx: number, dy: number, { col, row, gap = GRID_GAP }: GridBox) {
  return { w: clampInt(Math.round((dx + gap) / col), GRID_MIN, GRID_MAX), h: clampInt(Math.round((dy + gap) / row), GRID_MIN, GRID_MAX) };
}

// 새 카드를 놓을 자리: 맨 아래(왼쪽)
export function nextLayout(items: GridItem[], w = 6, h = 8): Layout {
  return { x: 0, y: Math.min(999, Math.max(0, ...items.map(i => i.layout.y + i.layout.h))), w, h };
}

// ======================= lib/dash_rules.js =======================
// 카드 설정 글 읽기: 조건 색 규칙(cond_rules)의 식을 읽어 칸 값에 맞는지 판정하고, 상태 규칙·연결선·문장 틀을 한 줄 글에서 읽는다.
// 조건 색 규칙 {column, match, color(HEX), arrow(none·up·down·auto), scope(cell·row)}: 맞는 칸만 색을 칠하고, 여러 규칙이 맞으면 위의 규칙 하나.
//   숫자 식: `= 0`, `> 100`, `>= 1,000`, `< -5%`, `a ~ b`(사이), 목록 `1, 3, 7`(이 숫자들만).  단어 식: 정확히 `취소`, 목록 `취소, 환불`, 포함 `*지연*`, 시작 `ERR*`, 끝 `*실패`(대소문자 무시).
// 틀린 규칙(숫자 칸에 단어 식, 읽을 수 없는 식, 틀린 HEX)은 이유 문장을 돌려주고 적용하지 않는다.
/** parseMatch 결과: error가 없으면 kind·test가 있다 */
export type Match = { kind?: "num" | "word"; test?: (v: any) => boolean; error?: string };  // eslint-disable-line @typescript-eslint/no-explicit-any
export type Compiled = { rule: CondRule; parsed: Match; error: string };

// "₩1,000"·"-5%"·"1,234.5" → 숫자(%는 ÷100). 숫자가 아니면 null. 안쪽에 공백이 있으면("100, 200") 숫자가 아니다(목록으로 읽힌다).
export function ruleNumber(text: unknown) {
  const t = String(text).trim().replace(/₩\s*/g, "");
  if (!/^-?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?%?$/.test(t)) return null;
  const n = parseFloat(t.replace(/,/g, "").replace("%", ""));
  return t.endsWith("%") ? n / 100 : n;
}
const sameNum = (a: number, b: number) => Math.abs(a - b) <= Math.max(1e-9 * Math.abs(b), 1e-12);  // DB 합계의 꼬리(0.1 + 0.2)는 같은 값으로
const OPERATORS: Record<string, (a: number, b: number) => boolean> = { ">=": (a, b) => a >= b || sameNum(a, b), "<=": (a, b) => a <= b || sameNum(a, b), ">": (a, b) => a > b && !sameNum(a, b), "<": (a, b) => a < b && !sameNum(a, b), "=": sameNum, "==": sameNum, "!=": (a, b) => !sameNum(a, b) };
// 식 글자 → { kind: "num"·"word", test(값) } 또는 { error }
export function parseMatch(text: unknown): Match {
  const t = String(text ?? "").trim();
  if (!t) return { error: "식을 적어 주세요. 예: > 100, 취소, *지연*" };
  const between = t.match(/^(.+?)\s*~\s*(.+)$/);
  if (between) {
    const [a, b] = [ruleNumber(between[1]), ruleNumber(between[2])];
    if (a == null || b == null) return { error: "~ 양쪽은 숫자여야 해요. 예: 10 ~ 20" };
    return { kind: "num", test: v => OPERATORS[">="](v, Math.min(a, b)) && OPERATORS["<="](v, Math.max(a, b)) };
  }
  const op = t.match(/^(>=|<=|!=|==|=|>|<)\s*(.*)$/);
  if (op) {
    const n = ruleNumber(op[2]);
    return n == null ? { error: `‘${op[2] || ""}’은 숫자가 아니에요. 예: ${op[1]} 100` } : { kind: "num", test: v => OPERATORS[op[1]](v, n) };
  }
  const single = ruleNumber(t);
  if (single != null) return { kind: "num", test: v => sameNum(v, single) };
  const parts = t.split(",").map(p => p.trim()).filter(Boolean);
  if (parts.length > 1 || t.includes(",")) {
    const nums = (/,\s/.test(t) ? t.split(/\s*,\s+/) : parts).map(ruleNumber);  // ", "로 나눈 목록이면 1,000 같은 천 단위 쉼표는 숫자 안에 둔다
    return nums.every(n => n != null) ? { kind: "num", test: v => nums.some(n => sameNum(v, n!)) } : { kind: "word", test: s => parts.some(p => p.toLowerCase() === s) };
  }
  if (t.includes("*")) {
    const core = t.replace(/^\*|\*$/g, "").toLowerCase();
    if (!core || core.includes("*")) return { error: "* 는 앞뒤에만 쓸 수 있어요. 예: *지연*, ERR*, *실패" };
    const [head, tail] = [t.startsWith("*"), t.endsWith("*")];
    return { kind: "word", test: s => head && tail ? s.includes(core) : tail ? s.startsWith(core) : s.endsWith(core) };
  }
  return { kind: "word", test: s => s === t.toLowerCase() };
}
// 규칙 하나를 컴파일: { rule, parsed, error }. columnKinds는 {컬럼: "num"·"word"}(칸 값의 종류를 알면 숫자 칸에 단어 식 같은 어긋남을 알린다)
export function compileRule(rule: CondRule, columnKinds: Record<string, string> = {}): Compiled {
  const parsed = parseMatch(rule.match);
  let error = parsed.error ?? "";
  if (!error && !HEX.test(rule.color ?? "")) error = `색 ‘${rule.color ?? ""}’은 HEX가 아니에요(예: #e03131)`;
  const kind = columnKinds[rule.column];
  if (!error && kind && kind !== parsed.kind) error = kind === "num" ? `‘${rule.column}’은 숫자 칸이라 단어 식은 쓸 수 없어요` : `‘${rule.column}’은 글자 칸이라 숫자 식은 쓸 수 없어요`;
  return { rule, parsed, error };
}
export const compileRules = (rules: CondRule[] = [], columnKinds: Record<string, string> = {}) => rules.map(r => compileRule(r, columnKinds));
// 칸(컬럼 column, 값 value)에 맞는 첫 규칙(맞는 것이 없으면 null). 오류가 있는 규칙은 건너뛴다. 빈 값은 맞지 않는다.
export function matchRule(compiled: Compiled[], column: string, value: unknown) {
  if (value == null || value === "") return null;
  for (const c of compiled) {
    if (c.error || c.rule.column !== column) continue;
    if (c.parsed.kind === "num" ? typeof value === "number" && c.parsed.test!(value) : typeof value !== "number" && c.parsed.test!(String(value).toLowerCase())) return c.rule;
  }
  return null;
}
// 화살표: up ▲ / down ▼ 고정, auto는 직전 값(prev)이 있을 때만 오르면 ▲ 내리면 ▼. 화면에 같은 값으로 보이면(shown으로 바꾼 글이 같으면) 강조 없이 =.
// 색은 규칙 색, 없으면 비교 tone(better가 down이면 반대).
export function ruleArrow(rule: Pick<CondRule, "arrow" | "color">, value: unknown, prev: unknown, better = "up", shown: (v: unknown) => string = String) {
  let text = "";
  if (rule.arrow === "up") text = "▲"; else if (rule.arrow === "down") text = "▼";
  else if (rule.arrow === "auto" && prev != null && typeof value === "number") text = value === prev || shown(value) === shown(prev) ? "=" : value > (prev as number) ? "▲" : "▼";
  if (!text) return null;
  if (text === "=") return { text, color: null, tone: "neutral" };
  const good = text === "▲" ? better === "up" : better === "down";
  return { text, color: rule.color || null, tone: good ? "good" : "bad" };
}
// 칸 값의 종류(숫자 칸·글자 칸): 비어 있지 않은 첫 값으로
export function kindOfValues(values: unknown[]) {
  const first = values.find(v => v != null && v !== "");
  return first == null ? null : typeof first === "number" ? "num" : "word";
}

// ---------- 한 줄 글 읽기 ----------
const ARROW_WORDS: Record<string, CondRule["arrow"]> = { "": "none", 없음: "none", none: "none", 위: "up", up: "up", 아래: "down", down: "down", 자동: "auto", auto: "auto" };
const SCOPE_WORDS: Record<string, CondRule["scope"]> = { "": "cell", 칸: "cell", cell: "cell", 줄: "row", row: "row" };
// `컬럼 | 식 | #색 | 화살표(없음·위·아래·자동) | 칸·줄` → { rule } 또는 { error }
export function parseRuleLine(line: string): { rule?: CondRule; error?: string } {
  const p = line.split("|").map(x => x.trim());
  if (p.length < 3) return { error: "‘컬럼 | 식 | #색 | 화살표 | 칸·줄’ 모양으로 적어 주세요. 예: 상태 | *지연* | #e03131 | 없음 | 줄" };
  const [column, match, color, arrow = "", scope = ""] = p;
  if (!column) return { error: "컬럼 이름을 적어 주세요" };
  if (!(arrow in ARROW_WORDS)) return { error: `화살표는 없음·위·아래·자동 중 하나예요(‘${arrow}’)` };
  if (!(scope in SCOPE_WORDS)) return { error: `적용 범위는 칸·줄 중 하나예요(‘${scope}’)` };
  const rule = { column, match, color, arrow: ARROW_WORDS[arrow], scope: SCOPE_WORDS[scope] }, found = compileRule(rule);
  return found.error ? { error: found.error } : { rule };
}
const LEVEL_WORDS: Record<string, "warn" | "bad"> = { 주의: "warn", warn: "warn", 이상: "bad", bad: "bad" };
// `컬럼 연산자 값 단계(주의·이상)` → { rule: {column, op, value, level} } 또는 { error }. 예: cpu >= 90 이상. 첫 연산자 왼쪽 전체가 컬럼(공백 있는 이름도)
export function parseStateLine(line: string) {
  const m = line.trim().match(/^(.+?)\s*(>=|<=|!=|>|<|=)\s*(.+?)\s+(\S+)$/);
  if (!m) return { error: "‘컬럼 연산자 값 단계’ 모양으로 적어 주세요. 예: cpu >= 90 이상" };
  if (!(m[4] in LEVEL_WORDS)) return { error: `단계는 주의나 이상이에요(‘${m[4]}’)` };
  const n = ruleNumber(m[3]);
  return { rule: { column: m[1], op: m[2], value: n ?? m[3], level: LEVEL_WORDS[m[4]] } };
}
// `출발 -> 도착` → { link: [출발, 도착] } 또는 { error }
export function parseLinkLine(line: string) {
  const m = line.trim().match(/^(.+?)\s*(?:->|→)\s*(.+)$/);
  return m ? { link: [m[1].trim(), m[2].trim()] as [string, string] } : { error: "‘출발 -> 도착’ 모양으로 적어 주세요" };
}
// 여러 줄 글을 줄마다 읽는다: [{ line, ...결과 }](빈 줄은 건너뜀). 화면은 오류가 있는 줄 옆에 이유를 보인다.
export const readLines = <T extends object>(text: string, parse: (line: string) => T) => text.split("\n").map(l => l.trim()).filter(Boolean).map(line => ({ line, ...parse(line) }));
// 문장 틀의 {이름}을 값 글자로 바꾼 조각들 [{ text } | { blank: 이름 }](화면이 빈칸에 색을 입힌다)
export function templateParts(template: unknown): ({ text: string; blank?: undefined } | { blank: string; text?: undefined })[] {
  return String(template ?? "").split(/(\{[^{}]+\})/).filter(Boolean).map(p => /^\{[^{}]+\}$/.test(p) ? { blank: p.slice(1, -1) } : { text: p });
}
// 규칙 하나 → 편집 칸의 한 줄 글(parseRuleLine의 반대)
const ARROW_NAMES: Record<string, string> = { none: "없음", up: "위", down: "아래", auto: "자동" };
export const ruleLine = (r: CondRule) => [r.column, r.match, r.color, ARROW_NAMES[r.arrow] ?? "없음", r.scope === "row" ? "줄" : "칸"].join(" | ");

// ======================= lib/dash_svg.js =======================
// 대시보드 카드의 값 → 모양 계산: 목표 비교, 호·고리·게이지 조각·칸·블록·교차·다각형·선 점, 값 글자, 어두운 색, 편차 칸, 배치도 처음 자리.
export const TAU = Math.PI * 2, RING_GAP = 0.06;  // 게이지 조각 사이 간격(라디안)
export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const slack = (t: number) => Math.max(Math.abs(t) * 1e-9, 1e-12);
export const overTarget = (v: number, t: number) => v > t + slack(t), underTarget = (v: number, t: number) => v < t - slack(t);  // 합계의 부동소수 꼬리(1.1 + 2.2)는 목표와 같은 값으로(서버 summary_text와 같은 기준)
// 12시 방향이 0, 시계 방향으로 커지는 각도의 점
export const polar = (cx: number, cy: number, r: number, a: number) => [cx + r * Math.sin(a), cy - r * Math.cos(a)];
// 각도 a0에서 a1까지(시계 방향) 호의 SVG 경로. 한 바퀴 가까우면 둘로 나눈다.
export function arcPath(cx: number, cy: number, r: number, a0: number, a1: number): string {
  if (a1 - a0 >= TAU - 1e-6) return arcPath(cx, cy, r, a0, a0 + Math.PI) + " " + arcPath(cx, cy, r, a0 + Math.PI, a0 + TAU - 1e-4);
  const [x0, y0] = polar(cx, cy, r, a0), [x1, y1] = polar(cx, cy, r, a1);
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}
// 고리(circle)의 stroke-dasharray: 원주 2πr 중 비율만큼만 칠한다.
export function ringDash(r: number, ratio: number) {
  const circ = TAU * r, len = circ * clamp(ratio, 0, 1);
  return { circ, len, dash: `${len.toFixed(2)} ${(circ - len).toFixed(2)}` };
}
// 항목 값들의 몫으로 나눈 호들(조각 사이 간격 RING_GAP). 값이 0 이하이거나 빈 항목은 뺀다. 가장 큰 항목에 max 표시(굵게 그린다).
export function gaugeSegments(values: (number | null)[], gap = RING_GAP) {
  const items = values.map((v, i) => ({ i, v })).filter((x): x is { i: number; v: number } => x.v != null && x.v > 0), total = items.reduce((a, x) => a + x.v, 0);
  if (!total) return [];
  const room = TAU - gap * items.length, top = Math.max(...items.map(x => x.v));
  let at = 0;
  return items.map(x => { const span = room * x.v / total, seg = { index: x.i, share: x.v / total, a0: at, a1: at + span, max: x.v === top }; at += span + gap; return seg; });
}
// 칸 채우기: 채운 칸 = round(비율 × n), 목표 칸(1~n)은 목표 비율로
export function stripeCells(ratio: number, n: number, targetRatio?: number | null) {
  return { filled: Math.round(clamp(ratio, 0, 1) * n), target: targetRatio == null ? null : clamp(Math.round(targetRatio * n), 1, n) };
}
// 블록: 한 칸 = size. 실제 칸, 목표까지 모자란 칸, 목표를 넘은 칸(실제 칸 안의 마지막 over 칸). 칸이 cap을 넘으면 같은 비율로 줄인다(scale = 줄인 배수).
export function blockCells(value: number, target: number | null | undefined, size: number, cap = 200) {
  const actual = Math.floor(value / size), goal = target == null ? actual : Math.floor(target / size), scale = Math.max(1, Math.ceil(Math.max(actual, goal) / cap));
  return { actual: Math.floor(actual / scale), toTarget: Math.max(0, Math.floor(goal / scale) - Math.floor(actual / scale)), over: Math.max(0, Math.floor((actual - goal) / scale)), scale };
}
// 값이 목표선을 위에서 아래로 지나간 횟수(앞 값 ≥ 목표 > 지금 값). 빈 값은 건너뛴다.
export function crossDown(values: (number | null)[], target: number) {
  let n = 0, before: number | null = null;
  for (const v of values) { if (v == null) continue; if (before != null && !underTarget(before, target) && underTarget(v, target)) n++; before = v; }
  return n;
}
// 선(xs, vs)이 목표(target) 아래에 있는 구간과 목표선 사이의 다각형들 [[x, y], …]. 목표선을 지나는 곳은 선분 위에서 보간한다. yOf는 값 → 화면 y.
export function belowPolygons(xs: number[], vs: (number | null)[], target: number, yOf: (v: number) => number) {
  const polys: number[][][] = [], ty = yOf(target);
  let run: number[][] | null = null;
  const close = (x: number) => { if (run) { run.push([x, ty]); polys.push(run); run = null; } };
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
export function sparkPoints(values: (number | null)[], w: number, h: number, pad = 2) {
  const real = values.filter((v): v is number => v != null), lo = Math.min(...real), hi = Math.max(...real), n = values.length;
  return values.map((v, i) => v == null ? null : `${(pad + (n < 2 ? (w - 2 * pad) / 2 : (w - 2 * pad) * i / (n - 1))).toFixed(1)},${(hi === lo ? h / 2 : h - pad - (h - 2 * pad) * (v - lo) / (hi - lo)).toFixed(1)}`).filter(Boolean).join(" ");
}
// 점들("x,y x,y …")을 부드러운 곡선 경로로: 점과 점 사이 가운데를 지나는 2차 곡선(꺾인 곳이 뾰족하지 않게, 큰 차트의 곡선과 비슷하게)
export function smoothPath(points: string) {
  const p = points.split(" ").map(s => s.split(",").map(Number));
  let d = `M${p[0][0]},${p[0][1]}`;
  for (let i = 1; i < p.length - 1; i++) d += ` Q${p[i][0]},${p[i][1]} ${((p[i][0] + p[i + 1][0]) / 2).toFixed(1)},${((p[i][1] + p[i + 1][1]) / 2).toFixed(1)}`;
  return d + ` L${p.at(-1)![0]},${p.at(-1)![1]}`;
}
// 값 글자(서버 core/compare.format_value와 같은 모양): number는 쉼표·소수 둘째 자리, percent는 ×100 소수 둘째 자리 %, currency는 ₩ + 쉼표(음수는 -₩). unit은 뒤에 붙인다.
// 숫자가 아닌 값(string_agg·first·any_value 등)은 글자 그대로.
export function showValue(v: unknown, format = "number", unit = "") {
  if (v == null) return "—";
  if (typeof v !== "number") return String(v) + unit;
  const text = format === "percent" ? roundText(v * 100, 2) + "%" : format === "currency" ? roundText(v, 0, "₩") : roundText(v, 2);
  return text + unit;
}
// compare.trimmed와 같은 규칙: 절댓값을 d자리에서 5를 올려 반올림(2.5 → 3), 쉼표, 끝의 0을 떼고, -0은 0. prefix(₩)는 부호 뒤에.
export function roundText(x: number, d: number, prefix = "") {
  const a = Math.abs(x), s = String(a), r = s.includes("e") ? +a.toFixed(d) : Math.round(+`${s}e${d}`) / 10 ** d;  // 10진 글자 그대로 반올림(1.005 → 1.01, toFixed는 1.00)
  return (x < 0 && r ? "-" : "") + prefix + r.toLocaleString(undefined, { maximumFractionDigits: d });
}
// 목표와의 차이 글자: percent는 %p, 그 밖은 값 모양대로
export const showGap = (v: number, format?: string) => format === "percent" ? `${+(Math.abs(v) * 100).toFixed(1)}%p` : showValue(Math.abs(v), format);
// 어두운 색(목표를 넘은 부분): #rrggbb를 비율만큼 어둡게
export function darken(hex: string, k = 0.35) {
  const h = hex6(hex);
  return "#" + [1, 3, 5].map(i => Math.round(parseInt(h.slice(i, i + 2), 16) * (1 - k)).toString(16).padStart(2, "0")).join("");
}
// deviation: 기준(baseline, 없으면 값들의 평균) 대비 ±. 기준 넘는 칸(허용 폭 threshold, 없으면 기준의 20%)을 over로
export function deviationCells(values: (number | null)[], baseline?: number | null, threshold?: number | null) {
  const real = values.filter((v): v is number => v != null), base = baseline ?? (real.length ? real.reduce((a, b) => a + b, 0) / real.length : null), limit = threshold ?? (base == null ? 0 : Math.abs(base) * 0.2);
  return { base, cells: values.map(v => v == null || base == null ? { v, dev: null, over: false } : { v, dev: v - base, over: Math.abs(v - base) > limit }) };
}
// 배치도의 처음 배치: 종류별로 한 줄씩, 한 줄 안에서 고르게(0~1 비율)
export function autoGrid(entities: { name: string; kind?: string }[]) {
  const kinds = [...new Set(entities.map(e => e.kind || ""))], out: Record<string, [number, number]> = {};
  kinds.forEach((k, row) => { const same = entities.filter(e => (e.kind || "") === k); same.forEach((e, j) => { out[e.name] = [(j + 1) / (same.length + 1), (row + 1) / (kinds.length + 1)]; }); });
  return out;
}
