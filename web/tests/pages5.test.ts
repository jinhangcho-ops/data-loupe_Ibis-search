// 대시보드 5단계: 쪽(탭)과 값/비율 바꾸기. 계산은 손으로 구한 값과 견주고, 화면 길은 코드 글자로 본다. 데이터는 지어낸 것.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { toShare, type Series } from "@/components/cards/chart/series";
import { wrapCard } from "@/components/grid/sections";
import { onPage, pageOf } from "@/lib/calc";
import type { Card } from "@/lib/types";

const src = (p: string) => readFileSync(join(__dirname, "..", p), "utf8");
const card = (id: string, page?: string) => ({ id, view: page ? { page } : {} }) as unknown as Card;
const s = (datasets: [string, (number | string | null)[]][]): Series => ({ labels: ["가", "나", "다"], datasets: datasets.map(([label, values]) => ({ label, values })), time: false, chartable: true });

describe("쪽(탭)", () => {
  const pages = ["현황", "조치"], cards = [card("a"), card("b", "조치"), card("c", "지운 쪽"), card("d", "현황")];
  it("쪽을 정하지 않았거나 없는 쪽의 카드는 첫 쪽", () => {
    expect(cards.map(c => pageOf(c, pages))).toEqual(["현황", "조치", "현황", "현황"]);
  });
  it("지금 쪽의 카드만 고른다. 모르는 쪽 이름이면 첫 쪽, 쪽이 없으면 전부", () => {
    expect(onPage(cards, pages, "조치").map(c => c.id)).toEqual(["b"]);
    expect(onPage(cards, pages, "").map(c => c.id)).toEqual(["a", "c", "d"]);
    expect(onPage(cards, [], "조치")).toBe(cards);
    expect(onPage(cards, undefined, "")).toBe(cards);
  });
  it("새 카드는 지금 쪽에 놓인다(쪽이 없으면 그대로)", () => {
    const fresh = { ...card("n"), kind: "kpi", layout: { x: 0, y: 0, w: 6, h: 6 } } as Card;  // B5: 쪽은 새 카드를 감싼 섹션이 가진다
    expect(wrapCard(fresh, [], pages, "조치").section.view.page).toBe("조치");
    expect(wrapCard(fresh, [], pages, "").section.view.page).toBe("현황");
    expect(wrapCard(fresh, [], [], "조치").section.view.page).toBeUndefined();
  });
  it("격자는 지금 쪽만 그리고 빈 줄을 붙이되, 저장할 때는 모든 쪽의 카드를 남긴다", () => {
    const grid = src("components/grid/Grid.tsx");
    expect(grid).toContain("const secs = onPage(allSecs, dash?.pages, page);");  // B5: 쪽은 섹션이 가진다
    expect(grid).toContain("up = paged || cut ? compactLayout(raw) : null");
    expect(grid).toContain('const n = applySections(all, allSecs, layouts); dispatch({ type: "cards", cards: n.cards, sections: n.sections });');
    expect(grid).toContain('dispatch({ type: "cards", cards: all.filter(c => !ids.includes(c.id)), sections });');
  });
  it("쪽 줄은 만드는 곳에서만 더하고 빼며, 카드 설정에 쪽 고르는 칸이 있다", () => {
    const tabs = src("components/shell/PageTabs.tsx");
    expect(tabs).toContain("if (!state.dash || (!pages.length && !build)) return null;");
    expect(tabs).toContain('dispatch({ type: "meta", patch: { pages: next } })');
    expect(src("components/shell/DashboardScreen.tsx")).toContain("<PageTabs />");
    // [대시보드만 보기]는 .board-ctl을 숨기는데 쪽 줄은 남긴다(안 그러면 그 화면에서 쪽을 못 바꾼다). 이 CSS는 next 빌드가 싣는다(layout.tsx)
    expect(src("styles/dashboard.css")).toContain(".board-ctl:not(#d-filters, #d-pages)");
    expect(src("components/edit/CardPanel.tsx")).toContain('sections.map(s => (s.id === sec.id ? { ...s, view: { ...s.view, page: e.target.value } }');
  });
});

describe("보는 곳은 확인만, 고치기는 만드는 곳에서만", () => {
  it("보는 곳에서 바꾼 기간·기준일은 저장하지 않고, 카드 이름은 고칠 수 있어 보이지 않는다", () => {
    expect(src("components/shell/ShellProvider.tsx")).toContain("if (!id || !build) return;");
    expect(src("styles/common.css")).toContain(".btitle:not([readonly]):hover");
  });
  it("만드는 곳에서는 이름 칸을 잡고도 카드를 끈다(쓰고 있는 이름 칸은 빼고, 안 끌고 떼면 이름 고치기)", () => {
    const drag = src("components/grid/useDrag.ts");
    expect(drag).toContain('closest("button, select") || (name && name === document.activeElement)');
    expect(drag).toContain('if (name && !far && ev.type === "pointerup") name.focus();');
  });
});

describe("값/비율 바꾸기", () => {
  it("계열 하나: 전체 합 가운데의 몫(%)", () => {
    expect(toShare(s([["매출", [10, 30, 60]]]))!.datasets[0].values).toEqual([10, 30, 60]);
    expect(toShare(s([["매출", [1, 1, 1]]]))!.datasets[0].values).toEqual([33.3, 33.3, 33.3]);
  });
  it("계열 여럿: 가로축 값마다 계열들 가운데의 몫(%)", () => {
    const r = toShare(s([["완료", [1, 3, 0]], ["취소", [3, 1, 0]]]))!;
    expect(r.datasets[0].values).toEqual([25, 75, null]);  // 합이 0인 칸은 빈 값
    expect(r.datasets[1].values).toEqual([75, 25, null]);
    expect(r.labels).toEqual(["가", "나", "다"]);
  });
  it("빈 값·글자는 빈 값으로, 합에는 넣지 않는다", () => {
    expect(toShare(s([["매출", [null, 1, "x"]]]))!.datasets[0].values).toEqual([null, 100, null]);
  });
  it("음수가 있으면 바꾸지 않는다", () => {
    expect(toShare(s([["손익", [5, -1, 2]]]))).toBeNull();
  });
  it("% 단추는 계열 차트 카드에만, 켠 카드만 비율로 그리고 저장하지 않는다", () => {
    const grid = src("components/grid/Grid.tsx");
    expect(grid).toContain('{!isShell(card) && !paint && card.kind !== "analysis" && !isPoints(card) && (');
    expect(grid).toContain("share={shares.includes(card.id)}");
    const chart = src("components/cards/ChartCard.tsx");
    expect(chart).toContain("const pct = share && series ? toShare(s) : null;");
    expect(chart).toContain("s={pct ?? s}");
  });
});
