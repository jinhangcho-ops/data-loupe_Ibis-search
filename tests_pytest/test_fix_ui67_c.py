"""문서 사진을 찍다 찾은 화면 문제(묶음 C)를 node와 파이썬으로 본다. node가 없으면 JS 시험은 건너뛴다.
각 시험은 고치기 전 코드에서 실패하도록 짠 것이다(값은 손으로 센 작은 예제)."""
import json
import re
import shutil

import pytest

from conftest import ROOT
from test_fix_audit_js import between, read, run_node

node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

# $(id)가 돌려줄 가짜 요소: 값·글·숨김(hidden 클래스)만 흉내 낸다.
ELS = r"""
const ELS = {};
const fakeEl = id => ({ id, value: "", textContent: "", disabled: false, hidden: false, className: "", innerHTML: "", options: [], children: [],
  classList: { toggle(c, on) { if (c === "hidden") ELS[id].hidden = on === undefined ? !ELS[id].hidden : !!on; }, add(c) { if (c === "hidden") ELS[id].hidden = true; }, remove(c) { if (c === "hidden") ELS[id].hidden = false; }, contains: c => c === "hidden" && ELS[id].hidden },
  style: { setProperty() {} }, replaceChildren(...k) { this.children = k; }, append(...k) { this.children.push(...k); }, querySelectorAll: () => [], querySelector: () => null });
globalThis.$ = id => (ELS[id] ??= fakeEl(id));
"""


@node
def test_correlation_table_has_no_minus_zero(tmp_path):
    """1: 0으로 반올림되는 작은 음수 상관계수는 표에 -0이 아니라 0으로(toLocaleString(-0)은 '-0')."""
    part = between("prep.html", "function anaTable(d, view)", "// 마지막 분석 결과를")
    body = """
const t = anaTable({ columns: ["a", "b"], matrix: [[1, -0.0001], [-0.0001, 1]] }, "heat");
const s = anaTable({ r: -0.00001, n: 3, points: [] }, "scatter");
console.log(JSON.stringify({ heat: Object.is(t.rows[0].b, -0), scat: Object.is(s.rows[0]["값"], -0), one: t.rows[0].a }));"""
    got = run_node(tmp_path, body, parts=[part])
    assert got == {"heat": False, "scat": False, "one": 1}


def test_scatter_hint_matches_even_sampling():
    """2: 서버(analysis/stats.py scatter)는 점이 많으면 고르게 뽑는다. 안내도 '앞에서부터'가 아니다."""
    hint = between("prep.html", '<div id="a-scat"', "</div></div>")
    assert "앞에서부터" not in hint and "고르게" in hint
    assert "picked.sample(MAX_POINTS" in (ROOT / "analysis/stats.py").read_text(encoding="utf-8")


@node
def test_funnel_hides_full_rerun(tmp_path):
    """3: 퍼널은 늘 테이블 전체로 구하니(prep_app.whole) [전체로 다시 구하기]를 숨긴다. 다른 분석은 보인다."""
    from prep import prep_app
    assert prep_app.whole({"how": {"kind": "funnel"}}) and not prep_app.whole({"how": {"kind": "groups"}})
    part = between("prep.html", "function drawAna()", "\n}\n") + "\n}\n"
    stubs = ("let A = { chart: null, result: null, view: 'funnel' }; const VIEW_NAMES = {}, VIEW_RATIOS = {};"
             "const anaTitle = () => 't', anaNote = () => 'n', anaTable = () => ({}), resultTable = () => {}, sankeySvg = () => 's', funnelSentence = () => 'f';")
    body = """
A.result = { kind: "funnel", views: ["funnel"], steps: [] }; A.view = "funnel"; drawAna(); const funnel = $("a-full").hidden;
A.result = { kind: "groups", views: ["groups"], values: [1] }; A.view = "groups"; $("ana").hidden = true; drawAna();
console.log(JSON.stringify({ funnel, groups: $("a-full").hidden }));"""
    assert run_node(tmp_path, body, parts=[ELS, stubs, part]) == {"funnel": True, "groups": False}


