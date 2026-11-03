"use client";
// 대시보드 화면 상태(useReducer + Context): 목록, 연 대시보드(카드 포함), 실시간·기간으로 구한 값(저장하지 않음), 선택 카드, 편집 모드, 기간 ctx, dirty.
// picked는 그림으로 저장할 카드들(여러 개, 옛 board.picked), selected는 카드 설정 패널을 여는 카드 하나. error는 아래 알림 줄의 글이고 ok면 성공 안내(보통 색, 옛 say(…, true)).
// 자동 저장·되돌리기(N6)는 useActionLog로 모든 액션을 받아 붙인다. 대시보드 열기는 화면 틀(components/shell/ShellProvider의 open) 한 곳에서 한다. 카드·이름·설정을 바꾸는 액션은 dirty를 켜고 saved가 끈다.
import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { Card, ComputeResult, Ctx, DashList, DashSummary, Dashboard } from "./types";

export type DashState = {
  list: DashSummary[];
  locked: boolean;
  last: string;          // 첫 화면에서 마지막으로 연 id(GET /api/dash)
  dash: Dashboard | null; // 연 대시보드. 카드는 dash.cards(저장할 모양)
  values: Record<string, ComputeResult>; // 카드 id → 실시간·기간으로 구한 값(화면에만, 저장하지 않음. 옛 showLive)
  selected: string | null;
  picked: string[];      // 고른 카드 id들(카드를 누르면 넣고 빼기, [모두 선택]·[선택 해제])
  editing: boolean;
  ctx: Ctx | null;       // 보는 기간·직전 기간·기준일(/api/period/parse의 context). 카드를 구할 때 보낸다
  dirty: boolean;        // 마지막 저장 뒤 바뀐 것이 있음
  error: string;
  ok: boolean;           // error가 성공 안내인가(notice)
};

export type Action =
  | { type: "list"; list: DashList }
  | { type: "open"; dash: Dashboard | null }                  // 다른 대시보드를 연다(값·선택·dirty를 비운다)
  | { type: "cards"; cards: Card[] }                           // 카드 목록 통째로(격자 옮기기·지우기·되돌리기)
  | { type: "card"; id: string; patch: Partial<Card>; auto?: boolean } // 카드 하나 고치기(패널·규칙·선반). auto: 화면이 스스로 붙인 것(새 라벨 색 순서) — 되돌리기에 쌓지 않는다
  | { type: "meta"; patch: Partial<Omit<Dashboard, "id" | "cards">> } // 이름·팔레트·기간·기준일·주기·북극성
  | { type: "values"; results: ComputeResult[] }               // 구한 값(덮어쓰기)
  | { type: "select"; id: string | null }
  | { type: "pick"; ids: string[] }                           // 고른 카드들(통째로)
  | { type: "editing"; on: boolean }
  | { type: "ctx"; ctx: Ctx | null }
  | { type: "saved"; dash?: Dashboard }                        // 저장 끝: dirty 끔(서버가 돌려준 모양이 있으면 data는 화면 것을 유지하고 나머지를 맞춘다)
  | { type: "error"; message: string }
  | { type: "notice"; message: string };                     // 성공 안내(옛 say(…, true))

export const initialState: DashState = { list: [], locked: false, last: "", dash: null, values: {}, selected: null, picked: [], editing: false, ctx: null, dirty: false, error: "", ok: false };

export function reducer(s: DashState, a: Action): DashState {
  switch (a.type) {
    case "list": return { ...s, list: a.list.dashboards, locked: a.list.locked, last: a.list.last };
    case "open": return { ...s, dash: a.dash, values: {}, selected: null, picked: [], dirty: false, error: "", ok: false };
    case "cards": return s.dash ? { ...s, dash: { ...s.dash, cards: a.cards }, picked: s.picked.filter(id => a.cards.some(c => c.id === id)), dirty: true } : s;  // 뺀 카드는 고른 것에서도
    case "card": return s.dash ? { ...s, dash: { ...s.dash, cards: s.dash.cards.map(c => (c.id === a.id ? { ...c, ...a.patch } : c)) }, dirty: true } : s;
    case "meta": return s.dash ? { ...s, dash: { ...s.dash, ...a.patch }, dirty: true } : s;
    case "values": return { ...s, values: { ...s.values, ...Object.fromEntries(a.results.map(r => [r.id, r])) } };
    case "select": return { ...s, selected: a.id };
    case "pick": return { ...s, picked: a.ids };
    case "editing": return { ...s, editing: a.on };
    case "ctx": return { ...s, ctx: a.ctx };
    case "saved": {
      if (!a.dash || !s.dash) return { ...s, dirty: false };
      const data = Object.fromEntries(s.dash.cards.map(c => [c.id, c.data]));  // 저장 응답에는 카드 값(data)이 빠져 있다
      return { ...s, dash: { ...a.dash, cards: a.dash.cards.map(c => ({ ...c, data: data[c.id] ?? c.data ?? {} })) }, dirty: false };
    }
    case "error": return { ...s, error: a.message, ok: false };
    case "notice": return { ...s, error: a.message, ok: true };
  }
}

type Listener = (a: Action, before: DashState) => void;
const Store = createContext<{ state: DashState; dispatch: (a: Action) => void; listeners: Set<Listener> } | null>(null);

export function DashProvider({ children }: { children: React.ReactNode }) {
  const [state, raw] = useReducer(reducer, initialState);
  const [listeners] = useState(() => new Set<Listener>());
  const now = useRef(state);
  useEffect(() => { now.current = state; });
  const dispatch = useCallback((a: Action) => {
    listeners.forEach(l => l(a, now.current));  // 바뀌기 전 상태와 함께(되돌리기가 그 전 카드를 쌓는다)
    raw(a);
  }, [listeners]);
  const value = useMemo(() => ({ state, dispatch, listeners }), [state, dispatch, listeners]);
  return <Store.Provider value={value}>{children}</Store.Provider>;
}

/** 대시보드 상태와 dispatch */
export function useDash() {
  const found = useContext(Store);
  if (!found) throw new Error("DashProvider 안에서만 쓸 수 있어요.");
  return { state: found.state, dispatch: found.dispatch };
}

/** 모든 액션을 (액션, 바뀌기 전 상태)로 받는다(자동 저장·되돌리기 자리). fn은 매번 새로 만들어도 된다 */
export function useActionLog(fn: Listener) {
  const listeners = useContext(Store)?.listeners;
  const latest = useRef(fn);
  useEffect(() => { latest.current = fn; });
  useEffect(() => {
    if (!listeners) return;
    const l: Listener = (a, before) => latest.current(a, before);
    listeners.add(l);
    return () => { listeners.delete(l); };
  }, [listeners]);
}

/** 고른 카드(없으면 null) */
export const selectedCard = (s: DashState) => s.dash?.cards.find(c => c.id === s.selected) ?? null;
