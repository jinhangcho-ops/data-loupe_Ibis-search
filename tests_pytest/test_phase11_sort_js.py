"""Phase 11: 표 머리글 정렬의 화면 쪽(common.js 한 벌 + 쿼리 집계·차트 표 + 전처리 데이터·컬럼 요약·분석 표). node로 해당 구간만 돌린다. plans/phase-11.md."""
import shutil

import pytest

from test_fix_audit_js import COMMON, STATIC, STUBS, index_slice, run_node

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
INDEX = (STATIC / "query" / "index.html").read_text(encoding="utf-8")
PREP = (STATIC / "prep" / "prep.html").read_text(encoding="utf-8")


def top_function(text, name):
    """화면 파일에서 맨 앞 칸의 `function name(`(async 포함)부터 그 함수의 닫는 줄(맨 앞 칸의 '}')까지."""
    a = text.index(f"function {name}(")
    a = text.rindex("\n", 0, a) + 1
    return text[a:text.index("\n}\n", a) + 3]


def common(tmp_path, body, parts=()):
    return run_node(tmp_path, body, parts=[STUBS, COMMON, *parts])


def test_next_sort_cycles(tmp_path):
    body = """
const a = nextSort(null, "x"), b = nextSort(a, "x"), c = nextSort(b, "x"), d = nextSort(b, "y");
console.log(JSON.stringify({ a, b, c, d }));
"""
    got = common(tmp_path, body)
    assert got == {"a": {"column": "x", "dir": "asc"}, "b": {"column": "x", "dir": "desc"}, "c": None, "d": {"column": "y", "dir": "asc"}}


def test_sort_rows_numbers_dates_text_nulls_last(tmp_path):
    body = """
const col = (rows, c, dir) => sortRows(rows, c, dir).map(r => r[c]);
const nums = [{ v: 10 }, { v: null }, { v: 9 }, { v: -1.5 }, {}];
const dates = [{ v: "2025-03-01" }, { v: "2025-01-02 10:00:00" }, { v: null }, { v: "2025-01-02" }];
const text = [{ v: "나" }, { v: "가10" }, { v: "가9" }, { v: null }, { v: "B" }, { v: "a" }];
const mixed = [{ v: "10" }, { v: 9 }, { v: "x" }];
console.log(JSON.stringify({ na: col(nums, "v", "asc"), nd: col(nums, "v", "desc"), da: col(dates, "v", "asc"), dd: col(dates, "v", "desc"),
  ta: col(text, "v", "asc"), ma: col(mixed, "v", "asc") }));
"""
    got = common(tmp_path, body)
    assert got["na"] == [-1.5, 9, 10, None, None] and got["nd"] == [10, 9, -1.5, None, None]  # 빈 값(null·없음)은 방향과 상관없이 맨 뒤
    assert got["da"] == ["2025-01-02", "2025-01-02 10:00:00", "2025-03-01", None] and got["dd"] == ["2025-03-01", "2025-01-02 10:00:00", "2025-01-02", None]
    assert got["ta"][-1] is None and got["ta"].index("가9") < got["ta"].index("가10") < got["ta"].index("나")  # 한국어 정렬, 글자 속 숫자는 숫자 차례
    assert got["ma"] == [9, "10", "x"]  # 섞인 컬럼은 글자 비교(numeric)


def test_sort_rows_bigint_and_pinned(tmp_path):
    body = """
const big = [{ v: "12345678901234567891" }, { v: 5 }, { v: "12345678901234567890" }];
const vals = [{ k: "b", n: 1 }, { k: "a", n: 3 }, { k: "(빈 값)", n: 9 }, { k: "그 밖", n: 0 }];
const pinned = (r, i) => i >= 2;
console.log(JSON.stringify({ big: sortRows(big, "v", "asc").map(r => r.v), pin: sortRows(vals, "n", "desc", pinned).map(r => r.k) }));
"""
    got = common(tmp_path, body)
    assert got["big"] == [5, "12345678901234567890", "12345678901234567891"]
    assert got["pin"] == ["a", "b", "(빈 값)", "그 밖"]  # 고정 행은 정렬하지 않고 맨 끝에 원래 차례로


def test_sort_rows_stable_and_pure(tmp_path):
    body = """
const rows = [{ g: 1, i: 0 }, { g: 0, i: 1 }, { g: 1, i: 2 }, { g: 0, i: 3 }];
const before = JSON.stringify(rows);
const asc = sortRows(rows, "g", "asc").map(r => r.i), desc = sortRows(rows, "g", "desc").map(r => r.i);
console.log(JSON.stringify({ asc, desc, same: JSON.stringify(rows) === before }));
"""
    got = common(tmp_path, body)
    assert got == {"asc": [1, 3, 0, 2], "desc": [0, 2, 1, 3], "same": True}  # 같은 값은 원래 차례, 원래 배열은 그대로


