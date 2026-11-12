// 카드 추가 메뉴의 칸들 → 카드 JSON(옛 dash_menu.js build). 화면 없이 계산하고, 잘못된 입력은 옛 문장 그대로 Error로 던진다.
import { linkLine, readLines, parseRuleLine, parseStateLine, parseLinkLine, ruleLine, stateLine } from "@/lib/calc";
import { seriesTitle } from "@/lib/series";
import type { Card, CardKind } from "@/lib/types";
import { CARD_KINDS, DATE_X, WATCH, WITH_DATE, blankLine, freshColors, isShell, parseBlankLine } from "./kinds";

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
  split: string; bins: string; bands: string; cUnit: string;  // P14: body.split(열·아랫단계), 히스토그램 구간 수, 불릿 구간, 순위·캔들·코호트 기간 단위
  icon: string; better: string; compare: string;  // 메뉴에는 칸이 없고 [데이터 다시 정하기] 때 카드의 값을 지킨다
  targetKeep: Record<string, unknown>;  // 이름 목표의 나머지(period·scope 등, 다시 정할 때 같은 이름이면 그대로)
  fDefine: "values" | "stages"; fStages: StageRow[]; fCount: "order" | "each"; fTrim: string;  // Phase 3 퍼널: 단계 정의·조건 단계·세기·절사평균 자르는 %
  fFlow: "steps" | "auto"; fStarts: string[]; fMiddles: string[]; fEnds: string[]; fDepth: string; fDropout: "" | "on" | "off"; fConds: string;  // Phase 3 생키: 방식·자동 흐름 값·깊이·이탈 칸·조건 줄
};
export const emptyForm = (kind: CardKind = "kpi", cType = ""): Form => ({
  table: "", kind, title: "", rows: [], group: "", date: "", metric: "", targetValue: "", targetName: "", format: "number", unitText: "", ring: "stack",
  cells: "10", size: "", rowCount: "10", groupCols: "", total: false,
  entity: "", timeCol: "", kindCol: "", linkCol: "", stale: "", bg: "rack", imagePath: "", bucket: "hour", baseline: "", threshold: "",
  cType, cX: "", cSize: "", fUser: "", fStep: "", fTime: "", fSteps: "", fWin: "", fUnit: "minute",
  gTable: "", gPath: "", gName: "", gGeom: "geometry", gRegion: "", gLat: "", gLon: "", gValue: "", gAgg: "sum", gPoints: true, gTiles: false,
  template: "", condText: "", stateText: "", linkText: "", blankText: "",
  split: "", bins: "20", bands: "", cUnit: "month", icon: "chart", better: "up", compare: "span", targetKeep: {},
  fDefine: "values", fStages: [], fCount: "order", fTrim: "", fFlow: "steps", fStarts: [], fMiddles: [], fEnds: [], fDepth: "", fDropout: "", fConds: "",
});

