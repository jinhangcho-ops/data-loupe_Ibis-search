// Phase 4(plans/phase-4.md) C: 표시할 그룹 고르기(web/lib/groups.ts) — 범위 판정·이름, 카드 종류별로 받은 값에서 고른 그룹만 남기기·범위 묶기·보이는 이름.
// 새 함수가 스켈레톤이면 실패하는 것이 정상이다.
import { describe, expect, it } from "vitest";
import type { Card, CardData } from "@/lib/types";
import { MERGE_DATE, applyGroups, dragPick, dragRange, groupNames, inRanges, rangeName, renameColors, shownName, visible, type GroupView } from "@/lib/groups";

const card = (kind: Card["kind"], groups?: GroupView): Card =>
  ({ id: "c1", kind, title: "t", layout: { x: 0, y: 0, w: 6, h: 6 }, colors: { series: {}, parts: {}, order: [], parts_order: [] },
     view: { type: "", unit: "day", compare: { mode: "span" }, better: "up", ...(groups ? { groups } : {}) }, source: {}, data: {} }) as unknown as Card;

describe("범위·이름(inRanges·rangeName·shownName·visible)", () => {
  it("숫자 범위는 양끝 포함, 한쪽은 열 수 있고, 숫자가 아니면 밖", () => {
    expect(inRanges("3", [[1, 5]])).toBe(true);
    expect(inRanges(7, [[1, 5]])).toBe(false);
    expect(inRanges("12", [[1, 5], [10, null]])).toBe(true);
    expect(inRanges("abc", [[1, 5]])).toBe(false);
    expect(inRanges("3", [])).toBe(true);
  });
  it("날짜 범위는 앞 10자(YYYY-MM-DD)를 글자 차례로 견준다", () => {
    expect(inRanges("2026-01-15", [["2026-01-01", "2026-01-31"]])).toBe(true);
    expect(inRanges("2026-01-31T10:00:00", [[null, "2026-01-31"]])).toBe(true);
    expect(inRanges("2026-02-01", [["2026-01-01", "2026-01-31"]])).toBe(false);
  });
  it("범위 이름과 보이는 이름", () => {
    expect([rangeName([1, 5]), rangeName([10, null]), rangeName([null, "2026-01-31"])]).toEqual(["1~5", "10~", "~2026-01-31"]);
    expect(shownName("a", { rename: { a: "에이" } })).toBe("에이");
    expect(shownName("b", { rename: { a: "에이" } })).toBe("b");
    expect(shownName("b", undefined)).toBe("b");
  });
  it("보일 그룹: keep이 있으면 그 안, 순서 값이면 범위 안", () => {
    expect(visible("a", undefined, undefined, false)).toBe(true);
    expect(visible("a", ["b"], undefined, false)).toBe(false);
    expect(visible("3", [], [[1, 2]], true)).toBe(false);
    expect(visible("3", [], [[1, 2]], false)).toBe(true);  // 순서가 없는 값에는 범위를 쓰지 않는다
  });
});

describe("끌어 고르기·색(dragPick·dragRange·renameColors)", () => {
  it("끈 처음~끝 값(거꾸로 끌어도)", () => {
    expect(dragPick(["a", "b", "c", "d"], 3, 1)).toEqual(["b", "c", "d"]);
    expect(dragPick(["a", "b"], 0, 0)).toEqual(["a"]);
  });
  it("순서 값이면 끈 처음·끝을 범위로(숫자는 숫자로)", () => {
    expect(dragRange(["1", "2", "5", "10"], 1, 2, "number")).toEqual([2, 5]);
    expect(dragRange(["1", "2", "5", "10"], 3, 0, "number")).toEqual([1, 10]);
    expect(dragRange(["2026-01-01", "2026-01-02T00:00:00"], 0, 1, "date")).toEqual(["2026-01-01", "2026-01-02"]);
  });
  it("이름을 바꿔도 색은 원래 이름의 색(보이는 이름 쪽으로 열쇠를 옮긴다)", () => {
    const colors = { series: { "개수": "#111111" }, parts: { a: "#222222", b: "#333333" }, order: ["a", "b"], parts_order: ["a", "b"] };
    expect(renameColors(colors, { rename: { a: "에이", "개수": "건수" } })).toEqual({ series: { "건수": "#111111" }, parts: { "에이": "#222222", b: "#333333" }, order: ["에이", "b"], parts_order: ["에이", "b"] });
    expect(renameColors(colors, undefined)).toBe(colors);
  });
});

const numChart: CardData = { x_kind: "num", values: { labels: ["1", "2", "3", "10", "12"], series: [{ name: "개수", values: [5, 4, 3, 2, 1] }, { name: "합계 (v)", values: [1, 1, 1, 1, 1] }] } };

