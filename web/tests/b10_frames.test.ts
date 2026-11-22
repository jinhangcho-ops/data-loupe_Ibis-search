// B10 틀 요소(plans/dash-blocks.md "B10 세부 명세"): 요소 종류·할 일의 한 벌(frame/registry.ts)과 서버(core/dash_store.py)가 같은지,
// 순수 계산(isFrame·freshFrame·frameError·findSpecs·runAction·filterNote), 요소의 최소 크기(1칸), 화면 파일이 요소를 잇는지 본다. 값은 지어낸 것만 쓴다.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { FRAME_ACTIONS, FRAME_GROUPS, FRAME_KINDS, FRAME_PARTS, filterNote, findSpecs, frameError, freshFrame, isFrame, runAction, type ActionHost } from "@/components/frame/registry";
import { gridItems, minHeight } from "@/components/grid/useDrag";
import { autoUnit } from "@/lib/autoviz";
import { BUCKET, GRID_MIN, clampLayout, resizeCard, sizeAt, unitLabel } from "@/lib/calc";
import type { Card } from "@/lib/types";

const src = (p: string) => readFileSync(join(__dirname, "..", p), "utf8");
const server = readFileSync(join(__dirname, "..", "..", "core", "dash_store.py"), "utf8");
const words = (text: string) => [...text.matchAll(/"(\w*)"/g)].map(m => m[1]);
const host = (over: Partial<ActionHost> = {}): ActionHost => ({
  recompute: vi.fn(), setPage: vi.fn(), dashOnly: false, setDashOnly: vi.fn(), setReport: vi.fn(), setZoom: vi.fn(), flipTheme: vi.fn(), pages: ["첫 쪽", "둘째 쪽"], cardIds: ["c1"],
  flipEasy: vi.fn(), clearFilters: vi.fn(), setSettings: vi.fn(), ...over,
});
const frameCard = (kind: string, layout = { x: 0, y: 0, w: 1, h: 1 }) => ({ id: "f1", kind, title: "요소", layout, view: {} }) as unknown as Card;

describe("등록 자리 한 곳: 화면 registry.ts와 서버 dash_store.py", () => {
  it("요소 종류가 서버 FRAME_KINDS와 같다", () => {
    const found = server.match(/^FRAME_KINDS = \(([^)]*)\)/m);
    expect(found).not.toBeNull();
    expect(words(found![1]).sort()).toEqual(Object.keys(FRAME_KINDS).sort());
  });
  it("할 일과 받는 값의 종류가 서버 FRAME_ACTIONS와 같다", () => {
    const found = server.match(/^FRAME_ACTIONS = \{([^}]*)\}/m);
    expect(found).not.toBeNull();
    const pairs = Object.fromEntries([...found![1].matchAll(/"(\w+)": "(\w*)"/g)].map(m => [m[1], m[2]]));
    expect(pairs).toEqual(Object.fromEntries(Object.entries(FRAME_ACTIONS).map(([k, v]) => [k, v.arg])));
  });
  it("위쪽 줄의 칸이 서버 FRAME_PARTS와 같은 차례다", () => {
    expect(words(server.match(/^FRAME_PARTS = \(([^)]*)\)/m)![1])).toEqual(Object.keys(FRAME_PARTS));
  });
  it("요소는 조작·틀 묶음에만 있고(보기는 카드), B3의 요소는 아직 없다", () => {
    expect([...FRAME_GROUPS]).toEqual(["조작", "보기", "틀"]);
    expect(new Set(Object.values(FRAME_KINDS).map(k => k.group))).toEqual(new Set(["조작", "틀"]));
    expect(Object.keys(FRAME_KINDS).sort()).toEqual(["asof_field", "button", "chip_row", "filter_bar", "nav_rail", "page_tabs", "period_pick", "segment", "text", "top_strip", "unit_seg"]);
  });
  it("할 일 목록에 LLM·도움말처럼 앱에 없는 일이 없다", () => {
    expect(Object.keys(FRAME_ACTIONS).sort()).toEqual(["dash_only", "easy", "export", "filters_clear", "page", "refresh", "settings", "theme", "zoom"]);
  });
});

describe("isFrame · freshFrame", () => {
  it("요소 종류만 참", () => {
    for (const k of Object.keys(FRAME_KINDS)) expect(isFrame(k)).toBe(true);
    for (const k of ["chart", "kpi", "blocks", "", "toString"]) expect(isFrame(k)).toBe(false);
  });
  it("막 놓은 설정: 단추 글자는 비어 있고 엠비언트는 꺼져 있다", () => {
    expect(freshFrame("button")).toEqual({ label: "", icon: "", action: "", arg: "", ambient: false });
    expect(freshFrame("nav_rail")).toEqual({ items: [], ambient: false });
    expect(freshFrame("top_strip")).toEqual({ parts: ["name", "period", "asof", "theme"], items: [] });
    for (const k of ["period_pick", "asof_field", "unit_seg", "page_tabs"] as const) expect(freshFrame(k)).toEqual({});
  });
});

