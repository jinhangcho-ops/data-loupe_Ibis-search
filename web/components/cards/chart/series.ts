// 카드의 값 → 그릴 모양: 옛 static/core/dash.js의 함수들(본문 그대로, 타입만). 색 고르기·기간 비교 값·계열 이름은 lib/series.ts 한 벌을 쓴다.
// 옛 코드가 전역 SERVER에서 읽던 집계 이름(agg_names)·다시 묶을 수 있는 집계(merge)·차트 종류(dash.types·time_types)는 seriesKit(서버 상수)로 받는다.
import type { AutoHints } from "@/lib/autoviz";
import { anaView, BUCKET, isPoints, okLabel, pointGroups, rollup } from "@/lib/calc";
import type { Card, ChartType, Constants, DaySeries, MoreValues, UnitSection } from "@/lib/types";
import { FIRST_ONLY, PAIR_ONLY, ROUND_TYPES } from "./shapes";

export type Dataset = { label: string; values: (number | string | null)[]; type?: "bar" | "line"; dash?: string; axis?: string };
/** 그릴 모양: 라벨, 계열, 날짜 X축인지, 종류를 바꿀 수 있는지 */
export type Series = { labels: string[]; datasets: Dataset[]; time: boolean; chartable: boolean; keys?: string[] };
export type Range = { start?: string; end?: string } | null | undefined;
/** 화면에서만 붙이는 것: range(보는 기간), norm(탐색 차트처럼 이미 그릴 모양인 값) */
export type SCard = Card & { range?: Range; norm?: Partial<Series> & Pick<Series, "labels" | "datasets">; auto?: AutoHints };
/** 색 고르기(lib/series.pickColor)에 쓰는 색: 대시보드 팔레트(없을 수 있음)와 화면 모드의 계열 색 */
export type ThemeColors = { palette?: string[]; series: string[] };
type ByUnit = { day: { labels: string[]; series: DaySeries[] } } & Record<string, unknown>;
type Server = Pick<Constants, "agg_names" | "merge"> & { dash: Pick<Constants["dash"], "types" | "time_types"> };

/** 색 하나(주 색)로 그리는 새 카드 */
const ONE_COLOR: string[] = ["histogram", "boxplot", "heatmap", "calheat", "cohort", "candle", "bullet"];
/** 날짜 X축 시간 단위의 이름(축 제목) */
export const UNIT_NAMES: Record<string, string> = { day: "일", week: "주", month: "월" };

