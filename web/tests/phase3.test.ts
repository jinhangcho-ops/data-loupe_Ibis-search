// Phase 3(plans/phase-3.md) 순수 함수: 사다리꼴 퍼널 좌표·옆 글, 가로축 확대·이동·자르기, 조건 단계 틀·읽기, 카드 만들기(조건 단계 퍼널·자동 흐름·생키 조건 줄).
// 새 함수가 스켈레톤이면 실패하는 것이 정상이다. 기존 함수 수정((수정) 절)이 끝나야 통과하는 시험은 it.fails로 표시했다(고친 뒤 it으로 바꾼다).
import { describe, expect, it } from "vitest";
import type { Card } from "@/lib/types";
import type { SCard, Series } from "@/components/cards/chart/series";
import { GAP_STATS, LADDER_MIN, MIN_POINTS, durationText, flowDropout, fullSpan, gapRows, ladderLayout, ladderLines, panRange, sliceWindow, zoomAxis, zoomRange, type Gap, type Step, type ZoomAxis } from "@/components/cards/svg/__pure__";
import { FUNNEL_TEMPLATES, RETENTION_NAME, buildCard, condLines, emptyForm, formFromCard, rowsOf, stagesOf, templateStages, type Form } from "@/components/edit/menu/build";

const steps: Step[] = [{ name: "획득", users: 600, conv: 1, from_first: 1 }, { name: "활성", users: 300, conv: 0.5, from_first: 0.5 }, { name: "유지", users: 150, conv: 0.5, from_first: 0.25 }];

describe("사다리꼴 퍼널(ladderLines·ladderLayout)", () => {
  it("옆 글: 번호·이름, 인원과 전 단계·첫 단계 대비(첫 단계는 인원만)", () => {
    expect(ladderLines(steps[0], 0)).toEqual(["1. 획득", "600명"]);
    expect(ladderLines(steps[1], 1)).toEqual(["2. 활성", "300명 · 전 단계 대비 50.0% · 첫 단계 대비 50.0%"]);
    expect(ladderLines({ name: "x", users: 0, conv: null, from_first: null }, 2)).toEqual(["3. x", "0명 · 전 단계 대비 - · 첫 단계 대비 -"]);
  });
  it("단계마다 떨어진 사다리꼴, 아래로 갈수록 좁아지고(인원과 상관없이) 옆에 글 자리", () => {
    const g = ladderLayout(steps, { width: 600, height: 300, gap: 6, shapeShare: 0.5, minRatio: 0.3, pad: 8, textGap: 12 });
    expect(g.rows.length).toBe(3);
    expect([g.rows[0].x0, g.rows[0].x1, g.rows[0].top]).toEqual([8, 292, 8]);
    expect(g.rows[2].bottom).toBeCloseTo(292, 6);
    expect(g.rows[1].top - g.rows[0].bottom).toBeCloseTo(6, 6);  // 칸 사이가 떨어져 있다
    const widths = g.rows.map(r => r.x1 - r.x0);
    expect(widths[0] > widths[1] && widths[1] > widths[2]).toBe(true);
    expect(g.rows.every(r => r.textX === 312)).toBe(true);
    expect(g.rows[0].path.startsWith("M8.0,8.0 L292.0,8.0 ")).toBe(true);
    const same = ladderLayout(steps.map(s => ({ ...s, users: 1 })), { width: 600, height: 300 });
    expect(same.rows.map(r => r.path)).toEqual(ladderLayout(steps, { width: 600, height: 300 }).rows.map(r => r.path));
    expect(ladderLayout([], { width: 600, height: 300 }).rows).toEqual([]);
  });
  it("인원 비례(scale): 윗변 = 인원 / 가장 많은 인원, 아랫변 = 다음 단계 윗변(마지막은 같은 폭), 0명도 LADDER_MIN만큼", () => {
    const g = ladderLayout(steps, { width: 600, height: 300, pad: 8, shapeShare: 0.5, scale: true });
    expect(g.rows.map(r => r.x1 - r.x0)).toEqual([284, 142, 71]);
    expect(g.rows[2].path.endsWith(`L${(150 + 35.5).toFixed(1)},${g.rows[2].bottom.toFixed(1)} L${(150 - 35.5).toFixed(1)},${g.rows[2].bottom.toFixed(1)} Z`)).toBe(true);
    const zero = ladderLayout([steps[0], { name: "x", users: 0, conv: 0, from_first: 0 }], { width: 600, height: 300, pad: 8, shapeShare: 0.5, scale: true });
    expect(zero.rows[1].x1 - zero.rows[1].x0).toBeCloseTo(284 * LADDER_MIN, 6);
  });
});

