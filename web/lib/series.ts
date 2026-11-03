// 카드 값 계산 중 여러 카드·화면 틀이 함께 쓰는 순수 함수 한 벌(옛 static/core/dash.js의 windowValue·loadedRange·pointValue·compareItem·pickColor·seriesTitle,
// common.js의 sameSource). 본문은 옛 것 그대로이고, 옛 전역 SERVER에서 읽던 merge(다시 묶을 수 있는 집계)·agg_names는 인자로 받는다.
import { daysBetween, labelIndex, ownRange, rollup, shiftDay, zeroWhenEmpty } from "./calc";
import type { Card, CompareItem, CompareMode, DaySeries, PrevSpan } from "./types";

/** 보는 기간·읽은 범위(열린 쪽은 비움) */
export type Range = { start?: string | null; end?: string | null } | null | undefined;

/** 연결 열쇠 비교(서버 use_counts.same_source와 같은 규칙: 폴더만 있는 예전 열쇠는 그 폴더의 어느 파일 연결과도, query 없던 예전 열쇠는 같은 종류의 지금 열쇠와 같다) */
export const sameSource = (saved: unknown, now: unknown) => saved === now || (typeof saved === "string" && typeof now === "string" &&
  ((!saved.includes("#") && now.split("#")[0] === saved) || (saved.endsWith("://:/") && now.startsWith(saved + "?"))));

/** 서버(dash_calc.series_name)와 같은 계열 이름: 개수 / 합계 (컬럼) / 옵션이 있으면 분위수 (컬럼) p=0.9 (order는 뺀다) */
export const seriesTitle = (s: { agg: string; y?: string | null; opts?: Record<string, unknown> }, aggNames: Record<string, string> = {}) =>
  (aggNames[s.agg] ?? s.agg) + (s.agg === "count" || !s.y ? "" : ` (${s.y})`) +
  Object.entries(s.opts ?? {}).filter(([k]) => k !== "order").map(([k, v]) => ` ${k}=${([] as unknown[]).concat(v).join(",")}`).join("");

/** 일 값 계열 s의 from~to(포함) 구간 값. 구간에 일 값이 없으면 null, 읽어 온 범위(loaded) 안이면 개수·합계는 0 */
export function windowValue(s: DaySeries, days: string[], from: string | null | undefined, to: string | null | undefined, merge: string[], loaded?: Range): number | null {
  const keep = days.map((_, i) => i).filter(i => (!from || days[i] >= from) && (!to || days[i] <= to));
  if (!keep.length) return loaded && zeroWhenEmpty(s.agg) && (!loaded.start || (from && from >= loaded.start)) && (!loaded.end || (to && to <= loaded.end)) ? 0 : null;
  if (!merge.includes(s.agg)) return keep.length === 1 ? s.values?.[keep[0]] ?? null : null;  // 고유 개수·중앙값 같은 집계는 여러 날을 다시 묶을 수 없다(하루 값만)
  const pick = (a?: (number | null)[]) => a && keep.map(i => a[i]);
  return (rollup({ name: s.name, agg: s.agg, sum: pick(s.sum), count: pick(s.count), min: pick(s.min), max: pick(s.max) }, keep.map(() => "2000-01-01"), "day").get("2000-01-01") as number | null | undefined) ?? null;
}

/** 일 값이 빈 날을 0으로 말할 수 있는 범위(data.window, 없으면 일 값의 첫 날~끝 날). 월 값을 서버가 따로 구했으면 남은 첫 날부터 */
export function loadedRange(card: Card, merge: string[]): Range {
  const by = card.data?.by_unit, days = by?.day?.labels ?? [];
  if (!by?.day) return null;
  const w = card.data.window ?? { start: days[0], end: days[days.length - 1] };
  const cut = (by.month?.series ?? []).some(m => by.day!.series.some(s => s.name === m.name && merge.includes(s.agg)));
  return cut ? { ...w, start: days[0] ?? w.start } : w;
}

/** date의 값(그날 값이 없으면 그 전 마지막 값) */
export function pointValue(s: DaySeries, days: string[], date: string, merge: string[]) {
  for (let i = days.length - 1; i >= 0; i--) if (days[i] <= date) { const v = windowValue(s, days, days[i], days[i], merge); if (v != null) return v; }
  return null;
}

