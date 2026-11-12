"use client";
// N4 — 위 도구 줄(옛 .d-tool, UI 지적 14로 묶음): [대시보드 ▾][새 대시보드] | [편집] [+ 카드 ▾](카드 추가 N5 AddCardMenu·카드 모음 N5 Gallery·지표 N6 MetricsWindow·쿼리에서 가져오기) [내보내기 ▾](N6 Report)
// | 연결·다시 구하기·설정·대시보드만 보기. 편집 모드(state.editing)에서만 격자 끌기·×·크기 손잡이와 카드 설정 패널이 있다.
// 카드 추가는 옛 화면처럼 값을 먼저 구해(지금 기간) 카드로 넣고 저장한다. 대시보드가 없으면 api.addCard로 "내 대시보드"를 새로 만든다.
import { useEffect, useRef, useState } from "react";
import { AddCardMenu } from "@/components/edit/AddCardMenu";
import { MetricsWindow } from "@/components/edit/MetricsWindow";
import { Report } from "@/components/report/Report";
import { newCardId } from "@/components/edit/Templates";
import { scrollToCard } from "@/components/grid/Grid";
import { addCard, cardRequest, computeCard } from "@/lib/api";
import { useConstants } from "@/lib/constants";
import { useDash } from "@/lib/store";
import type { Card } from "@/lib/types";
import { PickDashboard } from "./PickDashboard";
import { SettingsDialog } from "./SettingsDialog";
import { useShell } from "./ShellProvider";

