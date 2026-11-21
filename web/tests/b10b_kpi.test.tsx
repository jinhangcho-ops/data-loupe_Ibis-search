// (B10b) KPI 카드 새 모양(그림 1)·KPI 큰 창(그림 2)·카드 몸통을 누르면 큰 창. 순수 계산은 값으로, 고친 컴포넌트는 서버 렌더 글자·코드 글자로 본다. 데이터는 지어낸 것.
// it.fails: 기존 파일을 고쳐야 통과하는 시험. 그 항목(plans/dash-blocks.md B10b 세부 명세)이 끝나면 .fails를 지운다.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ComponentType } from "react";
import type { Card, CardData } from "@/lib/types";
import { PAINTERS } from "@/components/cards/SvgCards";
import { cardValue } from "@/components/cards/svg/__pure__";
import { previousSpan } from "@/lib/calc";
import { compareItem } from "@/lib/series";
import { seriesKit } from "@/components/cards/chart/series";
import { GRID_DASH, ZOOM_TICKS, dayTrend, hourTrend, kpiColor, kpiDelta, sparkParts, tickLabel, trendSpans, unitTrend, zoomRows } from "@/components/cards/svg/kpi_trend";

const src = (p: string) => readFileSync(join(__dirname, "..", p), "utf8");
const MERGE = ["sum", "count"];
const THEME = { card: "#fff", head: "#eee", ink: "#000", text: "#555", line: "#ddd", err: "#c00", ok: "#070", series: ["#111", "#222"], hi: "#333" };
const days = (from: number, n: number, month = "01") => Array.from({ length: n }, (_, i) => `2026-${month}-${String(from + i).padStart(2, "0")}`);
const daily = (labels: string[], sums: number[]) => ({ by_unit: { day: { labels, series: [{ name: "합계", agg: "sum", sum: sums, count: sums.map(() => 1) }] } } }) as unknown as CardData;
const card = (data: CardData, view: Record<string, unknown> = {}, periods?: string[]): Card => ({ id: "k", kind: "kpi", title: "신고 건수", layout: { x: 0, y: 0, w: 4, h: 4 },
  colors: { series: {}, parts: {}, order: [], parts_order: [] }, view: { type: "", unit: "day", compare: { mode: "span" }, better: "up", ...view } as Card["view"],
  source: { body: { series: [{ y: "amt", agg: "sum" }], ...(periods ? { periods } : {}) } }, data });
const SIX = daily(days(1, 6), [1, 2, 3, 4, 5, 6]);
const SPANS = { now: { start: "2026-01-04", end: "2026-01-06" }, prev: { start: "2026-01-01", end: "2026-01-03", text: "직전 3일" } };

describe("큰 창 눈금(사용자 결정)", () => {
  it("월 → 일, 주 → 시간, 분기 → 주, 연 → 월, 없음·일 → 일. 단추는 한 단계 큰 눈금과 둘", () => {
    expect(ZOOM_TICKS).toEqual({ none: ["day", "week"], day: ["day", "week"], week: ["hour", "day"], month: ["day", "week"], quarter: ["week", "month"], year: ["month", "quarter"] });
  });
});

describe("trendSpans", () => {
  it("대시보드 기간이 있으면 그것과 직전 기간", () => {
    expect(trendSpans(card(SIX), SPANS.now, SPANS.prev, "prev")).toEqual(SPANS);
    expect(trendSpans(card(SIX), SPANS.now, null, "prev")).toEqual({ now: SPANS.now, prev: null });
  });
  it("대시보드 기간이 없으면 카드 기간 줄(ownRange), 그것도 없으면 null", () => {
    expect(trendSpans(card(SIX, {}, ["day 2026-03-01 ~ 2026-03-31"]), undefined, undefined, "prev")).toEqual({ now: { start: "2026-03-01", end: "2026-03-31" }, prev: { start: "2026-02-01", end: "2026-02-28", text: "전월" } });
    expect(trendSpans(card(SIX), undefined, undefined, "prev")).toBeNull();
  });
});

