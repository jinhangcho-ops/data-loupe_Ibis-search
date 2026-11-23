"use client";
// B12 — 바로가기 창의 연결 묻기(plans/dash-blocks.md "B12 세부 명세"): [최신화]·첫 자동 갱신 때 연다. 기억한 연결이면 비밀번호만, 기억한 연결이 없거나 기록에서 못 찾으면 주소·아이디·비밀번호. 비밀번호는 저장하지 않는다(서버 메모리의 연결에만).
import { useState } from "react";
import { shortcutConnect } from "@/lib/api";
import { useShell } from "./ShellProvider";

const NOT_REMEMBERED = "연결했지만 바로가기로 열지 않아 이 연결을 기억하지 못했어요. 다음에도 주소를 물어요.";

/** [SPECIFICATION] plans/dash-blocks.md#PasswordAsk
 *  Shell의 ask가 있을 때 비밀번호 칸(needs "address"면 주소·아이디 칸도)을 보이고 [연결]이면 연결한 뒤 다시 구한다(없으면 아무것도 그리지 않는다). */
export function PasswordAsk(): React.ReactElement | null {
  const { ask, setAsk, loadConn, recompute, setBell } = useShell();
  const [pass, setPass] = useState(""), [url, setUrl] = useState(""), [user, setUser] = useState(""), [err, setErr] = useState(""), [busy, setBusy] = useState(false);
  if (!ask) return null;
  const address = ask.needs === "address";
  const close = () => { setAsk(null); setPass(""); setErr(""); };
  const go = async () => {
    setBusy(true); setErr("");
    try {
      const r = await shortcutConnect(address ? { url, user, password: pass } : { password: pass });
      setPass(""); setAsk(null);
      if (address && !r.remembered) setBell("shortcut", NOT_REMEMBERED);
      await loadConn(); await recompute();
    } catch (e) { setErr((e as Error).message); await loadConn(); } finally { setBusy(false); }
  };
  return (
    <div className="modal" onClick={e => { if (e.target === e.currentTarget) close(); }} onKeyDown={e => { if (e.key === "Escape") { e.preventDefault(); close(); } }}>
      <div className="modal-box" role="dialog" aria-modal="true" aria-label="데이터 연결">
        <div className="phead"><b>데이터 연결</b><button type="button" className="ghost min-w-[1.5rem] min-h-[1.5rem]" aria-label="데이터 연결 창 닫기" title="닫기" onClick={close}>×</button></div>
        <p className="hint">{address ? "이 바로가기가 기억한 연결을 찾지 못했어요. 데이터베이스 주소를 넣어 새로 읽어요. 연결하면 바로가기가 이 연결을 기억해요. Parquet·CSV 폴더는 여기서 연결할 수 없어요. Data Loupe에서 그 폴더에 한 번 연결해 주세요." : `${ask.label}${ask.user ? ` · ${ask.user}` : ""}에 연결해 새로 읽어요.`} 비밀번호는 저장하지 않아요.</p>
        {err && <div className="err" role="alert">{err}</div>}
        {address && <div className="row"><input autoFocus aria-label="데이터베이스 주소" placeholder="예: postgres://host:5432/db" value={url} onChange={e => setUrl(e.target.value)} /></div>}
        <div className="row [display:flex] gap-3">
          {address && <input aria-label="아이디" placeholder="아이디" autoComplete="off" value={user} onChange={e => setUser(e.target.value)} />}
          <input type="password" autoComplete="off" autoFocus={!address} aria-label="비밀번호" placeholder="비밀번호" value={pass} onChange={e => setPass(e.target.value)} onKeyDown={e => { if (e.key === "Enter") go(); }} />
          <button type="button" className="btn p" disabled={busy || (address && !url.trim())} onClick={go}>연결</button>
        </div>
      </div>
    </div>
  );
}
