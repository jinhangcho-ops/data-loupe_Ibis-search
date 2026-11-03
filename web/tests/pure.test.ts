// P1·P14 순수 함수: 링 반지름 나누기, 폭포 받침, 파레토 누적 %, 추세선, 달력 히트맵 배치, 생키 좌표, 달력 고르기 순서
import { describe, expect, it } from "vitest";
import { fitLine, paretoRows, ringRadii, waterfallRows } from "@/components/cards/chart/shapes";
import { calendarCells, flowLayout, sectorPath, sparkY, sunburstArcs, treeRoot, withRest } from "@/components/cards/svg/__pure__";
import { galleryItems } from "@/components/edit/menu/kinds";
import { addMonth, monthCells, pickDay, readSpan } from "@/components/shell/calPick";
import { buildCard, emptyForm, parseBands } from "@/components/edit/menu/build";

describe("새 카드 만들기(D2 입력 규칙)", () => {
  const aggs = ["sum", "count"], row = { label: "", y: "amt", agg: "sum" };
  it("히트맵은 body.x·body.split, 히스토그램은 body.x = 첫 값 컬럼과 구간 수", () => {
    const h = buildCard({ ...emptyForm("heatmap"), table: "t", rows: [row], group: "store", split: "kind" }, aggs);
    expect(h.source?.body).toMatchObject({ x: "store", split: "kind", series: [{ y: "amt", agg: "sum" }] });
    const g = buildCard({ ...emptyForm("histogram"), table: "t", rows: [row], bins: "500" }, aggs);
    expect(g.source?.body).toMatchObject({ x: "amt" });
    expect(g.view?.bins).toBe(100);
    expect(() => buildCard({ ...emptyForm("heatmap"), table: "t", rows: [row], group: "store" }, aggs)).toThrow("열 컬럼을 고르세요.");
  });
  it("생키는 source.analysis {user, step, time, steps}, 코호트는 {user, time}과 view.unit", () => {
    const s = buildCard({ ...emptyForm("sankey"), table: "t", fUser: "u", fStep: "e", fTime: "ts", fSteps: "view, cart" }, aggs);
    expect(s.source).toMatchObject({ date: "ts", analysis: { user: "u", step: "e", time: "ts", steps: ["view", "cart"] } });
    expect(s.source?.body).toBeTruthy();
    const c = buildCard({ ...emptyForm("cohort"), table: "t", fUser: "u", fTime: "ts", cUnit: "week" }, aggs);
    expect(c.source?.analysis).toEqual({ user: "u", time: "ts" });
    expect(c.view?.unit).toBe("week");
    expect(() => buildCard({ ...emptyForm("sankey"), table: "t", fUser: "u", fStep: "e", fTime: "ts", fSteps: "view" }, aggs)).toThrow("2~10개");
  });
  it("순위 변화는 body.x = 날짜, body.split = 대상, source.date = 날짜", () => {
    const b = buildCard({ ...emptyForm("bump"), table: "t", rows: [row], date: "d", group: "store", cUnit: "month" }, aggs);
    expect(b.source).toMatchObject({ date: "d", body: { x: "d", split: "store" } });
    expect(b.view?.unit).toBe("month");
  });
  it("불릿 구간은 오름차순 숫자 1~6개", () => {
    expect(parseBands("50, 80,100")).toEqual([50, 80, 100]);
    expect(() => parseBands("80, 50")).toThrow();
    expect(() => parseBands("1,2,3,4,5,6,7")).toThrow();
  });
});

describe("겹친 도넛 링", () => {
  it("안쪽부터 고르게 나누고 틈을 둔다", () => {
    const r = ringRadii(3, 30, 95, 2);
    expect(r).toHaveLength(3);
    expect(r[0].inner).toBe(30);
    expect(r[2].outer).toBeCloseTo(95);
    expect(r[1].inner - r[0].outer).toBeCloseTo(2);
    expect(r[0].outer - r[0].inner).toBeCloseTo(r[2].outer - r[2].inner);
  });
  it("링 하나는 전체, 0개는 빈 목록", () => {
    expect(ringRadii(1, 0, 95)).toEqual([{ inner: 0, outer: 95 }]);
    expect(ringRadii(0, 0, 95)).toEqual([]);
  });
});

