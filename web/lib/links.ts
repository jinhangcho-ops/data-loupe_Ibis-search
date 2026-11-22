// B2+B4 카드끼리 링크(plans/dash-blocks.md "B2+B4 세부 명세 (2차, 확정)"): 보내는 쪽(view.link_to·link_map)에서 받는 카드를 고르고,
// 누른 것(Press)·요소 값(ElemState)을 카드마다의 필터·기간·단위·보기로 바꾸는 순수 함수들(DOM 없음). 서버의 한 벌은 core/dash_store.py SENDER_KINDS·link_defaults.
import type { Card, CardData, Dashboard, PickedFilter, Unit } from "@/lib/types";
import { isFrame } from "@/components/frame/registry";
import { sectionsOf } from "@/components/grid/sections";
import { onPage } from "@/lib/calc";

/** 링크를 보내는 틀 요소(서버 dash_store.LINK_FRAMES와 같은 차례) */
export const SENDER_FRAMES: readonly string[] = ["segment", "chip_row", "period_pick", "asof_field", "unit_seg", "top_strip"];
/** 위쪽 화면 줄에서 기간 요소로 치는 칸 */
export const PERIOD_PARTS: readonly string[] = ["period", "asof"];

/** 옛 대시보드에서 link_to 없이 모든 데이터 카드로 치는 기간 요소(서버 dash_store.PERIOD_FRAMES) */
const PERIOD_ELEMENTS: readonly string[] = ["period_pick", "asof_field", "unit_seg"];

