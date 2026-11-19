"""연결·쿼리 화면 쓰기 쉽게(v2.3.4): 연결 창의 파일 고르기, 쿼리 칸의 [골라서 넣기 | 직접 쓰기], 테이블 하나면 기준 자동 고르기,
조인 첫 줄, 기간 달력 단추·연도 탐색, 조건의 글자 비교, 예시 데이터. 화면 글자·배치는 정적 검사, 서버 동작은 지어낸 데이터로 돌려 본다."""
import shutil

import duckdb
import ibis
import pytest

from core import demo, parse, settings
from test_fix_audit_js import between, read, run_node


# ---------- 조건: 타입이 안 맞는 값은 글자로 비교 ----------

@pytest.fixture
def table():
    con = ibis.duckdb.connect()
    con.raw_sql("create table t as select * from (values (1, 'apple', date '2025-01-02'), (12, 'pear', date '2025-03-04'), (3, null, null)) v(id, name, d)")
    return con.table("t")


@pytest.mark.parametrize("line, ids", [
    ("id = apple", []),  # 정수 컬럼에 글자: 오류가 아니라 0행
    ("id != apple", [1, 3, 12]),
    ("id = 12", [12]),  # 타입에 맞는 값은 그대로
    ("id in 1, x", [1]),
    ("id except 1, x", [3, 12]),
    ("id like 1", [1, 12]),  # 숫자·날짜 컬럼에도 포함·시작·끝
    ("id starts 1", [1, 12]),
    ("id ends 2", [12]),
    ("d like 2025-03", [12]),
    ("name = apple", [1]),
])
def test_condition_compares_as_text_when_the_value_does_not_fit(table, line, ids):
    assert sorted(table.filter(parse.parse_condition(line, table)).id.to_pyarrow().to_pylist()) == ids


def test_size_comparison_and_matches_still_need_the_right_type(table):
    for line, why in (("id > apple", "'apple'은 정수 값이 아니에요."), ("id between a b", "'b'은 정수 값이 아니에요."), ("id matches 1", "matches는 문자 컬럼에만 쓸 수 있어요.")):
        with pytest.raises(ValueError) as e:
            parse.parse_condition(line, table)
        assert str(e.value) == why


@pytest.mark.parametrize("dialect", ["postgres", "mysql", "mssql", "bigquery", "snowflake", "oracle", "clickhouse", "trino", "sqlite"])
def test_text_comparison_compiles_everywhere(table, dialect):
    for line in ("id = apple", "id in 1, x", "id like 1", "d starts 2025"):
        assert ibis.to_sql(table.filter(parse.parse_condition(line, table)), dialect=dialect)


# ---------- 예시 데이터 ----------

def test_demo_makes_three_tables_once(client):
    info = demo.make()
    path = settings.PATH.parent / demo.FOLDER / demo.FILE
    assert info == {"kind": "folder", "path": str(path.parent), "file": str(path)} and not path.with_name(demo.FILE + ".part").exists()
    with duckdb.connect(str(path), read_only=True) as con:
        counts = dict(con.execute("select table_name, estimated_size from duckdb_tables()").fetchall())
        first = con.execute("select * from 주문 order by 주문ID limit 3").fetchall()
        steps = [r[0] for r in con.execute("select 단계 from 방문기록 group by 단계 order by count(*) desc").fetchall()]
        span = con.execute("select min(주문일), max(주문일) from 주문").fetchone()
    assert counts == {"주문": demo.ORDERS, "고객": demo.CUSTOMERS, "방문기록": demo.VISITS}
    assert steps == ["방문", "상품보기", "장바구니", "결제"]  # 퍼널 모양: 뒤 단계일수록 적다
    assert str(span[0]) >= demo.START and (span[1] - span[0]).days < demo.DAYS
    stamp = path.stat().st_mtime_ns
    assert demo.make() == info and path.stat().st_mtime_ns == stamp  # 있으면 다시 만들지 않는다
    path.unlink()
    demo.make()
    with duckdb.connect(str(path), read_only=True) as con:
        assert con.execute("select * from 주문 order by 주문ID limit 3").fetchall() == first  # 다시 만들어도 같은 값
    got = client.post("/api/demo", json={}).get_json()
    assert got == info
    assert client.post("/api/connect", json=got).status_code == 200  # 돌려준 값 그대로 연결 요청이 된다(시험의 연결은 가짜)


