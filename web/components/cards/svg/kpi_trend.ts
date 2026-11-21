// (B10b) KPI 카드(그림 1)와 KPI 큰 창(그림 2)의 추이 계산(화면 요소 없음): 직전 기간 + 이번 기간 나누기, 큰 창 눈금, 그리기 좌표, 비교 한 줄.
import type { Said } from "@/components/shell/ShellProvider";
import type { SeriesKit } from "../chart/series";
import type { Card, CompareMode, Ctx, PrevSpan, Unit } from "@/lib/types";
import { ownRange, roundText, smoothPath, sparkPoints, unitLabel } from "@/lib/calc";
import { pickColor } from "@/lib/series";
import { cardDays } from "./__pure__";

/** 큰 창 눈금 */
export type Tick = "hour" | "day" | "week" | "month" | "quarter";
/** 추이의 점 하나: key는 날짜(YYYY-MM-DD)·단위 시작(YYYY-MM-DD)·시각(YYYY-MM-DDTHH) */
export type TrendPt = { key: string; v: number | null };
/** 직전 기간 점들(회색)과 이번 기간 점들(계열 색). prev가 비면 한 색 */
export type Trend = { prev: TrendPt[]; now: TrendPt[] };
/** 보는 기간(now)과 직전 기간(prev, 없으면 null) */
export type Spans = { now: { start: string; end: string }; prev: PrevSpan | null };
/** 서버 /api/dash/card/hours의 답 */
export type HourValues = { labels: string[]; values: (number | null)[]; reason: string };
/** 카드 추이선의 SVG 조각: 선·바탕 경로(없으면 ""), 끝 점 "x,y", 가로 점선 눈금의 y들 */
export type SparkParts = { prev: string; prevArea: string; now: string; nowArea: string; end: string; grid: number[] };
/** 큰 창 그림(Recharts)의 한 줄 */
export type ZoomRow = { key: string; label: string; prev: number | null; now: number | null };

/** 기간 단위 요소가 고른 단위(없으면 none) → [자동 눈금, 한 단계 큰 눈금] (사용자 결정 + 메인 세션 결정: 없음·일 → 일) */
export const ZOOM_TICKS: Record<Unit | "none", [Tick, Tick]> = {
  none: ["day", "week"], day: ["day", "week"], week: ["hour", "day"], month: ["day", "week"], quarter: ["week", "month"], year: ["month", "quarter"],
};
/** 눈금 단추 글자 */
export const TICK_NAMES: Record<Tick, string> = { hour: "시간", day: "일", week: "주", month: "월", quarter: "분기" };
export const GRID_LINES = 3;        // 카드 추이선의 가로 점선 눈금 수(그림 1)
export const GRID_DASH = "2 4";     // 가로 점선 눈금 모양(목표 점선 "4 3"과 다르게)
export const PREV_FILL = 0.08;      // 직전 기간 옅은 회색 바탕 진하기
export const NOW_FILL = 0.18;       // 이번 기간 색 바탕 진하기
export const UP = "▲", DOWN = "▼";  // 비교 한 줄의 방향 표시

/** [SPECIFICATION] plans/dash-blocks.md#trendSpans
 *  대시보드 기간(range·prev)이 있으면 그것, 없으면 카드 자신의 기간 줄(ownRange), 둘 다 없으면 null. */
export function trendSpans(card: Card, range: Ctx["range"], prev: Ctx["prev"], mode: CompareMode): Spans | null {
  if (range) return { now: range, prev: prev ?? null };
  const own = ownRange(card, mode);
  return own ? { now: own.range, prev: own.prev } : null;
}

/** [SPECIFICATION] plans/dash-blocks.md#dayTrend
 *  일 값(cardDays)을 직전·이번 기간 점들로 나눈다(spans가 null이면 읽어 온 전체가 now). */
export function dayTrend(card: Card, spans: Spans | null, merge: string[]): Trend {
  const pts = (span?: { start: string; end: string } | null): TrendPt[] =>
    cardDays(card, 0, span ?? undefined, merge).map(d => ({ key: d.day, v: d.v }));
  if (spans === null) return { prev: [], now: pts() };
  return { prev: spans.prev ? pts(spans.prev) : [], now: pts(spans.now) };
}

/** [SPECIFICATION] plans/dash-blocks.md#unitTrend
 *  주·월·분기 눈금 값(seriesKit.byUnit)을 직전·이번 기간 점들로 나눈다(두 기간에 걸친 칸은 이번 기간). */
export function unitTrend(card: Card, spans: Spans | null, tick: "week" | "month" | "quarter", byUnit: SeriesKit["byUnit"]): Trend {
  if (!card.data?.by_unit?.day) return { prev: [], now: [] };
  const pts = (span?: { start: string; end: string } | null): TrendPt[] => {
    const s = byUnit(card.data.by_unit as Parameters<typeof byUnit>[0], tick, span ?? undefined);
    return s.keys.map((key, i) => ({ key, v: typeof s.datasets[0]?.values[i] === "number" ? s.datasets[0].values[i] as number : null }));
  };
  if (spans === null) return { prev: [], now: pts() };
  const now = pts(spans.now);
  const prev = spans.prev ? pts(spans.prev).filter(p => !now.length || p.key < now[0].key) : [];
  return { prev, now };
}

