"""R4-D2b: 쿼리 화면의 차트·지표 탭이 차트 섬(/chart iframe, web/lib/island.ts 규약)에 postMessage로 그리고, Chart.js를 지웠다.
[전처리로 보내기]는 파일 없이 검색을 넘기고(mode "query"), /api/sql은 limit이 null이면 LIMIT 없는 SQL을 준다.
JS는 index.html의 구간을 잘라 가짜 화면($·iframe.contentWindow.postMessage 기록) 위에서 node로 돌린다. 값은 모두 지어낸 것."""
import json
import shutil

import pytest

from conftest import ROOT
from test_fix_audit_js import between, read
from test_paging import local, remote  # noqa: F401  지어낸 DuckDB·SQLite에 실제로 연결한 시험 클라이언트

node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")

# 가짜 화면: $(id)는 값·글·숨김·style과 iframe의 contentWindow.postMessage(보낸 것을 SENT에 쌓는다)만 흉내 낸다. fire(id, data)는 그 iframe이 보낸 메시지.
HEAD = r"""
const SENT = [], LISTEN = [], ELS = {};
globalThis.location = { origin: "http://app", href: "" };
globalThis.addEventListener = (name, f) => LISTEN.push(f);
globalThis.document = { documentElement: { dataset: { theme: "dark" } } };
const mkEl = id => ({ id, value: "", textContent: "", hidden: false, style: {}, isConnected: true,
  classList: { add(c) { if (c === "hidden") ELS[id].hidden = true; }, remove(c) { if (c === "hidden") ELS[id].hidden = false; }, toggle(c, on) { if (c === "hidden") ELS[id].hidden = on === undefined ? !ELS[id].hidden : !!on; } },
  contentWindow: { postMessage: (m, origin) => SENT.push({ id, m: JSON.parse(JSON.stringify(m)), origin }) } });
globalThis.$ = id => (ELS[id] ??= mkEl(id));
const fire = (id, data, origin = "http://app") => LISTEN.forEach(f => f({ origin, source: $(id).contentWindow, data }));
"""


