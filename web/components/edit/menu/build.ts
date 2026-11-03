// 카드 추가 메뉴의 칸들 → 카드 JSON(옛 dash_menu.js build). 화면 없이 계산하고, 잘못된 입력은 옛 문장 그대로 Error로 던진다.
import { readLines, parseRuleLine, parseStateLine, parseLinkLine } from "@/lib/calc";
import { seriesTitle } from "@/lib/series";
import type { Card, CardKind } from "@/lib/types";
import { CARD_KINDS, WATCH, WITH_DATE, freshColors, parseBlankLine } from "./kinds";

export type Row = { label: string; y: string; agg: string };
export type Form = {
  table: string; kind: CardKind; title: string; rows: Row[];
  group: string; date: string; metric: string; targetValue: string; targetName: string; format: string; unitText: string; ring: string;
  cells: string; size: string; rowCount: string; groupCols: string; total: boolean;
  entity: string; timeCol: string; kindCol: string; linkCol: string; stale: string; bg: string; imagePath: string; bucket: string; baseline: string; threshold: string;
  cType: string; cX: string; cSize: string;
  fUser: string; fStep: string; fTime: string; fSteps: string; fWin: string; fUnit: string;
  gTable: string; gPath: string; gName: string; gGeom: string; gRegion: string; gLat: string; gLon: string; gValue: string; gAgg: string; gPoints: boolean; gTiles: boolean;
  template: string; condText: string; stateText: string; linkText: string; blankText: string;
};
export const emptyForm = (kind: CardKind = "kpi", cType = ""): Form => ({
  table: "", kind, title: "", rows: [], group: "", date: "", metric: "", targetValue: "", targetName: "", format: "number", unitText: "", ring: "stack",
  cells: "10", size: "", rowCount: "10", groupCols: "", total: false,
  entity: "", timeCol: "", kindCol: "", linkCol: "", stale: "", bg: "rack", imagePath: "", bucket: "hour", baseline: "", threshold: "",
  cType, cX: "", cSize: "", fUser: "", fStep: "", fTime: "", fSteps: "", fWin: "", fUnit: "minute",
  gTable: "", gPath: "", gName: "", gGeom: "geometry", gRegion: "", gLat: "", gLon: "", gValue: "", gAgg: "sum", gPoints: true, gTiles: false,
  template: "", condText: "", stateText: "", linkText: "", blankText: "",
});

type Body = NonNullable<Card["source"]["body"]>;  // series의 y는 컬럼 없는 개수면 null(서버가 받는 모양)
const num = (s: string) => +s;
/** 글 칸을 줄마다 읽어 틀린 줄이 있으면 그 줄과 이유로 오류를 낸다 */
function lines(text: string, parse: (l: string) => { error?: string } & Record<string, unknown>, key: string): unknown[] {
  const found = readLines(text, parse), bad = found.find(f => f.error);
  if (bad) throw new Error(`${bad.line} → ${bad.error}`);
  return found.map(f => f[key]);
}

