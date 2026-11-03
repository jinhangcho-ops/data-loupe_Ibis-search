// 리뷰 C(2026-11-03 23:40) W1~W10: 다시 정하기에서 지운 목표·단위, 트리맵 윗단계 이름, 글자 폭 자르기, 상자 그림 라벨, 날짜가 가로축인 카드의 기간, 달력 Tab 칸, 앞 2계열 안내, 캔들 색, 히트맵 숫자
// 주의: Recharts 그림은 서버 렌더에서 빈 틀이라 트리맵은 칸 그림(TreeCell)만 따로 그려 본다.
import { renderToStaticMarkup } from "react-dom/server";
import type { TreemapNode } from "recharts";
import { describe, expect, it } from "vitest";
import type { ComponentType } from "react";
import type { Card, CardData, CardKind } from "@/lib/types";
import type { P } from "@/components/cards/svg/common";
import { PAINTERS } from "@/components/cards/SvgCards";
import { TreeCell } from "@/components/cards/svg/Treemap";
import { fitText } from "@/components/cards/svg/__pure__";
import { seriesKit } from "@/components/cards/chart/series";
import { buildCard, emptyForm, formFromCard, refillView } from "@/components/edit/menu/build";
import { datedCard, galleryItems, isShell, shellOf } from "@/components/edit/menu/kinds";
import { keepFocus } from "@/components/shell/calPick";
import { Calendar } from "@/components/shell/Calendar";

const THEME = { card: "#fff", head: "#eee", ink: "#000", text: "#555", line: "#ddd", err: "#c00", ok: "#070", series: ["#111"] };
const card = (kind: CardKind, data: CardData, view: Record<string, unknown> = {}): Card => ({ id: "c", kind, title: "카드", layout: { x: 0, y: 0, w: 4, h: 4 }, colors: { series: {}, parts: {}, order: [], parts_order: [] },
  view: { type: "", unit: "day", compare: { mode: "span" }, better: "up", ...view } as Card["view"], source: { table: "T", body: { series: [{ y: "amt", agg: "sum" }] } }, data });
const draw = (c: Card) => { const Paint = PAINTERS[c.kind] as ComponentType<P>; return renderToStaticMarkup(<Paint card={c} colors={THEME} range={undefined} merge={["sum", "count"]} />); };
const row = { label: "", y: "amt", agg: "sum" };

describe("W1 [데이터 다시 정하기]", () => {
  const again = (old: Card, more: Partial<ReturnType<typeof emptyForm>> = {}) =>
    refillView(old.view, buildCard({ ...emptyForm(old.kind), ...formFromCard(old), table: "T", rows: [row], ...more }, ["sum", "count"]).view!);
  it("목표·단위를 비우면 지운다(옛 값이 남지 않게)", () => {
    const v = again(card("kpi", {}, { target: { value: 100 }, unit_text: "명", fit: "fill" }), { targetValue: "", unitText: "" });
    expect(v.target).toBeUndefined();
    expect(v.unit_text).toBeUndefined();
    expect(v.fit).toBe("fill");  // 폼이 다루지 않는 모양 설정은 그대로
  });
  it("이름 목표는 기간·범위까지 그대로", () => {
    expect(again(card("kpi", {}, { target: { name: "매출", period: "2026-Q3", scope: "서울" } })).target).toEqual({ name: "매출", period: "2026-Q3", scope: "서울" });
    expect(again(card("kpi", {}, { target: { name: "매출", period: "2026-Q3" } }), { targetName: "방문" }).target).toEqual({ name: "방문" });  // 다른 이름을 고르면 그 이름만
  });
});

describe("W2·W3 트리맵 칸과 글자 폭", () => {
  const node = (o: Record<string, unknown>) => renderToStaticMarkup(<svg>{TreeCell({ depth: 2, index: 0, value: 60, children: null, tooltipIndex: "", x: 0, y: 0, width: 200, height: 100, name: "강남", fill: "#111", ...o } as unknown as TreemapNode)}</svg>);
  it("아랫단계 툴팁은 부모 › 이름, 부모의 왼쪽 위 칸은 부모 이름도", () => {
    const html = node({ root: { x: 0, y: 0, name: "서울" } });
    expect(html).toContain("서울 › 강남: 60");
    expect(html).toContain(">서울</text>");
    expect(node({ x: 50, root: { x: 0, y: 0, name: "서울" } })).not.toContain(">서울</text>");
  });
  it("한글은 글자 폭으로 자른다", () => {
    expect(fitText("abcdefgh", 100, 10)).toBe("abcdefgh");
    expect(fitText("가나다라마바사아자차", 60, 10)).toBe("가나다라마…");  // 한글 한 글자 10px + … 6.2px
    expect(fitText("가", 5, 10)).toBe("…");
  });
});

