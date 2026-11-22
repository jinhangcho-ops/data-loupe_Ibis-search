// B3 목록 카드(plans/dash-blocks.md "B3 세부 명세 (2차)"): 줄 만들기(listRows)·고르기(nextPick)·CSV(tableChart·listCsv)·그리기(List)와
// 카드 종류 등록·링크 보내기·몸통이 누르기를 넘김·단추 할 일 "목록을 CSV로 저장"·그림의 표 모양·CSS. 데이터는 지어낸 것.
// it.fails: 기존 파일을 고쳐야 통과하는 시험. 주석에 적은 항목이 끝나면 그 항목의 strict-coder가 .fails를 지운다.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ComponentType } from "react";
import type { Card, CardData, ListPart, ListView } from "@/lib/types";
import { showValue } from "@/lib/calc";
import { PAINTERS } from "@/components/cards/SvgCards";
import { LIST_CSV_BOM, LIST_NO_PARTS, List, listCsv, listRows, nextPick, tableChart, type ListCol } from "@/components/cards/svg/List";
import { CARD_KINDS, galleryItems } from "@/components/edit/menu/kinds";
import { buildCard, emptyForm } from "@/components/edit/menu/build";
import { FRAME_ACTIONS, frameError, runAction, type ActionHost } from "@/components/frame/registry";
import { isSender } from "@/lib/links";

const src = (p: string) => readFileSync(join(__dirname, "..", p), "utf8");
const MERGE = ["sum", "count"];
const THEME = { card: "#fff", head: "#eee", ink: "#000", text: "#555", line: "#ddd", err: "#c00", ok: "#070", series: ["#111", "#222"], hi: "#333" };
const COLUMNS: ListCol[] = [{ name: "store", kind: "group" }, { name: "kind", kind: "group" }, { name: "합계 (amt)", kind: "value", agg: "sum" }, { name: "개수", kind: "value", agg: "count" }];
/** 컬럼별 값(dash_calc.list_data의 values.data): 줄 셋 */
const DATA: unknown[][] = [["A", "B", "C"], ["x", "y", null], [10, 30, null], [1, 4, 5]];
const part = (p: Partial<ListPart>): ListPart => ({ kind: "text", column: "store", swap: false, format: "number", rules: [], basis: "max", side: "start", ...p });
const view = (v: Partial<ListView>): ListView => ({ parts: [], pick: "none", key: "", swaps: [], report: "screen", ...v });
const PARTS = [part({}), part({ kind: "chip", column: "kind" }), part({ kind: "number", column: "개수", side: "end" }), part({ kind: "bar", column: "합계 (amt)", side: "end" })];
const card = (list: ListView | undefined, data: unknown[][] = DATA): Card => ({ id: "l1", kind: "list", title: "목록", layout: { x: 0, y: 0, w: 4, h: 4 },
  colors: { series: {}, parts: {}, order: [], parts_order: [] }, view: { type: "", unit: "day", compare: { mode: "span" }, better: "up", group_cols: ["store", "kind"], ...(list ? { list } : {}) } as Card["view"],
  source: { body: { series: [{ y: "amt", agg: "sum" }] } }, data: { values: { columns: COLUMNS, data, more: false } } as unknown as CardData } as unknown as Card);
const draw = (c: Card, more: Record<string, unknown> = {}) => {
  const L = List as unknown as ComponentType<Record<string, unknown>>;
  return renderToStaticMarkup(<L card={c} colors={THEME} range={undefined} merge={MERGE} {...more} />);
};

