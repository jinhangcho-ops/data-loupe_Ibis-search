// 3차원 그래프(막대·곡면·산점도)의 순수 계산: 투영·돌리기·크게 작게, 뒤에서 앞으로 그릴 차례, 높이 색, 눈금, 그릴 모양, 그릴 수 없는 이유 문장, 종류 이유, 확대 축.
// 값은 모두 시험 안에서 지어낸다.
import { describe, expect, it } from "vitest";
import {
  MAX_CELLS_3D, MAX_FACES_3D, NEED_THREE, NEED_Z, PITCH_RANGE, PLAIN, SMOOTH_STEPS, START_VIEW, ZOOM_RANGE, axisShapes, dragMode, gridReason, gridScene, heightColor, liftGrid, niceTicks, orbit, paint, pointsReason, pointsScene, project,
  smoothGrid, smoothReason, smoothStep, zPerPx, zoomed,
  type GridSet, type P3, type Shape, type Shaping, type View3,
} from "@/components/cards/chart/three";
import { GRID_3D, THREE_TYPES } from "@/components/cards/chart/shapes";
import { seriesKit } from "@/components/cards/chart/series";
import { pointsTable } from "@/components/cards/ChartCard";
import { zoomAxis } from "@/components/cards/svg/__pure__";
import { buildCard, emptyForm } from "@/components/edit/menu/build";
import { isPoints, POINT_TYPES } from "@/lib/calc";
import type { Card, CardData } from "@/lib/types";

const FRONT: View3 = { yaw: 0, pitch: 0, zoom: 1 }, TOP: View3 = { yaw: 0, pitch: Math.PI / 2, zoom: 1 };
const W = 400, H = 200;
const fmt = (v: number) => String(v);
const set = (label: string, values: (number | null)[], line = false): GridSet => ({ label, values, line, color: `색${label}` });

describe("투영", () => {
  it("정육면체 가운데는 그림 가운데, 깊이 0", () => {
    expect(project([0.5, 0.5, 0.5], START_VIEW, W, H)).toEqual([W / 2, H / 2, 0]);
  });
  it("정면(yaw 0·pitch 0): x는 오른쪽, z는 위, y가 작을수록 보는 쪽에 가깝다", () => {
    const k = Math.min(W, H) * 0.5;
    expect(project([1, 0.5, 0.5], FRONT, W, H)[0]).toBeCloseTo(W / 2 + k / 2);
    expect(project([0.5, 0.5, 1], FRONT, W, H)[1]).toBeCloseTo(H / 2 - k / 2);
    expect(project([0.5, 0, 0.5], FRONT, W, H)[2]).toBeGreaterThan(project([0.5, 1, 0.5], FRONT, W, H)[2]);
  });
  it("바로 위에서 보면 높은 점이 가깝고 화면 위치는 높이와 무관하다", () => {
    const lo = project([0.2, 0.3, 0], TOP, W, H), hi = project([0.2, 0.3, 1], TOP, W, H);
    expect(hi[2]).toBeGreaterThan(lo[2]);
    expect(hi[0]).toBeCloseTo(lo[0]);
    expect(hi[1]).toBeCloseTo(lo[1]);
  });
  it("배율 2면 가운데에서의 거리가 두 배", () => {
    const a = project([1, 0.5, 0.5], FRONT, W, H)[0] - W / 2, b = project([1, 0.5, 0.5], { ...FRONT, zoom: 2 }, W, H)[0] - W / 2;
    expect(b).toBeCloseTo(a * 2);
  });
});

describe("돌리기·크게 작게", () => {
  it("끌면 yaw·pitch가 바뀌고 pitch는 범위를 넘지 않는다", () => {
    expect(orbit(START_VIEW, 100, 0).yaw).toBeCloseTo(START_VIEW.yaw + 1);
    expect(orbit(START_VIEW, 0, 10000).pitch).toBe(PITCH_RANGE[1]);
    expect(orbit(START_VIEW, 0, -10000).pitch).toBe(PITCH_RANGE[0]);
    expect(orbit(START_VIEW, 5, 5).zoom).toBe(START_VIEW.zoom);
  });
  it("휠 한 칸에 1.25배, 범위 안에서만", () => {
    expect(zoomed(START_VIEW, 1).zoom).toBeCloseTo(1.25);
    expect(zoomed(START_VIEW, -1).zoom).toBeCloseTo(0.8);
    expect(zoomed(START_VIEW, 100).zoom).toBe(ZOOM_RANGE[1]);
    expect(zoomed(START_VIEW, -100).zoom).toBe(ZOOM_RANGE[0]);
  });
});