/** 카드 하나를 만든다(layout은 부른 쪽이 붙인다). needs: 그 집계가 컬럼을 꼭 받는가(컬럼이 없으면 개수로 센다). aggNames: 상수 agg_names(계열 이름이 서버와 같게) */
export function buildCard(f: Form, aggs: string[], needs: (agg: string) => boolean = () => false, aggNames: Record<string, string> = {}): Partial<Card> {
  const k = f.kind, baseName = f.table || (k === "geomap" ? f.gTable : "");
  if (!baseName) throw new Error("테이블을 고르세요.");
  const [kindName, minRows] = CARD_KINDS[k]!;
  if (k === "funnel") {  // 흐름은 쿼리(body) 대신 전처리 없는 테이블(recipe)과 흐름 분석(analysis)을 다시 구할 기록으로 둔다
    const steps = f.fSteps.split(",").map(s => s.trim()).filter(Boolean);
    if (!f.fUser || !f.fStep || !f.fTime) throw new Error("사용자·단계·시각 컬럼을 모두 고르세요.");
    if (!steps.length || steps.length > 10) throw new Error("단계는 1~10개를 쉼표로 나눠 적어 주세요.");
    if (f.fWin !== "" && !(num(f.fWin) > 0)) throw new Error("기간 제한은 0보다 큰 숫자이거나 비워 두세요.");
    return { kind: k, title: f.title.trim() || `${f.table} 퍼널`, colors: freshColors(), view: { type: "", ratio: 4 / 3 } as unknown as Card["view"], data: {},
      source: { schema: "", table: f.table, recipe: { schema: "", base: f.table }, full: true, date: f.fTime,
        analysis: { kind: "funnel", who: f.fUser, step: f.fStep, time: f.fTime, steps, window: f.fWin === "" ? null : { n: num(f.fWin), unit: f.fUnit } } } };
  }
  if (k === "chart" && !f.cX) throw new Error("가로축 컬럼을 고르세요.");
  const empty = { schema: "", base: baseName, joins: [], periods: [], conditions: [], calcs: [], sorts: [], columns: [], series: [] as unknown[] };
  if (k === "geomap") {  // 영역(GeoJSON 테이블이나 파일)과 점의 위도·경도 중 하나는 있어야 한다. 읽을 조회는 점 테이블이고, 영역은 geo.regions가 따로 읽는다
    const regions = f.gPath.trim() ? { path: f.gPath.trim() } : f.gTable ? { table: f.gTable } : null;
    if (!regions && !f.gLat) throw new Error("영역 테이블이나 GeoJSON 파일, 또는 점의 위도와 경도 컬럼을 고르세요.");
    if (regions && !f.gName.trim()) throw new Error("영역 이름 컬럼을 적어 주세요. GeoJSON의 속성 이름이에요.");
    if (!!f.gLat !== !!f.gLon) throw new Error("점은 위도와 경도 컬럼을 함께 고르세요.");
    const geo = { ...(regions ? { regions, name: f.gName.trim(), geometry: f.gGeom.trim() || "geometry" } : {}), ...(f.gRegion.trim() ? { region_value: f.gRegion.trim() } : {}),
      ...(f.gLat ? { lat: f.gLat, lon: f.gLon, ...(f.gValue ? { value: f.gValue } : {}), agg: f.gValue ? f.gAgg : "count", points: f.gPoints } : {}), tiles: f.gTiles };
    return { kind: k, title: f.title.trim() || `${baseName} 지도`, colors: freshColors(), view: { type: "", ratio: 4 / 3, format: f.format, geo } as unknown as Card["view"], data: {},
      source: { schema: "", table: baseName, body: empty as unknown as Body, ...(f.date ? { date: f.date } : {}) } };
  }
  const values = f.rows.map(r => {  // 컬럼이 꼭 필요한 집계인데 컬럼을 안 고르면 개수
    const y = r.y || null;
    return { label: r.label.trim(), y, agg: needs(r.agg) && !y ? "count" : r.agg };
  });
  const series = values.map(({ y, agg }) => ({ y, agg })), byMetric = k === "kpi" && !!f.metric;
  if (!byMetric && minRows && !series.length) throw new Error("값을 하나 이상 넣으세요.");
  if (k === "gauge" && !f.group) throw new Error("게이지는 나눌 컬럼을 고르세요.");
  const body = { ...empty, series: byMetric ? [] : series, ...(k === "gauge" ? { x: f.group } : {}), ...(k === "chart" ? { x: f.cX } : {}), ...(byMetric ? { names: [f.metric] } : {}) };
  const view: Record<string, unknown> = { type: "", ratio: 4 / 3, format: f.format, icon: "chart", compare: { mode: "span" }, better: "up" };
  if (byMetric && f.format === "number") delete view.format;  // 형식을 따로 고르지 않은 지표 카드는 지표 파일의 형식을 쓴다(F22: 지표마다 단위가 다르다)
  if (f.unitText.trim() && ["kpi", "donut_kpi", "stripes"].includes(k)) view.unit_text = f.unitText.trim();
  if (f.targetValue !== "") view.target = { value: num(f.targetValue) };
  else if (f.targetName) view.target = { name: f.targetName };
  if (k === "donut_kpi") { view.ring = f.ring; view.slots = values.map((l, i) => ({ label: l.label || seriesTitle(series[i], aggNames), format: f.format, unit_text: view.unit_text ?? "" })); }
  if (k === "chart") {
    view.type = f.cType;
    view.ratio = 16 / 9;
    if (f.cSize && f.cType === "bubble") view.points = { size: f.cSize };
    if (["scatter", "bubble"].includes(f.cType) && series.length !== 1) throw new Error("산점도와 버블은 값을 하나만 넣으세요. 가로축과 값이 점의 위치예요.");
  }
  if (k === "stripes") view.cells = num(f.cells) || 10;
  if (k === "blocks" && f.size) view.size = num(f.size);
  if (k === "recent_rows" || k === "table") view.rows = num(f.rowCount) || 10;
  if (k === "summary" && f.group) view.group = f.group;
  if (k === "table") {
    view.group_cols = f.groupCols.split(",").map(c => c.trim()).filter(Boolean);
    view.total = f.total;
    view.formats = Object.fromEntries(series.map(s => [seriesTitle(s, aggNames), f.format]));
    view.cond_rules = lines(f.condText, parseRuleLine, "rule");
  }
  if (k === "sentence") {
    if (!f.template.trim()) throw new Error("문장 틀을 쓰세요. 예: 오늘 매출은 {매출}이에요");
    view.template = f.template.trim();
    view.blanks = lines(f.blankText, l => parseBlankLine(l, aggs), "blank");
    view.cond_rules = lines(f.condText, parseRuleLine, "rule");
  }
  if (WATCH.includes(k)) {
    if (!f.entity) throw new Error("대상 컬럼을 고르세요.");
    Object.assign(view, { entity: f.entity, rules: lines(f.stateText, parseStateLine, "rule"), links: lines(f.linkText, parseLinkLine, "link"), ratio: 400 / 260 });
    if (f.timeCol) view.time_col = f.timeCol;
    if (f.kindCol) view.kind_col = f.kindCol;
    if (f.linkCol) view.link_col = f.linkCol;
    if (f.stale) view.stale = num(f.stale);
    if (k === "map") { view.bg = f.bg; if (f.bg === "image") view.image_path = f.imagePath.trim(); }
  }
  if (k === "deviation") {
    if (!f.entity || !f.date) throw new Error("편차 표는 대상 컬럼과 날짜 컬럼을 골라야 해요.");
    Object.assign(view, { entity: f.entity, bucket: f.bucket });
    if (f.baseline !== "") view.baseline = num(f.baseline);
    if (f.threshold !== "") view.threshold = num(f.threshold);
  }
  return { kind: k, title: f.title.trim() || `${baseName} ${kindName}`, colors: freshColors(), view: view as unknown as Card["view"], data: {},
    source: { schema: "", table: baseName, body: body as unknown as Body, ...(f.date && WITH_DATE.includes(k) ? { date: f.date } : {}) } };
}