def test_sort_mark(tmp_path):
    body = """
const s = { column: "x", dir: "desc" };
console.log(JSON.stringify([sortMark(s, "x"), sortMark({ column: "x", dir: "asc" }, "x"), sortMark(s, "y"), sortMark(null, "x")]));
"""
    got = common(tmp_path, body)
    assert got == [{"arrow": " ↓", "aria": ' aria-sort="descending"'}, {"arrow": " ↑", "aria": ' aria-sort="ascending"'},
                   {"arrow": "", "aria": ""}, {"arrow": "", "aria": ""}]


def test_sort_note(tmp_path):
    got = common(tmp_path, "console.log(JSON.stringify([sortNote(3, 10), sortNote(500, null)]));")
    assert got == ["일부 행만 정렬했어요(전체 10개 중 받은 3개).", "일부 행만 정렬했어요(상한 때문에 받은 500개만)."]


HOST = r"""
const host = () => ({ html: "", set innerHTML(v) { this.html = v; }, get innerHTML() { return this.html; }, querySelector: () => null });
"""


def test_result_table_sort_head(tmp_path):
    body = """
const data = { columns: ["이름", "n"], rows: [{ 이름: "a", n: 1 }] };
const plain = host(), sorted = host();
resultTable(plain, data);
resultTable(sorted, data, { column: "n", dir: "asc" });
console.log(JSON.stringify({ plain: plain.html, sorted: sorted.html }));
"""
    got = common(tmp_path, body, [HOST])
    assert "data-sort" not in got["plain"]  # 셋째 인자가 없으면 예전과 같다(SQL 결과·문장 보기)
    assert 'data-sort="이름"' in got["sorted"] and 'data-sort="n"' in got["sorted"] and " ↑" in got["sorted"] and 'aria-sort="ascending"' in got["sorted"]


def test_sorted_table_keeps_sort_when_column_stays(tmp_path):
    body = """
const h = {}, drawn = [];
const draw = (host, d, s) => drawn.push([d.rows.map(r => r.v), s]);
sortedTable(h, { columns: ["v"], rows: [{ v: 2 }, { v: 1 }] }, draw);
h._sort = nextSort(h._sort, "v"); h._sortDraw();
sortedTable(h, { columns: ["v"], rows: [{ v: 3 }, { v: 4 }, { v: 0 }] }, draw);  // 같은 컬럼이 있는 새 결과: 정렬이 남는다
sortedTable(h, { columns: ["w"], rows: [{ w: 1 }] }, draw);  // 컬럼이 없으면 원래 순서
console.log(JSON.stringify({ drawn, last: h._sort }));
"""
    got = common(tmp_path, body)
    assert got["drawn"][0] == [[2, 1], None] and got["drawn"][1] == [[1, 2], {"column": "v", "dir": "asc"}]
    assert got["drawn"][2] == [[0, 3, 4], {"column": "v", "dir": "asc"}] and got["last"] is None


def test_sorted_table_note_slice_and_shown(tmp_path):
    body = """
const notes = [], drawn = [];
const h = { insertAdjacentHTML: (where, html) => notes.push([where, html]) };
const data = { columns: ["k", "n"], rows: [{ k: "a", n: 1 }, { k: "b", n: 3 }, { k: "c", n: 2 }, { k: "(빈 값)", n: 9 }] };
const opts = { pinned: (r, i) => i >= 3, cut: { total: 10 }, slice: rows => rows.slice(0, 2) };
sortedTable(h, data, (host, d, s) => drawn.push(d.rows.map(r => r.k)), opts);
const before = { notes: notes.length, shown: h._shown.rows.map(r => r.k) };
h._sort = nextSort(nextSort(h._sort, "n"), "n"); h._sortDraw();  // n 내림차순
console.log(JSON.stringify({ before, drawn, notes, shown: h._shown.rows.map(r => r.k) }));
"""
    got = common(tmp_path, body)
    assert got["before"] == {"notes": 0, "shown": ["a", "b", "c", "(빈 값)"]}  # 정렬하지 않으면 안내 없음
    assert got["drawn"] == [["a", "b"], ["b", "c"]]  # 결과 전체를 정렬한 뒤 자른다
    assert got["shown"] == ["b", "c", "a", "(빈 값)"]  # 내보내기는 자르기 전, 화면 정렬 차례(고정 행은 맨 끝)
    assert got["notes"] == [["beforeend", '<div class="hint sort-note">일부 행만 정렬했어요(전체 10개 중 받은 3개).</div>']]


def test_index_grid_spec_own_sort(tmp_path):
    code = index_slice("function gridSpec(data", "// 검색과 [SQL 보기]")
    prelude = 'const S = { sorts: ["a asc"], base: "t" };\n'
    body = """
const data = { columns: ["a", "b"], rows: [{ a: 1, b: "x" }] };
console.log(JSON.stringify({ rows: gridSpec(data).head, own: gridSpec(data, {}, { column: "b", dir: "desc" }).head, none: gridSpec(data, {}, null).head }));
"""
    got = common(tmp_path, body, [prelude, code])
    assert "data-sort" not in got["rows"] and "a ↑" in got["rows"]  # 결과 표(#rows)는 서버 정렬 S.sorts 그대로
    assert 'data-sort="a"' in got["own"] and 'data-sort="b"' in got["own"] and "b ↓" in got["own"] and "↑" not in got["own"]
    assert 'data-sort="a"' in got["none"] and "↑" not in got["none"] and "↓" not in got["none"]