describe("걸린 시간 표(durationText·gapRows)", () => {
  const gaps: Gap[] = [{ from: "획득", to: "활성", n: 3, mean: 31200, median: 3600, trimmed: 30 }, { from: "활성", to: "유지", n: 0, mean: null, median: null, trimmed: null }];
  it("초 → 초·분·시간·일(소수 한 자리), 없으면 -", () => {
    expect([durationText(30), durationText(90), durationText(5400), durationText(129600), durationText(null)]).toEqual(["30초", "1.5분", "1.5시간", "1.5일", "-"]);
  });
  it("고른 값만 GAP_STATS 차례로, 절사평균은 자르는 몫을 이름에, 지나간 사람이 없으면 안내", () => {
    expect(gapRows(gaps, ["trimmed", "mean"], 0.1)).toEqual([
      { label: "획득 → 활성", values: "평균 8.7시간 · 절사평균(10%) 30초 (3명)" },
      { label: "활성 → 유지", values: "지나간 사람이 없어요" }]);
    expect(gapRows(gaps, [], 0.1)).toEqual([]);
    expect(GAP_STATS).toEqual(["mean", "median", "trimmed"]);
  });
});

describe("가로축 확대·이동(zoomRange·panRange·sliceWindow·fullSpan)", () => {
  const idx: ZoomAxis = { kind: "index", count: 101 }, val: ZoomAxis = { kind: "value", lo: 0, hi: 1000 };
  it("전체 범위", () => {
    expect(fullSpan(idx)).toEqual([0, 100]);
    expect(fullSpan(val)).toEqual([0, 1000]);
  });
  it("휠 한 칸은 1.25배, 마우스 자리를 붙잡고, 전체·최소 폭 안", () => {
    expect(zoomRange([0, 100], idx, 0.5, 1)).toEqual([10, 90]);
    expect(zoomRange([0, 100], idx, 0, 1)).toEqual([0, 80]);
    expect(zoomRange([10, 90], idx, 0.5, -5)).toEqual([0, 100]);
    expect(zoomRange([0, 100], idx, 0.5, 100)).toEqual([48, 52]);  // 라벨 MIN_POINTS개(폭 4)까지
    expect(MIN_POINTS).toBe(5);
    expect(zoomRange([0, 1000], val, 1, 100)).toEqual([999, 1000]);  // 값 축은 전체 폭 × MIN_SHARE
  });
  it("끌기: 오른쪽으로 끌면 앞(이른) 쪽이 보이고, 끝에 닿으면 멈춘다(폭은 그대로)", () => {
    expect(panRange([10, 90], idx, 0.25)).toEqual([0, 80]);
    expect(panRange([10, 90], idx, -0.05)).toEqual([14, 94]);
    expect(panRange([0, 100], idx, 0.5)).toEqual([0, 100]);
  });
  it("자르기: 순서 번호 범위를 덮는 행(받아 둔 값에서만)", () => {
    const rows = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];
    expect(sliceWindow(rows, null)).toBe(rows);
    expect(sliceWindow(rows, [2.4, 5.2])).toEqual([2, 3, 4, 5, 6]);
    expect(sliceWindow(rows, [0, 9])).toEqual(rows);
  });
});

