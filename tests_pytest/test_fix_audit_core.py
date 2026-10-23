"""감사 수정 A(core): 고치기 전에는 실패하던 것들을 하나씩 재현한다. 데이터는 테스트 안에서 지어내고 값은 손으로 센다."""
import polars as pl
import pytest
from lazy import ibis

from core import metric_file
from core import parse
from core import server
from core import source
from core import sql_exec
from core import sql_funcs
from core import use_counts


# ---------- 1 연결 열쇠: 같은 폴더의 다른 파일 ----------

def test_connection_key_tells_files_in_same_folder_apart(client):
    keys = []
    for name in ("a.duckdb", "b.duckdb"):
        server.state["opened"] = {"kind": "folder", "path": "C:/data", "file": f"C:/data/{name}"}
        keys.append(server.connection_key())
    assert keys[0] != keys[1]
    from core import dash_store
    server.state["con"] = object()
    found = []
    for name in ("a.duckdb", "b.duckdb"):
        server.state["opened"] = {"kind": "folder", "path": "C:/data", "file": f"C:/data/{name}"}
        found.append(dash_store.current_source()["key"])
    assert found[0] != found[1]


def test_use_counts_keeps_newest_connection_when_full(folder, monkeypatch):
    monkeypatch.setattr(use_counts, "MAX_CONNECTIONS", 2)
    for key in ("a", "a", "b", "b", "c"):
        use_counts.use("connections", key, {"kind": "url", "url": key})
    assert "c" in use_counts.load()["connections"]


# ---------- 2 비밀 매개변수 이름 ----------

@pytest.mark.parametrize("name", ["sslpassword", "private_key_file_pwd", "apikey", "client_secret", "authToken"])
def test_secret_param_by_name(name):
    assert source.secret_param(f"{name}=abc")
    assert not source.secret_param("sslmode=require")


def test_placeholder_only_capital_hyphen():
    assert source.without_placeholder("postgres://u:[YOUR-PASSWORD]@h/db") == "postgres://u@h/db"
    assert source.without_placeholder("postgres://u:[abc]@h/db") == "postgres://u:[abc]@h/db"


# ---------- 3 LLM에 나가는 조건 컬럼 ----------

def test_condition_columns_do_not_leak_quoted_values():
    assert metric_file.condition_columns(["customer = 'Hong Gildong or Kim Minsu'"]) == ["customer"]
    assert metric_file.condition_columns(["a = 1 or b = 2"]) == ["a", "b"]


# ---------- 4 공백 있는 컬럼 이름 ----------

def test_spaced_column_names_in_sort_and_conditions():
    t = ibis.memtable({"order date": [3, 1, 2], "order": [1, 1, 1], "my name": ["x", "y", "z"]})
    assert t.order_by(parse.parse_sort("order date desc", t)).to_polars()["order date"].to_list() == [3, 2, 1]
    assert t.order_by(parse.parse_sort("order date", t)).to_polars()["order date"].to_list() == [1, 2, 3]
    assert t.filter(parse.parse_condition("order date >= 2", t)).count().execute() == 2
    assert t.filter(parse.parse_condition("my name in 'x', y", t)).count().execute() == 2
    assert t.filter(parse.parse_condition("order = 1", t)).count().execute() == 3  # 짧은 이름도 그대로


# ---------- 5 큰 정수 ----------

def test_to_json_sends_huge_ints_as_text():
    got = server.to_json(pl.DataFrame({"big": [2**60, 1], "small": [1, 2]}))
    assert got["rows"][0] == {"big": str(2**60), "small": 1}


# ---------- 6 SQL 검사 ----------

@pytest.mark.parametrize("sql,dialect", [
    ("select pg_read_file('/etc/passwd')", "postgres"), ("select lo_import('/etc/passwd')", "postgres"), ("select nextval('s')", "postgres"),
    ("select set_config('a','b',false)", "postgres"), ("select pg_terminate_backend(1)", "postgres"), ("select * from dblink('x','select 1') as t(a int)", "postgres"),
    ("select load_file('/etc/passwd')", "mysql"), ("select sleep(10)", "mysql"),
    ("select * from openrowset('a','b','c') t", "tsql"), ("select * from postgres_query('db','select 1')", "duckdb")])
def test_dangerous_server_functions_blocked(sql, dialect):
    with pytest.raises(sql_exec.SecurityViolationError):
        sql_exec.check(sql, dialect)


def test_raw_sql_cannot_change_from():
    con = ibis.duckdb.connect()
    con.raw_sql("create table t as select * from (values (1), (2)) v(a)")
    t = con.table("t")
    with pytest.raises(sql_exec.SecurityViolationError):
        sql_funcs.raw_sql(t, "n", '1) AS "n" FROM t -- ')
    assert sql_funcs.raw_expr(t, "n", "a + 1").to_polars()["n"].to_list() == [2, 3]


# ---------- 7 원격 중복 조회 ----------

def test_write_excel_over_limit_counts_while_writing_and_cleans_up(tmp_path, monkeypatch):
    from core import result
    monkeypatch.setattr(result, "EXCEL_MAX_ROWS", 2)
    monkeypatch.setattr(result, "get_count", lambda *a: pytest.fail("미리 세지 않는다"))
    path = tmp_path / "o.xlsx"
    with pytest.raises(ValueError, match="행까지"):
        result.write_excel(ibis.memtable({"a": [1, 2, 3]}), path)
    assert not path.exists()
    result.write_excel(ibis.memtable({"a": [1, 2]}), path)
    assert path.exists()


