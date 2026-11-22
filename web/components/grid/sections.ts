// (B5) 섹션과 조각의 순수 계산(화면 요소 없음): 섹션 = 쪽 12칸 격자에 놓는 카드 상자, 조각 = 섹션 안 눈금에 놓는 카드.
// 옛 카드(section 없음)는 그 카드가 꽉 채운 섹션으로 읽고, 섹션 안 조각은 겹치지 않으며, 끌 때 다른 조각과 맞으면 안내선을 보이고 붙인다. 서버 core/dash_store.py의 check_sections와 규칙을 맞춘다.
import { FRAME_MIN, GRID_MIN, clampLayout, nextLayout, onPage, type GridItem } from "@/lib/calc";
import { isFrame } from "@/components/frame/registry";
import type { Card, Dashboard, Layout, Section } from "@/lib/types";
import { minHeight } from "./useDrag";

/** 섹션 눈금: 쪽 격자 한 칸을 가로 x·세로 y 눈금으로 나눈다(서버 dash_store.SECTION_SUB와 같다) */
export const SUB = { x: 16, y: 8 } as const;
/** 같은 섹션의 조각 사이에 띄울 가장 작은 눈금 수(서버 dash_store.SECTION_GAP과 같다) */
export const PIECE_GAP = 1;
/** "섹션 크기를 바꾸면 조각도 같이 바뀜"의 처음 값(서버 dash_store.SECTION_FOLLOW와 같다) */
export const SECTION_FOLLOW = true;
/** 끌 때(옮기기·크기) 다른 조각의 가장자리·가운데에 붙는 거리(눈금) */
export const GUIDE_SNAP = 1;
/** 기존 섹션에 끌어 넣는 새 조각의 처음 크기(쪽 격자 칸, 섹션이 더 작으면 섹션에 맞춘다) */
export const NEW_PIECE = 2;
export const MISSING_SECTION = "조각이 놓인 섹션이 없어요.";
export const TAKEN_SECTION = "섹션 id가 겹쳐요.";
export const ALIGN_FEW = "맞출 조각을 둘 이상 골라 주세요.";
export const DISTRIBUTE_FEW = "같은 간격으로 나누려면 조각을 셋 이상 골라 주세요.";
export const NOT_SAME = "같은 섹션의 조각만 함께 맞출 수 있어요.";
export const ALIGN_OVERLAP = "그렇게 맞추면 조각이 겹치거나 붙어요. 조각 사이는 1눈금 이상 띄워요.";
export const SIZE_FAIL = "같은 크기로 맞추면 조각이 섹션 밖으로 나가거나 겹치거나 붙어요.";

/** 섹션 안 눈금 수 */
export type Room = { cols: number; rows: number };
/** 안내선 하나: axis x는 세로선(가로 자리 at), y는 가로선(세로 자리 at). at은 눈금(가운데면 .5가 될 수 있다) */
export type Guide = { axis: "x" | "y"; at: number };
/** 조각 끌기 결과: 모든 조각의 자리와 안내선 */
export type Move = { layouts: Record<string, Layout>; lines: Guide[] };
/** 맞춤 여섯 가지 */
export type Align = "left" | "hcenter" | "right" | "top" | "vcenter" | "bottom";

/** [SPECIFICATION] plans/dash-blocks.md#roomOf
 *  섹션 자리(쪽 격자 칸)의 눈금 수 {cols, rows}. */
export function roomOf(section: Pick<Section, "layout">): Room {
  return { cols: section.layout.w * SUB.x, rows: section.layout.h * SUB.y };
}

/** [SPECIFICATION] plans/dash-blocks.md#sectionsOf
 *  대시보드의 섹션과 카드: 섹션이 없는 카드는 그 카드가 꽉 채운 새 섹션으로 옮긴 모양을 돌려준다. */
