"use client";
// N4 — 기간 칸(옛 dash_ctl.js DashCtl의 칸): 기준일(+달력, 오늘 뒤 막기)·기간·비교 기준·최신화 주기·실시간 간격(시작/멈추기)·실시간 최소 간격(초).
// 규칙과 상태는 ShellProvider(useShell().period)에 있다(창을 닫아도 실시간 갱신이 이어진다). 칸은 옛 change처럼 벗어나거나 Enter일 때 확정한다.
import { useRef } from "react";
import { useConstants } from "@/lib/constants";
import { useDash } from "@/lib/store";
import type { CompareMode } from "@/lib/types";
import { useShell, type Note } from "./ShellProvider";

const NoteLine = ({ n }: { n?: Note }) => <div className={"hint dc-note" + (n?.bad ? " bad" : "")} role={n?.bad ? "alert" : undefined}>{n?.text ?? ""}</div>;
// 초를 "1시간"·"30분"·"45초" 글자로(서버 dash_refresh.minimum_text와 같다)
const minimumText = (s: number) => s % 3600 === 0 ? `${s / 3600}시간` : s % 60 === 0 ? `${s / 60}분` : `${s}초`;
const enter = (go: () => void) => (e: React.KeyboardEvent) => { if (e.key === "Enter") go(); };

export function PeriodBar() {
  const { state } = useDash();
  const { period: p } = useShell();
  const minRefresh = useConstants()?.dash.min_refresh ?? 3600;
  const pick = useRef<HTMLInputElement>(null);
  const cards = state.dash?.cards ?? [];
  const times = cards.map(c => (state.values[c.id]?.data ?? c.data)?.computed_at).filter(Boolean).sort() as string[];
  const last = times.length ? `마지막으로 구한 시각 ${times[times.length - 1].replace("T", " ")}` : "아직 구한 값이 없어요";
  return (
    <div className="dctl">
      <div className="dc-field">
        <label className="hint" htmlFor="dc-asof">기준일</label>
        <div className="row">
          <input id="dc-asof" value={p.inputs.as_of} placeholder="오늘" title="이 날짜를 기준으로 값을 봐요. 예: 2026-10-01, 어제, 지난달 말. 비우면 오늘이에요"
            onChange={e => p.setInput("as_of", e.target.value)} onBlur={() => p.commitDay()} onKeyDown={enter(() => p.commitDay())} />
          <button className="btn" title="달력에서 기준일을 골라요" onClick={() => { const el = pick.current; if (el) { if (el.showPicker) el.showPicker(); else el.click(); } }}>달력</button>
          <input ref={pick} type="date" className="dc-pick" tabIndex={-1} aria-hidden="true" max={p.today || undefined} onChange={e => { if (e.target.value) p.commitDay(e.target.value); }} />
        </div>
        <NoteLine n={p.notes.asof} />
      </div>
      <div className="dc-field">
        <label className="hint" htmlFor="dc-per">기간</label>
        <input id="dc-per" value={p.inputs.period} placeholder="비우면 카드의 원래 기간" title="모든 카드에 같은 기간을 걸어요. 예: 최근 7일, 지난달, 이번 달, 2026-10, 2026-Q3, 2026-10-01 ~ 2026-10-31. 비우면 카드마다 원래 조건대로 보여요"
          onChange={e => p.setInput("period", e.target.value)} onBlur={p.commitPeriod} onKeyDown={enter(p.commitPeriod)} />
        <NoteLine n={p.notes.per} />
      </div>
      <div className="dc-field">
        <label className="hint" htmlFor="dc-cmp">비교 기준</label>
        <select id="dc-cmp" value={p.compareMode} onChange={e => p.setCompareMode(e.target.value as CompareMode)} title="비교 문장이 지금 기간과 견줄 기간이에요">
          <option value="prev">직전 기간</option>
          <option value="same_weekday">같은 요일 기간</option>
          <option value="yoy">작년 같은 기간</option>
        </select>
      </div>
      <div className="dc-field">
        <label className="hint" htmlFor="dc-every">최신화 주기</label>
        <input id="dc-every" value={p.inputs.refresh} placeholder="예: 1일 (비우면 안 함)" title={`연결할 때 이 주기가 지난 카드만 다시 구해요. ${minimumText(minRefresh)} 이상으로 적어 주세요`}
          onChange={e => p.setInput("refresh", e.target.value)} onBlur={() => p.commitEvery("refresh")} onKeyDown={enter(() => p.commitEvery("refresh"))} />
        <NoteLine n={p.notes.refresh?.bad ? p.notes.refresh : { text: last }} />
      </div>
      <div className="dc-field">
        <label className="hint" htmlFor="dc-live">실시간 간격</label>
        <div className="row">
          <input id="dc-live" value={p.inputs.live} placeholder="예: 30초" title="화면을 보는 동안 이 간격으로 카드를 다시 구해요. 앞 조회가 끝난 뒤부터 세고, 이 값은 저장하지 않아요"
            onChange={e => p.setInput("live", e.target.value)} onBlur={() => p.commitEvery("live")} onKeyDown={enter(() => p.commitEvery("live"))} />
          <button className="btn" onClick={p.toggleLive}>{p.liveOn ? "멈추기" : "시작"}</button>
        </div>
        <NoteLine n={p.notes.live} />
      </div>
      <div className="dc-field">
        <label className="hint" htmlFor="dc-min">실시간 최소 간격(초)</label>
        <div className="row">
          <input id="dc-min" type="number" min={0} value={p.min} placeholder="기본값" disabled={!p.minOn} onChange={e => p.setMin(e.target.value)}
            title="지금 연결에서 쓸 수 있는 가장 짧은 실시간 간격(초)이에요. 비우면 기본값을 써요(원격 연결 60초, 이 PC 파일은 제한 없음)" />
          <button className="btn" onClick={p.saveMin} disabled={!p.minOn}>적용</button>
        </div>
        <NoteLine n={p.notes.min} />
      </div>
      {p.past && <span className="dc-past">지난 날짜 기준으로 보는 중</span>}
    </div>
  );
}
