// (Phase 4) 표시할 그룹 고르기: 카드의 view.groups(보일 그룹 keep·보일 계열 series·범위 ranges·범위 묶기 merge·보이는 이름 rename)를
// 받은 값(data)에 입혀 그릴 값을 만든다(다시 조회하지 않는다). CardBody가 그리기 전에 한 번 부르고, 카드 종류의 그림(PAINTERS·ChartCard)은 고치지 않는다.
import type { Card, CardData, Colors } from "@/lib/types";

/** 범위 끝 하나: 숫자, 날짜 글자(YYYY-MM-DD), 또는 열림(null) */
export type RangeEnd = number | string | null;
/** view.groups(서버 dash_store.check_groups와 같은 모양). sets는 지도 카드의 영역 묶음 {묶음 이름: [영역 이름…]} */
export type GroupView = { keep?: string[]; series?: string[]; ranges?: [RangeEnd, RangeEnd][]; merge?: boolean; rename?: Record<string, string>; sets?: Record<string, string[]> };
/** 고르기 패널이 보일 이름들: names(그룹), series(계열), ordered(범위를 쓸 수 있는 순서 값인지), kind(범위 값 종류) */
export type GroupNames = { names: string[]; series: string[]; ordered: "number" | "date" | null };

// 그룹 고르기를 입히는 카드 종류(plans/phase-4.md#groupNames 표의 "그 밖"이 아닌 것)
const GROUP_KINDS: string[] = ["chart", "metric", "histogram", "boxplot", "heatmap", "calheat", "candle", "cohort", "treemap", "sunburst", "bump", "sankey", "wordcloud", "funnel", "geomap"];
// 날짜 차트 by_unit 계열에서 라벨 차례를 따르는 배열 칸
const SERIES_ARRAYS = ["sum", "count", "min", "max", "values"] as const;
// applyGroups·groupNames가 읽는 받은 값의 모양(서버 응답 그대로, 쓰지 않는 칸은 펼쳐서 넘긴다)
type Named = { name: string; children?: Named[] };
type Series = { name: string; values?: (number | string | null)[]; ranks?: unknown[]; [k: string]: unknown };
type Values = {
  labels?: string[]; series?: Series[]; merged?: boolean; groups?: Named[]; rows?: string[]; cols?: string[]; cells?: unknown[][];
  items?: Named[]; nodes?: (string | Named)[]; links?: { s: number | string; t: number | string }[]; words?: { w: string }[]; steps?: Named[];
  regions?: { name: string; value: unknown; rings: unknown[] }[]; days?: { d: string }[]; candles?: unknown[]; cohorts?: { start: string }[];
};
type Shaped = { x_kind?: string; values?: Values; by_unit?: Record<string, { labels: string[]; series: Series[] }>; bins?: { edges: number[]; counts: number[] } };

export const MERGE_DATE ="날짜 가로축 차트는 범위를 하나로 묶을 수 없어요. 묶기를 끄세요.";  // merge를 숫자 가로축이 아닌 차트에 쓸 때(Guard가 이 글로 보인다)
export const MERGE_AGAIN = "범위 묶기 값은 서버에서 다시 구해요. 잠시 기다리거나 [다시 구하기]를 누르세요.";  // (Phase 17) 묶기를 켰는데 서버가 묶은 값(values.merged)이 아닐 때

// [SPECIFICATION] plans/phase-4.md#inRanges
export function inRanges(value: string | number, ranges: [RangeEnd, RangeEnd][]): boolean {
  if (!ranges.length) return true;
  return ranges.some(([lo, hi]) => {
    if (lo === null && hi === null) return true;  // 막 더한 빈 범위: 날짜 라벨을 숫자로 비교해 다 지우지 않게
    if (typeof lo === "string" || typeof hi === "string") {
      const s = String(value).slice(0, 10);
      return (lo === null || s >= String(lo)) && (hi === null || s <= String(hi));
    }
    const v = typeof value === "number" ? value : Number(value);
    return Number.isFinite(v) && (lo === null || v >= lo) && (hi === null || v <= hi);
  });
}
// [SPECIFICATION] plans/phase-4.md#rangeName
export function rangeName(range: [RangeEnd, RangeEnd]): string {
  return `${range[0] ?? ""}~${range[1] ?? ""}`;
}
// [SPECIFICATION] plans/phase-4.md#shownName
export function shownName(name: string, g: GroupView | undefined): string {
  return g?.rename?.[name] ?? name;
}
// [SPECIFICATION] plans/phase-4.md#visible
export function visible(name: string, keep: string[] | undefined, ranges: [RangeEnd, RangeEnd][] | undefined, ordered: boolean): boolean {
  return (!keep?.length || keep.includes(name)) && (!ordered || !ranges?.length || inRanges(name, ranges));
}
// [SPECIFICATION] plans/phase-4.md#dragPick
export function dragPick(names: string[], from: number, to: number): string[] {
  return names.slice(Math.min(from, to), Math.max(from, to) + 1);
}
// [SPECIFICATION] plans/phase-4.md#dragRange
export function dragRange(names: string[], from: number, to: number, ordered: "number" | "date"): [RangeEnd, RangeEnd] {
  const a = names[Math.min(from, to)];
  const b = names[Math.max(from, to)];
  return ordered === "number" ? [Number(a), Number(b)] : [a.slice(0, 10), b.slice(0, 10)];
}
// [SPECIFICATION] plans/phase-4.md#renameColors
export function renameColors(colors: Colors, g: GroupView | undefined): Colors {
  if (!g?.rename || !Object.keys(g.rename).length) return colors;
  const keys = <T,>(m: Record<string, T>): Record<string, T> => Object.fromEntries(Object.entries(m).map(([k, v]) => [shownName(k, g), v]));
  const names = (l: string[]): string[] => l.map((name) => shownName(name, g));
  return {
    ...colors,
    ...(colors.series && { series: keys(colors.series) }),
    ...(colors.parts && { parts: keys(colors.parts) }),
    ...(colors.order && { order: names(colors.order) }),
    ...(colors.parts_order && { parts_order: names(colors.parts_order) }),
  };
}
// 지도 영역 이름 → 그 영역이 든 묶음 이름
const regionSets = (g: GroupView | undefined): Map<string, string> =>
  new Map(Object.entries(g?.sets ?? {}).flatMap(([set, members]) => members.map((m) => [m, set] as const)));
