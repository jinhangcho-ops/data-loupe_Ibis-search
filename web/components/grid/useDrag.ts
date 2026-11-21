"use client";
// N3 — 끌어 옮기기·크기 바꾸기(옛 dash_edit.js startMove/startResize).
// 포인터를 잡으면 격자 상자(GridBox: left·top·칸+간격 col·row)로 calc.cellAt/sizeAt을 구하고, calc.moveCard/resizeCard로 모든 카드의 새 자리를 preview로 준다.
// 놓으면 commit(새 자리들), Esc·취소면 버린다. 키보드 대안은 패널의 자리 숫자 칸(N5)이 맡는다.
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from "react";
import { ROUND_KINDS, ROUND_MIN_H, ROUND_TYPES } from "@/components/cards/chart/shapes";
import { GRID_COLS, GRID_GAP, GRID_MIN, GRID_ROW, cellAt, clampLayout, compactLayout, moveCard, resizeCard, sizeAt, type GridBox, type GridItem } from "@/lib/calc";
import type { Card, Layout } from "@/lib/types";

/** 카드의 최소 높이(칸): 둥근 그림 카드(둥근 차트·도넛 KPI·게이지)는 ROUND_MIN_H, 나머지는 GRID_MIN */
export const minHeight = (card: Pick<Card, "kind" | "view">) => (ROUND_KINDS.includes(card.kind) || ROUND_TYPES.includes(card.view?.type ?? "") ? ROUND_MIN_H : GRID_MIN);

/** 카드들의 격자 자리(최소 높이 포함). 낮게 저장된 둥근 카드가 올라갔으면 같은 줄·같은 높이였던 카드도 같이 올리고(줄 높이 통일) 아래 카드를 밀어 겹치지 않게 하며(세로 압축), 올라간 카드가 없으면 저장된 자리 그대로 */
export function gridItems(cards: Card[]): GridItem[] {
  const row = (c: Card) => `${c.layout.y}:${c.layout.h}`;
  const raised = new Map(cards.filter(c => c.layout.h < minHeight(c)).map(c => [row(c), minHeight(c)]));
  const items = cards.map(c => ({ id: c.id, minH: minHeight(c), layout: clampLayout(c.layout, raised.get(row(c)) ?? minHeight(c)) }));
  if (items.every((it, i) => it.layout.h === clampLayout(cards[i].layout).h)) return items;
  const at = compactLayout(items);
  return items.map(it => ({ ...it, layout: at[it.id] }));
}

const DRAG_START = 4;  // 이름 칸에서 이만큼(px) 움직여야 끌기로 본다(그보다 적으면 이름 고치기)

export type Drag = {
  /** 끌기 중의 미리보기 자리(id → layout). 끌지 않을 때 null */
  preview: Record<string, Layout> | null;
  startMove: (e: ReactPointerEvent, id: string) => void;
  startResize: (e: ReactPointerEvent, id: string) => void;
};

// 격자 한 칸의 크기: left·top(격자 안쪽 왼쪽 위), col·row(칸 하나 + 간격의 길이), gap
export function metrics(grid: HTMLElement): GridBox {
  const rect = grid.getBoundingClientRect(), css = getComputedStyle(grid);
  const padL = parseFloat(css.paddingLeft) || 0, padR = parseFloat(css.paddingRight) || 0, gap = parseFloat(css.columnGap) || GRID_GAP;
  const colW = (rect.width - padL - padR - gap * (GRID_COLS - 1)) / GRID_COLS;
  return { left: rect.left + padL, top: rect.top + (parseFloat(css.paddingTop) || 0), col: colW + gap, row: GRID_ROW + gap, gap };
}

export function useDrag(grid: RefObject<HTMLElement | null>, items: GridItem[], commit: (layouts: Record<string, Layout>) => void): Drag {
  const [preview, setPreview] = useState<Record<string, Layout> | null>(null);
  const live = useRef({ items, commit });
  useEffect(() => { live.current = { items, commit }; });  // 끄는 동안 바뀐 최신 값을 쓴다

  function begin(e: ReactPointerEvent, id: string, kind: "move" | "resize") {
    const g = grid.current, handle = e.currentTarget as HTMLElement, box = handle.closest<HTMLElement>(".bcard");
    if (e.button !== 0 || !g || !box) return;
    // 이름 칸이 머리 줄을 거의 다 차지하므로 이름 칸에서도 끈다. 쓰고 있는 이름 칸(초점)은 글자 고르기라 그대로 두고, 끌지 않고 뗐으면 이름 칸에 초점을 준다
    const name = kind === "move" ? (e.target as HTMLElement).closest<HTMLInputElement>("input") : null;
    if (kind === "move" && ((e.target as HTMLElement).closest("button, select") || (name && name === document.activeElement))) return;
    e.preventDefault();
    let far = false;
    if (kind === "resize") e.stopPropagation();
    const m = metrics(g), at = box.getBoundingClientRect(), grabX = e.clientX - at.left, grabY = e.clientY - at.top;
    try { handle.setPointerCapture(e.pointerId); } catch { /* 잡지 못해도 window가 받는다 */ }
    box.classList.add("dragging");
    let layouts: Record<string, Layout> | null = null;
    const move = (ev: PointerEvent) => {
      far ||= Math.abs(ev.clientX - e.clientX) + Math.abs(ev.clientY - e.clientY) > DRAG_START;
      if (name && !far) return;
      const s = sizeAt(ev.clientX - at.left, ev.clientY - at.top, m);
      layouts = kind === "move" ? moveCard(live.current.items, id, cellAt(ev.clientX, ev.clientY, m, grabX, grabY)) : resizeCard(live.current.items, id, s.w, s.h);
      setPreview(layouts);
    };
    const end = (ev: Event) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      window.removeEventListener("keydown", esc);
      box.classList.remove("dragging");
      try { handle.releasePointerCapture(e.pointerId); } catch { /* 이미 풀림 */ }
      setPreview(null);
      if (name && !far && ev.type === "pointerup") name.focus();
      if (layouts && ev.type === "pointerup") {
        if (kind === "move") {  // 끈 뒤의 click이 카드 고르기로 번지지 않게
          const stop = (c: Event) => c.stopPropagation();
          box.addEventListener("click", stop, { capture: true, once: true });
          setTimeout(() => box.removeEventListener("click", stop, true), 0);
        }
        live.current.commit(layouts);
      }
    };
    const esc = (ev: KeyboardEvent) => { if (ev.key === "Escape") { layouts = null; end(ev); } };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
    window.addEventListener("keydown", esc);
  }

  return { preview, startMove: (e, id) => begin(e, id, "move"), startResize: (e, id) => begin(e, id, "resize") };
}
