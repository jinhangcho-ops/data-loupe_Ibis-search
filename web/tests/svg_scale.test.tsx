// viewBox로 그리는 카드 그림(cards/svg/*)의 글자가 화면 크기 배율(chart/scale.useRootScale)을 따라가고, 글자에 묶인 자리(줄 간격·칸 폭·글자 줄)가 같이 커지는가.
// 화면 없이 그리므로 배율은 useRootScale을 바꿔 끼워 준다(배율 1은 고치기 전과 같은 그림).
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ComponentType } from "react";
import type { Card, CardData, CardKind } from "@/lib/types";
import type { P } from "@/components/cards/svg/common";
import { PAINTERS } from "@/components/cards/SvgCards";
import { growFont } from "@/components/cards/svg/__pure__";

const now = vi.hoisted(() => ({ scale: 1 }));
vi.mock("@/components/cards/chart/scale", async orig => ({ ...(await orig<typeof import("@/components/cards/chart/scale")>()), useRootScale: () => now.scale }));
afterEach(() => { now.scale = 1; });

const THEME = { card: "#fff", head: "#eee", ink: "#000", text: "#555", line: "#ddd", err: "#c00", ok: "#070", series: ["#111"] };
const card = (kind: CardKind, data: CardData, view: Record<string, unknown> = {}): Card => ({ id: "c", kind, title: "카드", layout: { x: 0, y: 0, w: 4, h: 4 }, colors: { series: {}, parts: {}, order: [], parts_order: [] },
  view: { type: "", unit: "day", compare: { mode: "span" }, better: "up", ...view } as Card["view"], source: { body: { series: [{ y: "amt", agg: "sum" }] } }, data });
const draw = (c: Card, scale: number) => { now.scale = scale; const Paint = PAINTERS[c.kind] as ComponentType<P>; return renderToStaticMarkup(<Paint card={c} colors={THEME} range={undefined} merge={["sum", "count"]} />); };
/** 그림 안 글자(<text>)의 속성 값들 */
const texts = (html: string, attr: string) => [...html.matchAll(/<text [^>]*>/g)].map(m => Number(new RegExp(` ${attr}="([^"]+)"`).exec(m[0])?.[1]));
const SCALES = [1.25, 1.5625];

const GAUGE = card("gauge", { values: { labels: ["가", "나", "다", "라"], series: [{ name: "n", values: [4, 3, 2, 1] }] } } as unknown as CardData);
const HEAT = card("heatmap", { values: { rows: ["A", "B"], cols: ["buy", "cart", "view"], cells: [[60, 20, 140], [70, null, 45]] } });
const BULLET = card("bullet", { values: { scalar: [375], names: ["합계 (amt)"] }, target: { value: 400, label: "", error: "" } });

describe("viewBox 카드 그림의 글자 배율", () => {
  it("growFont: 배율만큼 키우되 자리 폭까지만, 기준 크기보다 작아지지 않는다", () => {
    expect(growFont("abc", 1000, 11, 1)).toBe(11);
    expect(growFont("abc", 1000, 11, 1.25)).toBe(13.75);
    expect(growFont("abc", 1000, 11, 1.5625)).toBe(17.1875);
    expect(growFont("", 100, 11, 1.25)).toBe(13.75);
    const capped = growFont("가나다라마바사아자차", 150, 11, 1.5625);  // 한글 10자 ≈ 10em: 150 폭이면 15까지만
    expect(capped).toBeCloseTo(15, 5);
    expect(growFont("가나다라마바사아자차", 50, 11, 1.5625)).toBe(11);
  });
  it("배율 1: 고치기 전과 같은 그림(게이지 260×200·글자 11, 히트맵 칸 44)", () => {
    const g = draw(GAUGE, 1);
    expect(g).toContain('viewBox="0 0 260 200"');
    expect(texts(g, "font-size")).toEqual([16, 11, 11, 11, 11]);
    expect(texts(g, "y").slice(1)).toEqual([49, 67, 85, 103]);
    expect(draw(HEAT, 1)).toContain('viewBox="0 0 222 70"');
  });
  it.each(SCALES)("게이지 배율 %s: 범례 글자가 곱해지고 줄 간격이 글자 크기 이상", scale => {
    const g = draw(GAUGE, scale), size = texts(g, "font-size").slice(1), ys = texts(g, "y").slice(1);
    expect(size).toEqual([11 * scale, 11 * scale, 11 * scale, 11 * scale]);
    ys.slice(1).forEach((y, i) => expect(y - ys[i]).toBeGreaterThanOrEqual(11 * scale));
    const [, , w, h] = /viewBox="([^"]+)"/.exec(g)![1].split(" ").map(Number);
    expect(w).toBeGreaterThan(260);  // 범례 글자 자리만큼 넓어진다
    expect(h).toBeGreaterThanOrEqual(ys[ys.length - 1]);
  });
  it.each(SCALES)("히트맵 배율 %s: 글자가 곱해지고 칸 폭·줄 이름 폭이 같이 커진다", scale => {
    const h = draw(HEAT, scale), size = texts(h, "font-size");
    expect(size.filter(n => n === 11 * scale)).toHaveLength(5);  // 머리글 3 + 줄 이름 2
    expect(size.filter(n => n === 10 * scale)).toHaveLength(5);  // 값이 있는 칸 5
    expect(h).toContain(`viewBox="0 0 ${(90 + 3 * 44) * scale} 70"`);
    expect(h).toContain(`width="${44 * scale - 2}"`);
  });
  it.each(SCALES)("불릿 배율 %s: 눈금 글자가 곱해지고 막대·목표 선(아래 끝 46) 아래에 놓인다", scale => {
    const b = draw(BULLET, scale), size = texts(b, "font-size"), ys = texts(b, "y");
    expect(size).toEqual([10 * scale, 10 * scale, 10 * scale]);
    ys.forEach(y => { expect(y - 10 * scale).toBeGreaterThanOrEqual(46); expect(y).toBeLessThan(84); });
  });
});
