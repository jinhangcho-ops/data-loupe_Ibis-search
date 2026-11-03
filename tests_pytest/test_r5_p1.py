"""R5 P1(전처리 화면 static/prep/prep.html): 쿼리에서 넘어온 검색(P10), 컬럼 우클릭 메뉴(P4·P5·P5-값·P11), 행 고르기(P12), [+] 새 컬럼 상자(P9),
테이블을 바꿀 때 메모리 놓기(F01), 통계 범위 표본/전체(F18). 글자 확인과 헤드리스 Edge(없거나 websocket-client가 없으면 건너뜀) 확인.
데이터는 시험 안에서 지어낸 임시 DuckDB. 헤드리스는 다운로드를 하지 않는다(Browser.setDownloadBehavior deny)."""
import datetime as dt
import json
import shutil
import socket
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import duckdb
import pytest

from conftest import ROOT, kill_edge, spawn_edge
from test_fix_audit_js import read

EDGE = next((p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if Path(p).exists()), None)
NAMES = ["가", "나", "다", "라", "마"]


# ---------- 글자 확인 ----------

def test_scripts_ids_and_texts():
    html = read("prep.html")
    order = [html.index(f'/static/core/{n}.js') for n in ("common", "funcs", "prepmenu", "rowpick", "calendar", "newcol")]
    assert order == sorted(order)  # 불러오는 순서
    for ident in ("handed-line", "handed-text", "handed-back", "row-bar", "nc-add", "scope-sample", "scope-all", "scope-sample-text", "a-src", "a-src-note"):
        assert f'id="{ident}"' in html, ident
    assert 'id="a-rows"' in html  # 예전 보일 행 수 칸은 그대로
    assert "앞 단계부터 이 시간 안에" in html and "첫 단계부터 이 시간 안에" not in html
    for code in ('api("/api/release"', '"/api/prep/handed?base="', 'saveSetting({ stats_scope', '"/api/profile/full"', "PrepMenu.load()", 'api("/api/tabs")', "PrepMenu.items(", "PrepMenu.valuesBox(", "PrepMenu.replaceBox(",
                 "RowPick.bar(", "RowPick.valueLines(", "NewCol.open(", "selectable: true", "addCol: openNewCol", "f.line ??"):
        assert code in html, code
    for op in ("nulls", "remove", "regex", "outlier", "round", "bins", "rare", "extract", "has", "counts"):  # 값 변환 규칙은 공용 PrepMenu.value 한 벌
        assert f'PrepMenu.value("{op}", st)' in html, op
    assert "const edges = st.text.split" not in html and 'st.sep === "" ? {}' not in html
    assert "need: isStr, init: { spec: null }" not in html and 'replace: { label: "값 바꿔 쓰기", group: "글자", init: { spec: null }' in html  # 값 바꿔 쓰기는 글자 컬럼으로 제한하지 않는다


# ---------- 헤드리스 ----------