def test_index_tables_use_sorted_table():
    for host in ("summary", "pivot", "pt-out", "ctable"):
        assert f'sortedTable($("{host}")' in INDEX, host
    assert 'mountTable($("ctable"), gridSpec(chartData()))' not in INDEX
    assert 'mountTable($("summary"), gridSpec(last.summary))' not in INDEX


def test_index_exports_follow_screen_order():
    assert 'saveCsv(chartRowsForExport(), "chart.csv")' in INDEX and 'saveCsv($("pt-out")._shown, "period_table.csv")' in INDEX
    assert 'const { columns, rows } = $("summary")._shown' in top_function(INDEX, "summaryImage")
    assert 'table: $("summary")._shown' in INDEX and 'table: chartRowsForExport()' in INDEX
    assert "truncated: !!d.truncated" in top_function(INDEX, "toDrawn")
    assert "drawn.truncated ? { cut: { total: null } }" in INDEX and "d.more ? { cut: { total: null } }" in INDEX  # 잘린 차트 표·피벗은 정렬 안내
    assert "ptData.rows.map(r => r[0])" not in top_function(INDEX, "showPeriodTable")  # 기간별 차트도 화면 정렬 차례(draw의 행)로


SORT_PRELUDE = r"""
const els = {}; globalThis.$ = id => els[id] ??= { textContent: "" };
let vsort = null, sortedView = null, token = 1, renders = 0;
const calls = [];
const renderGrid = () => renders++;
const act = (button, fn) => fn({ base: "t", filters: [] });
const timedHere = (kind, fn) => fn();
const api = async (url, body) => { calls.push([url, body]); return { rows: { columns: ["v"], rows: [] }, view_sort: body.view_sort }; };
"""


def test_prep_grid_sort_button_calls_sorted_route(tmp_path):
    body = """
(async () => {
  await sortGrid("v", {});
  const first = { vsort, calls: calls.map(c => [c[0], c[1].view_sort, c[1].base]) };
  await sortGrid("v", {});
  console.log(JSON.stringify({ first, second: vsort, n: calls.length, renders }));
})();
"""
    got = common(tmp_path, body, [SORT_PRELUDE, top_function(PREP, "sortGrid")])
    assert got["first"] == {"vsort": {"column": "v", "dir": "asc"}, "calls": [["/api/prep/sorted", {"column": "v", "dir": "asc"}, "t"]]}
    assert got["second"] == {"column": "v", "dir": "desc"} and got["n"] == 2 and got["renders"] == 2


def test_prep_grid_sort_cycle_back_to_original(tmp_path):
    body = """
(async () => {
  vsort = { column: "v", dir: "desc" }; sortedView = { rows: {} };
  await sortGrid("v", {});
  console.log(JSON.stringify({ vsort, sortedView, n: calls.length, renders }));
})();
"""
    got = common(tmp_path, body, [SORT_PRELUDE, top_function(PREP, "sortGrid")])
    assert got == {"vsort": None, "sortedView": None, "n": 0, "renders": 1}  # 원래 순서는 다시 조회하지 않는다
    assert 'data-vsort' in PREP and "sortGrid(" in PREP


def test_prep_analysis_tables_use_sorted_table():
    assert 'sortedTable($("a-table")' in PREP
    assert "sortedTable(" in top_function(PREP, "renderSpec") and "sortedTable(" in top_function(PREP, "segTables")
    assert 'resultTable($("a-table"), anaTable(' not in PREP
    ana = top_function(PREP, "drawAna")
    assert "slice: rows => anaRows(" in ana and "cut" in ana and "d.values.unique" in ana


def test_prep_ana_rows_sort_then_cut(tmp_path):
    body = """
const rows = ["a", "b", "c", "d", "e", "(빈 값)", "그 밖"].map(k => ({ k }));
const ks = r => r.map(x => x.k);
console.log(JSON.stringify([ks(anaRows(rows, 5, 1, 4)), ks(anaRows(rows, 5, 0, 2)), ks(anaRows(rows, 5, 2, 5)), ks(anaRows(rows.slice(0, 2), 0, 0, 0))]));
"""
    got = common(tmp_path, body, [top_function(PREP, "anaRows")])
    assert got == [["b", "c", "d"], ["a", "b"], ["c", "d", "e", "(빈 값)", "그 밖"], ["a", "b"]]  # 끝까지 볼 때만 고정 행, n이 0이면 그대로


def test_prep_spec_view_split_columns():
    spec = top_function(PREP, "renderSpec")
    for name in ("빈 값 비율", "고유값 수", "명세서 설명", "표본 요약", "추천"):
        assert f'"{name}"' in spec, name
    assert "sortedTable($(\"spec-view\")" in spec and "_gone" in spec and "prof.sampled?.rows ?? prof.rows ?? 0" in spec
