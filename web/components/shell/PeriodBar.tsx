"use client";
// N4 — 기간 칸(옛 dash_ctl.js DashCtl의 칸): 기준일(+달력, 오늘 뒤 막기)·기간·비교 기준·최신화 주기·실시간 간격(시작/멈추기)·실시간 최소 간격(초).
// 기간 칸 옆 [달력](P1): 두 달 달력(Calendar)에서 고른 기간을 "YYYY-MM-DD ~ YYYY-MM-DD"로 칸에 넣고 바로 확정(commitPeriod). 기준일(없으면 오늘) 뒤는 막는다.
// 기간 칸(PeriodField)은 머리 줄(Toolbar)도 같이 쓰고, 마지막으로 구한 시각(useLastComputed)은 머리 줄의 "N분 전 최신화"(LastRefreshed)도 쓴다.
// 규칙과 상태는 ShellProvider(useShell().period)에 있다(창을 닫아도 실시간 갱신이 이어진다). 칸은 옛 change처럼 벗어나거나 Enter일 때 확정한다.
import { useEffect, useRef, useState } from "react";
import { useConstants } from "@/lib/constants";
import { useDash } from "@/lib/store";
import type { CompareMode } from "@/lib/types";
import { Calendar } from "./Calendar";
import { readSpan } from "./calPick";
import { useShell, type Note } from "./ShellProvider";

const NoteLine = ({ n }: { n?: Note }) => <div className={"hint dc-note" + (n?.bad ? " bad" : "")} role={n?.bad ? "alert" : undefined}>{n?.text ?? ""}</div>;
const enter = (go: () => void) => (e: React.KeyboardEvent) => { if (e.key === "Enter") go(); };

/** 카드 값을 마지막으로 구한 시각(ISO 글자). 구한 값이 없으면 undefined. 설정 창의 최신화 주기 안내와 머리 줄의 "N분 전 최신화"가 같이 쓴다. */
function useLastComputed(): string | undefined {
  const { state } = useDash();
  const cards = state.dash?.cards ?? [];
  return (cards.map(c => (state.values[c.id]?.data ?? c.data)?.computed_at).filter(Boolean).sort() as string[]).at(-1);
}

const MINUTE_MS = 60_000, HOUR_MIN = 60, DAY_MIN = 1440;
/** 구한 시각(at)이 now(ms)에서 얼마나 지났는지: "방금 최신화"·"N분 전 최신화"·"N시간 전 최신화"·"N일 전 최신화" */
export function agoText(at: string, now: number): string {
  const min = Math.max(0, Math.floor((now - new Date(at).getTime()) / MINUTE_MS));
  return (min < 1 ? "방금" : min < HOUR_MIN ? `${min}분 전` : min < DAY_MIN ? `${Math.floor(min / HOUR_MIN)}시간 전` : `${Math.floor(min / DAY_MIN)}일 전`) + " 최신화";
}

/** 머리 줄의 작은 글자 "N분 전 최신화"(1분마다 고친다). 구한 값이 없으면 아무것도 안 그린다. */
export function LastRefreshed() {
  const at = useLastComputed();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), MINUTE_MS); return () => clearInterval(t); }, []);
  return at ? <span className="hint d-fresh" title={`마지막으로 구한 시각 ${at.replace("T", " ")}`}>{agoText(at, now)}</span> : null;
}