export function sectionsOf(dash: Pick<Dashboard, "cards" | "sections">): { sections: Section[]; cards: Card[] } {
  const sections = [...(dash.sections ?? [])];
  const ids = new Set(sections.map(s => s.id));
  const cards = dash.cards.map(card => {
    if (card.section) {
      if (!ids.has(card.section)) throw new Error(MISSING_SECTION);
      return card;
    }
    if (ids.has(card.id)) throw new Error(TAKEN_SECTION);
    const layout = clampLayout(card.layout, minHeight(card), isFrame(card.kind) ? FRAME_MIN : undefined);
    const { page, ...view } = card.view ?? {};
    sections.push({ id: card.id, title: "", layout, follow: SECTION_FOLLOW, view: page ? { page } : {} });
    ids.add(card.id);
    return { ...card, section: card.id, layout: { x: 0, y: 0, w: layout.w * SUB.x, h: layout.h * SUB.y }, view };
  });
  return { sections, cards };
}

/** [SPECIFICATION] plans/dash-blocks.md#wrapCard
 *  새 카드 하나를 지금 쪽 맨 아래 새 섹션에 꽉 채워 놓은 {섹션, 카드}를 돌려준다. */
export function wrapCard(card: Card, sections: Section[], pages: string[] | undefined, page: string): { section: Section; card: Card } {
  const size = clampLayout(card.layout, minHeight(card), isFrame(card.kind) ? FRAME_MIN : undefined);
  const items = onPage(sections, pages, page).map(s => ({ id: s.id, layout: s.layout }));
  const layout = nextLayout(items, size.w, size.h);
  const view = { ...card.view };
  delete view.page;
  const section: Section = { id: card.id, title: "", layout, follow: SECTION_FOLLOW, view: pages?.length ? { page: pages.includes(page) ? page : pages[0] } : {} };
  return { section, card: { ...card, section: card.id, layout: { x: 0, y: 0, w: size.w * SUB.x, h: size.h * SUB.y }, view } };
}

/** [SPECIFICATION] plans/dash-blocks.md#pieceMin
 *  조각의 가장 작은 너비·높이(눈금). */
export function pieceMin(card: Pick<Card, "kind" | "view">): { w: number; h: number } {
  return { w: (isFrame(card.kind) ? FRAME_MIN : GRID_MIN) * SUB.x / 2, h: minHeight(card) * SUB.y / 2 };
}

/** [SPECIFICATION] plans/dash-blocks.md#pieceItems
 *  섹션 하나의 조각들 → 끌기 계산에 쓰는 GridItem(최소 크기 포함). */
export function pieceItems(pieces: Card[]): GridItem[] {
  return pieces.map(c => ({ id: c.id, layout: c.layout, minW: pieceMin(c).w, minH: pieceMin(c).h }));
}

/** [SPECIFICATION] plans/dash-blocks.md#sectionMin
 *  섹션을 줄일 수 있는 가장 작은 너비·높이(쪽 격자 칸): follow에 따라 조각이 다 들어가거나 최소 크기를 지키는 크기. */
export function sectionMin(section: Section, pieces: Card[]): { w: number; h: number } {
  if (!pieces.length) return { w: FRAME_MIN, h: FRAME_MIN };
  if (section.follow === false) {
    return {
      w: Math.max(FRAME_MIN, Math.ceil(Math.max(...pieces.map(c => c.layout.x + c.layout.w)) / SUB.x)),
      h: Math.max(FRAME_MIN, Math.ceil(Math.max(...pieces.map(c => c.layout.y + c.layout.h)) / SUB.y)),
    };
  }
  const { w: wOld, h: hOld } = section.layout;
  const ok = (w: number, h: number): boolean => {
    const next = scalePieces(pieces, section.layout, { ...section.layout, w, h });
    const laid = pieces.map(c => ({ min: pieceMin(c), at: next[c.id] }));
    return laid.every((p, i) => p.at.w >= p.min.w && p.at.h >= p.min.h && laid.slice(i + 1).every(q => !tooClose(p.at, q.at)));
  };
  let minW = wOld;
  for (let w = wOld - 1; w >= FRAME_MIN && ok(w, hOld); w--) minW = w;
  let minH = hOld;
  for (let h = hOld - 1; h >= FRAME_MIN; h--) {
    let all = true;
    for (let w = minW; w <= wOld && all; w++) all = ok(w, h);
    if (!all) break;
    minH = h;
  }
  return { w: minW, h: minH };
}

