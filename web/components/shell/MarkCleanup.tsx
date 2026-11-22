"use client";
// B9 대시보드 설정 창의 "줄 표시" 묶음(plans/dash-blocks.md "B9 세부 명세"): 표시 칸이 있는 목록마다 지금 데이터에 없는 줄의 표시 수를 [세기]로 세고 [지우기]로 지운다.
// 자동으로 세거나 지우지 않는다. 보는 곳에서도 쓴다(SettingsDialog 두 갈래 모두).
import { useState } from "react";
import { dropMarkOrphans, markOrphans } from "@/lib/api";
import type { Dashboard } from "@/lib/types";

const MARK_RULE = "카드에 적어 둔 조건·기간 줄로만 견줘요. 대시보드 기간·필터로 잠깐 안 보이는 줄은 지금 데이터에 있는 것으로 봐요. PC 계산 대시보드는 받아 둔 데이터로 견줘요. 표시는 이 PC의 설정 폴더(dash_marks)에 두어요.";

export type MarkCleanupProps = { dash: Dashboard };

/** [SPECIFICATION] plans/dash-blocks.md#MarkCleanup
 *  표시 칸이 있는 목록마다 [세기]·"지금 데이터에 없는 줄의 표시 N개"·[지우기]를 그린다(그런 목록이 없으면 아무것도 안 그림). */
export function MarkCleanup({ dash }: MarkCleanupProps): React.ReactElement | null {
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const lists = dash.cards.filter((c) => c.kind === "list" && (c.view.row_marks?.fields.length ?? 0) > 0);
  if (lists.length === 0) return null;
  const run = async (job: () => Promise<void>): Promise<void> => {
    setBusy(true);
    try { await job(); setMsg(""); } catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  };
  const count = (id: string): Promise<void> => run(async () => {
    const r = await markOrphans(dash.id, id);
    setCounts((c) => ({ ...c, [id]: r.count }));
  });
  const drop = (id: string): Promise<void> => run(async () => {
    await dropMarkOrphans(dash.id, id);
    setCounts((c) => ({ ...c, [id]: 0 }));
  });
  return (
    <>
      <h4>줄 표시</h4>
      {lists.map((c) => (
        <div className="row" key={c.id}>
          <span className="hint m-0">{c.title}</span>
          <button className="btn" onClick={() => count(c.id)} disabled={busy}>세기</button>
          {counts[c.id] !== undefined && <span className="hint m-0">{`지금 데이터에 없는 줄의 표시 ${counts[c.id].toLocaleString()}개`}</span>}
          {(counts[c.id] ?? 0) > 0 && <button className="btn" onClick={() => drop(c.id)} disabled={busy}>지우기</button>}
        </div>
      ))}
      <div className="hint m-0">{MARK_RULE}</div>
      <div className="err" role="status">{msg}</div>
    </>
  );
}
