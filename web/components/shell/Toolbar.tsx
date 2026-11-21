"use client";
// 보는 곳(/dashboard, build 아님): [대시보드 ▾] | 기간 칸 … [최신화]·[대시보드만 보기]·[만들기](#d-build, 만드는 곳으로)·[예시 데이터로 확인하기]·[내보내기](#d-more). 만드는 곳(/dashboard/build): 아래 설명에서 [편집]·[대시보드만 보기]가 빠지고(늘 편집 상태) [보러 가기](#d-view, 저장한 뒤 보는 곳으로)가 있다.
// N4 — 머리 줄(.d-tool): [대시보드 ▾][새 대시보드] | 기간 칸(PeriodBar.PeriodField, 설정 창과 같은 것)·"N분 전 최신화"(LastRefreshed) … [최신화](recompute)·[카드 추가](강조 단추 #d-add-open: 편집을 켜고 카드 추가 서랍을 연다)
// ·[편집]·[+ 카드 ▾](편집 중에만: 카드 모음 N5 Gallery·그림으로 고르기(예시 그림 타일을 누르면 빈 카드, Gallery pics)·지표 N6 MetricsWindow·쿼리에서 가져오기)·[대시보드만 보기]·[예시 데이터로 확인하기]·[더 보기 ▾](내보내기 N6 Report·대시보드 설정). [대시보드만 보기] #d-only와 [예시 데이터로 확인하기] #d-demo-bar(연결돼 있으면 한 번 더 눌러야 바꾼다)는 메뉴 밖 도구 줄에 늘 보인다.
// [대시보드만 보기](dashOnly)에서는 이 줄이 화면의 위쪽 줄이 된다: 왼쪽에 대시보드 이름·연결 이름, 오른쪽에 기간 칸·필터 칸(FilterBar)·[최신화]·화면 모드·[보기 끝내기](#d-only-exit). 나머지 단추는 CSS(body.dash-only)가 숨긴다.
// 두 메뉴는 menu 상태 하나("card"·"more")를 같이 써 바깥 클릭·Esc로 닫는다. 카드 추가는 오른쪽 서랍(page.tsx). 편집 모드(state.editing)에서만 격자 끌기·×·크기 손잡이와 카드 설정 패널이 있다.
// 카드 추가(useAddCard)는 옛 화면처럼 값을 먼저 구해(지금 기간) 카드로 넣고 저장한다. 대시보드가 없으면 api.addCard로 "내 대시보드"를 새로 만든다.
import { useEffect, useRef, useState } from "react";
import { MetricsWindow } from "@/components/edit/MetricsWindow";
import { Report } from "@/components/report/Report";
import { newCardId } from "@/components/edit/Templates";
import { Gallery } from "@/components/edit/Shelf";
import { scrollToCard, useAddShell } from "@/components/grid/Grid";
import { addCard, cardRequest, computeCard, connectDemo } from "@/lib/api";
import { onPageNow } from "@/lib/calc";
import { useConstants } from "@/lib/constants";
import { useDash } from "@/lib/store";
import type { Card } from "@/lib/types";
import { Bell } from "./Bell";
import { FilterBar } from "./FilterBar";
import { LastRefreshed } from "./PeriodBar";
import { PickDashboard } from "./PickDashboard";
import { SettingsDialog } from "./SettingsDialog";
import { buildHref, useShell, viewHref } from "./ShellProvider";
import { ThemeButton } from "./TopBar";

/** [SPECIFICATION] plans/phase-16.md#useAddCard
 *  카드 하나를 값을 구해 지금 대시보드에 넣는 함수를 돌려준다(도구 줄 지표 창과 카드 추가 서랍이 같이 쓴다). */
