// (Phase 9) 시각화 자동 정렬·렌더링 가드레일의 화면 규칙(순수 계산, DOM·React 없음). 대시보드 ChartCard와 쿼리 화면 차트 섬(web/app/chart)이 같은 함수를 쓴다.
// 규칙마다 기본 켬, 카드 view.auto[규칙] = false면 끔, 객체면 수치를 바꾼다(기본값·범위는 서버 core/viz_rules.AUTO_PARAMS → 상수 dash.auto).
// 사용자 지정(정렬 body.order가 auto가 아님, 종류 view.type, 축 범위 view.ranges, 고른 그룹 view.groups.keep)이 있으면 그것이 먼저다. view.all_data면 덜어 내는 규칙(Top-N·기타·파이 상한·LTTB)을 풀고 전부 그린다.
// 서버 규칙(히스토그램 구간 수 bins·꼬리 자르기 tail·산점도 밀도 density)은 core/viz_rules.py.
import type { Dataset, SCard, Series } from "@/components/cards/chart/series";
import type { ChartType, Unit } from "@/lib/types";
import { textUnits } from "@/components/cards/svg/__pure__";
import { shownum } from "@/lib/calc";
import { HBARS, ROUND_TYPES } from "@/components/cards/chart/shapes";

/** 범주 축의 종류(Phase 5 서버 추론 결과, 임시 키 data.x_scale·x_levels) */
export type Scale = "nominal" | "ordinal" | "interval";
/** 정렬에 쓴 규칙 */
export type OrderRule = "time" | "number" | "user" | "calendar" | "ordinal" | "stack" | "grouped" | "diverging" | "nominal";
/** 화면 규칙 이름(서버 규칙 bins·tail·density 중 density의 점 투명도 단계도 화면이 쓴다) */
export type AutoRule = "sort" | "pin" | "topn" | "round" | "horizontal" | "spaghetti" | "zero" | "yzoom" | "log_hint" | "sync" | "resample" | "density" | "num" | "refs" | "semantic" | "bins" | "tail";
/** 서버 AUTO_PARAMS 한 칸: [기본값, 최솟값, 최댓값](숫자) 또는 [기본값, 고를 값들](글자·목록) */
export type ParamSpec = [number, number, number] | [string | string[], string[] | null];
/** 상수 dash.auto: 규칙 → 수치 이름 → ParamSpec */
export type AutoTable = Record<AutoRule, Record<string, ParamSpec>>;
/** 카드 view.auto: 규칙마다 false(끔) 또는 바꾼 수치. 키가 없으면 기본 켬·기본 수치 */
export type AutoView = Partial<Record<AutoRule, false | Record<string, number | string | string[]>>>;
/** 켠 규칙의 수치(끈 규칙은 null) */
export type AutoParams = Record<AutoRule, Record<string, number | string | string[]> | null>;
/** 사용자가 고른 값 축 눈금(view.scale, 없으면 보통) */
export type AxisScale = "log" | "symlog";
/** 산점도 점 그리기 단계 */
export type Tier = { mode: "dots" | "faint" | "hex"; alpha: number; r: number };
/** 그림 컴포넌트(Charts.tsx)가 읽는 자동 규칙 결과(SCard.auto, 화면에서만 붙인다) */
export type AutoHints = {
  rule: OrderRule; zeroLine: boolean; gray: number[]; small: boolean; clockwise: boolean;
  yDomain: [number, number] | null; notZero: boolean; barLo: number | null; y1Pct: [number, number] | null;
  refs: { label: string; value: number; axis: "y" | "y1" }[]; num: "ko" | "en" | null; thin: number | null; semantic: boolean;
};
/** 의미 색·핀 낱말(사용자가 더하고 고치고 지운다, 서버 설정 파일 viz.json의 words → 상수 dash.viz.words). good·bad는 들어 있으면, other·null은 이름 전체가 같으면 */
export type SemanticWords = { good: string[]; bad: string[]; other: string[]; null: string[] };
/** autoSeries가 받는 그림 밖 정보: 그림 너비(px, 0이면 아직 모름), 계열마다 집계 id(body.series 차례), 서버가 보낸 상위 밖 값(values.rest), 축 종류, 낱말 */
export type AutoEnv = {
  width: number; table: AutoTable; aggs: (string | undefined)[]; rest: (number | null)[] | null; truncated: boolean;
  xKind: "num" | "date" | "text" | undefined; scale: Scale | undefined; levels: string[] | undefined; order: Order; weekStart: "mon" | "sun"; words: SemanticWords;
};
/** 정렬(body.order, 서버 result.ORDERS와 같은 값): auto 자동 / x 원래 순서(서버 X 순) / value 값 내림 / value_asc 값 오름 / name 이름 순 */
export type Order = "auto" | "x" | "value" | "value_asc" | "name";
/** 차트 데이터 표(쿼리 화면 chartData와 같은 모양): columns 첫째가 X, 다음이 계열 이름, 행은 {컬럼 이름: 값} 객체 */
export type ChartTable = { columns: string[]; rows: Record<string, string | number | null>[] };