@node
def test_geo_table_select_has_a_label(tmp_path):
    """4: ④ 좌표 단계의 영역·지점 테이블 칸은 빈 칸으로 보이지 않고 무엇을 고르는 칸인지 첫 줄에 적혀 있다."""
    part = between("prep.html", "let geoTables = []", "async function geoLoadCols()")
    stubs = "let prof = null, geoSteps = [], preview = null; const EPSG_LIST = [], kept = () => [], chip = t => t, geoLoadCols = () => {};"
    body = """
const first = op => { $("geo-op").value = op; renderGeo(); const s = $("geo-form").children.find(c => c.id === "geo-table"); return s.children[0].textContent; };
console.log(JSON.stringify({ region: first("region"), nearest: first("nearest") }));"""
    got = run_node(tmp_path, body, parts=[ELS, stubs, part])
    assert got["region"].strip() and got["nearest"].strip()


def test_sentence_number_input_fits_coordinates():
    """5a: 문장 카드의 숫자 칸은 37.5665·126.978이 잘리지 않을 만큼 넓다(전에는 80px)."""
    css = read("sentences.css")
    width = int(re.search(r"\.slot-wrap input\.num \{[^}]*?[^-]width:(\d+)px", css).group(1))
    assert width >= 110


SENT_HEAD = r"""
Node.prototype.querySelector = function () { return { disabled: false }; };
const walk = (n, out = []) => { out.push(n); (n.children ?? []).forEach(c => walk(c, out)); return out; };
const wait = () => new Promise(r => setTimeout(r, 300));
"""


def sentence_run(tmp_path, body):
    from test_dash_metrics_tab import run_node as run_sent
    return run_sent(tmp_path, SENT_HEAD + body, page="query")


@node
def test_sentence_column_prep_and_period_row(tmp_path):
    """5b: 컬럼 칸(any)을 고르면 다시 그려 ‘그대로’ 다듬기 칸이 바로 나온다(when 칸이 없는 틀도). first_n을 고르면 글자 수 칸도.
    5c: 날짜 컬럼이 없는 테이블에서는 문장마다 붙는 기간 줄(빈 날짜 컬럼 칸)을 그리지 않는다."""
    body = r"""
const CAT = { preps: { first_n: "앞 N글자" }, templates: [{ id: "e01", no: "1", name: "값별 개수", text: "{컬럼}별 개수", kind: "explore", slots: [{ name: "컬럼", type: "column", kinds: "any", label: "컬럼" }] }] };
const base = globalThis.api;
globalThis.api = async (url, b) => url === "/api/sentences" ? CAT : url === "/api/sentence/check" ? { ok: true, text: "", issues: [] } : base(url, b);
S.base = "stores";
(async () => {
  const host = new Node("div");
  const view = SentenceView(host, { body: () => ({}), columns: () => [["지역", "text"], ["매출", "num"]], tables: () => ["stores"] });
  await view.show();
  walk(host).find(n => n.className === "tpl").onclick();
  const pick = walk(host).find(n => n.tag === "select" && n.children.some(c => c.value === "지역"));
  pick.value = "지역"; pick.onchange();
  const prep = walk(host).find(n => n.className === "prep");
  let nInput = false;
  if (prep) { prep.value = "first_n"; prep.onchange(); nInput = walk(host).some(n => n.tag === "input" && n.title === "글자 수"); }
  const period = walk(host).some(n => n.title === "날짜 컬럼");
  await wait();
  console.log(JSON.stringify({ prep: !!prep, nInput, period, slots: view.items()[0].slots }));
})();
"""
    got = sentence_run(tmp_path, body)
    assert got == {"prep": True, "nInput": True, "period": False, "slots": {"컬럼": {"column": "지역", "prep": "first_n", "n": 3}}}


