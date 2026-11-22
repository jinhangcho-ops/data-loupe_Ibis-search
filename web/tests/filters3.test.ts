// 대시보드 3단계(필터 칸): 필터를 건 값은 화면에만 두고 저장하지 않는다, 필터 줄이 두 곳에서 어떻게 보이는지.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { computedUpdate, plainCtx } from "@/lib/store";

const src = (p: string) => readFileSync(join(__dirname, "..", p), "utf8");
const PICK = [{ column: "지역", in: ["부산"] }];

describe("필터를 건 값", () => {
  it("필터가 걸려 있으면 구한 값을 카드에 저장하지 않고 화면 값으로만", () => {
    expect(computedUpdate("c", { note: "거른 값" }, { filters: PICK })).toEqual({ patch: {}, result: { id: "c", data: { note: "거른 값" } } });
    expect(computedUpdate("c", { note: "전체" }, { filters: [] })).toEqual({ patch: { data: { note: "전체" } }, result: { id: "c" } });
  });
  it("저장할 값을 구할 때는 필터를 뺀다", () => {
    expect(plainCtx({ filters: PICK, as_of: "2026-01-31" })).toEqual({ range: undefined, prev: null, filters: undefined, as_of: "2026-01-31" });
  });
});

describe("필터 줄", () => {
  const bar = src("components/shell/FilterBar.tsx"), shell = src("components/shell/ShellProvider.tsx");
  it("필터가 없으면 보는 곳에는 줄이 없고, 만드는 곳에만 [필터 추가]와 ×", () => {
    expect(bar).toContain("if (!dash || (!filters.length && !build && !pressed.length)) return null;");
    expect(bar).toContain("{build && filters.length < max && (");
    expect(bar).toContain('{build && <button type="button" className="ghost" title="이 필터 칸 빼기"');
  });
  it("연결이 없으면 칸을 잠그고 안내를 보인다", () => {
    expect(bar).toContain("disabled={!conn}");
    expect(bar).toContain("데이터에 연결하면 거를 수 있어요");
  });
  it("값을 고르면 ctx.filters를 바꾸고 모든 카드를 다시 구한다. 다 풀리면 화면 값을 비운다", () => {
    expect(shell).toContain("picked.current.length ? { ...rest, filters: picked.current }");
    expect(shell).toContain('if (cards.every(c => !ctxOf(c.id))) return dispatch({ type: "values", results: cards.map(c => ({ id: c.id })) });');
    expect(shell).toContain("await recomputeRange(cards);");
    expect(shell).toContain("picked.current = []; setPicks([]);");  // 다른 대시보드를 열면 푼다
    expect(shell).toContain("picked.current.length ? { ...span, filters: picked.current } : span");  // 기간을 바꿔도 남는다
  });
  it("행을 직접 읽는 카드는 서버가 걸러 준 조회로 읽는다", () => {
    expect(src("components/cards/SvgCards.tsx")).toContain("filters={ctx?.filters}");
  });
});