describe("dayTrend·unitTrend·hourTrend", () => {
  it("일: 직전 기간 날들과 이번 기간 날들", () => {
    expect(dayTrend(card(SIX), SPANS, MERGE)).toEqual({ prev: [{ key: "2026-01-01", v: 1 }, { key: "2026-01-02", v: 2 }, { key: "2026-01-03", v: 3 }], now: [{ key: "2026-01-04", v: 4 }, { key: "2026-01-05", v: 5 }, { key: "2026-01-06", v: 6 }] });
  });
  it("일: 기간이 없으면 읽어 온 전체가 한 색(now)", () => {
    const t = dayTrend(card(SIX), null, MERGE);
    expect(t.prev).toEqual([]);
    expect(t.now.map(p => p.v)).toEqual([1, 2, 3, 4, 5, 6]);
  });
  it("주: 기간마다 주 칸, 두 기간에 걸친 칸은 이번 기간", () => {
    const kit = seriesKit({ agg_names: {}, merge: MERGE, dash: { types: {}, time_types: [] } } as never);
    const june = card(daily(days(1, 14, "06"), Array(14).fill(1)));  // 2026-06-01은 월요일
    expect(unitTrend(june, { now: { start: "2026-06-08", end: "2026-06-14" }, prev: { start: "2026-06-01", end: "2026-06-07", text: "직전 7일" } }, "week", kit.byUnit))
      .toEqual({ prev: [{ key: "2026-06-01", v: 7 }], now: [{ key: "2026-06-08", v: 7 }] });
    expect(unitTrend(june, { now: { start: "2026-06-04", end: "2026-06-14" }, prev: { start: "2026-05-25", end: "2026-06-03", text: "직전 10일" } }, "week", kit.byUnit))
      .toEqual({ prev: [], now: [{ key: "2026-06-01", v: 7 }, { key: "2026-06-08", v: 7 }] });
  });
  it("시간: 날짜(앞 10자)로 나누고 두 기간 밖은 뺀다", () => {
    const h = { labels: ["2026-06-01T09", "2026-06-02T10", "2026-06-03T00"], values: [1, 2, 3], reason: "" };
    const spans = { now: { start: "2026-06-02", end: "2026-06-02" }, prev: { start: "2026-06-01", end: "2026-06-01", text: "직전 1일" } };
    expect(hourTrend(h, spans)).toEqual({ prev: [{ key: "2026-06-01T09", v: 1 }], now: [{ key: "2026-06-02T10", v: 2 }] });
    expect(hourTrend(h, null).now).toHaveLength(3);
  });
});

describe("sparkParts", () => {
  const t = { prev: [{ key: "a", v: 1 }, { key: "b", v: 2 }], now: [{ key: "c", v: 3 }, { key: "d", v: 4 }] };
  it("직전 선·이번 선(직전 끝 점에서 이어짐)·바탕·끝 점·점선 눈금", () => {
    const p = sparkParts(t, 120, 30)!;
    expect(p.prev).toBe("M2,28 L40.7,19.3");
    expect(p.prevArea).toBe("M2,28 L40.7,19.3 L40.7,30 L2,30 Z");
    expect(p.now.startsWith("M40.7,19.3 ")).toBe(true);
    expect(p.nowArea.endsWith(" L118,30 L40.7,30 Z")).toBe(true);
    expect(p.end).toBe("118.0,2.0");
    expect(p.grid).toEqual([7.5, 15, 22.5]);
  });
  it("직전이 없으면 이번 선만, 값이 2개 미만이면 null", () => {
    const one = sparkParts({ prev: [], now: t.now }, 120, 30)!;
    expect(one.prev).toBe("");
    expect(one.prevArea).toBe("");
    expect(one.now).toBe("M2,28 L118,2");
    expect(sparkParts({ prev: [{ key: "a", v: null }], now: [{ key: "b", v: 5 }] }, 120, 30)).toBeNull();
  });
});

describe("tickLabel·zoomRows", () => {
  it("눈금 글자", () => {
    expect(tickLabel("2026-06-01T13", "hour")).toBe("6/1 13시");
    expect(tickLabel("2026-06-05", "day")).toBe("6/5");
    expect(tickLabel("2026-06-08", "week")).toBe("6/8");
    expect(tickLabel("2026-06-01", "month")).toBe("2026-06");
    expect(tickLabel("2026-04-01", "quarter")).toBe("2026-Q2");
  });
  it("직전 끝 점에 이번 값도 넣어 선이 이어진다", () => {
    const t = { prev: [{ key: "2026-06-01", v: 1 }, { key: "2026-06-02", v: 2 }], now: [{ key: "2026-06-03", v: 3 }] };
    expect(zoomRows(t, "day")).toEqual([
      { key: "2026-06-01", label: "6/1", prev: 1, now: null }, { key: "2026-06-02", label: "6/2", prev: 2, now: 2 }, { key: "2026-06-03", label: "6/3", prev: null, now: 3 }]);
    expect(zoomRows({ prev: [], now: t.now }, "day")).toEqual([{ key: "2026-06-03", label: "6/3", prev: null, now: 3 }]);
  });
});

describe("kpiDelta·kpiColor", () => {
  const said = (pct: number | null, direction: string, more: Record<string, unknown> = {}) => ({ text: "서버 문장", tone: "bad", delta_text: "-34", pct, direction, ...more });
  it("변화율과 방향, 비교 이름", () => {
    expect(kpiDelta(said(-90.8, "down"), "전월", "number")).toBe("▼ 90.8% · 전월 대비");
    expect(kpiDelta(said(1234.56, "up"), "직전 7일", "number")).toBe("▲ 1,234.6% · 직전 7일 대비");
    expect(kpiDelta(said(0.5, "up"), "전월", "number")).toBe("▲ 0.5% · 전월 대비");
    expect(kpiDelta(said(0.004, "up"), "전월", "number")).toBe("▲ 0.01% 미만 · 전월 대비");
    expect(kpiDelta(said(-90.8, "down"), "", "number")).toBe("▼ 90.8%");
  });
  it("비율 형식은 변화량(%p), 변화율이 없거나 같으면 서버 문장", () => {
    expect(kpiDelta(said(10, "up", { delta_text: "+3.5%p" }), "전월", "percent")).toBe("▲ 3.5%p · 전월 대비");
    expect(kpiDelta(said(null, "up"), "전월", "number")).toBe("서버 문장");
    expect(kpiDelta(said(0, "flat"), "전월", "number")).toBe("서버 문장");
  });
  it("색: 카드 지정 색이 먼저", () => {
    const c = { ...card(SIX), colors: { series: { 합계: "#123456" }, parts: {}, order: [], parts_order: [] } };
    expect(kpiColor(c, { series: ["#111"] })).toBe("#123456");
  });
});

