// 2차 감사 C(2026-11-04) web 몫: 긴 이름의 색 순서, 다시 정하기의 색·분석 기록·지도 설정, 소수 눈금, 퍼널 색 견본
import { describe, expect, it } from "vitest";
import { MAX_ORDER, labelIndex } from "@/lib/calc";
import { newOrder } from "@/lib/series";
import type { Card, CardKind } from "@/lib/types";
import { tickBy, tickNum } from "@/components/cards/chart/Charts";
import { FUNNEL_COLORS, funnelColors } from "@/components/cards/svg/__pure__";
import { buildCard, emptyForm, formFromCard, refillColors, type Form } from "@/components/edit/menu/build";
import { freshColors } from "@/components/edit/menu/kinds";

const card = (kind: CardKind, more: Partial<Card> = {}): Card => ({ id: "c", kind, title: "t", layout: { x: 0, y: 0, w: 4, h: 4 }, colors: freshColors(), view: { type: "" } as Card["view"], source: {}, data: {}, ...more });

describe("AC1 긴 이름은 색 순서에 넣지 않는다(서버 check_colors 100자 상한)", () => {
  it("100자를 넘으면 붙이지 않고 MAX_ORDER", () => {
    const order: string[] = [], long = "가".repeat(101);
    expect(labelIndex(order, long)).toBe(MAX_ORDER);
    expect(order).toEqual([]);
    expect(labelIndex(order, "가".repeat(100))).toBe(0);
  });
  it("새 순서에도 긴 이름이 들어가지 않는다", () => {
    expect(newOrder(card("wordcloud"), "series", ["아주 긴 댓글 ".repeat(20)])).toBeNull();
  });
});

describe("AC2 [데이터 다시 정하기]는 정해 둔 색을 지킨다", () => {
  it("값이 있던 카드는 옛 색, 빈 틀만 새 색", () => {
    const colors = { series: { 합계: "#123456" }, parts: {}, order: ["합계"], parts_order: [] };
    const filled = card("kpi", { colors, source: { body: {} } });
    expect(refillColors(filled, freshColors())).toEqual(colors);
    expect(refillColors(card("kpi", { colors }), freshColors())).toEqual(freshColors());
  });
});

describe("AC3 손수 그린 눈금은 소수 꼬리를 줄인다(RC4: 간격으로 자릿수)", () => {
  it("상자 그림·캔들의 3눈금(간격 = 범위/2), 1만 이상은 짧게", () => {
    expect(tickBy(2994.2)(2995.3815)).toBe("2,995");
    expect(tickBy(0.5)(1.1666)).toBe("1.2");
    expect(tickNum(12345)).toBe("1.2만");
  });
});

describe("AC5 [데이터 다시 정하기]가 분석 기록·지도 설정을 되돌린다", () => {
  const again = (c: Partial<Card>, kind: CardKind) => buildCard({ ...emptyForm(kind), ...formFromCard(c as Card), table: "T", rows: [] } as Form, ["sum", "count"]);
  it("퍼널·생키·코호트 source.analysis", () => {
    for (const [kind, more] of [["funnel", { fWin: "2", fUnit: "hour" }], ["sankey", { fWin: "30" }], ["cohort", { cUnit: "week" }]] as [CardKind, Partial<Form>][]) {
      const first = buildCard({ ...emptyForm(kind), table: "T", fUser: "u", fStep: "e", fTime: "ts", fSteps: "a, b, c", ...more } as Form, ["sum", "count"]);
      expect(again(first, kind).source?.analysis, kind).toEqual(first.source?.analysis);
    }
  });
  it("지도 view.geo(배경 타일·점)", () => {
    const first = buildCard({ ...emptyForm("geomap"), table: "T", gPath: "g.geojson", gName: "nm", gLat: "lat", gLon: "lon", gValue: "amt", gAgg: "mean", gPoints: false, gTiles: true } as Form, ["sum", "count"]);
    expect(again(first, "geomap").view?.geo).toEqual(first.view?.geo);
  });
});

describe("AC6 퍼널 색 견본은 그림과 같다", () => {
  it("정하지 않으면 고정 색(단계 막대는 팔레트 첫 색), 정하면 그 색", () => {
    expect(funnelColors(card("funnel"))).toEqual({ "단계 막대": FUNNEL_COLORS.bar, "다음 단계": FUNNEL_COLORS.next, 이탈: FUNNEL_COLORS.drop });
    expect(funnelColors(card("funnel"), ["#abcdef"])["단계 막대"]).toBe("#abcdef");
    expect(funnelColors(card("funnel", { colors: { ...freshColors(), series: { 이탈: "#111111" } } })).이탈).toBe("#111111");
  });
});
