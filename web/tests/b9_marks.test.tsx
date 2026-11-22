// B9 줄 표시(plans/dash-blocks.md "B9 세부 명세"): 목록 값 고치기·세기(applyMark·markTotal·markBars), 줄 표시 입력(MarkInputs)·목록에 붙은 입력(List),
// 표시 세기 요소·할 일 등록(registry·links), CSS. 데이터는 지어낸 것(매장·주문).
// it.fails: 기존 파일을 고쳐야 통과하는 시험. 주석에 적은 항목이 끝나면 그 항목의 strict-coder가 .fails를 지운다.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ComponentType } from "react";
import type { Card, CardData, ListView, MarkField, MarkView } from "@/lib/types";
import { MARK_EMPTY, MARK_EMPTY_COLOR, applyMark, markBars, markTotal } from "@/lib/marks";
import { List, MarkInputs } from "@/components/cards/svg/List";
import { FRAME_ACTIONS, FRAME_KINDS, frameError, freshFrame, runAction, type ActionHost } from "@/components/frame/registry";
import { isSender } from "@/lib/links";

const src = (p: string) => readFileSync(join(__dirname, "..", p), "utf8");
const MERGE = ["sum", "count"];
const THEME = { card: "#fff", head: "#eee", ink: "#000", text: "#555", line: "#ddd", err: "#c00", ok: "#070", series: ["#111", "#222"], hi: "#333" };
const FIELDS: MarkField[] = [{ id: "f1", name: "단계", kind: "choice", choices: ["할 일", "끝"] }, { id: "f2", name: "메모", kind: "text", choices: [] }];
const COLUMNS = [{ name: "store", kind: "group" }, { name: "합계 (amt)", kind: "value", agg: "sum" }, { name: "단계", kind: "mark", field: "f1", mark: "choice" }, { name: "메모", kind: "mark", field: "f2", mark: "text" }];
/** 열쇠 글자(서버 dash_marks.key_expr, 열쇠 ["store"]). 화면은 풀지 않고 그대로 쓴다 */
const KA = '{"0":"A"}', KB = '{"0":"B"}', KC = '{"0":"C"}';
/** 목록 값 세 줄(dash_calc.list_data가 표시 칸이 있을 때 주는 꼴): A만 "끝", B에 메모 */
const DATA = { values: { columns: COLUMNS, data: [["A", "B", "C"], [30, 55, 1], ["끝", null, null], [null, "가", null]], more: false, keys: [KA, KB, KC],
  row_marks: { total: 3, counts: { f1: { "끝": 1, "": 2 } } } } } as unknown as CardData;
type Shown = { data: unknown[][]; row_marks: { total: number; counts: Record<string, Record<string, number>> } };
const shown = (d: CardData) => d.values as unknown as Shown;
const MARKS: MarkView = { keys: ["store"], fields: FIELDS, edit: "cell", clash: "keep" };
const LIST: ListView = { parts: [{ kind: "text", column: "store", swap: false, format: "number", rules: [], basis: "max", side: "start" }, { kind: "chip", column: "단계", swap: false, format: "number", rules: [], basis: "max", side: "end" }],
  pick: "none", key: "", swaps: [], report: "screen" };
const card = (marks: MarkView | undefined): Card => ({ id: "l1", kind: "list", title: "목록", layout: { x: 0, y: 0, w: 4, h: 4 },
  colors: { series: {}, parts: {}, order: [], parts_order: [] }, view: { type: "", unit: "day", compare: { mode: "span" }, better: "up", group_cols: ["store"], list: LIST, ...(marks ? { row_marks: marks } : {}) } as Card["view"],
  source: { body: { series: [{ y: "amt", agg: "sum" }] } }, data: DATA } as unknown as Card);
const draw = (c: Card, more: Record<string, unknown> = {}) => {
  const L = List as unknown as ComponentType<Record<string, unknown>>;
  return renderToStaticMarkup(<L card={c} colors={THEME} range={undefined} merge={MERGE} {...more} />);
};

describe("줄 표시 — applyMark", () => {
  it("고친 줄의 칸 값만 바꾸고 고르기 칸 수를 옮긴다(원본은 그대로)", () => {
    const got = shown(applyMark(DATA, KB, { f1: "끝", f2: "가" }));
    expect(got.data[2]).toEqual(["끝", "끝", null]);
    expect(got.data[3]).toEqual([null, "가", null]);
    expect(got.data[0]).toEqual(["A", "B", "C"]);
    expect(got.row_marks).toEqual({ total: 3, counts: { f1: { "끝": 2, "": 1 } } });
    expect(shown(DATA).data[2]).toEqual(["끝", null, null]);
    expect(shown(DATA).row_marks.counts).toEqual({ f1: { "끝": 1, "": 2 } });
  });
  it("값을 비우면 표시 없음으로 세고 0이 된 값은 뺀다", () => {
    expect(shown(applyMark(DATA, KA, { f1: null })).row_marks.counts).toEqual({ f1: { "": 3 } });
  });
  it("목록에 없는 줄이면 던진다", () => {
    expect(() => applyMark(DATA, "A", { f1: "끝" })).toThrow("고친 줄이 목록에 없어요.");  // 열쇠 글자가 아니라 값만 주면 없는 줄
    expect(() => applyMark({ values: {} } as CardData, KA, { f1: "끝" })).toThrow("이 목록에는 아직 구한 값이 없어요.");
  });
});