describe("생키 이탈 칸 켜짐(flowDropout)", () => {
  const sankey = (analysis: Record<string, unknown>, view: Record<string, unknown> = {}) => ({ id: "c", kind: "sankey", title: "t", layout: { x: 0, y: 0, w: 4, h: 4 },
    colors: { series: {}, parts: {}, order: [], parts_order: [] }, view: { type: "", ...view }, source: { analysis }, data: {} } as unknown as Card);
  it("정하지 않으면 끝 값을 골랐을 때만 켬, 카드에서 정하면 그대로", () => {
    expect(flowDropout(sankey({ mode: "auto", ends: ["buy"] }))).toBe(true);
    expect(flowDropout(sankey({ mode: "auto", ends: [] }))).toBe(false);
    expect(flowDropout(sankey({ mode: "auto" }))).toBe(false);
    expect(flowDropout(sankey({ mode: "auto", ends: ["buy"] }, { flow_dropout: false }))).toBe(false);
    expect(flowDropout(sankey({ mode: "auto" }, { flow_dropout: true }))).toBe(true);
  });
});

describe("확대할 수 있는 가로축(zoomAxis)", () => {
  const card = (type: string, data: Record<string, unknown> = {}) => ({ id: "c", kind: "chart", title: "t", layout: { x: 0, y: 0, w: 4, h: 4 }, colors: { series: {}, parts: {}, order: [], parts_order: [] },
    view: { type }, source: {}, data } as unknown as SCard);
  const s = (n: number): Series => ({ labels: Array.from({ length: n }, (_, i) => `d${i}`), datasets: [], time: true, chartable: true });
  it("가로축이 순서인 계열 차트만, 라벨이 MIN_POINTS개 넘을 때", () => {
    expect(zoomAxis(card("line"), s(10), false)).toEqual({ kind: "index", count: 10 });
    expect(zoomAxis(card("waterfall"), s(10), false)).toEqual({ kind: "index", count: 10 });
    for (const t of ["doughnut", "pie", "radial", "polar", "radar", "slope", "hbar", "hstack", "hstack100", "butterfly"]) expect(zoomAxis(card(t), s(10), false), t).toBeNull();
    expect(zoomAxis(card("line"), s(MIN_POINTS), false)).toBeNull();
    expect(zoomAxis(card("line"), null, false)).toBeNull();
  });
  it("산점도는 값 축(x 최소~최대), 분석 그림은 보기마다", () => {
    expect(zoomAxis(card("scatter", { points: { points: [[3, 1], [9, 2], [-1, 5]], groups: [], x: "a", y: "b" } }), s(0), false)).toEqual({ kind: "value", lo: -1, hi: 9 });
    expect(zoomAxis(card("", { values: { kind: "trend", labels: s(30).labels, values: [] } }), s(30), true)).toEqual({ kind: "index", count: 30 });
    expect(zoomAxis(card("", { values: { kind: "distribution", histogram: { edges: Array(21).fill(0), counts: Array(20).fill(1) } } }), s(20), true)).toEqual({ kind: "index", count: 20 });
    expect(zoomAxis(card("", { values: { kind: "correlation", columns: ["a", "b"], matrix: [] } }), s(0), true)).toBeNull();
  });
});

describe("조건 단계(templateStages·stagesOf·rowsOf·condLines)", () => {
  it("틀은 단계 이름·차례를 채우고 유지 단계만 재방문 N일", () => {
    const a = templateStages("aarrr");
    expect(a.map(r => r.name)).toEqual(FUNNEL_TEMPLATES.aarrr.names);
    expect(a.map(r => r.revisit)).toEqual(["", "", "7", "", ""]);
    expect(templateStages("rarra")[0]).toEqual({ name: RETENTION_NAME, conds: "", revisit: "7" });
  });
  it("조건 줄은 줄마다 하나(빈 줄 빼고)", () => {
    expect(condLines(" event in purchase, refund \n\n amount > 0\r\n")).toEqual(["event in purchase, refund", "amount > 0"]);
  });
  it("서버 모양으로 읽고 되돌린다, 틀린 칸은 이유", () => {
    const rows = [{ name: " 획득 ", conds: "event = signup", revisit: "" }, { name: "유지", conds: "", revisit: "7" }];
    const st = stagesOf(rows);
    expect(st).toEqual([{ name: "획득", conds: ["event = signup"] }, { name: "유지", conds: [], revisit: 7 }]);
    expect(rowsOf(st)).toEqual([{ name: "획득", conds: "event = signup", revisit: "" }, { name: "유지", conds: "", revisit: "7" }]);
    expect(() => stagesOf([])).toThrow("1~10개");
    expect(() => stagesOf([{ name: "", conds: "a = 1", revisit: "" }])).toThrow("이름");
    expect(() => stagesOf([{ name: "a", conds: "", revisit: "" }])).toThrow("조건 줄");
    expect(() => stagesOf([{ name: "a", conds: "", revisit: "1.5" }])).toThrow("재방문");
    expect(() => stagesOf([{ name: "a", conds: "x = 1", revisit: "" }, { name: "a", conds: "x = 2", revisit: "" }])).toThrow("같은 이름");
  });
});

