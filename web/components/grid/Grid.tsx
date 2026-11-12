"use client";
// N3 — 카드 격자와 카드 상자(옛 dash_edit.js EditBoard·board.js cardBox). N7: 카드 모음 놓기(옛 dash_panel.js acceptGalleryDrops·dashboard.html addShell), 비교 문장·다시 구하지 못한 이유.
// 12칸 × 40px 격자(dash_edit.css .board.grid)에 card.layout대로 놓는다. 편집 모드에서만 제목 줄 끌기·⚙·×·크기 손잡이가 있다(대시보드만 보기에서는 고르기·편집 없음).
// 카드를 누르면 고른 카드(state.picked, 여러 개, .sel)에 넣고 빼고(옛 board.toggle), 넣을 때는 그 카드를 설정 패널 대상(selected, 편집 모드에서 .editing)으로도 삼는다.
// 끌기·크기는 useDrag가 미리보기를 주고, 놓으면 dispatch({type:"cards"})로 layout을 바꾼다(세로 압축은 calc가 한다). 카드가 없어도 격자를 그려 카드 모음을 놓을 수 있다.
import { useCallback, useRef, useState, type DragEvent, type KeyboardEvent } from "react";
import { CardBody } from "@/components/cards/CardBody";
import { CHART_KINDS } from "@/components/cards/ChartCard";
import { newCardId } from "@/components/edit/Templates";
import { CARD_MIME, isShell, shellOf, type Spec } from "@/components/edit/menu/kinds";
import { useShell } from "@/components/shell/ShellProvider";
import { cardValue } from "@/components/cards/svg/__pure__";
import { GRID_COLS, cellAt, clampLayout, isPoints, moveCard, nextLayout } from "@/lib/calc";
import { useConstants } from "@/lib/constants";
import { useDash } from "@/lib/store";
import { sameSource } from "@/lib/series";
import type { Card, Ctx, Layout, Source } from "@/lib/types";
import { metrics, useDrag } from "./useDrag";

// 빈 틀 카드(템플릿·카드 모음에서 막 놓은 카드)의 안내
const SHELL_NOTE = "아직 데이터가 없어요. 제목 옆 ⚙를 눌러 테이블과 컬럼을 고르세요.";

/** 카드 아래 안내(옛 dash.js .bwhen): 다른 연결의 이전 값, 값을 구한 시각, 서버 안내, 저장된 일 값의 시작일, 기간 기준 날짜, 연결해서 다시 구해야 하는 집계. 한 줄에 하나 */
export function whenLines(card: Card, d: Card["data"], range: Ctx["range"], conn: Source, merge: string[]) {
  const first = d.by_unit?.day?.labels[0], paint = !CHART_KINDS.includes(card.kind);
  const noDate = range && !d.by_unit && !card.source?.date && !["recent_rows", "status", "alerts", "map"].includes(card.kind) && !isPoints(card);
  return [
    conn && card.source?.key && !sameSource(card.source.key, conn.key) ? "다른 연결에서 구한 이전 값이에요" : "",
    d.computed_at ? "값을 구한 시각 " + d.computed_at.replace("T", " ") : "",
    d.note,
    range?.start && first && range.start < first ? `저장된 일 값은 ${first}부터예요` : "",
    card.source?.date && !d.by_unit ? `기간 기준 날짜: ${card.source.date}` : noDate ? "기간·기준일은 날짜 X축 카드나 기간 기준 날짜 컬럼을 고른 카드에만 적용돼요" : "",
    range && paint && d.by_unit?.day?.series.some((s, i) => !merge.includes(s.agg) && cardValue(card, i, range, merge) == null) ? "이 집계(고유 개수·중앙값 …)는 기간을 바꾸면 연결해서 다시 구해야 해요" : "",
  ].filter(Boolean).join("\n");
}

/** [SPECIFICATION] plans/phase-15.md#scrollToCard
 *  격자에서 id 카드(section[data-id])를 다음 그리기 뒤에 화면 안으로 스크롤한다. */