export const OTHERS_PREFIX = "기타";  // 자동으로 만든 '기타' 막대 이름의 앞(기타(외 K개)), 낱말 목록과 상관없이 늘 '기타'
export const STACKED = ["stacked", "stack100", "hstack", "hstack100", "sarea", "sarea100"];  // 누적 그림(나비형은 뺀다)
export const TURN_FILL = 0.9;  // 라벨 폭이 칸 폭의 이 몫을 넘으면 꺾어야 한다고 본다
export const LABEL_EM = 0.62;  // 라벨 글자 한 단위의 폭(em)
export const LABEL_FONT = 12;  // 축 라벨 글자 크기(px, Charts TICK)
export const SYMLOG_C = 1;  // SymLog 축 상수: sign(v)·log10(1 + |v|/C)
export const DOT_R = 3, FAINT_R = 2;  // 산점도 점 반지름: 보통 / 1,000개 넘을 때(점 축소)
export const OTHERS_MERGE: Record<string, "sum" | "min" | "max"> = { count: "sum", sum: "sum", min: "min", max: "max" };  // '기타'로 합칠 수 있는 집계와 합치는 법
export const WEEKDAYS: string[][] = [  // 요일 이름(월요일부터), 비교는 trim().toLowerCase()
  ["월", "월요일", "mon", "monday"], ["화", "화요일", "tue", "tuesday"], ["수", "수요일", "wed", "wednesday"], ["목", "목요일", "thu", "thursday"],
  ["금", "금요일", "fri", "friday"], ["토", "토요일", "sat", "saturday"], ["일", "일요일", "sun", "sunday"],
];
export const MONTHS: string[][] = [  // 월 이름(1월부터)
  ["1월", "01월", "jan", "january"], ["2월", "02월", "feb", "february"], ["3월", "03월", "mar", "march"], ["4월", "04월", "apr", "april"],
  ["5월", "05월", "may", "may"], ["6월", "06월", "jun", "june"], ["7월", "07월", "jul", "july"], ["8월", "08월", "aug", "august"],
  ["9월", "09월", "sep", "september"], ["10월", "10월", "oct", "october"], ["11월", "11월", "nov", "november"], ["12월", "12월", "dec", "december"],
];
export const TONE_COLORS = { good: "var(--ok)", bad: "var(--err)", none: "var(--muted)" } as const;
export const NOT_ZERO_TEXT = "0부터 아님";  // 동적 Y축 줌일 때 값 축 제목
export const LOG_NEEDS_POSITIVE = "로그 축은 0보다 큰 값만 그릴 수 있어요. 값 축을 SymLog로 고르세요.";