describe("줄 표시 — 세기", () => {
  it("markTotal: 고른 값들의 줄 수, 값이 없으면 센 줄 전부, 표시 수가 없으면 null", () => {
    expect(markTotal(DATA, "f1", ["끝"])).toBe(1);
    expect(markTotal(DATA, "f1", [""])).toBe(2);
    expect(markTotal(DATA, "f1", ["끝", ""])).toBe(3);
    expect(markTotal(DATA, "f1", [])).toBe(3);
    expect(markTotal(DATA, "f9", ["끝"])).toBe(0);
    expect(markTotal({ values: {} } as CardData, "f1", [])).toBeNull();
    expect(markTotal(undefined, "f1", [])).toBeNull();
  });
  it("markBars: 고를 값 차례(0줄도) + 표시 없음(있을 때만), 몫은 센 줄 전부 대비", () => {
    expect(markBars(DATA, FIELDS[0])).toEqual([
      { value: "할 일", label: "할 일", n: 0, ratio: 0, color: "var(--c1)" },
      { value: "끝", label: "끝", n: 1, ratio: 1 / 3, color: "var(--c2)" },
      { value: "", label: MARK_EMPTY, n: 2, ratio: 2 / 3, color: MARK_EMPTY_COLOR },
    ]);
    const all = applyMark(applyMark(DATA, KB, { f1: "할 일" }), KC, { f1: "끝" });
    expect(markBars(all, FIELDS[0]).map(b => [b.value, b.n])).toEqual([["할 일", 1], ["끝", 2]]);
    expect(markBars(undefined, FIELDS[0])).toEqual([]);
  });
});

describe("줄 표시 — 입력", () => {
  it("MarkInputs: 고르기 칸은 값 없음 + 고를 값(지금 값이 골라짐), 글 칸은 지금 값", () => {
    const html = renderToStaticMarkup(<MarkInputs fields={FIELDS} values={{ f1: "끝", f2: "가" }} label="목록 A" onChange={() => {}} />);
    expect(html).toContain('class="lmarks"');
    expect(html).toContain('aria-label="단계"');
    expect(html).toContain(`<option value="">${MARK_EMPTY}</option>`);
    expect(html).toContain('<option value="끝" selected="">끝</option>');
    expect(html).toContain('aria-label="메모"');
    expect(html).toContain('value="가"');
  });
  // 마지막 항목: B9 List (수정)
  it("List: 줄 끝 칸이면 onMark가 있을 때 줄마다 입력, 없으면 없음. 표시 칸은 줄 부분 컬럼으로 보인다", () => {
    const html = draw(card(MARKS), { onMark: () => {} });
    expect(html.match(/class="lmarks"/g)?.length).toBe(3);
    expect(html.match(/class="lmarked"/g)?.length).toBe(3);
    expect(html).toContain(">끝</span>");
    expect(draw(card(MARKS))).not.toContain("lmarks");
    expect(draw(card(undefined), { onMark: () => {} })).not.toContain("lmarks");
  });
  // 마지막 항목: B9 List (수정)
  it("List: 누르면 뜨는 칸이면 줄마다 ✎ 단추만(펼치기 전에는 입력 없음)", () => {
    const html = draw(card({ ...MARKS, edit: "pop" }), { onMark: () => {} });
    expect(html.match(/class="ib lmarkbtn"/g)?.length).toBe(3);
    expect(html).not.toContain('class="lmarks"');
  });
});

describe("줄 표시 — 등록", () => {
  // 마지막 항목: B9 registry (수정)
  it("표시 세기 요소와 할 일 둘(표시 저장·가져오기)", () => {
    const kinds = FRAME_KINDS as unknown as Record<string, { name: string; group: string }>;
    expect(kinds.mark_count).toEqual({ name: "표시 세기", group: "조작", help: "목록 줄의 표시를 세어 숫자나 진척 칸으로 보여요", w: 3, h: 2 });
    const actions = FRAME_ACTIONS as unknown as Record<string, { name: string; arg: string }>;
    expect(actions.marks_save).toEqual({ name: "표시를 파일로 저장", arg: "card" });
    expect(actions.marks_load).toEqual({ name: "표시를 파일에서 가져오기", arg: "card" });
    expect(freshFrame("mark_count" as never)).toEqual({ label: "", list: "", field: "", values: [], look: "number", ambient: false });
    expect(frameError("mark_count" as never, {})).toBe("셀 목록을 고르세요.");
    expect(frameError("mark_count" as never, { list: "l1" } as never)).toBe("표시 칸을 고르세요.");
    expect(frameError("mark_count" as never, { list: "l1", field: "f1" } as never)).toBe("");
    expect(frameError("button", { label: "가", action: "marks_save" as never })).toBe("표시를 주고받을 목록을 고르세요.");
    const done: string[] = [];
    const host = { cardIds: ["l1"], saveMarks: (id: string) => done.push("save " + id), loadMarks: (id: string) => done.push("load " + id) } as unknown as ActionHost;
    runAction({ action: "marks_save" as never, arg: "l1" }, host);
    runAction({ action: "marks_load" as never, arg: "l1" }, host);
    expect(done).toEqual(["save l1", "load l1"]);
    expect(() => runAction({ action: "marks_load" as never, arg: "zz" }, host)).toThrow("표시를 주고받을 목록이 없어요.");
  });
  // 마지막 항목: B9 links (수정)
  it("표시 세기 요소는 링크를 보낸다", () => {
    expect(isSender("mark_count")).toBe(true);
  });
  // 마지막 항목: B9 CSS (수정)
  it("CSS: 줄 끝 칸·누르면 뜨는 칸·표시 세기, 모서리는 변수만", () => {
    const css = src("styles/dash_edit.css");
    for (const sel of [".lmarks {", ".lmarkpop {", ".fr-mcount {", ".fr-mbar-track {", ".fr-mbar-legend {"]) expect(css).toContain(sel);
    const b9 = css.slice(css.indexOf("/* B9 줄 표시"));
    expect(b9).not.toMatch(/border-radius:\s*[\d.]/);
  });
});