describe("목록 — listRows", () => {
  it("설정한 부분을 차례대로, 값 모양을 입힌 글자", () => {
    const rows = listRows(COLUMNS, DATA, view({ parts: PARTS }), 0);
    expect(rows.map(r => r.cells.map(c => [c.kind, c.side, c.text]))).toEqual([
      [["text", "start", "A"], ["chip", "start", "x"], ["number", "end", showValue(1, "number")], ["bar", "end", showValue(10, "number")]],
      [["text", "start", "B"], ["chip", "start", "y"], ["number", "end", showValue(4, "number")], ["bar", "end", showValue(30, "number")]],
      [["text", "start", "C"], ["chip", "start", ""], ["number", "end", showValue(5, "number")], ["bar", "end", ""]],
    ]);
    expect(listRows(COLUMNS, DATA, view({ parts: [part({ kind: "number", column: "개수", format: "percent" })] }), 0)[0].cells[0].text).toBe(showValue(1, "percent"));
  });
  it("막대 몫: 최댓값 대비 또는 합계 몫(빈 값·0 이하는 0), 막대가 아니면 null", () => {
    const max = listRows(COLUMNS, DATA, view({ parts: [part({ kind: "bar", column: "개수" })] }), 0).map(r => r.cells[0].ratio);
    expect(max).toEqual([0.2, 0.8, 1]);
    const sum = listRows(COLUMNS, DATA, view({ parts: [part({ kind: "bar", column: "개수", basis: "sum" })] }), 0).map(r => r.cells[0].ratio);
    expect(sum).toEqual([0.1, 0.4, 0.5]);
    expect(listRows(COLUMNS, DATA, view({ parts: [part({ kind: "bar", column: "합계 (amt)" })] }), 0)[2].cells[0].ratio).toBe(0);
    expect(listRows(COLUMNS, DATA, view({ parts: [part({})] }), 0)[0].cells[0].ratio).toBeNull();
  });
  it("색 규칙이 맞으면 그 색, 아니면 null", () => {
    const rows = listRows(COLUMNS, DATA, view({ parts: [part({ kind: "tag", rules: [{ match: "*B*", color: "#e03131" }] })] }), 0);
    expect(rows.map(r => r.cells[0].color)).toEqual([null, "#e03131", null]);
  });
  it("값 바꾸기를 따르는 부분은 고른 단추의 값을 읽는다", () => {
    const list = view({ parts: [part({ kind: "number", column: "", swap: true })], swaps: [{ label: "합계", value: "합계 (amt)" }, { label: "건", value: "개수" }] });
    expect(listRows(COLUMNS, DATA, list, 0)[1].cells[0].text).toBe(showValue(30, "number"));
    expect(listRows(COLUMNS, DATA, list, 1)[1].cells[0].text).toBe(showValue(4, "number"));
  });
  it("거를 값: 고르기를 하면 거를 컬럼 값의 글자, 안 하거나 값이 비면 null", () => {
    expect(listRows(COLUMNS, DATA, view({ parts: PARTS, pick: "one", key: "store" }), 0).map(r => r.key)).toEqual(["A", "B", "C"]);
    expect(listRows(COLUMNS, DATA, view({ parts: PARTS, pick: "many", key: "kind" }), 0).map(r => r.key)).toEqual(["x", "y", null]);
    expect(listRows(COLUMNS, DATA, view({ parts: PARTS, key: "store" }), 0).map(r => r.key)).toEqual([null, null, null]);
  });
  it("값에 없는 컬럼이면 던진다", () => {
    expect(() => listRows(COLUMNS, DATA, view({ parts: [part({ column: "nope" })] }), 0)).toThrow(/nope/);
  });
});

describe("목록 — nextPick", () => {
  it("하나만: 다른 줄로 바꾸고 같은 줄은 풂, 여러 줄: 넣고 빼기", () => {
    expect(nextPick("one", [], "A")).toEqual(["A"]);
    expect(nextPick("one", ["A"], "B")).toEqual(["B"]);
    expect(nextPick("one", ["A"], "A")).toEqual([]);
    expect(nextPick("many", ["A"], "B")).toEqual(["A", "B"]);
    expect(nextPick("many", ["A", "B"], "A")).toEqual(["B"]);
  });
});

describe("목록 — CSV", () => {
  it("tableChart: 컬럼 이름 차례, 숫자는 숫자·빈 값은 null·나머지는 글자", () => {
    expect(tableChart(COLUMNS.slice(0, 3), [["A", "C"], ["x", null], [10, null]])).toEqual({
      columns: ["store", "kind", "합계 (amt)"], rows: [{ store: "A", kind: "x", "합계 (amt)": 10 }, { store: "C", kind: null, "합계 (amt)": null }],
    });
  });
  it("listCsv: BOM으로 시작하고 머리 줄에 컬럼 이름, 값이 없으면 던진다", () => {
    const text = listCsv(card(view({})).data);
    expect(text.startsWith(LIST_CSV_BOM)).toBe(true);
    expect(text).toContain("store");
    expect(() => listCsv({})).toThrow();
  });
});

describe("목록 — List 그리기", () => {
  it("고르기를 켜고 누르기를 받으면 줄마다 단추, 고른 값은 눌림", () => {
    const html = draw(card(view({ parts: PARTS, pick: "many", key: "store" })), { onPress: () => {}, pressed: { column: "store", in: ["B"] } });
    expect(html.startsWith('<div class="cbox list">')).toBe(true);
    expect(html.match(/<button type="button" class="lrow" aria-pressed="false">/g)?.length).toBe(2);
    expect(html.match(/<button type="button" class="lrow" aria-pressed="true">/g)?.length).toBe(1);
    expect(html).toContain('<span class="lstart"><span class="ltext">A</span><span class="chip lchip">x</span></span>');
    expect(html).toContain('style="--r:1"');  // B의 합계가 가장 크다
  });
  it("고르기를 안 하거나 누르기가 없으면 단추가 아니다", () => {
    expect(draw(card(view({ parts: PARTS })), { onPress: () => {} })).not.toContain("<button");
    expect(draw(card(view({ parts: PARTS, pick: "one", key: "store" })))).not.toContain("<button");
  });
  it("값 바꾸기 단추는 사용자가 적은 이름으로, 첫 단추가 눌린 채", () => {
    const html = draw(card(view({ parts: [part({ kind: "number", column: "", swap: true })], swaps: [{ label: "가", value: "합계 (amt)" }, { label: "나", value: "개수" }] })));
    expect(html).toContain('<button type="button" class="fr-seg-b" aria-pressed="true">가</button><button type="button" class="fr-seg-b" aria-pressed="false">나</button>');
    expect(draw(card(view({ parts: PARTS })))).not.toContain("fr-seg-b");
  });
  it("그림·보고서 모양이 표면 보고서용 표도 그린다", () => {
    expect(draw(card(view({ parts: PARTS, report: "table" })))).toContain('<table class="lprint">');
    expect(draw(card(view({ parts: PARTS })))).not.toContain("lprint");
  });
  it("부분이 없으면 안내, 줄이 없으면 0건, 값이 없으면 안내", () => {
    expect(draw(card(view({})))).toContain(LIST_NO_PARTS);
    expect(draw(card(view({ parts: PARTS }), [[], [], [], []]))).toContain('<div class="cempty" role="status">값이 없어요</div>');
    expect(draw({ ...card(view({ parts: PARTS })), data: {} } as Card)).toContain("아직 구한 값이 없어요");
  });
});

