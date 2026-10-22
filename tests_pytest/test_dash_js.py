"""static/dash.js의 계산 부분(일 값 다시 묶기, 색 정하기, 종류 고르기, 차트 설정 9종)을 node로 돌려 본다. node가 없으면 건너뛴다.
board.js와 dash.js를 이어 붙이고 서버가 주는 SERVER.dash만 흉내 낸다. 값은 손으로 계산한 작은 예제다."""
import json
import shutil
import subprocess

import pytest

import dash_store
from conftest import ROOT

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

SCRIPT = """
const assert = require("assert");
globalThis.SERVER = { dash: JSON.parse(%(dash)s) };
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
assert.deepStrictEqual([line.data.datasets[0].tension, poly.data.datasets[0].tension], [0.4, 0]);
assert.deepStrictEqual([line.data.datasets[0].fill, area.data.datasets[0].fill], [false, true]);
assert.strictEqual(area.data.datasets[0].backgroundColor, alpha("#111111", 0x55 / 255));  // 영역형 라인은 색 알파 0x55
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
console.log("ok");
"""


def test_dash_js(tmp_path):
    dash = json.dumps({"types": dash_store.CHART_TYPES, "time_types": dash_store.TIME_TYPES, "units": dash_store.UNITS, "max_palette": dash_store.MAX_PALETTE}, ensure_ascii=False)
    script = tmp_path / "dash_test.js"
    script.write_text((ROOT / "static" / "board.js").read_text(encoding="utf-8") + (ROOT / "static" / "dash.js").read_text(encoding="utf-8")
                      + SCRIPT % {"dash": json.dumps(dash, ensure_ascii=False)}, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr[-3000:]
