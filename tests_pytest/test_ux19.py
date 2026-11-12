"""(Phase 19) 처음 쓰는 사람용 공통 장치: 쉬운 보기 설정·스위치·접기 규칙, 타입 이름 풀이, DB 오류를 쉬운 말로. 값은 시험 안에서 지어낸다."""
import json
import re
import shutil
from urllib.parse import quote, unquote

import pytest

from test_fix_audit_js import between, read, run_node

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


def test_easy_setting_and_switch(client):
    from core import settings
    assert settings.CHOICES["easy"] == ["on", "off"]
    assert client.post("/api/settings", json={"adv_open": "%5B%22%2Fsearch%23pager%22%5D"}).status_code == 200
    assert any(c.startswith("adv_open=") and "search" in c for c in client.get("/").headers.getlist("Set-Cookie"))  # 다음 실행의 화면이 쿠키로 받는다
    assert settings.valid("adv_open", "%5B%22%2Fsearch%23pager%22%5D") and not settings.valid("adv_open", "x" * (settings.ADV_OPEN_MAX + 1)) and not settings.valid("adv_open", 1)
    assert client.post("/api/settings", json={"easy": "off"}).status_code == 200
    assert "easy=off" in client.get("/").headers.get("Set-Cookie", "") + " ".join(client.get("/").headers.getlist("Set-Cookie"))
    assert 'id="easy"' in read("status.html") and 'saved("easy") !== "off"' in read("head.html")  # 고른 적이 없으면 켬
    css = read("common.css")
    assert "html.easy [data-adv]:not(.adv-shown) .adv" in css and "html:not(.easy) .adv-more" in css


@needs_node
def test_type_word(tmp_path):
    code = between("common.js", "function typeWord", "advMore();\n")
    got = run_node(tmp_path, "console.log(JSON.stringify(['!int64', 'float64', 'decimal(10, 2)', 'string', 'date', 'timestamp(6)', 'boolean', 'array<string>', 'struct<a: int8>', 'interval', '정수', '실수', '문자'].map(typeWord)));",
                   parts=[code])
    assert got == ["숫자", "숫자", "숫자", "글자", "날짜", "날짜·시각", "예/아니오", "목록", "묶음", "interval", "숫자", "숫자", "글자"]


@needs_node
def test_adv_more_ignores_fields_made_by_script(tmp_path):
    """스크립트가 만든 체크 칸(처음 값이 비어 늘 '바뀐' 것으로 보임)은 묶음을 펼치지 않고, HTML의 칸(id)에 넣은 값은 펼친다."""
    code = between("common.js", "function showAdv", "// 컬럼 타입을 쉬운 말로")
    got = run_node(tmp_path, r"""
const field = (id, type, value, dflt) => ({ id, type, tagName: "INPUT", value, defaultValue: dflt, checked: true, defaultChecked: false });
const box = fields => ({ cls: new Set(), classList: { add(c) { this.o.add(c); }, contains(c) { return this.o.has(c); }, o: null }, fields,
  querySelector: s => s === ".adv" ? {} : null, querySelectorAll: s => s === ".adv-more" ? [{ closest: () => null }] : fields, append() {} });
const a = box([field("", "checkbox", "on", ""), field("", "text", "x", "")]), b = box([field("ymin", "text", "5", "")]);
a.classList.o = a.cls; b.classList.o = b.cls;
advMore({ querySelectorAll: () => [a, b] });
console.log(JSON.stringify([a.cls.has("adv-shown"), b.cls.has("adv-shown")]));""", parts=[code])
    assert got == [False, True]


def open_page(tmp_path, store, click, box_id="pager", limit=2000):
    """/search 화면을 한 번 연다: 묶음(box_id) 하나, 설정 저장소는 store(설정 이름 → 저장된 글자). [펼쳐 있었는지, 끝난 뒤 store]."""
    code = between("common.js", "function showAdv", "// 컬럼 타입을 쉬운 말로")
    stubs = f"""const store = {json.dumps(store)}; const SERVER = {{ adv_open_max: {limit} }};
globalThis.saved = n => store[n] === undefined ? undefined : decodeURIComponent(store[n]);
globalThis.api = (url, body) => {{ Object.assign(store, body); return Promise.resolve({{}}); }};\n"""
    page = rf"""
globalThis.location = {{ pathname: "/search" }};
const b = {{ id: "{box_id}", o: new Set(), btn: null, hasAttribute: a => a === "data-adv", querySelector: s => s === ".adv" ? {{}} : null,
  querySelectorAll: s => s === ".adv-more" ? (b.btn ? [b.btn] : []) : [], append: x => {{ b.btn = x; x.closest = () => b; }} }};
b.classList = {{ add: c => b.o.add(c), remove: c => b.o.delete(c), contains: c => b.o.has(c) }};
globalThis.document = {{ querySelectorAll: () => [b], getElementById: id => id === b.id ? b : null }};
advMore(document); const shown = b.o.has("adv-shown");
if ({json.dumps(click)}) b.btn.onclick();  // 펼쳐 있으면 접고, 접혀 있으면 펼친다
console.log(JSON.stringify([shown, store]));"""
    return run_node(tmp_path, page, parts=[stubs, code])


