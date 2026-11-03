"use client";
// N5 — 카드 설정 패널(옛 dash_panel.js CardPanel): 이름·자리(x·y·w·h 숫자 칸 = 끌기의 키보드 대안)·데이터·모양 줄·북극성/카운터·차트 선반(Shelf) + 접기 띠.
// onChange(patch)로 카드 하나를 고친다(부른 쪽이 dispatch({type:"card", id, patch})). 자리는 다른 카드도 밀리므로 여기서 calc.moveCard·resizeCard로 모두 다시 놓아
// dispatch({type:"cards"})로 직접 넣는다. 북극성은 dispatch({type:"meta"}). Esc나 [닫기]로 닫고, 접으면 오른쪽 띠 하나만 남는다(고르던 값은 그대로).
import { useEffect, useRef, useState } from "react";
import { columns as fetchColumns, cardRequest, computeCard } from "@/lib/api";
import { moveCard, resizeCard } from "@/lib/calc";
import { useDash } from "@/lib/store";
import type { Card, Columns } from "@/lib/types";
import { AddCardMenu } from "./AddCardMenu";
import { Shelf } from "./Shelf";
import { KIND_NAMES, isShell } from "./menu/kinds";
import { StarSection } from "./panel/StarSection";
import { Tools } from "./panel/Tools";

export type CardPanelProps = { card: Card | null; onChange: (patch: Partial<Card>) => void; onClose: () => void };

const LIMITS = { x: [0, 11], y: [0, 999], w: [2, 12], h: [2, 12] } as const;
const NAMES = { x: "가로 위치", y: "세로 위치", w: "너비", h: "높이" } as const;

export function CardPanel(props: CardPanelProps) {
  return props.card ? <Panel key={props.card.id} {...props} card={props.card} /> : null;  // 카드가 바뀌면 처음 모양(접힘·데이터 열림)으로
}

