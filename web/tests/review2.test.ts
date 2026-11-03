// 리뷰 2차 C(2026-11-04 03:45) RC1~RC7: 기간이 걸린 화면 값, 다시 정하기에서 비운 칸·되돌릴 칸, 눈금 자릿수, 색 이름 한계, 구간 수 상한
import { describe, expect, it } from "vitest";
import { MAX_LABEL, labelIndex, okLabel } from "@/lib/calc";
import { computedUpdate, plainCtx } from "@/lib/store";
import type { Card, CardKind } from "@/lib/types";
import { tickBy, tickNum } from "@/components/cards/chart/Charts";
import { seriesKit } from "@/components/cards/chart/series";
import { FORM_KEYS, buildCard, emptyForm, formFromCard, refillView } from "@/components/edit/menu/build";
import { freshColors } from "@/components/edit/menu/kinds";

const card = (kind: CardKind, view: Record<string, unknown>, source: Record<string, unknown> = {}, data: Record<string, unknown> = {}): Card =>
  ({ id: "c", kind, title: "t", layout: { x: 0, y: 0, w: 4, h: 4 }, colors: freshColors(), view: { type: "", ...view } as never, source: source as never, data: data as never });
const row = { label: "", y: "amt", agg: "sum" };

describe("RC1 다시 구한 값을 넣는 곳", () => {
  it("기간이 걸려 있으면 화면 값에만(저장 값은 그대로), 바꾼 칸은 저장", () => {
    expect(computedUpdate("c", { note: "n" }, { range: { start: "a", end: "b" } }, { source: { date: "x" } })).toEqual({ patch: { source: { date: "x" } }, result: { id: "c", data: { note: "n" } } });
  });
  it("기간이 걸린 채 정의가 바뀌면 기간 없이 구한 값을 저장 값으로", () => {
    expect(computedUpdate("c", { note: "기간" }, { range: { start: "a", end: "b" } }, { view: { type: "bar" } as never }, { note: "전체" }))
      .toEqual({ patch: { view: { type: "bar" }, data: { note: "전체" } }, result: { id: "c", data: { note: "기간" } } });
    expect(plainCtx({ range: { start: "a", end: "b" }, prev: { start: "x", end: "y", text: "t" }, as_of: "2026-01-31" })).toEqual({ range: undefined, prev: null, as_of: "2026-01-31" });
  });
  it("기간이 없으면 카드에 저장하고 화면 값은 비운다", () => {
    expect(computedUpdate("c", { note: "n" }, null)).toEqual({ patch: { data: { note: "n" } }, result: { id: "c" } });
  });
});

describe("RC2 다시 정하기에서 비운 칸은 지운다", () => {
  it("폼이 비우면 키를 안 만드는 칸 전부", () => {
    const old = Object.fromEntries(FORM_KEYS.map(k => [k, "옛 값"])) as unknown as Card["view"];
    const v = refillView({ ...old, fit: "fill" } as Card["view"], {});
    for (const k of FORM_KEYS) expect(v[k], k).toBeUndefined();
    expect(v.fit).toBe("fill");
    expect(FORM_KEYS).toEqual(expect.arrayContaining(["target", "unit_text", "bands", "stale", "time_col", "kind_col", "link_col", "baseline", "threshold", "group", "points", "size", "image_path"]));
  });
});

describe("RC4 눈금 자릿수", () => {
  it("축 눈금은 그대로 보이고 좁은 범위도 겹치지 않는다", () => {
    expect([1000, 1000.25, 1000.5].map(v => tickNum(v))).toEqual(["1,000", "1,000.25", "1,000.5"]);
    expect(tickNum(0.00001234)).toBe("0.0000123");  // 아주 작은 값은 유효숫자 3
    expect(tickNum(12345)).toBe("1.2만");
  });
  it("손수 그린 3눈금은 간격으로 자릿수", () => {
    expect(tickBy(2994.2)(2995.3815)).toBe("2,995");
    expect(tickBy(0.05)(1.1666)).toBe("1.17");
    expect(tickBy(0.5)(1000.5)).toBe("1,000.5");
  });
});

describe("RC5 색 이름 한계(서버 check_colors 1~100자)", () => {
  const kit = seriesKit({ agg_names: {}, merge: [], dash: { types: {}, time_types: [] } } as never);
  it("빈 이름·앞뒤 공백·100자 넘는 이름은 색 칸에도 순서에도 없다", () => {
    const names = kit.colorNames(card("wordcloud", {}, {}, { values: { words: [{ w: "가".repeat(MAX_LABEL + 1), n: 5 }, { w: " 공백", n: 4 }, { w: "", n: 3 }, { w: "좋아", n: 2 }] } })).names;
    expect(names).toEqual(["좋아"]);
    expect([okLabel(""), okLabel(" a"), okLabel("a"), okLabel("가".repeat(MAX_LABEL))]).toEqual([false, false, true, true]);
    const order: string[] = [];
    expect(labelIndex(order, " a")).toBe(200);
    expect(order).toEqual([]);
  });
});

describe("RC6·RC7 다시 정하기가 되돌리는 칸·구간 수 상한", () => {
  const again = (c: Card, more: Record<string, unknown> = {}) => buildCard({ ...emptyForm(c.kind, c.view.type), ...formFromCard(c), table: "T", rows: [row], ...more } as never, ["sum", "count"]);
  it("가로축·나누기 컬럼·버블 크기·차트 기간 기준 날짜", () => {
    expect(again(card("heatmap", {}, { body: { x: "store", split: "kind" } })).source?.body).toMatchObject({ x: "store", split: "kind" });
    expect(again(card("treemap", {}, { body: { x: "store", split: "kind" } })).source?.body).toMatchObject({ x: "store", split: "kind" });
    expect(again(card("bump", {}, { body: { x: "d", split: "store" }, date: "d" })).source?.body).toMatchObject({ x: "d", split: "store" });
    expect(again(card("gauge", {}, { body: { x: "region" } })).source?.body).toMatchObject({ x: "region" });
    const chart = again(card("chart", { type: "bubble", points: { size: "qty" } }, { body: { x: "price" }, date: "ts" }));
    expect(chart.source).toMatchObject({ date: "ts", body: { x: "price" } });
    expect(chart.view?.points).toEqual({ size: "qty" });
  });
  it("구간 수는 서버 상한을 따른다", () => {
    expect(buildCard({ ...emptyForm("histogram"), table: "T", rows: [row], bins: "80" }, ["sum"], () => false, {}, 6, 50).view?.bins).toBe(50);
  });
});