describe("KPI 카드(그림 1) — Kpi (수정)", () => {
  const Paint = PAINTERS.kpi as unknown as ComponentType<Record<string, unknown>>;
  const draw = (c: Card, extra: Record<string, unknown> = {}) => renderToStaticMarkup(<Paint card={c} colors={THEME} range={undefined} merge={MERGE} {...extra} />);
  it("값 옆 작은 단위, 아이콘 없음, 점선 눈금 3줄, 직전 기간 회색 선", () => {
    const html = draw(card(SIX, { unit_text: "건" }), { range: SPANS.now, prev: SPANS.prev, mode: "prev" });
    expect(html).toContain('<span class="kpi-unit">건</span>');
    expect(html).not.toContain("kpi-icon");
    expect(html.split(`stroke-dasharray="${GRID_DASH}"`)).toHaveLength(4);
    expect(html).toContain('stroke="var(--muted)"');
    expect(html).toContain("<title>2026-01-01: 1건</title>");  // 직전 기간 날도 마우스를 올리면 그날 값
  });
});

describe("카드 몸통을 누르면 큰 창 — Grid·Pop·Tools (수정)", () => {
  it("보는 곳: KPI는 KpiZoom, 고르기는 제목 줄 체크 단추로만", () => {
    const grid = src("components/grid/Grid.tsx");
    expect(grid).toContain("{big && big.kind === \"kpi\" && <KpiZoom card={big}");
    expect(grid).toContain('<button type="button" className="bcheck" aria-pressed={sel}');
    expect(grid).toContain("onClick={look ? undefined : () => pick(card.id)}");
  });
  it("Pop은 head 자리를 받는다", () => {
    expect(src("components/cards/Pop.tsx")).toContain("{head ?? <>");
  });
  it("KPI 설정의 아이콘·큰 값 크기·추이선 자리 칸이 없다", () => {
    const tools = src("components/edit/panel/Tools.tsx");
    for (const gone of ["아이콘", "큰 값 크기", "추이선 자리"]) expect(tools).not.toContain(`<label className="hint bset">${gone}<`);
  });
});

// (B10b 추가) 카드 기간 줄만 있는 KPI: 서버가 직전 기간 일 값까지 읽어 오고(data.window), 화면은 이번 기간만 큰 값으로.
describe("카드 기간 줄의 직전 기간 읽기 — cardValue (수정)", () => {
  const OWN = ["day 2026-01-04 ~ 2026-01-06"];  // 이번 1-04~1-06, 직전 1-01~1-03
  const wide = card({ ...SIX, window: { start: "2026-01-01", end: "2026-01-06" } }, {}, OWN);
  it("넓게 읽은 카드(data.window)는 대시보드 기간이 없어도 카드 기간 줄만 합친다", () => {
    expect(cardValue(wide, 0, undefined, MERGE)).toBe(15);  // 4+5+6(1~3일은 직전 기간)
  });
  it("넓히지 않은 카드·대시보드 기간이 있는 카드는 지금 그대로", () => {
    expect(cardValue(card(SIX, {}, OWN), 0, undefined, MERGE)).toBe(21);  // data.window가 없으면 읽어 온 전체
    expect(cardValue(wide, 0, { start: "2026-01-02", end: "2026-01-03" }, MERGE)).toBe(5);
  });
  it("추이선은 직전(회색)·이번(색)으로 나뉘고 비교는 직전 기간 일 값으로", () => {
    expect(dayTrend(wide, trendSpans(wide, undefined, undefined, "prev"), MERGE)).toEqual(dayTrend(card(SIX), SPANS, MERGE));
    expect(compareItem(wide, undefined, undefined, MERGE)).toMatchObject({ cur: 15, prev: 6, label: "직전 3일", partial: false });
  });
});

describe("비교 이름 전월 — previousSpan (수정)", () => {
  it("한 달은 전월, 나머지 이름은 그대로", () => {
    expect(previousSpan("2026-03-01", "2026-03-31").text).toBe("전월");
    expect(previousSpan("2026-04-01", "2026-06-30").text).toBe("직전 분기");
    expect(previousSpan("2026-03-04", "2026-03-06").text).toBe("직전 3일");
  });
});
