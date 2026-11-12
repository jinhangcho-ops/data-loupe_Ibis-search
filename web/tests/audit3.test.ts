// 감사(화면): 빈 범위가 날짜 라벨을 지우지 않음, 단위 경계의 줄인 숫자, 일별 카드 값이 예전 계산과 같음. 값은 여기서 지어낸다.
import { describe, expect, it } from "vitest";
import { cardDays } from "@/components/cards/svg/__pure__";
import { shortNum } from "@/lib/autoviz";
import { inRanges } from "@/lib/groups";
import { windowValue } from "@/lib/series";
import type { Card } from "@/lib/types";

describe("감사 3", () => {
  it("막 더한 빈 범위는 날짜·숫자 라벨을 다 남긴다", () => {
    expect(inRanges("2024-01-01", [[null, null]])).toBe(true);
    expect(inRanges(5, [[null, null], [10, 20]])).toBe(true);
  });
  it("반올림해 다음 단위가 되면 한 단위 위로", () => {
    expect(shortNum(999950, "en")).toBe("1M");
    expect(shortNum(99999500, "ko")).toBe("1억");
    expect(shortNum(1500, "en")).toBe("1.5K");
    expect(shortNum(999, "en")).toBe("999");
  });
  it("cardDays는 날마다 windowValue를 부른 값과 같다", () => {
    const labels = ["2024-01-01", "2024-01-02", "2024-01-04", "2024-01-05"];
    const s = { name: "n", agg: "sum", sum: [1, 2, null, 4], count: [1, 1, 0, 2], values: [1, 2, null, 4] };
    const card = { data: { by_unit: { day: { labels, series: [s] } } }, view: {} } as unknown as Card;
    const merge = ["sum", "count"];
    const got = cardDays(card, 0, { start: "2024-01-01", end: "2024-01-05" }, merge);
    expect(got.map(d => d.day)).toEqual(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]);
    const loaded = { start: labels[0], end: labels[labels.length - 1] };
    expect(got.map(d => d.v)).toEqual(got.map(d => windowValue(s, labels, d.day, d.day, merge, loaded)));
  });
});