def run(tmp_path, parts, body):
    import subprocess
    script = tmp_path / "t.js"
    script.write_text(HEAD + "\n".join(parts) + "\n" + body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def island_part():
    return between("common.js", "function island(frame, onError)", "\n}\n") + "\n}\n" + between("index.html", "const islandSets", "\n") + "\n"


@node
def test_chart_tab_posts_series_to_island(tmp_path):
    """drawChart: ready 전에는 마지막 것만 기억했다가 보내고, 모양은 {type:draw, kind:series, payload:{labels, datasets}, opts:{type, ranges, relabel, ratio}, theme}.
    size는 iframe 높이, error는 #err-chart, picture는 png 답(data URL)으로 풀린다. 다른 창·다른 출처의 메시지는 무시한다."""
    parts = [island_part(), between("index.html", "const badRange", "// 계열:"), between("index.html", "const RATIOS", "// /api/chart의 결과를")]
    body = r"""
let drawn = null;
(async () => {
  drawChart();
  const hiddenEmpty = $("chart").hidden;
  drawn = { labels: ["1", "2"], datasets: [{ y: "amt", agg: "sum", label: "합계 (amt)", values: [3, 4], type: "bar", dash: "solid", axis: "left" }] };
  drawChart();
  drawn = { labels: ["1", "2", "3"], datasets: [{ y: "amt", agg: "sum", opts: { p: 1 }, label: "합계 (amt)", values: [3, 4, 5], type: "line", dash: "dashed", axis: "right" }] };
  $("ymin").value = "0"; $("cratio").value = "square"; $("clabels").value = "1=가";
  drawChart();
  const before = SENT.length;
  fire("chart", { type: "ready" }, "http://evil");  // 다른 출처는 무시
  fire("chart", { type: "ready" });
  fire("chart", { type: "size", height: 321 });
  fire("chart", { type: "error", message: "못 그렸어요" });
  $("cratio").value = "auto";
  drawChart();
  const pic = chartDraw.picture("light", "jpeg");
  fire("chart", { type: "png", dataUrl: "data:image/jpeg;base64,AA" });
  console.log(JSON.stringify({ hiddenEmpty, shown: !$("chart").hidden, before, sent: SENT, height: $("chart").style.height, err: $("err-chart").textContent, png: await pic }));
})();"""
    got = run(tmp_path, parts, body)
    first, second, pic = got["sent"]
    assert got["hiddenEmpty"] and got["shown"] and got["before"] == 0
    assert first == {"id": "chart", "origin": "http://app", "m": {"type": "draw", "kind": "series", "theme": "dark",
        "payload": {"labels": ["1", "2", "3"], "datasets": [{"label": "합계 (amt)", "values": [3, 4, 5], "type": "line", "dash": "dashed", "axis": "right"}]},
        "opts": {"type": "", "ranges": {"y": {"min": 0}, "y1": {}}, "relabel": "1=가", "ratio": 1}}}
    assert "ratio" not in second["m"]["opts"]  # 자동이면 섬이 그룹 수로 정한다
    assert pic["m"] == {"type": "picture", "theme": "light", "format": "jpeg"}
    assert got["height"] == "321px" and got["err"] == "못 그렸어요" and got["png"] == "data:image/jpeg;base64,AA"


@node
def test_metric_tab_posts_percent_series(tmp_path):
    """drawMetric: 같은 섬 규약으로 opts.percent = (format === "percent"), ranges {}."""
    parts = [island_part(), between("index.html", "const metDraw = ", "\n}\n") + "\n}\n"]
    body = r"""
let metDrawn = { kind: "metric", used: ["전환율"], x: "shop", labels: ["A", "B"], raw: { labels: ["A", "B"], formats: { "전환율": "percent" }, values: { "전환율": [0.1, 0.2] } },
  datasets: [{ type: "bar", axis: "left", label: "전환율 (%)", values: [10, 20] }] };
const metrics = { metrics: [] }, metricText = v => String(v), mountTable = () => {}, gridSpec = x => x;
fire("met-chart", { type: "ready" });
drawMetric();
console.log(JSON.stringify({ sent: SENT, shown: !$("met-box").hidden }));"""
    got = run(tmp_path, parts, body)
    assert got["shown"] and [s["m"] for s in got["sent"]] == [{"type": "draw", "kind": "series", "theme": "dark",
        "payload": {"labels": ["A", "B"], "datasets": [{"label": "전환율 (%)", "values": [10, 20], "type": "bar", "axis": "left"}]},
        "opts": {"type": "", "ranges": {}, "percent": True, "ratio": 16 / 9}}]


@node
def test_handoff_sends_query_mode_without_dialog(tmp_path):
    """[전처리로 보내기]: 저장 대화상자 없이 mode "query"와 불러온 저장 검색 이름(없으면 빼고)으로 /api/handoff, 답의 url로 간다."""
    parts = [between("index.html", '$("handoff").onclick = ', "\n};\n") + "\n};\n"]
    body = r"""
const S = { base: "orders" }, CALLS = [], state = () => ({ schema: "", base: "orders", conditions: [] }), busy = (b, f) => f(), searchDraft = { clear: async () => {} };
const api = async (url, b) => { CALLS.push([url, b]); return { base: "@" + (b.name ?? "검색 결과"), url: "/prep?base=x" + CALLS.length }; };
(async () => {
  $("saved").value = "내 검색";
  await $("handoff").onclick();
  const named = location.href;
  $("saved").value = "";
  await $("handoff").onclick();
  console.log(JSON.stringify({ CALLS, named, plain: location.href }));
})();"""
    got = run(tmp_path, parts, body)
    assert got["CALLS"] == [["/api/handoff", {"schema": "", "base": "orders", "conditions": [], "mode": "query", "name": "내 검색"}],
                            ["/api/handoff", {"schema": "", "base": "orders", "conditions": [], "mode": "query"}]]
    assert got["named"] == "/prep?base=x1" and got["plain"] == "/prep?base=x2"
    assert "Parquet 파일로 저장" not in between("index.html", '<button id="handoff"', "</button>")


@node
def test_handed_search_cannot_become_dashboard_card(tmp_path):
    """기준이 전처리로 보낸 검색(@이름)이면 [대시보드에 추가]를 막고 안내한다."""
    parts = [between("index.html", "const HANDED = ", "\n") + "\n", between("index.html", '$("dash-add").onclick = ', "\n});\n") + "\n});\n"]
    body = r"""
const ADDED = [], SAID = [], busy = (b, f) => f(), say = (id, t) => SAID.push([id, t]), addToDashboard = async c => { ADDED.push(c); return null; };
const seriesTitle = s => s.agg, axisRanges = () => ({}), RATIOS = {}, autoRatio = () => 1, showAdded = () => {};
let drawn = { query: { base: "@검색 결과", schema: "" }, used: [{ agg: "count" }], x: "a", labels: [] };
(async () => { await $("dash-add").onclick(); console.log(JSON.stringify({ ADDED: ADDED.length, SAID })); })();"""
    got = run(tmp_path, parts, body)
    assert got == {"ADDED": 0, "SAID": [["err-chart", "전처리로 보낸 검색은 대시보드 카드로 만들 수 없어요."]]}


def test_sql_without_limit(local, remote):  # noqa: F811
    """/api/sql: limit이 null이면 행 수를 자르지 않은 검색 SQL(가상 테이블로 저장). 받아 두는 연결(원격)도 그 검색 그대로."""
    for got in (local, remote):
        full = got("/api/sql", base="orders", limit=None, offset=0, conditions=[])["sql"]
        assert "orders" in full and "LIMIT" not in full.upper() and "받아 두고" not in full
        assert "LIMIT" in got("/api/sql", base="orders", limit=5, offset=0)["sql"].upper() or "받아 두고" in got("/api/sql", base="orders", limit=5)["sql"]


def test_chart_js_is_gone():
    html, common = read("index.html"), read("common.js")
    assert '<iframe id="chart" src="/chart" title="차트"' in html and '<iframe id="met-chart" src="/chart"' in html
    for gone in ("<canvas", "new Chart", "chartConfig", "tuneChart", "fitChart", "CHART_JS", "chartPicture"):
        assert gone not in html, gone
    assert "CHART_JS" not in common and "chartLook" not in common
    assert not (ROOT / "static/core/chart.umd.min.js").exists()
    assert "new Chart" not in read("board.js") and not (ROOT / "static/core/dash.js").exists()
    vt = between("index.html", '$("wtab-vt").onclick', "\n});\n")
    assert "limit: null" in vt and "LIMIT" not in vt