@node
def test_sentence_period_row_stays_with_a_date_column(tmp_path):
    """5c 반대쪽: 날짜 컬럼이 있으면 기간 줄과 날짜 컬럼 칸이 그대로 있다."""
    body = r"""
const CAT = { preps: {}, templates: [{ id: "e01", no: "1", name: "개수", text: "{컬럼}별 개수", kind: "explore", slots: [{ name: "컬럼", type: "column", kinds: "any", label: "컬럼" }] }] };
const base = globalThis.api;
globalThis.api = async (url, b) => url === "/api/sentences" ? CAT : url === "/api/sentence/check" ? { ok: true, text: "", issues: [] } : base(url, b);
S.base = "orders";
(async () => {
  const host = new Node("div");
  const view = SentenceView(host, { body: () => ({}), columns: () => [["주문일", "date"], ["매출", "num"]], tables: () => ["orders"] });
  await view.show();
  walk(host).find(n => n.className === "tpl").onclick();
  await wait();
  console.log(JSON.stringify({ period: walk(host).some(n => n.title === "날짜 컬럼") }));
})();
"""
    assert sentence_run(tmp_path, body) == {"period": True}


def test_empty_metric_form_is_hidden():
    """6a: 지표 탭의 빈 .mform(테두리만 있는 상자)은 보이지 않는다."""
    assert re.search(r"\.mform:empty\s*\{\s*display:\s*none", read("sentences.css"))


@node
def test_catalog_hint_shows_once(tmp_path):
    """6b: 지표 사전에서 고른 항목의 ‘북극성 후보’ 안내는 설명에 한 번만(계산 결과에 또 붙지 않는다)."""
    body = r"""
const ENTRY = { id: "dau", area: "a", name: "DAU", help: "하루 사용자", formula: "고유 사용자 수", sentence: "하루 사용자", slots: [], hint: "north_star" };
const base = globalThis.api;
globalThis.api = async (url, b) => url === "/api/catalog" ? { areas: [{ id: "a", name: "사용" }], entries: [ENTRY] }
  : url === "/api/catalog/available" ? { availability: { dau: { usable: true, suggest: {} } } } : url === "/api/catalog/compute" ? { value: 3, format: "" } : base(url, b);
S.base = "orders";
(async () => {
  const host = new Node("div");
  const view = SentenceView(host, { only: "metric", scope: () => ({ base: "orders", schema: "" }), body: () => ({}), columns: () => [["id", "num"]], tables: () => ["orders"] });
  await view.show(); await wait();
  walk(host).find(n => (n.className ?? "").startsWith("mitem")).onclick();
  await wait();
  await walk(host).find(n => n.tag === "button" && n._text === "계산하기").onclick({ currentTarget: {} });
  await wait();
  console.log(JSON.stringify({ hints: walk(host).filter(n => n.className === "mhint").length, value: walk(host).some(n => n.className === "mval") }));
})();
"""
    assert sentence_run(tmp_path, body) == {"hints": 1, "value": True}


@node
def test_explore_chart_tuning(tmp_path):
    """6c: 비율(percent) 지표 차트의 세로축 눈금에 %를 붙인다. 8: 범례는 계열을 넣은 순서(Chart.js는 order가 작은 선을 막대보다 앞에 둔다)."""
    part = between("index.html", "function tuneChart(", "\n}\n") + "\n}\n"
    body = """
const cfg = () => ({ options: { plugins: { legend: { labels: {} } }, scales: { y: { ticks: { color: "#000" } } } } });
const pct = tuneChart(cfg(), true), plain = tuneChart(cfg(), false), pie = tuneChart({ options: { plugins: { legend: { labels: {} } }, scales: {} } }, true);
const items = [{ datasetIndex: 1, text: "평균" }, { datasetIndex: 0, text: "합계" }].sort((a, b) => a.datasetIndex - b.datasetIndex);  // 범례 정렬은 chartConfig로 옮겼다(test_fix_ui67_c_legend)
console.log(JSON.stringify({ tick: pct.options.scales.y.ticks.callback(9), color: pct.options.scales.y.ticks.color, plainTick: "callback" in plain.options.scales.y.ticks, order: items.map(i => i.text), pie: Object.keys(pie.options.scales) }));"""
    got = run_node(tmp_path, body, parts=[part])
    assert got == {"tick": "9%", "color": "#000", "plainTick": False, "order": ["합계", "평균"], "pie": []}
    html = read("index.html")
    assert "tuneChart(chartConfig(drawnCard(drawn, axisRanges())" in html and 'format === "percent")' in between("index.html", "function drawMetric()", "\n}\n")