// [SPECIFICATION] plans/phase-9.md#autoParams
export function autoParams(auto: AutoView | undefined, table: AutoTable): AutoParams {
  return Object.fromEntries((Object.keys(table) as AutoRule[]).map((rule) => {
    if (auto?.[rule] === false) return [rule, null];
    const defaults = Object.fromEntries(Object.entries(table[rule]).map(([name, spec]) => [name, spec[0]]));
    return [rule, { ...defaults, ...(auto?.[rule] || {}) }];
  })) as AutoParams;
}
// [SPECIFICATION] plans/phase-9.md#calendarIndex
export function calendarIndex(labels: (string | null)[], weekStart: "mon" | "sun"): number[] | null {
  const named = labels.filter((l): l is string => l !== null);
  if (named.length < 2) return null;
  const rankIn = (table: string[][], shift: number) => {
    const ranks = labels.map((l) => (l === null ? null : table.findIndex((names) => names.includes(l.trim().toLowerCase()))));
    return ranks.some((r, i) => labels[i] !== null && r === -1) ? null : ranks.map((r) => (r === null ? table.length : (r + shift) % table.length));
  };
  const ranks = rankIn(WEEKDAYS, weekStart === "sun" ? 1 : 0) ?? rankIn(MONTHS, 0);
  if (!ranks) return null;
  return labels.map((_, i) => i).sort((a, b) => ranks[a] - ranks[b]);
}
// [SPECIFICATION] plans/phase-9.md#levelIndex
export function levelIndex(labels: (string | null)[], levels: string[]): number[] {
  const rank = labels.map((l) => (l === null ? levels.length + 1 : levels.includes(l) ? levels.indexOf(l) : levels.length));
  return labels.map((_, i) => i).sort((a, b) => rank[a] - rank[b]);
}
// [SPECIFICATION] plans/phase-9.md#valueIndex
export function valueIndex(keys: (number | null)[]): number[] {
  const valid = (k: number | null): k is number => k !== null && !Number.isNaN(k);
  return keys.map((_, i) => i).sort((a, b) => {
    const ka = keys[a], kb = keys[b];
    if (valid(ka) && valid(kb)) return kb - ka || a - b;
    return valid(ka) ? -1 : valid(kb) ? 1 : a - b;
  });
}
// [SPECIFICATION] plans/phase-9.md#labelKeys
export function labelKeys(datasets: Dataset[], by: string): (number | null)[] {
  const finite = (v: number | string | null): number | null => (v !== null && Number.isFinite(Number(v)) ? Number(v) : null);
  if (by === "") {
    return (datasets[0]?.values ?? []).map((_, i) => {
      const nums = datasets.map((d) => finite(d.values[i])).filter((n): n is number => n !== null);
      return nums.length ? nums.reduce((a, b) => a + b, 0) : null;
    });
  }
  const main = datasets.find((d) => d.label === by);
  if (!main) throw new Error(`대표 계열이 없어요: ${by}`);
  return main.values.map(finite);
}
// [SPECIFICATION] plans/phase-9.md#stackIndex
export function stackIndex(datasets: Dataset[]): number[] {
  const size = datasets.map((d) => d.values.reduce((sum: number, v) => (v !== null && Number.isFinite(Number(v)) ? sum + Math.abs(Number(v)) : sum), 0));
  return datasets.map((_, i) => i).sort((a, b) => size[b] - size[a] || a - b);
}
// [SPECIFICATION] plans/phase-9.md#isDiverging
export function isDiverging(values: (number | string | null)[]): boolean {
  const nums = values.filter((v) => v !== null && Number.isFinite(Number(v))).map(Number);
  return nums.some((n) => n > 0) && nums.some((n) => n < 0);
}
// [SPECIFICATION] plans/phase-9.md#tailKind
export function tailKind(label: string | null, words: SemanticWords): "other" | "null" | null {
  if (label === null || label === "" || words.null.includes(label)) return "null";
  return words.other.includes(label) || label.startsWith(`${OTHERS_PREFIX}(외 `) ? "other" : null;
}
// [SPECIFICATION] plans/phase-9.md#pinTail
export function pinTail(labels: (string | null)[], index: number[], words: SemanticWords): number[] {
  const kinds = index.map((i) => tailKind(labels[i], words));
  return ([null, "other", "null"] as const).flatMap((kind) => index.filter((_, j) => kinds[j] === kind));
}
// [SPECIFICATION] plans/phase-9.md#userIndex
export function userIndex(s: Series, order: Exclude<Order, "auto">): number[] {
  const id = s.labels.map((_, i) => i);
  if (order === "x") return id;
  if (order === "value") return valueIndex(labelKeys(s.datasets, ""));
  if (order === "value_asc") {
    const keys = labelKeys(s.datasets, "");
    return id.sort((a, b) => {
      const ka = keys[a], kb = keys[b];
      if (ka !== null && kb !== null) return ka - kb || a - b;
      return ka !== null ? -1 : kb !== null ? 1 : a - b;
    });
  }
  const labels = s.labels as (string | null)[];
  return id.sort((a, b) => {
    const la = labels[a], lb = labels[b];
    if (la !== null && lb !== null) return String(la).localeCompare(String(lb), "ko", { numeric: true }) || a - b;
    return la !== null ? -1 : lb !== null ? 1 : a - b;
  });
}
// [SPECIFICATION] plans/phase-9.md#axisOrder
export function axisOrder(s: Series, env: Pick<AutoEnv, "xKind" | "scale" | "levels" | "order" | "weekStart">, type: string, groupBy: string): { rule: OrderRule; index: number[]; stack: number[] | null } {
  if (env.order !== "auto") return { rule: "user", index: userIndex(s, env.order), stack: null };
  const id = s.labels.map((_, i) => i);
  const labels = s.labels as (string | null)[];
  if (s.time || env.xKind === "date") return { rule: "time", index: id, stack: null };
  if (env.xKind === "num") return { rule: "number", index: id, stack: null };
  if (env.xKind === undefined) return { rule: "user", index: id, stack: null };
  if ((env.scale === "ordinal" || env.scale === "interval") && env.levels?.length) return { rule: "ordinal", index: levelIndex(labels, env.levels), stack: null };
  const calendar = calendarIndex(labels, env.weekStart);
  if (calendar) return { rule: "calendar", index: calendar, stack: null };
  const ds = s.datasets;
  if (STACKED.includes(type)) return { rule: "stack", index: valueIndex(labelKeys(ds, "")), stack: stackIndex(ds) };
  if (ds.length >= 2) return { rule: "grouped", index: valueIndex(labelKeys(ds, groupBy)), stack: null };
  return { rule: isDiverging(ds[0].values) ? "diverging" : "nominal", index: valueIndex(labelKeys(ds, "")), stack: null };
}
// [SPECIFICATION] plans/phase-9.md#needsTurn
export function needsTurn(labels: (string | null)[], width: number, font: number): boolean {
  if (width <= 0 || labels.length === 0) return false;
  const widest = Math.max(...labels.map((l) => textUnits(String(l ?? "")) * LABEL_EM * font));
  return widest > (width / labels.length) * TURN_FILL;
}
// [SPECIFICATION] plans/phase-9.md#othersName
export function othersName(k: number, more: boolean): string {
  return `${OTHERS_PREFIX}(외 ${k}개${more ? " 이상" : ""})`;
}
// [SPECIFICATION] plans/phase-9.md#topOthers
export function topOthers(labels: string[], datasets: Dataset[], n: number, ops: ("sum" | "min" | "max" | null)[], rest: (number | null)[] | null): { labels: string[]; datasets: Dataset[]; cut: number; merged: boolean } {
  if (labels.length <= n) return { labels, datasets, cut: 0, merged: true };
  const rank = valueIndex(labelKeys(datasets, ""));
  const keep = rank.slice(0, n).sort((a, b) => a - b);
  const drop = rank.slice(n).sort((a, b) => a - b);
  const cut = drop.length;
  const kept = keep.map((i) => labels[i]);
  if (ops.some((op) => op === null)) {
    return { labels: kept, datasets: datasets.map((d) => ({ ...d, values: keep.map((i) => d.values[i]) })), cut, merged: false };
  }
  const merge = (d: Dataset, k: number): number | null => {
    const nums = [...drop.map((i) => d.values[i]), rest?.[k] ?? null].filter((v) => v !== null && Number.isFinite(Number(v))).map(Number);
    if (!nums.length) return null;
    const op = ops[k];
    return op === "sum" ? nums.reduce((a, b) => a + b, 0) : op === "min" ? Math.min(...nums) : Math.max(...nums);
  };
  return {
    labels: [...kept, othersName(cut, rest !== null)],
    datasets: datasets.map((d, k) => ({ ...d, values: [...keep.map((i) => d.values[i]), merge(d, k)] })),
    cut,
    merged: true,
  };
}
// [SPECIFICATION] plans/phase-9.md#spaghettiPick
export function spaghettiPick(datasets: Dataset[], keep: number): number[] {
  const size = stackIndex(datasets);
  return size.slice(0, keep).sort((a, b) => a - b);
}
// [SPECIFICATION] plans/phase-9.md#barDomain
export function barDomain(values: (number | string | null)[]): number {
  return Math.min(0, ...values.filter((v) => v !== null && Number.isFinite(Number(v))).map(Number));
}
// [SPECIFICATION] plans/phase-9.md#lineDomain
export function lineDomain(values: (number | string | null)[], pad: number): { domain: [number, number]; zero: boolean } | null {
  const nums = values.filter((v) => v !== null && Number.isFinite(Number(v))).map(Number);
  if (nums.length === 0) return null;
  const lo = Math.min(...nums), hi = Math.max(...nums), span = hi - lo;
  const margin = span === 0 ? Math.abs(lo) * pad || 1 : span * pad;
  let dlo = lo - margin, dhi = hi + margin;
  if (lo >= 0 && hi > 0 && dlo < 0) dlo = 0;
  if (hi <= 0 && lo < 0 && dhi > 0) dhi = 0;
  return { domain: [dlo, dhi], zero: dlo <= 0 && dhi >= 0 };
}
const LOG_HINT_MIN_N = 3;  // 로그 축 제안을 보려면 유한한 값이 이 개수 이상이어야 한다
// [SPECIFICATION] plans/phase-9.md#logHint
export function logHint(values: (number | string | null)[], ratio: number, skew: number): AxisScale | null {
  const nums = values.filter((v) => v !== null && Number.isFinite(Number(v))).map(Number);
  if (nums.length < LOG_HINT_MIN_N) return null;
  const sorted = [...nums].sort((a, b) => a - b);
  const half = Math.floor(sorted.length / 2);
  const med = sorted.length % 2 ? sorted[half] : (sorted[half - 1] + sorted[half]) / 2;
  const max = sorted[sorted.length - 1];
  const mean = nums.reduce((a, b) => a + b, 0) / nums.length;
  const m2 = nums.reduce((a, b) => a + (b - mean) ** 2, 0) / nums.length;
  const m3 = nums.reduce((a, b) => a + (b - mean) ** 3, 0) / nums.length;
  const wide = med > 0 && max / med >= ratio;
  const skewed = m2 !== 0 && m3 / m2 ** 1.5 > skew;
  if (!wide && !skewed) return null;
  return nums.every((n) => n > 0) ? "log" : "symlog";
}
// [SPECIFICATION] plans/phase-9.md#toSymlog
export function toSymlog(v: number): number {
  return Math.sign(v) * Math.log10(1 + Math.abs(v) / SYMLOG_C);
}
// [SPECIFICATION] plans/phase-9.md#fromSymlog
export function fromSymlog(u: number): number {
  return Math.sign(u) * (10 ** Math.abs(u) - 1) * SYMLOG_C;
}
// [SPECIFICATION] plans/phase-9.md#percentDomain
export function percentDomain(values: (number | string | null)[]): [number, number] | null {
  const nums = values.filter((v) => v !== null && Number.isFinite(Number(v))).map(Number);
  if (nums.length === 0) return null;
  return nums.every((n) => n >= 0 && n <= 1) ? [0, 1] : null;
}
// [SPECIFICATION] plans/phase-9.md#lttb
export function lttb(ys: (number | string | null)[], threshold: number): number[] {
  const idx: number[] = [];
  const vals: number[] = [];
  ys.forEach((v, i) => {
    if (v !== null && Number.isFinite(Number(v))) { idx.push(i); vals.push(Number(v)); }
  });
  const m = idx.length;
  if (m <= threshold || threshold < 3) return idx;
  const every = (m - 2) / (threshold - 2);
  const picked = [0];
  let a = 0;
  for (let j = 0; j < threshold - 2; j++) {
    const lo = Math.floor(j * every) + 1, hi = Math.floor((j + 1) * every) + 1;
    const cLo = Math.min(hi, m - 1), cHi = Math.min(Math.floor((j + 2) * every) + 1, m);
    let cx = 0, cy = 0;
    for (let k = cLo; k < cHi; k++) { cx += idx[k]; cy += vals[k]; }
    cx /= cHi - cLo; cy /= cHi - cLo;
    let best = lo, bestArea = -1;
    for (let k = lo; k < hi; k++) {
      const area = Math.abs((idx[a] - cx) * (vals[k] - vals[a]) - (idx[a] - idx[k]) * (cy - vals[a])) / 2;
      if (area > bestArea) { bestArea = area; best = k; }
    }
    picked.push(best);
    a = best;
  }
  picked.push(m - 1);
  return picked.map((k) => idx[k]);
}
// [SPECIFICATION] plans/phase-9.md#thinRows
export function thinRows<T extends Record<string, unknown>>(rows: T[], keys: string[], threshold: number | null): T[] {
  if (threshold === null || rows.length <= threshold) return rows;
  const picked = new Set<number>();
  for (const k of keys) {
    for (const i of lttb(rows.map((r) => r[k] as number | string | null), threshold)) picked.add(i);
  }
  return [...picked].sort((a, b) => a - b).map((i) => rows[i]);
}
const AUTO_UNITS: Unit[] = ["day", "week", "month", "year"];  // 자동 묶기 단위, 작은 것부터
// [SPECIFICATION] plans/phase-9.md#autoUnit
export function autoUnit(counts: Partial<Record<Unit, number>>, width: number, px: number): Unit {
  if (width <= 0) return "day";
  const limit = Math.floor(width / px);
  const present = AUTO_UNITS.filter((u) => counts[u] !== undefined);
  const fit = present.find((u) => (counts[u] as number) <= limit);
  if (fit !== undefined) return fit;
  const largest = present[present.length - 1];
  if (largest === undefined) throw new Error("자동 묶기 단위가 없어요.");
  return largest;
}
// [SPECIFICATION] plans/phase-9.md#scatterTier
export function scatterTier(total: number, p: Record<string, number | string | string[]> | null): Tier | null {
  if (p === null) return null;
  if (total < (p.faint as number)) return { mode: "dots", alpha: p.alpha as number, r: DOT_R };
  if (total <= (p.hex as number)) return { mode: "faint", alpha: p.faint_alpha as number, r: FAINT_R };
  return { mode: "hex", alpha: p.faint_alpha as number, r: FAINT_R };
}
const HEX_ANGLES = [90, 150, 210, 270, 330, 30];  // 꼭짓점 각도(도), 이 차례대로
const DEG_TO_RAD = Math.PI / 180;  // 도를 라디안으로
// [SPECIFICATION] plans/phase-9.md#hexVertices
export function hexVertices(cu: number, cv: number, w: number): [number, number][] {
  const r = w / Math.sqrt(3);
  return HEX_ANGLES.map((deg): [number, number] => [cu + r * Math.cos(deg * DEG_TO_RAD), cv + r * Math.sin(deg * DEG_TO_RAD)]);
}
const KO_UNITS: [number, string][] = [[1e12, "조"], [1e8, "억"], [1e4, "만"]];  // 한국어 단위, 큰 것부터
const EN_UNITS: [number, string][] = [[1e9, "B"], [1e6, "M"], [1e3, "K"]];  // 영어식 단위, 큰 것부터
const SHORT_DIGITS = 1;  // 줄인 값의 소수 자리
// [SPECIFICATION] plans/phase-9.md#shortNum
export function shortNum(v: unknown, style: "ko" | "en" | null): string {
  if (style === null || typeof v !== "number" || !Number.isFinite(v)) return shownum(v);
  const hit = (style === "ko" ? KO_UNITS : EN_UNITS).find(([min]) => Math.abs(v) >= min);
  if (hit === undefined) return shownum(v);
  const [min, unit] = hit;
  return `${(v / min).toFixed(SHORT_DIGITS).replace(/\.0$/, "")}${unit}`;
}
// [SPECIFICATION] plans/phase-9.md#refValues
export function refValues(values: (number | string | null)[]): { mean: number; median: number } | null {
  const nums = values.filter((v) => v !== null && Number.isFinite(Number(v))).map(Number);
  if (nums.length === 0) return null;
  const sorted = [...nums].sort((a, b) => a - b);
  const half = Math.floor(sorted.length / 2);
  const median = sorted.length % 2 ? sorted[half] : (sorted[half - 1] + sorted[half]) / 2;
  const mean = nums.reduce((a, b) => a + b, 0) / nums.length;
  return { mean, median };
}
// [SPECIFICATION] plans/phase-9.md#semanticTone
export function semanticTone(name: string | null, words: SemanticWords): "good" | "bad" | "none" | null {
  if (name === null || tailKind(name, words) !== null) return "none";
  if (words.bad.some((w) => name.includes(w))) return "bad";
  if (words.good.some((w) => name.includes(w))) return "good";
  return null;
}
let WORDS: SemanticWords | null = null;  // 상수 dash.viz.words를 받기 전에는 null
// [SPECIFICATION] plans/phase-9.md#setWords
export function setWords(words: SemanticWords): void {
  WORDS = words;
}
// [SPECIFICATION] plans/phase-9.md#currentWords
export function currentWords(): SemanticWords | null {
  return WORDS;
}
// [SPECIFICATION] plans/phase-9.md#y1Domain
export function y1Domain(values: (number | string | null)[], user: { min?: number; max?: number } | undefined): [number, number] | null {
  if (percentDomain(values) === null) return null;
  return [user?.min != null ? user.min / 100 : 0, user?.max != null ? user.max / 100 : 1];
}
// [SPECIFICATION] plans/phase-9.md#clipBox
export function clipBox(groups: { q1: number; q3: number; p01?: number | null; p99?: number | null }[]): { lo: number; hi: number } | null {
  const lows = groups.map((g) => g.p01).filter((v): v is number => v != null && Number.isFinite(v));
  const highs = groups.map((g) => g.p99).filter((v): v is number => v != null && Number.isFinite(v));
  if (lows.length === 0 && highs.length === 0) return null;
  return { lo: Math.min(...lows, ...groups.map((g) => g.q1)), hi: Math.max(...highs, ...groups.map((g) => g.q3)) };
}
// [SPECIFICATION] plans/phase-9.md#autoEnv
export function autoEnv(card: SCard, width: number, table: AutoTable, weekStart: "mon" | "sun", words: SemanticWords): AutoEnv {
  const d = card.data ?? {};
  const body = card.source?.body ?? {};
  const rest = Array.isArray(d.values?.rest) ? d.values.rest : null;
  return {
    width, table, aggs: (body.series ?? []).map((s: { agg?: string }) => s.agg), rest, truncated: rest !== null,
    xKind: d.x_kind === "num" || d.x_kind === "date" || d.x_kind === "text" ? d.x_kind : undefined,
    scale: d.x_scale, levels: d.x_levels, order: body.order ?? "x", weekStart, words,
  };
}
// [SPECIFICATION] plans/phase-9.md#seriesTable
export function seriesTable(x: string, s: Series): ChartTable {
  return {
    columns: [x, ...s.datasets.map((d) => d.label)],
    rows: s.labels.map((l, j) => ({ ...Object.fromEntries(s.datasets.map((d) => [d.label, d.values[j] ?? null])), [x]: l })),
  };
}
const CSV_FORMULA_START = ["=", "+", "-", "@", "\t", "\r"];  // 수식으로 읽힐 수 있는 첫 글자(앞 빈칸을 건너뛰고 본다)
// [SPECIFICATION] plans/phase-9.md#csvText
export function csvText(t: ChartTable): string {
  const cell = (v: string | number | null | undefined): string => {
    if (v === null || v === undefined) return "";
    if (typeof v === "number") return String(v);
    const guarded = CSV_FORMULA_START.includes(v.replace(/^ +/, "").charAt(0)) ? `'${v}` : v;
    return /[,"\r\n]/.test(guarded) ? `"${guarded.replace(/"/g, '""')}"` : guarded;
  };
  const lines: (string | number | null | undefined)[][] = [t.columns, ...t.rows.map((row) => t.columns.map((c) => row[c]))];
  return lines.map((line) => line.map(cell).join(",")).join("\r\n");
}
// [SPECIFICATION] plans/phase-9.md#autoSeries
export function autoSeries(card: SCard, s: Series, env: AutoEnv): { card: SCard & { auto: AutoHints }; s: Series; notes: string[] } {
  const view = card.view;
  const p = autoParams(view.auto, env.table);
  const type = view.type || "";
  const cat = !s.time && env.xKind === "text";
  const ranged = (r: { min?: number | null; max?: number | null } | undefined) => r?.min != null || r?.max != null;
  const aggOf = (k: number) => env.aggs[Math.floor((k * env.aggs.length) / s.datasets.length)];
  const notes: string[] = [];
  let changed = false;
  // 1. 정렬
  const o: { rule: OrderRule; index: number[]; stack: number[] | null } = p.sort
    ? axisOrder(s, env, type, p.sort.group_by as string)
    : { rule: s.time || env.xKind === "date" ? "time" : env.xKind === "num" ? "number" : "user", index: s.labels.map((_, i) => i), stack: null };
  const perm = o.stack ?? s.datasets.map((_, k) => k);  // 지금 계열 차례 → 원래 계열 번호
  if (o.index.some((v, i) => v !== i) || perm.some((v, i) => v !== i)) changed = true;
  let labels = o.index.map((i) => s.labels[i]) as string[];
  let ds: Dataset[] = perm.map((k) => ({ ...s.datasets[k], values: o.index.map((i) => s.datasets[k].values[i]) }));
  // 2. 가로 막대
  let shown = type as ChartType;  // view.type은 저장된 글자라 넓은 string
  if (p.horizontal && cat && type === "" && ds.every((d) => d.type !== "line")
    && (labels.length >= (p.horizontal.from as number) || needsTurn(labels, env.width, LABEL_FONT))) {
    shown = "hbar";
    changed = true;
  }
  // 3. Top-N·파이
  if (cat && !view.groups?.keep?.length && view.all_data !== true) {
    let n: number | null = null;
    if (ROUND_TYPES.includes(type)) {
      if (p.round && labels.length > (p.round.max as number)) n = p.round.keep as number;
    } else if (p.topn) {
      n = (HBARS.includes(shown) ? p.topn.h : p.topn.v) as number;
    }
    if (n !== null && labels.length > n) {
      const ops = perm.map((k) => OTHERS_MERGE[aggOf(k) ?? ""] ?? null);
      const rest = env.truncated && env.rest ? perm.map((k) => env.rest?.[k] ?? null) : null;
      const t = topOthers(labels, ds, n, ops, rest);
      labels = t.labels;
      ds = t.datasets;
      changed = true;
      notes.push(t.merged ? `그룹이 많아 큰 ${n}개와 '기타'로 그렸어요.` : `그룹이 많아 큰 ${n}개만 그렸어요. 나머지 ${t.cut}개는 값을 합칠 수 없는 집계라 뺐어요.`);
    }
  }
  // 4. 핀
  if (p.pin && cat) {
    const pin = pinTail(labels, labels.map((_, i) => i), env.words);
    if (pin.some((v, i) => v !== i)) {
      labels = pin.map((i) => labels[i]);
      ds = ds.map((d) => ({ ...d, values: pin.map((i) => d.values[i]) }));
      changed = true;
    }
  }
  // 5. 스파게티
  const lineChart = ["line", "polyline", "step", "area"].includes(type) || (type === "" && ds.every((d) => d.type === "line"));
  const areaLike = ["area", "sarea", "sarea100"].includes(type);
  let gray: number[] = [];
  let small = false;
  if (p.spaghetti && lineChart && ds.length > (p.spaghetti.max as number)) {
    const keep = spaghettiPick(ds, p.spaghetti.keep as number);
    if (p.spaghetti.mode === "gray") {
      gray = ds.map((_, k) => k).filter((k) => !keep.includes(k));
      notes.push(`선이 많아 큰 ${keep.length}개만 색으로 그렸어요.`);
    } else if (p.spaghetti.mode === "small") {
      small = true;
      notes.push("선이 많아 계열마다 따로 그렸어요.");
    }
  }
  // 6. 축 범위
  const left = (type === "combo" ? ds.slice(0, 1) : ds.filter((d) => d.axis !== "right")).flatMap((d) => d.values);
  let barLo: number | null = null;
  let yDomain: [number, number] | null = null;
  let notZero = false;
  if (!ranged(view.ranges?.y) && !view.scale) {
    if (!lineChart && !areaLike && p.zero) barLo = barDomain(left);
    if (lineChart && !areaLike && !STACKED.includes(type) && p.yzoom) {
      const ld = lineDomain(left, p.yzoom.pad as number);
      if (ld) {
        yDomain = ld.domain;
        notZero = !ld.zero;
      }
    }
  }
  // 7. 로그 제안
  if (p.log_hint && !view.scale) {
    const k = logHint(left, p.log_hint.ratio as number, p.log_hint.skew as number);
    if (k) notes.push(`값 차이가 커요. 도구 칸 [값 축]에서 ${k === "log" ? "로그" : "SymLog"} 축을 고르면 작은 값도 보여요.`);
  }
  // 8. 보조 축 동기화
  let y1Pct: [number, number] | null = null;
  if (p.sync) {
    const right = type === "combo" ? ds.slice(1) : type === "" ? ds.filter((d) => d.axis === "right") : [];
    if (right.length) y1Pct = y1Domain(right.flatMap((d) => d.values), view.ranges?.y1);
  }
  // 9. 참조선
  const refs: AutoHints["refs"] = [];
  if (p.refs) {
    const lines = p.refs.lines as string[];
    const rv = ds.length ? refValues(ds[0].values) : null;
    if (rv && lines.includes("mean")) refs.push({ label: "평균", value: rv.mean, axis: "y" });
    if (rv && lines.includes("median")) refs.push({ label: "중앙값", value: rv.median, axis: "y" });
    if (lines.includes("p80") && type === "pareto") refs.push({ label: "80%", value: 80, axis: "y1" });
  }
  // 10. 리샘플링
  let thin: number | null = null;
  if (p.resample && p.resample.mode === "lttb" && view.all_data !== true
    && (s.time || env.xKind === "date" || env.xKind === "num") && lineChart && env.width > 0) {
    const thr = Math.floor(env.width / (p.resample.px as number));
    if (labels.length > thr) thin = thr;
  }
  // 11·12
  const hints: AutoHints = {
    rule: o.rule, zeroLine: o.rule === "diverging", gray, small, clockwise: ROUND_TYPES.includes(type) && !!p.round,
    yDomain, notZero, barLo, y1Pct, refs, num: p.num ? (p.num.style as "ko" | "en") : null, thin, semantic: !!p.semantic,
  };
  const colors = card.colors ?? { series: {}, parts: {}, order: s.datasets.map((d) => d.label), parts_order: s.labels.map(String) };
  return {
    card: { ...card, view: { ...view, type: shown }, colors, auto: hints },
    s: changed ? { ...s, labels, datasets: ds } : s,
    notes,
  };
}
