"use client";
// N5 — 카드 설정 패널(옛 dash_panel.js CardPanel): 이름·데이터·모양 줄·북극성/카운터·차트 선반(Shelf)·자리(x·y·w·h 숫자 칸 = 끌기의 키보드 대안, 아래쪽 접힌 [위치·크기]) + 접기 띠.
// onChange(patch)로 카드 하나를 고친다(부른 쪽이 dispatch({type:"card", id, patch})). 자리는 다른 카드도 밀리므로 여기서 calc.moveCard·resizeCard로 모두 다시 놓아
// dispatch({type:"cards"})로 직접 넣는다. 북극성은 dispatch({type:"meta"}). Esc나 [닫기]로 닫고, 접으면 오른쪽 띠 하나만 남는다(고르던 값은 그대로).
import { useEffect, useRef, useState } from "react";
import { columns as fetchColumns, cardRequest, computeCard } from "@/lib/api";
import { applySections, movePiece, pieceItems, pieceMin, resizePiece, roomOf, sectionMin, sectionsOf } from "@/components/grid/sections";
import { FramePanel } from "@/components/frame/FramePanel";
import { FRAME_KINDS, isFrame, type FrameKind } from "@/components/frame/registry";
import { clampLayout, compactLayout, moveCard, onPage, pageOf, resizeCard, type GridItem } from "@/lib/calc";
import { useShell } from "@/components/shell/ShellProvider";
import { computedUpdate, plainCtx, useDash } from "@/lib/store";
import type { Card, Columns, Layout } from "@/lib/types";
import { AddCardMenu } from "./AddCardMenu";
import { refillColors, refillView } from "./menu/build";
import { Shelf } from "./Shelf";
import { KIND_NAMES, isShell } from "./menu/kinds";
import { FIT, StarSection } from "./panel/StarSection";
import { LinkPanel } from "./panel/LinkPanel";
import { Tools } from "./panel/Tools";

export type CardPanelProps = { card: Card | null; onChange: (patch: Partial<Card>) => void; onClose: () => void };

const NAMES = { x: "가로 위치", y: "세로 위치", w: "너비", h: "높이" } as const;
// 위치·크기 두 줄(섹션 = 쪽 격자 칸, 조각 = 섹션 눈금)의 자리·범위·바꾸는 일. apply가 false면 겹침·붙음이라 못 바꾼 것
type Spot = { name: string; unit: string; at: Layout; low: { w: number; h: number }; high: Record<keyof typeof NAMES, number>; apply: (key: keyof typeof NAMES, v: number) => boolean };

export function CardPanel(props: CardPanelProps) {
  return props.card ? <Panel key={props.card.id} {...props} card={props.card} /> : null;  // 카드가 바뀌면 처음 모양(접힘·데이터 열림)으로
}