@node
def test_calc_summary_shows_new_column_name(tmp_path):
    """9: 접힌 ⑥ 새 컬럼 요약은 식 줄의 첫 낱말(‘식’)이 아니라 새 컬럼 이름."""
    part = between("index.html", "function showSummaries()", "\n}\n") + "\n}\n"
    stubs = ("document.querySelectorAll = () => []; let connected = true, connName = 'c'; const UNION_HOW = {}, sortText = x => x, text = i => i.line ?? i, columnCount = () => 0;"
             "const S = { base: 't', union: { tables: [] }, joins: [], periods: [], conditions: [], sorts: [], columns: [], calcs: [] };")
    body = """
const show = calcs => { S.calcs = calcs; showSummaries(); return $("sum-calcs").textContent; };
console.log(JSON.stringify([show(["식 금액 * 2 이름 두배"]), show(["SQL식 a + b 이름 합", "식 x 이름 y"]), show(["rank 금액 기준 날짜"]), show([])]));"""
    assert run_node(tmp_path, body, parts=[ELS, stubs, part]) == ["두배", "합 외 1개", "rank", "없음"]


@node
def test_virtual_table_name_clears_after_save(tmp_path):
    """10: SQL 탭에서 가상 테이블을 저장하면 이름 칸을 비운다(저장한 SQL 이름 칸처럼). 실패하면 남긴다."""
    part = between("index.html", "async function saveVirtual(body)", "\n}\n") + "\n}\n"
    stubs = ("let fail = false; const tableCols = {}, searchDraft = { clear() {} }, say = () => {}, showVirtual = async () => {}, loadTables = async () => {};"
             "const api = async () => { if (fail) throw new Error('x'); return {}; };")
    body = """
(async () => {
  $("vt-name").value = "v1"; await saveVirtual({ name: "v1", sql: "select 1" }); const saved = $("vt-name").value;
  $("vt-name").value = "v2"; fail = true; await saveVirtual({ name: "v2", sql: "select 1" });
  console.log(JSON.stringify({ saved, failed: $("vt-name").value }));
})();"""
    assert run_node(tmp_path, body, parts=[ELS, stubs, part]) == {"saved": "", "failed": "v2"}


def test_integrations_card_names_the_real_folder():
    """11: 새 서비스 안내의 폴더는 실제 위치(integrations/adapters/)."""
    hint = between("integrations.html", "서비스 하나는", "</div>")
    assert "<code>integrations/adapters/</code>" in hint and "<code>integrations/adapters/__init__.py</code>" in hint
    assert (ROOT / "integrations/adapters/__init__.py").exists()


def test_pivot_prefix_applies_to_every_value_when_one_clashes():
    from lazy import ibis as ib
    from core import calc
    t = ib.memtable({"매장": ["강남", "강남", "홍대"], "채널": ["매장", "앱", "웹"], "금액": [1, 2, 3]})
    out = calc.pivot(t, {"rows": ["매장"], "column": "채널", "how": "sum", "value": "금액"}, lambda e: e.to_pyarrow()["v"].to_pylist()).to_pyarrow()
    assert [c for c in out.column_names if c.startswith("채널=")] == ["채널=매장", "채널=앱", "채널=웹"]  # 예전엔 채널=매장, 앱, 웹으로 섞였다


def test_legend_keeps_added_order_in_chart_config():
    assert "sort: (a, b) => a.datasetIndex - b.datasetIndex" in between("dash.js", "function chartConfig(", "\n}\n")