// [SPECIFICATION] plans/phase-4.md#groupNames
export function groupNames(card: Card, data: CardData): GroupNames {
  const d = data as unknown as Shaped;
  const v = d.values ?? {};
  const names = (l: { name: string }[] | undefined): string[] => (l ?? []).map((s) => s.name);
  switch (card.kind) {
    case "chart":
    case "metric":
      return {
        names: d.x_kind === "date" ? [] : v.labels ?? [],
        series: v.series ? names(v.series) : names(d.by_unit?.day?.series),
        ordered: card.kind === "chart" ? (d.x_kind === "num" ? "number" : d.x_kind === "date" ? "date" : null) : null,
      };
    case "histogram": return { names: [], series: [], ordered: "number" };
    case "boxplot": return { names: names(v.groups), series: [], ordered: null };
    case "heatmap": return { names: v.rows ?? [], series: v.cols ?? [], ordered: null };
    case "calheat":
    case "candle":
    case "cohort": return { names: [], series: [], ordered: "date" };
    case "treemap":
    case "sunburst": return { names: names(v.items), series: [], ordered: null };
    case "bump": return { names: [], series: names(v.series), ordered: "date" };
    case "sankey": return { names: (v.nodes ?? []).map((x) => (typeof x === "string" ? x : x.name)), series: [], ordered: null };
    case "wordcloud": return { names: (v.words ?? []).map((x) => x.w), series: [], ordered: null };
    case "funnel": return { names: names(v.steps), series: [], ordered: null };
    case "geomap": {  // 묶음에 든 영역은 묶음 이름으로(applyGroups가 keep을 묶은 뒤에 보므로)
      const setOf = regionSets((card.view as unknown as { groups?: GroupView }).groups);
      return { names: [...new Set(names(v.regions).map((r) => setOf.get(r) ?? r))], series: [], ordered: null };
    }
    default: return { names: [], series: [], ordered: null };
  }
}
// [SPECIFICATION] plans/phase-4.md#applyGroups
export function applyGroups(card: Card, data: CardData): CardData {
  const g = (card.view as unknown as { groups?: GroupView }).groups;
  if (!g || !GROUP_KINDS.includes(card.kind)) return data;
  const d = data as unknown as Shaped;
  const v = d.values ?? {};
  const ranges = g.ranges ?? [];
  const n = (name: string): string => shownName(name, g);
  const keepName = (name: string): boolean => !g.keep?.length || g.keep.includes(name);
  const keepSeries = (name: string): boolean => !g.series?.length || g.series.includes(name);
  const at = <T,>(l: T[], idx: number[]): T[] => idx.map((i) => l[i]);
  const where = <T,>(l: T[], ok: (x: T) => boolean): number[] => l.flatMap((x, i) => (ok(x) ? [i] : []));
  const out = (values: Values, more: Partial<Shaped> = {}): CardData => ({ ...data, values, ...more }) as unknown as CardData;
  const renameTree = (items: Named[]): Named[] => items.map((x) => ({ ...x, name: n(x.name), ...(x.children && { children: renameTree(x.children) }) }));
  switch (card.kind) {
    case "chart":
    case "metric": {
      let values = v;
      if (v.labels && v.series) {
        const series = v.series.filter((s) => keepSeries(s.name));
        if (v.merged) {
          values = { ...v, labels: v.labels.map(n), series: series.map((s) => ({ ...s, name: n(s.name) })) };
        } else if (g.merge) {
          throw new Error(d.x_kind !== "num" ? MERGE_DATE : MERGE_AGAIN);  // (Phase 17) 받은 X 값(최대 MAX_GROUPS개)을 화면이 더하지 않는다
        } else {
          const ord = card.kind === "chart" && (d.x_kind === "num" || d.x_kind === "date");
          const labels = v.labels;
          const idx = where(labels, (l) => visible(l, g.keep, g.ranges, ord));
          values = { ...v, labels: at(labels, idx).map(n), series: series.map((s) => ({ ...s, name: n(s.name), values: at(s.values ?? [], idx) })) };
        }
      }
      if (card.kind !== "chart" || !d.by_unit) return out(values);
      const byUnit = Object.fromEntries(Object.entries(d.by_unit).map(([unit, u]) => {
        const idx = where(u.labels, (l) => inRanges(l, ranges));
        const series = u.series.filter((s) => keepSeries(s.name)).map((s): Series => ({
          ...s,
          name: n(s.name),
          ...Object.fromEntries(SERIES_ARRAYS.flatMap((k) => { const arr = s[k]; return Array.isArray(arr) ? [[k, at(arr, idx)]] : []; })),
        }));
        return [unit, { ...u, labels: at(u.labels, idx), series }];
      }));
      return out(values, { by_unit: byUnit });
    }
    case "histogram": {
      if (!d.bins) return data;
      const { edges, counts } = d.bins;
      const kept = counts.map((c, i) => (!ranges.length || ranges.some(([lo, hi]) => (lo === null || edges[i] >= Number(lo)) && (hi === null || edges[i + 1] <= Number(hi))) ? c : 0));
      return { ...data, bins: { ...d.bins, counts: kept } } as unknown as CardData;
    }
    case "boxplot":
      return out({ ...v, groups: (v.groups ?? []).filter((x) => keepName(x.name)).map((x) => ({ ...x, name: n(x.name) })) });
    case "heatmap": {
      const rows = v.rows ?? [], cols = v.cols ?? [], cells = v.cells ?? [];
      const ri = where(rows, keepName), ci = where(cols, keepSeries);
      return out({ ...v, rows: at(rows, ri).map(n), cols: at(cols, ci).map(n), cells: ri.map((i) => ci.map((j) => cells[i][j])) });
    }
    case "calheat":
      return out({ ...v, days: (v.days ?? []).filter((x) => inRanges(x.d, ranges)) });
    case "treemap":
    case "sunburst":
      return out({ ...v, items: renameTree((v.items ?? []).filter((x) => keepName(x.name))) });
    case "bump": {
      const labels = v.labels ?? [];
      const idx = where(labels, (l) => inRanges(l, ranges));
      const series = (v.series ?? []).filter((s) => keepSeries(s.name))
        .map((s) => ({ ...s, name: n(s.name), values: at(s.values ?? [], idx), ...(s.ranks && { ranks: at(s.ranks, idx) }) }));
      return out({ ...v, labels: at(labels, idx), series });
    }
    case "candle": {
      const labels = v.labels ?? [];
      const idx = where(labels, (l) => inRanges(l, ranges));
      return out({ ...v, labels: at(labels, idx), candles: at(v.candles ?? [], idx) });
    }
    case "sankey": {
      const nodes = v.nodes ?? [];
      const nameOf = (x: string | Named): string => (typeof x === "string" ? x : x.name);
      const idx = where(nodes, (x) => keepName(nameOf(x)));
      const renum = new Map(idx.map((old, i) => [old, i]));
      const ok = (e: number | string): boolean => (typeof e === "number" ? renum.has(e) : keepName(e));
      const to = (e: number | string): number | string => (typeof e === "number" ? renum.get(e) as number : n(e));
      return out({
        ...v,
        nodes: at(nodes, idx).map((x) => (typeof x === "string" ? n(x) : { ...x, name: n(x.name) })),
        links: (v.links ?? []).filter((l) => ok(l.s) && ok(l.t)).map((l) => ({ ...l, s: to(l.s), t: to(l.t) })),
      });
    }
    case "cohort":
      return out({ ...v, cohorts: (v.cohorts ?? []).filter((x) => inRanges(x.start, ranges)) });
    case "wordcloud":
      return out({ ...v, words: (v.words ?? []).filter((x) => keepName(x.w)).map((x) => ({ ...x, w: n(x.w) })) });
    case "funnel":
      return out({ ...v, steps: (v.steps ?? []).filter((x) => keepName(x.name)).map((x) => ({ ...x, name: n(x.name) })) });
    default: {  // geomap
      const regions = v.regions ?? [];
      const setOf = regionSets(g);
      const grouped = regions.flatMap((r) => {
        const set = setOf.get(r.name);
        if (set === undefined) return [r];
        const members = regions.filter((x) => setOf.get(x.name) === set);
        if (members[0] !== r) return [];  // 묶음은 첫 영역 자리에 하나만
        const nums = members.map((x) => x.value).filter((x): x is number => typeof x === "number");
        return [{ ...r, name: set, value: nums.length ? nums.reduce((a, b) => a + b, 0) : null, rings: members.flatMap((x) => x.rings) }];
      });
      return out({ ...v, regions: grouped.filter((r) => keepName(r.name)).map((r) => ({ ...r, name: n(r.name) })) });
    }
  }
}
