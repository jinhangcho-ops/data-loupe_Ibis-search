// N6 — 보고서 그림의 순수 계산(옛 dash_report.js reportLayout, board.js BOARD_RATIOS·TITLE_BAND). DOM 없음(자가 점검은 같은 폴더 __pure__.ts).
import { clampLayout, GRID_COLS, GRID_ROW } from "@/lib/calc";
import type { Layout } from "@/lib/types";

/** 보고서 한 칸의 너비(px), 칸 간격, 바깥 여백, 위쪽 제목 줄, 카드 안의 이름 줄 */
export const REPORT = { col: 100, gap: 10, pad: 24, head: 56, band: 34 };
/** 그림 비율(가로/세로). screen은 화면 모양 그대로. 고른 비율은 세로 PICTURE_HEIGHT로 그린다 */
export const BOARD_RATIOS: Record<string, number> = { square: 1, "4:3": 4 / 3, wide: 16 / 9 };
export const PICTURE_HEIGHT = 450;
export const TITLE_BAND = 36;

export type Rect = { x: number; y: number; w: number; h: number };

/** 격자 자리 → 보고서 그림 안의 사각형 {width, height, rects: {카드 id: 사각형}} */
export function reportLayout(cards: { id: string; layout: Layout }[], { col, gap, pad, head } = REPORT) {
  const rects: Record<string, Rect> = {};
  let rows = 0;
  for (const c of cards) {
    const l = clampLayout(c.layout);
    rects[c.id] = { x: pad + l.x * (col + gap), y: head + l.y * (GRID_ROW + gap), w: l.w * col + (l.w - 1) * gap, h: l.h * GRID_ROW + (l.h - 1) * gap };
    rows = Math.max(rows, l.y + l.h);
  }
  return { width: pad * 2 + GRID_COLS * col + (GRID_COLS - 1) * gap, height: head + Math.max(1, rows) * (GRID_ROW + gap) - gap + pad, rects };
}

/** w × h 그림을 상자(bw × bh) 안에 비율을 지켜 가운데로(그림이 상자보다 커도 작아도) */
export function fitRect(w: number, h: number, bw: number, bh: number): Rect {
  const k = Math.min(bw / Math.max(w, 1), bh / Math.max(h, 1));
  return { x: (bw - w * k) / 2, y: (bh - h * k) / 2, w: w * k, h: h * k };
}
