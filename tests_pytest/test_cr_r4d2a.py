"""R4-D2a: 차트 섬(/chart, Next web/out/chart.html)을 서버가 내주고, 분석 화면(prep.html drawAna)과 문장 결과 차트(sentences.js)가
Chart.js 대신 iframe 섬에 postMessage(web/lib/island.ts 규약)로 그릴 것을 보낸다. 값은 모두 지어낸 것."""
import json
import re
import shutil

import pytest

from conftest import ROOT
from dashboard import dash_app
from test_fix_audit_js import between, run_node
from test_fix_ui67_c import ELS

node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


def test_chart_route_serves_next_island(client, monkeypatch, tmp_path):
    page = tmp_path / "chart.html"
    page.write_text('<html><head><script src="/next/_next/a.js"></script></head><body><script>1</script></body></html>', encoding="utf-8")
    monkeypatch.setattr(dash_app, "CHART_PAGE", page)
    r = client.get("/chart")
    csp = r.headers["Content-Security-Policy"]
    nonce = re.search(r"script-src 'nonce-([^']+)'", csp).group(1)
    assert r.status_code == 200 and r.text.count(f'<script nonce="{nonce}"') == 2
    assert f"script-src 'nonce-{nonce}' 'strict-dynamic';" in csp and "frame-ancestors 'self'" in csp and "frame-src 'self'" in csp


def test_chart_route_not_built(client, monkeypatch, tmp_path):
    monkeypatch.setattr(dash_app, "CHART_PAGE", tmp_path / "없음.html")
    r = client.get("/chart")
    assert r.status_code == 200 and "빌드되지 않았어요" in r.text and "npm run build" in r.text and "<script" not in r.text


def test_old_pages_may_frame_island(client):
    """옛 화면(분석·쿼리)이 같은 출처의 섬을 iframe으로 끼울 수 있다(default-src 'none'만이면 막힌다)."""
    for url in ("/prep", "/search"):
        r = client.get(url)
        assert r.status_code == 200 and "frame-src 'self'" in r.headers["Content-Security-Policy"], url
    assert "frame-ancestors" not in client.get("/prep").headers["Content-Security-Policy"]


def test_no_chartjs_calls_left():
    prep, sent = (ROOT / "static/prep/prep.html").read_text(encoding="utf-8") + (ROOT / "static/prep/analysis.js").read_text(encoding="utf-8"),(ROOT / "static/query/sentences.js").read_text(encoding="utf-8")
    for text in (prep, sent):
        assert "new Chart" not in text and "CHART_JS" not in text
    assert "anaConfig" not in prep and '<iframe id="a-chart" src="/chart" title="분석 그림"' in prep
    for text in (prep, sent):  # 같은 도우미는 R4-D2b가 common.js 한 곳으로 옮겼다(쿼리 화면도 같은 것을 쓴다)
        assert "function island(" not in text and "island(" in text
    assert "function island(" in (ROOT / "static/core/common.js").read_text(encoding="utf-8")


ISLAND_BODY = """
const heard = []; globalThis.addEventListener = (t, f) => heard.push(f); globalThis.removeEventListener = (t, f) => heard.splice(heard.indexOf(f), 1);
globalThis.location = { origin: "http://app" }; document.documentElement.dataset.theme = "light";
const posted = [], errs = [], win = { postMessage: (m, o) => posted.push([m, o]) };
const frame = { isConnected: true, style: {}, contentWindow: win };
const send = island(frame, m => errs.push(m));
const fire = (data, source = win, origin = "http://app") => [...heard].forEach(f => f({ data, source, origin }));
send({ kind: "ana", payload: 1 }); send({ kind: "ana", payload: 2 });
const before = posted.length;
fire({ type: "ready" }, {}); fire({ type: "ready" }, win, "http://evil");
const stranger = posted.length;
fire({ type: "ready" });
fire({ type: "size", height: 321 }); fire({ type: "error", message: "못 그렸어요" });
document.documentElement.dataset.theme = "dark"; send();
send({ kind: "ana", payload: 3 });
frame.isConnected = false; fire({ type: "size", height: 5 });
console.log(JSON.stringify({ before, stranger, posted, height: frame.style.height, errs, left: heard.length }));
"""


