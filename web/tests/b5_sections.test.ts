// B5 섹션·조각(plans/dash-blocks.md "B5 세부 명세"): 눈금·간격 상수가 서버(core/dash_store.py)와 같은지, 순수 계산(grid/sections.ts·report/layout.pieceRect),
// 고친 것(calc.sizeAt의 max, store reducer의 sections, calc.onPage의 섹션)을 본다. 값은 지어낸 것만 쓴다.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  ALIGN_FEW, ALIGN_OVERLAP, DISTRIBUTE_FEW, MISSING_SECTION, NEW_PIECE, NOT_SAME, PIECE_GAP, SECTION_FOLLOW, SIZE_FAIL, SUB, TAKEN_SECTION,
  alignPieces, applySections, distributePieces, dropPiece, movePiece, pieceItems, pieceMin, resizePiece, roomOf, sameSize, scalePieces, sectionMin, sectionsOf, tooClose, wrapCard,
} from "@/components/grid/sections";
import { TEXT_ALIGNS, TEXT_SIZES, TEXT_SIZE_VARS, freshFrame, type FrameKind } from "@/components/frame/registry";
import { TEXT_LINE, pieceRect, textLines } from "@/components/report/layout";
import { onPage, sizeAt, type GridItem } from "@/lib/calc";
import { initialState, reducer } from "@/lib/store";
import type { Card, Dashboard, Layout, Section } from "@/lib/types";

const server = readFileSync(join(__dirname, "..", "..", "core", "dash_store.py"), "utf8");
const L = (x: number, y: number, w: number, h: number): Layout => ({ x, y, w, h });
const VIEW = { type: "", unit: "day", compare: { mode: "span" }, better: "up" } as Card["view"];
const card = (id: string, layout: Layout, more: Partial<Card> = {}) =>
  ({ id, kind: "kpi", title: id, layout, colors: { series: {}, parts: {}, order: [], parts_order: [] }, view: VIEW, source: {}, data: {}, ...more }) as Card;
const sec = (id: string, layout: Layout, more: Partial<Section> = {}): Section => ({ id, title: "", layout, follow: true, view: {}, ...more });
const item = (id: string, layout: Layout, minW = 16, minH = 8): GridItem => ({ id, layout, minW, minH });
const ROOM = { cols: 96, rows: 32 };  // 6 × 4칸 섹션

describe("한 벌: 화면 sections.ts와 서버 dash_store.py", () => {
  it("SUB·PIECE_GAP이 서버 SECTION_SUB·SECTION_GAP과 같고 follow 처음 값은 켜짐, 새 조각은 2칸", () => {
    const found = server.match(/^SECTION_SUB = \((\d+), (\d+)\)/m);
    expect(found).not.toBeNull();
    expect([+found![1], +found![2]]).toEqual([SUB.x, SUB.y]);
    expect([SUB.x, SUB.y]).toEqual([16, 8]);
    expect(+server.match(/^SECTION_GAP = (\d+)/m)![1]).toBe(PIECE_GAP);
    expect(/^SECTION_FOLLOW = True/m.test(server)).toBe(true);
    expect(SECTION_FOLLOW).toBe(true);
    expect(NEW_PIECE).toBe(2);
  });
  it("roomOf는 쪽 격자 칸 × 눈금", () => {
    expect(roomOf(sec("s", L(0, 0, 6, 4)))).toEqual(ROOM);
  });
});

describe("tooClose: 겹침과 1눈금 간격", () => {
  it("맞닿음·모서리 맞닿음·겹침은 가깝고, 한 축으로 1눈금 띄우면 괜찮다", () => {
    const a = L(0, 0, 16, 8);
    expect(tooClose(a, L(10, 4, 16, 8))).toBe(true);
    expect(tooClose(a, L(16, 0, 16, 8))).toBe(true);
    expect(tooClose(a, L(16, 8, 16, 8))).toBe(true);
    expect(tooClose(a, L(17, 0, 16, 8))).toBe(false);
    expect(tooClose(a, L(0, 9, 16, 8))).toBe(false);
    expect(tooClose(a, L(17, 8, 16, 8))).toBe(false);
  });
});

