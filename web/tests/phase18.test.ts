// Phase 18(plans/phase-18.md) C: 서버가 고른 그룹·숫자 범위로 먼저 거른 차트 값에 화면 거르기(applyGroups·visible)를 한 번 더 해도 값이 바뀌지 않는다.
import { describe, expect, it } from "vitest";
import type { Card, CardData } from "@/lib/types";
import { applyGroups, type GroupView } from "@/lib/groups";

const card = (groups: GroupView): Card =>
  ({ id: "c1", kind: "chart", title: "t", layout: { x: 0, y: 0, w: 6, h: 6 }, colors: { series: {}, parts: {}, order: [], parts_order: [] },
     view: { type: "", unit: "day", groups }, source: {}, data: {} }) as unknown as Card;

describe("서버가 거른 값에 화면 거르기(이중 거르기)", () => {
  it("keep만: 서버가 남긴 라벨(빈 X 'None' 포함)은 모두 보인다", () => {
    const served = { x_kind: "num", values: { labels: ["3", "55", "None"], series: [{ name: "개수", values: [1, 1, 1] }] } } as unknown as CardData;
    expect(applyGroups(card({ keep: ["3", "55", "None"] }), served).values).toEqual(served.values);
  });
  it("keep과 숫자 범위: 서버가 빈 X를 뺀 결과 그대로", () => {
    const served = { x_kind: "num", values: { labels: ["3", "55"], series: [{ name: "개수", values: [1, 1] }] } } as unknown as CardData;
    expect(applyGroups(card({ keep: ["3", "55", "None"], ranges: [[1, 60]] }), served).values).toEqual(served.values);
  });
  it("글자 X: 범위는 화면에서도 쓰지 않는다(서버와 같다)", () => {
    const served = { x_kind: "text", values: { labels: ["a"], series: [{ name: "개수", values: [2] }] } } as unknown as CardData;
    expect(applyGroups(card({ keep: ["a"], ranges: [[1, 5]] }), served).values).toEqual(served.values);
  });
});