describe("폭포", () => {
  it("받침은 이전 누적과 이번 누적 중 작은 쪽", () => {
    expect(waterfallRows([10, -4, 6, null])).toEqual([
      { base: 0, size: 10, up: true, end: 10 }, { base: 6, size: 4, up: false, end: 6 }, { base: 6, size: 6, up: true, end: 12 }, { base: 12, size: 0, up: true, end: 12 },
    ]);
  });
  it("누적이 음수로 내려가도 범위가 맞다", () => {
    const r = waterfallRows([-5, 2]);
    expect(r[0]).toMatchObject({ base: -5, size: 5, up: false });
    expect(r[1]).toMatchObject({ base: -5, size: 2, up: true, end: -3 });
  });
});

describe("파레토", () => {
  it("큰 값부터, 누적 %는 100에서 끝난다", () => {
    const r = paretoRows(["a", "b", "c"], [20, 50, 30]);
    expect(r.map(x => x.label)).toEqual(["b", "c", "a"]);
    expect(r.map(x => x.pct)).toEqual([50, 80, 100]);
  });
  it("합이 0이면 0%", () => expect(paretoRows(["a"], [0])[0].pct).toBe(0));
  it("음수는 0으로 봐서 누적이 100을 넘지 않는다(리뷰: 250%였음)", () => expect(paretoRows(["a", "b"], [50, -30]).map(x => x.pct)).toEqual([100, 100]));
});

describe("추세선", () => {
  it("y = 1 + 2i", () => { const f = fitLine([1, 3, null, 7])!; expect(f.a).toBeCloseTo(1); expect(f.b).toBeCloseTo(2); });
  it("점이 하나면 null", () => expect(fitLine([5, null])).toBeNull());
});

describe("달력 히트맵 배치", () => {
  it("월요일 시작: 2026-11-01(일)은 첫 열 마지막 줄, 다음 날은 둘째 열 첫 줄", () => {
    const { cells, weeks, months } = calendarCells([{ day: "2026-10-31", v: 1 }, { day: "2026-11-01", v: 2 }, { day: "2026-11-02", v: 3 }]);
    expect(cells.map(c => [c.col, c.row])).toEqual([[0, 5], [0, 6], [1, 0]]);
    expect(weeks).toBe(2);
    expect(months).toEqual([{ col: 0, label: "11월" }]);  // 같은 열에서 달이 바뀌면 새 달 이름
  });
  it("일요일 시작이면 2026-11-01(일)이 새 열 첫 줄", () => {
    const { cells } = calendarCells([{ day: "2026-10-31", v: 1 }, { day: "2026-11-01", v: 2 }], true);
    expect(cells.map(c => [c.col, c.row])).toEqual([[0, 6], [1, 0]]);
  });
  it("빈 날 목록", () => expect(calendarCells([]).weeks).toBe(0));
  it("빠진 날은 v null 칸으로 채우고 달이 바뀐 주에 새 달 이름(리뷰: 2월 이름이 없었음)", () => {
    const { cells, months } = calendarCells([{ day: "2026-02-03", v: 2 }, { day: "2026-01-30", v: 1 }]);
    expect(cells.map(c => c.day)).toEqual(["2026-01-30", "2026-01-31", "2026-02-01", "2026-02-02", "2026-02-03"]);
    expect(cells[2].v).toBeNull();
    expect(months).toEqual([{ col: 0, label: "2월" }]);  // 2026-02-01(일)이 첫 열에 있어 그 열은 2월
  });
});

describe("생키 좌표", () => {
  const flow = { nodes: ["방문", "장바구니", "결제", "이탈"], links: [{ s: 0, t: 1, v: 60 }, { s: 0, t: 3, v: 40 }, { s: "장바구니", t: "결제", v: 30 }] };
  const g = flowLayout(flow, { width: 300, height: 120, pad: 10, nodeW: 10, gap: 10 });
  it("열은 가장 긴 거리, 첫 노드는 왼쪽·끝 노드는 오른쪽", () => {
    expect(g.nodes[0].x).toBe(10);
    expect(g.nodes[2].x).toBe(280);
    expect(g.nodes[1].x).toBe(g.nodes[3].x);
  });
  it("노드 높이는 인원에 비례하고 띠는 노드 안에 차례로 쌓인다", () => {
    expect(g.nodes[0].h).toBeCloseTo(g.nodes[1].h + g.nodes[3].h);
    expect(g.links[1].y0).toBeCloseTo(g.links[0].y0 + g.links[0].h);
    expect(g.links[2].y1).toBe(g.nodes[2].y);
    expect(g.links[0].path.startsWith("M20,10 C")).toBe(true);
  });
  it("자기 자신·0명·없는 노드 링크는 뺀다", () => {
    expect(flowLayout({ nodes: ["a"], links: [{ s: 0, t: 0, v: 3 }, { s: 0, t: 5, v: 1 }] }).links).toEqual([]);
  });
});