describe("frameError", () => {
  const EMPTY = "단추에는 글자나 아이콘 중 하나가 있어야 해요.";
  it("단추: 글자와 아이콘이 모두 비면 이유, 하나라도 있으면 통과", () => {
    expect(frameError("button", freshFrame("button"))).toBe(EMPTY);
    expect(frameError("button", { label: "  ", icon: "", action: "", arg: "" })).toBe(EMPTY);
    expect(frameError("button", { label: "새로 고침", icon: "", action: "refresh", arg: "" })).toBe("");
    expect(frameError("button", { label: "", icon: "clock", action: "", arg: "" })).toBe("");
  });
  it("받는 값이 필요한 할 일인데 비면 이유", () => {
    expect(frameError("button", { label: "가기", icon: "", action: "page", arg: "" })).toBe("이동할 쪽을 고르세요.");
    expect(frameError("button", { label: "크게", icon: "", action: "zoom", arg: "" })).toBe("크게 볼 카드를 고르세요.");
    expect(frameError("button", { label: "가기", icon: "", action: "page", arg: "둘째 쪽" })).toBe("");
  });
  it("줄 안 항목도 같은 규칙(먼저 걸린 항목의 이유 하나)", () => {
    expect(frameError("nav_rail", { items: [], ambient: false })).toBe("");
    expect(frameError("nav_rail", { items: [{ label: "첫 쪽", icon: "", action: "page", arg: "첫 쪽" }, { label: "", icon: "", action: "", arg: "" }] })).toBe(EMPTY);
    expect(frameError("top_strip", { parts: ["name"], items: [{ label: "가기", icon: "", action: "page", arg: "" }] })).toBe("이동할 쪽을 고르세요.");
    for (const k of ["period_pick", "asof_field", "unit_seg", "page_tabs"] as const) expect(frameError(k, {})).toBe("");
  });
});

describe("findSpecs", () => {
  const specs = [{ name: "단추", help: "누르면 고른 일을 해요" }, { name: "KPI", help: "큰 숫자 하나" }, { name: "기준일", help: "기준일을 정해요" }];
  it("빈 글자는 전부, 아니면 이름·풀이에 든 것만(대소문자 무시, 차례 그대로)", () => {
    expect(findSpecs(specs, "  ")).toEqual(specs);
    expect(findSpecs(specs, "kpi").map(s => s.name)).toEqual(["KPI"]);
    expect(findSpecs(specs, "요").map(s => s.name)).toEqual(["단추", "기준일"]);
    expect(findSpecs(specs, "없는 말")).toEqual([]);
  });
});

describe("runAction: 이미 있는 기능만 부른다", () => {
  it("내보내기·최신화·화면 모드·대시보드만 보기", () => {
    const h = host();
    runAction({ action: "export", arg: "" }, h);
    expect(h.setReport).toHaveBeenCalledWith(true);
    runAction({ action: "refresh", arg: "" }, h);
    expect(h.recompute).toHaveBeenCalledTimes(1);
    runAction({ action: "theme", arg: "" }, h);
    expect(h.flipTheme).toHaveBeenCalledTimes(1);
    runAction({ action: "dash_only", arg: "" }, h);
    expect(h.setDashOnly).toHaveBeenCalledWith(true);
    const on = host({ dashOnly: true });
    runAction({ action: "dash_only", arg: "" }, on);
    expect(on.setDashOnly).toHaveBeenCalledWith(false);
  });
  it("쉬운 보기·필터 모두 풀기", () => {
    const h = host();
    runAction({ action: "easy", arg: "" }, h);
    expect(h.flipEasy).toHaveBeenCalledTimes(1);
    runAction({ action: "filters_clear", arg: "" }, h);
    expect(h.clearFilters).toHaveBeenCalledTimes(1);
    for (const fn of [h.recompute, h.setPage, h.setDashOnly, h.setReport, h.setZoom, h.flipTheme]) expect(fn).not.toHaveBeenCalled();
  });
  it("쪽 이동·크게 보기는 있는 쪽·카드만", () => {
    const h = host();
    runAction({ action: "page", arg: "둘째 쪽" }, h);
    expect(h.setPage).toHaveBeenCalledWith("둘째 쪽");
    runAction({ action: "zoom", arg: "c1" }, h);
    expect(h.setZoom).toHaveBeenCalledWith("c1");
    expect(() => runAction({ action: "page", arg: "지운 쪽" }, h)).toThrow("'지운 쪽' 쪽이 없어요.");
    expect(() => runAction({ action: "zoom", arg: "gone" }, h)).toThrow("크게 볼 카드가 없어요.");
    expect(h.setPage).toHaveBeenCalledTimes(1);
    expect(h.setZoom).toHaveBeenCalledTimes(1);
    runAction({ action: "settings", arg: "" }, h);
    expect(h.setSettings).toHaveBeenCalledWith(true);
  });
  it("정하지 않은 일·모르는 일은 던진다(다른 일로 바꿔 하지 않는다)", () => {
    const h = host();
    expect(() => runAction({ action: "", arg: "" }, h)).toThrow("이 단추가 할 일을 아직 정하지 않았어요.");
    expect(() => runAction({ action: "help" as never, arg: "" }, h)).toThrow("모르는 할 일이에요: help");
    for (const fn of [h.recompute, h.setPage, h.setDashOnly, h.setReport, h.setZoom, h.flipTheme, h.flipEasy, h.clearFilters, h.setSettings]) expect(fn).not.toHaveBeenCalled();
  });
});

