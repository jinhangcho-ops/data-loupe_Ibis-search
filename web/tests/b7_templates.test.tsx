// B7 템플릿(plans/dash-blocks.md "B7 세부 명세"): 형식 번호가 서버와 같은지, 목록 잇기(allTemplates), 템플릿 → 새 대시보드(templateDash),
// 기본 템플릿 파일, 새 템플릿 api의 경로·body, 화면 파일(Templates·PickDashboard)이 새 길을 쓰는지. 값은 지어낸 것만 쓴다.
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { exportTemplate, fetchTemplates, importTemplate, listTemplates, remoteTemplates, saveTemplate } from "@/lib/api";
import { BLANK, FIRST_PICK, MY_HELP, MY_PREFIX, MY_SUFFIX, TEMPLATE_FORMAT, allTemplates, templateDash } from "@/lib/templates";
import type { Layout, Template } from "@/lib/types";

const root = join(__dirname, "..", "..");
const server = readFileSync(join(root, "core", "dash_store.py"), "utf8");
const L = (x: number, y: number, w: number, h: number): Layout => ({ x, y, w, h });
const VIEW = { type: "", unit: "day", compare: { mode: "span" }, better: "up" } as const;
const COLORS = { series: { a: "#fff" }, parts: {}, order: ["a"], parts_order: [] };
const tpl = (more: Partial<Template> = {}): Template => ({
  format: 1, id: "t1", name: "시험", help: "", pages: ["첫째"],
  sections: [{ id: "s1", title: "위", layout: L(0, 0, 6, 4), follow: true, view: { page: "첫째" } }, { id: "s2", title: "", layout: L(6, 0, 6, 4), follow: false, view: {} }],
  pieces: [
    { kind: "kpi", title: "값", section: "s1", layout: L(0, 0, 40, 32), colors: COLORS, view: { ...VIEW, format: "percent", digits: 1 } },
    { kind: "button", title: "단추", section: "s1", layout: L(41, 0, 20, 8), view: { ...VIEW, frame: { label: "다음", icon: "", action: "page", arg: "첫째", ambient: false } } },
    { kind: "status", title: "상태", section: "s1", layout: L(41, 9, 20, 8) },
    { kind: "future_kind", title: "못 여는 것", section: "s2", layout: L(0, 0, 40, 32) },
  ],
  unknown: ["future_kind"], ...more,
});

describe("한 벌: 화면 lib/templates.ts와 서버 dash_store.py", () => {
  it("TEMPLATE_FORMAT이 서버와 같고 직접 구성도 그 번호", () => {
    expect(+server.match(/^TEMPLATE_FORMAT = (\d+)/m)![1]).toBe(TEMPLATE_FORMAT);
    expect(BLANK.format).toBe(TEMPLATE_FORMAT);
  });
});

describe("기본 템플릿 파일(templates/)", () => {
  it("셋이고 처음 고를 것이 있으며 업무 낱말이 없다", () => {
    const files = readdirSync(join(root, "templates")).sort();
    expect(files).toEqual(["goal.json", "monitor.json", "overview.json"]);
    expect(files).toContain(FIRST_PICK + ".json");
    for (const f of files) {
      const text = readFileSync(join(root, "templates", f), "utf8");
      expect(text).not.toMatch(/신고|조치|기한/);
      expect(JSON.parse(text).format).toBe(TEMPLATE_FORMAT);
    }
  });
});

describe("allTemplates", () => {
  // 절: allTemplates
  it("직접 구성 + 기본 + 내 템플릿(id·이름 바꿈, 설명이 없으면 MY_HELP)", () => {
    const found = allTemplates([tpl({ id: "overview", name: "기본", help: "설명" })], [tpl({ id: "abc", name: "내 것" }), tpl({ id: "d", name: "둘", help: "적음" })]);
    expect(found.map(t => [t.id, t.name, t.help])).toEqual([
      [BLANK.id, BLANK.name, BLANK.help], ["overview", "기본", "설명"], [MY_PREFIX + "abc", "내 것" + MY_SUFFIX, MY_HELP], [MY_PREFIX + "d", "둘" + MY_SUFFIX, "적음"]]);
    expect(found[2].pieces).toHaveLength(4);
  });
});

