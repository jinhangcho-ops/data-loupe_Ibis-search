// Phase 17(plans/phase-17.md#applyGroups): 범위 묶기는 서버가 다시 구한 값(values.merged)으로만 그린다. 화면이 받은 X 값(최대 50개)을 더하지 않는다.
// applyGroups (수정)이 끝나야 통과하므로 it.fails로 표시했다(고친 뒤 it으로 바꾼다).
import { describe, expect, it } from "vitest";
import type { Card, CardData } from "@/lib/types";
import { MERGE_DATE, applyGroups, type GroupView } from "@/lib/groups";

const card = (groups: GroupView): Card =>
  ({ id: "c1", kind: "chart", title: "t", layout: { x: 0, y: 0, w: 6, h: 6 }, colors: { series: {}, parts: {}, order: [], parts_order: [] },
     view: { type: "", unit: "day", compare: { mode: "span" }, better: "up", groups }, source: {}, data: {} }) as unknown as Card;
const numChart: CardData = { x_kind: "num", values: { labels: ["1", "2", "10"], series: [{ name: "합계 (v)", values: [1, 2, 3] }] } };

describe("범위 묶기(applyGroups)", () => {
  it("서버가 묶지 않은 값에 묶기를 켜면 더하지 않고 다시 구하라고 알린다", () => {
    expect(() => applyGroups(card({ ranges: [[1, 2]], merge: true }), numChart)).toThrow("서버에서 다시 구해요");
  });
  it("날짜 가로축 묶기는 지금처럼 MERGE_DATE", () => {
    expect(() => applyGroups(card({ ranges: [["2026-01-01", null]], merge: true }), { ...numChart, x_kind: "date" })).toThrow(MERGE_DATE);
  });
  it("서버가 묶은 값은 그대로(이름만)", () => {
    const merged: CardData = { x_kind: "num", values: { labels: ["1~2"], series: [{ name: "합계 (v)", values: [2] }], merged: true } };
    expect(applyGroups(card({ ranges: [[1, 2]], merge: true, rename: { "1~2": "적음" } }), merged).values).toEqual({ labels: ["적음"], series: [{ name: "합계 (v)", values: [2] }], merged: true });
  });
});