def test_search_rows_counts_only_when_more(monkeypatch):
    from core import querying
    from core import result
    monkeypatch.setattr(querying, "runnable", lambda body: ibis.memtable({"a": [1, 2, 3]}))
    monkeypatch.setattr(querying, "run", lambda f: f())
    monkeypatch.setattr(result, "get_count", lambda e: pytest.fail("모자라면 세지 않는다"))
    table, count = querying.search_rows({}, 5)
    assert count == 3 and len(table["rows"]) == 3
    monkeypatch.setattr(result, "get_count", lambda e: 3)
    table, count = querying.search_rows({}, 2)
    assert count == 3 and len(table["rows"]) == 2


# ---------- 8 작은 것 ----------

def one_row(**cols):
    return ibis.memtable({k: [v] for k, v in cols.items()})


def test_first_desc_uses_argmax():
    t = ibis.memtable({"g": [1, 1, 1], "v": [10, 20, 30], "k": [1, 2, 3]})
    expr = t.group_by("g").aggregate(f=sql_funcs.aggregate("first", t, "v", None, {"order": [t.k.desc()]}))
    assert expr.to_polars()["f"].to_list() == [30]


def test_date_diff_counts_full_years_and_months():
    import datetime as dt
    t = ibis.memtable({"a": [dt.date(1990, 12, 31), dt.date(2026, 10, 23)], "b": [dt.date(2026, 10, 23), dt.date(1990, 12, 31)]})
    for unit, want in (("year", [35, -35]), ("month", [429, -429]), ("day", [13080, -13080])):
        got = t.mutate(d=sql_funcs.make("date_diff", t, {"x": "a", "y": "b", "unit": unit})).to_polars()["d"].to_list()
        assert got == want, (unit, got)


def test_between_end_date_includes_whole_day():
    import datetime as dt
    t = ibis.memtable({"at": [dt.datetime(2026, 10, 1, 23, 0), dt.datetime(2026, 10, 2, 1, 0)]})
    assert t.filter(parse.parse_condition("at between 2026-10-01 2026-10-01", t)).count().execute() == 1


def test_compare_rounds_half_up():
    from core import compare
    assert compare.format_value(2.5, "currency") == "₩3" and compare.format_value(0.125) == "0.13" and compare.format_value(-0.4, "currency") == "₩0"
    assert compare.format_delta(-2.5, "currency") == "-₩3"


def test_targets_huge_value_is_value_error():
    from core import targets
    with pytest.raises(ValueError):
        targets.parse_value("9" * 400)


def test_read_env_value_ending_with_escaped_backslash(tmp_path):
    from core import settings
    path = tmp_path / ".env"
    b = chr(92)  # 역슬래시 한 글자
    path.write_text("A=\"C:" + b * 2 + "dir" + b * 2 + "\"" + chr(10) + "B=\"x" + b + "\"y\"" + chr(10), encoding="utf-8")
    assert settings.read_env(path) == {"A": "C:" + b + "dir" + b, "B": 'x"y'}


def test_substr_zero_and_split_part_zero_rejected():
    t = ibis.memtable({"s": ["a,b"]})
    for f, args in (("substr", {"x": "s", "start": 0}), ("split_part", {"x": "s", "sep": ",", "n": 0})):
        with pytest.raises(ValueError):
            sql_funcs.make(f, t, args)


def test_date_bounds_unknown_with_or_not_subquery():
    from core import scan_size
    assert scan_size.date_bounds("select * from t where d >= '2026-01-01' and d < '2026-02-01'", None, "d")[0] is not None
    for sql in ("select * from t where d >= '2026-01-01' or x = 1", "select * from t where not (d >= '2026-01-01')",
                "select * from t where x in (select x from u where d >= '2026-01-01')"):
        assert scan_size.date_bounds(sql, None, "d") == (None, None)


def test_read_spec_cached_by_stat(tmp_path, monkeypatch):
    from core import profiler
    path = tmp_path / "s.json"
    path.write_text('{"tables": {"t": {}}}', encoding="utf-8")
    calls = []
    real = profiler.load_spec
    monkeypatch.setattr(profiler, "load_spec", lambda p: calls.append(p) or real(p))
    profiler.read_spec(path)
    profiler.read_spec(path)
    assert len(calls) == 1
    path.write_text('{"tables": {"t": {}, "u": {}}}', encoding="utf-8")
    assert "u" in profiler.read_spec(path)["tables"]


def test_settings_backup_retries_permission_error(folder, monkeypatch):
    from core import settings
    settings.write("x.json", {"a": 1})
    real, tries = settings.shutil.copyfile, []

    def flaky(src, dst):
        tries.append(1)
        if len(tries) < 3:
            raise PermissionError()
        return real(src, dst)
    monkeypatch.setattr(settings.shutil, "copyfile", flaky)
    settings.write("x.json", {"a": 2})
    assert len(tries) == 3 and settings.read("x.json") == {"a": 2}


def test_release_connection_clears_held(monkeypatch):
    import threading
    from core import held
    done = []
    monkeypatch.setattr(source, "interrupt", lambda con: None)
    monkeypatch.setattr(source, "close", lambda con: None)
    monkeypatch.setattr(held, "clear", lambda: done.append(1))
    server.state.update(con=object(), cancel=threading.Event())
    server.release_connection(wait=2)
    server.state["con"] = None
    assert done == [1]


def test_concurrent_saves_keep_every_key(folder):
    """스레드 8개가 동시에 저장해도 키가 사라지지 않는다(읽고-고치고-쓰기 잠금)."""
    import threading
    from core import settings
    for _ in range(5):
        settings.write("things.json", {})
        threads = [threading.Thread(target=lambda i=i: settings.save_named("things.json", "검색", 1000, f"n{i}", {"v": i})) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sorted(settings.read("things.json")) == sorted(f"n{i}" for i in range(8))