function Panel({ card, onChange, onClose }: CardPanelProps & { card: Card }) {
  const { state, dispatch } = useDash();
  const { ctxOf } = useShell();
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

  const pages = state.dash?.pages ?? [];
  const { sections, cards } = sectionsOf(state.dash!);
  const me = cards.find(c => c.id === card.id)!;  // 옛 카드는 sectionsOf가 꽉 채운 섹션으로 옮긴 모양(조각 자리 = 섹션 눈금)
  const sec = sections.find(s => s.id === me.section)!;
  const pieces = cards.filter(c => c.section === sec.id);
  const room = roomOf(sec), secMin = sectionMin(sec, pieces), pieceLow = pieceMin(me);
  // 지금 쪽 섹션 자리들(Grid와 같은 자리): 줄일 수 있는 한계로 자르고, 쪽을 나눴거나 잘렸으면 위로 붙인다
  const secItems = (): GridItem[] => {
    const here = onPage(sections, pages, pageOf(sec, pages));
    const items = here.map(s => { const m = sectionMin(s, cards.filter(c => c.section === s.id)); return { id: s.id, layout: clampLayout(s.layout, m.h, m.w), minW: m.w, minH: m.h }; });
    if (!pages.length && items.every((it, i) => JSON.stringify(it.layout) === JSON.stringify(here[i].layout))) return items;
    const packed = compactLayout(items);
    return items.map(it => ({ ...it, layout: packed[it.id] }));
  };
  const spots: Spot[] = [
    { name: "섹션", unit: "칸", at: sec.layout, low: secMin, high: { w: 12, h: 12, x: 12 - sec.layout.w, y: 999 }, apply: (key, v) => {
      const items = secItems(), at = sec.layout;
      const next = key === "w" || key === "h" ? resizeCard(items, sec.id, key === "w" ? v : at.w, key === "h" ? v : at.h) : moveCard(items, sec.id, { x: key === "x" ? v : at.x, y: key === "y" ? v : at.y });
      const n = applySections(cards, sections, next);
      dispatch({ type: "cards", cards: n.cards, sections: n.sections });
      return true;
    } },
    { name: "조각", unit: "눈금", at: me.layout, low: pieceLow, high: { w: room.cols - me.layout.x, h: room.rows - me.layout.y, x: room.cols - me.layout.w, y: room.rows - me.layout.h }, apply: (key, v) => {
      const items = pieceItems(pieces), at = me.layout;
      const r = key === "w" || key === "h" ? resizePiece(items, me.id, key === "w" ? v : at.w, key === "h" ? v : at.h, room) : movePiece(items, me.id, { x: key === "x" ? v : at.x, y: key === "y" ? v : at.y }, room);
      if (!r) return false;
      dispatch({ type: "cards", cards: cards.map(c => (c.section === sec.id && r.layouts[c.id] ? { ...c, layout: r.layouts[c.id] } : c)), sections });
      return true;
    } },
  ];
  const place = (spot: Spot, key: keyof typeof NAMES, raw: string) => {
    const v = +raw, min = key === "w" || key === "h" ? spot.low[key] : 0, max = spot.high[key];
    if (raw.trim() === "" || !Number.isInteger(v) || v < min || v > max) {
      return setErr(`${NAMES[key]}는 ${min}~${max} 사이의 정수로 적어 주세요. 이전 값은 그대로예요.`);
    }
    setErr(spot.apply(key, v) ? "" : "다른 조각과 겹치거나 1눈금보다 가까워요. 이전 값은 그대로예요.");
  };
  // 이 카드 값을 지금 연결에서 다시 구한다(바꾼 카드로). 실패하면 이유를 보이고 카드는 그대로
  // redefine: 카드 정의(source·view)가 바뀌는 경로(선반 반영·데이터 다시 정하기). 기간이 걸려 있으면 저장할 값을 기간 없이 한 번 더 구한다
  const recompute = async (next: Card, redefine = false) => {
    const ctx = ctxOf(card.id), r = await computeCard(cardRequest(next), ctx, state.dash?.id ?? ""), plain = await plainData(next, redefine);
    put(computedUpdate(card.id, r.data, ctx, { source: next.source }, plain));
  };
  const plainData = async (next: Card, redefine: boolean) => { const ctx = ctxOf(card.id); return redefine && ctx?.range ? (await computeCard(cardRequest(next), plainCtx(ctx), state.dash?.id ?? "")).data : undefined; };
  // 구한 값을 넣는다: 기간이 걸려 있으면 화면 값에(저장 값은 그대로), 아니면 카드에 저장하고 화면 값을 비운다(lib/store.computedUpdate)
  const put = (u: ReturnType<typeof computedUpdate>) => { onChange(u.patch); dispatch({ type: "values", results: [u.result] }); };
  const again = async () => { try { await recompute(card); setErr(""); } catch (e) { setErr((e as Error).message); } };
  const reshelf = async (body: NonNullable<Card["source"]["body"]>) => {
    try { await recompute({ ...card, source: { ...card.source, body } }, true); setErr("반영했어요."); }
    catch (e) { setErr(`${(e as Error).message} 이전 설정으로 되돌렸어요.`); }
  };
  // 빈 틀(또는 다시 정하는 카드)에 데이터를 채운다(옛 fillCard): 자리·id는 그대로 두고 값을 구해 넣는다. 그사이 다른 대시보드를 열었으면 넣지 않는다
  const fill = async (patch: Partial<Card>) => {
    const dash = state.dash?.id, next = { ...card, ...patch, view: refillView(card.view, patch.view ?? {}) } as Card;  // 비운 목표·단위는 지운다
    try {
      const ctx = ctxOf(card.id), r = await computeCard(cardRequest({ ...next, data: {} }), ctx, dash ?? ""), plain = await plainData({ ...next, data: {} }, true);
      if (now.current.dash?.id !== dash || !now.current.dash?.cards.some(c => c.id === card.id)) return dispatch({ type: "error", message: "값을 구하는 사이 다른 대시보드를 열어서 카드에 넣지 않았어요." });
      put(computedUpdate(card.id, r.data, ctx, { kind: next.kind, view: next.view, colors: refillColors(card, patch.colors ?? card.colors), title: next.title, source: { ...next.source, ...r.source } }, plain));
      setBuilder(false);
      setErr("");
      dispatch({ type: "notice", message: "데이터를 정했어요." });
    } catch (e) { setErr((e as Error).message); }
  };
  const what = isShell(card) ? "아직 데이터를 정하지 않았어요" : card.source.recipe ? `${card.source.table ?? ""} 테이블의 분석 결과` : card.source.metric_id ? `지표 ${card.source.metric_id}` : `${card.source.table ?? ""} 테이블${card.source.date ? `, 기간 기준 날짜 ${card.source.date}` : ""}`;
  const isChart = card.kind === "chart" && !!card.source?.body;
  const frame = isFrame(card.kind);

  return (
    <aside id="d-panel" className={`d-panel${folded ? " folded" : ""}`} aria-label="카드 설정" onKeyDown={e => { if (e.key === "Escape" && !folded) { e.stopPropagation(); onClose(); } }}>
      <div className="phead">
        <b>{frame ? FRAME_KINDS[card.kind as FrameKind].name : KIND_NAMES[card.kind] ?? card.kind} 카드 설정</b>
        <button type="button" className="fold min-w-[1.5rem] min-h-[1.5rem]" ref={fold} aria-label="패널 접기" title="패널 접기" onClick={() => { setFolded(true); queueMicrotask(() => strip.current?.focus()); }}>›</button>
        <button type="button" className="ghost min-w-[1.5rem] min-h-[1.5rem]" aria-label="패널 닫기" title="패널 닫기" onClick={onClose}>×</button>
      </div>
      <label className="hint pfield"><span>이름</span>
        <input key={card.title} defaultValue={card.title} onBlur={e => { const v = e.target.value.trim() || card.title; e.target.value = v; if (v !== card.title) onChange({ title: v }); }}
          onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur(); }} />
      </label>
      {pages.length > 0 && (
        <label className="hint pfield"><span>쪽</span>
          <select aria-label="쪽" title="이 카드가 보일 쪽(탭)이에요" value={pageOf(sec, pages)} onChange={e => dispatch({ type: "cards", cards, sections: sections.map(s => (s.id === sec.id ? { ...s, view: { ...s.view, page: e.target.value } } : s)) })}>
            {pages.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
      )}
      {frame ? <><FramePanel card={card} onChange={f => dispatch({ type: "card", id: card.id, patch: { view: { ...card.view, frame: f } } })} />
        <LinkPanel card={card} onChange={p => dispatch({ type: "card", id: card.id, patch: p })} /></> : (<>
      <div>
        <h5>데이터</h5>
        <div className="hint">{what}</div>
        <button type="button" className={`btn${isShell(card) && !builder ? " p" : ""} ${FIT}`} aria-expanded={builder} onClick={() => setBuilder(b => !b)}>
          {builder ? "접기" : isShell(card) ? "데이터 정하기" : "데이터 다시 정하기"}
        </button>
        <div className="pdata"><AddCardMenu open={builder} fill={card} onClose={() => setBuilder(false)} onAdd={fill} /></div>
      </div>
      {!isShell(card) && (<><h5>모양</h5><Tools card={card} columns={cols.map(c => c[0])} onChange={onChange} /></>)}
      {!isShell(card) && <LinkPanel card={card} onChange={onChange} />}
      <StarSection id={card.id} />
      {isChart && (<><h5 title="컬럼을 끌어 칸에 놓거나, 컬럼을 누른 뒤 칸을 눌러요">차트 구성</h5>
        {cols.length ? <Shelf card={card} columns={cols} onChange={reshelf} /> : <div className="hint">데이터에 연결하면 컬럼을 끌어 가로축, 값, 색, 필터를 바꿀 수 있어요.</div>}</>)}
      {!isShell(card) && <button type="button" className="btn min-h-[1.5rem]" title="이 카드 값만 지금 연결에서 다시 구해요" onClick={again}>이 카드 다시 구하기</button>}{/* 빈 틀은 구할 기록이 없다 */}
      </>)}
      <details className="pplace"><summary>위치·크기</summary>
        {spots.map(s => (
          <div key={s.name} className="prow">
            {(["w", "h", "x", "y"] as const).map(k => (
              <label key={k + s.at[k]} className="hint pfield"><span>{s.name} {{ w: `너비(${s.unit})`, h: `높이(${s.unit})`, x: "가로 위치", y: "세로 위치" }[k]}</span>
                <input type="number" className="w-[4rem]" min={k === "w" || k === "h" ? s.low[k] : 0} max={s.high[k]} defaultValue={s.at[k]}
                  onBlur={e => { if (+e.target.value !== s.at[k]) place(s, k, e.target.value); }} onKeyDown={e => { if (e.key === "Enter") e.currentTarget.blur(); }} />
              </label>
            ))}
          </div>
        ))}
      </details>
      <div className="err" role="alert">{err}</div>
      {folded && (
        <button type="button" className="side-strip" ref={strip} title="카드 설정 펴기" aria-label="카드 설정 펴기" onClick={() => { setFolded(false); queueMicrotask(() => fold.current?.focus()); }}>
          <b>‹</b><span>카드 설정</span>
        </button>
      )}
    </aside>
  );
}