describe("뒤에서 앞으로 그리기", () => {
  const dot = (y: number, more: Partial<Shape> = {}): Shape => ({ kind: "dot", pts: [[0.5, y, 0.5]], fill: `y${y}`, r: 1, ...more } as Shape);
  const order = (shapes: Shape[], v = FRONT) => paint(shapes, v, W, H).map(d => (d.shape as { fill: string }).fill);
  it("먼 것(y 큰 것)부터, 뒤로 돌려 보면 반대", () => {
    expect(order([dot(0), dot(1), dot(0.5)])).toEqual(["y1", "y0.5", "y0"]);
    expect(order([dot(0), dot(1), dot(0.5)], { ...FRONT, yaw: Math.PI })).toEqual(["y0", "y0.5", "y1"]);
  });
  it("layer -1은 맨 먼저, 1은 맨 나중, anchor·bias가 차례를 정한다", () => {
    expect(order([dot(0, { layer: -1 }), dot(1, { layer: 1 }), dot(0.5)])).toEqual(["y0", "y0.5", "y1"]);
    expect(order([dot(0, { anchor: [0.5, 1, 0.5] }), dot(0.5)])).toEqual(["y0", "y0.5"]);
    expect(order([dot(1, { bias: 5 }), dot(0)])).toEqual(["y0", "y1"]);
  });
  it("뒤를 보는 면은 그리지 않는다", () => {
    const face = (normal: P3): Shape => ({ kind: "poly", pts: [[0, 0, 0], [1, 0, 0], [1, 0, 1]], fill: normal.join(), normal });
    expect(order([face([0, -1, 0]), face([0, 1, 0])])).toEqual(["0,-1,0"]);
  });
  it("꼭짓점을 화면 좌표로 바꾼다", () => {
    const [d] = paint([{ kind: "line", pts: [[0.5, 0.5, 0.5], [1, 0.5, 0.5]], stroke: "s", width: 1 }], FRONT, W, H);
    expect(d.xy[0]).toEqual([W / 2, H / 2]);
    expect(d.xy[1][0]).toBeGreaterThan(W / 2);
  });
});

describe("색·눈금", () => {
  it("높이 색은 두 색 사이(0 = 낮은 색, 1 = 높은 색), 범위 밖은 끝 색", () => {
    expect(heightColor(0, "var(--c3)", "var(--c8)")).toBe("color-mix(in srgb, var(--c8) 0%, var(--c3))");
    expect(heightColor(0.5, "L", "H")).toBe("color-mix(in srgb, H 50%, L)");
    expect(heightColor(1, "L", "H")).toBe("color-mix(in srgb, H 100%, L)");
    expect(heightColor(7, "L", "H")).toBe(heightColor(1, "L", "H"));
    expect(heightColor(-1, "L", "H")).toBe(heightColor(0, "L", "H"));
  });
  it("눈금은 1·2·5 간격으로 값을 덮는다", () => {
    expect(niceTicks(0, 10)).toEqual([0, 2, 4, 6, 8, 10]);
    expect(niceTicks(3, 97)).toEqual([0, 20, 40, 60, 80, 100]);
    expect(niceTicks(-0.4, 0.3)).toEqual([-0.4, -0.2, 0, 0.2, 0.4]);
    expect(niceTicks(5, 5)).toEqual([0, 2, 4, 6, 8, 10]);
    expect(niceTicks(0, 0)).toEqual([-1, -0.5, 0, 0.5, 1]);
  });
});

