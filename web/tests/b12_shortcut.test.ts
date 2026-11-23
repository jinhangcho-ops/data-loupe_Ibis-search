// B12 대시보드 바로가기(plans/dash-blocks.md "B12 세부 명세"): 바로가기 창의 단추 할 일 막기(runAction), 화면 파일이 바로가기 상태를 잇는지 본다. 값은 지어낸 것만 쓴다.
// it.fails: 기존 파일을 고쳐야 통과하는 시험. 주석에 적은 항목(그 묶음의 마지막 줄)이 끝나면 그 항목의 strict-coder가 .fails를 지운다.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { FRAME_ACTIONS, SHORTCUT_ACTIONS, SHORTCUT_NO, runAction, type ActionHost } from "@/components/frame/registry";

const src = (p: string) => readFileSync(join(__dirname, "..", p), "utf8");
const host = (over: Partial<ActionHost> = {}): ActionHost => ({
  recompute: vi.fn(), setPage: vi.fn(), dashOnly: true, setDashOnly: vi.fn(), setReport: vi.fn(), setZoom: vi.fn(), flipTheme: vi.fn(), pages: ["첫 쪽"], cardIds: ["c1"], saveCsv: vi.fn(), saveMarks: vi.fn(), loadMarks: vi.fn(),
  flipEasy: vi.fn(), clearFilters: vi.fn(), setSettings: vi.fn(), ...over,
});

describe("바로가기 창의 단추 할 일", () => {
  it("되는 할 일은 등록된 할 일 가운데 일곱(사용자 답 2차 6)", () => {
    expect([...SHORTCUT_ACTIONS].sort()).toEqual(["csv", "export", "filters_clear", "page", "refresh", "theme", "zoom"]);
    for (const a of SHORTCUT_ACTIONS) expect(Object.hasOwn(FRAME_ACTIONS, a)).toBe(true);
  });
  // B12 runAction (수정)
  it("바로가기 창에서는 설정 창·보기 끝내기·쉬운 보기·표시 저장/가져오기를 막고 아무것도 부르지 않는다", () => {
    const h = host({ shortcut: true });
    for (const action of ["dash_only", "easy", "settings", "marks_save", "marks_load"] as const) {
      expect(() => runAction({ action, arg: "c1" }, h)).toThrow(SHORTCUT_NO);
    }
    for (const fn of [h.setDashOnly, h.flipEasy, h.setSettings, h.saveMarks, h.loadMarks]) expect(fn).not.toHaveBeenCalled();
  });
  it("바로가기 창에서도 쪽 넘기기·최신화·화면 모드·필터 풀기·크게 보기·내보내기·CSV는 된다", () => {
    const h = host({ shortcut: true });
    runAction({ action: "page", arg: "첫 쪽" }, h);
    runAction({ action: "refresh", arg: "" }, h);
    runAction({ action: "theme", arg: "" }, h);
    runAction({ action: "filters_clear", arg: "" }, h);
    runAction({ action: "zoom", arg: "c1" }, h);
    runAction({ action: "export", arg: "" }, h);
    runAction({ action: "csv", arg: "c1" }, h);
    expect(h.setPage).toHaveBeenCalledWith("첫 쪽");
    expect(h.recompute).toHaveBeenCalled();
    expect(h.flipTheme).toHaveBeenCalled();
    expect(h.clearFilters).toHaveBeenCalled();
    expect(h.setZoom).toHaveBeenCalledWith("c1");
    expect(h.setReport).toHaveBeenCalledWith(true);
    expect(h.saveCsv).toHaveBeenCalledWith("c1");
  });
  it("바로가기 창이 아니면 그대로다", () => {
    const h = host();
    runAction({ action: "dash_only", arg: "" }, h);
    expect(h.setDashOnly).toHaveBeenCalledWith(false);
  });
});

describe("화면 파일이 잇는 것", () => {
  // B12 ShellProvider (수정)
  it("Shell에 바로가기 상태가 있고 바로가기 창은 늘 대시보드만 보기다", () => {
    const shell = src("components/shell/ShellProvider.tsx");
    expect(shell).toMatch(/shortcut: string;/);
    expect(shell).toMatch(/ask: ShortcutConn \| null; setAsk: \(ask: ShortcutConn \| null\) => void; startShortcut: \(\) => Promise<void>;/);
    expect(shell).toContain("const dashOnly = shortcut !== \"\" || dashOnlyOn;");
  });
  // B12 FrameCard (수정)
  it("FrameCard가 바로가기 상태를 할 일에 넘긴다", () => {
    expect(src("components/frame/FrameCard.tsx")).toContain('shortcut: shell.shortcut !== ""');
  });
  // B12 dashboard.css (수정) — U129의 마지막 줄
  it("도구 줄: [바로가기 만들기]는 [대시보드만 보기] 옆, 바로가기 창에는 설정·보기 끝내기가 없고 비밀번호 창이 있다", () => {
    const bar = src("components/shell/Toolbar.tsx");
    expect(bar.indexOf("<ShortcutMaker />")).toBeGreaterThan(bar.indexOf('id="d-only"'));
    expect(bar.indexOf("<ShortcutMaker />")).toBeLessThan(bar.indexOf('id="d-build"'));
    expect(bar).toContain("{!shortcut && <button className=\"btn\" id=\"d-settings\"");
    expect(bar).toContain("{!shortcut && <button id=\"d-only-exit\"");
    expect(bar).toContain("{shortcut && <PasswordAsk />}");
    expect(src("components/shell/Bell.tsx")).toContain("shortcut ? NONE : collect(dashId, !!conn)");
    const css = src("styles/dashboard.css");
    expect(css.split("\n").find(l => l.startsWith("body.dash-only :is("))).toContain("#d-shortcut");
    expect(css).toContain("#dash.wait, body:has(#dash.wait) :is(.rail, .topbar) { filter:blur(.25rem); pointer-events:none; user-select:none; }");  // 상수를 받기 전: 틀만 흐리게(사용자 답 2차 12)
  });
  // B12 Grid (수정) — U130의 마지막 줄
  it("상수를 받기 전에는 흐리게 열지 않고, 바로가기 창에서는 정리 묻기·줄 표시 바꾸기를 하지 않는다", () => {
    expect(src("components/shell/PullCleanup.tsx")).toContain("if (!consts || consts.shortcut) return;");
    const screen = src("components/shell/DashboardScreen.tsx");
    expect(screen).toContain('<div className={consts ? "board-view" : "board-view wait"} id="dash">');
    expect(screen).toContain("open(shortcut || (list.dashboards.some(d => d.id === want) ? want : list.dashboards[0]?.id ?? \"\"))");
    expect(src("components/grid/Grid.tsx")).toContain("onMark={look && !shortcut && state.dash?.id ?");
  });
});
