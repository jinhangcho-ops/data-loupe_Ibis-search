"use client";
// N3 — 끌어 옮기기·크기 바꾸기(옛 dash_edit.js startMove/startResize).
// 포인터를 잡으면 격자 상자(GridBox: left·top·칸+간격 col·row)로 calc.cellAt/sizeAt을 구하고, calc.moveCard/resizeCard로 모든 카드의 새 자리를 preview로 준다.
// 놓으면 commit(새 자리들), Esc·취소면 버린다. 키보드 대안은 패널의 자리 숫자 칸(N5)이 맡는다.
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from "react";
import { ROUND_KINDS, ROUND_MIN_H, ROUND_TYPES } from "@/components/cards/chart/shapes";
import { FRAME_MIN, GRID_COLS, GRID_MIN, cellAt, clampLayout, compactLayout, moveCard, resizeCard, sizeAt, type GridBox, type GridItem } from "@/lib/calc";
import { isFrame } from "@/components/frame/registry";
import type { Card, Layout } from "@/lib/types";
import type { Guide, Move } from "./sections";

/** 카드의 최소 높이(칸): 둥근 그림 카드(둥근 차트·도넛 KPI·게이지)는 ROUND_MIN_H, 나머지는 GRID_MIN */
export const minHeight = (card: Pick<Card, "kind" | "view">) => (isFrame(card.kind) ? FRAME_MIN : ROUND_KINDS.includes(card.kind) || ROUND_TYPES.includes(card.view?.type ?? "") ? ROUND_MIN_H : GRID_MIN);

/** 카드들의 격자 자리(최소 높이 포함). 낮게 저장된 둥근 카드가 올라갔으면 같은 줄·같은 높이였던 카드도 같이 올리고(줄 높이 통일) 아래 카드를 밀어 겹치지 않게 하며(세로 압축), 올라간 카드가 없으면 저장된 자리 그대로 */
export function gridItems(cards: Card[]): GridItem[] {
  const row = (c: Card) => `${c.layout.y}:${c.layout.h}`;
  const raised = new Map(cards.filter(c => c.layout.h < minHeight(c)).map(c => [row(c), minHeight(c)]));
  const items = cards.map(c => ({ id: c.id, minH: minHeight(c), ...(isFrame(c.kind) ? { minW: FRAME_MIN } : {}), layout: clampLayout(c.layout, raised.get(row(c)) ?? minHeight(c), isFrame(c.kind) ? FRAME_MIN : undefined) }));
  if (items.every((it, i) => it.layout.h === clampLayout(cards[i].layout).h)) return items;
  const at = compactLayout(items);
  return items.map(it => ({ ...it, layout: at[it.id] }));
}

const DRAG_START = 4;  // 이름 칸에서 이만큼(px) 움직여야 끌기로 본다(그보다 적으면 이름 고치기)

export type Drag = {
  /** 끌기 중의 미리보기 자리(id → layout). 끌지 않을 때 null */
  preview: Record<string, Layout> | null;
  /** (B5) 끌기 중의 안내선(섹션 눈금). 끌지 않을 때·쪽 격자는 [] */
  lines: Guide[];
  startMove: (e: ReactPointerEvent, id: string) => void;
  startResize: (e: ReactPointerEvent, id: string) => void;
};

/** (B5) 섹션 안 조각 끌기: 눈금 수와 옮기기·크기 계산(grid/sections.movePiece·resizePiece). null이면 그 자리는 안 되는 자리(미리보기를 바꾸지 않는다) */
export type DragPlan = {
  cols: number;
  rows: number;
  move: (items: GridItem[], id: string, cell: { x: number; y: number }) => Move | null;
  resize: (items: GridItem[], id: string, w: number, h: number) => Move | null;
  /** 옮기기를 놓을 때 포인터가 다른 섹션 위면 그 섹션으로 옮긴다(Grid). true면 여기서 끝(섹션 안 commit 없음) */
  away: (id: string, at: { x: number; y: number; grabX: number; grabY: number }) => boolean;
};

