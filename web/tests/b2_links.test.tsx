// B2+B4(plans/dash-blocks.md "B2+B4 세부 명세 (2차, 확정)", 3차 손질 포함): 숫자 타일 카드(Tile)·0건 한 벌(Empty)·구하는 중 모양(CSS)·링크 순수 함수(lib/links)·꼬리표 문구·새 요소 등록·빈 곳 누르기·도구 줄 칩 설정. 데이터는 지어낸 것.
// it.fails: 기존 파일을 고쳐야 통과하는 시험. 주석에 적은 항목이 끝나면 그 항목의 strict-coder가 .fails를 지운다.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ComponentType } from "react";
import type { Card, CardData, Dashboard } from "@/lib/types";
import { PAINTERS } from "@/components/cards/SvgCards";
import { EMPTY_TEXT, Empty } from "@/components/cards/svg/common";
import { Tile } from "@/components/cards/svg/Tile";
import { CARD_KINDS, galleryItems } from "@/components/edit/menu/kinds";
import { FRAME_KINDS, filterNote, frameError, freshFrame, type FrameKind } from "@/components/frame/registry";
import { fieldsFor, filtersFor, freshLinks, isSender, linkTargets, missingTargets, onlySeries, pressLabel, tileBig, tileSmall, type Press } from "@/lib/links";

const src = (p: string) => readFileSync(join(__dirname, "..", p), "utf8");
const MERGE = ["sum", "count"];
const THEME = { card: "#fff", head: "#eee", ink: "#000", text: "#555", line: "#ddd", err: "#c00", ok: "#070", series: ["#111", "#222"], hi: "#333" };
const DAYS = ["2026-01-01", "2026-01-02", "2026-01-03"];
const DATA = { by_unit: { day: { labels: DAYS, series: [{ name: "합계", agg: "sum", sum: [1, 2, 3], count: [1, 1, 1] }] } } } as unknown as CardData;
const card = (kind: string, data: CardData, view: Record<string, unknown> = {}): Card => ({ id: "t1", kind, title: "타일", layout: { x: 0, y: 0, w: 3, h: 2 },
  colors: { series: {}, parts: {}, order: [], parts_order: [] }, view: { type: "", unit: "day", compare: { mode: "span" }, better: "up", ...view } as Card["view"],
  source: { body: { series: [{ y: "amt", agg: "sum" }] } }, data } as unknown as Card);
const paint = (Paint: ComponentType<Record<string, unknown>>, c: Card) => renderToStaticMarkup(<Paint card={c} colors={THEME} range={undefined} merge={MERGE} />);
const bigNumber = (html: string) => html.match(/class="kpi-val"[^>]*>([^<]*)/)?.[1];
/** 링크 시험용 카드: id·종류·보기만 의미가 있다 */
const mk = (id: string, kind: string, view: Record<string, unknown> = {}, more: Record<string, unknown> = {}): Card =>
  ({ id, kind, title: id, layout: { x: 0, y: 0, w: 2, h: 2 }, colors: { series: {}, parts: {}, order: [], parts_order: [] }, view, source: {}, data: {}, ...more } as unknown as Card);

describe("숫자 타일 — Tile", () => {
  it("값은 KPI와 같은 큰 숫자, 단위는 작게, 추이선 없음", () => {
    const html = paint(Tile as unknown as ComponentType<Record<string, unknown>>, card("tile", DATA, { unit_text: "건" }));
    expect(html.startsWith('<div class="cbox kpi tile">')).toBe(true);
    expect(html).toContain('<span class="kpi-unit">건</span>');
    expect(html).not.toContain("kpi-spark");
    expect(html).not.toContain("bcmp");  // 화면 틀 밖에서는 비교 문장이 없다
    expect(bigNumber(html)).toBe(bigNumber(paint(PAINTERS.kpi as unknown as ComponentType<Record<string, unknown>>, card("kpi", DATA, { unit_text: "건" }))));
  });
  it("단위가 없으면 단위 칸이 없다", () => {
    expect(paint(Tile as unknown as ComponentType<Record<string, unknown>>, card("tile", DATA))).not.toContain("kpi-unit");
  });
  it("설명 글은 큰 숫자 아래 한 줄", () => {
    const html = paint(Tile as unknown as ComponentType<Record<string, unknown>>, card("tile", DATA, { note: "지난주보다" }));
    expect(html).toContain('<div class="hint tile-note">지난주보다</div>');
    expect(html.indexOf("tile-note")).toBeGreaterThan(html.indexOf("kpi-val"));
    expect(paint(Tile as unknown as ComponentType<Record<string, unknown>>, card("tile", DATA))).not.toContain("tile-note");
  });
  it("카드 모음·추가 메뉴·그리기 표에 있다 (B2 PAINTERS·B2 kinds (수정))", () => {  // 마지막 항목: B2 kinds (수정)
    expect(PAINTERS.tile).toBe(Tile);
    expect(CARD_KINDS.tile?.slice(0, 3)).toEqual(["숫자 타일", 1, 1]);
    const kinds = galleryItems({}).map(s => s.kind);
    expect(kinds.indexOf("tile")).toBe(kinds.indexOf("kpi") + 1);
  });
});