export function Toolbar() {
  const { state, dispatch } = useDash();
  const { conn, open, flush, recompute, addOpen, setAddOpen, gallery, setGallery, setDashOnly } = useShell();
  const [menu, setMenu] = useState(false);
  const [metrics, setMetrics] = useState(false);
  const [report, setReport] = useState(false);
  const [settings, setSettings] = useState(false);
  const [busy, setBusy] = useState(false);
  const drop = useRef<HTMLDivElement>(null);
  const max = useConstants()?.dash.max_cards ?? Infinity;
  const now = useRef(state);
  useEffect(() => { now.current = state; });
  // 메뉴 밖을 누르면 닫는다(body 수준 목록 .menu의 클릭은 닫지 않는다)
  useEffect(() => {
    if (!menu && !addOpen) return;
    const off = (e: MouseEvent) => { const t = e.target as Element; if (!drop.current?.contains(t) && !t.closest?.(".menu")) { setMenu(false); setAddOpen(false); } };
    document.addEventListener("click", off);
    return () => document.removeEventListener("click", off);
  }, [menu, addOpen, setAddOpen]);
  const add = async (card: Partial<Card>) => {
    try {
      const dash = state.dash;
      if (dash) {
        if (dash.cards.length >= max) throw new Error(`대시보드에는 카드를 ${max}개까지 넣을 수 있어요.`);
        const full = { ...card, id: newCardId() } as Card, r = await computeCard(cardRequest(full), state.ctx);
        const cur = now.current.dash;
        if (cur?.id !== dash.id) throw new Error("값을 구하는 사이 다른 대시보드를 열어서 카드를 넣지 않았어요.");
        dispatch({ type: "cards", cards: [...cur.cards, { ...full, source: { ...full.source, ...r.source }, data: r.data }] });
        scrollToCard(full.id);
        await flush();
        dispatch({ type: "notice", message: `카드를 넣었어요. 지금 카드는 ${cur.cards.length + 1}개예요.` });
        return;
      }
      await flush();
      const made = await addCard(state.dash?.id ?? "", card, state.dash ? undefined : "내 대시보드");
      dispatch({ type: "list", list: made });
      await open(made.dashboard.id);
      const last = now.current.dash?.cards.at(-1);
      if (last) scrollToCard(last.id);
      dispatch({ type: "notice", message: `카드를 넣었어요. 지금 카드는 ${made.dashboard.cards}개예요.` });
    } catch (e) { dispatch({ type: "error", message: (e as Error).message }); }
  };
  const again = async () => { setBusy(true); try { await recompute(); } finally { setBusy(false); } };
  return (
    <>
      <div className="tool d-tool">
        <PickDashboard />
        <span className="d-sep"></span>
        <button className={"btn" + (state.editing ? " on" : "")} aria-pressed={state.editing} disabled={!state.dash} onClick={() => dispatch({ type: "editing", on: !state.editing })}
          title={state.dash ? "카드를 끌어 옮기고, 크기를 바꾸고, 빼고, 설정해요" : "대시보드를 고르거나 [새 대시보드]·[+ 카드]로 만들면 편집할 수 있어요"}>{state.editing ? "편집 끝내기" : "편집"}</button>
        <div className="drop" ref={drop} onKeyDown={e => { if (e.key === "Escape" && menu) { setMenu(false); drop.current?.querySelector<HTMLElement>("button")?.focus(); } }}>
          <button className="btn" aria-haspopup="menu" aria-expanded={menu} onClick={() => { setMenu(!menu); setAddOpen(false); }} title="카드를 넣거나 지표를 봐요">+ 카드 ▾</button>
          {menu && (
            <div className="pop" role="menu" style={{ left: 0, right: "auto" }}>
              <button role="menuitem" onClick={() => { setMenu(false); setAddOpen(true); }} title="테이블을 골라 KPI, 표, 문장 같은 카드를 바로 만들어요">카드 추가</button>
              <button role="menuitemcheckbox" aria-checked={gallery} onClick={() => { setMenu(false); setGallery(!gallery); if (!gallery) dispatch({ type: "editing", on: true }); }} title="카드 종류를 끌어 격자에 놓거나 두 번 누르면 빈 카드가 생겨요">카드 모음</button>
              <button role="menuitem" onClick={() => { setMenu(false); setMetrics(true); }} title="내 지표와 지표 사전을 봐요. 쿼리 화면의 [지표] 탭과 같은 내용이에요">지표</button>
              <a role="menuitem" className="btn" href="/search" style={{ textDecoration: "none", whiteSpace: "nowrap" }} title="쿼리 화면에서 차트나 지표를 만들고 [대시보드에 추가]를 누르면 여기에 모여요">쿼리에서 가져오기</a>
            </div>
          )}
          <AddCardMenu open={addOpen} onClose={() => setAddOpen(false)} onAdd={add} />
        </div>
        <button className="btn" onClick={() => setReport(true)} aria-haspopup="dialog" title="카드를 그림이나 PNG·PDF 보고서로 저장해요">내보내기 ▾</button>
        <span style={{ marginLeft: "auto" }}></span>
        <a className="btn" href="/search" style={{ textDecoration: "none" }} title={conn ? "다른 데이터에 연결하려면 쿼리 화면에서 연결해요" : "쿼리 화면에서 데이터에 연결해요. 연결하면 대시보드 값을 최신으로 바꿔요"}>{conn ? conn.label || "연결됨" : "연결"}</a>
        <button className="btn" onClick={again} disabled={busy} title="모든 카드 값을 지금 연결에서 다시 구해 저장해요">다시 구하기</button>
        <button className="btn" onClick={() => setSettings(true)} aria-haspopup="dialog" title="기준일, 기간, 최신화 주기, 카드 색, 목표 파일을 정해요">설정</button>
        <button className="btn" id="d-only" onClick={() => setDashOnly(true)} title="메뉴와 도구를 숨기고 대시보드만 봐요. Esc나 오른쪽 위 [보기 끝내기]로 돌아와요">대시보드만 보기</button>
      </div>
      <SettingsDialog open={settings} onClose={() => setSettings(false)} />
      <MetricsWindow open={metrics} onClose={() => setMetrics(false)} onAdd={add} />
      <Report open={report} onClose={() => setReport(false)} />
    </>
  );
}
