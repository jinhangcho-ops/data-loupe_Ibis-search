"""CR U2: 공통 스타일·분석·수집·연동 화면 UX. 24px 미만 단추 없음(헤드리스 Edge로 측정, 없으면 건너뜀), 분석의 마지막 테이블 복원, 상단 상태 알약, 용어."""
import json
import re
import shutil
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from conftest import ROOT, kill_edge, spawn_edge
from test_fix_audit_js import between, read, run_node

EDGE = next((p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if Path(p).exists()), None)


def test_css_sizes_and_labels():
    css = read("common.css")
    assert "input[type=checkbox], input[type=radio] { width:16px !important; height:16px !important;" in css
    assert re.search(r"label:has\(> input\[type=checkbox\]\)[^{]*label\.check \{ min-height:24px; \}", css)
    assert re.search(r"button\.ghost \{[^}]*min-height:24px;", css)
    assert re.search(r"summary \{ padding:2px 0; \}", css)


def test_accessible_names_and_button_sizes():
    prep, collect, integ = read("prep.html"), read("collect.html"), read("integrations.html")
    assert re.search(r'<input id="sample"[^>]*aria-label="미리 읽을 행 수"', prep)
    assert 'id="uploads" aria-label=' in collect
    assert 'id="sample" role="region" aria-label=' in collect
    assert re.search(r'class="btn" data-add="query"', collect) and 'id="up-toggle" class="btn"' in collect and 'id="runs-toggle" class="btn"' in collect
    assert 'id="ga4-check" class="btn"' in integ and ">인증 확인<" in integ


def test_request_card_first_and_paste_is_details():
    html = read("collect.html")
    assert html.index('<h3>요청</h3>') < html.index('<details id="paste-box">') < html.index("<h3>응답</h3>")
    assert "<h3>요청 예시로 채우기</h3>" not in html


def test_terms():
    prep, common = read("prep.html"), read("common.js")
    assert "미리 읽을 행 수" in prep and "표본 행 수</span>" not in prep
    assert ">계산 위치</span>" in prep and "원격 DB 계산" not in prep and "전처리 전체를 이 PC에서</option>" not in prep
    assert '"스키마(폴더)"' in common and "기본 스키마" not in common
    assert "넣을 DB" in read("collect.html")


def test_usage_folded_behind_status_pill():
    common, css = read("common.js"), read("common.css")
    assert '$("usage").before(el("button", { id: "usage-pill"' in common and 'textContent: "상태"' in common
    assert '보낼 곳 설정' in read("status.html") and 'textContent = "보낼 곳 설정"' not in common  # 글자는 status.html에, JS 덮어쓰기는 없다
    assert "#usage.sx { margin-left:0; }" in css and ".pill {" in css


def test_empty_title_is_section_title():
    prep = read("prep.html")
    assert re.search(r"\.big\.none b \{ font-size:16px; \}", prep) and ".big.none { padding-top:16px; }" in prep
    assert '$("p-big").parentNode.classList.toggle("none", !prof)' in prep


def test_last_table_is_remembered_and_restored():
    common, prep = read("common.js"), read("prep.html")
    assert 'localStorage.setItem(LAST_TABLE, JSON.stringify({ conn: connName, schema: S.schema || "", base: S.base }))' in common
    assert "restoreLastTable()" in prep and "last.conn !== connName" in prep


@pytest.mark.skipif(EDGE is None or shutil.which("node") is None, reason="Edge가 없다")
def test_no_small_targets_headless(tmp_path):
    """홈·쿼리·분석·수집·연동을 헤드리스로 열어 보이는 단추·입력·라벨 중 24px 미만이 없다(체크·라디오 자체는 16px이고 감싼 라벨이 24px 이상이면 통과). JS 오류도 0."""
    websocket = pytest.importorskip("websocket")  # websocket-client는 로컬 전용(CI에는 없음)
    from werkzeug.serving import make_server
    from core import server
    from query.search_app import app
    from collect import collect_app  # noqa: F401  /collect 라우트(conftest는 prep·integrations만 올린다)
    http = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    port, cdp = http.server_port, 9341
    profile = tempfile.mkdtemp(prefix="u2edge_")
    spawn_edge([EDGE, "--headless=new", f"--remote-debugging-port={cdp}", "--remote-allow-origins=*", f"--user-data-dir={profile}", "--no-first-run",
                       "--disable-extensions", "--window-size=1440,900", f"http://127.0.0.1:{port}/?key={server.KEY}"])
    ws, n = None, [0]

    def call(method, **params):
        n[0] += 1
        ws.send(json.dumps({"id": n[0], "method": method, "params": params}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == n[0]:
                return msg.get("result", msg)

    js = """(async () => { await new Promise(r => setTimeout(r, 1500));
      const bad = [];
      document.querySelectorAll('button, a[href], input, select, textarea, summary, label').forEach(e => {
        const r = e.getBoundingClientRect(), box = e.matches('input[type=checkbox],input[type=radio]');
        if (!r.width || !r.height || e.offsetParent === null || e.type === 'hidden') return;
        if (box ? (e.closest('label')?.getBoundingClientRect().height ?? 0) >= 23.5 : (e.tagName === 'LABEL' && !e.querySelector('input[type=checkbox],input[type=radio]'))) return;
        if (r.height < 23.5) bad.push(e.tagName + '#' + e.id + ' ' + Math.round(r.width) + 'x' + Math.round(r.height) + ' ' + (e.innerText || '').slice(0, 16));
      });
      return JSON.stringify({ bad, errs: window.__errs || [] }); })()"""
    try:
        t0 = time.time()
        while ws is None and time.time() - t0 < 30:
            try:
                page = next(p for p in json.load(urllib.request.urlopen(f"http://127.0.0.1:{cdp}/json", timeout=1)) if p["type"] == "page" and p["url"].startswith("http://127.0.0.1"))
                ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=60)
            except Exception:
                time.sleep(0.3)
        assert ws, "Edge에 연결하지 못함"
        call("Page.enable")
        call("Page.addScriptToEvaluateOnNewDocument", source="window.__errs=[];addEventListener('error',e=>__errs.push(e.message));addEventListener('unhandledrejection',e=>__errs.push(String(e.reason)));")
        call("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)
        for path in ("/", "/search", "/prep", "/collect", "/integrations"):
            call("Page.navigate", url=f"http://127.0.0.1:{port}{path}")
            time.sleep(2)
            out = json.loads(call("Runtime.evaluate", expression=js, awaitPromise=True, returnByValue=True)["result"]["value"])
            assert out["bad"] == [] and out["errs"] == [], (path, out)
    finally:
        if ws:
            ws.close()
        kill_edge(profile)
        for p in Path(profile).parent.glob("u2edge_*"):
            shutil.rmtree(p, ignore_errors=True)
        http.shutdown()