describe("0건 한 벌 — Empty", () => {
  it("몸통 가운데에 '값이 없어요' 한 줄", () => {
    expect(EMPTY_TEXT).toBe("값이 없어요");
    expect(renderToStaticMarkup(<Empty />)).toBe('<div class="cempty" role="status">값이 없어요</div>');
  });
  it("있는 카드의 빈 안내가 모두 Empty로 (B2 0건 자리 (수정))", () => {  // 마지막 항목: B2 0건 자리 (수정)
    const empty = { values: {}, bins: { edges: [], counts: [] } } as unknown as CardData;
    expect(paint(PAINTERS.histogram as unknown as ComponentType<Record<string, unknown>>, card("histogram", empty))).toContain('<div class="cempty" role="status">값이 없어요</div>');
    for (const f of ["BoxPlot", "Bullet", "Bump", "CalHeat", "Candle", "Cohort", "Deviation", "DualTrend", "Heatmap", "Histogram", "MapCard", "Sankey", "TargetTrend", "Treemap", "WordCloud"]) {
      const text = src(`components/cards/svg/${f}.tsx`);
      expect(text, f).toContain("<Empty />");
      expect(text, f).not.toMatch(/NOTHING|이 기간의 값이 없어요|"값이 없어요"|보여 줄 대상이 없어요/);
    }
    expect(src("components/cards/svg/common.tsx")).not.toContain("NOTHING");
    expect(src("components/cards/ChartCard.tsx")).toContain("return box(<Empty />);");
  });
});

