// Phase 9(plans/phase-9.md#autoDraw): 대시보드 차트 카드의 자동 규칙·데이터 표 묶음(web/components/cards/ChartCard.tsx autoDraw).
// 스켈레톤이면 실패하는 것이 정상이다. 상수·값은 시험 안에서 지어낸다.
import { describe, expect, it } from "vitest";
import { GROUP_NAME, HIST_COLUMNS, NO_AUTO, autoDraw } from "@/components/cards/ChartCard";
import { seriesKit, type SCard } from "@/components/cards/chart/series";
import { shownum } from "@/lib/calc";
import type { AutoTable, SemanticWords } from "@/lib/autoviz";
import type { Constants } from "@/lib/types";

const WORDS: SemanticWords = { good: ["증가"], bad: ["감소"], other: ["기타"], null: ["None"] };
// 서버 AUTO_PARAMS 중 이 시험이 쓰는 칸만
const TABLE = {
  resample: { mode: ["lttb", ["lttb", "rollup"]], px: [1, 1, 20] },
  density: { faint: [1000, 1, 1e9], hex: [10000, 1, 1e9], alpha: [0.8, 0.05, 1], faint_alpha: [0.2, 0.05, 1], cols: [40, 5, 200] },
  tail: { lo: [0.01, 0, 0.5], hi: [0.99, 0.5, 1] },
} as unknown as AutoTable;
const consts = (auto: AutoTable | null = TABLE) => ({  // null = 규칙 표를 못 받음(undefined를 넘기면 기본값 TABLE이 들어간다)
  agg_names: { sum: "합계" }, merge: ["sum", "count", "min", "max"], week_start: "mon",
  dash: { types: {}, time_types: ["bar", "line"], units: { day: "일", week: "주", month: "월", year: "연" }, auto, viz: { words: WORDS, query_auto: {} } },
}) as unknown as Constants;
const card = (kind: string, view: Record<string, unknown>, data: Record<string, unknown>): SCard =>
  ({ id: "c1", kind, title: "t", layout: { x: 0, y: 0, w: 6, h: 6 }, view: { unit: "day", ...view }, source: { body: { series: [{ agg: "sum" }] } }, data }) as unknown as SCard;

describe("ChartCard autoDraw", () => {
  const C = consts(), kit = seriesKit(C);

  it("값이 없으면 계열·표 없음, 안내 없음", () => {
    const d = autoDraw(card("chart", { type: "" }, {}), kit, C, 600, false);
    expect(d.s).toBeNull();
    expect(d.table).toBeNull();
    expect(d.notes).toEqual([]);
  });

  it("산점도: 받은 점 그대로 표(x, y, 그룹), 점 단계는 밀도 수치로, 모든 데이터면 단계 없음", () => {
    const c = card("chart", { type: "scatter" }, { points: { x: "키", y: "몸무게", points: [[1, 2, "A"], [3, 4, "B"]], groups: ["A", "B"], total: 2 } });
    const d = autoDraw(c, kit, C, 600, false);
    expect(d.table).toEqual({ columns: ["키", "몸무게", GROUP_NAME], rows: [{ 키: 1, 몸무게: 2, [GROUP_NAME]: "A" }, { 키: 3, 몸무게: 4, [GROUP_NAME]: "B" }] });
    expect(d.tier).toEqual({ mode: "dots", alpha: 0.8, r: 3 });
    expect(d.notes).toEqual([]);
    const all = autoDraw(c, kit, C, 600, true);
    expect(all.tier).toBeNull();
    expect(all.c.view.all_data).toBe(true);
    expect(all.table).toEqual(d.table);
  });

  it("버블(그룹 없음): 크기 열 이름은 view.points.size", () => {
    const c = card("chart", { type: "bubble", points: { size: "무게" } }, { points: { x: "키", y: "몸무게", points: [[1, 2, 10]], groups: [], total: 1 } });
    expect(autoDraw(c, kit, C, 600, false).table).toEqual({ columns: ["키", "몸무게", "무게"], rows: [{ 키: 1, 몸무게: 2, 무게: 10 }] });
  });

  it("분석 히스토그램: 구간·행 수 표, 꼬리 칸은 AnaChart와 같은 이름, tail은 수치표 기본", () => {
    const c = card("analysis", {}, { values: { kind: "distribution", column: "v", histogram: { edges: [0, 1, 2], counts: [3, 4], under: 1, over: 0 } } });
    const d = autoDraw(c, kit, C, 600, false);
    expect(d.tail).toEqual([0.01, 0.99]);
    expect(d.tier).toBeNull();
    const [g, n] = HIST_COLUMNS;
    expect(d.table).toEqual({ columns: HIST_COLUMNS, rows: [
      { [g]: "P1 미만(1건)", [n]: 1 },
      { [g]: `${shownum(0)}–${shownum(1)}`, [n]: 3 },
      { [g]: `${shownum(1)}–${shownum(2)}`, [n]: 4 },
    ] });
  });

  it("상수에 자동 규칙 표가 없으면 규칙 없이 그리고 안내 한 줄", () => {
    const N = consts(null);
    const c = card("chart", { type: "" }, { x: "이름", values: { labels: ["a", "b"], series: [{ name: "합", values: [1, 2] }] } });
    const d = autoDraw(c, seriesKit(N), N, 600, false);
    expect(d.notes).toEqual([NO_AUTO]);
    expect(d.s?.labels).toEqual(["a", "b"]);
    expect(d.tier).toBeNull();
    expect(d.tail).toBeUndefined();
    expect(d.table).toEqual({ columns: ["이름", "합"], rows: [{ 합: 1, 이름: "a" }, { 합: 2, 이름: "b" }] });
  });
});
