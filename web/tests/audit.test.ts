// 감사 C(2026-11-03) web 몫: 더해지지 않는 집계의 트리, 새 카드 색 이름, [데이터 다시 정하기] 폼 시작값, 종류 이유·안내, 달력 초점 이동, 기간이 걸리는 카드, KPI 글자 폭
import { describe, expect, it } from "vitest";
import { TAU } from "@/lib/calc";
import type { Card, CardData, CardKind } from "@/lib/types";
import { seriesKit } from "@/components/cards/chart/series";
import { sunburstArcs, textUnits, treeRoot, withRest } from "@/components/cards/svg/__pure__";
import { buildCard, emptyForm, formFromCard } from "@/components/edit/menu/build";
import { moveFocus } from "@/components/shell/calPick";
import { datedCard } from "@/components/edit/menu/kinds";

const TYPES = ["bar", "hbar", "stacked", "line", "polyline", "area", "doughnut", "pie", "radar", "group", "stack100", "hstack", "hstack100", "sarea", "sarea100", "step", "combo",
  "waterfall", "pareto", "radial", "polar", "range", "slope", "trendline", "butterfly", "lollipop"];
const TIME = ["bar", "stacked", "line", "polyline", "area", "sarea", "sarea100", "step", "combo", "waterfall", "pareto", "slope"];  // 서버 TIME_TYPES(D2)
const kit = seriesKit({ agg_names: {}, merge: ["sum", "count"], dash: { types: Object.fromEntries(TYPES.map(t => [t, t])), time_types: TIME } } as never);
const card = (kind: CardKind, data: CardData, view: Record<string, unknown> = {}): Card => ({ id: "c", kind, title: "카드", layout: { x: 0, y: 0, w: 4, h: 4 }, colors: { series: {}, parts: {}, order: [], parts_order: [] },
  view: { type: "", unit: "day", compare: { mode: "span" }, better: "up", ...view } as Card["view"], source: { body: {} }, data });
const plain = (n: number): CardData => ({ values: { labels: ["a", "b"], series: Array.from({ length: n }, (_, i) => ({ name: `s${i}`, values: [1, 2] })) } });
const timed = (n: number): CardData => ({ by_unit: { day: { labels: ["2026-01-01", "2026-01-02"], series: Array.from({ length: n }, (_, i) => ({ name: `s${i}`, agg: "sum", sum: [1, 2], count: [1, 1] })) } } as never });

describe("C2 더해지지 않는 집계의 트리(평균 등)", () => {
  const bad = [{ name: "B", value: 47.5, children: [{ name: "x", value: 80 }, { name: "z", value: 15 }] }, { name: "A", value: 30, children: [{ name: "y", value: 40 }, { name: "x", value: 10 }] }];
  it("아이 합이 부모 값보다 크면 아이를 버린다", () => expect(withRest(bad)).toEqual([{ name: "B", value: 47.5 }, { name: "A", value: 30 }]));
  it("합이 안 맞는 입력을 바로 넣어도 조각은 부모 각도·한 바퀴를 넘지 않는다", () => {
    const arcs = sunburstArcs(treeRoot("t", bad));
    expect(Math.max(...arcs.map(a => a.a1))).toBeLessThanOrEqual(TAU + 1e-9);
    for (const k of arcs.filter(a => a.depth === 2)) expect(k.a1).toBeLessThanOrEqual(arcs.find(a => a.depth === 1 && a.name === k.parent)!.a1 + 1e-9);
  });
});

describe("C3 새 카드 색 이름", () => {
  it("값 하나짜리는 주 색, 트리·생키·워드클라우드는 이름", () => {
    for (const k of ["histogram", "boxplot", "heatmap", "calheat", "cohort", "candle", "bullet"] as CardKind[]) expect(kit.colorNames(card(k, { values: {} })).names).toEqual(["주 색"]);
    expect(kit.colorNames(card("treemap", { values: { items: [{ name: "A", value: 1 }, { name: "B", value: 2 }] } })).names).toEqual(["A", "B"]);
    expect(kit.colorNames(card("sunburst", { values: { items: [{ name: "A", value: 1 }] } })).names).toEqual(["A"]);
    expect(kit.colorNames(card("sankey", { values: { nodes: [{ name: "view" }, { name: "cart" }], links: [] } })).names).toEqual(["view", "cart"]);
    expect(kit.colorNames(card("wordcloud", { values: { words: [{ w: "b", n: 1 }, { w: "a", n: 5 }] } })).names).toEqual(["a", "b"]);
  });
});

