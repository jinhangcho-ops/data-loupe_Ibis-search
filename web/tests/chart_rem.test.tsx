// 차트 그림 안의 크기가 바탕 글자 크기를 따라가는가(chart/scale): 배율 계산, 그림 뿌리의 배치 크기, 배치 너비, 글자 구름의 rem 글자.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ComponentType } from "react";
import type { Card } from "@/lib/types";
import type { P } from "@/components/cards/svg/common";
import { PAINTERS } from "@/components/cards/SvgCards";
import { Frame } from "@/components/cards/chart/Charts";
import { BASE_FONT_PX, layoutWidth, rem, rootScale, scaledBox } from "@/components/cards/chart/scale";

const THEME = { card: "#fff", head: "#f5f6f6", ink: "#000", text: "#555", line: "#ddd", err: "#c00", ok: "#070", series: ["#111", "#222"], hi: "#333", palette: [] };

describe("차트 배율(chart/scale)", () => {
  it("바탕 글자 크기 → 배율: 16px은 1, 20px은 1.25", () => {
    expect(BASE_FONT_PX).toBe(16);
    expect(rootScale("16px")).toBe(1);
    expect(rootScale("20px")).toBe(1.25);
    expect(rootScale("14px")).toBe(0.875);
  });
  it("배율 1.25: 그림 뿌리는 80% 크기로 배치하고 1.25배로 키운다(화면 크기는 그대로)", () => {
    expect(scaledBox(1.25)).toEqual({ width: "80%", height: "80%", transform: "scale(1.25)", transformOrigin: "0 0" });
    expect(layoutWidth(500, 1.25)).toBe(400);
  });
  it("배율 1: 배치 크기와 너비가 그대로", () => {
    expect(scaledBox(1)).toEqual({ width: "100%", height: "100%", transform: "scale(1)", transformOrigin: "0 0" });
    expect(layoutWidth(500, 1)).toBe(500);
  });
  it("px → rem 글자", () => {
    expect(rem(12)).toBe("0.75rem");
    expect(rem(48)).toBe("3rem");
  });
  it("차트 틀(Frame)의 그림 뿌리가 배율 모양을 쓴다(화면 없이 그리면 배율 1)", () => {
    const html = renderToStaticMarkup(<Frame><svg /></Frame>);
    expect(html).toContain("width:100%;height:100%;transform:scale(1);transform-origin:0 0");
  });
  it("글자 구름의 글자 크기는 rem(12~48px → 0.75~3rem)", () => {
    const card = { id: "w", kind: "wordcloud", title: "글자", view: {}, data: { values: { words: [{ w: "가", n: 9 }, { w: "나", n: 1 }] } } } as unknown as Card;
    const Paint = PAINTERS.wordcloud as ComponentType<P>;
    const html = renderToStaticMarkup(<Paint card={card} colors={THEME} range={undefined} merge={[]} />);
    expect(html).toContain("font-size:3rem");
    expect(html).toContain("font-size:0.75rem");
    expect(html).not.toMatch(/font-size:\d+px/);
  });
});