export function scrollToCard(id: string): void {
  requestAnimationFrame(() => document.querySelector<HTMLElement>(`section[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
}

/** 빈 틀 카드 하나 넣기(옛 addShell): 다음 빈 자리(놓은 칸이 있으면 그 자리, 겹치면 다른 카드가 밀린다)에 놓고 편집 모드로 그 카드를 고른다 */
export function useAddShell() {
  const { state, dispatch } = useDash();
  const max = useConstants()?.dash.max_cards ?? Infinity;
  return useCallback((spec: Spec, cell?: { x: number; y: number }) => {
    const dash = state.dash;
    if (!dash) return dispatch({ type: "error", message: "대시보드를 먼저 고르거나 새로 만드세요." });
    if (dash.cards.length >= max) return dispatch({ type: "error", message: `대시보드에는 카드를 ${max}개까지 넣을 수 있어요.` });
    const items = dash.cards.map(c => ({ id: c.id, layout: clampLayout(c.layout) }));
    const card = { ...shellOf(spec), id: newCardId(), layout: nextLayout(items, 6, 8) } as Card;
    let cards = [...dash.cards, card];
    if (cell) {
      const at = moveCard([...items, { id: card.id, layout: card.layout }], card.id, { x: Math.min(Math.max(0, cell.x), GRID_COLS - 6), y: Math.max(0, cell.y) });
      cards = cards.map(c => ({ ...c, layout: at[c.id] ?? c.layout }));
    }
    dispatch({ type: "cards", cards });
    dispatch({ type: "editing", on: true });
    dispatch({ type: "select", id: card.id });
    scrollToCard(card.id);
    dispatch({ type: "notice", message: `빈 ${spec.name} 카드를 놓았어요. 오른쪽 카드 설정에서 데이터를 정하세요.` });
  }, [state.dash, dispatch, max]);
}

function WhenLine({ card, d }: { card: Card; d: Card["data"] }) {
  const { state } = useDash(), { conn } = useShell(), merge = useConstants()?.dash.merge ?? [];
  return <div className="hint bwhen" style={{ padding: "0 10px" }}>{whenLines(card, d ?? {}, state.ctx?.range, conn, merge)}</div>;
}

export function Grid() {
  const { state, dispatch } = useDash();
  const { compares, dashOnly } = useShell();
  const addShell = useAddShell();
  const ref = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  const dash = state.dash;
  const cards = dash?.cards ?? [];
  const { preview, startMove, startResize } = useDrag(
    ref,
    cards.map(c => ({ id: c.id, layout: clampLayout(c.layout) })),
    layouts => {
      const same = cards.every(c => !layouts[c.id] || JSON.stringify(layouts[c.id]) === JSON.stringify(clampLayout(c.layout)));
      if (!same) dispatch({ type: "cards", cards: cards.map(c => (layouts[c.id] ? { ...c, layout: layouts[c.id] } : c)) });
    },
  );
  if (!dash) return null;
  const star = dash.north_star, edit = state.editing && !dashOnly;

  function remove(id: string) {
    dispatch({ type: "cards", cards: cards.filter(c => c.id !== id) });
    if (state.selected === id) dispatch({ type: "select", id: null });
    if (star.card === id || star.counters.includes(id)) {  // 지운 카드의 id가 남으면 서버가 저장을 거부한다(liveStar)
      const counters = star.counters.filter(c => c !== id);
      dispatch({ type: "meta", patch: { north_star: { card: star.card === id ? "" : star.card, counters, limits: Object.fromEntries(Object.entries(star.limits ?? {}).filter(([k]) => counters.includes(k))) } } });
    }
  }
  const pick = (id: string) => {
    if (dashOnly) return;
    const on = !state.picked.includes(id);
    dispatch({ type: "pick", ids: on ? [...state.picked, id] : state.picked.filter(p => p !== id) });
    if (on) dispatch({ type: "select", id }); else if (state.selected === id) dispatch({ type: "select", id: null });
  };
  const key = (e: KeyboardEvent, id: string) => {
    if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); pick(id); }
  };
  // 카드 모음 칸을 끌어 놓기
  const gallery = (e: DragEvent) => !dashOnly && [...e.dataTransfer.types].includes(CARD_MIME);
  const drop = (e: DragEvent) => {
    setReady(false);
    const raw = e.dataTransfer.getData(CARD_MIME);
    if (!raw || !ref.current) return;
    e.preventDefault();
    addShell(JSON.parse(raw) as Spec, cellAt(e.clientX, e.clientY, metrics(ref.current)));
  };

  return (
    <div className={"board grid" + (ready ? " drop-ready" : "")} ref={ref} style={cards.length ? undefined : { minHeight: 200 }}
      onDragOver={e => { if (gallery(e)) { e.preventDefault(); setReady(true); } }} onDragLeave={e => { if (e.target === e.currentTarget) setReady(false); }} onDrop={drop}>
      {cards.map(card => {
        const l: Layout = preview?.[card.id] ?? clampLayout(card.layout);
        const sel = state.picked.includes(card.id), open = state.selected === card.id;
        const paint = !CHART_KINDS.includes(card.kind);
        const result = state.values[card.id], said = card.kind === "kpi" ? undefined : compares[card.id];
        return (
          <section key={card.id} data-id={card.id} tabIndex={0} aria-label={card.title + (sel ? " (선택됨)" : "")} title={dashOnly ? undefined : "눌러서 선택해요. 여러 개 고를 수 있어요"}
            className={"bcard" + (sel ? " sel" : "") + (open && edit ? " editing" : "")}
            style={{ gridColumn: `${l.x + 1} / span ${l.w}`, gridRow: `${l.y + 1} / span ${l.h}` }}
            onClick={() => pick(card.id)} onKeyDown={e => key(e, card.id)}>
            <div className={"bhead" + (edit ? " movable" : "")} onPointerDown={edit ? e => startMove(e, card.id) : undefined}>
              <i className="bcheck" />
              {star.card === card.id && <span className="bbadge" title="북극성 지표">★</span>}
              {star.counters.includes(card.id) && <span className="bbadge" title="카운터 지표">⚑</span>}
              <input className="btitle" key={card.title} defaultValue={card.title} readOnly={!edit} aria-label="카드 이름" title="카드 이름. 그림으로 저장할 때 파일 이름이 돼요"
                onClick={e => e.stopPropagation()}
                onBlur={e => { const t = e.target.value.trim() || card.title; e.target.value = t; if (t !== card.title) dispatch({ type: "card", id: card.id, patch: { title: t } }); }} />
              {edit && <button className="ib" title="카드 설정" aria-label={`${card.title} 카드 설정`} onClick={e => { e.stopPropagation(); dispatch({ type: "select", id: card.id }); }}>⚙</button>}
              {edit && <button className="ib" title="이 카드 빼기" aria-label={`${card.title} 카드 빼기`} onClick={e => { e.stopPropagation(); remove(card.id); }}>×</button>}
            </div>
            <div className={"bbody" + (paint ? " paint" : "")}>
              {isShell(card) ? <div className="hint bnote">{SHELL_NOTE}</div> : <CardBody card={card} result={result} palette={dash.palette} ctx={state.ctx} />}
            </div>
            {said && <div className={"bcmp " + said.tone} title={said.delta_text} style={{ padding: "0 10px" }}>{said.text}</div>}
            {!isShell(card) && <WhenLine card={card} d={result?.data ?? card.data} />}
            {result?.error && <div className="hint bnote" role="status" style={{ padding: "0 10px", margin: 0 }}>{result.error}</div>}
            {edit && <i className="bresize" title="끌어서 크기를 바꿔요" aria-hidden="true" onPointerDown={e => startResize(e, card.id)} />}
          </section>
        );
      })}
    </div>
  );
}
