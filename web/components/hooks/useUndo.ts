"use client";
// N6 — 되돌리기(옛 draft.js Undo·Shortcuts, Ctrl+Z / Ctrl+Y·Ctrl+Shift+Z).
// 카드를 바꾸는 액션("cards"·"card"·"meta")이 오기 전의 대시보드(before.dash)를 쌓는다. 0.6초 안에 이어진 편집(글자 치기)은 한 번으로 묶는다.
// "open"이 오면 비운다(옛 dashUndo.reset). 되돌리기는 dispatch cards·meta로 그 대시보드를 그대로 놓는다(카드 값 data도 그때 것, 실시간 값은 state.values에 남는다).
// 단축키는 입력 칸 안·팝업이 떠 있을 때는 무시한다(글자 되돌리기는 창 엔진에 맡긴다). 여러 컴포넌트가 불러도 기록·단축키는 한 벌(모듈 공용).
import { useEffect, useState } from "react";
import { useActionLog, useDash, type Action, type DashState } from "@/lib/store";
import type { Dashboard } from "@/lib/types";
import { isTyping, modalOpen } from "./useAutosave";

export type Undo = { undo: () => void; redo: () => void; canUndo: boolean; canRedo: boolean };

const MAX = 50, QUIET = 600;  // 옛 Undo의 max·quiet
// 대시보드 설정(기간 칸에서 확정하면 따로 저장된다): 되돌리기가 쌓지도 되돌리지도 않는다(칸·보는 기간과 어긋나지 않게)
const SETTINGS: string[] = ["as_of", "period", "compare_mode", "refresh", "live"];
const U = { back: [] as Dashboard[], fwd: [] as Dashboard[], busy: false, at: 0, seen: null as Action | null };
const cur: { state: DashState | null; dispatch: (a: Action) => void } = { state: null, dispatch: () => {} };
const subs = new Set<() => void>();
const bump = () => subs.forEach(f => f());

/** 액션 하나를 기록에 반영한다(훅이 여러 번 불려도 같은 액션은 한 번만). 화면이 스스로 붙인 카드 고침(auto)·설정만 바꾼 meta는 쌓지 않는다 */
export function record(a: Action, before: DashState, now = Date.now()) {
  if (a === U.seen) return;
  U.seen = a;
  if (a.type === "open") { U.back = []; U.fwd = []; U.at = 0; return bump(); }
  if (U.busy || !before.dash || (a.type !== "cards" && a.type !== "card" && a.type !== "meta")) return;
  if ((a.type === "card" && a.auto) || (a.type === "meta" && Object.keys(a.patch).every(k => SETTINGS.includes(k)))) return;
  const pushed = now - U.at > QUIET;
  if (pushed) { U.back.push(before.dash); if (U.back.length > MAX) U.back.shift(); }
  U.at = now;
  const had = U.fwd.length;
  U.fwd = [];  // 새 편집 뒤에는 다시 하기가 없다
  if (pushed || had) bump();
}

/** from 맨 위 대시보드로 되돌리고(설정 키는 빼고) 지금 것을 to에 쌓는다 */
export function move(from: Dashboard[], to: Dashboard[]) {
  const dash = cur.state?.dash, target = from.pop();
  if (!dash || !target) return;
  to.push(dash);
  const meta: Partial<Dashboard> = { ...target };
  delete meta.id;
  delete meta.cards;
  SETTINGS.forEach(k => delete meta[k as keyof Dashboard]);
  U.busy = true;  // 되돌리며 보내는 액션은 기록하지 않는다(dispatch는 듣는 쪽을 바로 부른다)
  try {
    cur.dispatch({ type: "cards", cards: target.cards });
    cur.dispatch({ type: "meta", patch: meta });
  } finally { U.busy = false; U.at = 0; }
  bump();
}
const undo = () => move(U.back, U.fwd), redo = () => move(U.fwd, U.back);
/** 순수 시험(__pure__.ts)이 기록·지금 상태를 들여다보고 바꾸는 자리 */
export const undoInternals = { U, cur };

export function useUndo(): Undo {
  const { state, dispatch } = useDash();
  const [, rerender] = useState(0);
  useEffect(() => { cur.state = state; cur.dispatch = dispatch; });
  useActionLog(record);
  useEffect(() => {
    const f = () => rerender(n => n + 1);
    subs.add(f);
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.isComposing || e.altKey || !(e.ctrlKey || e.metaKey) || isTyping(e.target) || modalOpen()) return;
      const k = e.key.toLowerCase();
      if (k !== "z" && k !== "y") return;
      e.preventDefault();
      if (k === "y" || e.shiftKey) redo(); else undo();
    };
    document.addEventListener("keydown", onKey);
    return () => { subs.delete(f); document.removeEventListener("keydown", onKey); };
  }, []);
  return { undo, redo, canUndo: U.back.length > 0, canRedo: U.fwd.length > 0 };
}
