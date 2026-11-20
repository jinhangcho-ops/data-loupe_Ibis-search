// 대시보드 2단계: 값 풍선(마우스를 올리면 그날 값), 카드 크게 보기(⤢), 표 줄을 누르면 상세 창. 데이터는 지어낸 것.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ComponentType } from "react";
import type { Card } from "@/lib/types";
import type { P } from "@/components/cards/svg/common";
import { PAINTERS } from "@/components/cards/SvgCards";

const src = (p: string) => readFileSync(join(__dirname, "..", p), "utf8");
const THEME = { card: "#fff", head: "#eee", ink: "#000", text: "#555", line: "#ddd", err: "#c00", ok: "#070", series: ["#111", "#222"] };
const DAYS = ["2026-01-01", "2026-01-02", "2026-01-03"];
const card = (kind: string, view: Record<string, unknown>, data: Record<string, unknown>): Card => ({ id: "c", kind, title: "카드", layout: { x: 0, y: 0, w: 4, h: 4 },
  colors: { series: {}, parts: {}, order: [], parts_order: [] }, view: { type: "", ...view } as Card["view"], source: {}, data } as Card);
const daily = { by_unit: { day: { labels: DAYS, series: [{ name: "매출", agg: "sum", values: [10, 20, 30], sum: [10, 20, 30], count: [1, 1, 1] }] } } };
const draw = (c: Card) => { const Paint = PAINTERS[c.kind] as ComponentType<P>; return renderToStaticMarkup(<Paint card={c} colors={THEME} range={undefined} merge={["sum", "count"]} />); };

describe("값 풍선", () => {
  it("KPI 추이선: 날마다 그날 값이 든 title", () => {
    const html = draw(card("kpi", {}, daily));
    for (const [day, v] of [["2026-01-01", "10"], ["2026-01-03", "30"]]) expect(html).toContain(`<title>${day}: ${v}</title>`);
  });
  it("목표 추세: 날마다 그날 값이 든 title", () => {
    const html = draw(card("target_trend", {}, daily));
    expect(html).toContain("<title>2026-01-02: 20</title>");
  });
});

describe("표 줄 상세", () => {
  it("표 카드의 줄은 누를 수 있다(pickable)", () => {
    const html = draw(card("table", {}, { values: { columns: [{ name: "지역", kind: "group" }, { name: "매출", kind: "value", agg: "sum" }], rows: [["서울", 10], ["부산", 20]] } }));
    expect(html.match(/<tr class="pickable"/g)).toHaveLength(2);
  });
  it("줄을 누르면 카드 고르기로 번지지 않고 상세 창(RowDetail)을 연다", () => {
    const rows = src("components/cards/svg/Rows.tsx");
    expect(rows.match(/onClick=\{e => \{ e\.stopPropagation\(\); setOpen\(/g)).toHaveLength(2);  // 앞 N행 표, 집계 표
    expect(rows).toContain('<Pop title="줄 상세" cls="row-box"');
  });
});

describe("카드 크게 보기", () => {
  it("빈 틀이 아닌 카드의 제목 줄에 ⤢, 누르면 큰 창에 같은 카드를 그린다", () => {
    const grid = src("components/grid/Grid.tsx");
    expect(grid).toContain('{!isShell(card) && <button className="ib" title="크게 보기"');
    expect(grid).toContain('<Pop title={big.title} cls="zoom-box" onClose={() => setZoom(null)}>');
    expect(grid).toContain("<CardBody card={big} result={state.values[big.id]}");
  });
  it("창 안 누르기·키는 카드로 번지지 않고, Esc와 바깥 누르기로 닫힌다", () => {
    const pop = src("components/cards/Pop.tsx");
    expect(pop).toContain("e.stopPropagation(); if (e.target === e.currentTarget) onClose();");
    expect(pop).toContain('if (e.key === "Escape")');
    expect(pop).toContain("document.body");
  });
});
