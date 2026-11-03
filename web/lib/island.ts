// 차트 섬(/chart, iframe) 메시지 규약: 쿼리 화면 차트·지표 탭과 분석 화면(옛 Chart.js chartConfig·anaConfig 자리)이 iframe에 postMessage로 그릴 것을 보낸다.
// 같은 출처만 받고(event.origin === location.origin, 보낸 쪽은 부모 창), 답도 같은 출처로만 보낸다.
import type { Ana, Points } from "@/components/cards/chart/Charts";
import type { Dataset } from "@/components/cards/chart/series";

export type Theme = "light" | "dark";
export type AxisRange = { min?: number; max?: number };
/** 왼쪽(y)·오른쪽(y1) 값 축 범위(옛 axisRanges) */
export type Ranges = { y?: AxisRange; y1?: AxisRange };
/** 계열 차트 값: 옛 toDrawn·metricDrawn의 {labels, datasets}. 계열마다 type(bar|line)·dash(dashed)·axis(right) */
export type SeriesData = { labels: string[]; datasets: Dataset[] };
/** type: 차트 종류(""면 계열마다 정한 대로), ranges: 축 범위, percent: 값 축 %, ratio: 가로/세로(없으면 그룹 수로 자동), relabel: 라벨 바꾸기 글(한 줄에 원래값=보일값) */
export type SeriesOpts = { type?: string; ranges?: Ranges; percent?: boolean; ratio?: number; relabel?: string };
/** /api/chart/points 응답 그대로. type: scatter|bubble, trend: 추세선, show_r: r 제목 */
export type PointsOpts = { type?: "scatter" | "bubble"; trend?: boolean; show_r?: boolean; ranges?: Ranges; ratio?: number };
/** /api/analyze 응답(필요하면 부모가 범위로 자른 것). view: hist|box|values|heat|scatter|groups|trend|funnel */
export type AnaOpts = { view: string; ratio?: number };

type Common = { type: "draw"; theme?: Theme; palette?: string[] };
export type Draw = Common & (
  | { kind: "series"; payload: SeriesData; opts?: SeriesOpts }
  | { kind: "points"; payload: Points; opts?: PointsOpts }
  | { kind: "ana"; payload: Ana; opts: AnaOpts });
/** 부모 → 섬. picture: 지금 그림을 그 모드(없으면 지금 모드)의 그림 파일로 */
export type ToIsland = Draw | { type: "picture"; theme?: Theme; format?: "png" | "jpeg" };
/** 섬 → 부모. ready: 받을 준비가 됨(처음 한 번), size: 그린 뒤 필요한 높이(px), png: picture의 답(data URL), error: 그리지 못한 이유 */
export type FromIsland = { type: "ready" } | { type: "size"; height: number } | { type: "png"; dataUrl: string } | { type: "error"; message: string };

/** 받은 메시지가 같은 출처의 부모 창에서 온 규약 메시지면 그것, 아니면 null */
export const fromParent = (e: MessageEvent): ToIsland | null =>
  e.origin === location.origin && e.source === window.parent && typeof e.data?.type === "string" ? e.data as ToIsland : null;
export const toParent = (m: FromIsland) => window.parent.postMessage(m, location.origin);

/** 옛 쿼리 화면 fitChart의 자동 비율: 그룹 5개 이하 1:1, 늘수록 넓어져 25개 이상이면 16:9 */
export const autoRatio = (groups: number) => 1 + (16 / 9 - 1) * Math.min(1, Math.max(0, (groups - 5) / 20));

/** 옛 쿼리 화면 labeled: 라벨 바꾸기를 X 라벨과 계열 이름("계열 · 값" 꼴은 부분마다)에 적용한 복사본. 값은 그대로 */
export function relabel(d: SeriesData, text = ""): SeriesData {
  const map = new Map(text.split("\n").map(l => l.split(/=([\s\S]*)/)).filter(p => p.length > 1).map(([k, v]) => [k.trim(), v.trim()] as const));
  const m = (s: unknown) => map.get(String(s)) ?? String(s);
  return map.size ? { ...d, labels: d.labels.map(m), datasets: d.datasets.map(k => ({ ...k, label: String(k.label).split(" · ").map(m).join(" · ") })) } : d;
}