describe("sectionsOf·wrapCard: 옛 카드는 섹션 하나를 꽉 채운 조각", () => {
  it("섹션이 없는 카드를 그 카드 id의 섹션으로 옮긴다(쪽은 섹션으로)", () => {
    const old = card("c1", L(2, 3, 6, 5), { view: { ...VIEW, page: "둘째" } });
    const got = sectionsOf({ cards: [old] });
    expect(got.sections).toEqual([{ id: "c1", title: "", layout: L(2, 3, 6, 5), follow: true, view: { page: "둘째" } }]);
    expect(got.cards[0].section).toBe("c1");
    expect(got.cards[0].layout).toEqual(L(0, 0, 96, 40));
    expect(got.cards[0].view.page).toBeUndefined();
  });
  it("이미 섹션에 있는 조각과 섹션은 그대로, 옛 카드는 뒤에 섹션을 더한다", () => {
    const s1 = sec("s1", L(0, 0, 6, 4)), a = card("a", L(0, 0, 16, 8), { section: "s1" }), b = card("b", L(6, 0, 6, 4));
    const got = sectionsOf({ sections: [s1], cards: [b, a] });
    expect(got.sections.map(s => s.id)).toEqual(["s1", "b"]);
    expect(got.cards.map(c => [c.id, c.section])).toEqual([["b", "b"], ["a", "s1"]]);
    expect(got.cards[1]).toEqual(a);
  });
  it("없는 섹션의 조각·겹치는 섹션 id는 던진다", () => {
    expect(() => sectionsOf({ sections: [], cards: [card("a", L(0, 0, 16, 8), { section: "없음" })] })).toThrow(MISSING_SECTION);
    expect(() => sectionsOf({ sections: [sec("c1", L(0, 0, 6, 4))], cards: [card("c1", L(6, 0, 6, 4))] })).toThrow(TAKEN_SECTION);
  });
  it("wrapCard: 지금 쪽 섹션들의 맨 아래에 카드 크기의 새 섹션, 카드는 꽉 채움", () => {
    const sections = [sec("s1", L(0, 0, 12, 5), { view: { page: "첫" } }), sec("s2", L(0, 0, 12, 9), { view: { page: "둘째" } })];
    const got = wrapCard(card("n1", L(0, 0, 6, 8)), sections, ["첫", "둘째"], "첫");
    expect(got.section).toEqual({ id: "n1", title: "", layout: L(0, 5, 6, 8), follow: true, view: { page: "첫" } });
    expect(got.card.section).toBe("n1");
    expect(got.card.layout).toEqual(L(0, 0, 96, 64));
    expect(wrapCard(card("n2", L(0, 0, 4, 4)), [], [], "").section.view).toEqual({});
  });
});

describe("최소 크기", () => {
  it("pieceMin: 옛 최소 크기(칸)의 반을 눈금으로, 틀 요소는 더 작게", () => {
    expect(pieceMin(card("a", L(0, 0, 16, 8)))).toEqual({ w: 16, h: 8 });
    expect(pieceMin({ kind: "button", view: VIEW } as Card)).toEqual({ w: 8, h: 4 });
  });
  it("pieceItems는 자리와 최소 크기", () => {
    expect(pieceItems([card("a", L(1, 2, 16, 8), { section: "s" })])).toEqual([{ id: "a", layout: L(1, 2, 16, 8), minW: 16, minH: 8 }]);
  });
  it("sectionMin(follow 끔): 조각이 다 들어가는 크기까지, 빈 섹션은 1칸", () => {
    const s = sec("s", L(0, 0, 6, 4), { follow: false });
    expect(sectionMin(s, [card("a", L(0, 0, 40, 12), { section: "s" }), card("b", L(41, 13, 9, 6), { section: "s" })])).toEqual({ w: 4, h: 3 });
    expect(sectionMin(s, [])).toEqual({ w: 1, h: 1 });
  });
  it("sectionMin(follow 켬): 같이 줄어든 조각이 최소 크기와 1눈금 간격을 지키는 크기까지", () => {
    const s = sec("s", L(0, 0, 6, 4));
    expect(sectionMin(s, [card("a", L(0, 0, 96, 32), { section: "s" })])).toEqual({ w: 1, h: 1 });
    expect(sectionMin(s, [card("a", L(0, 0, 47, 32), { section: "s" }), card("b", L(48, 0, 48, 32), { section: "s" })])).toEqual({ w: 4, h: 1 });
  });
});