describe("그릴 수 없는 이유", () => {
  const one = [{ values: [1, 2] }], two = [{ values: [1, 2] }, { values: [3, 4] }];
  it("계열이 하나면 축이 셋이 아니다", () => {
    expect(NEED_THREE).toBe("3차원 그래프는 축이 세 개 필요해요. [나누기]에 컬럼을 넣거나 계열을 둘 이상 넣어 주세요.");
    for (const t of GRID_3D) expect(gridReason(t as "bar3d", 2, one)).toBe(NEED_THREE);
    expect(gridReason("bar3d", 2, two)).toBe("");
  });
  it("곡면은 X 값이 둘 이상, 막대는 하나여도 된다", () => {
    expect(gridReason("surface3d", 1, two)).toBe("3차원 곡면은 X축 값이 둘 이상 있어야 해요. X축에 값이 여러 개인 컬럼을 골라 주세요.");
    expect(gridReason("bar3d", 1, two)).toBe("");
  });
  it("칸 수 상한", () => {
    const labels = MAX_CELLS_3D / 2 + 1;
    expect(gridReason("bar3d", labels, two)).toBe(`칸이 ${(labels * 2).toLocaleString()}개라 3차원으로 그리기에 너무 많아요(상한 ${MAX_CELLS_3D.toLocaleString()}개). X축 값이나 계열 수를 줄여 주세요.`);
    expect(gridReason("bar3d", MAX_CELLS_3D / 2, two)).toBe("");
  });
  it("숫자 값이 하나도 없으면", () => {
    expect(gridReason("bar3d", 2, [{ values: [null, ""] }, { values: ["가", null] }])).toBe("3차원으로 그릴 숫자 값이 없어요.");
  });
  it("산점도: 점 없음, Z 없음, 숫자가 아닌 값", () => {
    const p = { x: "가로", y: "세로", groups: [] as string[] };
    expect(pointsReason({ ...p, points: [] })).toBe("3차원 산점도로 그릴 점이 없어요. X축·Y축·Z축 값이 모두 숫자인 행이 있어야 해요.");
    expect(NEED_Z).toBe("3차원 산점도는 숫자 컬럼이 셋 필요해요. [Z축]에 숫자 컬럼을 골라 주세요.");
    expect(pointsReason({ ...p, points: [[1, 2]] })).toBe(NEED_Z);
    expect(pointsReason({ ...p, groups: ["갑"], points: [[1, 2, "갑"]] })).toBe(NEED_Z);  // 셋째 칸이 그룹 이름이면 Z가 없다
    expect(pointsReason({ ...p, points: [[1, 2, "셋"]] })).toBe("3차원 산점도의 X축·Y축·Z축 값은 숫자여야 해요. 숫자가 아닌 값이 들어 있어요.");
    expect(pointsReason({ ...p, points: [[1, 2, 3]] })).toBe("");
    expect(pointsReason({ ...p, groups: ["갑"], points: [[1, 2, 3, "갑"]] })).toBe("");
  });
});

describe("3차원 막대", () => {
  const s = gridScene("bar3d", ["1월", "2월"], [set("갑", [10, null]), set("을", [-5, 20])], "L", "H", ["달", "계열", "값"], fmt);
  const tops = s.shapes.filter(x => x.kind === "poly" && x.normal?.[2] === 1) as Extract<Shape, { kind: "poly" }>[];
  it("값이 있는 칸마다 상자(면 다섯), 빈 값은 건너뛴다", () => {
    expect(s.shapes).toHaveLength(3 * 5);
    expect(tops.map(t => t.title)).toEqual(["1월 · 갑: 10", "1월 · 을: -5", "2월 · 을: 20"]);
  });
  it("높이 축은 0을 품고, 윗면은 값(음수면 0) 높이", () => {
    expect(s.axes.z.ticks.map(t => t.text)).toEqual(["-5", "0", "5", "10", "15", "20"]);
    expect(tops.map(t => t.pts[0][2])).toEqual([15 / 25, 5 / 25, 1]);
  });
  it("윗면은 계열 색, 옆면은 어둡게, 범례는 계열", () => {
    expect(tops[0].fill).toBe("색갑");
    expect((s.shapes[1] as { fill: string }).fill).toContain("색갑");
    expect((s.shapes[1] as { fill: string }).fill).not.toBe("색갑");
    expect(s.keys).toEqual([{ name: "갑", color: "색갑" }, { name: "을", color: "색을" }]);
  });
  it("축 이름과 이름 눈금(칸 가운데)", () => {
    expect([s.axes.x.name, s.axes.y.name, s.axes.z.name]).toEqual(["달", "계열", "값"]);
    expect(s.axes.x.ticks).toEqual([{ at: 0.25, text: "1월" }, { at: 0.75, text: "2월" }]);
    expect(s.axes.y.ticks.map(t => t.text)).toEqual(["갑", "을"]);
  });
  it("같은 칸의 면은 한데 모여 그려진다(막대끼리 섞이지 않는다)", () => {
    const titles = paint(s.shapes, START_VIEW, W, H).map(d => (d.shape as { title?: string }).title);
    const runs = titles.filter((t, i) => t !== titles[i - 1]);
    expect(runs).toHaveLength(new Set(titles).size);
  });
});