/** 조건 한 개를 글 한 줄로: 글자는 그대로, {line}은 line, {any}는 각 줄을 " or "로 이음 */
const condText = (c: unknown): string => typeof c === "string" ? c : (c as { line?: string }).line ?? ((c as { any?: unknown[] }).any ?? []).map(condText).join(" or ");
/** body.x가 폼의 [나눌 컬럼](group)인 종류 */
const X_GROUP: CardKind[] = ["gauge", "boxplot", "heatmap", "treemap", "sunburst", "wordcloud"];
/** [데이터 다시 정하기]의 폼 시작값: 카드의 모양 설정(view)을 폼 칸으로 되돌린다(buildCard가 다시 만든 view로 덮어도 그대로 남게) */
export function formFromCard(card: Card): Partial<Form> {
  const v = card.view ?? ({} as Card["view"]), t = v.target as { value?: number; name?: string } | undefined, s = (x: unknown) => (x == null ? "" : String(x));
  const { name, ...keep } = (t?.value == null ? t ?? {} : {}) as Record<string, unknown>;
  type Geo = { regions?: { path?: string; table?: string }; name?: string; geometry?: string; region_value?: string; lat?: string; lon?: string; value?: string; agg?: string; points?: boolean; tiles?: boolean };
  const b = (card.source?.body ?? {}) as { x?: string; split?: string; conditions?: unknown[] };
  const a = (card.source?.analysis ?? {}) as { kind?: string; who?: string; user?: string; step?: string; time?: string; steps?: string[]; stages?: Stage[]; count?: "order" | "each"; trim?: number | null; mode?: string; starts?: string[]; middles?: string[]; ends?: string[]; depth?: number | null; window?: { n?: number; unit?: string } | null }, g = (v.geo ?? {}) as Geo;
  const auto = a.mode === "auto";
  return {
    // Phase 3: 퍼널 단계 정의·세기·절사평균, 생키 자동 흐름·조건 줄
    fDefine: a.kind === "stages" ? "stages" : "values", fStages: a.kind === "stages" ? rowsOf(a.stages ?? []) : [], fCount: a.count ?? "order",
    fTrim: a.trim == null ? "" : String(Math.round(a.trim * 100)),
    fFlow: auto ? "auto" : "steps", fStarts: auto ? a.starts ?? [] : [], fMiddles: auto ? a.middles ?? [] : [], fEnds: auto ? a.ends ?? [] : [],
    fDepth: auto && a.depth != null ? String(a.depth) : "", fDropout: v.flow_dropout == null ? "" : v.flow_dropout ? "on" : "off",
    fConds: (b.conditions ?? []).map(condText).join("\n"),
    // 흐름(퍼널 who, 생키·코호트 user)과 지도는 buildCard의 반대로
    fUser: s(a.who ?? a.user), fStep: s(a.step), fTime: s(a.time), fSteps: (a.steps ?? []).join(", "), fWin: s(a.window?.n), fUnit: a.window?.unit ?? "minute",
    gTable: s(g.regions?.table), gPath: s(g.regions?.path), gName: s(g.name), gGeom: g.geometry ?? "geometry", gRegion: s(g.region_value), gLat: s(g.lat), gLon: s(g.lon), gValue: s(g.value),
    gAgg: g.agg && g.agg !== "count" ? g.agg : "sum", gPoints: g.points ?? true, gTiles: !!g.tiles, timeCol: s(v.time_col),
    // 가로축·나누기 컬럼(source.body.x·split)과 버블 크기: 종류마다 어느 폼 칸인지(buildCard의 반대)
    date: s(card.source?.date ?? (DATE_X.includes(card.kind) ? b.x : undefined)), cX: card.kind === "chart" ? s(b.x) : "", cSize: s(v.points?.size),
    group: s(v.group ?? (card.kind === "bump" ? b.split : X_GROUP.includes(card.kind) ? b.x : undefined)), split: ["heatmap", "treemap", "sunburst"].includes(card.kind) ? s(b.split) : "",
    format: v.format ?? "number", unitText: v.unit_text ?? "", icon: v.icon ?? "chart", better: v.better ?? "up", compare: v.compare?.mode ?? "span",
    targetValue: s(t?.value), targetName: s(name), targetKeep: { ...keep, name }, ring: v.ring ?? "stack", cells: s(v.cells ?? 10), size: s(v.size), rowCount: s(v.rows ?? 10),
    groupCols: ((v.group_cols ?? []) as string[]).join(", "), total: !!v.total,
    entity: s(v.entity), kindCol: s(v.kind_col), linkCol: s(v.link_col), stale: s(v.stale), bg: v.bg ?? "rack", imagePath: v.image_path ?? "",
    bucket: v.bucket ?? "hour", baseline: s(v.baseline), threshold: s(v.threshold), template: v.template ?? "",
    condText: (v.cond_rules ?? []).map(ruleLine).join("\n"), stateText: (v.rules ?? []).map(stateLine).join("\n"), linkText: (v.links ?? []).map(linkLine).join("\n"),
    blankText: ((v.blanks ?? []) as unknown as Parameters<typeof blankLine>[0][]).map(blankLine).join("\n"),
    bins: s(v.bins ?? 20), bands: (v.bands ?? []).join(", "), cUnit: v.unit || "month",
  };
}

