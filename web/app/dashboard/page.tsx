"use client";
// 대시보드 화면의 뿌리(클라이언트 경계). 상수·상태·화면 틀 Provider 아래에 위쪽 줄·도구 줄·격자를 놓고, 목록을 받아 ?open=id → 마지막에 연 것 → 첫 것 순으로 연다.
// 열 때는 저장된 값을 그대로 보이고, 기간 규칙대로 다시 구할 카드만 연결됐을 때 구한다(ShellProvider). 가운데 격자, 왼쪽 카드 모음, 오른쪽 칸은 카드 설정 패널(편집 모드에서 고른 카드) 또는 카드 추가 서랍(나중에 연 것). 정적 내보내기라 브라우저 값은 useEffect 안에서만 읽는다.
// 제목 줄 아래: 카운터 지표 한계 경고(옛 #d-alerts), 고른 카드 수와 [모두 선택]·[선택 해제](옛 .board-ctl, 편집 중이거나 고른 카드가 있을 때만), 알림 줄(성공 안내는 보통 색 .ok).
import { useEffect, useMemo, useState } from "react";
import { counterAlerts } from "@/components/cards/svg/__pure__";
import { AddCardMenu } from "@/components/edit/AddCardMenu";
import { CardPanel } from "@/components/edit/CardPanel";
import { Gallery } from "@/components/edit/Shelf";
import { Grid, useAddShell } from "@/components/grid/Grid";
import { ShellProvider, useShell } from "@/components/shell/ShellProvider";
import { Toolbar, useAddCard } from "@/components/shell/Toolbar";
import { TopBar } from "@/components/shell/TopBar";
import { listDashboards } from "@/lib/api";
import { ConstantsProvider, useConstants } from "@/lib/constants";
import { DashProvider, selectedCard, useActionLog, useDash } from "@/lib/store";

export default function DashboardPage() {
  return (
    <ConstantsProvider>
      <DashProvider>
        <ShellProvider>
          <TopBar />
          <DashboardView />
        </ShellProvider>
      </DashProvider>
    </ConstantsProvider>
  );
}