describe("카드 종류별로 받은 값에 입히기(applyGroups)", () => {
  it("고르기가 없거나 그룹이 없는 카드는 받은 값 그대로", () => {
    expect(applyGroups(card("chart"), numChart)).toBe(numChart);
    expect(applyGroups(card("kpi", { keep: ["a"] }), numChart)).toBe(numChart);
  });
  it("항목 차트: 범위·keep·계열·묶기·이름", () => {
    expect(applyGroups(card("chart", { ranges: [[1, 2], [10, null]] }), numChart).values).toEqual({ labels: ["1", "2", "10", "12"], series: [{ name: "개수", values: [5, 4, 2, 1] }, { name: "합계 (v)", values: [1, 1, 1, 1] }] });
    expect(applyGroups(card("chart", { keep: ["2", "3"], series: ["개수"] }), numChart).values).toEqual({ labels: ["2", "3"], series: [{ name: "개수", values: [4, 3] }] });
    expect(() => applyGroups(card("chart", { ranges: [["2026-01-01", null]], merge: true }), { ...numChart, x_kind: "date" })).toThrow(MERGE_DATE);
  });
  it("서버가 이미 묶은 값(values.merged, 고유 개수)은 다시 더하지 않고 계열·이름만", () => {
    const merged: CardData = { x_kind: "num", values: { labels: ["1~2", "10~"], series: [{ name: "고유 개수 (u)", values: [3, 1] }], merged: true } };
    expect(applyGroups(card("chart", { ranges: [[1, 2], [10, null]], merge: true, rename: { "10~": "많음" } }), merged).values)
      .toEqual({ labels: ["1~2", "많음"], series: [{ name: "고유 개수 (u)", values: [3, 1] }], merged: true });
  });
  it("지도: 보일 영역·영역 묶음(값은 더하고 고리는 모은다)·이름", () => {
    const data: CardData = { values: { regions: [{ name: "서울", value: 3, rings: [[1]] }, { name: "경기", value: 2, rings: [[2]] }, { name: "부산", value: 1, rings: [[3]] }], points: [], bounds: [0, 0, 1, 1] } };
    expect(applyGroups(card("geomap", { sets: { "수도권": ["서울", "경기"] }, rename: { "부산": "부산시" } }), data).values)
      .toEqual({ regions: [{ name: "수도권", value: 5, rings: [[1], [2]] }, { name: "부산시", value: 1, rings: [[3]] }], points: [], bounds: [0, 0, 1, 1] });
    expect((applyGroups(card("geomap", { keep: ["부산"] }), data).values as { regions: unknown[] }).regions).toEqual([{ name: "부산", value: 1, rings: [[3]] }]);
  });
  it("날짜 차트: 일 값과 단위별 값의 라벨을 범위로, 계열을 이름으로", () => {
    const data = { x_kind: "date", by_unit: { day: { labels: ["2026-01-01", "2026-01-02"], series: [{ name: "개수", agg: "count", count: [1, 2] }, { name: "합계 (v)", agg: "sum", sum: [3, 4], count: [1, 1] }] },
      month: { labels: ["2026-01-01"], series: [{ name: "개수", values: [3] }] } } } as unknown as CardData;  // by_unit 교차 타입은 day 계열에 values를 요구한다(서버 모양 그대로 둔다)
    const got = applyGroups(card("chart", { ranges: [["2026-01-02", null]], series: ["개수"] }), data).by_unit!;
    expect(got.day).toEqual({ labels: ["2026-01-02"], series: [{ name: "개수", agg: "count", count: [2] }] });
    expect(got.month).toEqual({ labels: [], series: [{ name: "개수", values: [] }] });
  });
  it("히스토그램: 범위 밖 구간은 0(구간 경계는 그대로 이어진다)", () => {
    const data: CardData = { bins: { edges: [0, 10, 20, 30], counts: [1, 2, 3] } };
    expect(applyGroups(card("histogram", { ranges: [[0, 10]] }), data).bins).toEqual({ edges: [0, 10, 20, 30], counts: [1, 0, 0] });
    expect(applyGroups(card("histogram", { ranges: [[10, null]] }), data).bins).toEqual({ edges: [0, 10, 20, 30], counts: [0, 2, 3] });
  });
  it("생키: 고르지 않은 칸과 그 칸에 닿는 띠를 빼고 번호를 다시 매긴다", () => {
    const data: CardData = { values: { nodes: [{ name: "a" }, { name: "b" }, { name: "c" }], links: [{ s: 0, t: 1, v: 5 }, { s: 1, t: 2, v: 3 }, { s: 0, t: 2, v: 1 }] } };
    expect(applyGroups(card("sankey", { keep: ["a", "c"], rename: { c: "씨" } }), data).values).toEqual({ nodes: [{ name: "a" }, { name: "씨" }], links: [{ s: 0, t: 1, v: 1 }] });
  });
  it("히트맵: keep은 행, series는 열", () => {
    const data: CardData = { values: { rows: ["r1", "r2"], cols: ["c1", "c2"], cells: [[1, 2], [3, 4]] } };
    expect(applyGroups(card("heatmap", { keep: ["r2"], series: ["c2"] }), data).values).toEqual({ rows: ["r2"], cols: ["c2"], cells: [[4]] });
  });
  it("워드클라우드·퍼널·트리맵·상자: 이름으로 고르고 보이는 이름으로", () => {
    expect(applyGroups(card("wordcloud", { keep: ["b"], rename: { b: "비" } }), { values: { words: [{ w: "a", n: 3 }, { w: "b", n: 1 }] } }).values).toEqual({ words: [{ w: "비", n: 1 }] });
    expect(applyGroups(card("funnel", { keep: ["가입", "구매"] }), { values: { steps: [{ name: "가입", users: 3 }, { name: "방문", users: 2 }, { name: "구매", users: 1 }], window: null } }).values)
      .toEqual({ steps: [{ name: "가입", users: 3 }, { name: "구매", users: 1 }], window: null });
    expect(applyGroups(card("treemap", { keep: ["x"], rename: { x: "엑스" } }), { values: { items: [{ name: "x", value: 2, children: [{ name: "x1", value: 2 }] }, { name: "y", value: 1 }] } }).values)
      .toEqual({ items: [{ name: "엑스", value: 2, children: [{ name: "x1", value: 2 }] }] });
    expect(applyGroups(card("boxplot", { keep: ["g2"] }), { values: { groups: [{ name: "g1", min: 0, q1: 1, med: 2, q3: 3, max: 4 }, { name: "g2", min: 0, q1: 1, med: 2, q3: 3, max: 4 }] } }).values)
      .toEqual({ groups: [{ name: "g2", min: 0, q1: 1, med: 2, q3: 3, max: 4 }] });
  });
  it("날짜 순서 카드: 달력·순위·양초·코호트", () => {
    expect(applyGroups(card("calheat", { ranges: [["2026-01-02", null]] }), { values: { days: [{ d: "2026-01-01", v: 1 }, { d: "2026-01-02", v: 2 }] } }).values).toEqual({ days: [{ d: "2026-01-02", v: 2 }] });
    const bump = { values: { labels: ["2026-01-01", "2026-02-01"], series: [{ name: "A", values: [1, 2], ranks: [1, 1] }, { name: "B", values: [0, 1], ranks: [2, 2] }] } } as unknown as CardData;  // ranks는 MoreValues 모양(CardData.values.series에는 없다)
    expect(applyGroups(card("bump", { ranges: [["2026-02-01", null]], series: ["A"] }), bump).values).toEqual({ labels: ["2026-02-01"], series: [{ name: "A", values: [2], ranks: [1] }] });
    const candle: CardData = { values: { labels: ["2026-01-01", "2026-01-02"], candles: [{ open: 1, high: 2, low: 0, close: 1 }, { open: 2, high: 3, low: 1, close: 2 }] } };
    expect(applyGroups(card("candle", { ranges: [[null, "2026-01-01"]] }), candle).values).toEqual({ labels: ["2026-01-01"], candles: [{ open: 1, high: 2, low: 0, close: 1 }] });
    const cohort: CardData = { values: { periods: [0, 1], cohorts: [{ start: "2026-01-01", size: 2, retained: [1, 0.5] }, { start: "2026-01-08", size: 1, retained: [1, null] }] } };
    expect(applyGroups(card("cohort", { ranges: [["2026-01-08", null]] }), cohort).values).toEqual({ periods: [0, 1], cohorts: [{ start: "2026-01-08", size: 1, retained: [1, null] }] });
  });
  it("고르기 패널이 보일 이름(groupNames)", () => {
    expect(groupNames(card("chart"), numChart)).toEqual({ names: ["1", "2", "3", "10", "12"], series: ["개수", "합계 (v)"], ordered: "number" });
    expect(groupNames(card("heatmap"), { values: { rows: ["r1"], cols: ["c1"], cells: [[1]] } })).toEqual({ names: ["r1"], series: ["c1"], ordered: null });
    expect(groupNames(card("calheat"), { values: { days: [{ d: "2026-01-01", v: 1 }] } })).toEqual({ names: [], series: [], ordered: "date" });
    expect(groupNames(card("kpi"), {})).toEqual({ names: [], series: [], ordered: null });
    expect(groupNames(card("geomap"), { values: { regions: [{ name: "서울", value: 1, rings: [] }] } })).toEqual({ names: ["서울"], series: [], ordered: null });
  });
});