/** 조건 한 줄(서버 parse.COND_RE와 같은 식): 컬럼, 연산자, 값 */
const COND_RE = /^(\S+?)\s*(!=|=~|>=|<=|=|>|<)\s*(.+)$/;
/** 줄 안에 앞뒤가 공백인 낱말 or */
const OR_WORD_RE = /\sor\s/i;
/** 값 양끝을 감싼 같은 따옴표 */
const QUOTED_RE = /^(['"])(.*)\1$/;
/** 서버 parse.py와 같은 조건 꼴 식들 */
const BETWEEN_RE = /^(\S+)\s+(not\s+)?between\s+(\S+)\s+(?:and\s+)?(\S+)$/i;
const IN_RE = /^(\S+)\s+(in|except)\s+(.+)$/i;
const TUPLE_RE = /^\s*\(([\s\S]*?)\)\s+(in|except)\s+(\([\s\S]*)$/i;
const LIKE_RE = /^(\S+)\s+(not\s+like|like|starts|matches|ends)\s+(.+)$/i;
const NULL_RE = /^(\S+)\s+is\s+(not\s+)?null$/i;
const GEO_RE = /^(near|box)\s+(.+)$/i;
/** 좌표 조건의 낱말 수 */
const GEO_COUNTS: Record<string, number> = { near: 5, box: 6 };
/** 기간 줄: 컬럼, 나머지 */
const PERIOD_LINE_RE = /^(\S+)\s+(.+)$/;
/** 줄을 or로 나눌 때 따옴표로 감싼 값(묶음 1)은 나누지 않는다 */
const OR_SPLIT_RE = /('[^']*'|"[^"]*")|\sor\s/gi;
const OR_JOIN = " 또는 ";
const SMALL_JOIN = ", ";
/** 한글 글자 범위와 받침 계산(받침 수 28, ㄹ 받침은 8번) */
const HANGUL_FIRST = 0xac00;
const HANGUL_LAST = 0xd7a3;
const BATCHIM_COUNT = 28;
const BATCHIM_RIEUL = 8;
/** 연산자별 문구(크기 비교) */
const COMPARE_TEXT: Record<string, string> = { ">=": "보다 크거나 같을 때", ">": "보다 클 때", "<": "보다 작을 때", "<=": "보다 작거나 같을 때" };

/** 타일 필터(ctx.filters 항목): 타일의 조회 그대로, 맞춘 컬럼(보내는 쪽 → 상대), 기간 줄도 거는지 */
export type TileFilter = { body: Record<string, unknown>; as?: Record<string, string>; periods?: boolean };
/** 카드를 구할 때 보내는 필터 하나: 값 필터 또는 타일 필터 */
export type CtxFilter = PickedFilter | TileFilter;
/** 보낸 쪽(카드·요소 id) 하나가 누른 것 하나(저장하지 않는다): 값(차트 막대·칩·값 세그먼트) 또는 타일 */
export type Press = { from: string; value?: PickedFilter; tile?: { body: Record<string, unknown>; periods: boolean } };
/** 요소 하나의 화면 값(저장하지 않는다). at이 클수록 나중에 바꾼 것 */
export type ElemState = { at: number; period?: string; as_of?: string; unit?: Unit | null; cell?: number; notes?: { asof?: { text: string; bad?: boolean }; per?: { text: string; bad?: boolean } } };
/** 보기 바꾸기 세그먼트가 상대 카드에 거는 보기: 보일 계열 이름("" = 모든 계열), 비율로 */
export type SeriesView = { series: string; share: boolean };

/** [SPECIFICATION] plans/dash-blocks.md#isSender
 *  이 종류가 링크를 보내는지(서버 SENDER_KINDS와 같다). */
export function isSender(kind: string): boolean {
  return kind === "chart" || kind === "tile" || kind === "list" || SENDER_FRAMES.includes(kind);
}

/** [SPECIFICATION] plans/dash-blocks.md#linkTargets
 *  보내는 쪽이 링크한 카드 id들(저장된 목록, 없으면 옛 대시보드 규칙). */
export function linkTargets(sender: Pick<Card, "id" | "kind" | "view">, cards: Card[]): string[] {
  const { link_to, cross, frame } = sender.view ?? {};
  if (Array.isArray(link_to)) return link_to;
  const old = (sender.kind === "chart" && cross === true) || PERIOD_ELEMENTS.includes(sender.kind)
    || (sender.kind === "top_strip" && PERIOD_PARTS.some(p => (frame?.parts as string[] | undefined)?.includes(p)));
  return old ? cards.filter(c => !isFrame(c.kind)).map(c => c.id) : [];
}

/** [SPECIFICATION] plans/dash-blocks.md#freshLinks
 *  막 놓은 보내는 쪽의 처음 상대: 그 쪽의 데이터 카드 id들(차트는 끝에 자기 자신도). */
export function freshLinks(sender: Pick<Card, "id" | "kind">, dash: Pick<Dashboard, "cards" | "sections" | "pages">, page: string): string[] {
  const { sections, cards } = sectionsOf(dash);
  const secs = onPage(sections, dash.pages, page);
  const found = cards.filter(c => !isFrame(c.kind) && c.id !== sender.id && secs.some(s => s.id === c.section)).map(c => c.id);
  return sender.kind === "chart" ? [...found, sender.id] : found;
}

/** [SPECIFICATION] plans/dash-blocks.md#missingTargets
 *  저장된 상대 가운데 대시보드에 없는 카드 id들. */
export function missingTargets(sender: Pick<Card, "view">, cards: Card[]): string[] {
  return (sender.view.link_to ?? []).filter(id => !cards.some(c => c.id === id));
}

/** [SPECIFICATION] plans/dash-blocks.md#filtersFor
 *  카드(또는 칩 줄) 하나가 받는 누른 것들을 그 카드 쪽 컬럼으로 맞춘 필터 목록. */
export function filtersFor(id: string, presses: Press[], cards: Card[]): CtxFilter[] {
  return presses.flatMap((p): CtxFilter[] => {
    const s = cards.find(c => c.id === p.from);
    if (!s || !linkTargets(s, cards).includes(id)) return [];
    const m = s.view.link_map?.[id] ?? {};
    if (p.value) return [{ column: m[p.value.column] ?? p.value.column, in: p.value.in }];
    if (p.tile) return [{ body: p.tile.body, ...(Object.keys(m).length ? { as: m } : {}), ...(p.tile.periods ? { periods: true } : {}) }];
    return [];
  });
}

/** [SPECIFICATION] plans/dash-blocks.md#fieldsFor
 *  카드 하나에 링크된 기간·기준일·단위 요소가 바꾼 값(마지막에 바꾼 요소의 것, 없으면 키 없음). */
export function fieldsFor(id: string, cards: Card[], elems: Record<string, ElemState>): { period?: string; as_of?: string; unit?: Unit | null } {
  const out: { period?: string; as_of?: string; unit?: Unit | null } = {};
  const at: Record<string, number> = {};
  const take = (key: "period" | "as_of" | "unit", s: ElemState, value: string | Unit | null | undefined): void => {
    if (value === undefined || (key in at && at[key] >= s.at)) return;
    at[key] = s.at;
    (out as Record<string, unknown>)[key] = value;
  };
  for (const e of cards) {
    const s = elems[e.id];
    if (!s || !(e.kind === "top_strip" || PERIOD_ELEMENTS.includes(e.kind)) || !linkTargets(e, cards).includes(id)) continue;
    const parts = (e.view.frame?.parts as string[] | undefined) ?? [];
    if (e.kind === "period_pick" || (e.kind === "top_strip" && parts.includes("period"))) take("period", s, s.period);
    if (e.kind === "asof_field" || (e.kind === "top_strip" && parts.includes("asof"))) take("as_of", s, s.as_of);
    if (e.kind === "unit_seg") take("unit", s, s.unit);
  }
  return out;
}

/** [SPECIFICATION] plans/dash-blocks.md#onlySeries
 *  구한 값에서 이름이 name인 계열만 남긴 사본(name이 ""이면 그대로). */
export function onlySeries(data: CardData, name: string): CardData {
  if (name === "") return data;
  const keep = <T extends { series?: unknown }>(o: T): T =>
    Array.isArray(o.series) ? { ...o, series: o.series.filter((s: { name: string }) => s.name === name) } : o;
  return {
    ...data,
    ...(data.values ? { values: keep(data.values) } : {}),
    ...(data.by_unit ? { by_unit: Object.fromEntries(Object.entries(data.by_unit).map(([k, v]) => [k, keep(v as NonNullable<typeof v>)])) as CardData["by_unit"] } : {}),
  };
}

/** [SPECIFICATION] plans/dash-blocks.md#tileBig
 *  타일 꼬리표의 큰 글자: '=' 조건의 값들 + 타일 이름. */
export function tileBig(title: string, conds: unknown[]): string {
  const values = conds
    .map(c => (typeof c === "string" ? c : typeof (c as { line?: unknown } | null)?.line === "string" ? (c as { line: string }).line : ""))
    .filter(line => !OR_WORD_RE.test(line))
    .map(line => COND_RE.exec(line))
    .filter((m): m is RegExpExecArray => m !== null && m[2] === "=")
    .map(m => m[3].trim().replace(QUOTED_RE, "$2"));
  return [...values, title].join(" ");
}

/** [SPECIFICATION] plans/dash-blocks.md#tileSmall
 *  타일 꼬리표의 작은 글씨: 큰 글자로 안 간 조건과 기간 줄을 문구 표대로 풀어 ( )로 감싼 글. */
export function tileSmall(conds: unknown[], lines: string[]): string {
  const lineOf = (c: unknown): string =>
    typeof c === "string" ? c : typeof (c as { line?: unknown } | null)?.line === "string" ? (c as { line: string }).line : "";
  const isBig = (line: string): boolean => !OR_WORD_RE.test(line) && COND_RE.exec(line)?.[2] === "=";
  const small = conds.flatMap((c): string[] => {
    const line = lineOf(c);
    if (line) return isBig(line) ? [] : [orPhrase(line)];
    const o = c as { any?: unknown; column?: unknown; in?: unknown } | null;
    if (Array.isArray(o?.any)) return [o.any.map(lineOf).filter(l => l !== "").map(orPhrase).join(OR_JOIN)];
    if (typeof o?.column === "string" && Array.isArray(o.in)) return [phrase(`${o.column} in ${o.in.join(SMALL_JOIN)}`)];
    return [];
  });
  const periods = lines.map(line => {
    const m = PERIOD_LINE_RE.exec(line.trim());
    return m ? `${subject(m[1])} ${m[2]}일 때` : line;
  });
  const parts = [...small, ...periods];
  return parts.length ? `(${parts.join(SMALL_JOIN)})` : "";
}

/** 마지막 글자의 한글 받침 번호(받침 없음 0, 한글이 아니면 -1) */
function batchim(word: string): number {
  const code = word.charCodeAt(word.length - 1);
  return code >= HANGUL_FIRST && code <= HANGUL_LAST ? (code - HANGUL_FIRST) % BATCHIM_COUNT : -1;
}

/** 이/가를 붙인 글(받침 있으면 이, 없거나 한글이 아니면 가) */
function subject(word: string): string {
  return word + (batchim(word) > 0 ? "이" : "가");
}

/** (으)로를 붙인 글(ㄹ 아닌 한글 받침이면 으로, 그 밖은 로) */
function by(word: string): string {
  return word + (batchim(word) > 0 && batchim(word) !== BATCHIM_RIEUL ? "으로" : "로");
}

/** 값 양끝을 감싼 같은 따옴표를 뗀 값 */
function bare(value: string): string {
  return value.trim().replace(QUOTED_RE, "$2");
}

/** 줄 안의 낱말 or로 나눠 각각 풀어 " 또는 "로 이은 문구 */
function orPhrase(line: string): string {
  const parts: string[] = [];
  let last = 0;
  for (const m of line.matchAll(OR_SPLIT_RE)) {
    if (m[1] !== undefined) continue;
    parts.push(line.slice(last, m.index));
    last = m.index + m[0].length;
  }
  parts.push(line.slice(last));
  return parts.map(phrase).join(OR_JOIN);
}

/** 조건 한 줄(or 없음)의 문구. 모르는 꼴은 줄 그대로 */
function phrase(raw: string): string {
  const line = raw.trim();
  const tuple = TUPLE_RE.exec(line);
  if (tuple) {
    const cols = tuple[1].split(",").map(s => s.trim()).join("·");
    return `${subject(cols)} ${tuple[3]}${tuple[2].toLowerCase() === "in" ? "일 때" : " 아닐 때"}`;
  }
  const geo = GEO_RE.exec(line);
  const w = geo ? geo[2].split(/\s+/) : [];
  if (geo && w.length === GEO_COUNTS[geo[1].toLowerCase()]) {
    const at = subject(`${w[0]}·${w[1]}`);
    return geo[1].toLowerCase() === "near" ? `${at} (${w[2]}, ${w[3]})에서 ${w[4]}km 안일 때` : `${at} 상자 (${w.slice(2).join(SMALL_JOIN)}) 안일 때`;
  }
  const empty = NULL_RE.exec(line);
  if (empty) {
    const not = empty[2] !== undefined;
    if (empty[1] === "*") return not ? "모든 컬럼에 값이 있을 때" : "빈 컬럼이 있을 때";
    return `${subject(empty[1])} ${not ? "비어 있지 않을 때" : "비어 있을 때"}`;
  }
  const between = BETWEEN_RE.exec(line);
  if (between) return `${between[3]}~${between[4]} 사이${between[2] ? " X" : ""}`;
  const within = IN_RE.exec(line);
  if (within) return within[2].toLowerCase() === "in" ? `${bare(within[3])} 중 하나` : `${bare(within[3])} X`;
  const like = LIKE_RE.exec(line);
  if (like) {
    const op = like[2].toLowerCase().replace(/\s+/g, " ");
    const v = bare(like[3]);
    if (op === "like") return `${subject(v)} 들어간 값`;
    if (op === "not like") return `${subject(v)} 들어간 값 X`;
    if (op === "starts") return `${by(v)} 시작하는 값`;
    if (op === "ends") return `${by(v)} 끝나는 값`;
    return `${subject(like[1])} 정규식 ${v}에 맞을 때`;
  }
  const cmp = COND_RE.exec(line);
  if (!cmp) return line;
  const v = bare(cmp[3]);
  if (cmp[2] in COMPARE_TEXT) return `${subject(cmp[1])} ${v} ${COMPARE_TEXT[cmp[2]]}`;
  if (cmp[2] === "!=") return `${v} X`;
  if (cmp[2] === "=~") return `${subject(cmp[1])} ${v}일 때(대소문자 무시)`;
  return v;
}

/** [SPECIFICATION] plans/dash-blocks.md#pressLabel
 *  누른 것 하나의 꼬리표 글(값은 "컬럼 = 값", 타일은 tileBig). */
export function pressLabel(p: Press, cards: Card[]): string {
  if (p.value) return `${p.value.column} = ${p.value.in.join(SMALL_JOIN)}`;
  return tileBig(cards.find(c => c.id === p.from)?.title ?? "", (p.tile?.body.conditions as unknown[] | undefined) ?? []);
}