describe("스파크 목표선", () => {
  it("값이 한결같으면 목표가 위면 위 끝, 아래면 아래 끝, 같으면 가운데(리뷰: 늘 가운데였음)", () => {
    expect(sparkY([5, 5, 5], 100, 30)).toBe(1);
    expect(sparkY([5, 5, 5], 1, 30)).toBe(29);
    expect(sparkY([5, 5, 5], 5, 30)).toBe(15);
  });
  it("범위 밖 목표는 끝에 붙는다", () => {
    expect(sparkY([0, 10], 5, 30)).toBeCloseTo(15);
    expect(sparkY([0, 10], 100, 30)).toBe(1);
  });
});

describe("기간 달력", () => {
  it("첫 클릭 시작, 둘째 클릭 끝, 역순이면 바꾼다", () => {
    let s = pickDay({ start: "", end: "" }, "2026-10-20");
    expect(s).toEqual({ start: "2026-10-20", end: "", done: false });
    s = pickDay(s, "2026-10-05");
    expect(s).toEqual({ start: "2026-10-05", end: "2026-10-20", done: true });
    expect(pickDay(s, "2026-11-01")).toEqual({ start: "2026-11-01", end: "", done: false });  // 다 고른 뒤에는 새로 시작
  });
  it("달 칸: 2026-11은 일요일에 시작해 월요일 시작이면 빈 칸 6개", () => {
    const c = monthCells("2026-11");
    expect(c.slice(0, 7)).toEqual([null, null, null, null, null, null, "2026-11-01"]);
    expect(c.filter(Boolean)).toHaveLength(30);
    expect(monthCells("2026-11", true)[0]).toBe("2026-11-01");
  });
  it("달 넘기기와 기간 글자 읽기", () => {
    expect(addMonth("2026-12", 1)).toBe("2027-01");
    expect(addMonth("2026-01", -1)).toBe("2025-12");
    expect(readSpan("2026-10-01 ~ 2026-10-31")).toEqual({ start: "2026-10-01", end: "2026-10-31" });
    expect(readSpan("최근 7일")).toEqual({ start: "", end: "" });
  });
});

describe("트리맵·선버스트", () => {
  it("뿌리 값은 첫 단계 합(리뷰: 뿌리 value가 없어 각도 NaN·빈 카드)", () => {
    const root = treeRoot("전체", [{ name: "A", value: 220 }, { name: "B", value: 80 }]);
    expect(root.value).toBe(300);
    const arcs = sunburstArcs(root);
    expect(arcs.every(a => Number.isFinite(a.a0) && Number.isFinite(a.a1))).toBe(true);
    expect(arcs.at(-1)!.a1).toBeCloseTo(Math.PI * 2);
  });
  it("아이들 합이 모자라면 기타, 0 이하는 뺀다", () => {
    const items = withRest([{ name: "A", value: 220, children: [{ name: "view", value: 140 }, { name: "x", value: 0 }] }, { name: "B", value: 0 }]);
    expect(items).toEqual([{ name: "A", value: 220, children: [{ name: "view", value: 140 }, { name: "기타", value: 80 }] }]);
    const arcs = sunburstArcs(treeRoot("t", items));
    expect(arcs.filter(a => a.depth === 2).at(-1)!.a1).toBeCloseTo(arcs[0].a1);  // 바깥 고리가 부모 각도에 꼭 맞는다
  });
  it("조각 경로: 한 바퀴는 반씩 둘, 숫자만", () => {
    const d = sectorPath(100, 100, 40, 80, 0, Math.PI * 2);
    expect(d.match(/M/g)).toHaveLength(2);
    expect(d).not.toContain("NaN");
  });
  it("뿌리 값이 0이면 조각 없음", () => expect(sunburstArcs(treeRoot("t", []))).toEqual([]));
});

describe("카드 모음", () => {
  it("퍼널·생키·코호트도 카드 모음에 있다(빈 틀로 놓고 설정에서 사용자·단계·시각을 정한다)", () => {
    const kinds = galleryItems({ bar: "막대" }).map(t => t.kind);
    for (const k of ["funnel", "sankey", "cohort", "histogram"]) expect(kinds).toContain(k);
  });
});