describe("섹션 크기 바꾸기", () => {
  it("scalePieces: 가장자리를 같은 비율로 옮겨 반올림", () => {
    const a = card("a", L(0, 0, 47, 32)), b = card("b", L(48, 0, 48, 32));
    expect(scalePieces([a, b], L(0, 0, 6, 4), L(0, 0, 4, 2))).toEqual({ a: L(0, 0, 31, 16), b: L(32, 0, 32, 16) });
    expect(scalePieces([card("c", L(5, 0, 10, 8))], L(0, 0, 6, 4), L(0, 0, 4, 4))).toEqual({ c: L(3, 0, 7, 8) });
  });
  it("applySections: follow 켠 섹션만 크기가 바뀌면 조각도 같이, 자리만 옮긴 섹션은 그대로", () => {
    const on = sec("on", L(0, 0, 6, 4)), off = sec("off", L(6, 0, 6, 4), { follow: false }), moved = sec("mv", L(0, 4, 6, 4));
    const cards = [card("a", L(0, 0, 96, 32), { section: "on" }), card("b", L(0, 0, 16, 8), { section: "off" }), card("c", L(0, 0, 16, 8), { section: "mv" })];
    const got = applySections(cards, [on, off, moved], { on: L(0, 0, 3, 2), off: L(6, 0, 3, 2), mv: L(0, 9, 6, 4) });
    expect(got.sections.map(s => s.layout)).toEqual([L(0, 0, 3, 2), L(6, 0, 3, 2), L(0, 9, 6, 4)]);
    expect(got.cards.map(c => c.layout)).toEqual([L(0, 0, 48, 16), L(0, 0, 16, 8), L(0, 0, 16, 8)]);
  });
});

describe("조각 끌기·크기(1눈금 간격, 안내선)", () => {
  const items = () => [item("a", L(0, 0, 16, 8)), item("b", L(40, 0, 16, 8))];
  it("붙을 곳이 없으면 그 자리, 안내선 없음", () => {
    expect(movePiece(items(), "a", { x: 60, y: 20 }, ROOM)).toEqual({ layouts: { a: L(60, 20, 16, 8), b: L(40, 0, 16, 8) }, lines: [] });
  });
  it("가까운 가장자리·가운데에 붙고 맞는 선을 차례대로 돌려준다", () => {
    expect(movePiece(items(), "a", { x: 60, y: 1 }, ROOM)).toEqual({
      layouts: { a: L(60, 0, 16, 8), b: L(40, 0, 16, 8) }, lines: [{ axis: "y", at: 0 }, { axis: "y", at: 8 }, { axis: "y", at: 4 }],
    });
    expect(movePiece(items(), "a", { x: 41, y: 20 }, ROOM)!.lines).toEqual([{ axis: "x", at: 40 }, { axis: "x", at: 56 }, { axis: "x", at: 48 }]);
  });
  it("붙이면 맞닿는 곳은 붙이지 않고, 1눈금 띄운 자리는 된다. 겹치면 null", () => {
    expect(movePiece(items(), "a", { x: 57, y: 0 }, ROOM)!.layouts.a).toEqual(L(57, 0, 16, 8));
    expect(movePiece(items(), "a", { x: 55, y: 0 }, ROOM)).toBeNull();
  });
  it("섹션 밖은 안으로 당긴다", () => {
    expect(movePiece(items(), "a", { x: 90, y: 40 }, ROOM)!.layouts.a).toEqual(L(80, 24, 16, 8));
  });
  it("resizePiece: 최소·섹션 끝으로 당기고, 가장자리가 붙으며 안내선을 준다. 겹치거나 붙으면 null", () => {
    expect(resizePiece(items(), "a", 3, 8, ROOM)).toEqual({ layouts: { a: L(0, 0, 16, 8), b: L(40, 0, 16, 8) }, lines: [{ axis: "y", at: 0 }, { axis: "y", at: 8 }, { axis: "y", at: 4 }] });
    expect(resizePiece(items(), "a", 16, 50, ROOM)).toEqual({ layouts: { a: L(0, 0, 16, 32), b: L(40, 0, 16, 8) }, lines: [{ axis: "y", at: 0 }] });
    expect(resizePiece(items(), "a", 38, 8, ROOM)!.layouts.a).toEqual(L(0, 0, 38, 8));
    expect(resizePiece(items(), "a", 41, 8, ROOM)).toBeNull();
    expect(resizePiece([item("a", L(0, 10, 16, 8)), item("b", L(40, 0, 16, 8))], "a", 55, 8, ROOM)).toEqual({ layouts: { a: L(0, 10, 56, 8), b: L(40, 0, 16, 8) }, lines: [{ axis: "x", at: 56 }] });
  });
});

