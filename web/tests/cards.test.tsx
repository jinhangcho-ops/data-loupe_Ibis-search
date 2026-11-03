// P14·P16 카드가 D2 데이터 모양(서버 dash_calc 예)으로 오류 없이 그려지는지(서버 렌더 글자로 확인, 화면 창 없음).
// 주의: Recharts 그림(ResponsiveContainer 안의 차트·트리맵·순위 변화)은 서버 렌더에서 빈 틀만 나와 확인하지 않는다. 손수 SVG·HTML 카드만 글자로 확인한다.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ComponentType } from "react";
import type { Card, CardData, CardKind } from "@/lib/types";
import type { P } from "@/components/cards/svg/common";
import { PAINTERS } from "@/components/cards/SvgCards";
import { SeriesChart } from "@/components/cards/chart/Charts";

const THEME = { card: "#fff", head: "#eee", ink: "#000", text: "#555", line: "#ddd", err: "#c00", ok: "#070", series: ["#111"] };
const card = (kind: CardKind, data: CardData, view: Record<string, unknown> = {}): Card => ({ id: "c", kind, title: "카드", layout: { x: 0, y: 0, w: 4, h: 4 }, colors: { series: {}, parts: {}, order: [], parts_order: [] },
  view: { type: "", unit: "day", compare: { mode: "span" }, better: "up", ...view } as Card["view"], source: { body: { series: [{ y: "amt", agg: "sum" }] } }, data });
const draw = (c: Card) => { const Paint = PAINTERS[c.kind] as ComponentType<P>; return renderToStaticMarkup(<Paint card={c} colors={THEME} range={undefined} merge={["sum", "count"]} />); };