type Body = NonNullable<Card["source"]["body"]>;  // series의 y는 컬럼 없는 개수면 null(서버가 받는 모양)
const num = (s: string) => +s;
/** 불릿 구간: 쉼표로 나눈 숫자 1~max개(서버 상수 dash.max_bands, 기본 6), 작은 것부터(서버 check_view와 같은 규칙) */
export function parseBands(text: string, max = 6): number[] {
  const out = text.split(",").map(t => t.trim()).filter(Boolean).map(Number);
  if (!out.length || out.length > max || out.some(v => !Number.isFinite(v)) || out.some((v, i) => i && v <= out[i - 1])) throw new Error(`구간은 숫자 1~${max}개를 쉼표로 나눠 작은 것부터 적어 주세요. 예: 50, 80, 100`);
  return out;
}
/** [데이터 다시 정하기]의 색: 값이 있던 카드는 정해 둔 색 그대로, 빈 틀만 새로 만든 색 */
export const refillColors = (card: Card, fresh: Card["colors"]) => (isShell(card) ? fresh : card.colors);
/** [데이터 다시 정하기]의 view 합치기: 폼이 다루는데 비울 수 있는 칸(목표·단위)은 옛 view에서 먼저 빼고 새 view를 덮는다(비우면 지워지게). 폼에 없는 모양 설정은 그대로 */
export function refillView(old: Card["view"], fresh: Partial<Card["view"]>): Card["view"] {
  const rest: Record<string, unknown> = { ...old };
  for (const k of FORM_KEYS) delete rest[k];
  return { ...rest, ...fresh } as Card["view"];
}
/** buildCard가 폼 칸이 비면 만들지 않는 view 키(다시 정할 때 옛 값을 먼저 지운다) */
export const FORM_KEYS = ["target", "unit_text", "bands", "stale", "time_col", "kind_col", "link_col", "baseline", "threshold", "group", "points", "size", "image_path"] as const;
/** 꼭 고를 칸: 비었으면 "<이름>을 고르세요." */
const need = (v: string, what: string) => { if (!v) throw new Error(`${what}을 고르세요.`); return v; };
/** 글 칸을 줄마다 읽어 틀린 줄이 있으면 그 줄과 이유로 오류를 낸다 */
function lines(text: string, parse: (l: string) => { error?: string } & Record<string, unknown>, key: string): unknown[] {
  const found = readLines(text, parse), bad = found.find(f => f.error);
  if (bad) throw new Error(`${bad.line} → ${bad.error}`);
  return found.map(f => f[key]);
}