def adv_list(store):
    return json.loads(unquote(store["adv_open"]))


@needs_node
def test_adv_more_remembers_opened_box(tmp_path):
    """[더 많은 설정]으로 펼친 묶음은 화면을 다시 열어도(설정 adv_open → 쿠키) 펼쳐 있고, [접기]로 접으면 다시 접힌 채로 연다."""
    store, seen = {}, []
    for _ in range(3):  # 화면을 세 번 연다: 처음(접힘) → 펼친 뒤 다시 열기(펼침) → 접은 뒤 다시 열기(접힘)
        shown, store = open_page(tmp_path, store, click=True)
        seen.append(shown)
    assert seen == [False, True, False] and adv_list(store) == ["/search#pager"]  # 마지막에 다시 펼침


@needs_node
def test_adv_list_drops_gone_boxes_and_old_ones(tmp_path):
    """화면에서 빠진 묶음의 기억은 그 화면을 열 때 버리고(다른 화면의 것은 둠), 글자 상한을 넘으면 먼저 펼친 것부터 버린다. id 없는 묶음은 기억하지 않는다."""
    old = {"adv_open": quote(json.dumps(["/search#gone", "/prep#peak-box", "/search#pager"]))}
    shown, store = open_page(tmp_path, old, click=False)
    assert shown and adv_list(store) == ["/prep#peak-box", "/search#pager"]
    _, same = open_page(tmp_path, store, click=False)
    assert same == store  # 바뀐 것이 없으면 저장하지 않는다(같은 글자)
    many = {"adv_open": quote(json.dumps([f"/prep#box{i}" for i in range(40)]))}
    _, store = open_page(tmp_path, many, click=True, limit=200)
    kept = adv_list(store)
    assert kept[-1] == "/search#pager" and "/prep#box0" not in kept and len(store["adv_open"]) <= 200
    shown, store = open_page(tmp_path, {}, click=True, box_id="")
    assert not shown and "adv_open" not in store  # id 없는 묶음: 펼쳐도 기억하지 않음


def test_every_adv_box_has_unique_id():
    """묶음을 순서로 기억하지 않으므로 화면의 [data-adv] 묶음은 모두 id가 있어야 하고 화면 안에서 겹치지 않아야 한다."""
    for name in ("index.html", "prep.html", "collect.html", "integrations.html", "home.html", "status.html", "connect.html"):
        tags = re.findall(r"<[a-z]+\b[^>]*\bdata-adv\b(?!-)[^>]*>", read(name))
        ids = [re.search(r'\bid="([^"]+)"', t) for t in tags]
        assert all(ids), (name, [t for t, i in zip(tags, ids) if not i])
        page_ids = re.findall(r'\bid="([^"]+)"', read(name))
        assert all(page_ids.count(i[1]) == 1 for i in ids), name


def test_schema_box_hidden_when_one_user_schema():
    code = between("common.js", "async function showConnected", "if (hasConnect)")
    assert "information_schema|pg_catalog" in code and '$("schema").classList.add("hidden")' in code and '$("base").options.length > 1' in code


def test_query_step_numbers_count_visible_steps():
    page = read("index.html")
    assert "#flow .step { counter-increment:qstep; }" in page and "content:counter(qstep)" in page


@pytest.mark.parametrize("raw, plain", [
    ('Binder Error: Referenced column "금액x" not found in FROM clause!', "‘금액x’ 컬럼이 없어요."),
    ('Catalog Error: Table with name ordrs does not exist!', "‘ordrs’ 테이블이 없어요."),
    ("Conversion Error: Could not convert string 'abc' to INT32", "값을 바꾸지 못했어요."),
    ('Parser Error: syntax error at or near "FORM"', "SQL 문장을 읽지 못했어요."),
    ("IO Error: No files found that match the pattern", "파일을 열 수 없어요."),
])
def test_engine_errors_in_plain_words(raw, plain):
    from core import server
    out = server.plain_error(raw)
    assert out.startswith(plain) and out.endswith("\n" + raw)  # 쉬운 말 한 줄 + 원문 그대로


def test_own_messages_left_alone():
    from core import server
    assert server.plain_error("조건 값이 비어 있어요.") == "조건 값이 비어 있어요."


def test_real_duckdb_error_reaches_screen_in_plain_words(folder, tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from core import server
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "t.duckdb"))
    raw.execute("create table t as select 1 as a")
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "t.duckdb")}).status_code == 200
    try:
        got = c.post("/api/sql/run", json={"sql": "select nothere from t", "rows": 10, "offset": 0}).get_json()
    finally:
        c.post("/api/disconnect", json={})
    assert got["error"].startswith("‘nothere’ 컬럼이 없어요."), got