class Page:
    """헤드리스 Edge 한 쪽. fetch 호출을 window.__calls에 쌓고, 오류는 window.__errs에 쌓는다."""

    def __init__(self, tmp_path, port):
        self.websocket = pytest.importorskip("websocket")  # websocket-client는 로컬 전용(CI에는 없음)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cdp = s.getsockname()[1]
        self.port, self.n, self.ws = port, 0, None
        self.profile = tempfile.mkdtemp(prefix="p1edge_", dir=tmp_path)
        self.edge = spawn_edge([EDGE, "--headless=new", f"--remote-debugging-port={cdp}", "--remote-allow-origins=*", f"--user-data-dir={self.profile}", "--no-first-run",
                                "--disable-extensions", "--window-size=1440,900", "about:blank"])
        t0 = time.time()
        while self.ws is None and time.time() - t0 < 30:
            try:
                page = next(p for p in json.load(urllib.request.urlopen(f"http://127.0.0.1:{cdp}/json", timeout=1)) if p["type"] == "page")
                self.ws = self.websocket.create_connection(page["webSocketDebuggerUrl"], timeout=90)
            except Exception:
                time.sleep(0.3)
        assert self.ws, "Edge에 연결하지 못함"
        self.call("Browser.setDownloadBehavior", behavior="deny")
        self.call("Page.enable")
        self.call("Page.addScriptToEvaluateOnNewDocument", source="""window.__errs=[];addEventListener('error',e=>__errs.push(e.message));addEventListener('unhandledrejection',e=>__errs.push(String(e.reason)));
window.__calls=[];window.__delay=u=>0;const f=window.fetch;window.fetch=(u,o)=>{window.__calls.push([String(u),(o&&o.body)||'']);const d=window.__delay(String(u));return d?new Promise(r=>setTimeout(r,d)).then(()=>f(u,o)):f(u,o)};""")
        self.call("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)

    def call(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                return msg.get("result", msg)

    def ev(self, expr):
        res = self.call("Runtime.evaluate", expression=f"(async () => {{ {expr} }})()", awaitPromise=True, returnByValue=True)
        assert "exceptionDetails" not in res, json.dumps(res, ensure_ascii=False)[:2000]
        return res["result"].get("value")

    def until(self, cond, secs=20):
        end = time.time() + secs
        while time.time() < end:
            try:
                if self.ev(f"return !!({cond});"):
                    return
            except AssertionError:  # 화면을 아직 읽는 중이라 변수가 없을 수 있다
                pass
            time.sleep(0.2)
        raise AssertionError(f"기다리다 시간이 지났어요: {cond}")

    def nav(self, path, wait=2.0):
        self.call("Page.navigate", url=f"http://127.0.0.1:{self.port}{path}")
        time.sleep(wait)

    def close(self):
        if self.ws:
            self.ws.close()
        kill_edge(self.profile)


@pytest.fixture
def app(folder, tmp_path):
    """임시 DuckDB(orders 30행·other)에 연결하고 진짜 HTTP 서버를 띄운다."""
    from werkzeug.serving import make_server
    from core import server
    from query.search_app import app as flask_app
    raw = duckdb.connect(str(tmp_path / "s.duckdb"))
    raw.execute('create table orders ("주문번호" varchar, "금액" double, "채널" varchar, "주문일" date)')
    raw.executemany("insert into orders values (?, ?, ?, ?)", [(f"o{i:02d}", 1000.0 + i, NAMES[i % 5], dt.date(2025, 3, 1) + dt.timedelta(days=i)) for i in range(30)])
    raw.execute("create table other (k varchar)")
    raw.execute("insert into other values ('a')")
    raw.close()
    c = flask_app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "s.duckdb")}).status_code == 200
    http = make_server("127.0.0.1", 0, flask_app, threaded=True)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    yield c, http.server_port, server.KEY
    http.shutdown()
    c.post("/api/disconnect", json={})


def handoff(c):
    """쿼리 화면이 보내는 것과 같은 모양으로 검색을 넘긴다(보이던 쪽 20행, 전체 30행)."""
    cols = ["주문번호", "금액", "채널"]
    body = {"schema": "", "base": "orders", "joins": [], "periods": [], "conditions": [], "calcs": [], "sorts": [], "columns": cols, "limit": 20}
    shown = {"columns": cols, "rows": [[f"o{i:02d}", 1000.0 + i, NAMES[i % 5]] for i in range(20)], "offset": 0, "total": 30}
    got = c.post("/api/handoff", json={**body, "mode": "query", "name": "넘김", "shown": shown}).get_json()
    assert got["base"] == "@넘김", got
    return got["url"]


needs_edge = pytest.mark.skipif(EDGE is None, reason="Edge가 없다")


