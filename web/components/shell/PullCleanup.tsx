"use client";
// B11 — 받은 데이터 정리 묻기(plans/dash-blocks.md "B11 세부 명세"): 아무 대시보드도 쓰지 않는 받은 데이터(앱을 켠 뒤 대시보드 화면을 처음 열 때 한 번, 사용자 답 2-6)와
// 지금 연 대시보드에서 더는 쓰지 않는 받은 파일(연결돼 있을 때 대시보드를 열 때마다)을 창으로 묻는다. [지우기]면 지우고, [두기]면 알림(종, Bell)에 남긴다(사용자 답 2-17).

import { useEffect, useState } from "react";
import { useShell } from "./ShellProvider";
import { dropOrphans, dropUnused, pullOrphans, pullUnused } from "@/lib/api";
import { useConstants } from "@/lib/constants";
import { useDash } from "@/lib/store";
import type { PullOrphan, PullUnused } from "@/lib/types";

const ASKED_KEY = "pull-orphans-asked";  // sessionStorage: 이번 실행에서 주인 없는 받은 데이터를 이미 물었다는 표시
const MB = 1024 * 1024;

/** [SPECIFICATION] plans/dash-blocks.md#PullCleanup
 *  남은 받은 데이터·더는 쓰지 않는 받은 파일을 묻는 창(물을 것이 없으면 아무것도 그리지 않는다). */
export function PullCleanup(): React.ReactElement | null {
  const { state, dispatch } = useDash();
  const { conn } = useShell();
  const consts = useConstants();
  const [orphans, setOrphans] = useState<{ orphans: PullOrphan[]; text: string } | null>(null);
  const [unused, setUnused] = useState<PullUnused[]>([]);
  const id = state.dash?.id ?? "";
  const fail = (e: Error) => dispatch({ type: "error", message: e.message });
  useEffect(() => {
    if (!consts || consts.shortcut) return;
    if (sessionStorage.getItem(ASKED_KEY)) return;
    sessionStorage.setItem(ASKED_KEY, "1");  // 앱을 켠 뒤 한 번만(창을 닫으면 사라져 다음 실행에 다시 묻는다)
    pullOrphans().then(r => { if (r.orphans.length) setOrphans(r); }).catch(fail);
  }, [consts]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!id || !conn || !consts || consts.shortcut) return;
    let live = true;
    pullUnused(id).then(r => { if (live) setUnused(r.unused); }).catch(fail);
    return () => { live = false; };
  }, [id, conn, consts]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (!orphans && unused.length === 0) return null;
  const keep = () => (orphans ? setOrphans(null) : setUnused([]));
  const drop = () => (orphans ? dropOrphans() : dropUnused(id, unused.map(u => u.slot))).then(keep).catch(fail);
  const size = (bytes: number) => `${(bytes / MB).toFixed(1)}MB`;
  const rows = orphans?.orphans ?? unused;
  return (
    <div className="modal" onClick={e => { if (e.target === e.currentTarget) keep(); }} onKeyDown={e => { if (e.key === "Escape") { e.preventDefault(); keep(); } }}>
      <div className="modal-box" role="dialog" aria-modal="true" aria-label="받은 데이터 정리">
        <h3>{orphans ? `지운 대시보드의 받은 데이터가 남아 있어요(${orphans.text}). 지울까요?` : "더는 사용하지 않는 파일입니다. 지울까요?"}</h3>
        <ul>{rows.map(r => <li key={r.slot}>{r.table} · {size(r.bytes)}{"shared" in r && r.shared ? " — 다른 대시보드도 써서 이 대시보드에서만 빼요" : ""}</li>)}</ul>
        <div className="row [display:flex] [justify-content:flex-end] gap-3">
          <button className="ghost" autoFocus onClick={keep}>두기</button>
          <button className="btn p" onClick={drop}>지우기</button>
        </div>
      </div>
    </div>
  );
}