export function useAddCard(): (card: Partial<Card>) => Promise<void> {
  const { state, dispatch } = useDash();
  const { open, flush, page } = useShell();
  const max = useConstants()?.dash.max_cards ?? Infinity;
  const now = useRef(state);
  useEffect(() => { now.current = state; });
  const add = async (card: Partial<Card>) => {
    try {
      const dash = state.dash;
      if (dash) {
        if (dash.cards.length >= max) throw new Error(`대시보드에는 카드를 ${max}개까지 넣을 수 있어요.`);
        const full = onPageNow({ ...card, id: newCardId() } as Card, dash.pages, page), r = await computeCard(cardRequest(full), state.ctx, dash.id);
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

/** 예시 데이터(지어낸 표)에 연결하고 대시보드를 다시 여는 함수를 돌려준다(도구 줄 단추와 빈 화면 안내가 같이 쓴다). */
export function useDemo(): () => void {
  const { dispatch } = useDash();
  return () => { connectDemo().then(() => location.reload(), (e: Error) => dispatch({ type: "error", message: e.message })); };
}

export function Toolbar() {
  const { state, dispatch } = useDash();
  const { recompute, setAddOpen, gallery, setGallery, dashOnly, setDashOnly, conn, build, flush, report, setReport, settings, setSettings } = useShell();
  const demo = useDemo();
  const [sure, setSure] = useState(false);  // 연결돼 있으면 [예시 데이터로 확인하기]를 한 번 더 눌러야 연결을 바꾼다(TopBar [비우기]와 같은 방식)
  const [menu, setMenu] = useState<false | "card" | "more">(false);  // 열린 메뉴: [+ 카드 ▾] 또는 [더 보기 ▾]
  const [metrics, setMetrics] = useState(false);
  const [picker, setPicker] = useState(false);  // 그림으로 고르기 화면
  const addShell = useAddShell();
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
        {dashOnly ? <><b className="d-name">{state.dash?.name || "대시보드"}</b><span className="d-conn" title="지금 연결된 데이터">{conn ? conn.label || "연결됨" : "연결 안 됨"}</span></> : <PickDashboard />}
        <span className="d-sep"></span>
        <LastRefreshed />
        <Bell />
        {dashOnly && <FilterBar />}
        <span className="ml-auto"></span>
        <button className="btn" onClick={again} disabled={busy} title="데이터를 새로 읽어 카드 숫자를 최신으로 고쳐요">최신화</button>
        {build && <button className="btn p" id="d-add-open" disabled={!state.dash} onClick={() => { dispatch({ type: "select", id: null }); setAddOpen(true); }}
          title={state.dash ? "카드 추가 서랍을 열어요. 테이블을 골라 KPI, 표, 차트 같은 카드를 만들어요" : "먼저 대시보드를 고르거나 [새 대시보드]를 만들어요"}>카드 추가</button>}
        {build && (
        <div className="drop" ref={drop} onKeyDown={esc}>
          <button className="btn" aria-haspopup="menu" aria-expanded={menu === "card"} onClick={() => setMenu(menu === "card" ? false : "card")} title="카드 모음, 지표, 쿼리 화면에서 카드를 가져와요">+ 카드 ▾</button>
          {menu === "card" && (
            <div className="pop" role="menu">
              <button role="menuitemcheckbox" aria-checked={gallery} onClick={() => { setMenu(false); setGallery(!gallery); }} title="카드 종류를 끌어 격자에 놓거나 두 번 누르면 빈 카드가 생겨요">카드 모음</button>
              <button role="menuitem" aria-haspopup="dialog" onClick={() => { setMenu(false); setPicker(true); }} title="종류마다 어떤 모양인지 그림으로 보고 골라요. 누르면 빈 카드가 생겨요">그림으로 고르기</button>
              <button role="menuitem" onClick={() => { setMenu(false); setMetrics(true); }} title="내 지표와 지표 사전을 봐요. 쿼리 화면의 [지표] 탭과 같은 내용이에요">지표</button>
              <a role="menuitem" className="btn [text-decoration:none] whitespace-nowrap" href="/search" title="쿼리 화면에서 차트나 지표를 만들고 [대시보드에 추가]를 누르면 여기에 모여요">쿼리에서 가져오기</a>
            </div>
          )}
        </div>
        )}
        {build ? <button className="btn" id="d-view" onClick={() => { flush().then(() => { location.href = viewHref(state.dash?.id); }, (e: Error) => dispatch({ type: "error", message: e.message })); }} title="지금까지 고친 것을 저장하고, 보는 곳에서 이 대시보드를 열어요">보러 가기</button>
          : <>
            <button className="btn" id="d-only" onClick={() => setDashOnly(true)} title="왼쪽 메뉴와 단추를 숨기고 카드만 크게 봐요. Esc를 누르면 돌아와요">대시보드만 보기</button>
            <a className="btn [text-decoration:none]" id="d-build" href={buildHref(state.dash?.id)} title="이 대시보드에 카드를 넣고, 옮기고, 고치는 곳으로 가요">만들기</a>
          </>}
        <button className="btn" id="d-demo-bar" onBlur={() => setSure(false)} onClick={() => { if (conn && !sure) return setSure(true); setSure(false); demo(); }}
          title="지어낸 주문·고객·방문기록 표에 연결하고 대시보드를 다시 열어요. 지금 다른 데이터에 연결돼 있으면 연결이 예시 데이터로 바뀌어요">{sure ? "한 번 더 누르면 연결이 바뀌어요" : "예시 데이터로 확인하기"}</button>
        {!build && <button className="btn" id="d-more" aria-haspopup="dialog" onClick={() => setReport(true)} title="대시보드를 그림(PNG)이나 PDF 보고서로 저장해요">내보내기</button>}
        {dashOnly && <><button className="btn" id="d-settings" aria-haspopup="dialog" onClick={() => setSettings(true)} title="갱신 주기·받는 범위·암호화·기간을 정해요">대시보드 설정</button><ThemeButton /><button id="d-only-exit" className="btn" onClick={() => setDashOnly(false)} title="메뉴와 도구를 다시 보여요(Esc)">보기 끝내기</button></>}
        {build && <div className="drop" ref={more} onKeyDown={esc}>
          <button className="btn" id="d-more" aria-haspopup="menu" aria-expanded={menu === "more"} onClick={() => setMenu(menu === "more" ? false : "more")} title="내보내기, 대시보드 설정">더 보기 ▾</button>
          {menu === "more" && (
            <div className="pop" role="menu">
              <button role="menuitem" aria-haspopup="dialog" onClick={() => { setMenu(false); setReport(true); }} title="대시보드를 그림(PNG)이나 PDF 보고서로 저장해요">내보내기</button>
              <button role="menuitem" aria-haspopup="dialog" onClick={() => { setMenu(false); setSettings(true); }} title="어느 날짜·기간의 숫자를 볼지, 얼마나 자주 새로 읽을지, 카드 색 같은 것을 정해요">대시보드 설정</button>
            </div>
          )}
        </div>}
      </div>
      {picker && (
        <div className="modal" onClick={e => { if (e.target === e.currentTarget) setPicker(false); }} onKeyDown={e => { if (e.key === "Escape") { e.preventDefault(); setPicker(false); } }}>
          <div className="modal-box pick-box" role="dialog" aria-modal="true" aria-label="그림으로 고르기">
            <div className="phead"><b>그림으로 고르기</b><button type="button" className="ghost min-w-[1.5rem] min-h-[1.5rem]" autoFocus aria-label="그림으로 고르기 닫기" title="닫기" onClick={() => setPicker(false)}>×</button></div>
            <Gallery pics onAdd={spec => { setPicker(false); addShell(spec); }} />
          </div>
        </div>
      )}
      <SettingsDialog open={settings} onClose={() => setSettings(false)} />
      <MetricsWindow open={metrics} onClose={() => setMetrics(false)} onAdd={add} />
      <Report open={report} onClose={() => setReport(false)} />
    </>
  );
}