// 칸 하나의 크기와 간격은 화면 CSS(#dash의 --grid-gap·--grid-row)에서 읽는다. (B5) cols·rows: 섹션 눈금(줄 높이를 상자 높이에서 나눠 구한다, 수정 명세 "B5 metrics (수정)")
export function metrics(grid: HTMLElement, cols: number = GRID_COLS, rows?: number): GridBox {
  const rect = grid.getBoundingClientRect(), css = getComputedStyle(grid);
  const gap = parseFloat(css.columnGap);
  const padL = parseFloat(css.paddingLeft) || 0, padR = parseFloat(css.paddingRight) || 0, padT = parseFloat(css.paddingTop) || 0, padB = parseFloat(css.paddingBottom) || 0;
  const rowH = rows === undefined ? parseFloat(css.gridAutoRows) : (rect.height - padT - padB - gap * (rows - 1)) / rows;
  if (!Number.isFinite(gap) || !Number.isFinite(rowH)) throw new Error("대시보드 격자의 간격·줄 높이를 읽지 못했어요.");
  const colW = (rect.width - padL - padR - gap * (cols - 1)) / cols;
  return { left: rect.left + padL, top: rect.top + padT, col: colW + gap, row: rowH + gap, gap };
}

export function useDrag(grid: RefObject<HTMLElement | null>, items: GridItem[], commit: (layouts: Record<string, Layout>) => void, plan?: DragPlan): Drag {
  const [preview, setPreview] = useState<Record<string, Layout> | null>(null);
  const [lines, setLines] = useState<Guide[]>([]);
  const live = useRef({ items, commit });
  useEffect(() => { live.current = { items, commit }; });  // 끄는 동안 바뀐 최신 값을 쓴다

  function begin(e: ReactPointerEvent, id: string, kind: "move" | "resize") {
    const g = grid.current, handle = e.currentTarget as HTMLElement, box = handle.closest<HTMLElement>(".bcard, .bsection");
    if (e.button !== 0 || !g || !box) return;
    // 이름 칸이 머리 줄을 거의 다 차지하므로 이름 칸에서도 끈다. 쓰고 있는 이름 칸(초점)은 글자 고르기라 그대로 두고, 끌지 않고 뗐으면 이름 칸에 초점을 준다
    const name = kind === "move" ? (e.target as HTMLElement).closest<HTMLInputElement>("input") : null;
    if (kind === "move" && ((e.target as HTMLElement).closest("button, select") || (name && name === document.activeElement))) return;
    e.preventDefault();
    let far = false;
    if (kind === "resize") e.stopPropagation();
    const m = plan ? metrics(g, plan.cols, plan.rows) : metrics(g), at = box.getBoundingClientRect(), grabX = e.clientX - at.left, grabY = e.clientY - at.top;
    try { handle.setPointerCapture(e.pointerId); } catch { /* 잡지 못해도 window가 받는다 */ }
    box.classList.add("dragging");
    let layouts: Record<string, Layout> | null = null;
    const move = (ev: PointerEvent) => {
      far ||= Math.abs(ev.clientX - e.clientX) + Math.abs(ev.clientY - e.clientY) > DRAG_START;
      if (name && !far) return;
      const s = sizeAt(ev.clientX - at.left, ev.clientY - at.top, m, plan && { w: plan.cols, h: plan.rows });
      if (!plan) {
        layouts = kind === "move" ? moveCard(live.current.items, id, cellAt(ev.clientX, ev.clientY, m, grabX, grabY)) : resizeCard(live.current.items, id, s.w, s.h);
        setPreview(layouts);
        return;
      }
      const r = kind === "move" ? plan.move(live.current.items, id, cellAt(ev.clientX, ev.clientY, m, grabX, grabY)) : plan.resize(live.current.items, id, s.w, s.h);
      if (!r) return;  // 안 되는 자리: 마지막으로 된 자리가 남는다
      layouts = r.layouts;
      setPreview(r.layouts);
      setLines(r.lines);
    };
    const end = (ev: Event) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      window.removeEventListener("keydown", esc);
      box.classList.remove("dragging");
      try { handle.releasePointerCapture(e.pointerId); } catch { /* 이미 풀림 */ }
      setPreview(null);
      setLines([]);
      if (name && !far && ev.type === "pointerup") name.focus();
      if (ev.type === "pointerup") {
        const up = ev as PointerEvent;
        const away = kind === "move" && !!plan?.away(id, { x: up.clientX, y: up.clientY, grabX, grabY });
        if (layouts || away) {
          if (kind === "move") {  // 끈 뒤의 click이 카드 고르기로 번지지 않게
            const stop = (c: Event) => c.stopPropagation();
            box.addEventListener("click", stop, { capture: true, once: true });
            setTimeout(() => box.removeEventListener("click", stop, true), 0);
          }
          if (!away && layouts) live.current.commit(layouts);
        }
      }
    };
    const esc = (ev: KeyboardEvent) => { if (ev.key === "Escape") { layouts = null; end(ev); } };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
    window.addEventListener("keydown", esc);
  }

  return { preview, lines, startMove: (e, id) => begin(e, id, "move"), startResize: (e, id) => begin(e, id, "resize") };
}