describe("목록 — 등록·연결", () => {
  it("카드 종류·카드 모음·그리기 표에 있다 (B3 PAINTERS (수정))", () => {  // 마지막 항목: B3 PAINTERS (수정)
    expect(PAINTERS.list).toBe(List);
    expect(CARD_KINDS.list?.slice(1, 3)).toEqual([0, 5]);
    const kinds = galleryItems({}).map(s => s.kind);
    expect(kinds.indexOf("list")).toBe(kinds.indexOf("table") + 1);
  });
  it("추가 메뉴로 만든 목록 카드: 묶음 컬럼과 값 (B3 build (수정))", () => {  // 마지막 항목: B3 build (수정)
    const made = buildCard({ ...emptyForm("list"), table: "orders", groupCols: "store, kind" }, ["sum", "count"]);
    expect(made.kind).toBe("list");
    expect(made.view?.group_cols).toEqual(["store", "kind"]);
    expect(() => buildCard({ ...emptyForm("list"), table: "orders" }, ["sum", "count"])).toThrow(/하나 이상/);
  });
  it("목록 카드는 링크를 보낸다 (B3 isSender (수정))", () => {  // 마지막 항목: B3 isSender (수정)
    expect(isSender("list")).toBe(true);
  });
  it("몸통이 고른 값·누르기를 그리는 카드에 넘기고, PC 계산이면 연결 없이도 받는다 (B3 Grid (수정))", () => {  // 마지막 항목: B3 Grid (수정)
    expect(src("components/cards/SvgCards.tsx")).toMatch(/<Paint [^>]*pressed=\{pressed\} onPress=\{onPress\}/);
    expect(src("components/cards/CardBody.tsx")).toContain("onPress");
    const grid = src("components/grid/Grid.tsx");  // PC 계산 대시보드는 연결 없이도 누르기를 받는다(사용자 답 B3 2차 1)
    expect(grid).toContain('pickable = !build && (!!conn || pc)');
    expect(grid).toContain("onPick={pickable ? (column, value) => cross(card.id, column, value) : undefined}");
    expect(grid).toMatch(/onPress=\{pickable \? /);
    expect(grid).not.toContain("build || !conn");
  });
  it("단추 할 일 '목록을 CSV로 저장' (B3 registry (수정))", () => {  // 마지막 항목: B3 registry (수정)
    expect(FRAME_ACTIONS.csv).toEqual({ name: "목록을 CSV로 저장", arg: "card" });
    expect(frameError("button", { label: "가", action: "csv", arg: "" })).toBe("저장할 목록을 고르세요.");
    const saved: string[] = [];
    const host = { cardIds: ["l1"], saveCsv: (id: string) => saved.push(id) } as unknown as ActionHost;
    runAction({ action: "csv", arg: "l1" }, host);
    expect(saved).toEqual(["l1"]);
    expect(() => runAction({ action: "csv", arg: "zz" }, host)).toThrow();
  });
  it("그림을 찍는 동안 표 모양으로 (B3 picture (수정))", () => {  // 마지막 항목: B3 picture (수정)
    expect(src("components/report/picture.ts")).toContain("dataset.picture");
  });
  it("목록 모양 CSS (B3 CSS (수정))", () => {  // 마지막 항목: B3 CSS (수정)
    const css = src("styles/dash_edit.css");
    expect(css).toMatch(/\.clist \{[^}]*overflow:auto/);
    expect(css).toMatch(/\.lrow\[aria-pressed="true"\] \{[^}]*var\(--accent\)/);
    expect(css).toMatch(/\.lbar > i \{[^}]*var\(--r\)/);
    expect(css).toMatch(/\.lprint \{ display:none; \}/);
    expect(css).toMatch(/html\[data-picture\] \.lprint \{ display:table; \}/);
  });
});