# ---------- 화면(정적) ----------

def test_connect_card_file_picker():
    html, js, css = read("connect.html"), read("common.js"), read("common.css")
    flat = between("connect.html", '<div id="flat"', '<div id="url-box"')
    assert flat.index('id="flat-find"') < flat.index('id="flat-all"') < flat.index('id="flat-list"') < flat.index('id="flat-picked"')
    assert ".seg2 label { justify-content:center; }" in css and ".conn-card .kinds" not in css  # 탭 글자는 가운데, 파일 종류 칩은 다른 칸처럼 왼쪽
    assert "#flat-list label { display:flex;" in css and "max-height:16.25rem" in css and "#flat-list label:hover" in css  # 큰 줄, 줄 전체 클릭
    lists = between("common.js", "async function listFiles()", "connMsg(more")
    assert 'if (!d.files.length && d.flat.length) $("file").value = FLAT;' in lists and '$("file").classList.toggle("hidden", !d.files.length && !!d.flat.length);' in lists  # 표 형식 파일뿐이면 고르는 칸 없이
    assert 'className: "tag"' in lists and "KIND_LABELS[ext] ?? ext.toUpperCase()" in lists  # 줄 끝 꼬리표는 종류 이름(Parquet·CSV…)
    assert "`파일 ${flatBoxes().filter(b => b.checked).length}개를 골랐어요 · 폴더에 ${flatBoxes().length}개`" in js  # 목록 머리: 고른 수와 폴더의 파일 수
    assert "function filterFlat()" in js and '$("flat-find").oninput = filterFlat;' in js
    assert 'flatBoxes().filter(b => !b.parentElement.classList.contains("hidden")).forEach(b => b.checked = true)' in js  # [모두 선택]은 찾기로 남은 파일만
    assert '$("flat-picked").replaceChildren(' in between("common.js", "function showFlat()", "function filterFlat()") and "PICKED_SHOWN" in js
    assert 'id="recent-conn"' in html
    assert "#flat-list label:has(input:checked) { background:var(--accent-bg); }" in css  # 고른 파일 줄은 강조색 바탕


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_connect_button_names_the_picked_file_count(tmp_path):
    """[연결] 단추: 표 형식 파일을 골랐을 때만 "파일 N개 연결"(목록에 안 그린 것도 센다). 고른 것이 없거나 DuckDB 파일·DB 주소면 "연결"."""
    body = r"""
const FLAT = "*flat", state = { kind: "folder", file: FLAT, picked: [] }, button = { textContent: "" };
globalThis.$ = id => ({ connect: button, file: { value: state.file } })[id];
globalThis.document = { querySelector: () => ({ value: state.kind }) };
globalThis.pickedFiles = () => state.picked;
const label = change => { Object.assign(state, change); showConnectLabel(); return button.textContent; };
console.log(JSON.stringify([label({}), label({ picked: ["a.csv", "b.parquet", "c.csv"] }), label({ file: "C:/d/x.duckdb" }), label({ file: FLAT, kind: "url" }), label({ kind: "folder", picked: ["a.csv"] })]));
"""
    code = between("common.js", "function showConnectLabel()", "// 파일 이름 찾기")
    assert run_node(tmp_path, body, parts=[code]) == ["연결", "파일 3개 연결", "연결", "연결", "파일 1개 연결"]
    js = read("common.js")
    assert "  showConnectLabel();\n}" in between("common.js", "function showFlat()", "function showConnectLabel()")  # 체크가 바뀔 때마다
    assert "  showConnectLabel();\n});" in between("common.js", 'document.querySelectorAll("input[name=kind]")', "function fillConnection")  # 종류(파일·DB)를 바꿀 때도
    assert js.count("showConnectLabel()") == 3


