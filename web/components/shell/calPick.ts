// 기간 달력(P1)의 순수 계산: 달 칸 배치와 두 번 눌러 기간 고르기. 날짜는 "YYYY-MM-DD" 글자(UTC로 계산해 시간대에 흔들리지 않게). web/tests의 vitest가 확인한다.
import { shiftDay } from "@/lib/calc";

/** "YYYY-MM"의 n달 뒤 */
export function addMonth(ym: string, n: number) {
  const m = +ym.slice(0, 4) * 12 + (+ym.slice(5, 7) - 1) + n;
  return `${Math.floor(m / 12)}-${String((m % 12) + 1).padStart(2, "0")}`;
}

/** 한 달의 칸: 첫 주 앞의 빈 칸(null) + 그달의 날들. 주는 월요일 시작(sunday면 일요일) */
export function monthCells(ym: string, sunday = false): (string | null)[] {
  const first = `${ym}-01`, lead = (new Date(first + "T00:00:00Z").getUTCDay() + (sunday ? 0 : 6)) % 7, out: (string | null)[] = Array(lead).fill(null);
  for (let d = first; d.startsWith(ym); d = shiftDay(d, 1)) out.push(d);
  return out;
}

export type Pick = { start: string; end: string };
/** 한 번 누르기: 처음(또는 다 고른 뒤)이면 시작만, 시작이 있으면 끝(앞 날을 누르면 둘을 바꾼다). done이면 기간이 정해졌다 */
export function pickDay(sel: Pick, day: string): Pick & { done: boolean } {
  if (!sel.start || sel.end) return { start: day, end: "", done: false };
  return day < sel.start ? { start: day, end: sel.start, done: true } : { start: sel.start, end: day, done: true };
}

/** 기간 칸 글자 "YYYY-MM-DD ~ YYYY-MM-DD"를 읽는다(아니면 빈 기간) */
export function readSpan(text: string): Pick {
  const m = text.match(/^\s*(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})\s*$/);
  return m ? { start: m[1], end: m[2] } : { start: "", end: "" };
}

const STEP: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
/** 키 하나로 초점 날 옮기기: 화살표는 하루·한 주, PageUp·PageDown은 한 달(29일 뒤는 28일로). max 뒤면 max, 보이는 두 달(month, 다음 달) 밖이면 달을 넘긴다. 다른 키는 null */
export function moveFocus(focus: string, key: string, month: string, max: string): { focus: string; month: string } | null {
  const d = key in STEP ? shiftDay(focus, STEP[key]) : key === "PageUp" || key === "PageDown" ? `${addMonth(focus.slice(0, 7), key === "PageUp" ? -1 : 1)}-${focus.slice(8, 10) > "28" ? "28" : focus.slice(8, 10)}` : "";
  if (!d) return null;
  const day = max && d > max ? max : d, m = day.slice(0, 7);
  return { focus: day, month: m < month ? m : m > addMonth(month, 1) ? addMonth(m, -1) : month };
}
/** 달을 넘긴 뒤의 초점 날: 보이는 두 달(month, 다음 달) 안이면 그대로, 밖이면 왼쪽 달 1일(max 뒤면 max). Tab으로 날짜 칸에 들어갈 칸이 늘 하나 있게 */
export function keepFocus(focus: string, month: string, max: string) {
  if (focus.slice(0, 7) === month || focus.slice(0, 7) === addMonth(month, 1)) return focus;
  const d = `${month}-01`;
  return max && d > max ? max : d;
}