describe("다른 섹션으로 옮기기", () => {
  it("dropPiece: 크기는 섹션에 맞춰 줄이고, 1눈금 띄운 빈 자리에 놓는다", () => {
    expect(dropPiece([], item("a", L(0, 0, 48, 24)), { x: 0, y: 0 }, { cols: 32, rows: 16 })).toEqual(L(0, 0, 32, 16));
    expect(dropPiece([item("b", L(0, 0, 16, 8))], item("a", L(5, 5, 16, 8)), { x: 17, y: 0 }, { cols: 64, rows: 16 })).toEqual(L(17, 0, 16, 8));
  });
  it("최소 크기가 안 들어가거나 자리가 비지 않으면 null", () => {
    expect(dropPiece([], item("a", L(0, 0, 40, 8), 20, 8), { x: 0, y: 0 }, { cols: 16, rows: 8 })).toBeNull();
    expect(dropPiece([item("b", L(0, 0, 16, 8))], item("a", L(0, 0, 16, 8)), { x: 10, y: 0 }, { cols: 64, rows: 16 })).toBeNull();
  });
});

describe("맞춤·같은 간격·같은 크기", () => {
  const items = () => [item("a", L(0, 0, 16, 8)), item("b", L(40, 12, 20, 8)), item("c", L(80, 4, 12, 8))];
  it("왼쪽·오른쪽·가로 가운데", () => {
    expect(alignPieces(items(), ["a", "b"], "left").b).toEqual(L(0, 12, 20, 8));
    expect(alignPieces(items(), ["a", "b"], "right").a).toEqual(L(44, 0, 16, 8));
    const mid = alignPieces(items(), ["a", "b"], "hcenter");
    expect([mid.a.x, mid.b.x]).toEqual([22, 20]);
    expect(mid.c).toEqual(L(80, 4, 12, 8));
  });
  it("위·아래·세로 가운데", () => {
    expect(alignPieces(items(), ["b", "c"], "top").b).toEqual(L(40, 4, 20, 8));
    expect(alignPieces(items(), ["b", "c"], "bottom").c).toEqual(L(80, 12, 12, 8));
    const mid = alignPieces(items(), ["b", "c"], "vcenter");
    expect([mid.b.y, mid.c.y]).toEqual([8, 8]);
  });
  it("겹치거나 붙거나 하나뿐이거나 다른 섹션의 조각이면 던진다", () => {
    expect(() => alignPieces([item("a", L(0, 0, 16, 8)), item("b", L(10, 12, 16, 8))], ["a", "b"], "top")).toThrow(ALIGN_OVERLAP);
    expect(() => alignPieces([item("a", L(0, 0, 16, 8)), item("b", L(16, 12, 16, 8))], ["a", "b"], "top")).toThrow(ALIGN_OVERLAP);
    expect(() => alignPieces(items(), ["a"], "left")).toThrow(ALIGN_FEW);
    expect(() => alignPieces(items(), ["a", "zz"], "left")).toThrow(NOT_SAME);
  });
  it("distributePieces: 양 끝은 두고 가운데를 같은 간격으로(남는 눈금은 앞 간격부터)", () => {
    const three = (cx: number) => [item("a", L(0, 0, 8, 8)), item("b", L(10, 10, 8, 8)), item("c", L(cx, 0, 8, 8))];
    expect(distributePieces(three(60), ["c", "a", "b"], "x").b).toEqual(L(30, 10, 8, 8));
    expect(distributePieces(three(61), ["a", "b", "c"], "x").b).toEqual(L(31, 10, 8, 8));
    expect(() => distributePieces(three(60), ["a", "b"], "x")).toThrow(DISTRIBUTE_FEW);
    expect(() => distributePieces([item("a", L(0, 0, 20, 8)), item("b", L(10, 10, 20, 8)), item("c", L(24, 0, 8, 8))], ["a", "b", "c"], "x")).toThrow(ALIGN_OVERLAP);
    expect(() => distributePieces([item("a", L(0, 0, 8, 8)), item("b", L(17, 0, 8, 8)), item("c", L(9, 0, 8, 8))], ["a", "b", "c"], "x")).toThrow(ALIGN_OVERLAP);
  });
  it("sameSize: 처음 고른 조각의 너비로, 밖·겹침·붙음·최소 밑이면 던진다", () => {
    expect(sameSize(items(), ["b", "a"], "w", ROOM).a).toEqual(L(0, 0, 20, 8));
    expect(() => sameSize([item("a", L(0, 0, 16, 8)), item("b", L(20, 0, 30, 8))], ["b", "a"], "w", ROOM)).toThrow(SIZE_FAIL);
    expect(() => sameSize([item("a", L(0, 0, 16, 8)), item("b", L(20, 0, 20, 8))], ["b", "a"], "w", ROOM)).toThrow(SIZE_FAIL);
    expect(() => sameSize([item("a", L(80, 0, 16, 8)), item("b", L(0, 12, 30, 8))], ["b", "a"], "w", ROOM)).toThrow(SIZE_FAIL);
    expect(() => sameSize([item("a", L(0, 0, 16, 8)), item("t", L(40, 16, 8, 4), 8, 4)], ["t", "a"], "w", ROOM)).toThrow(SIZE_FAIL);
  });
});

