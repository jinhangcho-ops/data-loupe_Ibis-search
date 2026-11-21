// Phase 19(plans/phase-19.md) 묶음 H: 대시보드(Next)의 쉬운 말 title·빈 화면 두 단계, 왼쪽 메뉴의 쉬운 보기 스위치(#easy, status.html과 같은 모양·동작).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const src = (path: string) => readFileSync(fileURLToPath(new URL("../" + path, import.meta.url)), "utf-8");

describe("19I1 위쪽 줄 title과 빈 화면", () => {
  const bar = src("components/shell/Toolbar.tsx");
  it("[최신화](옛 [다시 구하기])·[대시보드만 보기] title이 쉬운 말", () => {
    expect(bar).toContain('<button className="btn" onClick={again} disabled={busy} title="데이터를 새로 읽어 카드 숫자를 최신으로 고쳐요">최신화</button>');
    expect(bar).not.toContain("다시 구하기</button>");
    expect(bar).toContain('title="왼쪽 메뉴와 단추를 숨기고 카드만 크게 봐요. Esc를 누르면 돌아와요">대시보드만 보기</button>');
  });
  it("강조 단추 [카드 추가](#d-add-open)는 만드는 곳(build)에만 있고 서랍을 연다([+ 카드 ▾] 메뉴에는 같은 항목이 없다)", () => {
    expect(bar).not.toContain('<button role="menuitem" onClick={() => { setMenu(false); dispatch({ type: "select", id: null }); setAddOpen(true); }}');
    const line = bar.split("\n").find(l => l.includes('id="d-add-open"'))!;
    expect(line).toContain('className="btn p"');
    expect(line).toContain("disabled={!state.dash}");
    expect(line).toContain('onClick={() => { dispatch({ type: "select", id: null }); setAddOpen(true); }}');
    expect(line.trimStart().startsWith("{build && <button")).toBe(true);
  });
  it("보는 곳에는 편집 도구가 없고 [만들기]로, 만드는 곳에는 [편집]·[대시보드만 보기]가 없고 [보러 가기]로 간다", () => {
    expect(bar).not.toContain("편집 끝내기");
    expect(bar.split("\n").find(l => l.includes('id="d-view"'))).toContain("flush().then(() => { location.href = viewHref(state.dash?.id); }");
    expect(bar).toContain('id="d-build" href={buildHref(state.dash?.id)}');
    expect(bar.replace(/\r/g, "")).toContain('{build && (\n        <div className="drop" ref={drop}');  // [+ 카드 ▾]는 만드는 곳에만
    expect(src("components/shell/PickDashboard.tsx")).toContain('{build && <span className="drop" ref={wrap}');  // [새 대시보드]도
    expect(src("components/shell/DashboardScreen.tsx")).toContain('useEffect(() => { dispatch({ type: "editing", on: build }); }, [build, dispatch]);');
    expect(src("app/dashboard/build/page.tsx")).toContain("<DashboardScreen build />");
    expect(src("app/dashboard/page.tsx")).toContain("<DashboardScreen build={false} />");
  });
  it("빈 화면은 한 줄 안내와 단추 둘([카드 추가]·[쿼리에서 가져오기]), 쿼리 쪽 방법은 단추 title에", () => {
    const page = src("components/shell/DashboardScreen.tsx");
    expect(page).not.toContain("① [카드 추가]를 눌러요");  // 단추와 같은 말을 되풀이하던 안내
    expect(page).toContain('title="쿼리 화면에서 차트를 만들고 [대시보드에 추가]를 눌러요">쿼리에서 가져오기</a>');
  });
});

describe("Next 쪽 쉬운 보기 스위치", () => {
  it("왼쪽 메뉴 아래 #easy가 [설정]과 화면 모드 사이에 있고 status.html과 같은 글·아이콘", () => {
    const bar = src("components/shell/TopBar.tsx"), status = src("../static/core/status.html");
    const foot = bar.slice(bar.indexOf('className="rail-foot"'));
    expect(foot.indexOf(">설정</span>")).toBeLessThan(foot.indexOf('id="easy"'));
    expect(foot.indexOf('id="easy"')).toBeLessThan(foot.indexOf('id="theme"'));
    expect(bar).toContain('id="easy" className="rail-btn" aria-pressed={easy}');
    expect(bar).toContain('<span id="easy-text">{easy ? "쉬운 보기 켜짐" : "쉬운 보기 꺼짐"}</span>');
    const tip = status.match(/id="easy" class="rail-btn" aria-pressed="true" title="([^"]+)"/)![1];
    expect(bar).toContain(`title="${tip}"`);
    expect(bar).toContain('saveSetting({ easy: next ? "on" : "off" })');
    expect(bar).toContain('document.documentElement.classList.toggle("easy", next)');
  });
  it("그리기 전에 쿠키 easy를 읽어 html.easy를 붙인다(고른 적이 없으면 켬)", () => {
    const layout = src("app/layout.tsx");
    const script = layout.slice(layout.indexOf("const THEME = `") + 15, layout.indexOf("`;", layout.indexOf("const THEME = `")));
    const run = (cookie: string) => {
      const cls = new Set<string>();
      const doc = { cookie, documentElement: { dataset: {} as Record<string, string>, classList: { toggle: (c: string, on: boolean) => { if (on) cls.add(c); else cls.delete(c); } } } };
      new Function("document", "matchMedia", script)(doc, () => ({ matches: false }));
      return cls.has("easy");
    };
    expect(run("")).toBe(true);
    expect(run("theme=dark; easy=off")).toBe(false);
    expect(run('easy="on"')).toBe(true);
  });
});