/** 시간별 카드의 첫 계열을 두 기간(또는 두 시점)으로 비교할 값(/api/compare가 문장으로). 안 되는 카드는 null.
 *  대시보드 기간이 비면 카드 자신의 기간 줄을 비교 기준(basis, 대시보드 compare_mode)대로 쓴다. card.data는 지금 보이는 값(구한 값 또는 저장된 값) */
export function compareItem(card: Card, range: { start: string; end: string } | null | undefined, prev: PrevSpan | null | undefined, merge: string[], basis: CompareMode = "prev"): CompareItem | null {
  if (!range) { const own = ownRange(card, basis); if (!own) return null; ({ range, prev } = own); }
  const by = card.data?.by_unit, s = by?.day?.series[0];
  if (!s || !range?.start || !range?.end) return null;
  const days = by!.day!.labels, mode = card.view?.compare?.mode ?? "span", better = card.view?.better ?? "up", n = daysBetween(range.start, range.end);
  if (!merge.includes(s.agg)) {  // 고유 개수 같은 집계는 서버가 두 기간으로 따로 구한 값(window_nunique와 그 prev)을 쓴다. 기간 비교만
    type Win = { start?: string; end?: string; values: Record<string, unknown>; prev?: Win };
    const w = card.data?.values?.window_nunique as Win | undefined, same = (a?: { start?: string; end?: string }, b?: { start?: string; end?: string } | null) => !!a && a.start === b?.start && a.end === b?.end;
    if (mode !== "span" || !prev?.start || !w || !same(w, range) || !same(w.prev, prev) || w.values["0"] == null || w.prev!.values["0"] == null) return null;
    return { cur: w.values["0"], prev: w.prev!.values["0"], mode, label: prev.text, better, partial: prev.start < days[0] };
  }
  if (mode === "point") {
    const before = shiftDay(range.end, -n);
    return { cur: pointValue(s, days, range.end, merge), prev: pointValue(s, days, before, merge), mode, label: `${n}일 전`, better, partial: before < days[0] };
  }
  if (!prev?.start) return null;
  const loaded = loadedRange(card, merge);
  return { cur: windowValue(s, days, range.start, range.end, merge, loaded), prev: windowValue(s, days, prev.start, prev.end, merge, loaded), mode, label: prev.text, better, partial: prev.start < days[0] };
}

// 카드 객체(그리는 한 번에 쓰는 것)마다 색 순서의 복사본. 새 라벨은 이 복사본에만 붙는다(React 상태인 card.colors를 그리는 중에 바꾸지 않는다).
// 새 라벨의 순서는 값이 바뀐 뒤 화면 틀(ShellProvider)이 newOrders로 찾아 dispatch card로 저장한다(옛 화면은 그리면서 card.colors에 붙여 저장했다).
const orders = new WeakMap<Card, { order: string[]; parts_order: string[] }>();
/** 색 정하기: 카드가 지정한 색 → 대시보드 팔레트의 번호째 → 화면 모드의 기본 색. group은 "series"(계열) 또는 "parts"(도넛·파이의 조각).
 *  카드에 colors가 없는 임시 카드(탐색 차트)는 계열 순서(index)로 정한다. 지정 색은 라이트·다크가 같다 */
export function pickColor(card: Card, group: "series" | "parts", name: string, colors: { palette?: string[]; series: string[] }, index = 0) {
  const c = card.colors, key = group === "parts" ? "parts_order" : "order";
  if (c && !orders.has(card)) orders.set(card, { order: [...(c.order ?? [])], parts_order: [...(c.parts_order ?? [])] });
  const i = c?.[key] ? labelIndex(orders.get(card)![key], name) : index;
  return c?.[group]?.[name] ?? colors.palette?.[i] ?? colors.series[i % colors.series.length];
}

/** 그린 라벨(names) 중 카드의 색 순서(colors.order·parts_order)에 아직 없는 것을 붙인 새 순서. 늘지 않았으면 null(저장할 것 없음) */
export function newOrder(card: Card, group: "series" | "parts", names: string[]) {
  const key = group === "parts" ? "parts_order" : "order", had = card.colors?.[key];
  if (!had) return null;
  const order = [...had];
  names.forEach(n => labelIndex(order, n));
  return order.length > had.length ? { ...card.colors, [key]: order } : null;
}