/** 카드 하나를 만든다(layout은 부른 쪽이 붙인다). needs: 그 집계가 컬럼을 꼭 받는가(컬럼이 없으면 개수로 센다). aggNames: 상수 agg_names(계열 이름이 서버와 같게) */
export function buildCard(f: Form, aggs: string[], needs: (agg: string) => boolean = () => false, aggNames: Record<string, string> = {}, maxBands = 6, maxBins = 100): Partial<Card> {
  const k = f.kind, baseName = f.table || (k === "geomap" ? f.gTable : "");
  if (!baseName) throw new Error("테이블을 고르세요.");
  const [kindName, minRows] = CARD_KINDS[k]!;
  if (k === "funnel") {  // 흐름은 쿼리(body) 대신 전처리 없는 테이블(recipe)과 흐름 분석(analysis)을 다시 구할 기록으로 둔다
    const byStages = f.fDefine === "stages", steps = f.fSteps.split(",").map(s => s.trim()).filter(Boolean);
    if (byStages) { if (!f.fUser || !f.fTime) throw new Error("사용자·시각 컬럼을 모두 고르세요."); }
    else {
      if (!f.fUser || !f.fStep || !f.fTime) throw new Error("사용자·단계·시각 컬럼을 모두 고르세요.");
      if (!steps.length || steps.length > 10) throw new Error("단계는 1~10개를 쉼표로 나눠 적어 주세요.");
    }
    if (f.fWin !== "" && !(num(f.fWin) > 0)) throw new Error("기간 제한은 0보다 큰 숫자이거나 비워 두세요.");
    const trimText = f.fTrim.trim(), trimPercent = Number(trimText);
    if (trimText && !(Number.isFinite(trimPercent) && trimPercent >= 0 && trimPercent <= TRIM_MAX_PERCENT)) throw new Error(`절사평균에서 자를 몫은 0~${TRIM_MAX_PERCENT}(%) 사이 숫자로 적거나 비워 두세요.`);
    const window = f.fWin === "" ? null : { n: num(f.fWin), unit: f.fUnit }, trim = trimText ? { trim: trimPercent / 100 } : {};
    const analysis = byStages ? { kind: "stages", who: f.fUser, time: f.fTime, stages: stagesOf(f.fStages), count: f.fCount, window, ...trim }
      : { kind: "funnel", who: f.fUser, step: f.fStep, time: f.fTime, steps, window, ...trim };
    return { kind: k, title: f.title.trim() || `${f.table} 퍼널`, colors: freshColors(), view: { type: "", ratio: 4 / 3, gap_stats: ["median"] } as unknown as Card["view"], data: {},
      source: { schema: "", table: f.table, recipe: { schema: "", base: f.table }, full: true, date: f.fTime, analysis } };
  }
  if (k === "chart" && !f.cX) throw new Error("가로축 컬럼을 고르세요.");
  const empty = { schema: "", base: baseName, joins: [], periods: [], conditions: [], calcs: [], sorts: [], columns: [], series: [] as unknown[] };
  if (k === "sankey" || k === "cohort") {  // 흐름·코호트는 사용자·시각(생키는 단계도)을 source.analysis로(서버는 이것이 없으면 거부한다)
    need(f.fUser, "사용자 컬럼");
    need(f.fTime, "시각 컬럼");
    const auto = k === "sankey" && f.fFlow === "auto", steps = f.fSteps.split(",").map(t => t.trim()).filter(Boolean);
    if (auto) need(f.fStep, "단계 컬럼");
    else {
      if (k === "sankey" && (!f.fStep || steps.length < 2 || steps.length > 10)) throw new Error("단계 컬럼을 고르고 단계는 2~10개를 쉼표로 나눠 적어 주세요.");
      if (k === "sankey" && f.fWin !== "" && !(num(f.fWin) > 0)) throw new Error("기간 제한은 0보다 큰 숫자이거나 비워 두세요.");
    }
    const depth = f.fDepth.trim() ? Number(f.fDepth) : null;
    if (auto && depth !== null && !Number.isInteger(depth)) throw new Error("단계 깊이는 정수로 적거나 비워 두세요.");
    const analysis = auto ? { user: f.fUser, step: f.fStep, time: f.fTime, mode: "auto", starts: f.fStarts, middles: f.fMiddles, ends: f.fEnds, ...(depth !== null ? { depth } : {}) }
      : k === "sankey" ? { user: f.fUser, step: f.fStep, time: f.fTime, steps, ...(f.fWin === "" ? {} : { window: { n: num(f.fWin), unit: f.fUnit } }) } : { user: f.fUser, time: f.fTime };
    const flowView = auto && f.fDropout ? { flow_dropout: f.fDropout === "on" } : {};
    return { kind: k, title: f.title.trim() || `${baseName} ${kindName}`, colors: freshColors(), view: { type: "", ratio: 4 / 3, ...(k === "cohort" ? { unit: f.cUnit } : {}), ...flowView } as unknown as Card["view"], data: {},
      source: { schema: "", table: baseName, body: { ...empty, conditions: condLines(f.fConds) } as unknown as Body, date: f.fTime, analysis } };
  }
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
  const body = { ...empty, series: byMetric ? [] : series, ...(k === "gauge" ? { x: f.group } : {}), ...(k === "chart" ? { x: f.cX, order: "auto" as const } : {}), ...(byMetric ? { names: [f.metric] } : {}) };
  const view: Record<string, unknown> = { type: "", ratio: 4 / 3, format: f.format, icon: f.icon || "chart", compare: { mode: f.compare || "span" }, better: f.better || "up" };
  if (byMetric && f.format === "number") delete view.format;  // 형식을 따로 고르지 않은 지표 카드는 지표 파일의 형식을 쓴다(F22: 지표마다 단위가 다르다)
  if (f.unitText.trim() && ["kpi", "donut_kpi", "stripes", "bullet", "calheat"].includes(k)) view.unit_text = f.unitText.trim();
  if (f.targetValue !== "") view.target = { value: num(f.targetValue) };
  else if (f.targetName) view.target = f.targetKeep?.name === f.targetName ? { ...f.targetKeep } : { name: f.targetName };
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
  // P14 새 종류(D2 입력 규칙): 가로축·나누기는 body.x·body.split, 첫 값 줄이 series[0]
  if (k === "histogram") { Object.assign(body, { x: need(series[0]?.y ?? "", "값 줄의 숫자 컬럼") }); view.bins = Math.min(maxBins, Math.max(1, Math.round(num(f.bins) || 20))); }
  if (k === "boxplot") { need(series[0]?.y ?? "", "값 줄의 숫자 컬럼"); if (f.group) Object.assign(body, { x: f.group }); }
  if (k === "candle") need(series[0]?.y ?? "", "값 줄의 숫자 컬럼");
  if (k === "heatmap") Object.assign(body, { x: need(f.group, "행 컬럼"), split: need(f.split, "열 컬럼") });
  if (k === "treemap" || k === "sunburst") Object.assign(body, { x: need(f.group, "윗단계 컬럼"), ...(f.split ? { split: f.split } : {}) });
  if (k === "wordcloud") Object.assign(body, { x: need(f.group, "글자 컬럼") });
  if (DATE_X.includes(k)) Object.assign(body, { x: need(f.date, "날짜 컬럼") });
  if (k === "bump") Object.assign(body, { split: need(f.group, "대상 컬럼") });
  if (k === "bump" || k === "candle") view.unit = f.cUnit;
  if (k === "bullet" && f.bands.trim()) view.bands = parseBands(f.bands, maxBands);
  if (k === "deviation") {
    if (!f.entity || !f.date) throw new Error("편차 표는 대상 컬럼과 날짜 컬럼을 골라야 해요.");
    Object.assign(view, { entity: f.entity, bucket: f.bucket });
    if (f.baseline !== "") view.baseline = num(f.baseline);
    if (f.threshold !== "") view.threshold = num(f.threshold);
  }
  return { kind: k, title: f.title.trim() || `${baseName} ${kindName}`, colors: freshColors(), view: view as unknown as Card["view"], data: {},
    source: { schema: "", table: baseName, body: body as unknown as Body, ...(f.date && (WITH_DATE.includes(k) || DATE_X.includes(k) || k === "chart") ? { date: f.date } : {}) } };  // 차트도 기간 기준 날짜를 쓴다(서버 compute)
}

