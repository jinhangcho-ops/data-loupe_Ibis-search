// N6 — 보고서 그림의 순수 계산(옛 dash_report.js reportLayout, board.js BOARD_RATIOS·TITLE_BAND). DOM 없음(자가 점검은 같은 폴더 __pure__.ts).
import { clampLayout, GRID_COLS } from "@/lib/calc";
import type { Layout } from "@/lib/types";

/** 보고서 한 칸의 너비(px), 위쪽 제목 줄, 카드 안의 이름 줄(간격·여백은 reportBox가 CSS에서 낸다) */
export const REPORT = { col: 100, head: 56, band: 34 };
const REM_VALUE = /^\d*\.?\d+rem$/;
/** 그림 비율(가로/세로). screen은 화면 모양 그대로. 고른 비율은 세로 PICTURE_HEIGHT로 그린다 */
export const BOARD_RATIOS: Record<string, number> = { square: 1, "4:3": 4 / 3, wide: 16 / 9 };
export const PICTURE_HEIGHT = 450;
export const TITLE_BAND = 36;
/** 보고서 그림에서 1rem의 길이(px). 화면 글자 크기와 상관없이 그림 크기가 같게 */
export const REPORT_REM = 16;
/** 보고서가 화면 격자(.board.grid)에서 읽는 CSS 변수(값은 dash_edit.css의 #dash 규칙 한 곳에만 있다) */
export const REPORT_TOKENS = { gap: "--grid-gap", row: "--grid-row", pad: "--page-x", radius: "--r-board", padX: "--card-px", padY: "--card-py", small: "--fs-xs" } as const;

export type Rect = { x: number; y: number; w: number; h: number };
/** 보고서 그림의 길이 한 벌(px): col·head는 REPORT, 나머지는 REPORT_TOKENS를 REPORT_REM으로 바꾼 값 */
export type ReportBox = { col: number; head: number } & Record<keyof typeof REPORT_TOKENS, number>;

/** [SPECIFICATION] plans/dash-blocks.md#reportBox
 *  read(CSS 변수 이름)로 받은 rem 값을 REPORT_REM 배 해 보고서 길이 한 벌을 돌려준다. */
export function reportBox(read: (name: string) => string): ReportBox {
  const px = Object.fromEntries(Object.entries(REPORT_TOKENS).map(([key, name]) => {
    const s = read(name).trim();
    if (!REM_VALUE.test(s)) throw new Error(`대시보드 모양 값 ${name}을 읽지 못했어요: "${s}"`);
    return [key, parseFloat(s) * REPORT_REM];
  }));
  return { col: REPORT.col, head: REPORT.head, ...px } as ReportBox;
}

/** 격자 자리 → 보고서 그림 안의 사각형 {width, height, rects: {카드 id: 사각형}} */
export function reportLayout(cards: { id: string; layout: Layout }[], { col, gap, row, pad, head }: Pick<ReportBox, "col" | "gap" | "row" | "pad" | "head">) {
  const rects: Record<string, Rect> = {};
  let rows = 0;
  for (const c of cards) {
    const l = clampLayout(c.layout);
    rects[c.id] = { x: pad + l.x * (col + gap), y: head + l.y * (row + gap), w: l.w * col + (l.w - 1) * gap, h: l.h * row + (l.h - 1) * gap };
    rows = Math.max(rows, l.y + l.h);
  }
  return { width: pad * 2 + GRID_COLS * col + (GRID_COLS - 1) * gap, height: head + Math.max(1, rows) * (row + gap) - gap + pad, rects };
}

/** w × h 그림을 상자(bw × bh) 안에 비율을 지켜 가운데로(그림이 상자보다 커도 작아도) */
export function fitRect(w: number, h: number, bw: number, bh: number): Rect {
  const k = Math.min(bw / Math.max(w, 1), bh / Math.max(h, 1));
  return { x: (bw - w * k) / 2, y: (bh - h * k) / 2, w: w * k, h: h * k };
}
