"use client";
// N4 — 머리 줄(.d-tool): [대시보드 ▾][새 대시보드] | 기간 칸(PeriodBar.PeriodField, 설정 창과 같은 것)·"N분 전 최신화"(LastRefreshed) … [최신화](recompute)·[카드 추가](강조 단추 #d-add-open: 편집을 켜고 카드 추가 서랍을 연다)
// ·[편집]·[+ 카드 ▾](편집 중에만: 카드 추가·카드 모음 N5 Gallery·지표 N6 MetricsWindow·쿼리에서 가져오기)·[더 보기 ▾](내보내기 N6 Report·대시보드 설정·대시보드만 보기 #d-only·예시 데이터로 확인하기 #d-demo-menu: 연결돼 있으면 한 번 더 눌러야 바꾼다).
// 두 메뉴는 menu 상태 하나("card"·"more")를 같이 써 바깥 클릭·Esc로 닫는다. 카드 추가는 오른쪽 서랍(page.tsx). 편집 모드(state.editing)에서만 격자 끌기·×·크기 손잡이와 카드 설정 패널이 있다.
// 카드 추가(useAddCard)는 옛 화면처럼 값을 먼저 구해(지금 기간) 카드로 넣고 저장한다. 대시보드가 없으면 api.addCard로 "내 대시보드"를 새로 만든다.
import { useEffect, useRef, useState } from "react";
import { MetricsWindow } from "@/components/edit/MetricsWindow";
import { Report } from "@/components/report/Report";
import { newCardId } from "@/components/edit/Templates";
import { scrollToCard } from "@/components/grid/Grid";
import { addCard, cardRequest, computeCard, connectDemo } from "@/lib/api";
import { useConstants } from "@/lib/constants";
import { useDash } from "@/lib/store";
import type { Card } from "@/lib/types";
import { LastRefreshed, PeriodField } from "./PeriodBar";
import { PickDashboard } from "./PickDashboard";
import { SettingsDialog } from "./SettingsDialog";
import { useShell } from "./ShellProvider";

/** [SPECIFICATION] plans/phase-16.md#useAddCard
 *  카드 하나를 값을 구해 지금 대시보드에 넣는 함수를 돌려준다(도구 줄 지표 창과 카드 추가 서랍이 같이 쓴다). */
export function useAddCard(): (card: Partial<Card>) => Promise<void> {
  const { state, dispatch } = useDash();
  const { open, flush } = useShell();
  const max = useConstants()?.dash.max_cards ?? Infinity;
  const now = useRef(state);
  useEffect(() => { now.current = state; });
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
  return add;
}

/** 예시 데이터(지어낸 표)에 연결하고 대시보드를 다시 여는 함수를 돌려준다([더 보기 ▾]와 빈 화면 안내가 같이 쓴다). */
export function useDemo(): () => void {
  const { dispatch } = useDash();
  return () => { connectDemo().then(() => location.reload(), (e: Error) => dispatch({ type: "error", message: e.message })); };
}