describe("카드 만들기(buildCard·formFromCard 수정)", () => {
  const aggs = ["sum", "count"];
  it("조건 단계 퍼널은 source.analysis {kind: stages, who, time, stages, count, window, trim}", () => {
    const stages = templateStages("aarrr").map((r, i) => ({ ...r, conds: i === 2 ? "" : `ev = s${i}` }));
    const f = { ...emptyForm("funnel"), table: "t", fUser: "u", fTime: "ts", fDefine: "stages", fStages: stages, fCount: "each", fWin: "2", fUnit: "day", fTrim: "20" } as unknown as Form;
    const c = buildCard(f, aggs);
    expect(c.source).toMatchObject({ table: "t", recipe: { schema: "", base: "t" }, full: true, date: "ts",
      analysis: { kind: "stages", who: "u", time: "ts", count: "each", window: { n: 2, unit: "day" }, trim: 0.2, stages: stagesOf(stages) } });  // 따로 세기도 기간 제한, 자르는 몫은 %
    const back = formFromCard(c as Card) as Record<string, unknown>;
    expect([back.fStages, back.fTrim]).toEqual([rowsOf(stagesOf(stages)), "20"]);
    const plain = buildCard({ ...f, fWin: "", fTrim: "" } as Form, aggs);
    expect(plain.source?.analysis).toMatchObject({ window: null });
    expect((plain.source?.analysis as Record<string, unknown>).trim).toBeUndefined();  // 비우면 서버 기본(10%)
  });
  it("자동 흐름 생키는 mode auto·starts·middles·ends(깊이를 비우면 보내지 않음), 조건 줄은 body.conditions", () => {
    const f = { ...emptyForm("sankey"), table: "t", fUser: "u", fStep: "page", fTime: "ts", fFlow: "auto", fStarts: ["home"], fMiddles: ["list"], fEnds: ["buy"], fDepth: "", fConds: "platform = iOS\n" } as unknown as Form;
    const c = buildCard(f, aggs);
    expect(c.source?.analysis).toEqual({ user: "u", step: "page", time: "ts", mode: "auto", starts: ["home"], middles: ["list"], ends: ["buy"] });
    expect((c.source?.body as Record<string, unknown>).conditions).toEqual(["platform = iOS"]);
    const back = formFromCard(c as Card) as Record<string, unknown>;
    expect([back.fFlow, back.fStarts, back.fEnds, back.fDepth, back.fConds]).toEqual(["auto", ["home"], ["buy"], "", "platform = iOS"]);
    const deep = buildCard({ ...f, fDepth: "8" } as Form, aggs);
    expect((deep.source?.analysis as Record<string, unknown>).depth).toBe(8);
    expect(c.view?.flow_dropout).toBeUndefined();  // 이탈 칸 기본(fDropout "")은 view에 쓰지 않는다(끝 값을 골랐는지로 정한다)
    const off = buildCard({ ...f, fDropout: "off" } as unknown as Form, aggs);
    expect(off.view?.flow_dropout).toBe(false);
    expect((formFromCard(off as Card) as Record<string, unknown>).fDropout).toBe("off");
    expect(buildCard({ ...f, fDropout: "on" } as unknown as Form, aggs).view?.flow_dropout).toBe(true);
  });
  it("단계를 적는 생키도 조건 줄을 body.conditions로", () => {
    const c = buildCard({ ...emptyForm("sankey"), table: "t", fUser: "u", fStep: "e", fTime: "ts", fSteps: "a, b", fConds: "platform = iOS" } as unknown as Form, aggs);
    expect((c.source?.body as Record<string, unknown>).conditions).toEqual(["platform = iOS"]);
    expect((c.source?.analysis as Record<string, unknown>).mode).toBeUndefined();
  });
});
