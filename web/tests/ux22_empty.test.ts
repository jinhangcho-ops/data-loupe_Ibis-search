// D1: 카드가 없는 대시보드의 시작 안내는 가운데 칸 안에 있다. 그래야 오른쪽 칸의 [카드 추가] 서랍이 안내 아래로 밀리지 않는다.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const src = (path: string) => readFileSync(fileURLToPath(new URL("../" + path, import.meta.url)), "utf-8");

describe("빈 대시보드: 시작 안내와 카드 추가 서랍이 나란히", () => {
  it("시작 안내(.board-empty)는 .d-main의 가운데 칸에서 격자 바로 위에 있다", () => {
    const page = src("app/dashboard/page.tsx");
    const main = page.indexOf('<div className="d-main">'), empty = page.indexOf('className="board-empty'), grid = page.indexOf("<Grid />"), add = page.indexOf("<AddCardMenu");
    expect(main).toBeGreaterThan(-1);
    expect(empty).toBeGreaterThan(main);
    expect(grid).toBeGreaterThan(empty);
    expect(add).toBeGreaterThan(grid);
    expect(page.slice(main, empty)).toContain("<div>{!n && (");  // 가운데 칸(.d-main > div, 2번 칸)의 첫 자식
  });
  it("서랍은 오른쪽 칸 위에 붙는다", () => {
    const rule = src("styles/dash_edit.css").split("\n").find(line => line.startsWith(".d-main > #d-add {")) ?? "";
    expect(rule).toContain("grid-column:3;");
    expect(rule).toContain("position:sticky; top:0;");
    expect(src("styles/dash_edit.css")).toContain(".d-main { display:grid; grid-template-columns:auto minmax(0, 1fr) auto; align-items:start;");
  });
});
