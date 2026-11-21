"use client";
// B11 — 대시보드 도구 줄의 알림(종) 단추(plans/dash-blocks.md "B11 세부 명세", 사용자 답 2-17): 더는 안 쓰는 받은 파일([두기]를 누른 것), 못 지운 받은 파일,
// 받기 오류(마지막 줄을 못 찾음 등), 연결 없음·빠진 구간 안내를 한 목록으로 보이고, 파일은 여기서 [지우기], 마지막 줄을 못 찾은 칸은 [기간을 정해 받기].
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { bellMark, bellRead, dropOrphans, dropUnused, pullOrphans, pullRange, pullState, pullUnused } from "@/lib/api";
import { useDash } from "@/lib/store";
import type { PullOrphan, PullSlot, PullUnused } from "@/lib/types";
import { useShell } from "./ShellProvider";

const BELL_PATH = "M6 16V11a6 6 0 0112 0v5l2 2H4zM10 20a2 2 0 004 0";  // 24×24 선 그림(ICON_PATHS에 더하지 않는다: 그 표는 KPI 카드 아이콘 고르기에 나온다)
const MB = 1024 * 1024;
const NONE = { lost: [] as PullSlot[], unused: [] as PullUnused[], orphans: [] as PullOrphan[] };
type Found = typeof NONE;
type Line = { id: string; body: ReactNode };

const size = (bytes: number) => `${(bytes / MB).toFixed(1)} MB`;
const collect = async (id: string, connected: boolean): Promise<Found> => ({
  lost: id ? (await pullState(id)).slots.filter(s => s.lost) : [],
  unused: id && connected ? (await pullUnused(id)).unused : [],
  orphans: (await pullOrphans()).orphans,
});

/** [SPECIFICATION] plans/dash-blocks.md#Bell
 *  알림(종) 단추와 그 목록 창. 알림이 없으면 단추만(개수 없이) 그린다. */
export function Bell(): React.ReactElement {
  const { state } = useDash();
  const { bell, conn, recompute } = useShell();
  const dashId = state.dash?.id ?? "";
  const [found, setFound] = useState<Found>(NONE);
  const [read, setRead] = useState<string[]>([]);
  const [bold, setBold] = useState<string[]>([]);  // 창을 연 때 안 읽은 알림(굵게)
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState("");
  const [span, setSpan] = useState<Record<string, { start: string; end: string }>>({});
  const reload = useCallback(() => Promise.all([bellRead(), collect(dashId, !!conn)]).then(([r, f]) => {
    setRead(r.read);
    setFound(f);
    return f;
  }), [dashId, conn]);
  useEffect(() => { reload().catch((e: Error) => setErr(e.message)); }, [reload, bell]);
  const act = (work: () => Promise<unknown>) => () => { setErr(""); work().then(reload).catch((e: Error) => setErr(e.message)); };

  const lines: Line[] = [
    ...bell.map(b => ({ id: `${b.key}:${dashId}:${b.text}`, body: b.text })),
    ...found.lost.map(s => {
      const r = span[s.slot] ?? { start: "", end: "" };
      return {
        id: `lost:${s.slot}:${s.pulled_at}`,
        body: <>
          {`'${s.table}' 마지막으로 받은 줄을 찾지 못했어요. `}
          {s.lost === "-" ? "기간 컬럼이 하나가 아니라 [전체 갱신]만 돼요." : <>
            <input type="date" aria-label="다시 받을 시작 날짜" value={r.start} onChange={e => setSpan({ ...span, [s.slot]: { ...r, start: e.target.value } })} />
            <input type="date" aria-label="다시 받을 끝 날짜" value={r.end} onChange={e => setSpan({ ...span, [s.slot]: { ...r, end: e.target.value } })} />
            <button type="button" className="btn" disabled={!conn || !r.start || !r.end} onClick={act(async () => { await pullRange(dashId, s.slot, r.start, r.end); await recompute(false); })}>이 기간 다시 받기</button>
          </>}
        </>,
      };
    }),
    ...found.unused.map(u => ({
      id: `unused:${dashId}:${u.slot}`,
      body: <>
        {`더는 사용하지 않는 파일입니다. ${u.table} (${size(u.bytes)})${u.shared ? " 다른 대시보드도 써서 이 대시보드에서만 빼요." : ""} `}
        <button type="button" className="btn" onClick={act(() => dropUnused(dashId, [u.slot]))}>지우기</button>
      </>,
    })),
    ...found.orphans.map(o => ({
      id: `orphan:${o.slot}`,
      body: <>
        {`지운 대시보드가 남긴 받은 데이터예요. ${o.table} (${size(o.bytes)}) `}
        <button type="button" className="btn" onClick={act(() => dropOrphans())}>지우기</button>
      </>,
    })),
  ];
  const ids = lines.map(l => l.id);
  const unread = ids.filter(id => !read.includes(id)).length;
  const show = () => {
    setErr("");
    reload().then(f => {
      const now = [...bell.map(b => `${b.key}:${dashId}:${b.text}`), ...f.lost.map(s => `lost:${s.slot}:${s.pulled_at}`), ...f.unused.map(u => `unused:${dashId}:${u.slot}`), ...f.orphans.map(o => `orphan:${o.slot}`)];
      setBold(now.filter(id => !read.includes(id)));
      setOpen(true);
      return bellMark(now, now).then(r => setRead(r.read));
    }).catch((e: Error) => setErr(e.message));
  };
  const close = () => setOpen(false);
  return (
    <>
      <button className="btn" id="d-bell" aria-haspopup="dialog" aria-label="알림" title="받은 데이터 정리·받기 오류·연결 안내" onClick={show}>
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={BELL_PATH} /></svg>
        {unread > 0 && <span className="hint m-0">{unread}</span>}
      </button>
      {open && (
        <div className="modal" onClick={e => { if (e.target === e.currentTarget) close(); }} onKeyDown={e => { if (e.key === "Escape") { e.preventDefault(); close(); } }}>
          <div className="modal-box" role="dialog" aria-modal="true" aria-label="알림">
            <div className="phead"><b>알림</b><button type="button" className="ghost min-w-[1.5rem] min-h-[1.5rem]" autoFocus aria-label="알림 닫기" title="닫기" onClick={close}>×</button></div>
            {err && <div className="err" role="alert">{err}</div>}
            {lines.length === 0 ? <p className="hint">알림이 없어요.</p> : lines.map(l => <div key={l.id} className="row">{bold.includes(l.id) ? <b>{l.body}</b> : l.body}</div>)}
          </div>
        </div>
      )}
    </>
  );
}
