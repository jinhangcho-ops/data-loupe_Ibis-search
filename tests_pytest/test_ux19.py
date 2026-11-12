"""(Phase 19) 처음 쓰는 사람용 공통 장치: 쉬운 보기 설정·스위치·접기 규칙, 타입 이름 풀이, DB 오류를 쉬운 말로. 값은 시험 안에서 지어낸다."""
import shutil

import pytest

from test_fix_audit_js import between, read, run_node

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


def test_easy_setting_and_switch(client):
    from core import settings
    assert settings.CHOICES["easy"] == ["on", "off"]
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