describe("W4 상자 그림 라벨", () => {
  it("20그룹이면 4칸마다 이름, 툴팁에는 모두", () => {
    const groups = Array.from({ length: 20 }, (_, i) => ({ name: `그룹${i}`, n: 10, min: 1, q1: 2, med: 3, q3: 4, max: 5 }));
    const html = draw(card("boxplot", { values: { groups } }));
    expect(html.match(/>그룹\d+</g)?.length).toBe(5);
    expect(html).toContain("그룹19 (10개)");
  });
});

describe("W5 날짜가 가로축인 카드의 기간", () => {
  const r = { start: "2026-01-01", end: "2026-01-31" };
  it("달력 히트맵·순위 변화·캔들은 기간 기준을 비워도 가로축 날짜로 다시 구한다", () => {
    for (const k of ["calheat", "bump", "candle"] as CardKind[]) {
      const c = { ...card(k, { values: {} }), source: { body: { x: "d" } } };
      expect(datedCard(c, r, false), k).toBe(true);
    }
    expect(datedCard({ ...card("histogram", { values: {} }), source: { body: { x: "amt" } } }, r, false)).toBe(false);  // 가로축이 날짜가 아닌 카드는 기간 기준이 있어야
  });
});

describe("W6 달력 Tab 칸", () => {
  it("끝 날이 기준일 뒤여도 보이는 달에 Tab으로 들어갈 칸이 하나", () => {
    const html = renderToStaticMarkup(<Calendar start="2026-12-01" end="2027-02-10" max="2026-11-03" onPick={() => {}} onClose={() => {}} />);
    expect(html.match(/tabindex="0"/g)?.length).toBe(1);
    expect(html).toContain("2026년 11월");
  });
  it("달을 넘기면 초점은 보이는 달의 날로(기준일 뒤면 기준일)", () => {
    expect(keepFocus("2026-11-03", "2026-08", "2026-11-03")).toBe("2026-08-01");
    expect(keepFocus("2026-11-03", "2026-10", "2026-11-03")).toBe("2026-11-03");
    expect(keepFocus("2026-09-15", "2026-10", "2026-10-05")).toBe("2026-10-01");
  });
});

describe("W7 앞 2계열만 그리는 종류", () => {
  const kit = seriesKit({ agg_names: {}, merge: [], dash: { types: { butterfly: "나비형 막대", range: "범위 막대" }, time_types: [] } } as never);
  const c = card("chart", { values: { labels: ["a"], series: [0, 1, 2].map(i => ({ name: `s${i}`, values: [1] })) } });
  it("나비형·범위 막대는 계열이 3개 이상이면 안내", () => {
    expect(kit.typeNote(c, "butterfly")).toContain("앞 2계열(s0, s1)만 그려요");
    expect(kit.typeNote(c, "range")).toContain("나머지 1개는 빼요");
  });
});

describe("W9·W10 캔들 색·히트맵 숫자", () => {
  it("캔들 오름 색은 주 색(토큰)과 같다", () => expect(draw(card("candle", { values: { labels: ["d"], candles: [{ open: 1, high: 3, low: 0, close: 2 }] } }))).toContain("var(--c1)"));
  it("히트맵 칸 숫자는 짧게", () => expect(draw(card("heatmap", { values: { rows: ["A"], cols: ["x"], cells: [[12345678]] } }))).toContain(">1234.6만<"));
});

describe("W8 흐름 카드 빈 틀", () => {
  it("카드 모음의 퍼널·생키·코호트 빈 틀은 빈 틀로 보인다(값을 구하지 않고 안내)", () => {
    for (const s of galleryItems({}).filter(t => ["funnel", "sankey", "cohort"].includes(t.kind))) expect(isShell(shellOf(s) as Card), s.kind).toBe(true);
  });
});
