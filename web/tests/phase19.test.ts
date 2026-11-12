// Phase 19(plans/phase-19.md) 묶음 H: 대시보드(Next)의 쉬운 말 title·빈 화면 두 단계, 왼쪽 메뉴의 쉬운 보기 스위치(#easy, status.html과 같은 모양·동작).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const src = (path: string) => readFileSync(fileURLToPath(new URL("../" + path, import.meta.url)), "utf-8");

describe("19I1 위쪽 줄 title과 빈 화면", () => {
  const bar = src("components/shell/Toolbar.tsx");
  it("[다시 구하기]·[대시보드만 보기] title이 쉬운 말", () => {
    expect(bar).toContain('title="데이터를 새로 읽어 카드 숫자를 고쳐요">다시 구하기</button>');
    expect(bar).toContain('title="왼쪽 메뉴와 단추를 숨기고 카드만 크게 봐요. Esc를 누르면 돌아와요">대시보드만 보기</button>');
  });
  it("빈 화면은 ① [카드 추가] ② 카드 종류 고르기, 또는 쿼리에서 [대시보드에 추가]", () => {
    const page = src("app/dashboard/page.tsx");
    expect(page).toContain("① [카드 추가]를 눌러요");
    expect(page).toContain("② 보고 싶은 카드 종류를 골라요");
    expect(page).toContain("쿼리 화면에서 차트를 만들고 [대시보드에 추가]를 눌러요");
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