describe("filterNote: 보고서에는 조작 요소 대신 걸린 것만 글로", () => {
  it("기간·기준일·필터를 차례로, 없는 것은 뺀다", () => {
    expect(filterNote("", "", [], [])).toBe("");
    expect(filterNote(" 2000-01-01 ~ 2000-01-31 ", "", [], [])).toBe("기간 2000-01-01 ~ 2000-01-31");
    expect(filterNote("2000-01-01 ~ 2000-01-31", "2000-02-01", [{ column: "지역", in: ["가", "나"] }, { column: "등급", in: [] }], [])).toBe("기간 2000-01-01 ~ 2000-01-31 · 기준일 2000-02-01 · 지역: 가, 나");
  });
});

// 여기부터는 기존 파일을 고쳐야 통과한다(각 절의 "시험"에 이름이 있다)
describe("요소의 최소 크기는 1칸, 카드는 그대로 2칸", () => {
  it("clampLayout: 최소 너비를 받는다(주지 않으면 예전처럼 GRID_MIN)", () => {
    expect(clampLayout({ x: 0, y: 0, w: 1, h: 1 }, 1, 1)).toEqual({ x: 0, y: 0, w: 1, h: 1 });
    expect(clampLayout({ x: 11, y: 0, w: 1, h: 1 }, 1, 1).x).toBe(11);
    expect(clampLayout({ x: 0, y: 0, w: 1, h: 1 })).toEqual({ x: 0, y: 0, w: GRID_MIN, h: GRID_MIN });
  });
  it("minHeight·gridItems: 요소 카드는 1칸까지", () => {
    expect(minHeight(frameCard("button"))).toBe(1);
    expect(gridItems([frameCard("button")])[0]).toMatchObject({ minH: 1, minW: 1, layout: { w: 1, h: 1 } });
    expect(gridItems([frameCard("kpi", { x: 0, y: 0, w: 3, h: 3 })])[0].minW).toBeUndefined();
  });
  it("손잡이로 줄이면 요소는 1칸, 카드는 2칸에서 멈춘다", () => {
    const box = { left: 0, top: 0, col: 100, row: 50, gap: 10 };
    expect(sizeAt(0, 0, box)).toEqual({ w: 1, h: 1 });
    const items = [{ id: "f", layout: { x: 0, y: 0, w: 3, h: 3 }, minH: 1, minW: 1 }, { id: "k", layout: { x: 3, y: 0, w: 3, h: 3 } }];
    expect(resizeCard(items, "f", 1, 1).f).toMatchObject({ w: 1, h: 1 });
    expect(resizeCard(items, "k", 1, 1).k).toMatchObject({ w: GRID_MIN, h: GRID_MIN });
  });
});