describe("보고서 그림의 조각 사각형", () => {
  it("pieceRect: 섹션 사각형을 눈금으로 나눠, 이름 줄 아래에", () => {
    const r = { x: 10, y: 20, w: 480, h: 160 };
    expect(pieceRect(r, L(0, 0, 6, 4), L(24, 8, 48, 16), 0)).toEqual({ x: 130, y: 60, w: 240, h: 80 });
    expect(pieceRect(r, L(0, 0, 6, 4), L(24, 8, 48, 16), 32)).toEqual({ x: 130, y: 84, w: 240, h: 64 });
  });
  it("textLines: 칸 높이에 들어가는 앞줄만(줄 높이 = 글자 크기 × 1.5)", () => {
    expect(TEXT_LINE).toBe(1.5);
    expect(textLines("a\nb\nc", 16, 60, 6)).toEqual(["a", "b"]);
    expect(textLines("a\nb", 16, 200, 6)).toEqual(["a", "b"]);
    expect(textLines("a", 16, 10, 6)).toEqual([]);
    expect(textLines("", 16, 60, 6)).toEqual([""]);
  });
});

describe("글 조각(꾸밈은 글자 크기·정렬만)", () => {
  it("크기·정렬 키가 서버 FRAME_TEXT_SIZES·FRAME_TEXT_ALIGNS와 같은 차례", () => {
    const words = (name: string) => [...server.match(new RegExp(`^${name} = \\(([^)]*)\\)`, "m"))![1].matchAll(/"(\w+)"/g)].map(m => m[1]);
    expect(Object.keys(TEXT_SIZES)).toEqual(words("FRAME_TEXT_SIZES"));
    expect(Object.keys(TEXT_ALIGNS)).toEqual(words("FRAME_TEXT_ALIGNS"));
    expect(Object.values(TEXT_SIZES)).toEqual(["작게", "보통", "크게"]);
    expect(Object.values(TEXT_ALIGNS)).toEqual(["왼쪽", "가운데", "오른쪽"]);
  });
  it("글자 크기는 앱의 글자 크기 변수, 막 놓은 글은 비고 보통·왼쪽", () => {
    expect(TEXT_SIZE_VARS).toEqual({ sm: "--fs-sm", md: "--fs-md", lg: "--fs-lg" });
    expect(freshFrame("text" as FrameKind)).toEqual({ text: "", size: "md", align: "left" });
  });
});

describe("고친 것", () => {
  it("onPage는 섹션도 쪽으로 거른다", () => {
    expect(onPage([sec("a", L(0, 0, 2, 2), { view: { page: "둘째" } }), sec("b", L(0, 0, 2, 2))], ["첫", "둘째"], "첫").map(s => s.id)).toEqual(["b"]);
  });
  it("sizeAt은 max까지 키운다(섹션 눈금)", () => {
    expect(sizeAt(2000, 1000, { left: 0, top: 0, col: 10, row: 10, gap: 0 }, { w: 192, h: 96 })).toEqual({ w: 192, h: 96 });
  });
  it("cards 액션에 sections를 주면 섹션 목록도 바뀐다", () => {
    const dash = { id: "d", name: "n", cards: [], sections: [] } as unknown as Dashboard;
    const next = reducer({ ...initialState, dash }, { type: "cards", cards: [], sections: [sec("s1", L(0, 0, 2, 2))] });
    expect(next.dash!.sections).toEqual([sec("s1", L(0, 0, 2, 2))]);
  });
});