function DashboardView() {
  const { state, dispatch } = useDash();
  const { open, dashOnly, setDashOnly, addOpen, setAddOpen, gallery, setGallery, conn } = useShell();
  const consts = useConstants();
  const addShell = useAddShell();
  const add = useAddCard();
  const [savedOnce, setSavedOnce] = useState(false);  // 연 뒤 한 번이라도 저장했나(옛 d-state "저장했어요")
  useActionLog(a => { if (a.type === "open") setSavedOnce(false); else if (a.type === "saved") setSavedOnce(true); });
  useEffect(() => {
    listDashboards().then(list => {
      dispatch({ type: "list", list });
      const want = new URLSearchParams(location.search).get("open") || list.last;
      open(list.dashboards.some(d => d.id === want) ? want : list.dashboards[0]?.id ?? "");
    }).catch(e => dispatch({ type: "error", message: e.message }));
  }, [dispatch, open]);
  useEffect(() => { if (state.selected) setAddOpen(false); }, [state.selected, setAddOpen]);  // 카드를 고르면 서랍 자리에 카드 설정이 온다
  // 대시보드만 보기: 메뉴·도구를 숨기고(body.dash-only, 옛 CSS) 선택·편집 모드를 푼다. Esc가 떠 있는 창을 닫았으면(defaultPrevented) 보기는 그대로
  useEffect(() => {
    document.body.classList.toggle("dash-only", dashOnly);
    if (!dashOnly) return;
    dispatch({ type: "select", id: null });
    dispatch({ type: "pick", ids: [] });
    dispatch({ type: "editing", on: false });
    document.getElementById("d-only-exit")?.focus();
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape" && !e.defaultPrevented) setDashOnly(false); };
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("keydown", esc); document.getElementById("d-only")?.focus(); };
  }, [dashOnly, setDashOnly, dispatch]);
  const n = state.dash?.cards.length ?? 0, picked = state.picked.length;
  const merge = consts?.merge, alerts = useMemo(() => !state.dash || !merge ? [] :
    counterAlerts(state.dash.cards.map(c => ({ ...c, data: state.values[c.id]?.data ?? c.data })), state.dash.north_star, state.ctx?.range, merge), [state.dash, state.values, state.ctx, merge]);
  const card = state.editing && !dashOnly ? selectedCard(state) : null;  // 카드 설정 패널은 편집 모드에서 고른 카드만
  return (
    <div className="board-view" id="dash">
      <Toolbar />
      {dashOnly && <button id="d-only-exit" className="btn" onClick={() => setDashOnly(false)} title="메뉴와 도구를 다시 보여요(Esc)">보기 끝내기</button>}
      <div className="big"><b>{state.dash?.name || "대시보드"}</b><span className="d-conn" title="지금 연결된 데이터">{conn ? conn.label || "연결됨" : "연결 안 됨"}</span><span>{n ? `카드 ${n}개` : ""}</span><span className="hint" id="d-state" role="status" style={{ marginLeft: 12 }}>{state.dirty ? "저장 중…" : savedOnce ? "저장했어요" : ""}</span></div>
      {alerts.length > 0 && <div className="d-alerts" id="d-alerts" role="alert"><b>카운터 지표가 한계를 벗어났어요</b>{alerts.map(f => <div key={f.id}>{f.text}</div>)}</div>}
      {n > 0 && (state.editing || picked > 0) && (
        <div className="ctl board-ctl">
          <span className="hint" id="d-picked" role="status">{picked ? `카드 ${picked}개 선택함` : "저장할 카드를 눌러 선택하세요"}</span>
          <button id="d-all" className="ghost" onClick={() => dispatch({ type: "pick", ids: state.dash?.cards.map(c => c.id) ?? [] })}>모두 선택</button>
          <button id="d-none" className="ghost" onClick={() => dispatch({ type: "pick", ids: [] })}>선택 해제</button>
        </div>
      )}
      <div className={state.locked || !state.ok ? "err" : "ok"} id="err-dash" role="status">{state.locked ? consts?.dash.locked : state.error}</div>
      {!n && (
        <div className="board-empty" style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
          <p className="hint" style={{ fontSize: 14 }}>{state.dash ? "아직 카드가 없어요." : "저장한 대시보드가 없어요."} 이렇게 시작해요.</p>
          <ol className="hint" style={{ fontSize: 14, margin: 0, padding: 0, listStyle: "none", lineHeight: 1.8 }}><li>① [카드 추가]를 눌러요</li><li>② 보고 싶은 카드 종류를 골라요</li></ol>
          <p className="hint" style={{ fontSize: 14 }}>또는 쿼리 화면에서 차트를 만들고 [대시보드에 추가]를 눌러요.</p>
          <div className="row" style={{ display: "flex", gap: 12 }}>
            <button className="btn p" style={{ height: 44, padding: "0 22px", fontSize: 15 }} onClick={e => { e.stopPropagation(); dispatch({ type: "select", id: null }); setAddOpen(true); }}>카드 추가</button>
            <a className="btn" style={{ height: 44, padding: "0 22px", fontSize: 15, textDecoration: "none" }} href="/search">쿼리에서 가져오기</a>
          </div>
        </div>
      )}
      <div className="d-main">
        {gallery && !dashOnly && (
          <aside id="d-gallery-box" aria-label="카드 모음">
            <div className="phead"><b>카드 모음</b><button type="button" className="ghost" aria-label="카드 모음 닫기" title="카드 모음 닫기" style={{ minWidth: 24, minHeight: 24 }} onClick={() => setGallery(false)}>×</button></div>
            <Gallery onAdd={spec => addShell(spec)} />
          </aside>
        )}
        <div><Grid /></div>
        {addOpen && !dashOnly ? <AddCardMenu open onClose={() => setAddOpen(false)} onAdd={add} /> : <CardPanel card={card} onChange={patch => card && dispatch({ type: "card", id: card.id, patch })} onClose={() => dispatch({ type: "select", id: null })} />}
      </div>
    </div>
  );
}
