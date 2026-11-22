// B9 목록 줄의 표시(plans/dash-blocks.md "B9 세부 명세"): 목록의 구한 값(data.values의 keys·row_marks와 kind "mark" 컬럼)을 고치고 세는 순수 함수들(DOM 없음).
import type { CardData, MarkField, MarkValue } from "@/lib/types";

/** 표시가 없는 줄의 값 이름(고르기 칸의 빈 고르기·진척 칸·셀 값 체크) */
export const MARK_EMPTY = "값 없음";
/** 진척 칸 색 수(common.css --c1~--c8) */
export const MARK_COLORS = 8;
/** 표시 없음 몫의 색 */
export const MARK_EMPTY_COLOR = "var(--muted)";

/** 목록의 구한 값 꼴(data.values) */
type MarkListValues = {
  columns?: { name: string; kind?: string; field?: string }[];
  data?: unknown[][];
  keys?: (string | null)[];
  row_marks?: { total: number; counts: Record<string, Record<string, number>> };
};

/** 진척 칸의 값 하나: 값("" = 표시 없음), 보일 이름, 줄 수, 몫 0~1, 색(CSS 값) */
export type MarkBar = { value: string; label: string; n: number; ratio: number; color: string };

/** [SPECIFICATION] plans/dash-blocks.md#applyMark
 *  서버에 저장한 줄 하나(열쇠 key)의 표시 값(칸 id → 값)을 목록의 구한 값에 넣고 고르기 칸 수를 고친 사본(원본은 그대로). */
export function applyMark(data: CardData, key: string, values: Record<string, MarkValue>): CardData {
  const v = data.values as MarkListValues | undefined;
  if (!v?.columns || !v.data || !v.keys) throw new Error("이 목록에는 아직 구한 값이 없어요.");
  const r = v.keys.indexOf(key);
  if (r === -1) throw new Error("고친 줄이 목록에 없어요.");
  const counts: Record<string, Record<string, number>> = { ...(v.row_marks?.counts ?? {}) };
  const text = (x: unknown): string => (x == null ? "" : String(x));
  const cols = v.columns.map((c, i) => {
    if (!(c.kind === "mark" && c.field && c.field in values)) return v.data![i];
    const old = v.data![i][r];
    const now = values[c.field];
    if (c.field in counts) {
      const copy = { ...counts[c.field] };
      const o = text(old);
      const n = text(now);
      copy[o] = (copy[o] ?? 0) - 1;
      if (copy[o] <= 0) delete copy[o];
      copy[n] = (copy[n] ?? 0) + 1;
      counts[c.field] = copy;
    }
    const col = [...v.data![i]];
    col[r] = now;
    return col;
  });
  return { ...data, values: { ...v, data: cols, ...(v.row_marks ? { row_marks: { ...v.row_marks, counts } } : {}) } };
}

/** [SPECIFICATION] plans/dash-blocks.md#markTotal
 *  목록의 고르기 칸 fieldId에서 값들(values, 비면 센 줄 전부)의 줄 수. 표시 수가 아직 없으면 null. */
export function markTotal(data: CardData | undefined, fieldId: string, values: string[]): number | null {
  const m = (data?.values as MarkListValues | undefined)?.row_marks;
  if (!m) return null;
  if (values.length === 0) return m.total;
  const c = m.counts[fieldId] ?? {};
  return values.reduce((s, x) => s + (c[x] ?? 0), 0);
}

/** [SPECIFICATION] plans/dash-blocks.md#markBars
 *  진척 칸에 그릴 값들: 고를 값 차례대로(0줄도) + 표시 없음(있을 때만), 몫은 센 줄 전부 대비. */
export function markBars(data: CardData | undefined, field: MarkField): MarkBar[] {
  const m = (data?.values as MarkListValues | undefined)?.row_marks;
  if (!m) return [];
  const c = m.counts[field.id] ?? {};
  const ratio = (n: number): number => (m.total > 0 ? n / m.total : 0);
  const bars: MarkBar[] = field.choices.map((x, i) => ({ value: x, label: x, n: c[x] ?? 0, ratio: ratio(c[x] ?? 0), color: `var(--c${(i % MARK_COLORS) + 1})` }));
  const none = c[""] ?? 0;
  return none > 0 ? [...bars, { value: "", label: MARK_EMPTY, n: none, ratio: ratio(none), color: MARK_EMPTY_COLOR }] : bars;
}