describe("3차원 곡면", () => {
  const s = gridScene("surface3d", ["a", "b", "c"], [set("갑", [0, 5, 10]), set("을", [10, 5, null], true), set("병", [0, 0, 10])], "L", "H", ["x", "계열", "값"], fmt);
  const quads = s.shapes.filter(x => x.kind === "poly") as Extract<Shape, { kind: "poly" }>[], lines = s.shapes.filter(x => x.kind === "line") as Extract<Shape, { kind: "line" }>[];
  it("이웃한 네 값이 다 있는 칸만 면으로(빈 값이 낀 칸은 뺀다)", () => {
    expect(quads).toHaveLength(2);  // (X 2칸 × 계열 2칸) - 을의 빈 값(c)에 닿는 두 칸
  });
  it("면의 색은 네 꼭짓점 높이의 평균", () => {
    expect(quads[0].fill).toBe(heightColor(0.5, "L", "H"));  // (0 + 5 + 5 + 10) / 4 = 5 → 0~10의 절반
    expect(quads[0].pts[0]).toEqual([0, 0, 0]);
    expect(quads[0].pts[2]).toEqual([0.5, 0.5, 0.5]);
  });
  it("선 계열은 곡면 위의 선(계열 색), 이웃한 면보다 나중에 그린다", () => {
    expect(lines).toHaveLength(1);  // 을: a-b만(c가 비었다)
    expect(lines[0]).toMatchObject({ stroke: "색을", pts: [[0, 0.5, 1], [0.5, 0.5, 0.5]] });
    const order = paint(s.shapes, START_VIEW, W, H).map(d => d.shape);
    expect(order.indexOf(lines[0])).toBeGreaterThan(order.indexOf(quads[0]));
  });
  it("범례는 낮음·높음과 선 계열", () => {
    expect(s.keys).toEqual([{ name: "낮음 0", color: "L" }, { name: "높음 10", color: "H" }, { name: "을", color: "색을" }]);
  });
  it("이름 눈금이 많으면 건너뛰며 적는다", () => {
    const many = gridScene("surface3d", Array.from({ length: 30 }, (_, i) => `x${i}`), [set("갑", Array(30).fill(1)), set("을", Array(30).fill(2))], "L", "H", ["", "", ""], fmt);
    expect(many.axes.x.ticks.map(t => t.text)).toEqual(["x0", "x8", "x16", "x24"]);
  });
});

