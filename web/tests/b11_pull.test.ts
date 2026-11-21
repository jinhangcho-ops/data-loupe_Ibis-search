// B11 데이터 받기·저장·갱신(plans/dash-blocks.md "B11 세부 명세"): 받기 설정의 순수 계산(lib/pull.ts), 틀 요소의 할 일 settings(frame/registry.ts),
// 화면 파일이 설정 창 열림을 Shell로 올리고, 카드 구하기에 대시보드 id·갱신 표를 싣고, 옛 최신화·실시간 칸을 없앴는지 본다. 값은 지어낸 것만 쓴다.
// it.fails: 기존 파일을 고쳐야 통과하는 시험. 그 항목이 끝나면 .fails를 지운다.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { FRAME_ACTIONS, runAction, type ActionHost } from "@/components/frame/registry";
import { everySeconds, pullError, pullOf } from "@/lib/pull";
import type { Pull, PullConsts, PullTable } from "@/lib/types";

const src = (p: string) => readFileSync(join(__dirname, "..", p), "utf8");
const C: PullConsts = {
  calcs: ["pc", "db"], ranges: ["all", "new"], crypts: ["none", "aes128", "aes256", "pass"], units: { s: 1, m: 60, h: 3600 }, unit_names: { s: "초", m: "분", h: "시" },
  min_every: 10, max_every: 604800, default: { calc: "pc", every: null, range: "all", marks: [], crypt: "none" }, gap_note: "데이터가 없는 구간이 있어요.",
};
const base: Pull = { calc: "pc", every: null, range: "all", marks: [], crypt: "none" };
const tables: PullTable[] = [
  { schema: "", table: "orders", columns: ["id", "day"], join: false },
  { schema: "", table: "customers", columns: ["cid"], join: true },
];

describe("pullOf · everySeconds", () => {
  it("저장본에 없으면 처음 값의 사본", () => {
    const got = pullOf({}, C);
    expect(got).toEqual(C.default);
    got.marks.push({ schema: "", table: "t", column: "c" });
    expect(C.default.marks).toEqual([]);
    const mine: Pull = { ...base, calc: "db" };
    expect(pullOf({ pull: mine }, C)).toBe(mine);
  });
  it("단위를 초로, 단추를 누를 때만은 0", () => {
    expect(everySeconds(null, C)).toBe(0);
    expect(everySeconds({ n: 30, unit: "s" }, C)).toBe(30);
    expect(everySeconds({ n: 5, unit: "m" }, C)).toBe(300);
    expect(everySeconds({ n: 168, unit: "h" }, C)).toBe(604800);
  });
});

describe("pullError", () => {
  it("맞으면 빈 글자", () => {
    expect(pullError(base, tables, C, 60)).toBe("");
    expect(pullError({ ...base, every: { n: 10, unit: "s" } }, tables, C, 60)).toBe("");  // PC 방식은 연결 최소를 보지 않는다
    expect(pullError({ ...base, range: "new", marks: [{ schema: "", table: "orders", column: "id" }] }, tables, C, 60)).toBe("");  // 조인으로만 읽는 테이블은 고르지 않는다
  });
  it("갱신 주기 범위와 모양", () => {
    expect(pullError({ ...base, every: { n: 9, unit: "s" } }, tables, C, 0)).toBe("갱신 주기는 10초 이상 168시 이하예요.");
    expect(pullError({ ...base, every: { n: 169, unit: "h" } }, tables, C, 0)).toBe("갱신 주기는 10초 이상 168시 이하예요.");
    expect(pullError({ ...base, every: { n: 1.5, unit: "m" } }, tables, C, 0)).toBe("갱신 주기는 1 이상의 정수와 단위(초·분·시)로 적어 주세요.");
    expect(pullError({ ...base, every: { n: 0, unit: "m" } }, tables, C, 0)).toBe("갱신 주기는 1 이상의 정수와 단위(초·분·시)로 적어 주세요.");
  });
  it("계산 방식 DB면 연결 최소 이상", () => {
    expect(pullError({ ...base, calc: "db", every: { n: 30, unit: "s" } }, tables, C, 60)).toBe("이 연결의 갱신 주기는 1분 이상이어야 해요(계산 방식 DB).");
    expect(pullError({ ...base, calc: "db", every: { n: 1, unit: "m" } }, tables, C, 60)).toBe("");
  });
  it("없는 것만 받기는 조인이 아닌 테이블마다 새 줄 컬럼", () => {
    expect(pullError({ ...base, range: "new" }, tables, C, 0)).toBe("'orders' 테이블의 새 줄을 알아볼 컬럼을 대시보드 설정에서 골라 주세요(없는 것만 받기).");
  });
});

