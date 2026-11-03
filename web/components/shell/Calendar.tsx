"use client";
// P1 기간 달력(대시보드): 두 달을 나란히(7열), 첫 클릭 시작·둘째 클릭 끝(앞 날을 누르면 둘을 바꾼다), max 뒤의 날은 고를 수 없다.
// 처음 보이는 달은 고른 끝(없으면 max, 없으면 오늘)의 달이 오른쪽. 키보드: 화살표로 날 이동(달을 넘으면 달도), PageUp·PageDown 한 달, Enter·Space 고르기(단추), Esc 닫기.
// 계산은 calPick.ts(순수, vitest). 모양은 .drop .pop + styles/dashboard.css의 .dcal
import { useEffect, useRef, useState } from "react";
import { addMonth, keepFocus, monthCells, moveFocus, pickDay, type Pick } from "./calPick";

const WEEK = ["월", "화", "수", "목", "금", "토", "일"];
const localToday = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const spoken = (d: string) => `${+d.slice(0, 4)}년 ${+d.slice(5, 7)}월 ${+d.slice(8, 10)}일`;

export type CalendarProps = { start?: string; end?: string; max?: string; sunday?: boolean; onPick: (start: string, end: string) => void; onClose: () => void };

export function Calendar({ start = "", end = "", max = "", sunday = false, onPick, onClose }: CalendarProps) {
  const [today] = useState(localToday), last = max || today;
  const [sel, setSel] = useState<Pick>({ start, end });
  const first = end || start || last, [focus, setFocus] = useState(max && first > max ? max : first);
  const [month, setMonth] = useState(() => addMonth((max && first > max ? max : first).slice(0, 7), -1));  // 왼쪽 달: 오른쪽 달에 초점 날(끝 날, 기준일 뒤면 기준일)
  const jump = useRef(true);  // 키보드·날 누르기로 초점 날이 바뀌었을 때만 그 칸으로 초점을 옮긴다(달 단추는 단추에 초점)
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => { if (jump.current) root.current?.querySelector<HTMLButtonElement>(`[data-day="${focus}"]`)?.focus(); jump.current = false; }, [focus]);
  const flip = (n: number) => { const m = addMonth(month, n); setMonth(m); setFocus(f => keepFocus(f, m, max)); };
  const week = sunday ? ["일", ...WEEK.slice(0, 6)] : WEEK;
  // 날로 초점 옮기기: max 뒤면 max로, 보이는 두 달 밖이면 달을 넘긴다
  const choose = (d: string) => {
    const next = pickDay(sel, d);
    setSel(next);
    setFocus(d);
    if (next.done) onPick(next.start, next.end);
  };
  const key = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.stopPropagation(); onClose(); return; }
    const next = moveFocus(focus, e.key, month, max);  // 화살표·PageUp·PageDown(calPick, 시험 있음)
    if (!next) return;
    e.preventDefault();
    jump.current = true;
    setMonth(next.month);
    setFocus(next.focus);
  };
  const lo = sel.start, hi = sel.end || sel.start;
  return (
    <div className="pop dcal" role="dialog" aria-label="기간 달력" ref={root} onKeyDown={key}>
      <div className="dcal-head">
        <button type="button" className="btn" aria-label="이전 달" onClick={() => flip(-1)}>‹</button>
        <span className="hint" role="status">{sel.start && !sel.end ? `${sel.start}부터, 끝 날을 고르세요` : "시작 날을 고르세요"}</span>
        <button type="button" className="btn" aria-label="다음 달" disabled={addMonth(month, 1) >= last.slice(0, 7)} onClick={() => flip(1)}>›</button>
      </div>
      <div className="dcal-months">
        {[month, addMonth(month, 1)].map(ym => (
          <div key={ym} className="dcal-month" role="group" aria-label={`${+ym.slice(0, 4)}년 ${+ym.slice(5, 7)}월`}>
            <div className="dcal-title">{`${+ym.slice(0, 4)}년 ${+ym.slice(5, 7)}월`}</div>
            <div className="dcal-grid">
              {week.map(w => <span key={w} className="dcal-wd" aria-hidden="true">{w}</span>)}
              {monthCells(ym, sunday).map((d, i) => d == null ? <span key={"e" + i} /> : (
                <button key={d} type="button" data-day={d} tabIndex={d === focus ? 0 : -1} disabled={!!max && d > max}
                  className={"dcal-day" + (d === lo || d === hi ? " on" : lo && d > lo && d < hi ? " mid" : "") + (d === today ? " today" : "")}
                  aria-pressed={!!lo && d >= lo && d <= hi} aria-label={spoken(d) + (d === today ? ", 오늘" : "")} onClick={() => choose(d)}>{+d.slice(8, 10)}</button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