def test_query_steps_pick_first_and_direct_by_mode():
    html = read("index.html")
    assert 'const STEP_MODES = ["joins", "periods", "calcs", "sorts", "columns"];' in html
    assert ".edx[data-mode=pick] .direct, .edx[data-mode=line] .pick { display:none; }" in html
    assert '<div class="row direct"><input placeholder="입력하고 Enter">' in html  # 목록의 한 줄 입력은 직접 쓰기에서만
    for key, pick, direct in (("joins", 'id="join-blocks"', 'id="join-line"'), ("periods", 'id="per-cal"', "최근 7일"), ("calcs", 'id="calc-new"', 'id="calc-example" class="direct"'),
                              ("sorts", 'id="sort-col"', "예: 금액 desc"), ("columns", 'id="col-pick"', "예: 이름, 금액.")):
        step = between("index.html", f'data-step="{key}"', "data-adv-slot" if key == "columns" else '<div class="cn')
        assert step.index("pick") < step.index(pick) and direct in step, key
    # 조인: 기준 테이블 = 붙일 테이블 한 줄, 키 줄은 테이블을 고른 뒤에만
    joins = between("index.html", "async function renderJoins()", '$("join-add").onclick')
    assert 'className: "jbase", textContent: S.base' in joins and '"붙일 테이블 고르기"' in joins and "...(j.table ? [...j.pairs.map(pairRow)," in joins
    # 기간: [날짜 컬럼] [시작 날짜] ~ [끝 날짜], 가장 많은 기간 찾기는 접힌 토글 안
    periods = between("index.html", 'data-step="periods"', 'data-step="conditions"')
    assert periods.index('id="per-col"') < periods.index('id="per-cal"') < periods.index('id="per-end"') < periods.index("<details")
    assert periods.index("<summary>데이터가 가장 많은 기간 찾기</summary>") < periods.index('id="pk-col"') < periods.index('id="peak-find"') < periods.index("</details>")
    assert '$("per-cal").onclick = $("per-end").onclick = e =>' in html
    # 새 컬럼: 단추 하나가 새 컬럼 상자를 연다(옛 폼은 없다)
    assert '$("newcol-open").onclick = $("calc-new").onclick = openNewCol;' in html
    for gone in ("cf-kind", "cf-add", "calc-form", "showCalcForm", "pickers.calc"):
        assert gone not in html, gone
    # 정렬: 컬럼과 방향을 골라 더한다
    assert '<option value="asc">작은 것부터 (asc)</option><option value="desc">큰 것부터 (desc)</option>' in html
    assert 'addItem("sorts", `${$("sort-col").value} ${$("sort-how").value}`' in html and 'keepFill($("sort-col"), S.cols.map(c => c[0]), "정렬할 컬럼 고르기");' in html


def test_condition_builder_is_one_row_with_mode_toggle():
    html = read("index.html")
    row = between("index.html", '<div id="builder">', '<input id="bfind"')
    assert row.count('<div class="row') == 1  # 한 줄로 끝난다
    order = [row.index(f'id="{i}"') for i in ("bcol", "bop", "bcol2", "bval", "bval2", "blist", "badd")]
    assert order == sorted(order)
    tabs = between("index.html", '<div id="cond" class="fields">', '<div class="hint format">')
    assert '<span class="seg2" id="bmode"' in tabs and 'name="bmode" value="value" checked> 값으로 찾기' in tabs and 'name="bmode" value="column"> 컬럼으로 찾기' in tabs
    assert 'const byColumn = () => document.querySelector("input[name=bmode]:checked").value === "column";' in html
    assert "#bmode { margin-left:auto; }" in html and "#cond.line :is(#builder, #bmode) { display:none; }" in html  # 오른쪽 위, 직접 쓰기에서는 숨는다
    assert '"in", "except", "like", "starts", "ends", "is null", "is not null"];  // 숫자·날짜도' in html


def test_only_table_becomes_the_base_without_searching():
    html = read("index.html")
    assert 'const openBase = $("base").onchange; $("base").onchange = async () => { await openBase(); autoSearch(); };' in html
    after = between("index.html", "const afterTables = () => {", "\n};")
    assert 'if (only.length === 1 && !$("base").value) { $("base").value = only[0].value; return openBase(); }' in after and "autoSearch" not in after and "runSearch" not in after


def test_calendar_year_view():
    js, css = read("calendar.js"), read("common.css")
    assert 'class="ghost cal-year" data-years' in js and "function years()" in js and "function jump(y)" in js
    assert 'e.key === "Escape" && st.years' in js and "prev.disabled = st.years ||" in js  # 연도 목록에서 Esc는 달 보기로, 달 넘기기는 잠근다
    assert "if (max && m > addMonths(monthOf(max), -1)) m = addMonths(monthOf(max), -1);" in js and "if (min && m < monthOf(min)) m = monthOf(min);" in js  # 범위 밖이면 가장 가까운 달
    assert ".cal-years { display:grid;" in css
