"use client";
// 북극성·카운터(옛 dash_panel.js starSection): 북극성은 대시보드에서 가장 중요한 지표 하나(★), 카운터는 북극성을 올리다 나빠지면 안 되는 지표(⚑, 한계를 벗어나면 위쪽 경고).
// 대시보드 설정(north_star)이라 카드 patch가 아니라 dispatch({type:"meta"})로 바꾼다.
import { useState } from "react";
import { useDash } from "@/lib/store";
import type { NorthStar } from "@/lib/types";

const STAR_HELP = "북극성은 대시보드에서 가장 중요한 지표 하나(★), 카운터는 북극성을 올리는 동안 나빠지면 안 되는 지표(⚑)예요.";

export function StarSection({ id }: { id: string }) {
  const { state, dispatch } = useDash();
  const star: NorthStar = { card: "", counters: [], limits: {}, ...state.dash?.north_star };
  const isStar = star.card === id, isCounter = star.counters.includes(id), lim = star.limits?.[id] ?? { op: ">=" as const, value: "" as number | "" };
  const [op, setOp] = useState<string>(lim.op), [value, setValue] = useState(String(lim.value)), [note, setNote] = useState("");
  const set = (next: NorthStar) => dispatch({ type: "meta", patch: { north_star: next } });
  return (
    <div>
      <h5>중요 지표</h5>
      <div className="prow">
        <button type="button" className={`btn${isStar ? " on" : ""}`} aria-pressed={isStar} style={{ minHeight: 24 }} title="대시보드에서 가장 중요한 지표 하나예요. 카드 제목 옆에 ★가 붙어요"
          onClick={() => set({ ...star, card: isStar ? "" : id, counters: star.counters.filter(c => c !== id) })}>{isStar ? "북극성 지표 해제" : "북극성 지표로 정하기"}</button>
        <button type="button" className={`btn${isCounter ? " on" : ""}`} aria-pressed={isCounter} style={{ minHeight: 24 }} title="북극성 지표를 올리는 동안 나빠지면 안 되는 지표예요(예: 환불률). 카드 제목 옆에 ⚑가 붙어요"
          onClick={() => {
            const limits = { ...star.limits };
            if (isCounter) delete limits[id];
            set({ ...star, card: star.card === id ? "" : star.card, counters: isCounter ? star.counters.filter(c => c !== id) : [...star.counters, id], limits });
          }}>{isCounter ? "카운터 지표 해제" : "카운터 지표로 정하기"}</button>
      </div>
      <div className="hint">{STAR_HELP}</div>
      {isCounter && (
        <>
          <div className="prow">
            <select aria-label="한계 방향" value={op} onChange={e => setOp(e.target.value)}><option value=">=">이 값 이상 유지</option><option value="<=">이 값 이하 유지</option></select>
            <input type="number" step="any" aria-label="한계 값" placeholder="한계 값" style={{ width: 100 }} value={value} onChange={e => setValue(e.target.value)} />
            <button type="button" className="btn" style={{ minHeight: 24 }} onClick={() => {
              if (value === "" || !Number.isFinite(+value)) return setNote("한계는 숫자로 적어 주세요.");
              setNote("");
              set({ ...star, limits: { ...star.limits, [id]: { op: op as ">=" | "<=", value: +value } } });
            }}>한계 저장</button>
          </div>
          <div className="hint">{note || "지금 값이 한계를 벗어나면 대시보드 위에 경고가 떠요."}</div>
        </>
      )}
    </div>
  );
}