describe("templateDash", () => {
  // 절: templateDash
  it("섹션마다 새 id, 조각은 새 카드(섹션 이어짐), 못 여는 종류는 빼고 빈 섹션은 남긴다", () => {
    let n = 0;
    const t = tpl();
    const d = templateDash(t, () => `n${++n}`);
    expect(d.pages).toEqual(["첫째"]);
    expect(d.sections).toEqual([{ ...t.sections[0], id: "n1" }, { ...t.sections[1], id: "n2" }]);
    expect(d.cards.map(c => [c.id, c.kind, c.section])).toEqual([["n3", "kpi", "n1"], ["n4", "button", "n1"], ["n5", "status", "n1"]]);
    expect(d.cards[0]).toEqual({ id: "n3", kind: "kpi", title: "값", layout: L(0, 0, 40, 32), section: "n1", colors: COLORS, view: { ...VIEW, format: "percent", digits: 1 }, source: {}, data: {} });
    expect(d.cards[1].view.frame).toEqual(t.pieces[1].view!.frame);
    expect(d.cards[1].view.frame).not.toBe(t.pieces[1].view!.frame);  // 템플릿을 고치지 않게 사본
    expect(d.cards[2].view).toEqual({ type: "", rules: [], links: [] });
    expect(d.cards[2].colors).toEqual({ series: {}, parts: {}, order: [], parts_order: [] });
  });
  // 절: templateDash
  it("직접 구성은 빈 대시보드", () => {
    expect(templateDash(BLANK, () => "x")).toEqual({ pages: [], sections: [], cards: [] });
  });
});

describe("템플릿 api", () => {
  afterEach(() => vi.unstubAllGlobals());
  // 절: B7 api
  it("경로와 body", async () => {
    const calls: [string, RequestInit | undefined][] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      return new Response(JSON.stringify({ templates: [], builtin: [], files: [], path: null }), { status: 200 });
    });
    await listTemplates();
    await saveTemplate("이름", { pages: [], sections: [], cards: [] });
    await exportTemplate("a1");
    await importTemplate("{}");
    await remoteTemplates();
    await fetchTemplates(["a.json"]);
    expect(calls.map(([url, init]) => [url, init?.body ?? null])).toEqual([
      ["/api/dash/templates", null],
      ["/api/dash/templates", JSON.stringify({ name: "이름", dashboard: { pages: [], sections: [], cards: [] } })],
      ["/api/dash/templates/export", JSON.stringify({ id: "a1" })],
      ["/api/dash/templates/import", JSON.stringify({ text: "{}" })],
      ["/api/dash/templates/remote", JSON.stringify({})],
      ["/api/dash/templates/fetch", JSON.stringify({ files: ["a.json"] })],
    ]);
  });
});

describe("화면 파일", () => {
  const read = (p: string) => readFileSync(join(root, "web", "components", p), "utf8");
  // 절: B7 Templates (수정)
  it("Templates.tsx는 조각 둘 섹션을 막지 않고 새 api·목록을 쓴다", () => {
    const src = read("edit/Templates.tsx");
    for (const gone of ["MULTI_PIECE", "TEMPLATES", "templateCards", "BaseTemplate"]) expect(src).not.toContain(gone);
    for (const used of ["allTemplates(", "exportTemplate(", "importTemplate(", "remoteTemplates(", "fetchTemplates(", "UNOPENED", 'type="file"']) expect(src).toContain(used);
  });
  // 절: B7 PickDashboard (수정)
  it("PickDashboard는 allTemplates·templateDash로 만든다", () => {
    const src = read("shell/PickDashboard.tsx");
    for (const gone of ["TEMPLATES", "templateCards"]) expect(src).not.toContain(gone);
    for (const used of ["allTemplates(", "templateDash(", "FIRST_PICK"]) expect(src).toContain(used);
  });
});
