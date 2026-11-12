// 카드 추가 메뉴·카드 모음의 종류 표(옛 dash_menu.js CARD_KINDS·dash_panel.js galleryItems·shellOf)와 순수 보조 함수. 화면 없이 계산한다.
import type { Card, CardKind, Colors } from "@/lib/types";

/** [이름, 값 줄 최소, 최대, 설명] */
export const CARD_KINDS: Partial<Record<CardKind, [string, number, number, string]>> = {
  chart: ["차트", 1, 5, "막대, 라인, 도넛, 산점도 같은 그래프"], kpi: ["KPI", 1, 1, "큰 숫자 하나와 지난 기간 비교, 작은 추이선"], donut_kpi: ["도넛 KPI", 1, 5, "가운데 값과 네 모서리 값을 고리로"],
  gauge: ["게이지", 1, 1, "항목마다 차지하는 몫"], summary: ["요약 문장", 1, 1, "목표와 비교한 한 줄 문장"], recent_rows: ["최근 행", 0, 0, "테이블의 앞 몇 행. 연결했을 때만 보여요"],
  dual_trend: ["두 계열 추이", 2, 2, "두 값을 한 그래프에 겹쳐서"], stripes: ["칸 채우기", 1, 1, "목표까지 몇 칸 찼는지"], blocks: ["블록", 1, 1, "양을 블록 개수로"],
  target_trend: ["목표 추이", 1, 1, "날마다 값과 목표선"], table: ["표", 0, 5, "집계한 값이나 원래 행"], sentence: ["문장", 0, 0, "직접 쓴 문장의 빈칸에 값, 비교, 기준일을 넣어요"],
  status: ["상태", 0, 0, "정상, 주의, 이상인 대상 수"], alerts: ["경고 목록", 0, 0, "문제가 있는 대상과 얼마나 됐는지"], map: ["배치도", 0, 0, "대상을 점으로 놓고 끌어서 옮겨요"],
  deviation: ["편차 표", 1, 1, "대상별 시간대 값과 기준의 차이"], funnel: ["퍼널", 0, 0, "단계마다 몇 명이 도달했는지"], geomap: ["지도", 0, 0, "경계와 점, 영역별 색"],
  // P14 새 종류(서버 KINDS, 입력 규칙은 build.ts)
  histogram: ["히스토그램", 1, 1, "숫자 값이 구간마다 몇 개인지"], boxplot: ["상자 그림", 1, 1, "그룹마다 값의 퍼짐(사분위, 이상치)"], heatmap: ["히트맵", 1, 1, "두 컬럼의 값 조합마다 색 칸"],
  calheat: ["달력 히트맵", 1, 1, "날마다 값을 달력 칸 색으로"], treemap: ["트리맵", 1, 1, "항목 크기를 사각형 넓이로"], sunburst: ["선버스트", 1, 1, "두 단계 항목을 겹친 고리로"],
  bullet: ["불릿", 1, 1, "값과 목표, 구간을 한 막대에"], bump: ["순위 변화", 1, 1, "기간마다 대상 순위가 어떻게 바뀌는지"], sankey: ["생키", 0, 0, "단계 사이를 오간 사람 수"],
  cohort: ["코호트", 0, 0, "처음 온 기간별로 다시 온 비율"], candle: ["캔들", 1, 1, "기간마다 시작, 최고, 최저, 끝 값"], wordcloud: ["워드 클라우드", 0, 0, "자주 나온 값을 크게"],
};
/** 날짜 컬럼이 가로축(body.x)이나 시각인 새 종류: 그 컬럼이 곧 기간 기준 날짜 */
export const DATE_X: CardKind[] = ["calheat", "bump", "candle"];
/** 대시보드 기간이 바뀌어 서버에서 다시 구할 카드: 기간 기준 날짜(source.date, 날짜가 가로축인 종류는 없으면 body.x)가 있고 화면이 기간으로 묶을 일 값(by_unit)이 없는 카드(최근 행 제외).
 *  기간이 없으면 전에 기간이 있었을 때(had)만(지운 기간을 원래 값으로) */
export const datedCard = (card: Card, range: { start?: string | null; end?: string | null } | null | undefined, had: boolean) =>
  !!(range || had) && !!(card.source?.date || (DATE_X.includes(card.kind) && card.source?.body?.x)) && !card.data?.by_unit && card.kind !== "recent_rows";