// ======================= Phase 3: 조건 단계 퍼널(AARRR·RARRA 틀)·생키 조건 줄 =======================
/** 단계 편집 칸 한 줄(폼 글자 그대로): 이름, 조건 줄(여러 줄 글), 재방문 N일(비우면 재방문 단계 아님) */
export type StageRow = { name: string; conds: string; revisit: string };
/** 서버로 가는 조건 단계(core/funnel.check_stages가 받는 모양) */
export type Stage = { name: string; conds: string[]; revisit?: number };
/** 틀 이름 → 단계 이름 차례와 재방문 단계(유지)의 N일 기본값(이름·개수는 사용자가 바꿀 수 있다) */
export const FUNNEL_TEMPLATES: Record<"aarrr" | "rarra", { label: string; names: string[] }> = {
  aarrr: { label: "AARRR", names: ["획득 (Acquisition)", "활성 (Activation)", "유지 (Retention)", "추천 (Referral)", "수익 (Revenue)"] },
  rarra: { label: "RARRA", names: ["유지 (Retention)", "활성 (Activation)", "추천 (Referral)", "수익 (Revenue)", "획득 (Acquisition)"] },
};
export const RETENTION_NAME = "유지 (Retention)";  // 틀에서 재방문 칸을 미리 채우는 단계 이름
export const REVISIT_DAYS = 7;  // 틀의 유지 단계 재방문 N일 기본값(서버 상한은 funnel.MAX_REVISIT_DAYS)
export const REVISIT_CHOICES = [1, 3, 7, 14, 30];  // 재방문 N일 고르는 목록(그 밖은 직접 입력)
export const TRIM_MAX_PERCENT = 50;  // 절사평균에서 자르는 몫의 상한(%, 서버 funnel.MAX_TRIM 0.5와 같다)
export const MAX_STAGES = 10;  // 단계 수 상한(서버 funnel.MAX_STEPS와 같다)
// [SPECIFICATION] plans/phase-3.md#templateStages
export function templateStages(name: keyof typeof FUNNEL_TEMPLATES): StageRow[] {
  return FUNNEL_TEMPLATES[name].names.map(n => ({ name: n, conds: "", revisit: n === RETENTION_NAME ? String(REVISIT_DAYS) : "" }));
}
// [SPECIFICATION] plans/phase-3.md#stagesOf
export function stagesOf(rows: StageRow[]): Stage[] {
  if (rows.length < 1 || rows.length > MAX_STAGES) throw new Error(`단계는 1~${MAX_STAGES}개예요.`);
  const out = rows.map((r): Stage => {
    const name = r.name.trim();
    if (!name) throw new Error("단계 이름을 적어 주세요.");
    const conds = condLines(r.conds), text = r.revisit.trim(), revisit = text ? Number(text) : 0;
    if (text && !(Number.isInteger(revisit) && revisit >= 1)) throw new Error(`${name}: 재방문 일수는 1 이상의 정수예요.`);
    if (!conds.length && !revisit) throw new Error(`${name}: 조건 줄을 적거나 재방문 일수를 정해 주세요.`);
    return { name, conds, ...(revisit ? { revisit } : {}) };
  });
  const dup = out.find((s, i) => out.findIndex(o => o.name === s.name) !== i);
  if (dup) throw new Error(`같은 이름의 단계가 있어요: ${dup.name}`);
  return out;
}
// [SPECIFICATION] plans/phase-3.md#rowsOf
export function rowsOf(stages: Stage[]): StageRow[] {
  return stages.map(s => ({ name: s.name, conds: s.conds.map(c => typeof c === "string" ? c : (c as { line?: string }).line ?? "").join("\n"), revisit: s.revisit ? String(s.revisit) : "" }));
}
// [SPECIFICATION] plans/phase-3.md#condLines
export function condLines(text: string): string[] {
  return text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
}