@needs_edge
def test_handed_search_screen(app, tmp_path):
    """P10 넘어온 검색(select 숨김·한 줄·표를 먼저 그림·분석 탭 표), P4·P5·P5-값·P11 우클릭 메뉴, P12 행 고르기, P9 [+] 상자, 모두 JS 오류 없이."""
    c, port, key = app
    url = handoff(c)
    page = Page(tmp_path, port)
    try:
        page.nav(f"/?key={key}", 1.0)
        page.call("Page.addScriptToEvaluateOnNewDocument", source="window.__delay = u => u.includes('/api/shape') ? 4000 : 0;")  # 프로필(/api/shape)이 늦게 와도 넘어온 표가 먼저 보여야 한다
        page.nav(url, 0.5)
        page.until("S.handed && document.querySelectorAll('#grid tbody tr').length === 20")
        # 프로필이 오기 전: 넘어온 표 20행이 이미 그려져 있고 ①은 한 줄이다
        early = page.ev("""return { prof: prof === null, rows: document.querySelectorAll('#grid tbody tr').length, big: $('p-big').textContent,
          selects: ['schema','base'].map(i => getComputedStyle($(i)).display), line: $('handed-text').textContent, lineShown: !$('handed-line').classList.contains('hidden') };""")
        assert early["prof"] and early["rows"] == 20 and early["big"] == "30행", early
        assert early["selects"][0] == "none" and early["selects"][1] != "none" and early["lineShown"]  # 스키마만 숨기고 테이블 목록은 남겨 다른 테이블로 돌아갈 수 있다
        assert early["line"] == "쿼리에서 넘어온 검색 '넘김' · 30행 · 컬럼 3개", early
        page.ev("window.__delay = u => 0; return 1;")
        page.until("prof && status === '' && first")
        state = page.ev("""return { rows: document.querySelectorAll('#grid tbody tr').length, heads: [...document.querySelectorAll('#grid th[data-col]')].map(t => t.dataset.col),
          pick: document.querySelectorAll('#grid td.pick input').length, addcol: document.querySelectorAll('#grid th.addcol button').length, base: S.base, ops: PrepMenu.ops.length };""")
        assert state["rows"] == 20 and state["heads"] == ["주문번호", "금액", "채널"] and state["pick"] == 20 and state["addcol"] == 1 and state["base"] == "@넘김" and state["ops"] > 20, state

        # 분석 탭 맨 위 표: 미리보기가 없으면 넘어온 쪽
        ana = page.ev("""showView('ana'); await new Promise(r => setTimeout(r, 300)); const t = $('a-src');
          return { rows: t.querySelectorAll('tbody tr').length, hidden: $('ana').classList.contains('hidden'), note: $('a-src-note').textContent, first: $('a-src').getBoundingClientRect().top < $('a-title').getBoundingClientRect().top }; """)
        assert ana["rows"] == 20 and not ana["hidden"] and "쿼리에서 넘어온" in ana["note"] and ana["first"], ana
        page.ev("showView('prep'); return 1;")

        # 컬럼 줄 우클릭: 항목 = 4 + 단계 수, 안 맞는 단계는 흐리게
        menu = page.ev("""const row = [...document.querySelectorAll('#pcols .pcol')].find(r => r.dataset.col === '금액');
          row.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 600, clientY: 60 }));
          const b = [...document.querySelectorAll('body > .drop .pop button')];
          return { left: document.querySelector('body > .drop').style.left, labels: b.map(x => x.textContent), off: b.filter(x => x.disabled).map(x => [x.textContent, x.title]), ops: PrepMenu.ops.length }; """)
        assert menu["labels"][:2] == ["이 컬럼 빼기", "값 골라서 제외·만 보기…"], menu["labels"][:6]
        assert len(menu["labels"]) == 2 + menu["ops"]  # PrepMenu 항목 전부 + 둘. 같은 상자를 여는 "값 바꾸기…"는 따로 두지 않는다(글자 › 값 바꿔 쓰기)
        assert "값 바꾸기…" not in menu["labels"] and menu["labels"].count("글자 › 값 바꿔 쓰기") == 1
        assert menu["left"] == "600px"  # 누른 자리에 뜬다
        key = page.ev("""document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
          const row = [...document.querySelectorAll('#pcols .pcol')].find(r => r.dataset.col === '금액'), b = row.getBoundingClientRect();
          row.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 0, clientY: 0 }));
          const m = document.querySelector('body > .drop'); return { dx: Math.abs(parseFloat(m.style.left) - b.left), dy: Math.abs(parseFloat(m.style.top) - Math.min(b.bottom, innerHeight - m.getBoundingClientRect().height)) }; """)
        assert key["dx"] < 1 and key["dy"] < 1, key  # 키보드로 열면 (0, 0)이 아니라 컬럼 줄 아래
        assert ["글자 › 앞뒤 공백 지우기", "글자 컬럼에만 쓸 수 있어요"] in menu["off"]  # 숫자 컬럼에서는 글자 단계가 꺼진다
        assert not any(label == "글자 › 값 바꿔 쓰기" for label, _ in menu["off"])  # 값 바꿔 쓰기는 숫자에도 쓸 수 있다

        # 글자 컬럼: 메뉴의 단계 → 정리 방법에 들어가고 ③이 열린다
        out = page.ev("""document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
          const row = [...document.querySelectorAll('#pcols .pcol')].find(r => r.dataset.col === '채널');
          row.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 60, clientY: 60 }));
          [...document.querySelectorAll('body > .drop .pop button')].find(x => x.textContent === '글자 › 앞뒤 공백 지우기').click();
          return { ops: Object.keys(edits['채널'].ops), picked, open: document.querySelector('#prep-view .step[data-step=clean]').classList.contains('open'), menuGone: !document.querySelector('body > .drop') };""")
        assert out == {"ops": ["trim"], "picked": "채널", "open": True, "menuGone": True}, out

        # 값 골라서 제외…: 상자에서 값 2개 → filters
        page.ev("""const row = [...document.querySelectorAll('#pcols .pcol')].find(r => r.dataset.col === '채널');
          row.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
          [...document.querySelectorAll('body > .drop .pop button')].find(x => x.textContent === '값 골라서 제외·만 보기…').click(); return 1;""")
        page.until("document.querySelectorAll('.pm-values input').length >= 5")
        got = page.ev("""const note = document.querySelector('.pm-list .hint').textContent, boxes = [...document.querySelectorAll('.pm-values input')]; const names = boxes.map(b => b.parentNode.textContent.trim());
          boxes.filter(b => ['가', '나'].includes(b.parentNode.textContent.trim())).forEach(b => b.checked = true);
          [...document.querySelectorAll('.pm-box button')].find(b => b.textContent === '고른 값 제외').click();
          return { names: names.sort(), filters: [...filters], chips: $('filters').textContent, note }; """)
        assert got["names"] == ["(빈 값)"] + NAMES and got["filters"] == ["채널 except 가, 나"], got
        assert "일부 행만 보고 모은 목록이에요." in got["note"], got["note"]  # B11 표본에서 모은 목록이라는 안내

        # 글자 › 값 바꿔 쓰기: 상자 → ops.replace.spec, 요약 글
        page.ev("""const row = [...document.querySelectorAll('#pcols .pcol')].find(r => r.dataset.col === '채널');
          row.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
          [...document.querySelectorAll('body > .drop .pop button')].find(x => x.textContent === '글자 › 값 바꿔 쓰기').click(); return 1;""")
        page.until("document.querySelector('.pm-pair input')")
        got = page.ev("""const pair = document.querySelector('.pm-pair'), [a, b] = pair.querySelectorAll('input'); a.value = '가'; b.value = 'A';
          [...document.querySelectorAll('.pm-box label.check')].find(l => l.textContent.includes('대소문자')).querySelector('input').checked = true;
          [...document.querySelectorAll('.pm-box button')].find(x => x.textContent === '적용').click();
          return { spec: edits['채널'].ops.replace.spec, text: [...document.querySelectorAll('#cl-body .op')].find(o => o.textContent.includes('값 바꿔 쓰기')).textContent }; """)
        assert got["spec"] == {"how": "exact", "pairs": [["가", "A"]], "ignore_case": True, "trim": False, "rest": "keep", "to_new": ""}, got
        assert "똑같은 값 1쌍, 대소문자 무시" in got["text"]

        # 옛 꼴로 읽기: [[a, b]] → how exact, 숫자 컬럼에도 들어간다
        got = page.ev("""putOp('금액', 'replace', [['1000', '0']]); renderClean(); return { spec: edits['금액'].ops.replace.spec, value: recipe().columns['금액'].ops.replace.how, old: replaceOf({ from: 'x', to: 'y' }).pairs }; """)
        assert got == {"spec": {"how": "exact", "pairs": [["1000", "0"]], "ignore_case": False, "trim": False, "rest": "keep", "to_new": ""}, "value": "exact", "old": [["x", "y"]]}, got
        page.ev("delete edits['금액'].ops.replace; renderClean(); return 1;")

        # 미리보기 → 필터(채널 except 가, 나)가 걸린 20행, 표는 미리보기 결과
        page.ev("$('preview').click(); return 1;")
        page.until("preview && !$('preview').disabled")
        got = page.ev("""return { rows: document.querySelectorAll('#grid tbody tr').length, after: preview.after, repl: preview.rows.rows.slice(0, 3).map(r => r['채널']) };""")
        assert got["after"] == 18 and got["rows"] == 18 and "가" not in got["repl"] and "나" not in got["repl"], got

        # 행 고르기: 첫 3행 → 막대, [선택한 행 빼기] → 키 컬럼(주문번호) 조건 → 미리보기를 다시 구함
        got = page.ev("""const boxes = [...document.querySelectorAll('#grid td.pick input')]; const k = boxes.length;
          const bar0 = $('row-bar').firstChild.classList.contains('hidden');
          for (let i = 0; i < 3; i++) document.querySelectorAll('#grid td.pick input')[i].click();  // 누를 때마다 표를 다시 그리므로 매번 새로 찾는다
          const info = document.querySelector('#row-bar .rowpick .hint').textContent, shownBar = !$('row-bar').firstChild.classList.contains('hidden');
          [...document.querySelectorAll('#row-bar button')].find(b => b.textContent === '선택한 행 빼기').click();
          return { k, bar0, info, shownBar, last: filters.at(-1) }; """)
        assert got["k"] == 18 and got["bar0"] and got["shownBar"] and got["info"] == "3행을 골랐어요.", got
        assert page.ev("return keyNames();") == []  # 표본으로 구한 고유값은 전체에서 고유하다는 뜻이 아니라 키로 쓰지 않는다
        assert got["last"] == "(주문번호, 금액) except (o02, 1002), (o03, 1003), (o04, 1004)", got  # 정리 방법을 건 채널은 조건에 넣지 않는다
        page.until("preview && !$('preview').disabled && preview.after === 15")
        assert page.ev("return document.querySelectorAll('#grid tbody tr').length;") == 15

        # [+]: 컬럼 목록 옆 단추와 머리글 끝 단추가 같은 상자를 연다
        got = page.ev("""const close = () => document.querySelectorAll('.nc-box').forEach(x => x.parentNode.click());
          $('nc-add').click(); const a = document.querySelectorAll('.nc-box').length; close();
          document.querySelector('#grid th.addcol button').click(); const b = document.querySelectorAll('.nc-box').length; close();
          return { a, b, cols: newColCols().map(x => x[0]) }; """)
        assert got["a"] == 1 and got["b"] == 1 and got["cols"][:3] == ["주문번호", "금액", "채널"], got

        # 줄 나누기: 식 → {name, expr}, 전처리 → 정리 방법, 그 밖 → {line}. 미리보기와 칩, 서버가 받아들이는지
        got = page.ev("""const lines = ['식 금액 * 2 이름 두배', '전처리 round 금액 0', '그룹집계 sum(금액) 그룹 채널 이름 채널합', '전처리 round 두배 0'];
          const plan = planLines(lines); const pv = await previewLines(['식 금액 + 1 이름 더하기']);
          addLines({ lines }); await new Promise(r => setTimeout(r, 100));
          return { made: plan.made, ops: plan.ops, pv: [pv.columns, pv.rows.length], chips: [...document.querySelectorAll('#formulas .chip, #formulas > div')].map(x => x.textContent.trim()), round: edits['금액'].ops.round }; """)
        assert got["made"] == [{"name": "두배", "expr": "금액 * 2"}, {"line": "그룹집계 sum(금액) 그룹 채널 이름 채널합"}, {"line": "전처리 round 두배 0"}], got
        assert got["ops"] == [["금액", "round", 0]] and got["pv"] == [["더하기"], 5] and got["round"] == {"digits": "0"}, got
        assert any("그룹집계 sum(금액) 그룹 채널 이름 채널합" in t for t in got["chips"]) and any("두배 = 금액 * 2" in t for t in got["chips"]), got["chips"]
        # B10 이미 있는 이름으로 추가하면 상자를 닫지 않고 입력을 둔다
        got = page.ev("""openNewCol(); const box = document.querySelector('.nc-box');
          box.querySelector('.nc-method[data-id=expr]').click(); box.querySelector('textarea').value = '금액 + 1'; box.querySelector('.nc-foot input').value = '두배';
          [...box.querySelectorAll('.nc-foot button')].find(b => b.textContent === '추가').click(); await new Promise(r => setTimeout(r, 300));
          const out = { open: !!document.querySelector(".nc-box"), err: document.querySelector('.nc-box .err')?.textContent, behind: $('err-prep').textContent, name: document.querySelector('.nc-box .nc-foot input')?.value, count: formulas.filter(f => f.name === '두배').length };
          document.querySelectorAll('.nc-box').forEach(x => x.parentNode.click()); return out; """)
        assert got["open"] and "'두배' 계산 컬럼이 이미 있어요" in got["err"] and got["behind"] == "" and got["name"] == "두배" and got["count"] == 1, got  # B10 이유는 상자 안에(뒤의 #err-prep이 아니라)
        page.ev("$('preview').click(); return 1;")
        page.until("preview && !$('preview').disabled && preview.rows.columns.includes('채널합')")
        got = page.ev("""const r = preview.rows; return { cols: r.columns, one: r.rows[0]['두배'], sum: r.rows[0]['채널합'], err: $('err-prep').textContent }; """)
        assert "두배" in got["cols"] and got["one"] is not None and got["sum"] is not None and got["err"] == "", got
        # R4 새 컬럼 상자에 값 목록(values)을 준다
        got = page.ev("""const open = NewCol.open; let seen; NewCol.open = o => { seen = o; return open(o); }; openNewCol(); NewCol.open = open;
          const d = await seen.values('채널'); document.querySelectorAll('.nc-box').forEach(x => x.parentNode.click());
          return { type: typeof seen.values, labels: d.values, partial: d.partial }; """)
        assert got["type"] == "function" and got["partial"] is True and sorted(got["labels"]) == sorted(NAMES), got
        # R3 행 고르기: 정리 방법이 걸린 컬럼만 있으면 막대가 잠기고 빈 줄이 filters에 들어가지 않는다
        got = page.ev("""putOp('주문번호', 'trim', true); putOp('금액', 'round', 0); renderGrid(); const n = filters.length;
          document.querySelectorAll('#grid td.pick input')[0].click();
          const b = [...document.querySelectorAll('#row-bar button')], drop = b.find(x => x.textContent === '선택한 행 빼기');
          const out = { disabled: drop.disabled, only: b.find(x => x.textContent === '선택한 행만 남기기').disabled, info: document.querySelector('#row-bar .rowpick .hint').textContent, same: filters.length === n, empty: filters.includes('') };
          delete edits['주문번호'].ops.trim; delete edits['금액'].ops.round; renderGrid(); return out; """)
        assert got["disabled"] and got["only"] and got["same"] and not got["empty"], got
        # 파일 연결(flat)에서는 '빈 값 처리' 상자에 앞·뒤 값 채우기가 없다(정리 칸 OPS.fill과 같은 규칙)
        got = page.ev("""const was = connected, how = flat => { connected = flat; document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
            [...document.querySelectorAll('#pcols .pcol')].find(r => r.dataset.col === '채널').dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
            [...document.querySelectorAll('body > .drop .pop button')].find(x => x.textContent === '빈 값 처리' || x.textContent.endsWith('빈 값 처리')).click();
            const o = [...document.querySelectorAll('.pm-box select option')].map(x => x.value); document.querySelectorAll('.pm-box').forEach(x => x.parentNode.click()); return o; };
          const out = { flat: how('flat'), other: how('other') }; connected = was; return out; """)
        assert "prev" not in got["flat"] and "drop" in got["flat"] and "prev" in got["other"] and "next" in got["other"], got
        # 12: 값 변환은 공용 PrepMenu.value가 한다(prep 쪽 옛 규칙과 같은 결과, 무한 수는 막는다)
        got = page.ev("""return [OPS.nulls.value({ text: '-, N/A', blank: true }), OPS.round.value({ digits: '2' }), OPS.round.value({ digits: '-1' }), OPS.bins.value({ text: '0, 10, 20' }),
          OPS.outlier.value({ how: 'drop', by: 'range', low: '1e999', high: '' }), OPS.outlier.value({ how: 'clip', by: 'iqr', k: '' }), OPS.extract.value({ pattern: 'a(b)', group: '1' }), OPS.has.value({ words: 'a, a', regex: false }), OPS.counts.value({ sep: '' })];""")
        assert got == [["-", "N/A", ""], 2, None, [0, 10, 20], None, {"how": "clip", "by": "iqr"}, {"pattern": "a(b)", "group": 1}, None, {}], got
        # P5 이 컬럼 빼기: 원래 컬럼은 keep=false(체크가 꺼짐), 이미 뺀 컬럼은 흐리게
        got = page.ev("""const menu = col => { document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
            [...document.querySelectorAll('#pcols .pcol')].find(r => r.dataset.col === col).dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
            return [...document.querySelectorAll('body > .drop .pop button')]; };
          menu('금액')[0].click();
          const out = { keep: edits['금액'].keep, checked: [...document.querySelectorAll('#pcols .pcol')].find(r => r.dataset.col === '금액').querySelector('input').checked };
          out.again = menu('금액').map(b => [b.textContent, b.disabled]).slice(0, 1);
          return out; """)
        assert got == {"keep": False, "checked": False, "again": [["이 컬럼 빼기", True]]}, got
        errs = page.ev("return window.__errs;")
        assert errs == [], errs
    finally:
        page.close()