/** [SPECIFICATION] plans/dash-blocks.md#scalePieces
 *  섹션 크기가 from에서 to로 바뀔 때 조각들의 새 자리(가장자리를 같은 비율로 옮겨 반올림). */
export function scalePieces(pieces: Card[], from: Layout, to: Layout): Record<string, Layout> {
  const kx = to.w / from.w;
  const ky = to.h / from.h;
  return Object.fromEntries(pieces.map(({ id, layout: { x, y, w, h } }) => {
    const x1 = Math.round(x * kx);
    const y1 = Math.round(y * ky);
    return [id, { x: x1, y: y1, w: Math.round((x + w) * kx) - x1, h: Math.round((y + h) * ky) - y1 }];
  }));
}

/** [SPECIFICATION] plans/dash-blocks.md#applySections
 *  섹션들의 새 쪽 격자 자리를 넣고, follow인 섹션의 크기가 바뀌었으면 그 조각들도 같이 바꾼 {섹션들, 카드들}. */
export function applySections(cards: Card[], sections: Section[], layouts: Record<string, Layout>): { sections: Section[]; cards: Card[] } {
  const scaled: Record<string, Layout> = {};
  const next = sections.map(s => {
    const layout = layouts[s.id];
    if (!layout) return s;
    if (s.follow !== false && (layout.w !== s.layout.w || layout.h !== s.layout.h)) {
      Object.assign(scaled, scalePieces(cards.filter(c => c.section === s.id), s.layout, layout));
    }
    return { ...s, layout };
  });
  return { sections: next, cards: cards.map(c => (scaled[c.id] ? { ...c, layout: scaled[c.id] } : c)) };
}

/** [SPECIFICATION] plans/dash-blocks.md#tooClose
 *  같은 섹션의 두 조각 자리가 겹치거나 PIECE_GAP 눈금보다 가까운지(모서리끼리 맞닿은 것도 가깝다). */
export function tooClose(a: Layout, b: Layout): boolean {
  return a.x < b.x + b.w + PIECE_GAP && b.x < a.x + a.w + PIECE_GAP && a.y < b.y + b.h + PIECE_GAP && b.y < a.y + a.h + PIECE_GAP;
}

/** [SPECIFICATION] plans/dash-blocks.md#dropPiece
 *  다른 섹션(items, room)으로 끌어 온 조각 piece를 cell에 놓을 자리. 최소 크기가 안 들어가거나 자리가 비지 않으면 null. */
export function dropPiece(items: GridItem[], piece: GridItem, cell: { x: number; y: number }, room: Room): Layout | null {
  const w = Math.min(piece.layout.w, room.cols);
  const h = Math.min(piece.layout.h, room.rows);
  if (w < (piece.minW ?? 1) || h < (piece.minH ?? 1)) return null;
  const moved = movePiece([...items, { ...piece, layout: { x: cell.x, y: cell.y, w, h } }], piece.id, cell, room);
  return moved ? moved.layouts[piece.id] : null;
}

/** [SPECIFICATION] plans/dash-blocks.md#movePiece
 *  조각 id를 cell로 옮긴 모든 조각의 자리와 안내선. 겹치거나 붙으면 null. */