/** 서버 상수로 묶은 계열·비교 함수들(옛 dash.js의 전역 함수) */
export function seriesKit(server: Server) {
  const aggName = (id: string) => server.agg_names?.[id] ?? id;
  const merges = (agg: string) => server.merge.includes(agg);  // 아니면 일 값을 주·월·연으로 다시 묶을 수 없다(서버가 단위마다 따로 구한다)

  // 시간별 값(data.by_unit)을 unit으로. 서버가 그 단위로 따로 구한 계열은 그 값을 쓰고, 나머지는 일 값을 다시 묶는다.
  // range({start, end}, 없으면 열린 쪽)를 주면 그 기간의 칸만 남긴다(월·연 칸은 그 기간과 겹치는 것).
  function byUnit(by: ByUnit, unit: string, range?: Range) {
    const day = by.day, sec = unit === "day" ? undefined : by[unit] as UnitSection | undefined;
    const server = new Map((sec?.series ?? []).map(s => [s.name, new Map(sec!.labels.map((l, i) => [l, s.values[i]]))]));
    const maps = day.series.map(s => server.get(s.name) ?? (merges(s.agg) ? rollup(s, day.labels, unit) : unit === "day" ? new Map(day.labels.map((l, i) => [l, s.values?.[i] ?? null])) : new Map<string, number | null>()));
    const from = range?.start ? BUCKET[unit](range.start) : "", to = range?.end ?? "9999";
    const labels = [...new Set(maps.flatMap(m => [...m.keys()]))].sort().filter(l => l >= from && l.slice(0, 10) <= to);
    return { keys: labels, labels: labels.map(l => unit === "month" ? l.slice(0, 7) : unit === "year" ? l.slice(0, 4) : l),
             datasets: day.series.map((s, i) => ({ label: s.name, values: labels.map(l => (maps[i] as Map<string, number | string | null>).get(l) ?? null) })) };
  }
  // 카드의 값을 그릴 모양. 값이 아직 없으면 null.
  function cardSeries(card: SCard): Series | null {
    if (card.norm) return { time: false, chartable: true, ...card.norm };  // 탐색 탭의 지금 차트처럼 이미 이 모양인 것
    if (isPoints(card)) return { labels: [], datasets: pointGroups(card.data.points!).map(label => ({ label, values: [] })), time: false, chartable: true };
    const d = card.data ?? {}, view = card.view ?? {};
    if (d.by_unit?.day) return { ...byUnit(d.by_unit as ByUnit, view.unit || "day", card.range), time: true, chartable: true };
    if (card.kind === "analysis" && d.values) {
      const v = d.values as { labels: string[]; values: (number | null)[]; agg: string }, name = anaView(card);
      if (name === "groups" || name === "trend") return { labels: v.labels, datasets: [{ label: aggName(v.agg), values: v.values }], time: name === "trend", chartable: true };
      return { labels: [], datasets: [], time: false, chartable: false };
    }
    if (d.values?.series) return { labels: d.values.labels ?? [], datasets: d.values.series.map((s, i) => ({ label: s.name, values: s.values, ...(view.styles?.[i] ?? {}) })), time: false, chartable: true };
    return null;
  }
  // 이 종류로 바꿀 수 없는 이유(바꿀 수 있으면 빈 글자). 버튼은 숨기지 않고 누르면 이 이유를 보인다.
  function typeReason(card: SCard, type: Exclude<ChartType, "">) {
    const s = cardSeries(card), names = server.dash.types;
    if (!s) return "아직 값이 없어서 종류를 바꿀 수 없어요. 연결한 뒤 [다시 구하기]를 누르세요.";
    if (!s.chartable) return "이 분석 그림은 종류를 바꿀 수 없어요. 묶음끼리 비교와 날짜에 따른 변화만 바꿀 수 있어요.";
    if (type === "scatter" || type === "bubble") {  // X와 값이 숫자 컬럼일 때만(버블은 [크기]도)
      const d = card.data ?? {}, body = card.source?.body ?? {}, y = body.series?.[0]?.y ?? body.y;
      if (d.x_kind !== "num" || !(d.num_cols ?? []).includes(y as string)) return "산점도·버블은 X가 숫자 컬럼이고 값이 숫자 컬럼일 때만 쓸 수 있어요.";
      return type === "bubble" && !card.view.points?.size ? "버블은 [크기]에 숫자 컬럼을 먼저 고르세요." : "";
    }
    if (s.time && !server.dash.time_types.includes(type)) return `날짜 X축 그림에는 ${server.dash.time_types.map(t => names[t as keyof typeof names]).join("·")}만 쓸 수 있어요.`;
    if (type === "stacked" && s.datasets.length < 2) return "누적 막대는 계열이 2개 이상일 때만 쓸 수 있어요.";
    if (["stack100", "hstack", "hstack100", "sarea100", "combo", "range", "butterfly"].includes(type) && s.datasets.length < 2) return `${names[type] ?? type}은(는) 계열이 2개 이상일 때만 쓸 수 있어요.`;
    return "";
  }
  // 고를 수는 있지만 계열 일부만 그리는 종류의 안내(빈 글자면 모두 그린다): 첫 계열만(둥근 차트는 [겹친 고리]를 켜면 계열마다 링이라 안내 없음), 나비형·범위 막대는 앞 2계열만
  function typeNote(card: SCard, type: Exclude<ChartType, "">) {
    const s = cardSeries(card), round = ROUND_TYPES.includes(type), name = server.dash.types[type] ?? type;
    if (s && PAIR_ONLY.includes(type) && s.datasets.length > 2) return `${name}은(는) 앞 2계열(${s.datasets[0].label}, ${s.datasets[1].label})만 그려요. 나머지 ${s.datasets.length - 2}개는 빼요.`;
    if (!s || s.datasets.length < 2 || !(round || FIRST_ONLY.includes(type)) || (round && card.view.rings)) return "";
    return `${name}은(는) 첫 계열(${s.datasets[0].label})만 그려요. 나머지 ${s.datasets.length - 1}개는 빼요.${round ? " 모두 보려면 [겹친 고리]를 켜세요." : ""}`;
  }
  // 분석의 원래 그림(anaConfig)을 쓰는 카드인지(종류를 정하지 않았거나 바꿀 수 없는 분석 그림)
  const usesAna = (card: SCard) => card.kind === "analysis" && !!card.data?.values && (!card.view.type || !cardSeries(card)!.chartable);
  // 색을 고를 수 있는 이름들: 분석 그림은 주 색·보조 색, 도넛·파이는 조각 이름, 나머지는 계열 이름.
  // P14 새 카드: 값 하나짜리는 주 색, 트리맵·선버스트는 첫 단계 이름, 생키는 노드 이름, 워드클라우드는 많이 나온 단어(30개까지)
  // 색 칸·색 순서에 쓰는 이름은 서버가 받는 것만(calc.okLabel)
  function colorNames(card: SCard): { group: "series" | "parts"; names: string[] } {
    const r = allNames(card);
    return { ...r, names: r.names.filter(okLabel) };
  }
  function allNames(card: SCard): { group: "series" | "parts"; names: string[] } {
    if (card.kind === "geomap") return { group: "parts", names: ((card.data?.values?.regions ?? []) as { name: string }[]).map(r => r.name).slice(0, 30) };
    if (card.kind === "funnel") return { group: "series", names: ["단계 막대", "다음 단계", "이탈"] };
    if (ONE_COLOR.includes(card.kind)) return { group: "series", names: ["주 색"] };
    const more = (card.data?.values ?? {}) as MoreValues;
    if (card.kind === "treemap" || card.kind === "sunburst") return { group: "series", names: (more.items ?? []).map(i => i.name).slice(0, 30) };
    if (card.kind === "sankey") return { group: "series", names: (more.nodes ?? []).map(n => n.name).slice(0, 30) };
    if (card.kind === "wordcloud") return { group: "series", names: [...(more.words ?? [])].sort((a, b) => b.n - a.n).slice(0, 30).map(w => w.w) };
    const s = cardSeries(card);
    if (usesAna(card)) return { group: "series", names: ["주 색", "보조 색"] };
    if (!s) return { group: "series", names: [] };
    return ROUND_TYPES.includes(card.view.type) ? { group: "parts", names: s.labels } : { group: "series", names: s.datasets.map(d => d.label) };
  }
  return { aggName, byUnit, cardSeries, typeReason, typeNote, usesAna, colorNames };
}
export type SeriesKit = ReturnType<typeof seriesKit>;