describe("곡면 매끈함(잘게 나누기)", () => {
  const vals = [[0, 5, 10], [10, 5, null], [0, 0, 10]], K = 4;
  const sets = [set("갑", [0, 5, 10]), set("을", [10, 5, null], true), set("병", [0, 0, 10])];
  const scene = (shape?: Shaping) => gridScene("surface3d", ["a", "b", "c"], sets, "L", "H", ["x", "계열", "값"], fmt, shape);
  const polys = (s: Shape[]) => s.filter(x => x.kind === "poly") as Extract<Shape, { kind: "poly" }>[], lines = (s: Shape[]) => s.filter(x => x.kind === "line") as Extract<Shape, { kind: "line" }>[];
  it("보간한 격자는 원래 격자점의 값을 그대로 지난다", () => {
    const fine = smoothGrid(vals, K);
    expect([fine.length, fine[0].length]).toEqual([9, 9]);
    vals.forEach((row, j) => row.forEach((v, i) => expect(fine[j * K][i * K], `${j},${i}`).toBe(v)));
  });
  it("곧은 줄은 곧게 남는다(값을 지어내지 않는다)", () => {
    expect(smoothGrid([[0, 1, 2], [0, 1, 2]], 2)[0]).toEqual([0, 0.5, 1, 1.5, 2]);
    expect(smoothGrid([[0, 1, 2], [0, 1, 2]], 2).map(r => r[2])).toEqual([1, 1, 1]);
  });
  it("빈 값에 닿는 칸의 안쪽 점은 빈 값(채우지 않는다)", () => {
    const fine = smoothGrid(vals, K);
    expect(fine[0][5]).not.toBeNull();  // 갑의 b~c 사이(둘 다 값이 있다)
    expect(fine[4][5]).toBeNull();  // 을의 b~c 사이(c가 비었다)
    expect(fine[2][6]).toBeNull();  // 빈 꼭짓점이 있는 칸의 안쪽
    expect(fine[2][8]).toBeNull();  // c 줄의 갑~을 사이
    expect(fine[6][8]).toBeNull();  // c 줄의 을~병 사이
    expect(fine[2][2]).not.toBeNull();  // 네 꼭짓점이 다 있는 칸의 안쪽
  });
  it("2배면 면이 칸마다 넷, 빈 값이 낀 칸은 여전히 비고, 면 테두리는 면 색", () => {
    const s = scene({ ...PLAIN, smooth: 2 }), q = polys(s.shapes);
    expect(q).toHaveLength(2 * 4);
    expect(q.every(p => p.stroke === p.fill)).toBe(true);
    expect(q.every(p => p.pts.every(([x]) => x <= 0.5))).toBe(true);  // 빈 값(c)에 닿는 오른쪽 칸에는 면이 없다
  });
  it("원래 격자선만 따로 긋고(0단계는 면 테두리가 격자선), 선 계열은 매끈해진 곡면을 따라간다", () => {
    expect(lines(scene().shapes).filter(l => l.stroke === "var(--card)")).toHaveLength(0);
    expect(polys(scene().shapes).every(p => p.stroke === "var(--card)")).toBe(true);
    const s = scene({ ...PLAIN, smooth: 2 }), grid = lines(s.shapes).filter(l => l.stroke === "var(--card)"), over = lines(s.shapes).filter(l => l.stroke === "색을");
    expect(grid.length).toBeGreaterThan(0);
    expect(grid.every(l => l.pts.every(([x, y]) => x % 0.5 === 0 || y % 0.5 === 0))).toBe(true);  // 원래 격자 줄 위에만
    expect(grid.every(l => l.pts.every(([x]) => x <= 0.5))).toBe(true);  // 면이 없는 칸에는 격자선도 없다
    expect(over.map(l => l.pts.map(p => p[0]))).toEqual([[0, 0.25], [0.25, 0.5]]);  // 을: a~b를 두 토막으로(c는 비었다)
    const mid = smoothGrid(vals, 2)[2][1]!, z = s.axes.z.ticks.length - 1;
    expect(over[0].pts[1][2]).toBeCloseTo((mid - Number(s.axes.z.ticks[0].text)) / (Number(s.axes.z.ticks[z].text) - Number(s.axes.z.ticks[0].text)));
  });
  it("면 수 상한: 넘는 단계는 이유가 있고, 고른 단계는 그릴 수 있는 가장 높은 단계로 내려간다", () => {
    expect(smoothReason(50, 40, 0)).toBe("");
    expect(smoothReason(50, 40, 1)).toBe("");  // 49 × 39 × 4 = 7,644
    expect(smoothReason(50, 40, 2)).toBe(`면이 ${(49 * 39 * 16).toLocaleString()}개가 되어 이 단계는 그릴 수 없어요(상한 ${MAX_FACES_3D.toLocaleString()}개). X축 값이나 계열 수를 줄이면 고를 수 있어요.`);
    expect(smoothStep(50, 40, 3)).toBe(1);
    expect(smoothStep(3, 3, 3)).toBe(3);
    SMOOTH_STEPS.forEach((k, n) => expect(polys(scene({ ...PLAIN, smooth: k }).shapes).length, `${n}단계`).toBeLessThanOrEqual(MAX_FACES_3D));
  });
});