@node
def test_island_helper_queues_until_ready(tmp_path):
    got = run_node(tmp_path, ISLAND_BODY, parts=[between("common.js", "function island(", "\n}\n") + "\n}\n"])
    assert got["before"] == 0 and got["stranger"] == 0  # ready 전·다른 창·다른 출처는 보내지 않는다
    assert got["posted"] == [[{"type": "draw", "kind": "ana", "payload": 2, "theme": "light"}, "http://app"],
                             [{"type": "draw", "kind": "ana", "payload": 2, "theme": "dark"}, "http://app"],
                             [{"type": "draw", "kind": "ana", "payload": 3, "theme": "dark"}, "http://app"]]
    assert got["height"] == "321px" and got["errs"] == ["못 그렸어요"] and got["left"] == 0  # 빠진 iframe의 듣기는 지운다


@node
def test_draw_ana_sends_island_message(tmp_path):
    part = between("analysis.js", "function drawAna()", "\n}\n") + "\n}\n"
    stubs = ("let A = { result: null, view: 'groups', lo: 0, hi: 0 }; const VIEW_NAMES = {}, VIEW_RATIOS = { box: 2.4 }, sent = [];"
             "const anaTitle = () => 't', anaNote = () => 'n', anaTable = () => ({}), anaSpan = (d, v) => v === 'groups' ? 3 : 0, anaSlice = (d, v, lo, hi) => ({ cut: [v, lo, hi] }),"
             "showRange = () => {}, resultTable = () => {}, sankeySvg = () => 's', funnelSentence = () => 'f', anaDraw = m => sent.push(m);"
             "const SERVER = { analysis: { whole: [] } }, NEW_KINDS = [], ADD_TITLE = '', anaAuto = {}, gapTable = () => [], segTables = () => [], pairBlock = () => [], noteLines = () => [], gradeLine = () => '', anaRows = r => r,"
             "sortedTable = (h, d, draw) => draw(h, d, null); globalThis.getComputedStyle = () => ({ getPropertyValue: () => '' }); globalThis.document = { documentElement: {} };")
    body = """
A.result = { kind: "groups", views: ["groups"], values: [1, 2, 3] }; drawAna();
A.lo = 1; A.hi = 2; drawAna(true);
A.result = { kind: "hist", views: ["box"], values: [] }; A.view = "box"; drawAna();
A.result = { kind: "groups", views: ["groups"], values: ["a"] }; A.view = "groups"; drawAna();
A.result = { kind: "funnel", views: ["funnel"], steps: [] }; A.view = "funnel"; drawAna();
console.log(JSON.stringify(sent));"""
    got = run_node(tmp_path, body, parts=[ELS, stubs, part])
    assert got == [{"kind": "ana", "payload": {"cut": ["groups", 0, 3]}, "opts": {"view": "groups", "ratio": 16 / 9, "auto": {}}},
                   {"kind": "ana", "payload": {"cut": ["groups", 1, 2]}, "opts": {"view": "groups", "ratio": 16 / 9, "auto": {}}},
                   {"kind": "ana", "payload": {"kind": "hist", "views": ["box"], "values": []}, "opts": {"view": "box", "ratio": 2.4, "auto": {}}}]  # auto는 자동 규칙(Phase 9), all은 [모든 데이터] 체크(가짜 요소엔 없음)  # 글자 결과·퍼널은 섬에 보내지 않는다


@node
def test_sent_chart_is_island_series(tmp_path):
    part = between("sentences.js", "function sentChart(", "\n}\n") + "\n}\n"
    rows = [{"일": f"2026-03-{d:02d}", "값": d} for d in range(1, 4)]
    big = [{"k": f"k{i}", "v": i} for i in range(60)]
    got = run_node(tmp_path, f"console.log(JSON.stringify([sentChart({{ columns: ['일', '값'], rows: {json.dumps(rows)} }}), sentChart({{ columns: ['k', 'v'], rows: {json.dumps(big)} }})]));", parts=[part])
    assert got[0] == {"draw": {"kind": "series", "payload": {"labels": ["2026-03-01", "2026-03-02", "2026-03-03"], "datasets": [{"label": "값", "values": [1, 2, 3]}]}, "opts": {"type": "line"}}, "truncated": False}
    assert got[1]["draw"]["opts"] == {"type": "bar"} and len(got[1]["draw"]["payload"]["labels"]) == 50 and got[1]["truncated"]