/** [SPECIFICATION] plans/dash-blocks.md#hourTrend
 *  서버가 준 시간별 값을 날짜로 직전·이번 기간 점들로 나눈다(spans가 null이면 모두 now). */
export function hourTrend(hours: HourValues, spans: Spans | null): Trend {
  const all: TrendPt[] = hours.labels.map((key, i) => ({ key, v: hours.values[i] ?? null }));
  if (spans === null) return { prev: [], now: all };
  const prevSpan = spans.prev;
  const inside = (p: TrendPt, span: { start: string; end: string }): boolean => {
    const d = p.key.slice(0, 10);
    return span.start <= d && d <= span.end;
  };
  return {
    prev: prevSpan ? all.filter(p => inside(p, prevSpan)) : [],
    now: all.filter(p => inside(p, spans.now)),
  };
}

/** [SPECIFICATION] plans/dash-blocks.md#sparkParts
 *  추이를 w×h 칸의 선·바탕 경로·끝 점·점선 눈금으로(값이 2개 미만이면 null). */
export function sparkParts(t: Trend, w: number, h: number): SparkParts | null {
  const values = [...t.prev, ...t.now].map(p => p.v);
  if (values.filter(v => v != null).length < 2) return null;
  const pts = sparkPoints(values, w, h).split(" ");
  const k = t.prev.filter(p => p.v != null).length;
  const prevPts = pts.slice(0, k);
  const nowPts = pts.length === k ? [] : k > 0 ? pts.slice(k - 1) : pts;  // 이번 선은 직전 끝 점에서 시작
  const x = (p: string): number => Number(p.split(",")[0]);
  const line = (p: string[]): string => p.length >= 2 ? smoothPath(p.join(" ")) : "";
  const area = (p: string[]): string => p.length >= 2 ? `${line(p)} L${x(p[p.length - 1])},${h} L${x(p[0])},${h} Z` : "";
  return {
    prev: line(prevPts), prevArea: area(prevPts), now: line(nowPts), nowArea: area(nowPts),
    end: pts[pts.length - 1],
    grid: Array.from({ length: GRID_LINES }, (_, i) => h * (i + 1) / (GRID_LINES + 1)),
  };
}

/** [SPECIFICATION] plans/dash-blocks.md#tickLabel
 *  눈금 key를 가로축 글자로(시간 "6/1 13시", 일·주 "6/1", 월·분기 unitLabel). */
export function tickLabel(key: string, tick: Tick): string {
  if (tick === "hour") return `${+key.slice(5, 7)}/${+key.slice(8, 10)} ${key.slice(11, 13)}시`;
  if (tick === "day" || tick === "week") return `${+key.slice(5, 7)}/${+key.slice(8, 10)}`;
  return unitLabel(key, tick);
}

/** [SPECIFICATION] plans/dash-blocks.md#zoomRows
 *  추이를 큰 창 그림의 줄들로(직전 끝 점에서 이번 기간 선이 이어진다). */
export function zoomRows(t: Trend, tick: Tick): ZoomRow[] {
  const prev: ZoomRow[] = t.prev.map(p => ({ key: p.key, label: tickLabel(p.key, tick), prev: p.v, now: null }));
  const now: ZoomRow[] = t.now.map(p => ({ key: p.key, label: tickLabel(p.key, tick), prev: null, now: p.v }));
  if (prev.length && now.length) prev[prev.length - 1].now = prev[prev.length - 1].prev;  // 직전 끝 점에서 이번 선이 이어지게
  return [...prev, ...now];
}

/** [SPECIFICATION] plans/dash-blocks.md#kpiDelta
 *  비교 결과를 "▼ 90.8% · 전월 대비" 한 줄로(변화율이 없거나 같으면 서버 문장 그대로). */
export function kpiDelta(said: Said, label: string, fmt: string): string {
  const pct = said.pct as number | null | undefined, dir = said.direction;
  if (pct == null || dir === "flat" || (dir !== "up" && dir !== "down")) return said.text;
  let amount: string;
  if (fmt === "percent") amount = String(said.delta_text ?? "").replace(/^[+-]/, "");
  else {
    const a = Math.abs(pct), s = roundText(a, a < 1 ? 2 : 1) + "%";  // 서버 compare.percent_text와 같은 규칙
    amount = s === "0%" && pct !== 0 ? "0.01% 미만" : s;
  }
  return `${dir === "up" ? UP : DOWN} ${amount}` + (label ? ` · ${label} 대비` : "");
}

/** [SPECIFICATION] plans/dash-blocks.md#kpiColor
 *  KPI 첫 계열의 색(카드 지정 색 → 표시색 단계, 지금 Kpi.tsx 18줄과 같은 규칙). */
export function kpiColor(card: Card, colors: { palette?: string[]; series: string[] }): string {
  return pickColor(card, "series", card.data?.by_unit?.day?.series[0]?.name ?? (card.data?.values?.names as string[] | undefined)?.[0] ?? card.title, colors, 0);
}