describe("구하는 중 모양 — CSS", () => {
  it("이전 값을 흐리게 + 작은 돌림표, 움직임 줄이기에서는 돌지 않는다 (B2 CSS (2차))", () => {  // 마지막 항목: B2 CSS (2차)
    const css = src("styles/dash_edit.css");
    expect(css).toContain("#dash :is(.bcard.busy > .bbody, .fr-chips.busy) > :not(.bspin) { opacity:.5; }");
    expect(css).toMatch(/#dash \.bspin \{[^}]*animation:bspin/);
    expect(css).toMatch(/prefers-reduced-motion: reduce\) \{ #dash \.bspin \{ animation:none; \} \}/);
    expect(css).toMatch(/\.cempty \{[^}]*place-items:center/);
    expect(css).toMatch(/\.fr-chips, \.fr-tags \{[^}]*position:relative/);  // 칩 줄의 돌림표 자리
    expect(css).toContain("#dash [data-ambient] :is(.fr-btn, .fr-seg-b, .fr-chip)[aria-pressed=true]::before");  // 엠비언트를 세그먼트 칸·칩에도
    expect(css).not.toMatch(/#dash \[data-ambient\] \.fr-btn\[aria-pressed=true\]::before/);
  });
});

describe("링크 — 보내는 쪽", () => {
  it("차트·타일·링크 요소만 보낸다", () => {
    for (const k of ["chart", "tile", "segment", "chip_row", "period_pick", "asof_field", "unit_seg", "top_strip"]) expect(isSender(k), k).toBe(true);
    for (const k of ["kpi", "table", "button", "filter_bar", "text", "page_tabs"]) expect(isSender(k), k).toBe(false);
  });
});

describe("링크 — 상대 고르기", () => {
  const cards = [mk("c1", "chart", { cross: true }), mk("c2", "kpi"), mk("f1", "button", { frame: {} }), mk("p1", "period_pick", { frame: {} }), mk("c3", "chart")];
  it("저장된 목록이 있으면 그대로(빈 목록도)", () => {
    expect(linkTargets(mk("s", "tile", { link_to: ["c2"] }), cards)).toEqual(["c2"]);
    expect(linkTargets(mk("s", "chart", { cross: true, link_to: [] }), cards)).toEqual([]);
  });
  it("옛 대시보드: 누르기 차트(자기 자신 포함)·기간 요소·기간 칸 있는 위쪽 줄은 데이터 카드 전부, 나머지는 없음", () => {
    expect(linkTargets(cards[0], cards)).toEqual(["c1", "c2", "c3"]);
    expect(linkTargets(cards[3], cards)).toEqual(["c1", "c2", "c3"]);
    expect(linkTargets(cards[4], cards)).toEqual([]);  // 누르기를 안 켠 차트
    expect(linkTargets(mk("t1", "top_strip", { frame: { parts: ["name", "theme"] } }), cards)).toEqual([]);
    expect(linkTargets(mk("t2", "top_strip", { frame: { parts: ["asof"] } }), cards)).toEqual(["c1", "c2", "c3"]);
  });
  it("놓을 때는 같은 쪽 데이터 카드", () => {
    const sec = (id: string, page: string) => ({ id, title: "", layout: { x: 0, y: 0, w: 6, h: 4 }, follow: true, view: { page } });
    const dash = { id: "d", name: "d", pages: ["A", "B"], sections: [sec("s1", "A"), sec("s2", "B")],
      cards: [mk("c1", "chart", {}, { section: "s1" }), mk("c2", "kpi", {}, { section: "s2" }), mk("f1", "button", { frame: {} }, { section: "s1" })] } as unknown as Dashboard;
    expect(freshLinks({ id: "new", kind: "tile" }, dash, "A")).toEqual(["c1"]);
    expect(freshLinks({ id: "c1", kind: "tile" }, dash, "A")).toEqual([]);
    expect(freshLinks({ id: "new", kind: "segment" }, dash, "B")).toEqual(["c2"]);
    expect(freshLinks({ id: "new", kind: "chart" }, dash, "A")).toEqual(["c1", "new"]);  // 차트는 자기 자신도 걸러진다

  });
  it("지워진 상대", () => {
    expect(missingTargets(mk("s", "tile", { link_to: ["c1", "gone"] }), cards)).toEqual(["gone"]);
    expect(missingTargets(mk("s", "tile"), cards)).toEqual([]);
  });
});

describe("링크 — 카드마다 필터", () => {
  const tileBody = { base: "orders", conditions: ["store = B"] };
  const cards = [mk("t1", "tile", { press: "cond", link_to: ["c1", "c2"], link_map: { c2: { store: "area" } } }), mk("h1", "chart", { cross: true, link_to: ["c2"], link_map: { c2: { kind: "type" } } }),
    mk("c1", "chart"), mk("c2", "chart"), mk("k1", "chip_row", { frame: { column: "kind" }, link_to: ["c1"] })];
  const presses: Press[] = [{ from: "t1", tile: { body: tileBody, periods: true } }, { from: "h1", value: { column: "kind", in: ["x"] } },
    { from: "gone", value: { column: "a", in: ["1"] } }, { from: "k1", value: { column: "kind", in: ["y"] } }];
  it("링크한 카드만, 맞춘 컬럼으로", () => {
    expect(filtersFor("c1", presses, cards)).toEqual([{ body: tileBody, periods: true }, { column: "kind", in: ["y"] }]);
    expect(filtersFor("c2", presses, cards)).toEqual([{ body: tileBody, as: { store: "area" }, periods: true }, { column: "type", in: ["x"] }]);
    expect(filtersFor("c3", presses, cards)).toEqual([]);
  });
});

describe("링크 — 기간 요소", () => {
  const cards = [mk("p1", "period_pick", { frame: {}, link_to: ["c1"] }), mk("p2", "period_pick", { frame: {}, link_to: ["c1", "c2"] }), mk("a1", "asof_field", { frame: {}, link_to: ["c2"] }),
    mk("u1", "unit_seg", { frame: {}, link_to: ["c1"] }), mk("top", "top_strip", { frame: { parts: ["asof"] }, link_to: ["c1"] }), mk("c1", "chart"), mk("c2", "chart")];
  const elems = { p1: { at: 3, period: "지난달" }, p2: { at: 1, period: "최근 7일" }, a1: { at: 2, as_of: "2026-10-01" }, u1: { at: 4, unit: "month" as const }, top: { at: 5, period: "이번 달", as_of: "2026-09-30" } };
  it("링크된 요소 가운데 마지막에 바꾼 것, 위쪽 줄은 보이는 칸만", () => {
    expect(fieldsFor("c1", cards, elems)).toEqual({ period: "지난달", as_of: "2026-09-30", unit: "month" });
    expect(fieldsFor("c2", cards, elems)).toEqual({ period: "최근 7일", as_of: "2026-10-01" });
    expect(fieldsFor("c3", cards, elems)).toEqual({});
    expect(fieldsFor("c1", cards, {})).toEqual({});  // 바꾼 요소가 없으면 저장된 기간
  });
});

describe("링크 — 세그먼트 보기", () => {
  const data = { values: { labels: ["a"], series: [{ name: "합계", values: [1] }, { name: "개수", values: [2] }] },
    by_unit: { day: { labels: DAYS, series: [{ name: "합계", agg: "sum", sum: [1, 2, 3] }, { name: "개수", agg: "count", count: [1, 1, 1] }] } } } as unknown as CardData;
  it("이름이 같은 계열만 남기고 원본은 그대로", () => {
    const one = onlySeries(data, "개수");
    expect(one.values?.series?.map(s => s.name)).toEqual(["개수"]);
    expect(one.by_unit?.day?.series.map(s => s.name)).toEqual(["개수"]);
    expect(one.values?.labels).toEqual(["a"]);
    expect(onlySeries(data, "")).toBe(data);
    expect(data.values?.series?.length).toBe(2);
  });
});

describe("링크 — 꼬리표", () => {
  it("tileBig: '=' 조건의 값 + 타일 이름", () => {
    expect(tileBig("판매 건수", ["지역 = 부산", "금액 >= 10000"])).toBe("부산 판매 건수");
    expect(tileBig("판매 건수", ["지역 = 부산", { line: "채널 = '온라인 몰'", quotes: true }])).toBe("부산 온라인 몰 판매 건수");
    expect(tileBig("판매 건수", ["금액 >= 1", "지역 != 부산", "이름 =~ ab"])).toBe("판매 건수");
    expect(tileBig("판매 건수", [{ any: ["지역 = 부산"] }, { column: "지역", in: ["대구"] }])).toBe("판매 건수");
  });
  it("tileSmall: 사용자가 정한 문구(>=·!=·between·like·is null)", () => {
    expect(tileSmall(["지역 = 부산", "금액 >= 10000"], [])).toBe("(금액이 10000 보다 크거나 같을 때)");
    expect(tileSmall(["지역 != 부산"], [])).toBe("(부산 X)");
    expect(tileSmall(["금액 between 1 10"], [])).toBe("(1~10 사이)");
    expect(tileSmall(["이름 like ab"], [])).toBe("(ab가 들어간 값)");
    expect(tileSmall(["메모 is null"], [])).toBe("(메모가 비어 있을 때)");
    expect(tileSmall(["지역 = 부산"], [])).toBe("");
  });
  it("tileSmall: 같은 꼴로 맞춘 나머지 연산자·or·여러 줄·기간 줄", () => {
    expect(tileSmall(["금액 not between 1 10", "이름 not like ab"], [])).toBe("(1~10 사이 X, ab가 들어간 값 X)");
    expect(tileSmall(["지역 in 부산, 대구", "지역 except 서울"], [])).toBe("(부산, 대구 중 하나, 서울 X)");
    expect(tileSmall(["이름 starts 서울", "이름 ends 부산", "메모 is not null"], [])).toBe("(서울로 시작하는 값, 부산으로 끝나는 값, 메모가 비어 있지 않을 때)");
    expect(tileSmall(["금액 > 1 or 금액 < 0", { any: ["지역 = 부산", "지역 = 대구"] }], [])).toBe("(금액이 1 보다 클 때 또는 금액이 0 보다 작을 때, 부산 또는 대구)");
    expect(tileSmall([], ["날짜 2026-01-01 ~ 2026-01-31"])).toBe("(날짜가 2026-01-01 ~ 2026-01-31일 때)");
  });
  it("tileSmall: 받침에 따라 조사, 읽을 수 없는 줄은 글자 그대로", () => {
    expect(tileSmall(["금액 <= 5", "등급 < 3", "이름 =~ ab"], [])).toBe("(금액이 5 보다 작거나 같을 때, 등급이 3 보다 작을 때, 이름이 ab일 때(대소문자 무시))");
    expect(tileSmall(["amt >= 1"], [])).toBe("(amt가 1 보다 크거나 같을 때)");
    expect(tileSmall(["알 수 없는 줄"], [])).toBe("(알 수 없는 줄)");
  });
  it("pressLabel: 값은 '컬럼 = 값', 타일은 tileBig", () => {
    const cards = [mk("t1", "tile", {}, { title: "판매 건수" })];
    expect(pressLabel({ from: "t1", tile: { body: { conditions: ["지역 = 부산"] }, periods: false } }, cards)).toBe("부산 판매 건수");
    expect(pressLabel({ from: "x", value: { column: "지역", in: ["부산"] } }, cards)).toBe("지역 = 부산");
  });
});

describe("빈 곳 누르기 — 격자", () => {
  it("보는 곳에서만, 누를 수 있는 곳(단추·입력·데이터 카드 몸통)을 빼고 모두 풀기 (B4 Grid (수정))", () => {  // 마지막 항목: B4 Grid (수정)
    const grid = src("components/grid/Grid.tsx");
    expect(grid).toContain('const ACTIVE = PRESSABLE + ", .bcard:not(.frame) > .bbody";');
    expect(grid).toMatch(/onClick=\{look \? e => \{ if \(!\(e\.target as Element\)\.closest\(ACTIVE\)\) clearFilters\(\)/);
  });
});

describe("도구 줄 칩 설정 — FilterBar", () => {
  it("대시보드 press_chips가 거짓이면 누른 칩을 안 보이고, 만드는 곳에서 켜고 끈다 (B4 FilterBar (3차))", () => {  // 마지막 항목: B4 FilterBar (3차)
    const bar = src("components/shell/FilterBar.tsx");
    expect(bar).toContain("dash?.press_chips === false ? [] : presses.filter(p => p.value)");
    expect(bar).toContain("patch: { press_chips: e.target.checked }");
    expect(bar).toContain("pressLabel(");
  });
});

describe("링크 — 새 요소 등록", () => {
  it("세그먼트·칩 줄·걸린 필터 줄 (B4 registry (수정))", () => {  // 마지막 항목: B4 registry (수정)
    const kinds = FRAME_KINDS as unknown as Record<string, { name: string; group: string; w: number; h: number }>;
    expect(Object.keys(kinds).slice(-3)).toEqual(["segment", "chip_row", "filter_bar"]);
    expect(["segment", "chip_row", "filter_bar"].map(k => kinds[k].name)).toEqual(["세그먼트", "칩 줄", "걸린 필터 줄"]);
    expect(freshFrame("segment" as FrameKind)).toEqual({ mode: "view", column: "", cells: [], ambient: false });
    expect(freshFrame("filter_bar" as FrameKind)).toEqual({ clear_label: "", ambient: false });
    expect(frameError("segment" as FrameKind, { cells: [] })).toBe("칸을 하나 이상 더하세요.");
    expect(frameError("chip_row" as FrameKind, { column: "" })).toBe("거를 컬럼을 고르세요.");
    expect((filterNote as unknown as (...a: unknown[]) => string)("", "", [{ column: "a", in: ["1"] }], ["부산 판매 건수"])).toBe("a: 1 · 부산 판매 건수");
  });
});