describe("C4 [데이터 다시 정하기]는 모양 설정을 지킨다", () => {
  const row = { label: "", y: "amt", agg: "sum" };
  const again = (old: Card, kind: CardKind, more: Partial<ReturnType<typeof emptyForm>> = {}) => {
    const view = buildCard({ ...emptyForm(kind), ...formFromCard(old), table: "t", rows: [row], ...more }, ["sum", "count"]).view!;
    return { ...old.view, ...view };  // CardPanel.fill과 같은 합치기
  };
  it("캔들 기간 단위·히스토그램 구간 수·KPI 아이콘·형식·좋은 방향·단위·목표", () => {
    expect(again(card("candle", {}, { unit: "week" }), "candle", { date: "ts" }).unit).toBe("week");
    expect(again(card("histogram", {}, { bins: 50 }), "histogram").bins).toBe(50);
    const k = again(card("kpi", {}, { icon: "cart", format: "percent", better: "down", unit_text: "명", target: { value: 3 }, compare: { mode: "point" } }), "kpi");
    expect(k).toMatchObject({ icon: "cart", format: "percent", better: "down", unit_text: "명", target: { value: 3 }, compare: { mode: "point" } });
  });
  it("규칙·빈칸 글도 되돌려 다시 읽는다", () => {
    const rules = [{ column: "상태", match: "*지연*", color: "#e03131", arrow: "none", scope: "row" }];
    const blanks = [{ name: "매출", kind: "value", y: "amt", agg: "sum", format: "currency" }];
    const s = again(card("sentence", {}, { template: "오늘 {매출}", cond_rules: rules, blanks }), "sentence");
    expect(s.cond_rules).toEqual(rules);
    expect(s.blanks).toEqual(blanks);
    const w = again(card("status", {}, { entity: "host", rules: [{ column: "cpu", op: ">=", value: 90, level: "bad" }], links: [["a", "b"]] }), "status");
    expect(w).toMatchObject({ entity: "host", rules: [{ column: "cpu", op: ">=", value: 90, level: "bad" }], links: [["a", "b"]] });
  });
});

describe("C6·C10 종류 이유와 안내", () => {
  it("날짜 X축에는 TIME_TYPES만, 계열 2개가 필요한 종류는 1개면 막는다", () => {
    for (const t of TYPES) {
      const why = kit.typeReason(card("chart", timed(2)), t as never);
      expect(why === "", t).toBe(TIME.includes(t));
    }
    for (const t of ["stacked", "stack100", "hstack", "hstack100", "sarea100", "combo", "range", "butterfly"]) {
      expect(kit.typeReason(card("chart", plain(1)), t as never), t).toContain("2개 이상");
      expect(kit.typeReason(card("chart", plain(2)), t as never), t).toBe("");
    }
  });
  it("첫 계열만 그리는 종류는 안내(겹친 고리를 켠 둥근 차트는 빼고)", () => {
    for (const t of ["waterfall", "pareto", "trendline", "lollipop", "doughnut", "pie", "radial", "polar"]) expect(kit.typeNote(card("chart", plain(3), { type: t }), t as never), t).toContain("첫 계열(s0)만 그려요");
    expect(kit.typeNote(card("chart", plain(3), { rings: true }), "doughnut")).toBe("");
    expect(kit.typeNote(card("chart", plain(1)), "waterfall")).toBe("");
    expect(kit.typeNote(card("chart", plain(3)), "bar")).toBe("");
  });
});

describe("C6 달력 초점 이동", () => {
  const month = "2026-10";  // 보이는 두 달: 10·11월
  it("화살표·PageUp·PageDown, 보이는 달 밖이면 달을 넘긴다", () => {
    expect(moveFocus("2026-10-15", "ArrowRight", month, "")).toEqual({ focus: "2026-10-16", month });
    expect(moveFocus("2026-10-01", "ArrowUp", month, "")).toEqual({ focus: "2026-09-24", month: "2026-09" });
    expect(moveFocus("2026-11-28", "ArrowDown", month, "")).toEqual({ focus: "2026-12-05", month: "2026-11" });
    expect(moveFocus("2026-10-31", "PageDown", month, "")).toEqual({ focus: "2026-11-28", month });
    expect(moveFocus("2026-10-15", "PageUp", month, "")).toEqual({ focus: "2026-09-15", month: "2026-09" });
    expect(moveFocus("2026-10-15", "Tab", month, "")).toBeNull();
  });
  it("max 뒤로는 가지 않는다", () => expect(moveFocus("2026-11-02", "ArrowDown", month, "2026-11-03")).toEqual({ focus: "2026-11-03", month }));
});

describe("C6 기간을 바꾸면 다시 구할 카드", () => {
  const r = { start: "2026-01-01", end: "2026-01-31" };
  it("기간 기준 날짜가 있고 일 값이 없는 새 카드는 다시 구한다", () => {
    for (const k of ["histogram", "boxplot", "heatmap", "calheat", "treemap", "sunburst", "bullet", "bump", "sankey", "cohort", "candle", "wordcloud"] as CardKind[]) {
      const c = { ...card(k, { values: {} }), source: { body: {}, date: "ts" } };
      expect(datedCard(c, r, false), k).toBe(true);
      expect(datedCard(c, null, false), k).toBe(false);  // 기간이 없고 전에도 없었으면 그대로
      expect(datedCard(c, null, true), k).toBe(true);    // 기간을 지웠으면 원래 값으로
    }
    expect(datedCard({ ...card("kpi", timed(1)), source: { date: "ts" } }, r, false)).toBe(false);  // 일 값은 화면이 기간으로 묶는다
    expect(datedCard({ ...card("recent_rows", {}), source: { date: "ts" } }, r, false)).toBe(false);
  });
});

describe("C13 KPI 글자 폭", () => {
  it("한글·전각은 1em(0.62em 단위로 약 1.6), 숫자·기호는 1", () => {
    expect(textUnits("1,234")).toBe(5);
    expect(textUnits("100명")).toBeCloseTo(3 + 1 / 0.62);
  });
});