@needs_edge
def test_release_and_stats_scope(app, tmp_path, monkeypatch):
    """F01 테이블을 바꾸면 이전 테이블로 /api/release, F18 통계 범위 전체면 표본 통계(/api/profile) 없이 /api/profile/full."""
    from core import settings
    monkeypatch.setitem(settings.CHOICES, "stats_scope", ["sample", "all"])  # core/settings.py에 이 설정이 들어와야 저장된다(필요한 변경)
    c, port, key = app
    page = Page(tmp_path, port)
    try:
        page.nav(f"/?key={key}", 1.0)
        page.nav("/prep", 1.5)
        page.until("[...$('base').options].some(o => o.value === 'orders')")
        page.ev("$('base').value = 'orders'; $('base').onchange(); return 1;")
        page.until("prof && status === '' && first")
        got = page.ev("""return { sample: window.__calls.filter(c => c[0] === '/api/profile').length, full: window.__calls.filter(c => c[0] === '/api/profile/full').length,
          fullBtn: !$('full-stats').classList.contains('hidden'), radios: [$('scope-sample').checked, $('scope-all').checked], label: $('scope-sample-text').textContent }; """)
        assert got["sample"] == 1 and got["full"] == 0 and got["fullBtn"] and got["radios"] == [True, False] and got["label"].startswith("표본 ") and got["label"].endswith("행"), got
        # 다른 테이블로 바꾸면 이전 테이블(orders)을 놓는다
        page.ev("window.__calls.length = 0; $('base').value = 'other'; $('base').onchange(); return 1;")
        page.until("prof && prof.columns.some(c => c.name === 'k') && status === '' && first")
        rel = page.ev("return window.__calls.filter(c => c[0] === '/api/release').map(c => JSON.parse(c[1]));")
        assert rel == [{"schema": "", "base": "orders"}], rel
        # 전체로 바꾸면 이 테이블을 전체 통계로 다시 구한다(표본 통계 없이)
        page.ev("window.__calls.length = 0; $('scope-all').checked = true; $('scope-all').dispatchEvent(new Event('change')); return 1;")
        page.until("prof.whole === 1 && status === '' && first")
        assert page.ev("return window.__calls.filter(c => c[0] === '/api/profile').length;") == 0
        assert page.ev("return $('full-stats').classList.contains('hidden');")  # [전체로 구하기]는 숨긴다
        assert "테이블 전체 1행으로 구한 통계예요." in page.ev("return $('sampled').textContent;")
        assert page.ev("return keyNames();") == ["k"]  # 전체 통계에서 값이 모두 다르면 키
        # 설정이 저장돼 다시 열면 전체로 시작한다: 표본 통계 요청 없이 전체 요청만
        page.nav("/prep", 1.5)
        page.until("[...$('base').options].some(o => o.value === 'orders')")
        page.ev("window.__calls.length = 0; $('base').value = 'orders'; $('base').onchange(); return 1;")
        page.until("prof && prof.whole === 30 && status === '' && first")
        got = page.ev("""return { sample: window.__calls.filter(c => c[0] === '/api/profile').length, full: window.__calls.filter(c => c[0] === '/api/profile/full').length,
          radios: [$('scope-sample').checked, $('scope-all').checked], nulls: prof.columns.map(c => [c.name, c.nulls, c.unique]), stats: document.querySelector('#cl-body .facts').textContent }; """)
        assert got["sample"] == 0 and got["full"] == 1 and got["radios"] == [False, True], got
        assert got["nulls"] == [["주문번호", 0, 30], ["금액", 0, 30], ["채널", 0, 5], ["주문일", 0, 30]], got["nulls"]
        assert "값 5가지" in page.ev("pickColumn('채널'); return document.querySelector('#cl-body .facts').textContent;")
        assert page.ev("return window.__errs;") == []
    finally:
        page.close()