describe("기간 단위에 분기가 있다(달력 분기: 1·4·7·10월)", () => {
  it("BUCKET.quarter: 그 날이 속한 분기의 첫날", () => {
    expect(BUCKET.quarter("2000-01-01")).toBe("2000-01-01");
    expect(BUCKET.quarter("2000-03-31")).toBe("2000-01-01");
    expect(BUCKET.quarter("2000-05-17")).toBe("2000-04-01");
    expect(BUCKET.quarter("2000-09-30")).toBe("2000-07-01");
    expect(BUCKET.quarter("2000-12-31")).toBe("2000-10-01");
    expect(Object.keys(BUCKET)).toEqual(["day", "week", "month", "quarter", "year"]);
  });
  it("unitLabel: 월 YYYY-MM, 분기 YYYY-Qn, 연 YYYY, 나머지는 그대로", () => {
    expect(unitLabel("2000-04-01", "quarter")).toBe("2000-Q2");
    expect(unitLabel("2000-10-01", "quarter")).toBe("2000-Q4");
    expect(unitLabel("2000-01-01", "quarter")).toBe("2000-Q1");
    expect(unitLabel("2000-04-01", "month")).toBe("2000-04");
    expect(unitLabel("2000-04-01", "year")).toBe("2000");
    expect(unitLabel("2000-04-03", "week")).toBe("2000-04-03");
    expect(unitLabel("2000-04-03", "day")).toBe("2000-04-03");
    expect(src("components/cards/chart/series.ts")).toContain("unitLabel(l, unit)");
  });
  it("자동 묶기는 월 다음에 분기를 본다", () => {
    const counts = { day: 1000, week: 143, month: 33, quarter: 11, year: 3 };
    expect(autoUnit(counts, 12, 1)).toBe("quarter");
    expect(autoUnit(counts, 40, 1)).toBe("month");
    expect(autoUnit(counts, 5, 1)).toBe("year");
  });
  it("서버의 단위 목록에 분기가 있다(화면의 단위 고르기는 이 목록을 그대로 그린다)", () => {
    expect([...server.match(/^UNITS = \{([^}]*)\}/m)![1].matchAll(/"(\w+)":/g)].map(m => m[1])).toEqual(["day", "week", "month", "quarter", "year"]);
  });
});

describe("화면 파일이 요소를 잇는다", () => {
  it("격자·설정 패널·추가 메뉴·보고서", () => {
    expect(src("components/grid/Grid.tsx")).toContain("<FrameCard");
    expect(src("components/edit/CardPanel.tsx")).toContain("<FramePanel");
    const menu = src("components/edit/AddCardMenu.tsx");
    for (const word of ["FRAME_GROUPS", "FRAME_KINDS", "findSpecs", 'id="cm-find"']) expect(menu).toContain(word);
    const report = src("components/report/Report.tsx");
    for (const word of ["isFrame", "filterNote"]) expect(report).toContain(word);
  });
  it("고정 기간 칸·쪽 탭 줄은 없고 요소로만 둔다", () => {
    const screen = src("components/shell/DashboardScreen.tsx"), bar = src("components/shell/Toolbar.tsx"), tabs = src("components/shell/PageTabs.tsx");
    expect(screen).toContain("<Toolbar");
    expect(screen).toContain("{build && <PageTabs />}");  // 쪽 탭 줄은 만드는 곳에만(쪽을 더하고 빼는 도구)
    expect(bar).not.toContain("d-per");
    expect(bar).not.toContain("<PeriodField");
    expect(bar).toContain("<LastRefreshed />");
    expect(tabs).toContain("const manage = build && !frame;");  // 요소로 놓은 쪽 탭은 이동만
    expect(src("components/frame/FrameCard.tsx")).toContain("<PageTabs frame />");
    for (const css of ["styles/dashboard.css", "styles/dash_edit.css"]) expect(src(css)).not.toContain(".d-per");
  });
  it("할 일이 부르는 기능이 실제로 있다(쉬운 보기·필터 모두 풀기)", () => {
    const top = src("components/shell/TopBar.tsx"), shell = src("components/shell/ShellProvider.tsx");
    for (const word of ["export function flipTheme(", "export function flipEasy("]) expect(top).toContain(word);
    for (const word of ["clearFilters", "setReport", "setZoom", "setElemUnit"]) expect(shell).toContain(word);
    const bar = src("components/shell/Toolbar.tsx");
    expect(bar).not.toContain("const [report, setReport] = useState");  // 그림·보고서 창의 열림 상태는 화면 틀(Shell)에
    expect(bar).toContain("settings, setSettings } = useShell()");  // 설정 창 열림은 Shell에(B11)
  });
  it("CSS: 요소 모양·엠비언트가 있고 길이는 rem만", () => {
    const css = src("styles/dash_edit.css"), mine = css.split("\n").filter(l => /\.fr-|\.bcard\.frame|fr-amb/.test(l));
    for (const word of [".bcard.frame", ".fr-btn", ".fr-nav", ".fr-top", ".fr-seg", ".fr-pages", "@property --fr-amb", "@keyframes fr-amb", "prefers-reduced-motion"]) expect(css).toContain(word);
    expect(mine.length).toBeGreaterThan(0);
    for (const l of mine) expect(l).not.toMatch(/\dpx/);
  });
});