/** 엔티티별 최근 행을 상태 규칙으로 보는 카드 */
export const WATCH: CardKind[] = ["status", "alerts", "map"];
/** 기간 기준 날짜 컬럼을 거는 종류(흐름은 시각 컬럼이 그 날짜 컬럼) */
export const WITH_DATE = (Object.keys(CARD_KINDS) as CardKind[]).filter(k => !["recent_rows", "funnel", "chart", "sankey", "cohort", ...DATE_X, ...WATCH].includes(k));
export const KIND_NAMES: Record<string, string> = { ...Object.fromEntries(Object.entries(CARD_KINDS).map(([k, v]) => [k, v![0]])), analysis: "분석", metric: "지표" };
export const CARD_MIME = "application/x-dash-card";

export const freshColors = (): Colors => ({ series: {}, parts: {}, order: [], parts_order: [] });
/** 빈 틀 카드: 다시 구할 기록(source)이 아직 없다(옛 dash_edit.js isShell) */
export const isShell = (c: Card) => !(c.source && (c.source.body || c.source.recipe || c.source.metric_id || c.source.flow_id));
/** 새 카드의 view 기본값: 감시 카드는 상태 규칙·연결선 칸을 빈 목록으로 둔다(규칙 편집이 그 칸을 보인다) */
export const freshView = (kind: CardKind, type = "") => ({ type, ...(WATCH.includes(kind) ? { rules: [], links: [] } : {}) }) as Card["view"];
export const colsOfType = (cols: [string, string][], types: string[]) => cols.filter(c => types.includes(c[1])).map(c => c[0]);
/** 서버 /api/columns가 주는 날짜만 있는 타입의 한글 이름 */
const DATE_ONLY = "날짜";
/** [SPECIFICATION] plans/phase-15.md#unitForDate
 *  고른 날짜 컬럼이 날짜만 있는 타입이면 "day", 아니면 timed(시각이 있는 컬럼의 기본 단위)를 돌려준다. */
export function unitForDate(cols: [string, string][], column: string, timed: string): string {
  return cols.some(([n, t]) => n === column && t === DATE_ONLY) ? "day" : timed;
}

export type Spec = { group: string; kind: CardKind; type?: string; name: string; help: string };
/** 카드 모음의 칸들: 차트는 종류마다 한 칸 */
export function galleryItems(types: Record<string, string>): Spec[] {
  const charts = Object.entries(types).map(([type, name]) => ({ group: "차트", kind: "chart" as CardKind, type, name, help: type === "scatter" || type === "bubble" ? "가로축과 값 하나로 점을 찍어요" : "가로축 컬럼과 값 최대 5개" }));
  const some = (group: string, list: [CardKind, string, string][]) => list.map(([kind, name, help]) => ({ group, kind, name, help }));
  return [...charts,
    ...some("값·목표", [["kpi", "KPI", "큰 숫자와 비교"], ["donut_kpi", "도넛 KPI", "가운데 값과 모서리 값"], ["gauge", "게이지", "항목마다 몫"], ["summary", "요약 문장", "목표와 비교한 문장"],
      ["stripes", "칸 채우기", "목표까지 찬 칸"], ["blocks", "블록", "양을 블록으로"], ["target_trend", "목표 추이", "날마다 값과 목표선"], ["dual_trend", "두 계열 추이", "두 값을 겹쳐서"]]),
    ...some("표·글", [["table", "표", "집계 또는 원래 행"], ["sentence", "문장", "빈칸에 값과 비교"], ["recent_rows", "최근 행", "연결했을 때만 보여요"]]),
    ...some("모니터링", [["status", "상태", "정상, 주의, 이상 수"], ["alerts", "경고 목록", "문제와 지속 시간"], ["map", "배치도", "끌어 옮기는 점"], ["deviation", "편차 표", "대상별 시간대 값"]]),
    ...some("분포·구성", [["histogram", "히스토그램", "값의 구간별 개수"], ["boxplot", "상자 그림", "그룹별 퍼짐"], ["heatmap", "히트맵", "두 컬럼 조합의 색 칸"], ["calheat", "달력 히트맵", "날마다 색 칸"],
      ["treemap", "트리맵", "넓이로 비중"], ["sunburst", "선버스트", "두 단계 고리"], ["wordcloud", "워드 클라우드", "자주 나온 값"]]),
    ...some("변화·비교", [["bullet", "불릿", "값, 목표, 구간"], ["bump", "순위 변화", "기간별 순위"], ["candle", "캔들", "시작·최고·최저·끝"]]),
    ...some("흐름", [["funnel", "퍼널", "단계마다 도달한 사람"], ["sankey", "생키", "단계 사이 흐름의 굵기"], ["cohort", "코호트", "시작 시기별 유지율"]]),  // 빈 틀로 놓고 사용자·단계·시각은 설정에서 정한다(서버 check_card placed)
    ...some("지도", [["geomap", "지도", "경계와 점, 영역별 색"]])];
}

