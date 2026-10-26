"""static/dash.js의 계산 부분(일 값 다시 묶기, 색 정하기, 종류 고르기, 차트 설정 9종)을 node로 돌려 본다. node가 없으면 건너뛴다.
board.js와 dash.js를 이어 붙이고 서버가 주는 SERVER.dash만 흉내 낸다. 값은 손으로 계산한 작은 예제다."""
import json
import shutil
import subprocess

import pytest

from dashboard import dash_calc
from core import dash_store
from core import sql_funcs
from conftest import ROOT

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

SCRIPT = """
const assert = require("assert");
globalThis.SERVER = { dash: JSON.parse(%(dash)s), merge: JSON.parse(%(merge)s), agg_names: {} };
const by = { day: { labels: ["2026-01-05", "2026-01-06", "2026-01-12", "2026-02-02"], series: [
  { name: "합계", agg: "sum", sum: [30, 35, 40, 50], count: [2, 2, 1, 1], min: [10, 5, 40, 50], max: [20, 30, 40, 50] },
  { name: "평균", agg: "mean", sum: [30, 35, 40, 50], count: [2, 2, 1, 1] },
  { name: "개수", agg: "count", count: [2, 2, 1, 2] },
  { name: "최소", agg: "min", min: [10, 5, 40, 50] },
  { name: "고유", agg: "nunique", values: [1, 2, 1, 2] }] },
  week: { labels: ["2026-01-05", "2026-01-12", "2026-02-02"], series: [{ name: "고유", values: [3, 1, 2] }] },
  month: { labels: ["2026-01-01", "2026-02-01"], series: [{ name: "고유", values: [3, 2] }] },
  year: { labels: ["2026-01-01"], series: [{ name: "고유", values: [5] }] } };
const rows = unit => Object.fromEntries(byUnit(by, unit).datasets.map(d => [d.label, d.values]));
const fc = () => ({ series: {}, parts: {}, order: [], parts_order: [] });

// 주(월요일 시작)·월·연으로 다시 묶기. sum=Σsum, count=Σcount, mean=Σsum/Σcount, min=min, 고유 개수는 서버 값
assert.deepStrictEqual(byUnit(by, "week").labels, ["2026-01-05", "2026-01-12", "2026-02-02"]);
assert.deepStrictEqual(rows("week"), { 합계: [65, 40, 50], 평균: [65 / 4, 40, 50], 개수: [4, 1, 2], 최소: [5, 40, 50], 고유: [3, 1, 2] });
assert.deepStrictEqual(byUnit(by, "month").labels, ["2026-01", "2026-02"]);
assert.deepStrictEqual(rows("month"), { 합계: [105, 50], 평균: [21, 50], 개수: [5, 2], 최소: [5, 50], 고유: [3, 2] });
assert.deepStrictEqual(byUnit(by, "year").labels, ["2026"]);
assert.deepStrictEqual(rows("year"), { 합계: [155], 평균: [155 / 6], 개수: [7], 최소: [5], 고유: [5] });
assert.deepStrictEqual(rows("day"), { 합계: [30, 35, 40, 50], 평균: [15, 17.5, 40, 50], 개수: [2, 2, 1, 2], 최소: [10, 5, 40, 50], 고유: [1, 2, 1, 2] });
assert.strictEqual(monday("2026-01-11"), "2026-01-05");  // 일요일 → 그 주 월요일
assert.strictEqual(monday("2026-01-12"), "2026-01-12");
assert.strictEqual(monday("2024-02-29"), "2024-02-26");  // 윤년
assert.strictEqual(monday("2026-01-01"), "2025-12-29");  // 해를 넘는 주

// 값이 빈 날은 건너뛴다. 모두 비면 빈 값
const holes = { day: { labels: ["2026-03-01", "2026-03-02"], series: [{ name: "합계", agg: "sum", sum: [null, 7], count: [0, 1], min: [null, 7], max: [null, 7] }] } };
assert.deepStrictEqual(byUnit(holes, "month").datasets[0].values, [7]);
const empty = { day: { labels: ["2026-03-01"], series: [{ name: "합계", agg: "sum", sum: [null], count: [0], min: [null], max: [null] }] } };
assert.deepStrictEqual(byUnit(empty, "week").datasets[0].values, [null]);

// 일 값을 자른 카드: 월·연은 서버가 구한 값을 쓴다(잘린 앞부분까지)
const cut = { day: { labels: ["2026-02-02"], series: [{ name: "합계", agg: "sum", sum: [50], count: [1], min: [50], max: [50] }] },
  month: { labels: ["2026-01-01", "2026-02-01"], series: [{ name: "합계", values: [105, 50] }] } };
assert.deepStrictEqual(byUnit(cut, "month").datasets[0].values, [105, 50]);
assert.deepStrictEqual(byUnit(cut, "week").datasets[0].values, [50]);  // 주는 남은 일 값으로

// 색 정하기: 카드 지정 색 → 팔레트 번호째 → 기본 색. 라벨 순서는 카드에 남는다.
const theme = { series: ["#100000", "#200000", "#300000", "#400000"], card: "#ffffff", text: "#000000", line: "#cccccc", ink: "#000000" };
const colors = { ...theme, palette: ["#111111", "#222222"] };
const card = { colors: { ...fc(), series: { 합계: "#abc" } }, view: {} };
assert.strictEqual(pickColor(card, "series", "합계", colors), "#abc");  // 지정 색이 먼저(번호 0을 차지)
assert.strictEqual(pickColor(card, "series", "평균", colors), "#222222");
assert.strictEqual(pickColor(card, "series", "개수", colors), "#300000");  // 팔레트를 넘으면 기본 색의 같은 번호
assert.deepStrictEqual(card.colors.order, ["합계", "평균", "개수"]);
const kept = { colors: { ...fc(), order: ["개수", "평균"] }, view: {} };  // 저장된 순서를 따른다(지금 나온 순서가 아니라)
assert.strictEqual(pickColor(kept, "series", "평균", colors), "#222222");
assert.strictEqual(pickColor(kept, "series", "개수", colors), "#111111");
assert.strictEqual(pickColor({ view: {} }, "series", "x", theme, 3), "#400000");  // 임시 카드는 계열 순서
assert.strictEqual(pickColor(card, "parts", "조각", colors), "#111111");  // 조각은 따로 센다

// HEX 검사, 알파
for (const ok of ["#fff", "#FFF", "#11706f", "#11706F"]) assert(HEX.test(ok), ok);
for (const bad of ["fff", "#ff", "#ffff", "#12345", "#1234567", "#ggg", "red", ""]) assert(!HEX.test(bad), bad);
assert.strictEqual(hex6("#abc"), "#aabbcc");
assert.strictEqual(alpha("#abc", 0.5), "rgba(170, 187, 204, 0.5)");

// 종류 고르기: 데이터 모양으로 막고, 이유를 돌려준다
const time = { kind: "chart", colors: fc(), view: {}, data: { by_unit: by } };
const cat = (n, view = {}) => ({ kind: "chart", colors: fc(), view,
  data: { values: { labels: ["a", "b", "c"], series: Array.from({ length: n }, (_, i) => ({ name: "s" + i, values: [1, 2, 3] })) } } });
for (const t of ["bar", "stacked", "line", "polyline", "area"]) assert.strictEqual(typeReason(time, t), "", t);
for (const t of ["hbar", "doughnut", "pie", "radar"]) assert.match(typeReason(time, t), /날짜 X축 그림에는 막대·누적 막대·라인·꺾인 선·영역형 라인만/, t);
for (const t of Object.keys(SERVER.dash.types)) if (!["stacked", "scatter", "bubble"].includes(t)) assert.strictEqual(typeReason(cat(2), t), "", t);
assert.match(typeReason(cat(2), "scatter"), /X가 숫자 컬럼이고 값이 숫자 컬럼일 때만/);  // X가 글자 컬럼
const num = (view = {}) => ({ ...cat(1), view, source: { body: { x: "x", series: [{ y: "y", agg: "sum" }] } }, data: { x_kind: "num", num_cols: ["x", "y", "s"], values: cat(1).data.values } });
assert.strictEqual(typeReason(num(), "scatter"), "");
assert.match(typeReason(num(), "bubble"), /크기.에 숫자 컬럼/);
assert.strictEqual(typeReason(num({ points: { size: "s" } }), "bubble"), "");
assert.match(typeReason({ ...num(), data: { ...num().data, num_cols: ["x"] } }, "scatter"), /숫자 컬럼일 때만/);  // 값이 숫자 컬럼이 아님
assert.match(typeReason({ ...num(), data: { ...num().data, x_kind: "date" } }, "scatter"), /숫자 컬럼일 때만/);
assert.match(typeReason(cat(1), "stacked"), /계열이 2개 이상/);
assert.strictEqual(typeReason(cat(2), "stacked"), "");
assert.match(typeReason({ kind: "chart", colors: fc(), view: {}, data: {} }, "bar"), /아직 값이 없어서/);
const hist = { kind: "analysis", colors: fc(), view: { analysis_view: "hist" }, data: { values: { kind: "distribution", histogram: { edges: [0, 1], counts: [3] }, column: "x" } } };
assert.match(typeReason(hist, "bar"), /종류를 바꿀 수 없어요/);
const groups = { kind: "analysis", colors: fc(), view: { analysis_view: "groups" }, data: { values: { kind: "groups", by: "g", value: "v", agg: "sum", labels: ["a", "b"], values: [1, 2] } } };
assert.strictEqual(typeReason(groups, "pie"), "");
const trend = { ...groups, view: { analysis_view: "trend" }, data: { values: { ...groups.data.values, kind: "trend", unit: "day" } } };
assert.match(typeReason(trend, "pie"), /날짜 X축/);
assert.strictEqual(typeReason(trend, "area"), "");

// 차트 설정: 9종류
const make = (view, n = 2) => chartConfig({ ...cat(n), view }, colors);
assert.strictEqual(make({ type: "bar" }).type, "bar");
const hbar = make({ type: "hbar", ranges: { y: { min: 5 }, y1: {} } });
assert.strictEqual(hbar.type, "bar"); assert.strictEqual(hbar.options.indexAxis, "y"); assert.strictEqual(hbar.options.scales.x.min, 5);  // 값 축은 가로
const stacked = make({ type: "stacked" });
assert.strictEqual(stacked.type, "bar"); assert(stacked.options.scales.x.stacked && stacked.options.scales.y.stacked);
const line = make({ type: "line" }), poly = make({ type: "polyline" }), area = make({ type: "area" });
assert.deepStrictEqual([line.type, poly.type, area.type], ["line", "line", "line"]);
assert.deepStrictEqual([line.data.datasets[0].tension, poly.data.datasets[0].tension], [0.3, 0]);
assert.deepStrictEqual([line.data.datasets[0].fill, area.data.datasets[0].fill], [false, true]);
assert.strictEqual(area.data.datasets[0].backgroundColor, alpha("#111111", 0.14));  // 영역형 라인은 옅은 색(알파 0.14)
assert.strictEqual(area.data.datasets[0].borderColor, "#111111");
for (const t of ["doughnut", "pie"]) {
  const c = make({ type: t });
  assert.strictEqual(c.type, t); assert.strictEqual(c.data.datasets.length, 1);  // 첫 계열만
  assert.strictEqual(c.data.datasets[0].backgroundColor.length, 3); assert(c.options.plugins.legend.display);
}
const radar = make({ type: "radar" });
assert.strictEqual(radar.type, "radar"); assert.strictEqual(radar.data.datasets.length, 2); assert(radar.options.scales.r);
// 종류를 정하지 않으면 계열마다 정한 대로(막대·선 겹치기, 점선, 오른쪽 축, 세로축 범위)
const mixed = make({ styles: [{ type: "line", dash: "dashed", axis: "right" }, { type: "bar" }], ranges: { y: { max: 9 }, y1: { min: 1 } } });
assert.deepStrictEqual(mixed.data.datasets.map(d => d.type), ["line", "bar"]);
assert.deepStrictEqual(mixed.data.datasets[0].borderDash, [6, 4]); assert.strictEqual(mixed.data.datasets[0].yAxisID, "y1");
assert.strictEqual(mixed.options.scales.y.max, 9); assert.strictEqual(mixed.options.scales.y1.min, 1);
// 시간별 카드는 단위로 다시 묶어 그린다
const month = chartConfig({ ...time, view: { type: "line", unit: "month" } }, colors);
assert.deepStrictEqual(month.data.labels, ["2026-01", "2026-02"]); assert.deepStrictEqual(month.data.datasets[0].data, [105, 50]);
// 지정 색은 라이트·다크 같고, 글자·축·격자는 테마 색
const custom = chartConfig({ ...cat(1), colors: { ...fc(), series: { s0: "#abcdef" } }, view: { type: "bar" } }, colors);
assert.strictEqual(custom.data.datasets[0].backgroundColor, "#abcdef"); assert.strictEqual(custom.options.scales.x.ticks.color, "#000000"); assert.strictEqual(custom.options.scales.x.grid.color, "#cccccc");
// 분석 그림은 주 색·보조 색을 팔레트에서
const anaHist = chartConfig(hist, colors);
assert.strictEqual(anaHist.type, "bar"); assert.strictEqual(anaHist.data.datasets[0].backgroundColor, "#111111");
const anaGroups = chartConfig({ ...groups, view: { analysis_view: "groups", type: "pie" } }, colors);
assert.strictEqual(anaGroups.type, "pie"); assert.deepStrictEqual(anaGroups.data.labels, ["a", "b"]);
// 예전 탐색 차트처럼 이미 그릴 모양(norm)이 있는 임시 카드
const drawn = chartConfig({ view: { type: "", ranges: { y: {}, y1: {} } }, norm: { labels: ["a"], datasets: [{ label: "x", values: [1], type: "bar", axis: "left" }] } }, theme);
assert.strictEqual(drawn.data.datasets[0].backgroundColor, "#100000");
// 산점도·버블: 점·크기 범위·추세선·r
const points = { x: "x", y: "y", size: "s", split: "g", groups: ["a", "b"], total: 4, n: 4, r: 0.96474, a: -0.5, b: 2.2, size_range: [10, 40],
  points: [[1, 2, 10, "a"], [2, 4, 20, "b"], [3, 5, 30, "a"], [4, 9, 40, "a"]] };
const sc = chartConfig({ ...cat(1), view: { type: "scatter", trend: true, show_r: true }, data: { points } }, colors);
assert.strictEqual(sc.type, "scatter"); assert.deepStrictEqual(sc.data.datasets.map(d => d.label), ["a", "b", "추세선"]);
assert.deepStrictEqual(sc.data.datasets[0].data, [{ x: 1, y: 2 }, { x: 3, y: 5 }, { x: 4, y: 9 }]);
assert.deepStrictEqual(sc.data.datasets[2].data, [{ x: 1, y: 1.7000000000000002 }, { x: 4, y: 8.3 }]);  // a + b·x
assert.strictEqual(sc.options.plugins.title.text, "r = 0.965 (4개)"); assert.strictEqual(sc.options.scales.x.title.text, "x");
const bub = chartConfig({ ...cat(1), view: { type: "bubble" }, data: { points } }, colors);
assert.strictEqual(bub.type, "bubble"); assert.strictEqual(bub.data.datasets.length, 2); assert(!bub.options.plugins.title.display);
assert.strictEqual(bub.data.datasets[0].data[0].r, 3);  // 최소 크기는 3px
assert.strictEqual(bub.data.datasets[0].data[2].r, 20);  // 최대 크기는 3 + 17 = 20px
assert.strictEqual(bub.data.datasets[1].data[0].r, 3 + 17 * Math.sqrt(10 / 30));  // 면적 비례
const one = chartConfig({ ...cat(1), view: { type: "scatter" }, data: { points: { ...points, groups: [], split: "", points: points.points.map(r => r.slice(0, 3)) } } }, colors);
assert.deepStrictEqual(one.data.datasets.map(d => d.label), ["y"]); assert(!one.options.plugins.legend.display);
// 기간·비교: 보는 기간으로 자르기, 구간 값, 시점 값, 비교할 두 값(기간 비교·시점 비교, 직전이 저장된 값보다 앞서면 partial)
assert.deepStrictEqual(byUnit(by, "day", { start: "2026-01-06", end: "2026-01-31" }).labels, ["2026-01-06", "2026-01-12"]);
assert.deepStrictEqual(byUnit(by, "month", { start: "2026-01-06", end: "2026-01-31" }).labels, ["2026-01"]);
assert.deepStrictEqual(byUnit(by, "day", { start: null, end: "2026-01-06" }).labels, ["2026-01-05", "2026-01-06"]);  // 열린 시작
const days = by.day.labels, [sum, mean] = by.day.series;
assert.strictEqual(windowValue(sum, days, "2026-01-05", "2026-01-12"), 105);
assert.strictEqual(windowValue(mean, days, "2026-01-05", "2026-01-12"), 21);
assert.strictEqual(windowValue(sum, days, "2025-01-01", "2025-12-31"), null);
assert.strictEqual(pointValue(sum, days, "2026-01-10"), 35);  // 그날 값이 없으면 그 전 마지막 값
assert.strictEqual(pointValue(sum, days, "2026-01-01"), null);
assert.strictEqual(shiftDay("2026-03-01", -1), "2026-02-28"); assert.strictEqual(shiftDay("2024-03-01", -1), "2024-02-29");
assert.strictEqual(daysBetween("2026-01-12", "2026-02-02"), 22);
const range = { start: "2026-01-12", end: "2026-02-02" }, prev = { start: "2025-12-21", end: "2026-01-11", text: "직전 22일" };
assert.deepStrictEqual(compareItem(time, range, prev), { cur: 90, prev: 65, mode: "span", label: "직전 22일", better: "up", partial: true });
const pointCard = { ...time, view: { compare: { mode: "point" }, better: "down" } };
assert.deepStrictEqual(compareItem(pointCard, range, null), { cur: 50, prev: 35, mode: "point", label: "22일 전", better: "down", partial: false });
assert.strictEqual(compareItem(time, null, prev), null); assert.strictEqual(compareItem(cat(1), range, prev), null);
assert.strictEqual(compareItem({ ...time, data: { by_unit: { day: { ...by.day, series: [by.day.series[4]] } } } }, range, prev), null);  // 고유 개수는 다시 묶을 수 없다(서버 값이 없으면 비교 없음)
const uniq = { ...time, data: { by_unit: { day: { ...by.day, series: [by.day.series[4]] } }, values: { window_nunique: { ...range, values: { 0: 3 }, prev: { start: prev.start, end: prev.end, values: { 0: 1 } } } } } };
assert.deepStrictEqual(compareItem(uniq, range, prev), { cur: 3, prev: 1, mode: "span", label: "직전 22일", better: "up", partial: true });  // 고유 개수: 서버가 두 기간으로 따로 구한 값
assert.strictEqual(compareItem(uniq, range, { ...prev, start: "2025-12-22" }), null);  // 기간이 다르면(옛 값) 비교 없음
assert.strictEqual(compareItem({ ...uniq, view: { compare: { mode: "point" } } }, range, prev), null);  // 시점 비교는 안 한다
assert.deepStrictEqual(chartConfig({ ...time, range: { start: "2026-01-12", end: "2026-02-02" }, view: { type: "line", unit: "day" } }, colors).data.labels, ["2026-01-12", "2026-02-02"]);
// 3단계 카드의 모양 계산(dash_cards.js): 호·고리·게이지 조각·칸·블록·교차·다각형·값 글자
const near = (a, b) => assert(Math.abs(a - b) < 1e-9, `${a} != ${b}`);
const [px, py] = polar(0, 0, 10, Math.PI / 2); near(px, 10); near(py, 0);
assert.deepStrictEqual(polar(0, 0, 10, 0), [0, -10]);
assert.match(arcPath(0, 0, 10, 0, Math.PI / 2), /^M 0[.]00 -10[.]00 A 10 10 0 0 1 10[.]00 -?0[.]00$/);
assert.match(arcPath(0, 0, 10, 0, Math.PI * 1.5), / A 10 10 0 1 1 /);  // 반 바퀴를 넘으면 큰 호
assert.strictEqual(ringDash(10, 0.25).dash, "15.71 47.12"); assert.strictEqual(ringDash(10, 2).len, ringDash(10, 1).circ); assert.strictEqual(ringDash(10, -1).len, 0);
const segs = gaugeSegments([1, 1, 2, 0, null]);
assert.deepStrictEqual(segs.map(g => [g.index, g.share, g.max]), [[0, 0.25, false], [1, 0.25, false], [2, 0.5, true]]);  // 몫대로, 0·빈 값은 뺌, 가장 큰 항목에 max
near(segs[1].a0, segs[0].a1 + RING_GAP); near(segs[2].a1 + RING_GAP, TAU);  // 조각 사이 간격 0.06rad, 한 바퀴를 채운다
assert.deepStrictEqual(gaugeSegments([0, null]), []);
assert.deepStrictEqual(stripeCells(0.34, 10, 0.5), { filled: 3, target: 5 }); assert.deepStrictEqual(stripeCells(1.7, 10, null), { filled: 10, target: null }); assert.strictEqual(stripeCells(0, 10, 0.01).target, 1);
assert.deepStrictEqual(blockCells(45, 80, 10), { actual: 4, toTarget: 4, over: 0, scale: 1 });
assert.deepStrictEqual(blockCells(95, 80, 10), { actual: 9, toTarget: 0, over: 1, scale: 1 });  // 목표를 넘은 칸
assert.deepStrictEqual(blockCells(10000, null, 1, 200), { actual: 200, toTarget: 0, over: 0, scale: 50 });  // 칸이 너무 많으면 줄인다
assert.strictEqual(crossDown([5, 3, 6, 2, null, 1], 4), 2); assert.strictEqual(crossDown([1, 2, 3], 4), 0); assert.strictEqual(crossDown([4, 3], 4), 1);  // 목표와 같으면 아직 안 내려간 것
const yOf = v => 100 - v * 10;
assert.deepStrictEqual(belowPolygons([0, 10, 20, 30], [6, 2, 2, 6], 4, yOf), [[[5, 60], [10, 80], [20, 80], [25, 60]]]);  // 목표선(y=60)과 선분이 만나는 곳에서 시작·끝
assert.deepStrictEqual(belowPolygons([0, 10], [2, 6], 4, yOf), [[[0, 60], [0, 80], [5, 60]]]);  // 처음부터 아래
assert.deepStrictEqual(belowPolygons([0, 10], [5, 6], 4, yOf), []);
assert.strictEqual(sparkPoints([1, 2, 3], 10, 10, 0), "0.0,10.0 5.0,5.0 10.0,0.0"); assert.strictEqual(sparkPoints([2, null, 2], 10, 10, 0), "0.0,5.0 10.0,5.0");
assert.strictEqual(showValue(null), "—"); assert.strictEqual(showValue(0.256, "percent"), "25.6%"); assert.strictEqual(showValue(1234.5, "currency"), "₩1,235"); assert.strictEqual(showValue(1234.567), "1,234.57"); assert.strictEqual(showValue(3, "number", "건"), "3건");
assert.strictEqual(showGap(-0.017, "percent"), "1.7%p"); assert.strictEqual(showGap(-1500, "currency"), "₩1,500");
assert.strictEqual(darken("#ffffff", 0.5), "#808080"); assert.strictEqual(darken("#fff", 0), "#ffffff");
const kpi = { kind: "kpi", colors: fc(), view: {}, data: { by_unit: by, target: { value: 100 } }, range: { start: "2026-01-12", end: "2026-02-02" } };
assert.strictEqual(cardValue(kpi), 90); assert.strictEqual(cardValue({ ...kpi, range: null }), 155); assert.strictEqual(cardTarget(kpi), 100); assert.strictEqual(cardTarget({ ...kpi, data: {}, view: { target: { value: 7 } } }), 7);
// 합계 계열이라 빈 날은 0으로 채운다(data.window가 없으면 일 값의 첫 날~끝 날 안에서만, 11차 리뷰 D9)
const kd = cardDays(kpi);
assert.strictEqual(kd.length, 22); assert.deepStrictEqual([kd[0], kd[1], kd[21]], [{ day: "2026-01-12", v: 40 }, { day: "2026-01-13", v: 0 }, { day: "2026-02-02", v: 50 }]);
const kw = cardDays({ ...kpi, view: { window: { from: "2026-02-01", to: "2026-02-28" } } });  // 보는 구간
assert.deepStrictEqual(kw, [{ day: "2026-02-01", v: 0 }, { day: "2026-02-02", v: 50 }]);
assert.deepStrictEqual(cardDays({ ...kpi, data: { ...kpi.data, by_unit: { day: { ...by.day, series: [by.day.series[1]] } } } }).map(d => d.day), ["2026-01-12", "2026-02-02"]);  // 평균은 있는 날만
assert.strictEqual(cardValue({ kind: "kpi", data: { values: { scalar: [7, 8] } } }, 1), 8); assert.strictEqual(cardFormat({ view: {}, data: { values: { format: "percent" } } }), "percent");
assert(!noValues({ kind: "kpi", data: { values: { scalar: [1] } } }) && noValues({ kind: "kpi", data: {} }) && !noValues({ kind: "recent_rows", data: { columns: ["a"] } }) && noValues({ kind: "chart", data: {} }));
// 조건 색 규칙(dash_rules.js): 숫자·단어 식, 맞는 칸만, 위의 규칙 하나, 오류 문장, 화살표
const m = (text, v) => { const f = parseMatch(text); assert(!f.error, text + ": " + f.error); return f.test(v); };
assert(m("= 0", 0) && !m("= 0", 1) && m("> 100", 101) && !m("> 100", 100) && m(">= 1,000", 1000) && !m(">= 1,000", 999) && m("<= 5", 5) && m("!= 5", 4));
assert(m("< -5%", -0.06) && !m("< -5%", -0.05) && m("10 ~ 20", 10) && m("10 ~ 20", 20) && !m("10 ~ 20", 21) && m("20 ~ 10", 15));  // %는 비율, 사이는 양끝 포함
assert(m("1, 3, 7", 3) && !m("1, 3, 7", 4) && m("1,000", 1000) && !m("1,000", 1));  // 목록, 1,000은 천 단위 숫자 하나
assert(m("10, 200", 200) && !m("10, 200", 10200) && m("100, 200", 100) && m("₩1,000, ₩2,000", 2000) && !m("₩1,000, ₩2,000", 1) && m("1,3,7", 7));  // 쉼표+공백 목록은 숫자 하나로 붙여 읽지 않는다
assert(m("취소", "취소") && !m("취소", "취소선") && m("취소, 환불", "환불") && !m("취소, 환불", "배송") && m("취소", "취소".toLowerCase()));
assert(m("*지연*", "a지연b") && !m("*지연*", "정상") && m("ERR*", "err001") && !m("ERR*", "xerr") && m("*실패", "로그인 실패") && !m("*실패", "실패 처리"));  // 포함·시작·끝, 대소문자 무시
for (const [bad, why] of [["", "식을 적어"], ["> abc", "숫자가 아니에요"], ["10 ~ x", "양쪽은 숫자"], ["*", "앞뒤에만"], ["a*b*", "앞뒤에만"], [">", "숫자가 아니에요"]]) assert.match(parseMatch(bad).error, new RegExp(why), bad);
assert.strictEqual(ruleNumber("₩1,234.5"), 1234.5); assert.strictEqual(ruleNumber("70%"), 0.7); assert.strictEqual(ruleNumber("1,3"), null); assert.strictEqual(ruleNumber("abc"), null);
const rules = [{ column: "상태", match: "*지연*", color: "#e03131", arrow: "none", scope: "row" }, { column: "금액", match: "> 100", color: "#2b8a3e", arrow: "auto", scope: "cell" }, { column: "금액", match: "> 10", color: "#f08c00", arrow: "up", scope: "cell" },
               { column: "금액", match: "취소", color: "#000000", arrow: "none", scope: "cell" }, { column: "건수", match: "= 0", color: "red", arrow: "none", scope: "cell" }];
const compiled = compileRules(rules, { 상태: "word", 금액: "num", 건수: "num" });
assert.deepStrictEqual(compiled.map(c => !!c.error), [false, false, false, true, true]);  // 숫자 칸에 단어 식, 틀린 HEX는 오류
assert.match(compiled[3].error, /숫자 칸이라 단어 식은 쓸 수 없어요/); assert.match(compiled[4].error, /HEX가 아니에요/);
assert.match(compileRule({ column: "상태", match: "> 1", color: "#fff" }, { 상태: "word" }).error, /글자 칸이라 숫자 식은 쓸 수 없어요/);
assert.strictEqual(matchRule(compiled, "금액", 150), rules[1]);  // 여러 규칙이 맞으면 위의 규칙 하나
assert.strictEqual(matchRule(compiled, "금액", 50), rules[2]); assert.strictEqual(matchRule(compiled, "금액", 5), null);  // 맞지 않는 칸은 그대로
assert.strictEqual(matchRule(compiled, "상태", "배송 지연"), rules[0]); assert.strictEqual(matchRule(compiled, "상태", 7), null);  // 숫자 값에는 단어 식이 맞지 않는다
assert.strictEqual(matchRule(compiled, "건수", 0), null); assert.strictEqual(matchRule(compiled, "금액", null), null); assert.strictEqual(matchRule(compiled, "없는 컬럼", 1), null);
assert.deepStrictEqual(ruleArrow(rules[1], 150, 100), { text: "▲", color: "#2b8a3e", tone: "good" }); assert.strictEqual(ruleArrow(rules[1], 150, 100, "down").tone, "bad");
assert.deepStrictEqual(ruleArrow(rules[1], 80, 100), { text: "▼", color: "#2b8a3e", tone: "bad" }); assert.deepStrictEqual(ruleArrow(rules[1], 100, 100), { text: "–", color: "#2b8a3e", tone: "neutral" });
assert.strictEqual(ruleArrow(rules[1], 150, null), null); assert.strictEqual(ruleArrow(rules[0], 1, 0), null); assert.strictEqual(ruleArrow(rules[2], 1, 5).text, "▲"); assert.strictEqual(ruleArrow({ ...rules[2], arrow: "down", color: "" }, 1, 5).color, null);
assert.deepStrictEqual([kindOfValues([null, "", 3]), kindOfValues(["a"]), kindOfValues([null])], ["num", "word", null]);
assert.deepStrictEqual(parseRuleLine("상태 | *지연* | #e03131 | 자동 | 줄").rule, { column: "상태", match: "*지연*", color: "#e03131", arrow: "auto", scope: "row" });
assert.strictEqual(parseRuleLine("금액 | > 1 | #fff").rule.arrow, "none");
for (const [bad, why] of [["금액 | > 1", "모양으로"], [" | > 1 | #fff", "컬럼 이름"], ["금액 | > 1 | #fff | 옆", "화살표"], ["금액 | > 1 | #fff | 위 | 열", "적용 범위"], ["금액 | > x | #fff", "숫자가 아니에요"], ["금액 | > 1 | 빨강", "HEX"]]) assert.match(parseRuleLine(bad).error, new RegExp(why), bad);
assert.deepStrictEqual(parseStateLine("cpu >= 90 이상").rule, { column: "cpu", op: ">=", value: 90, level: "bad" }); assert.deepStrictEqual(parseStateLine("상태 = DOWN 주의").rule, { column: "상태", op: "=", value: "DOWN", level: "warn" });
assert.match(parseStateLine("cpu 90").error, /모양으로/); assert.match(parseStateLine("cpu > 9 심각").error, /단계는 주의나 이상/);
assert.deepStrictEqual(parseLinkLine("a -> b").link, ["a", "b"]); assert.deepStrictEqual(parseLinkLine("라우터 → 서버 1").link, ["라우터", "서버 1"]); assert.match(parseLinkLine("a b").error, /출발/);
assert.deepStrictEqual(parseBlankLine("매출 = 값 amt sum 금액").blank, { name: "매출", kind: "value", y: "amt", agg: "sum", format: "currency" }); assert.deepStrictEqual(parseBlankLine("기준 = 기준일").blank, { name: "기준", kind: "asof", y: "", agg: "count", format: "number" });
assert.strictEqual(parseBlankLine("전 = 직전 amt").blank.agg, "sum"); assert.match(parseBlankLine("매출 = 합 amt").error, /종류는/); assert.match(parseBlankLine("매출 = 값 amt avg").error, /집계는/); assert.match(parseBlankLine("매출").error, /모양으로/);
assert.deepStrictEqual(readLines(["a -> b", "", "나쁨", "c -> d"].join(String.fromCharCode(10)), parseLinkLine).map(r => r.link ?? r.error.slice(0, 2)), [["a", "b"], "‘출", ["c", "d"]]);
assert.deepStrictEqual(templateParts("오늘 {매출}은 {비교}"), [{ text: "오늘 " }, { blank: "매출" }, { text: "은 " }, { blank: "비교" }]);
// 배치도 처음 배치(종류별 줄), 편차 칸, 고유 개수의 기간 값
assert.deepStrictEqual(autoGrid([{ name: "a", kind: "서버" }, { name: "b", kind: "서버" }, { name: "c", kind: "스위치" }, { name: "d" }]), { a: [1 / 3, 1 / 4], b: [2 / 3, 1 / 4], c: [1 / 2, 2 / 4], d: [1 / 2, 3 / 4] });  // 종류마다 한 줄, 줄 안에서 고르게
assert.deepStrictEqual(autoGrid([]), {});
const dev = deviationCells([10, 20, null, 30], null, null);  // 기준 = 평균 20, 허용 폭 = 기준의 20% = 4
near(dev.base, 20); assert.deepStrictEqual(dev.cells.map(c => [c.v, c.dev, c.over]), [[10, -10, true], [20, 0, false], [null, null, false], [30, 10, true]]);
assert.deepStrictEqual(deviationCells([10, 12], 10, 3).cells.map(c => c.over), [false, false]); assert.deepStrictEqual(deviationCells([10, 14], 10, 3).cells.map(c => c.over), [false, true]); assert.strictEqual(deviationCells([null], null, null).base, null);
const unique = { kind: "kpi", colors: fc(), view: {}, range: { start: "2026-01-05", end: "2026-01-06" }, data: { by_unit: by, values: { window_nunique: { start: "2026-01-05", end: "2026-01-06", values: { 4: 3 } } } } };
assert.strictEqual(cardValue(unique, 4), 3);  // 서버가 이 기간으로 따로 구한 고유 개수
assert.strictEqual(cardValue({ ...unique, range: { start: "2026-01-05", end: "2026-01-12" } }, 4), null);  // 기간이 바뀌면 값이 없다(다시 구해야 함)
assert.strictEqual(cardValue({ ...unique, range: { start: "2026-01-06", end: "2026-01-06" } }, 4), 2);  // 하루 값은 일 값 그대로
assert.strictEqual(cardValue({ ...unique, range: { start: "2027-01-01", end: "2027-01-31" } }, 4), null);
assert.strictEqual(cardValue(unique, 0), 65);  // 합계 계열은 일 값을 묶는다
// 읽은 날짜 범위(window) 밖으로 보는 기간이 나가면 다시 읽는다
const win = { data: { window: { start: "2025-12-21", end: "2026-02-02" } } };
assert(!outsideWindow(win, { start: "2026-01-12", end: "2026-02-02" }) && outsideWindow(win, { start: "2025-12-01", end: "2026-02-02" }) && outsideWindow(win, { start: "2026-01-12", end: "2026-03-01" }));
assert(!outsideWindow({ data: {} }, { start: "2020-01-01", end: "2030-01-01" }) && !outsideWindow(win, null) && !outsideWindow(win, { start: null, end: "2026-01-01" }));  // 범위가 없으면 해당 없음
// 다시 묶을 수 없는 집계(서버가 SERVER.merge로 알려 준다): 중앙값도 고유 개수처럼 일 값은 그대로, 주·월·연은 서버가 따로 구한 값
const by2 = { day: { labels: ["2026-01-05", "2026-01-06", "2026-02-02"], series: [{ name: "중앙", agg: "median", merge: false, values: [5, 6, 8] }, { name: "합계", agg: "sum", sum: [1, 2, 3], count: [1, 1, 1] }] },
  month: { labels: ["2026-01-01", "2026-02-01"], series: [{ name: "중앙", values: [5.5, 8] }] } };
assert.deepStrictEqual(byUnit(by2, "day").datasets.map(d => d.values), [[5, 6, 8], [1, 2, 3]]);
assert.deepStrictEqual(byUnit(by2, "month").datasets.map(d => d.values), [[5.5, 8], [3, 3]]);  // 중앙값은 서버 값, 합계는 일 값을 다시 묶음
assert.strictEqual(merges("sum") && merges("count") && !merges("median") && !merges("nunique") && !merges("p90"), true);
assert.strictEqual(aggName("median"), "median");  // 이름 표가 없으면 id 그대로(서버가 SERVER.agg_names로 준다)
console.log("ok");
"""


