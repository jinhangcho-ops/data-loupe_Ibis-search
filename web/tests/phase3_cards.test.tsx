// Phase 3(plans/phase-3.md) 카드 그림: 퍼널 사다리꼴 보기와 단계마다 따로 센 퍼널의 설명(서버 렌더 글자로 확인).
// Funnel (수정)이 끝나야 통과하므로 it.fails로 표시했다(고친 뒤 it으로 바꾼다).
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ComponentType } from "react";
import type { Card } from "@/lib/types";
import type { P } from "@/components/cards/svg/common";
import { PAINTERS } from "@/components/cards/SvgCards";
import { EACH_TEXT } from "@/components/cards/svg/__pure__";

const THEME = { card: "#fff", head: "#eee", ink: "#000", text: "#555", line: "#ddd", err: "#c00", ok: "#070", series: ["#111"] };
const steps = [{ name: "획득", users: 600, conv: 1, from_first: 1, drop: 0 }, { name: "활성", users: 300, conv: 0.5, from_first: 0.5, drop: 300 }];
const funnel = (view: Record<string, unknown>, values: Record<string, unknown>): Card => ({ id: "c", kind: "funnel", title: "퍼널", layout: { x: 0, y: 0, w: 4, h: 4 },
  colors: { series: {}, parts: {}, order: [], parts_order: [] }, view: { type: "", ...view } as Card["view"], source: {}, data: { values } });
const draw = (c: Card) => { const Paint = PAINTERS[c.kind] as ComponentType<P>; return renderToStaticMarkup(<Paint card={c} colors={THEME} range={undefined} merge={["sum", "count"]} />); };

describe("퍼널 보기(Funnel 수정)", () => {
  it("흐름 보기(기본)는 그대로 표가 있다", () => {
    expect(draw(funnel({}, { steps }))).toContain("<table");
  });
  it("사다리꼴 보기: 단계마다 번호·이름·인원·전환율, 표는 없다", () => {
    const html = draw(funnel({ funnel_shape: "ladder" }, { steps }));
    expect(html).toContain("1. 획득");
    expect(html).toContain("300명 · 전 단계 대비 50.0% · 첫 단계 대비 50.0%");
    expect(html).not.toContain("<table");
  });
  it("걸린 시간은 그림이 아니라 표로, 고른 값만(view.gap_stats). 고른 것이 없으면 표 없음", () => {
    const gaps = [{ from: "획득", to: "활성", n: 3, mean: 31200, median: 3600, trimmed: 3600 }];
    for (const shape of ["flow", "ladder"]) {
      const html = draw(funnel({ funnel_shape: shape, gap_stats: ["median"] }, { steps, gaps, trim: 0.1 }));
      expect(html, shape).toContain("획득 → 활성");
      expect(html, shape).toContain("중앙값 1시간 (3명)");
      expect(draw(funnel({ funnel_shape: shape, gap_stats: [] }, { steps, gaps, trim: 0.1 })), shape).not.toContain("중앙값 1시간");  // "획득 → 활성"은 그림 아래 문장에도 있어 표의 값으로 본다
    }
  });
  it("단계마다 따로 센 퍼널은 순서 문장 대신 EACH_TEXT", () => {
    const html = draw(funnel({ funnel_shape: "ladder" }, { steps: steps.map(s => ({ ...s, drop: null })), count: "each" }));
    expect(html).toContain(EACH_TEXT);
    expect(html).not.toContain("까지 갔어요");
  });
});