describe("곡면 모양 조절(손잡이 끌기)", () => {
  const sets = [set("갑", [0, 5, 10]), set("을", [10, 5, null]), set("병", [0, 0, 10])];
  const scene = (shape?: Shaping) => gridScene("surface3d", ["a", "b", "c"], sets, "L", "H", ["x", "계열", "값"], fmt, shape);
  it("끈 점은 끈 만큼, 주변은 거리에 따라 덜(가우스), 퍼짐 0이면 그 점만", () => {
    const g = liftGrid(null, 5, 4, 2, 1, 10, 1);
    expect(g.length).toBe(4);
    expect(g[1][2]).toBe(10);
    expect(g[1][3]).toBeCloseTo(10 * Math.exp(-0.5));
    expect(g[1][1]).toBeCloseTo(g[1][3]);
    expect(g[2][3]).toBeCloseTo(10 * Math.exp(-1));
    expect(g[1][4]).toBeLessThan(g[1][3]);
    expect(liftGrid(null, 5, 4, 2, 1, 10, 3)[1][3]).toBeGreaterThan(g[1][3]);  // 퍼짐이 넓으면 더 따라온다
    expect(liftGrid(null, 3, 2, 0, 0, 7, 0)).toEqual([[7, 0, 0], [0, 0, 0]]);
  });
  it("이미 조절한 모양 위에 더하고, 반대로 끌면 처음으로 돌아온다", () => {
    const once = liftGrid(null, 3, 3, 1, 1, 4, 1.5), twice = liftGrid(once, 3, 3, 0, 0, 2, 0), back = liftGrid(once, 3, 3, 1, 1, -4, 1.5);
    expect(twice[0][0]).toBeCloseTo(once[0][0] + 2);
    expect(twice[1][1]).toBe(4);
    back.flat().forEach(v => expect(v).toBeCloseTo(0));
  });
  it("조절한 높이는 그림에만 더해지고 높이 축이 그 범위를 덮는다. 빈 값 칸은 그대로 빈다", () => {
    const lift = liftGrid(null, 3, 3, 0, 0, 100, 0), s = scene({ ...PLAIN, lift }), top = Number(s.axes.z.ticks.at(-1)!.text);
    expect(top).toBeGreaterThanOrEqual(100);
    expect(s.span).toBe(top - Number(s.axes.z.ticks[0].text));
    expect(s.shapes.filter(x => x.kind === "poly")).toHaveLength(2);
    expect((s.shapes[0] as Extract<Shape, { kind: "poly" }>).pts[0][2]).toBeCloseTo((100 - Number(s.axes.z.ticks[0].text)) / s.span);
    expect(sets[0].values[0]).toBe(0);  // 받은 값은 바뀌지 않는다
  });
  it("처음 모양(조절 없음)은 모양을 주지 않은 그림과 같다", () => {
    expect(scene(PLAIN)).toEqual(scene());
    expect(scene().span).toBe(10);
  });
  it("손잡이는 값이 있는 원래 격자점마다(빈 값 자리에는 없다), 켰을 때만", () => {
    const grips = (s: Shape[]) => s.filter(x => x.kind === "dot") as Extract<Shape, { kind: "dot" }>[];
    expect(grips(scene().shapes)).toHaveLength(0);
    const g = grips(scene({ smooth: 2, lift: null, grips: true }).shapes);
    expect(g.map(d => d.grip)).toEqual([[0, 0], [1, 0], [2, 0], [0, 1], [1, 1], [0, 2], [1, 2], [2, 2]]);
    expect(grips(scene({ ...PLAIN, grips: true }).shapes)[1].pts[0]).toEqual([0.5, 0, 0.5]);  // 손잡이는 그 점의 값 높이에
  });
  it("끌기 판단: Ctrl이면 언제나 돌리기, 아니면 켜진 그림에서만(손잡이 위는 높이 조절)", () => {
    expect(dragMode(true, false, false)).toBe("turn");
    expect(dragMode(true, true, true)).toBe("turn");
    expect(dragMode(false, false, true)).toBeNull();
    expect(dragMode(false, false, false)).toBeNull();
    expect(dragMode(false, true, true)).toBe("lift");
    expect(dragMode(false, true, false)).toBe("turn");
  });
  it("끈 px을 높이로: 투영의 반대", () => {
    const dz = 0.2, px = project([0.5, 0.5, 0.5], START_VIEW, W, H)[1] - project([0.5, 0.5, 0.5 + dz], START_VIEW, W, H)[1];
    expect(px * zPerPx(START_VIEW, W, H)).toBeCloseTo(dz);
  });
});