export function movePiece(items: GridItem[], id: string, cell: { x: number; y: number }, room: Room): Move | null {
  const me = items.find(i => i.id === id);
  if (!me) return null;
  const { w, h } = me.layout;
  const others = items.filter(i => i.id !== id);
  const x0 = Math.min(Math.max(cell.x, 0), room.cols - w);
  const y0 = Math.min(Math.max(cell.y, 0), room.rows - h);
  const snap = (v: number, size: number, limit: number, starts: (o: Layout) => [number, number]): number => {
    let best = v;
    let gap = Infinity;
    for (const o of others) {
      const [p, len] = starts(o.layout);
      for (const c of [p, p + len, p - size, p + len - size, p + len / 2 - size / 2]) {
        const d = Math.abs(c - v);
        if (Number.isInteger(c) && d <= GUIDE_SNAP && c >= 0 && c <= limit - size && d < gap) { best = c; gap = d; }
      }
    }
    return best;
  };
  const snapped = { x: snap(x0, w, room.cols, o => [o.x, o.w]), y: snap(y0, h, room.rows, o => [o.y, o.h]) };
  const clear = (at: { x: number; y: number }): boolean => others.every(o => !tooClose({ ...at, w, h }, o.layout));
  const at = clear(snapped) ? snapped : { x: x0, y: y0 };
  if (at !== snapped && !clear(at)) return null;
  const moved: Layout = { ...at, w, h };
  return { layouts: Object.fromEntries(items.map(i => [i.id, i.id === id ? moved : i.layout])), lines: guideLines(others, moved) };
}

/** 움직이는 조각 mine의 시작·끝·가운데가 다른 조각과 같은 자리의 안내선(같은 axis·at은 한 번만) */
function guideLines(others: GridItem[], mine: Layout): Guide[] {
  const lines: Guide[] = [];
  for (const axis of ["x", "y"] as const) {
    const [p, len] = axis === "x" ? [mine.x, mine.w] : [mine.y, mine.h];
    for (const at of [p, p + len, p + len / 2]) {
      const hit = others.some(o => {
        const [q, n] = axis === "x" ? [o.layout.x, o.layout.w] : [o.layout.y, o.layout.h];
        return at === q || at === q + n || at === q + n / 2;
      });
      if (hit && !lines.some(l => l.axis === axis && l.at === at)) lines.push({ axis, at });
    }
  }
  return lines;
}

/** [SPECIFICATION] plans/dash-blocks.md#resizePiece
 *  조각 id의 크기를 w·h로 바꾼 모든 조각의 자리와 안내선(오른쪽·아래 가장자리가 붙는다). 겹치거나 붙으면 null. */
export function resizePiece(items: GridItem[], id: string, w: number, h: number, room: Room): Move | null {
  const me = items.find(i => i.id === id);
  if (!me) return null;
  const { x, y } = me.layout;
  const minW = me.minW ?? 1;
  const minH = me.minH ?? 1;
  const others = items.filter(i => i.id !== id);
  const w0 = Math.min(Math.max(w, minW), room.cols - x);
  const h0 = Math.min(Math.max(h, minH), room.rows - y);
  const snap = (size: number, start: number, min: number, limit: number, starts: (o: Layout) => [number, number]): number => {
    let best = size;
    let gap = Infinity;
    for (const o of others) {
      const [p, len] = starts(o.layout);
      for (const c of [p, p + len, p + len / 2]) {
        const d = Math.abs(c - (start + size));
        if (Number.isInteger(c) && d <= GUIDE_SNAP && c - start >= min && c - start <= limit - start && d < gap) { best = c - start; gap = d; }
      }
    }
    return best;
  };
  const snapped = { w: snap(w0, x, minW, room.cols, o => [o.x, o.w]), h: snap(h0, y, minH, room.rows, o => [o.y, o.h]) };
  const clear = (size: { w: number; h: number }): boolean => others.every(o => !tooClose({ x, y, ...size }, o.layout));
  const size = clear(snapped) ? snapped : { w: w0, h: h0 };
  if (size !== snapped && !clear(size)) return null;
  const resized: Layout = { x, y, ...size };
  return { layouts: Object.fromEntries(items.map(i => [i.id, i.id === id ? resized : i.layout])), lines: guideLines(others, resized) };
}

/** [SPECIFICATION] plans/dash-blocks.md#alignPieces
 *  고른 조각들(ids)을 그 조각들을 감싼 상자의 한쪽·가운데에 맞춘 모든 조각의 자리. 겹치면 던진다. */