describe("새 카드(D2 데이터 예)", () => {
  it("boxplot·heatmap·calheat·sankey·cohort·candle·wordcloud·bullet", () => {
    expect(draw(card("boxplot", { values: { groups: [{ name: "A", n: 5, min: 10, q1: 20, med: 50, q3: 60, max: 80, outliers: [], outlier_count: 0 }] } }))).toContain("중앙값 50");
    expect(draw(card("heatmap", { values: { rows: ["A", "B"], cols: ["buy", "cart", "view"], cells: [[60, 20, 140], [70, null, 45]] } }))).toContain("B × cart: -");
    expect(draw(card("calheat", { values: { days: [{ d: "2026-01-05", v: 60 }, { d: "2026-01-06", v: 10 }] } }))).toContain("2026-01-05: 60");
    const s = draw(card("sankey", { values: { nodes: [{ name: "view", users: 4 }, { name: "cart", users: 2 }, { name: "이탈", users: 2 }], links: [{ s: 0, t: 1, v: 2 }, { s: 0, t: 2, v: 2 }] } }));
    expect(s).toContain("view → cart: 2명");
    expect(draw(card("cohort", { values: { periods: [0, 1], cohorts: [{ start: "2026-01-01", size: 3, retained: [1.0, 0.333] }] } }))).toContain("33.3%");
    expect(draw(card("candle", { values: { labels: ["2026-01-01"], candles: [{ open: 100, high: 120, low: 90, close: 99 }] } }))).toContain("2026-01-01: 시작 100");
    expect(draw(card("wordcloud", { values: { words: [{ w: "view", n: 5 }, { w: "cart", n: 2 }] } }))).toContain("view: 5번");
    expect(draw(card("bullet", { values: { scalar: [375], names: ["합계 (amt)"] }, target: { value: 400, label: "", error: "" } }, { bands: [100, 300] }))).toContain("목표 400");
  });
  it("캔들은 화면에서 자르지 않는다(리뷰: 200개 상한이 최근 기간을 버렸음, 상한은 서버)", () => {
    const labels = Array.from({ length: 300 }, (_, i) => `P${i}`), candles = labels.map((_, i) => ({ open: i, high: i + 1, low: i - 1, close: i }));
    expect(draw(card("candle", { values: { labels, candles } }))).toContain("P299:");
  });
  it("상자 그림: 사분위가 빈 그룹은 빼고 수염 끝이 비면 상자 끝(리뷰: NaN 좌표)", () => {
    const n = null as unknown as number;
    const html = draw(card("boxplot", { values: { groups: [{ name: "A", n: 1, min: n, q1: 1, med: 2, q3: 3, max: n }, { name: "B", n: 0, min: n, q1: n, med: n, q3: n, max: n }] } }));
    expect(html).not.toContain("NaN");
    expect(html).toContain("A (1개)");
    expect(html).not.toContain("B (0개)");
  });
  it("선버스트: 각도가 숫자이고 조각 글자는 이름(리뷰: 뿌리 value가 없어 빈 카드)", () => {
    const html = draw(card("sunburst", { values: { items: [{ name: "A", value: 220, children: [{ name: "view", value: 140 }] }, { name: "B", value: 80 }] } }));
    expect(html).toContain("<path");
    expect(html).not.toContain("NaN");
    expect(html).toContain(">A</text>");
    expect(html).toContain("A › 기타: 80");
  });
  it("히트맵·코호트 글자는 흰색이 아니라 본문 색", () => {
    expect(draw(card("heatmap", { values: { rows: ["A"], cols: ["x"], cells: [[9]] } }))).not.toContain("#fff");
    expect(draw(card("cohort", { values: { periods: [0], cohorts: [{ start: "s", size: 3, retained: [1] }] } }))).toContain("60%");
  });
  it("감사 C9·C11·C3: 상자 그림 툴팁은 수염, 불릿 눈금은 표시 형식, 캔들 오름 색은 주 색", () => {
    expect(draw(card("boxplot", { values: { groups: [{ name: "A", n: 5, min: 10, q1: 20, med: 50, q3: 60, max: 80 }] } }))).toContain("수염 아래 10");
    const pct = draw(card("bullet", { values: { scalar: [0.5] }, target: { value: 0.8, label: "", error: "" } }, { format: "percent" }));
    expect(pct).toContain(">0%</text>");
    expect(pct).not.toContain(">0.42</text>");
    const c = { ...card("candle", { values: { labels: ["d"], candles: [{ open: 1, high: 3, low: 0, close: 2 }] } }), colors: { series: { "주 색": "#123456" }, parts: {}, order: [], parts_order: [] } };
    expect(draw(c)).toContain("#123456");
  });
  it("값이 비면 안내", () => {
    expect(draw(card("treemap", { values: { items: [] } }))).toContain("그릴 값이 없어요");
    expect(draw(card("bump", { values: { labels: [], series: [] } }))).toContain("그릴 값이 없어요");
  });
  it("KPI: 목표를 채우면 값 색 --ok, 오른쪽 추이선·꽉 채우기 클래스", () => {
    const by = { day: { labels: ["2026-01-01", "2026-01-02"], series: [{ name: "합계", agg: "sum", sum: [5, 7], count: [1, 1] }] } };
    const html = draw(card("kpi", { by_unit: by as unknown as CardData["by_unit"] },{ target: { value: 10 }, layout: "right", fit: "fill" }));
    expect(html).toContain("lay-right");
    expect(html).toContain("fit-fill");
    expect(html).toContain("var(--ok)");
    expect(html).toContain("stroke-dasharray=\"4 3\"");
  });
  it("계열 차트 새 종류가 던지지 않는다", () => {
    const s = { labels: ["a", "b", "c"], datasets: [{ label: "x", values: [3, -1, 2] }, { label: "y", values: [1, 2, 3] }], time: false, chartable: true };
    for (const type of ["group", "stack100", "hstack", "hstack100", "sarea", "sarea100", "step", "combo", "waterfall", "pareto", "radial", "polar", "range", "slope", "trendline", "butterfly", "lollipop", "doughnut"]) {
      expect(() => renderToStaticMarkup(<SeriesChart card={card("chart", {}, { type, rings: true })} s={s} colors={{ series: ["var(--c1)"] }} />)).not.toThrow();
    }
  });
});