def test_dash_js(tmp_path):
    dash = json.dumps({"types": dash_store.CHART_TYPES, "time_types": dash_store.TIME_TYPES, "units": dash_store.UNITS, "max_palette": dash_store.MAX_PALETTE, "aggs": list(dash_calc.AGGS)}, ensure_ascii=False)
    script = tmp_path / "dash_test.js"
    script.write_text((ROOT / "static" / "core" / "board.js").read_text(encoding="utf-8") + (ROOT / "static" / "core" / "dash.js").read_text(encoding="utf-8")
                      + (ROOT / "static" / "dashboard" / "dash_cards.js").read_text(encoding="utf-8") + (ROOT / "static" / "dashboard" / "dash_rules.js").read_text(encoding="utf-8")
                      + SCRIPT.replace("%(dash)s", json.dumps(dash, ensure_ascii=False)).replace("%(merge)s", json.dumps(json.dumps(list(sql_funcs.MERGE)))), encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr[-3000:]


OWN_CASES = [["day 2026-01-12 ~ 2026-02-02"], ["day 2026-01-01 ~ ", "day ~ 2026-03-31"], ["day 2026-01-01 ~ ", "when 2026-03-01 ~ 2026-03-31"], ["day 2026-03-31 ~ 2026-03-01"],
             ["day 2026-02-30 ~ 2026-03-01", "day 2026-03-01 ~ 2026-03-01"], ["  day   2026-12-31~2027-01-02 "], ["day 2026-01-01 00:00 ~ 2026-01-02"], [], ["엉터리"]]


def test_own_range_same_in_python_and_js(tmp_path):
    """dash_calc.own_range(서버)와 dash.js ownRange(화면)가 같은 입력에서 같은 기간·직전 기간을 낸다(열린 줄, 여러 줄, 거꾸로 된 줄, 없는 날, 기간 줄 없음)."""
    from dashboard import dash_calc
    want = []
    for periods in OWN_CASES:
        got = dash_calc.own_range({"source": {"body": {"periods": periods}}})
        want.append(got and {"range": got["range"], "prev": got["prev"]})
    script = tmp_path / "own_test.js"
    dash = json.dumps({"types": dash_store.CHART_TYPES, "time_types": dash_store.TIME_TYPES, "units": dash_store.UNITS, "max_palette": dash_store.MAX_PALETTE}, ensure_ascii=False)
    body = ("const assert = require('assert'); globalThis.SERVER = { dash: JSON.parse(%s), merge: [] };\nconst cases = %s;\n" % (json.dumps(dash, ensure_ascii=False), json.dumps(OWN_CASES, ensure_ascii=False))
            + "const got = cases.map(p => ownRange({ source: { body: { periods: p } } }));\nconsole.log(JSON.stringify(got));\n")
    script.write_text((ROOT / "static" / "core" / "board.js").read_text(encoding="utf-8") + (ROOT / "static" / "core" / "dash.js").read_text(encoding="utf-8")
                      + (ROOT / "static" / "dashboard" / "dash_cards.js").read_text(encoding="utf-8") + (ROOT / "static" / "dashboard" / "dash_rules.js").read_text(encoding="utf-8") + body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == want
    assert want[0]["prev"]["text"] == "직전 22일" and want[1] is None and want[2] is not None and want[4]["range"]["start"] == "2026-03-01" and want[7] is None