/** 기간 칸 + [달력](두 달 달력 팝오버) + 안내 줄. 머리 줄(Toolbar)과 설정 창(PeriodBar)이 같이 쓴다. id는 칸의 id(달력 단추는 id-cal). */
export function PeriodField({ id, elem }: { id: string; elem?: string }) {
  const { state } = useDash();
  const shell = useShell();
  const { period: p } = shell;
  const consts = useConstants();
  const mineNow = elem ? shell.elems[elem]?.period : undefined;
  const [mine, setMine] = useState(mineNow ?? p.inputs.period);
  const [seen, setSeen] = useState(mineNow);
  if (elem && seen !== mineNow) { setSeen(mineNow); setMine(mineNow ?? p.inputs.period); }  // 요소 값이 바뀌면 칸이 따라간다(그리는 중에 맞춘다)
  const text = elem ? mine : p.inputs.period;
  const commit = (t: string = text) => { if (elem) void shell.elemPeriod(elem, "period", t); else p.commitPeriod(t === text ? undefined : t); };
  const calBtn = useRef<HTMLButtonElement>(null), calBox = useRef<HTMLDivElement>(null);
  const [cal, setCal] = useState(false);
  const closeCal = () => { setCal(false); calBtn.current?.focus(); };
  // 달력 밖을 누르면 닫는다
  useEffect(() => {
    if (!cal) return;
    const away = (e: PointerEvent) => { if (!calBox.current?.contains(e.target as Node)) setCal(false); };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [cal]);
  const span = readSpan(text), max = /^\d{4}-\d{2}-\d{2}$/.test(p.inputs.as_of.trim()) ? p.inputs.as_of.trim() : p.today;
  return (
    <>
      <div className="row drop" ref={calBox}>
        <input id={id} aria-label="기간" disabled={!state.dash} value={text} placeholder="비우면 카드의 원래 기간" title={(elem ? "링크한 카드에 이 기간을 걸어요" : "모든 카드에 같은 기간을 걸어요") + ". 예: 최근 7일, 지난달, 이번 달, 2026-10, 2026-Q3, 2026-10-01 ~ 2026-10-31. 비우면 카드마다 원래 조건대로 보여요"}
          onChange={e => elem ? setMine(e.target.value) : p.setInput("period", e.target.value)} onBlur={() => commit()} onKeyDown={enter(() => commit())} />
        <button ref={calBtn} className="btn" id={`${id}-cal`} disabled={!state.dash} title="달력에서 시작 날과 끝 날을 골라요" aria-expanded={cal} aria-haspopup="dialog" onClick={() => setCal(!cal)}>달력</button>
        {cal && <Calendar start={span.start} end={span.end} max={max} sunday={consts?.week_start === "sun"} onClose={closeCal}
          onPick={(a, b) => { closeCal(); setMine(`${a} ~ ${b}`); commit(`${a} ~ ${b}`); }} />}
      </div>
      <NoteLine n={elem ? shell.elems[elem]?.notes?.per : p.notes.per} />
    </>
  );
}

/** 기준일 칸 + [달력](브라우저 날짜 고르기) + 안내 줄. id는 칸의 id. */
export function AsOfField({ id, elem }: { id: string; elem?: string }) {
  const shell = useShell();
  const { period: p } = shell;
  const pick = useRef<HTMLInputElement>(null);
  const mineNow = elem ? shell.elems[elem]?.as_of : undefined;
  const [mine, setMine] = useState(mineNow ?? p.inputs.as_of);
  const [seen, setSeen] = useState(mineNow);
  if (elem && seen !== mineNow) { setSeen(mineNow); setMine(mineNow ?? p.inputs.as_of); }  // 요소 값이 바뀌면 칸이 따라간다(그리는 중에 맞춘다)
  const text = elem ? mine : p.inputs.as_of;
  const commit = (t: string = text) => { if (elem) void shell.elemPeriod(elem, "as_of", t); else p.commitDay(t === text ? undefined : t); };
  return (
    <>
      <div className="row">
        <input id={id} value={text} placeholder="오늘" title="이 날짜를 기준으로 값을 봐요. 예: 2026-10-01, 어제, 지난달 말. 비우면 오늘이에요"
          onChange={e => elem ? setMine(e.target.value) : p.setInput("as_of", e.target.value)} onBlur={() => commit()} onKeyDown={enter(() => commit())} />
        <button className="btn" title="달력에서 기준일을 골라요" onClick={() => { const el = pick.current; if (el) { if (el.showPicker) el.showPicker(); else el.click(); } }}>달력</button>
        <input ref={pick} type="date" className="dc-pick" tabIndex={-1} aria-hidden="true" max={p.today || undefined} onChange={e => { if (e.target.value) { setMine(e.target.value); commit(e.target.value); } }} />
      </div>
      <NoteLine n={elem ? shell.elems[elem]?.notes?.asof : p.notes.asof} />
    </>
  );
}

export function PeriodBar() {
  const { period: p } = useShell();
  const at = useLastComputed();
  const last = at ? `마지막으로 구한 시각 ${at.replace("T", " ")}` : "아직 구한 값이 없어요";
  return (
    <div className="dctl">
      <div className="dc-field">
        <label className="hint" htmlFor="dc-asof">기준일</label>
        <AsOfField id="dc-asof" />
      </div>
      <div className="dc-field">
        <label className="hint" htmlFor="dc-per">기간</label>
        <PeriodField id="dc-per" />
        <NoteLine n={{ text: last }} />
      </div>
      <div className="dc-field">
        <label className="hint" htmlFor="dc-cmp">비교 기준</label>
        <select id="dc-cmp" value={p.compareMode} onChange={e => p.setCompareMode(e.target.value as CompareMode)} title="비교 문장이 지금 기간과 견줄 기간이에요">
          <option value="prev">직전 기간</option>
          <option value="same_weekday">같은 요일 기간</option>
          <option value="yoy">작년 같은 기간</option>
        </select>
      </div>
      {p.past && <span className="dc-past">지난 날짜 기준으로 보는 중</span>}
    </div>
  );
}
