// 대시보드 4단계: 카드 눌러 서로 거르기. "누르면 거르기"(view.cross)를 켠 차트의 막대·조각을 누르면 가로축 값으로 모든 카드를 거르고, 걸린 것은 필터 줄에 칩으로 보인다.
// 누르기 자체(Recharts의 마우스 자리 계산)는 jsdom에서 크기가 0이라 못 돌려서, 길이 이어져 있는지는 코드 글자로 본다. 데이터는 지어낸 것.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const src = (p: string) => readFileSync(join(__dirname, "..", p), "utf8");

describe("카드 눌러 서로 거르기", () => {
  it("카드 설정에 [누르면 거르기] 체크(분석·점 그림 제외), view.cross로 저장", () => {
    const tools = src("components/edit/panel/Tools.tsx");
    expect(tools).toContain('CHART_KINDS.includes(card.kind) && look !== "ana" && look !== "points" && (');
    expect(tools).toContain('aria-label="누르면 거르기" checked={view.cross === true} onChange={e => (e.target.checked ? setView({ cross: true, ...(view.link_to || !state.dash ? {} : { link_to: freshLinks(card, state.dash, page) }) }) : without("cross"))}');
  });
  it("차트 카드: 켠 카드만, 가로축이 컬럼이고 날짜 축이 아닐 때만, 실제 값(합친 이름 아님)만 받고 카드 고르기로 번지지 않는다", () => {
    const card = src("components/cards/ChartCard.tsx");
    expect(card).toContain('const pick = onPick && card.view.cross && typeof x === "string" && x && !data.by_unit');
    expect(card).toContain("if (kit!.cardSeries(c0)?.labels.includes(label)) { e.stopPropagation(); onPick(x, label); }");
  });
  it("그림: 막대·선은 가로축 자리(activeLabel), 도넛·파이·장미는 조각 이름으로 부른다", () => {
    const charts = src("components/cards/chart/Charts.tsx");
    expect(charts).toContain("onClick={onPick ? (st, e) => { if (st?.activeLabel != null) onPick(String(st.activeLabel), e); } : undefined}");
    expect(charts).toContain("if (d.payload) onPick(d.payload.label, e);");
    expect(charts.match(/isAnimationActive=\{false\} \{\.\.\.press\}/g)).toHaveLength(2);
  });
  it("격자: 같은 값을 다시 누르면 풀고, 만드는 곳이나 연결이 없을 때는 누르기를 받지 않는다", () => {
    const grid = src("components/grid/Grid.tsx");
    expect(grid).toContain("press(from, on ? null : { value: { column, in: [value] } })");
    expect(grid).toContain("onPick={build || !conn ? undefined : (column, value) => cross(card.id, column, value)}");
    expect(grid).toContain('<CardBody card={big} result={state.values[big.id]} palette={dash.palette} ctx={ctxOf(big.id)} share={shares.includes(big.id)} />');  // 크게 보기 창에서는 받지 않는다
  });
  it("필터 줄: 필터 칸이 없는 컬럼에 걸린 것은 칩으로 보이고 ×로 푼다. 칩만 있어도 줄이 보인다", () => {
    const bar = src("components/shell/FilterBar.tsx");
    expect(bar).toContain("const pressed = dash?.press_chips === false ? [] : presses.filter(p => p.value);");
    expect(bar).toContain("if (!dash || (!filters.length && !build && !pressed.length)) return null;");
    expect(bar).toContain("aria-label={`${pressLabel(p, dash.cards)} 거르기 풀기`} onClick={() => { press(p.from, null); }}");
  });
});