describe("3차원 산점도", () => {
  const p = { x: "가로", y: "세로", groups: ["갑", "을"], points: [[0, 0, 0, "갑"], [10, 10, 10, "을"], [5, 10, 0, "갑"], [10, 0, 5, "을"]] as (number | string)[][] };
  it("점마다 원(그룹 색), 자리는 축 범위에 맞춘다", () => {
    const s = pointsScene(p, ["색갑", "색을"], false, "높이", fmt), dots = s.shapes as Extract<Shape, { kind: "dot" }>[];
    expect(dots.map(d => d.fill)).toEqual(["색갑", "색갑", "색을", "색을"]);
    expect(dots.map(d => d.pts[0])).toEqual([[0, 0, 0], [0.5, 1, 0], [1, 1, 1], [1, 0, 0.5]]);
    expect(dots[1].title).toBe("갑 · 가로 5, 세로 10, 높이 0");
    expect([s.axes.x.name, s.axes.y.name, s.axes.z.name]).toEqual(["가로", "세로", "높이"]);
    expect(s.keys).toEqual([{ name: "갑", color: "색갑" }, { name: "을", color: "색을" }]);
  });
  it("점 잇기: 그룹마다 받은 차례대로 선", () => {
    const lines = pointsScene(p, ["색갑", "색을"], true, "높이", fmt).shapes.filter(x => x.kind === "line") as Extract<Shape, { kind: "line" }>[];
    expect(lines.map(l => [l.stroke, l.pts])).toEqual([["색갑", [[0, 0, 0], [0.5, 1, 0]]], ["색을", [[1, 1, 1], [1, 0, 0.5]]]]);
  });
  it("그룹이 없으면 한 색, 범례 없음", () => {
    const s = pointsScene({ ...p, groups: [], points: [[1, 2, 3], [2, 3, 4]] }, ["색"], true, "z", fmt);
    expect(s.shapes.map(x => x.kind)).toEqual(["dot", "dot", "line"]);
    expect(s.keys).toEqual([]);
  });
  it("데이터 표의 셋째 열 이름은 Z축 컬럼", () => {
    expect(pointsTable({ ...p, groups: [], points: [[1, 2, 3]] }, "높이")).toEqual({ columns: ["가로", "세로", "높이"], rows: [{ 가로: 1, 세로: 2, 높이: 3 }] });
  });
});

describe("축 그리기", () => {
  const axes = { x: { name: "가로", ticks: [{ at: 0.5, text: "x가운데" }] }, y: { name: "", ticks: [{ at: 0.5, text: "y가운데" }] }, z: { name: "값", ticks: [{ at: 0, text: "0" }, { at: 1, text: "9" }] } };
  const texts = (v: View3) => (axisShapes(axes, v).filter(s => s.kind === "text") as Extract<Shape, { kind: "text" }>[]);
  it("이름이 빈 축은 이름을 적지 않고, 글자는 맨 앞·선은 맨 뒤 층", () => {
    const all = axisShapes(axes, START_VIEW);
    expect(texts(START_VIEW).map(t => t.text).sort()).toEqual(["0", "9", "x가운데", "y가운데", "가로", "값"].sort());
    expect(all.every(s => s.layer === (s.kind === "text" ? 1 : -1))).toBe(true);
  });
  it("X축 글자는 보는 쪽 바닥 모서리에(돌리면 반대쪽으로 옮긴다)", () => {
    const at = (v: View3) => texts(v).find(t => t.text === "x가운데")!.pts[0][1];
    expect(at(FRONT)).toBeLessThan(0);
    expect(at({ ...FRONT, yaw: Math.PI })).toBeGreaterThan(1);
  });
  it("높이 글자는 화면 왼쪽 끝 모서리에", () => {
    const v = START_VIEW, z = texts(v).find(t => t.text === "9")!.pts[0];
    const left = Math.min(...[[0, 0], [0, 1], [1, 0], [1, 1]].map(([x, y]) => project([x, y, 0], v, 1, 1)[0]));
    expect(project([z[0], z[1], 0], v, 1, 1)[0]).toBeCloseTo(left);
  });
});