export function Toolbar() {
  const { state, dispatch } = useDash();
  const { recompute, setAddOpen, gallery, setGallery, setDashOnly, conn } = useShell();
  const demo = useDemo();
  const [sure, setSure] = useState(false);  // 연결돼 있으면 [예시 데이터로 확인하기]를 한 번 더 눌러야 연결을 바꾼다(TopBar [비우기]와 같은 방식)
  const [menu, setMenu] = useState<false | "card" | "more">(false);  // 열린 메뉴: [+ 카드 ▾] 또는 [더 보기 ▾]
  const [metrics, setMetrics] = useState(false);
  const [report, setReport] = useState(false);
  const [settings, setSettings] = useState(false);
  const [busy, setBusy] = useState(false);
  const drop = useRef<HTMLDivElement>(null), more = useRef<HTMLDivElement>(null);
  const add = useAddCard();
  // 열린 메뉴 밖을 누르면 닫는다(body 수준 목록 .menu의 클릭은 닫지 않는다). 서랍은 도구 줄 밖이라 여기서 닫지 않는다
  useEffect(() => { if (!menu) return; const off = (e: MouseEvent) => { const t = e.target as Element; if (!drop.current?.contains(t) && !more.current?.contains(t) && !t.closest?.(".menu")) setMenu(false); }; document.addEventListener("click", off); return () => document.removeEventListener("click", off); }, [menu]);
  // Esc: 열린 메뉴를 닫고 그 메뉴를 연 단추로 초점을 돌려준다
  const esc = (e: React.KeyboardEvent<HTMLDivElement>) => { if (e.key === "Escape" && menu) { setMenu(false); e.currentTarget.querySelector<HTMLElement>("button")?.focus(); } };
  const again = async () => { setBusy(true); try { await recompute(); } finally { setBusy(false); } };
  return (
    <>
      <div className="tool d-tool">
        <PickDashboard />
        <span className="d-sep"></span>
        <div className="dc-field d-per"><PeriodField id="d-per" /></div>
        <LastRefreshed />
        <span style={{ marginLeft: "auto" }}></span>
        <button className="btn" onClick={again} disabled={busy} title="데이터를 새로 읽어 카드 숫자를 최신으로 고쳐요">최신화</button>
        <button className="btn p" id="d-add-open" disabled={!state.dash} onClick={() => { dispatch({ type: "editing", on: true }); dispatch({ type: "select", id: null }); setAddOpen(true); }}
          title={state.dash ? "편집을 켜고 카드 추가 서랍을 열어요. 테이블을 골라 KPI, 표, 차트 같은 카드를 만들어요" : "먼저 대시보드를 고르거나 [새 대시보드]를 만들어요"}>카드 추가</button>
        <button className={"btn" + (state.editing ? " on" : "")} aria-pressed={state.editing} disabled={!state.dash} onClick={() => { if (state.editing) setAddOpen(false); dispatch({ type: "editing", on: !state.editing }); }}
          title={state.dash ? "카드를 옮기고, 크기를 바꾸고, 빼고, 고칠 수 있게 해요" : "먼저 대시보드를 고르거나 [새 대시보드]를 만들어요"}>{state.editing ? "편집 끝내기" : "편집"}</button>
        {state.editing && (
        <div className="drop" ref={drop} onKeyDown={esc}>
          <button className="btn" aria-haspopup="menu" aria-expanded={menu === "card"} onClick={() => setMenu(menu === "card" ? false : "card")} title="대시보드에 새 카드(숫자·표·차트)를 넣어요">+ 카드 ▾</button>
          {menu === "card" && (
            <div className="pop" role="menu">
              <button role="menuitem" onClick={() => { setMenu(false); dispatch({ type: "select", id: null }); setAddOpen(true); }} title="테이블을 골라 KPI, 표, 문장 같은 카드를 바로 만들어요">카드 추가</button>
              <button role="menuitemcheckbox" aria-checked={gallery} onClick={() => { setMenu(false); setGallery(!gallery); if (!gallery) dispatch({ type: "editing", on: true }); }} title="카드 종류를 끌어 격자에 놓거나 두 번 누르면 빈 카드가 생겨요">카드 모음</button>
              <button role="menuitem" onClick={() => { setMenu(false); setMetrics(true); }} title="내 지표와 지표 사전을 봐요. 쿼리 화면의 [지표] 탭과 같은 내용이에요">지표</button>
              <a role="menuitem" className="btn" href="/search" style={{ textDecoration: "none", whiteSpace: "nowrap" }} title="쿼리 화면에서 차트나 지표를 만들고 [대시보드에 추가]를 누르면 여기에 모여요">쿼리에서 가져오기</a>
            </div>
          )}
        </div>
        )}
        <div className="drop" ref={more} onKeyDown={esc}>
          <button className="btn" id="d-more" aria-haspopup="menu" aria-expanded={menu === "more"} onClick={() => setMenu(menu === "more" ? false : "more")} title="내보내기, 대시보드 설정, 대시보드만 보기, 예시 데이터로 확인하기">더 보기 ▾</button>
          {menu === "more" && (
            <div className="pop" role="menu">
              <button role="menuitem" aria-haspopup="dialog" onClick={() => { setMenu(false); setReport(true); }} title="대시보드를 그림(PNG)이나 PDF 보고서로 저장해요">내보내기</button>
              <button role="menuitem" aria-haspopup="dialog" onClick={() => { setMenu(false); setSettings(true); }} title="어느 날짜·기간의 숫자를 볼지, 얼마나 자주 새로 읽을지, 카드 색 같은 것을 정해요">대시보드 설정</button>
              <button role="menuitem" id="d-only" onClick={() => { setMenu(false); setDashOnly(true); }} title="왼쪽 메뉴와 단추를 숨기고 카드만 크게 봐요. Esc를 누르면 돌아와요">대시보드만 보기</button>
              <button role="menuitem" id="d-demo-menu" onBlur={() => setSure(false)} onClick={() => { if (conn && !sure) return setSure(true); setMenu(false); demo(); }}
                title="지어낸 주문·고객·방문기록 표에 연결하고 대시보드를 다시 열어요. 지금 다른 데이터에 연결돼 있으면 연결이 예시 데이터로 바뀌어요">{sure ? "한 번 더 누르면 연결이 바뀌어요" : "예시 데이터로 확인하기"}</button>
            </div>
          )}
        </div>
      </div>
      <SettingsDialog open={settings} onClose={() => setSettings(false)} />
      <MetricsWindow open={metrics} onClose={() => setMetrics(false)} onAdd={add} />
      <Report open={report} onClose={() => setReport(false)} />
    </>
  );
}
