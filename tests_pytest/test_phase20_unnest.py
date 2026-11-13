"""(Phase 20) 전처리 '목록을 행으로 펼치기'(unnest): 배열 타입·JSON 배열 글자를 원소마다 한 행으로. 데이터는 모두 시험 안에서 지어낸다."""
import duckdb
import polars as pl
import pytest
from lazy import ibis

from core import prep_ops
from core import source
from prep import prep_diag

ROWS = """(1, 'a', '["2023-05-28", "2023-06-02"]', [1, 2]),
          (2, 'b', '[]', []::int[]),
          (3, 'c', NULL, NULL),
          (4, 'd', '{"k": 1}', [7]),
          (5, 'e', '[1, true, {"k": "v"}, null, "x"]', [NULL, 5]),
          (6, 'f', '7', [9])"""


@pytest.fixture
def t():
    con = ibis.duckdb.connect()
    con.raw_sql(f"create table t as select * from (values {ROWS}) v(id, other, s, a)")
    con.raw_sql("create table j as select id, other, cast(s as json) as s from t")
    return con


def rows(expr, name):
    """(id, other, 펼친 값)을 id·값 순으로(행 순서는 연결이 정하지 않는다)."""
    return sorted(expr.select("id", "other", name).to_polars().rows(), key=lambda r: (r[0], str(r[2])))


def test_ops_table_and_labels():
    assert prep_ops.OPS_NAMES[-2:] == ["expand", "unnest"]
    label = prep_ops.LABELS[-1]
    assert (label["id"], label["label"], label["group"], label["fields"]) == ("unnest", "목록을 행으로 펼치기", "새 컬럼", [])
    assert prep_ops.LABELS[-2]["id"] == "expand"


def test_array_column_one_row_per_element(t):
    out = prep_ops.apply_ops(t.table("t"), "a", {"unnest": True})
    assert out.columns == ("id", "other", "s", "a") and out.a.type().is_integer()  # 이름 그대로, 원소 타입 그대로
    assert rows(out, "a") == [(1, "a", 1), (1, "a", 2), (2, "b", None), (3, "c", None), (4, "d", 7), (5, "e", 5), (5, "e", None), (6, "f", 9)]


@pytest.mark.parametrize("table", ["t", "j"])  # 글자 컬럼과 json 타입 컬럼
def test_json_text_one_row_per_element(t, table):
    out = prep_ops.apply_ops(t.table(table), "s", {"unnest": True})
    assert out.s.type().is_string()
    assert rows(out, "s") == [
        (1, "a", "2023-05-28"), (1, "a", "2023-06-02"),  # 글자는 따옴표 없이, 다른 컬럼 값은 복사
        (2, "b", None), (3, "c", None), (4, "d", None),  # 빈 목록·빈 값·객체: 행은 남고 그 컬럼만 빈 값
        (5, "e", "1"), (5, "e", None), (5, "e", "true"), (5, "e", "x"), (5, "e", '{"k":"v"}'),
        (6, "f", None)]  # 배열이 아닌 JSON 값(숫자)


def test_text_that_is_not_json_keeps_row(t):
    t.raw_sql("create table bad as select * from (values (1, 'x', 'not json'), (2, 'y', '[1]')) v(id, other, s)")
    assert rows(prep_ops.apply_ops(t.table("bad"), "s", {"unnest": True}), "s") == [(1, "x", None), (2, "y", "1")]


def test_wrong_type_and_value(t):
    with pytest.raises(ValueError, match="목록.*JSON.*'id' 컬럼은 정수 컬럼"):
        prep_ops.apply_ops(t.table("t"), "id", {"unnest": True})
    with pytest.raises(ValueError, match="'unnest' 값은 true 형식"):
        prep_ops.apply_ops(t.table("t"), "a", {"unnest": {"keys": ["a"]}})


def test_expand_points_lists_to_unnest(t):
    with pytest.raises(ValueError, match="펼칠 키를 찾지 못했어요.*목록이면 '목록을 행으로 펼치기'를 쓰세요"):
        prep_ops.apply_ops(t.table("t").filter(lambda x: x.id == 1), "s", {"expand": True})
    t.raw_sql("create table plain as select 'abc' as s")
    with pytest.raises(ValueError) as e:
        prep_ops.apply_ops(t.table("plain"), "s", {"expand": True})
    assert "펼칠 키를 찾지 못했어요" in str(e.value) and "목록이면" not in str(e.value)  # 배열이 아니면 덧붙이지 않는다


