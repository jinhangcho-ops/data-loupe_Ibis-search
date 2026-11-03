// 차트 선반의 순수 계산(옛 static/core/dash_shelf.js): 컬럼을 칸에 놓을 수 있는지, 놓은 뒤의 상태, 칸의 칩 글자. 화면 없이 계산한다.
// 컬럼 종류는 /api/columns의 한글 타입이다: 정수·실수(숫자), 날짜·날짜시간, 문자, 참/거짓.
export type ShelfKey = "x" | "y" | "split" | "filter";
export type Series = { y: string | null; agg: string; opts?: unknown };
export type Cond = string | { line?: string; any?: (string | { line?: string })[] };
export type ShelfState = { x: string; series: Series[]; split: string; conditions: Cond[] };

export const SHELVES: Record<ShelfKey, string> = { x: "X축", y: "값", split: "색(나누기)", filter: "필터" };
export const SHELF_ROWS = "(행 개수)";  // 값 칸에 놓으면 개수를 세는 특별 칩
export const shelfKind = (type: string) => ["정수", "실수"].includes(type) ? "num" : ["날짜", "날짜시간"].includes(type) ? "date" : type === "참/거짓" ? "bool" : "text";

/** 칸에 놓을 수 있는지. 놓을 수 없으면 이유 문장 */
export function dropCheck(shelf: ShelfKey, column: string, type: string, state: ShelfState): { ok: boolean; reason: string } {
  const kind = column === SHELF_ROWS ? "rows" : shelfKind(type), no = (reason: string) => ({ ok: false, reason });
  if (!SHELVES[shelf]) return no("놓을 수 없는 칸이에요.");
  if (kind === "rows" && shelf !== "y") return no("(행 개수)는 [값] 칸에만 놓을 수 있어요.");
  if (shelf === "y") {
    if (kind === "text" || kind === "bool") return no(`‘${column}’은 문자 컬럼이라 합계·평균을 낼 수 없어요. 개수를 세려면 [값]에 (행 개수)를 놓고, 이 컬럼으로 나누려면 [색(나누기)]나 [X축]에 놓으세요.`);
    if (kind === "date") return no(`‘${column}’은 날짜 컬럼이라 값(합계·평균)이 될 수 없어요. [X축]에 놓으면 시간별로 볼 수 있어요.`);
    if (state.series.some(s => (kind === "rows" ? !s.y : s.y === column))) return no(`‘${column === SHELF_ROWS ? "행 개수" : column}’은 이미 값에 있어요. 칩을 눌러 합계·평균·개수를 바꾸세요.`);
    if (state.series.length >= 5) return no("값은 5개까지 넣을 수 있어요.");
  }
  if (shelf === "split") {
    if (kind === "num") return no(`‘${column}’은 숫자 컬럼이라 값의 종류가 너무 많아 색으로 나누기 어려워요. 문자 컬럼을 고르거나 구간을 나눈 컬럼을 만드세요.`);
    if (kind === "date") return no(`‘${column}’은 날짜 컬럼이라 색으로 나누면 값이 너무 많아져요. [X축]에 놓으세요.`);
    if (column === state.x) return no("X축과 같은 컬럼으로는 나눌 수 없어요.");
  }
  if (shelf === "x" && column === state.split) return no("색(나누기)과 같은 컬럼을 X축에 놓을 수 없어요. 나누기를 먼저 비우세요.");
  return { ok: true, reason: "" };
}

/** 놓은 결과의 새 상태(dropCheck를 통과한 뒤에). 필터는 조건 한 줄 `컬럼 연산자 값`을 conditions에 더한다 */
export function applyDrop(shelf: ShelfKey, column: string, type: string, state: ShelfState, value = "", op = "="): ShelfState {
  const next = { ...state, series: [...state.series], conditions: [...state.conditions] };
  if (shelf === "x") next.x = column;
  else if (shelf === "split") next.split = column;
  else if (shelf === "y") next.series.push(column === SHELF_ROWS ? { y: null, agg: "count" } : { y: column, agg: shelfKind(type) === "num" ? "sum" : "count" });
  else next.conditions.push(`${column} ${op} ${value}`);
  return next;
}

/** 값 칸의 집계를 바꾼다((행 개수) 칩은 바꾸지 않는다) */
export const setAgg = (series: Series[], i: number, agg: string) => series.map((x, j) => (j === i && x.y ? { y: x.y, agg } : x));

/** 칸의 현재 내용을 칩 글자로. 서버 모양 조건({line}·{any})도 글자로 */
export const shelfChips = (s: ShelfState): Record<ShelfKey, string[]> => ({
  x: s.x ? [s.x] : [],
  y: s.series.map(r => (r.y ? `${r.agg} (${r.y})` : "행 개수")),
  split: s.split ? [s.split] : [],
  filter: s.conditions.map(c => (typeof c === "string" ? c : c.line ?? (c.any ? c.any.map(x => (typeof x === "string" ? x : x.line ?? "")).join(" or ") : ""))),
});

/** 카드 body → 선반 상태 */
export const stateOf = (body: { x?: unknown; series?: { y?: string | null; agg?: string; opts?: unknown }[]; split?: unknown; conditions?: unknown[] }): ShelfState => ({
  x: String(body.x ?? ""), series: (body.series ?? []).map(s => ({ ...s, y: s.y ?? null, agg: s.agg ?? "count" })), split: String(body.split ?? ""), conditions: [...(body.conditions ?? [])] as Cond[],
});
