// 둥근 그림 카드(둥근 차트·도넛 KPI·게이지)는 최소 높이가 ROUND_MIN_H칸이라 카드마다 원 크기(ROUND_PX)가 같다. 낮게 저장된 카드는 열 때 올라가고 아래 카드는 밀린다.
import { describe, expect, it } from "vitest";
import { ROUND_MIN_H } from "@/components/cards/chart/shapes";
import { gridItems, minHeight } from "@/components/grid/useDrag";
import { GRID_MIN, clampLayout, resizeCard } from "@/lib/calc";
import type { Card } from "@/lib/types";

const card = (id: string, kind: string, type: string, x: number, y: number, w: number, h: number) => ({ id, kind, view: { type }, layout: { x, y, w, h } }) as unknown as Card;

describe("둥근 카드의 최소 높이", () => {
  it("둥근 차트·도넛 KPI·게이지만 ROUND_MIN_H, 나머지는 GRID_MIN", () => {
    expect(ROUND_MIN_H).toBe(6);
    for (const c of [card("a", "chart", "doughnut", 0, 0, 3, 4), card("a", "chart", "pie", 0, 0, 3, 4), card("a", "donut_kpi", "", 0, 0, 3, 4), card("a", "gauge", "", 0, 0, 3, 4)]) expect(minHeight(c)).toBe(ROUND_MIN_H);
    for (const c of [card("a", "chart", "bar", 0, 0, 3, 4), card("a", "kpi", "", 0, 0, 3, 4)]) expect(minHeight(c)).toBe(GRID_MIN);
  });
  it("clampLayout은 최소 높이를 받아 올린다(주지 않으면 예전처럼 GRID_MIN)", () => {
    expect(clampLayout({ x: 0, y: 0, w: 3, h: 4 }, ROUND_MIN_H).h).toBe(ROUND_MIN_H);
    expect(clampLayout({ x: 0, y: 0, w: 3, h: 8 }, ROUND_MIN_H).h).toBe(8);
    expect(clampLayout({ x: 0, y: 0, w: 3, h: 1 }).h).toBe(GRID_MIN);
  });
  it("낮게 저장된 둥근 카드는 열 때 올라가고 같은 줄·같은 높이 카드도 같이 올라가며 아래 카드가 밀린다. 올릴 것이 없으면 자리는 그대로", () => {
    const got = gridItems([card("k", "kpi", "", 0, 0, 3, 4), card("b", "chart", "bar", 3, 0, 3, 5), card("d", "chart", "doughnut", 6, 0, 6, 4), card("t", "table", "", 6, 4, 6, 4)]);
    expect(Object.fromEntries(got.map(i => [i.id, i.layout]))).toEqual({ k: { x: 0, y: 0, w: 3, h: ROUND_MIN_H }, b: { x: 3, y: 0, w: 3, h: 5 }, d: { x: 6, y: 0, w: 6, h: ROUND_MIN_H }, t: { x: 6, y: ROUND_MIN_H, w: 6, h: 4 } });
    const loose = [card("a", "kpi", "", 0, 3, 6, 4)];  // 위가 비어 있어도 올릴 카드가 없으면 건드리지 않는다
    expect(gridItems(loose)[0].layout).toEqual({ x: 0, y: 3, w: 6, h: 4 });
  });
  it("크기를 줄여도 둥근 카드는 ROUND_MIN_H 아래로 내려가지 않는다", () => {
    const items = gridItems([card("d", "gauge", "", 0, 0, 6, 8)]);
    expect(resizeCard(items, "d", 6, 2).d.h).toBe(ROUND_MIN_H);
  });
});
