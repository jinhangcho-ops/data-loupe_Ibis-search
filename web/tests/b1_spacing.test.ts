// B1 간격·모양(plans/dash-blocks.md "B1 세부 명세"): 대시보드의 격자 간격·좌우 여백·카드 안 여백·모서리·작은 글자·조작 칸 높이가
// 참조 값(dashboard/src/report.css) 한 벌로 dash_edit.css의 #dash 규칙 한 곳에만 있고, 화면 끌기(metrics)와 보고서 그림(reportBox)이 그 값을 읽는지 본다.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as calc from "@/lib/calc";
import { metrics } from "@/components/grid/useDrag";
import { REPORT, REPORT_REM, REPORT_TOKENS, reportBox, reportLayout } from "@/components/report/layout";

const src = (p: string) => readFileSync(join(__dirname, "..", p), "utf8");
const line = (css: string, head: string) => css.split("\n").find(l => l.startsWith(head)) ?? "";
const vars = (rule: string) => Object.fromEntries([...rule.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(m => [m[1], m[2].trim()]));

// 참조 report.css: 69줄 --rd-gap .75rem·좌우 1.25rem, 184줄 카드 padding 1rem 1.125rem·모서리 1.125rem, 84·86·91·93줄 작은 글자 .6875rem
const B1 = { "--page-x": "1.25rem", "--grid-gap": ".75rem", "--grid-row": "2.5rem", "--card-py": "1rem", "--card-px": "1.125rem", "--r-board": "1.125rem", "--fs-xs": ".6875rem" };
const BOX = { col: 100, head: 56, gap: 12, row: 40, pad: 20, radius: 18, padX: 18, padY: 16, small: 11 };

describe("B1 값 한 벌(CSS)", () => {
  const edit = src("styles/dash_edit.css");
  it("dash_edit.css의 #dash 규칙에 B1 값이 모두 있다", () => {
    expect(vars(line(edit, "#dash {"))).toEqual(B1);
  });
  it("격자·카드·화면 여백 규칙은 숫자 대신 #dash의 변수를 쓴다", () => {
    const grid = line(edit, ".board.grid {"), main = line(edit, ".d-main {"), card = line(edit, ".board.grid .bcard {");
    expect(grid).toContain("grid-auto-rows:var(--grid-row);");
    expect(grid).toContain("gap:var(--grid-gap);");
    expect(grid).toContain("padding:.375rem 0 0;");
    expect(main).toContain("gap:var(--grid-gap);");
    expect(main).toContain("padding:0 var(--page-x) 1.5rem;");
    expect(card).toContain("padding:var(--card-py) var(--card-px);");
    expect(card).toContain("border-radius:var(--r-board);");
    expect(line(edit, ".board.grid .bcard.sel {")).toContain("padding-left:calc(var(--card-px) - .1875rem);");
  });
  it("도구 줄·필터 줄·쪽 줄·오류 줄의 좌우 여백은 --page-x", () => {
    expect(line(edit, "#dash :is(.tool, .big, .board-ctl), #dash > [id^=\"err-\"] {")).toContain("padding-left:var(--page-x); padding-right:var(--page-x);");
    expect(line(edit, "#dash .d-alerts {")).toContain("margin-left:var(--page-x); margin-right:var(--page-x);");
  });
  it("위쪽 줄·필터 줄의 조작 칸 높이는 --h-sm(1.75rem)", () => {
    expect(line(edit, "#dash .d-tool > :is(button, .btn),")).toContain("height:var(--h-sm); padding-top:0; padding-bottom:0;");
    expect(src("styles/common.css")).toContain("--h-sm:1.75rem;");
  });
  it("[대시보드만 보기] 위쪽 줄은 높이 3.25rem, 좌우 여백은 #dash 규칙이 준다", () => {
    const bar = line(src("styles/dashboard.css"), "body.dash-only .d-tool {");
    expect(bar).toContain("min-height:3.25rem;");
    expect(bar).toContain("padding-top:.375rem; padding-bottom:.375rem;");
    expect(bar).not.toContain("1.5rem");
  });
});

describe("값이 한 곳에서만 나온다(CSS와 TS가 어긋나지 않는다)", () => {
  it("보고서가 읽는 변수 이름은 모두 #dash 규칙에 있고 rem이다", () => {
    const own = vars(line(src("styles/dash_edit.css"), "#dash {"));
    for (const name of Object.values(REPORT_TOKENS)) expect(own[name], name).toMatch(/^\d*\.?\d+rem$/);
  });
  it("TS에 px로 박힌 간격·줄 높이·보고서 간격이 없다", () => {
    expect("GRID_GAP" in calc).toBe(false);
    expect("GRID_ROW" in calc).toBe(false);
    expect("gap" in REPORT).toBe(false);
    expect("pad" in REPORT).toBe(false);
    expect(src("components/report/picture.ts")).not.toMatch(/roundRect\([^)]*\b12 \* scale/);
  });
});

describe("reportBox", () => {
  const read = (over: Record<string, string> = {}) => (name: string) => ({ ...B1, ...over } as Record<string, string>)[name] ?? "";
  it("rem 값을 REPORT_REM 배 해 보고서 길이로", () => {
    expect(REPORT_REM).toBe(16);
    expect(reportBox(read())).toEqual(BOX);
  });
  it("앞뒤 공백은 무시한다", () => {
    expect(reportBox(read({ "--grid-gap": " .75rem " })).gap).toBe(12);
  });
  it("비었거나 rem이 아니면 던진다(기본값 없음)", () => {
    expect(() => reportBox(read({ "--grid-gap": "" }))).toThrow("--grid-gap");
    expect(() => reportBox(read({ "--page-x": "20px" }))).toThrow("--page-x");
    expect(() => reportBox(read({ "--r-board": "var(--r-card)" }))).toThrow("--r-board");
  });
});

describe("reportLayout(B1 상자)", () => {
  it("칸 간격 12·줄 높이 40·바깥 여백 20으로 자리를 낸다", () => {
    const lay = reportLayout([{ id: "a", layout: { x: 0, y: 0, w: 4, h: 4 } }, { id: "b", layout: { x: 4, y: 2, w: 8, h: 6 } }], BOX);
    expect(lay.rects.a).toEqual({ x: 20, y: 56, w: 4 * 100 + 3 * 12, h: 4 * 40 + 3 * 12 });
    expect(lay.rects.b.x).toBe(20 + 4 * 112);
    expect(lay.width).toBe(20 * 2 + 12 * 100 + 11 * 12);
    expect(lay.height).toBe(56 + 8 * 52 - 12 + 20);
    expect(reportLayout([], BOX).height).toBe(56 + 52 - 12 + 20);
  });
});

describe("metrics: 끌기 칸 크기는 화면 CSS에서 읽는다", () => {
  afterEach(() => { vi.unstubAllGlobals(); });
  const fake = (width: number) => ({ getBoundingClientRect: () => ({ left: 10, top: 20, width }) }) as unknown as HTMLElement;
  const style = (gap: string, row: string) => vi.stubGlobal("getComputedStyle", () => ({ columnGap: gap, gridAutoRows: row, paddingLeft: "0px", paddingRight: "0px", paddingTop: "0px" }));
  it("보통(1rem 16px): 간격 12px, 줄 40px", () => {
    style("12px", "40px");
    expect(metrics(fake(1200 + 11 * 12))).toEqual({ left: 10, top: 20, col: 112, row: 52, gap: 12 });
  });
  it("아주 크게(1rem 20px): 간격 15px, 줄 50px(예전처럼 40px로 어림하지 않는다)", () => {
    style("15px", "50px");
    expect(metrics(fake(1200 + 11 * 15))).toEqual({ left: 10, top: 20, col: 115, row: 65, gap: 15 });
  });
  it("간격·줄 높이를 읽지 못하면 던진다", () => {
    style("normal", "40px");
    expect(() => metrics(fake(1000))).toThrow();
    style("12px", "auto");
    expect(() => metrics(fake(1000))).toThrow();
  });
});