describe("카드에서 3차원 종류", () => {
  const TYPES = { bar3d: "3차원 막대", surface3d: "3차원 곡면", scatter3d: "3차원 산점도", bar: "막대" };
  const kit = seriesKit({ agg_names: {}, merge: ["sum"], dash: { types: TYPES, time_types: ["bar", "bar3d", "surface3d"] } } as never);
  const card = (data: CardData, view: Record<string, unknown> = {}, body: Record<string, unknown> = {}): Card => ({ id: "c", kind: "chart", title: "카드", layout: { x: 0, y: 0, w: 4, h: 4 },
    colors: { series: {}, parts: {}, order: [], parts_order: [] }, view: { type: "", unit: "day", compare: { mode: "span" }, better: "up", ...view } as Card["view"], source: { body }, data });
  const plain = (n: number): CardData => ({ values: { labels: ["a", "b"], series: Array.from({ length: n }, (_, i) => ({ name: `s${i}`, values: [1, 2] })) } });
  it("종류 목록", () => {
    expect(THREE_TYPES).toEqual(["bar3d", "surface3d", "scatter3d"]);
    expect(POINT_TYPES).toContain("scatter3d");
  });
  it("막대·곡면은 계열이 둘 이상일 때만", () => {
    for (const t of GRID_3D) {
      expect(kit.typeReason(card(plain(1)), t as never), t).toBe(NEED_THREE);
      expect(kit.typeReason(card(plain(2)), t as never), t).toBe("");
    }
  });
  it("산점도는 X·값이 숫자 컬럼이고 Z축을 골랐을 때만", () => {
    const data = { ...plain(1), x_kind: "num", num_cols: ["값1", "값2"] }, body = { x: "가로", series: [{ y: "값1", agg: "sum" }] };
    expect(kit.typeReason(card(data, {}, body), "scatter3d")).toBe(NEED_Z);
    expect(kit.typeReason(card(data, { points: { size: "값2" } }, body), "scatter3d")).toBe("");
    expect(kit.typeReason(card({ ...data, x_kind: "text" }, { points: { size: "값2" } }, body), "scatter3d")).toContain("숫자 컬럼");
    expect(kit.typeReason(card(data, {}, body), "bubble")).toBe("버블은 [크기]에 숫자 컬럼을 먼저 고르세요.");
    expect(kit.typeReason(card(data, {}, body), "scatter")).toBe("");
  });
  it("3차원 그림은 가로축 확대를 쓰지 않는다(끌면 돈다)", () => {
    const many: CardData = { values: { labels: Array.from({ length: 9 }, (_, i) => `l${i}`), series: [{ name: "s0", values: Array(9).fill(1) }, { name: "s1", values: Array(9).fill(2) }] } };
    const s = (c: Card) => kit.cardSeries(c);
    expect(zoomAxis(card(many, { type: "bar" }), s(card(many, { type: "bar" })), false)).toEqual({ kind: "index", count: 9 });
    for (const t of GRID_3D) expect(zoomAxis(card(many, { type: t }), s(card(many, { type: t })), false), t).toBeNull();
    const pts = card({ points: { x: "가로", y: "세로", points: [[1, 2, 3], [4, 5, 6]], groups: [] } as never }, { type: "scatter3d" });
    expect(isPoints(pts)).toBe(true);
    expect(zoomAxis(pts, null, false)).toBeNull();
    expect(zoomAxis({ ...pts, view: { ...pts.view, type: "scatter" } }, null, false)).toEqual({ kind: "value", lo: 1, hi: 4 });
  });
  it("카드 만들기: 3차원 산점도는 Z축 컬럼이 있어야 하고 view.points.size에 담는다", () => {
    const form = { ...emptyForm("chart"), table: "t", cType: "scatter3d", cX: "가로", rows: [{ label: "", y: "세로", agg: "sum" }] };
    expect(() => buildCard(form, ["sum", "count"])).toThrow(NEED_Z);
    expect(buildCard({ ...form, cSize: "높이" }, ["sum", "count"]).view).toMatchObject({ type: "scatter3d", points: { size: "높이" } });
  });
});