describe("할 일 settings(대시보드 설정 창 열기)", () => {
  it("등록 자리에 있고 받는 값이 없다", () => {
    expect((FRAME_ACTIONS as Record<string, { name: string; arg: string }>).settings).toEqual({ name: "대시보드 설정 창 열기", arg: "" });
  });
  it("runAction이 host.setSettings(true)를 부른다", () => {
    const setSettings = vi.fn();
    const host = { recompute: vi.fn(), setPage: vi.fn(), dashOnly: false, setDashOnly: vi.fn(), setReport: vi.fn(), setZoom: vi.fn(), flipTheme: vi.fn(), pages: [], cardIds: [],
      flipEasy: vi.fn(), clearFilters: vi.fn(), setSettings } as unknown as ActionHost;
    runAction({ action: "settings" as never, arg: "" }, host);
    expect(setSettings).toHaveBeenCalledWith(true);
  });
});

describe("화면 파일이 잇는 것", () => {
  it("카드 구하기·시간 눈금·점에 대시보드 id와 갱신 표를 싣는다", () => {
    const api = src("lib/api.ts");
    expect(api).toContain('export const computeCard = (card: Card, ctx: Ctx | null | undefined, dash: string, fresh = "")');
    expect(api).toContain('export const computeCards = (cards: Card[], ctx: Ctx | null | undefined, dash: string, fresh = "")');
    expect(api).toContain("export const cardHours = (card: Card, ctx: Ctx | null, dash: string)");
    expect(api).not.toMatch(/parseEvery|setLiveMin|refreshStatus|stopRefresh/);
    expect(api).toContain('"/api/dash/pull/full"');
  });
  it("옛 최신화 주기·실시간 칸과 최신화 알약이 없고 갱신 주기 사슬이 있다", () => {
    const shell = src("components/shell/ShellProvider.tsx"), bar = src("components/shell/PeriodBar.tsx"), top = src("components/shell/TopBar.tsx");
    for (const id of ["dc-every", "dc-live", "dc-min"]) expect(bar).not.toContain(`id="${id}"`);
    expect(shell).not.toMatch(/toggleLive|commitEvery|saveMin|liveOn/);
    expect(shell).toContain("crypto.randomUUID()");
    expect(shell).toMatch(/settings: boolean; setSettings: \(open: boolean\) => void;/);
    expect(top).not.toContain("rf-pill");
  });
  it("설정 창 열림은 Shell에 있고 Toolbar·FrameCard가 그것을 쓴다, 보는 곳 설정 창은 데이터 받기 + 기간 칸, 도구 줄에 종 단추", () => {
    const toolbar = src("components/shell/Toolbar.tsx");
    expect(toolbar).not.toMatch(/const \[settings, setSettings\] = useState/);
    expect(toolbar).toContain('id="d-settings"');
    expect(toolbar).toContain("<Bell />");
    expect(src("components/frame/FrameCard.tsx")).toContain("setSettings: shell.setSettings");
    const dialog = src("components/shell/SettingsDialog.tsx");
    expect(dialog).toContain("<PullSettings");
    expect(dialog).toContain('<PeriodField id="dc-view-per"');
    expect(src("components/shell/PullSettings.tsx")).toContain("disabled={!build}");  // 계산 방식은 만드는 곳에서만 바꾼다(사용자 답 2-13)
    expect(src("components/shell/PullSettings.tsx")).toContain("pullRange(");  // 설정 창에서도 기간을 정해 다시 받는다(사용자 답 3-11)
    const bell = src("components/shell/Bell.tsx");
    expect(bell).toContain("BELL_PATH");
    expect(bell).toContain("bellMark(");  // 종 그림 + 읽음 기록(사용자 답 3-5)
    expect(src("components/edit/Templates.tsx")).not.toContain('"recent_rows"');
  });
});

describe("행을 그대로 보이는 카드 없애기(사용자 답 2-13b)", () => {
  it("최근 행 카드와 원래 행 읽기가 없다", () => {
    const rows = src("components/cards/svg/Rows.tsx"), common = src("components/cards/svg/common.tsx"), kinds = src("components/edit/menu/kinds.ts");
    expect(rows).not.toMatch(/RecentRows|useRows/);
    expect(common).not.toMatch(/useRows|filteredBody|\/api\/query/);
    expect(kinds).not.toContain('["recent_rows", "최근 행"');
    expect(src("lib/api.ts")).not.toMatch(/filteredBody|export const query =/);
    expect(src("components/cards/SvgCards.tsx")).toContain("더는 지원하지 않는 카드");
  });
});
