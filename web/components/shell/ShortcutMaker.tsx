"use client";
// B12 — 도구 줄의 [바로가기 만들기](#d-shortcut, [대시보드만 보기] 옆)와 그 창(plans/dash-blocks.md "B12 세부 명세"): 아이콘(.ico, 안 고르면 앱 아이콘)을 고르고 저장 대화상자로 .lnk를 쓴다.

import { useState } from "react";
import { makeShortcut, pickIcon } from "@/lib/api";
import { useDash } from "@/lib/store";

/** [SPECIFICATION] plans/dash-blocks.md#ShortcutMaker
 *  [바로가기 만들기] 단추와 아이콘 칸·[만들기]가 있는 창. 지금 대시보드가 없으면 단추를 막는다. */
export function ShortcutMaker(): React.ReactElement {
  const { state, dispatch } = useDash();
  const dash = state.dash;
  const [open, setOpen] = useState(false), [icon, setIcon] = useState(""), [err, setErr] = useState(""), [busy, setBusy] = useState(false);
  const close = () => setOpen(false);
  const pick = () => { setErr(""); pickIcon().then(r => { if (r.path) setIcon(r.path); }, (e: Error) => setErr(e.message)); };
  const make = async () => {
    if (!dash) return;
    setBusy(true); setErr("");
    try {
      const r = await makeShortcut(dash.id, icon);
      if (r.path) { setOpen(false); dispatch({ type: "notice", message: `바로가기를 만들었어요: ${r.path}` }); }
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <>
      <button className="btn" id="d-shortcut" aria-haspopup="dialog" disabled={!dash} onClick={() => { setErr(""); setOpen(true); }} title="이 대시보드만 보기로 바로 여는 바로가기 파일(.lnk)을 만들어요">바로가기 만들기</button>
      {open && dash && (
        <div className="modal" onClick={e => { if (e.target === e.currentTarget) close(); }} onKeyDown={e => { if (e.key === "Escape") { e.preventDefault(); close(); } }}>
          <div className="modal-box" role="dialog" aria-modal="true" aria-label="바로가기 만들기">
            <div className="phead"><b>바로가기 만들기</b><button type="button" className="ghost min-w-[1.5rem] min-h-[1.5rem]" autoFocus aria-label="바로가기 만들기 닫기" title="닫기" onClick={close}>×</button></div>
            <p className="hint">'{dash.name}'만 보는 창을 여는 바로가기예요. 지금 연결을 기억해요(비밀번호는 빼고). 그 창에서는 기간·필터·쪽 넘기기·최신화·화면 모드·내보내기만 쓸 수 있어요.</p>
            <div className="row [display:flex] gap-3 items-center"><span className="hint m-0">아이콘</span><span>{icon || "앱 아이콘"}</span>
              <button type="button" className="ghost" onClick={pick}>아이콘 고르기</button>{icon && <button type="button" className="ghost" onClick={() => setIcon("")}>앱 아이콘으로</button>}</div>
            {err && <div className="err" role="alert">{err}</div>}
            <div className="row [display:flex] [justify-content:flex-end] gap-3"><button type="button" className="ghost" onClick={close}>닫기</button><button type="button" className="btn p" disabled={busy} onClick={make}>만들기</button></div>
          </div>
        </div>
      )}
    </>
  );
}