export function alignPieces(items: GridItem[], ids: string[], how: Align): Record<string, Layout> {
  const picked = ids.map(id => items.find(i => i.id === id));
  if (picked.some(p => !p)) throw new Error(NOT_SAME);
  if (ids.length < 2) throw new Error(ALIGN_FEW);
  const boxes = picked.map(p => p!.layout);
  const minX = Math.min(...boxes.map(b => b.x));
  const maxR = Math.max(...boxes.map(b => b.x + b.w));
  const minY = Math.min(...boxes.map(b => b.y));
  const maxB = Math.max(...boxes.map(b => b.y + b.h));
  const place = (l: Layout): Layout => {
    switch (how) {
      case "left": return { ...l, x: minX };
      case "right": return { ...l, x: maxR - l.w };
      case "hcenter": return { ...l, x: Math.floor((minX + maxR - l.w) / 2) };
      case "top": return { ...l, y: minY };
      case "bottom": return { ...l, y: maxB - l.h };
      case "vcenter": return { ...l, y: Math.floor((minY + maxB - l.h) / 2) };
    }
  };
  const out = Object.fromEntries(items.map(i => [i.id, ids.includes(i.id) ? place(i.layout) : i.layout]));
  const all = Object.values(out);
  if (all.some((a, i) => all.slice(i + 1).some(b => tooClose(a, b)))) throw new Error(ALIGN_OVERLAP);
  return out;
}

/** [SPECIFICATION] plans/dash-blocks.md#distributePieces
 *  고른 조각들(셋 이상)을 양 끝은 두고 가로나 세로로 같은 간격이 되게 놓은 모든 조각의 자리. */
export function distributePieces(items: GridItem[], ids: string[], axis: "x" | "y"): Record<string, Layout> {
  const picked = ids.map(id => items.find(i => i.id === id));
  if (picked.some(p => !p)) throw new Error(NOT_SAME);
  if (ids.length < 3) throw new Error(DISTRIBUTE_FEW);
  const len = axis === "x" ? "w" : "h";
  const sorted = (picked as GridItem[]).sort((a, b) => a.layout[axis] - b.layout[axis] || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  const first = sorted[0].layout;
  const last = sorted[sorted.length - 1].layout;
  const gaps = sorted.length - 1;
  const free = last[axis] - (first[axis] + first[len]) - sorted.slice(1, -1).reduce((sum, p) => sum + p.layout[len], 0);
  if (free < 0) throw new Error(ALIGN_OVERLAP);
  const g = Math.floor(free / gaps);
  const extra = free - g * gaps;
  const out: Record<string, Layout> = Object.fromEntries(items.map(i => [i.id, i.layout]));
  let at = first[axis] + first[len];
  sorted.slice(1, -1).forEach((p, i) => {
    at += g + (i < extra ? 1 : 0);
    out[p.id] = { ...p.layout, [axis]: at };
    at += p.layout[len];
  });
  const all = Object.values(out);
  if (all.some((a, i) => all.slice(i + 1).some(b => tooClose(a, b)))) throw new Error(ALIGN_OVERLAP);
  return out;
}

/** [SPECIFICATION] plans/dash-blocks.md#sameSize
 *  고른 조각들을 처음 고른 조각(ids[0])과 같은 너비나 높이로 맞춘 모든 조각의 자리. */
export function sameSize(items: GridItem[], ids: string[], dim: "w" | "h", room: Room): Record<string, Layout> {
  const picked = ids.map(id => items.find(i => i.id === id));
  if (picked.some(p => !p)) throw new Error(NOT_SAME);
  if (ids.length < 2) throw new Error(ALIGN_FEW);
  const size = (picked[0] as GridItem).layout[dim];
  const out: Record<string, Layout> = Object.fromEntries(items.map(i => [i.id, ids.includes(i.id) ? { ...i.layout, [dim]: size } : i.layout]));
  const tooSmall = items.some(i => ids.includes(i.id) && size < ((dim === "w" ? i.minW : i.minH) ?? 1));
  const all = Object.values(out);
  const outside = all.some(l => l.x + l.w > room.cols || l.y + l.h > room.rows);
  if (tooSmall || outside || all.some((a, i) => all.slice(i + 1).some(b => tooClose(a, b)))) throw new Error(SIZE_FAIL);
  return out;
}