def test_suggest_by_json_shape():
    o = prep_diag.options(None)
    found = lambda objects, arrays: {"kind": "string", "json": {"objects": objects, "arrays": arrays, "rest": 10, "share": (objects + arrays) / 10}}  # noqa: E731
    arrays = prep_diag.suggest("c", "string", found(1, 9), o)
    assert [s["ops"] for s in arrays] == [{"unnest": True}] and arrays[0]["text"] == "목록을 행으로 펼치기"
    assert [s["ops"] for s in prep_diag.suggest("c", "string", found(10, 0), o)] == [{"expand": True}]
    assert prep_diag.suggest("c", "string", found(1, 1), o) == []  # JSON이 적으면 추천하지 않는다


def test_polars_connection_cannot_unnest():
    """파일 연결(Polars)은 행으로 펼치기를 못 한다: 식은 만들어지고 컴파일에서 '지원하지 않는 계산' 계열 오류가 난다(전처리 화면은 이때 이 PC 사본에서 다시 만든다)."""
    p = ibis.polars.connect()
    p.create_table("t", pl.DataFrame({"id": [1, 2], "s": ['["a", "b"]', "[]"], "a": [[1, 2], []]}))
    for name in ("a", "s"):
        out = prep_ops.apply_ops(p.table("t"), name, {"unnest": True})
        assert not source.compiles(out)
        with pytest.raises(source.unsupported_errors()):
            out.to_polars()


@pytest.mark.parametrize("order", ["a, id, price", "id, price, a"])  # 목록 컬럼이 숫자 컬럼보다 앞이든 뒤든
def test_stats_use_rows_before_unnest(t, order):
    """다른 컬럼의 통계(fill mean → minmax)는 펼치기 전 행으로 구한다: 평균 (10+40)/2 = 25 → 0·0.5·1."""
    from prep import prep_expr
    t.raw_sql(f"""create table o as select {order} from (values ([1, 2, 3], 1, 10.0::double), ([4], 2, NULL), ([]::int[], 3, 40.0)) v(a, id, price)""")
    recipe = {"base": "o", "columns": {"a": {"ops": {"unnest": True, "flag": True}}, "price": {"ops": {"fill": {"how": "mean"}, "scale": "minmax"}}}}
    out, _ = prep_expr.shape(t, recipe)
    assert sorted(out.select("id", "price", "a_isnull").to_polars().rows()) == [(1, 0.0, False)] * 3 + [(2, 0.5, False), (3, 1.0, False)]  # flag는 펼치기 전 값(빈 목록도 빈 값이 아님)
    assert prep_expr.changes_rows(recipe) and not prep_expr.changes_rows({"base": "o", "columns": {"a": {"ops": {"flag": True}}}})


def test_preview_counts_unnested_rows(folder, tmp_path):
    from core import server
    from prep import prep_app  # noqa: F401  전처리 라우트
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "x.duckdb"))
    raw.execute("create table t as select * from (values (1, [1, 2, 3]), (2, [4]), (3, []::int[])) v(id, a)")
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "x.duckdb")}).status_code == 200
    got = c.post("/api/preview", json={"base": "t", "columns": {"a": {"ops": {"unnest": True}}}}).get_json()
    c.post("/api/disconnect", json={})
    assert (got["before"], got["after"], len(got["rows"]["rows"])) == (3, 5, 5)


def test_preview_on_file_connection(folder, tmp_path):
    """Parquet 연결의 미리보기: 이 PC 사본(DuckDB)에서 펼쳐 행 수가 늘어난 결과가 나온다."""
    from core import server
    from prep import prep_app  # noqa: F401  전처리 라우트
    from query.search_app import app
    duckdb.connect().execute(f"""copy (select * from (values (1, 'x', '["a", "b"]'), (2, 'y', '[]'), (3, 'z', '["c"]')) v(id, other, s))
                                 to '{(tmp_path / "f.parquet").as_posix()}' (format parquet)""")
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "files": [str(tmp_path / "f.parquet")]}).status_code == 200
    got = c.post("/api/preview", json={"base": "f", "columns": {"s": {"ops": {"unnest": True}}}}).get_json()
    c.post("/api/disconnect", json={})
    assert "error" not in got, got
    assert sorted((r["id"], r["other"], r["s"]) for r in got["rows"]["rows"]) == [(1, "x", "a"), (1, "x", "b"), (2, "y", None), (3, "z", "c")]