@needs_edge
def test_tab_names_fill_the_table_list(app, tmp_path):
    """P17 서버 등록부의 목록(GET /api/tabs)이 ① 테이블 목록에 @이름으로 들어가고, 고르면 넘어온 검색처럼 열린다."""
    c, port, key = app
    handoff(c)  # 등록부에 '@넘김'
    page = Page(tmp_path, port)
    try:
        page.nav(f"/?key={key}", 1.0)
        page.nav("/prep", 1.5)
        page.until("[...$('base').options].some(o => o.value === '@넘김')")
        page.ev("$('base').value = '@넘김'; $('base').onchange(); return 1;")
        page.until("prof && status === '' && S.handed && S.handed.base === '@넘김' && first")
        got = page.ev("return { rows: document.querySelectorAll('#grid tbody tr').length, line: $('handed-text').textContent, group: [...$('base').querySelectorAll('optgroup')].map(g => [g.label, g.firstChild.textContent]) };")
        assert got == {"rows": 20, "line": "쿼리에서 넘어온 검색 '넘김' · 30행 · 컬럼 3개", "group": [["쿼리 작업 탭", "넘김"]]}, got
        # 8: 테이블 목록이 숨지 않아 다른 테이블로 돌아갈 수 있고, 돌아가면 넘어온 검색 표시를 놓는다
        assert page.ev("return getComputedStyle($('base')).display !== 'none';")
        page.ev("window.__calls.length = 0; $('base').value = 'orders'; $('base').onchange(); return 1;")
        page.until("prof && status === '' && first && S.base === 'orders'")
        got = page.ev("return { handed: !!S.handed, line: $('handed-line').classList.contains('hidden'), cls: $('table-box').classList.contains('handed'), rows: document.querySelectorAll('#grid tbody tr').length };")
        assert got == {"handed": False, "line": True, "cls": False, "rows": 20}, got  # orders 표본 앞 20행
        # 10: 쿼리 탭이 쓰는 테이블(orders)을 떠나도 받아 둔 결과를 놓지 않는다
        assert page.ev("return [...S.tabBases];") == ["orders"]
        page.ev("window.__calls.length = 0; $('base').value = 'other'; $('base').onchange(); return 1;")
        page.until("prof && prof.columns.some(c => c.name === 'k') && status === '' && first")
        assert page.ev("return window.__calls.filter(c => c[0] === '/api/release').length;") == 0
        assert page.ev("return window.__errs;") == []
    finally:
        page.close()