function Panel({ card, onChange, onClose }: CardPanelProps & { card: Card }) {
  const { state, dispatch } = useDash();
  const now = useRef(state);
  useEffect(() => { now.current = state; });
  const [folded, setFolded] = useState(false);
  const [builder, setBuilder] = useState(isShell(card));  // 빈 틀이면 데이터 칸을 처음부터 연다
  const [err, setErr] = useState("");
  const [cols, setCols] = useState<Columns["columns"]>([]);
  const strip = useRef<HTMLButtonElement>(null), fold = useRef<HTMLButtonElement>(null), back = useRef<HTMLElement | null>(null);
  const table = card.source?.table, schema = card.source?.schema ?? "";

  useEffect(() => {
    back.current = document.activeElement as HTMLElement | null;
    return () => back.current?.focus?.();
  }, []);
  useEffect(() => {
    let live = true;
    (table ? fetchColumns(table, schema).then(r => r.columns) : Promise.resolve([] as Columns["columns"])).then(c => live && setCols(c)).catch(() => live && setCols([]));
    return () => { live = false; };
  }, [table, schema]);

  const place = (key: keyof typeof NAMES, raw: string) => {
    const v = +raw, [min, max] = LIMITS[key], top = key === "x" ? 12 - card.layout.w - 0 : max;
    if (raw.trim() === "" || !Number.isInteger(v) || v < min || v > Math.min(max, top)) {
      return setErr(`${NAMES[key]}는 ${min}~${Math.min(max, top)} 사이의 정수로 적어 주세요. 이전 값은 그대로예요.`);
    }
    setErr("");
    const items = state.dash?.cards.map(c => ({ id: c.id, layout: c.layout })) ?? [];
    const next = key === "w" || key === "h" ? resizeCard(items, card.id, key === "w" ? v : card.layout.w, key === "h" ? v : card.layout.h)
      : moveCard(items, card.id, { x: key === "x" ? v : card.layout.x, y: key === "y" ? v : card.layout.y });
    if (state.dash) dispatch({ type: "cards", cards: state.dash.cards.map(c => ({ ...c, layout: next[c.id] ?? c.layout })) });
  };
  // 이 카드 값을 지금 연결에서 다시 구한다(바꾼 카드로). 실패하면 이유를 보이고 카드는 그대로
  const recompute = async (next: Card) => {
    const r = await computeCard(cardRequest(next), state.ctx);
    onChange({ source: next.source, data: r.data });
  };
  const again = async () => { try { await recompute(card); setErr(""); } catch (e) { setErr((e as Error).message); } };
  const reshelf = async (body: NonNullable<Card["source"]["body"]>) => {
    try { await recompute({ ...card, source: { ...card.source, body } }); setErr("반영했어요."); }
    catch (e) { setErr(`${(e as Error).message} 이전 설정으로 되돌렸어요.`); }
  };
  // 빈 틀(또는 다시 정하는 카드)에 데이터를 채운다(옛 fillCard): 자리·id는 그대로 두고 값을 구해 넣는다. 그사이 다른 대시보드를 열었으면 넣지 않는다
  const fill = async (patch: Partial<Card>) => {
    const dash = state.dash?.id, next = { ...card, ...patch, view: { ...card.view, ...patch.view } } as Card;
    try {
      const r = await computeCard(cardRequest({ ...next, data: {} }), state.ctx);
      if (now.current.dash?.id !== dash || !now.current.dash?.cards.some(c => c.id === card.id)) return dispatch({ type: "error", message: "값을 구하는 사이 다른 대시보드를 열어서 카드에 넣지 않았어요." });
      onChange({ kind: next.kind, view: next.view, colors: next.colors, title: next.title, source: { ...next.source, ...r.source }, data: r.data });
      setBuilder(false);
      setErr("");
      dispatch({ type: "notice", message: "데이터를 정했어요." });
    } catch (e) { setErr((e as Error).message); }
  };
  const what = isShell(card) ? "아직 데이터를 정하지 않았어요" : card.source.recipe ? `${card.source.table ?? ""} 테이블의 분석 결과` : card.source.metric_id ? `지표 ${card.source.metric_id}` : `${card.source.table ?? ""} 테이블${card.source.date ? `, 기간 기준 날짜 ${card.source.date}` : ""}`;
  const isChart = card.kind === "chart" && !!card.source?.body;

  return (
    <aside id="d-panel" className={`d-panel${folded ? " folded" : ""}`} aria-label="카드 설정" onKeyDown={e => { if (e.key === "Escape" && !folded) { e.stopPropagation(); onClose(); } }}>
      <div className="phead">
        <b>{KIND_NAMES[card.kind] ?? card.kind} 카드 설정</b>
        <button type="button" className="fold" ref={fold} aria-label="패널 접기" title="패널 접기" style={{ minWidth: 24, minHeight: 24 }} onClick={() => { setFolded(true); queueMicrotask(() => strip.current?.focus()); }}>›</button>
        <button type="button" className="ghost" aria-label="패널 닫기" title="패널 닫기" style={{ minWidth: 24, minHeight: 24 }} onClick={onClose}>×</button>
      </div>
      <label className="hint pfield"><span>이름</span>
        <input key={card.title} defaultValue={card.title} onBlur={e => { const v = e.target.value.trim() || card.title; e.target.value = v; if (v !== card.title) onChange({ title: v }); }}
          onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur(); }} />
      </label>
      <div className="prow">
        {(["w", "h", "x", "y"] as const).map(k => (
          <label key={k + card.layout[k]} className="hint pfield"><span>{{ w: "너비(칸)", h: "높이(칸)", x: "가로 위치", y: "세로 위치" }[k]}</span>
            <input type="number" style={{ width: 64 }} min={LIMITS[k][0]} max={k === "x" ? 12 - card.layout.w : LIMITS[k][1]} defaultValue={card.layout[k]}
              onBlur={e => { if (+e.target.value !== card.layout[k]) place(k, e.target.value); }} onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur(); }} />
          </label>
        ))}
      </div>
      <div>
        <h5>데이터</h5>
        <div className="hint">{what}</div>
        <button type="button" className={isShell(card) && !builder ? "btn p" : "btn"} style={{ minHeight: 24 }} aria-expanded={builder} onClick={() => setBuilder(b => !b)}>
          {builder ? "접기" : isShell(card) ? "데이터 정하기" : "데이터 다시 정하기"}
        </button>
        <div className="pdata"><AddCardMenu open={builder} fill={card} onClose={() => setBuilder(false)} onAdd={fill} /></div>
      </div>
      {!isShell(card) && (<><h5>모양</h5><Tools card={card} columns={cols.map(c => c[0])} onChange={onChange} /></>)}
      <StarSection id={card.id} />
      {isChart && (<><h5 title="컬럼을 끌어 칸에 놓거나, 컬럼을 누른 뒤 칸을 눌러요">차트 구성</h5>
        {cols.length ? <Shelf card={card} columns={cols} onChange={reshelf} /> : <div className="hint">데이터에 연결하면 컬럼을 끌어 가로축, 값, 색, 필터를 바꿀 수 있어요.</div>}</>)}
      <button type="button" className="btn" style={{ minHeight: 24 }} title="이 카드 값만 지금 연결에서 다시 구해요" onClick={again}>이 카드 다시 구하기</button>
      <div className="err" role="alert">{err}</div>
      {folded && (
        <button type="button" className="side-strip" ref={strip} title="카드 설정 펴기" aria-label="카드 설정 펴기" onClick={() => { setFolded(false); queueMicrotask(() => fold.current?.focus()); }}>
          <b>‹</b><span>카드 설정</span>
        </button>
      )}
    </aside>
  );
}