/** 카드 추가 서랍의 첫 묶음 이름(카드 모음 galleryItems의 차트 묶음과 같은 이름) */
const CHART_GROUP = "차트";
/** [SPECIFICATION] plans/phase-16.md#kindGroups
 *  카드 추가 서랍의 종류 단추 묶음: [묶음 이름, 종류들]을 카드 모음(galleryItems)의 묶음 차례로, 맨 앞은 차트. */
export function kindGroups(): [string, CardKind[]][] {
  const groups = new Map<string, CardKind[]>([[CHART_GROUP, ["chart"]]]);
  for (const s of galleryItems({})) {
    const list = groups.get(s.group) ?? [];
    if (!list.includes(s.kind)) list.push(s.kind);
    groups.set(s.group, list);
  }
  const all = [...groups.values()].flat();
  const keys = Object.keys(CARD_KINDS);
  const off = [...keys.filter(k => !all.includes(k as CardKind)), ...all.filter((k, i) => !keys.includes(k) || all.indexOf(k) !== i)];
  if (off.length) throw new Error(`카드 종류 묶음이 CARD_KINDS와 맞지 않아요: ${off.join(", ")}`);
  return [...groups];
}

/** 빈 틀 카드(source 없음): 카드 모음이 만든다. 데이터는 설정 패널의 [데이터]에서 정한다 */
export const shellOf = (spec: Spec): Partial<Card> => ({ kind: spec.kind, title: spec.name, colors: freshColors(), view: freshView(spec.kind, spec.type), source: {}, data: {} });

// ---------- 문장 빈칸 줄(옛 dash_rules.js parseBlankLine) ----------
const BLANK_WORDS: Record<string, string> = { 값: "value", 직전: "prev", 비교: "delta", 기준일: "asof" };
const FORMAT_WORDS: Record<string, string> = { 숫자: "number", "%": "percent", 금액: "currency", "₩": "currency", number: "number", percent: "percent", currency: "currency" };
/** 빈칸 하나 → 한 줄 글(parseBlankLine의 반대). 컬럼이 없으면(개수) `이름 = 종류`만 */
export function blankLine(b: { name: string; kind: string; y?: string; agg?: string; format?: string }) {
  const word = (table: Record<string, string>, v?: string) => Object.keys(table).find(k => table[k] === v) ?? v ?? "";
  return [b.name, "=", word(BLANK_WORDS, b.kind), ...(b.y ? [b.y, b.agg ?? "sum", word(FORMAT_WORDS, b.format ?? "number")] : [])].join(" ");
}
/** `이름 = 종류(값·직전·비교·기준일) [컬럼 [집계 [모양]]]` → { blank } 또는 { error } */
export function parseBlankLine(line: string, aggs: string[]): { blank?: Record<string, string>; error?: string } {
  const m = line.trim().match(/^(\S+)\s*=\s*(\S+)(?:\s+(\S+))?(?:\s+(\S+))?(?:\s+(\S+))?$/);
  if (!m) return { error: "‘이름 = 종류 [컬럼 [집계 [모양]]]’ 모양으로 적어 주세요. 예: 매출 = 값 amt sum 금액" };
  if (!(m[2] in BLANK_WORDS)) return { error: `종류는 값·직전·비교·기준일 중 하나예요(‘${m[2]}’)` };
  const agg = m[4] ?? (m[3] ? "sum" : "count");
  if (!aggs.includes(agg)) return { error: `집계는 ${aggs.slice(0, 6).join("·")} … 중 하나예요(‘${agg}’)` };
  if (m[5] && !(m[5] in FORMAT_WORDS)) return { error: `모양은 숫자·%·금액 중 하나예요(‘${m[5]}’)` };
  return { blank: { name: m[1], kind: BLANK_WORDS[m[2]], y: m[3] ?? "", agg, format: m[5] ? FORMAT_WORDS[m[5]] : "number" } };
}
