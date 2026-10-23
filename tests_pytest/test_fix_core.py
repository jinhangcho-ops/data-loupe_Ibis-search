"""코드 리뷰 묶음 1(핵심·보안·NaN): 고치기 전에는 실패하던 것들을 하나씩 재현한다. 데이터는 테스트 안에서 지어내고 값은 손으로 센다."""
import datetime as dt
import threading
import time
from decimal import Decimal

import polars as pl
import pyarrow as pa
import pytest
from lazy import ibis

import analysis
import funnel
import metrics
import nl2sql
import profiler
import result
import scan_size
import server
import source
import sql_exec


# ---------- M1 큰따옴표 경로 파일 읽기 ----------

@pytest.mark.parametrize("sql", ['select * from "C:/data/a.csv"', 'select * from "C:\\data\\a"', 'select * from "a.parquet"', 'select * from "/etc/passwd"',
                                 'select * from "x.xlsx" t', 'select * from orders o join "d/b.json" b on o.id = b.id'])
def test_quoted_path_tables_blocked(sql):
    with pytest.raises(sql_exec.SecurityViolationError):
        sql_exec.check(sql)


def test_quoted_plain_table_still_allowed():
    sql_exec.check('select * from "orders" o join "my table" t on o.id = t.id')


# ---------- M2 주소 query의 비밀 ----------

def test_recent_drops_secret_query_values_and_passwords_masks_them():
    info = {"kind": "url", "url": "postgres://u@h:5432/db?sslmode=require&password=abc123&access_token=tok456&api_key=key789&x=1"}
    assert source.recent(info)["url"] == "postgres://u@h:5432/db?sslmode=require&x=1"
    found = source.passwords(info)
    assert {"abc123", "tok456", "key789"} <= set(found)
    assert source.mask("접속 실패 abc123 / tok456 / key789 / sslmode", found) == "접속 실패 *** / *** / *** / sslmode"


def test_recent_keeps_url_without_secrets_exactly():
    assert source.recent({"kind": "url", "url": "db://u@host/x?a=1&b=%20"})["url"] == "db://u@host/x?a=1&b=%20"


# ---------- M4 NL2SQL 지표 조건 값 ----------

def test_nl2sql_prompt_has_no_metric_condition_values(folder, monkeypatch):
    found = metrics.metric("CTR", {"numerator": {"agg": "count", "where": "event = secret_value_xyz"},
                                   "denominator": {"agg": "count", "where": ["event = other_value_abc", "region in 서울, 부산"]}})
    monkeypatch.setattr(metrics, "registry", lambda: {"CTR": found})
    con = ibis.polars.connect()
    con.create_table("t", pl.DataFrame({"event": ["a"], "region": ["b"]}))
    text = nl2sql.system_prompt(nl2sql.context(con, "k"))
    assert "secret_value_xyz" not in text and "other_value_abc" not in text and "서울" not in text
    assert "개수 [event 조건] ÷ 개수 [event, region 조건]" in text
    assert "secret_value_xyz" in metrics.describe(found)  # 지표 화면의 글은 그대로


# ---------- M6 NaN·무한·Decimal ----------

def test_plain_maps_non_finite_to_none():
    for value in (float("nan"), float("inf"), float("-inf"), Decimal("NaN")):
        assert result.plain(value) is None and profiler.plain(value) is None
    assert result.plain(Decimal("1.50")) == 1.5 and profiler.plain(Decimal("1.50")) == 1.5
    assert (result.plain(3), result.plain(2.5), result.plain(None), result.plain(dt.date(2026, 1, 2))) == (3, 2.5, None, "2026-01-02")
    assert profiler.plain(b"ab") == "ab" and profiler.plain("x") == "x" and profiler.plain(True) is True


def test_get_chart_values_are_plain():
    t = ibis.memtable(pa.table({"g": ["a", "a", "b"], "y": [1.0, float("nan"), 3.0]}))
    labels, series, _ = result.get_chart(t, "g", [{"y": "y", "agg": "sum"}])
    assert labels == ["a", "b"] and series[0]["values"] == [None, 3.0]


def test_analysis_treats_nan_as_empty():
    t = ibis.memtable(pa.table({"a": [1.0, 2.0, 3.0, float("nan")], "b": [1.0, 3.0, 2.0, 5.0]}))
    d = analysis.analyze(t, {"kind": "correlation", "columns": ["a", "b"]})
    assert d["n"] == 3 and d["matrix"][0][1] == pytest.approx(0.5)  # (1,1) (2,3) (3,2)의 상관
    s = analysis.analyze(t, {"kind": "scatter", "x": "a", "y": "b"})
    assert len(s["points"]) == 3 and s["r"] == pytest.approx(0.5)
    dist = analysis.analyze(t, {"kind": "distribution", "column": "a"})
    assert dist["stats"]["n"] == 3 and (dist["stats"]["min"], dist["stats"]["max"]) == (1, 3) and sum(dist["histogram"]["counts"]) == 3
    assert dist["values"]["nulls"] == 1


def test_analysis_decimal_becomes_number():
    t = ibis.memtable(pa.table({"d": pa.array([Decimal("1.50"), Decimal("2.00")], pa.decimal128(10, 2))}))
    total = analysis.one(t.aggregate(s=t.d.sum()))["s"]
    assert total == 3.5 and isinstance(total, float)


def test_write_excel_nan_is_blank(tmp_path):
    t = ibis.memtable(pa.table({"k": [1, 2, 3, 4, 5], "y": [1.0, float("nan"), None, float("inf"), 2.0]}))
    path = tmp_path / "o.xlsx"
    result.write_excel(t, path)
    assert pl.read_excel(path)["y"].to_list() == [1.0, None, None, None, 2.0]


# ---------- 창 닫을 때 멈춤 ----------

def test_release_connection_cancels_and_does_not_wait_forever(monkeypatch):
    told, gate = [], threading.Event()
    monkeypatch.setattr(source, "interrupt", lambda con: told.append(con))
    monkeypatch.setattr(source, "close", lambda con: gate.wait(30))  # 도는 쿼리 때문에 닫기가 끝나지 않는 상황
    con = object()
    server.state.update(con=con, cancel=threading.Event())
    start = time.time()
    server.release_connection(wait=0.3)
    assert time.time() - start < 5 and told == [con] and server.state["cancel"].is_set()
    gate.set()
    server.state.update(con=None)


# ---------- 가상 테이블 대소문자 ----------

def test_virtual_table_inside_reference_ignores_case():
    con = ibis.duckdb.connect()
    vts = {"A": {"sql": "SELECT 1 AS x"}, "b": {"sql": "SELECT * FROM a"}}
    assert sql_exec.to_expr(con, "SELECT * FROM b", vts).to_polars()["x"].to_list() == [1]


# ---------- calc 패턴 앞 고정 ----------

def test_name_pattern_is_anchored_at_start():
    import calc
    t = ibis.memtable(pa.table({"amt_a": [1], "my_amt_b": [2], "amt_c": [3]}))
    assert list(t.select(calc.targets(["amt_*"], t)).columns) == ["amt_a", "amt_c"]


# ---------- COUNT(*) 처리 용량 ----------

def test_count_star_does_not_read_every_column():
    assert scan_size.referenced("select count(*) from orders", None)[1] == set()
    assert scan_size.referenced("select count(*), id from orders", None)[1] == {"id"}
    assert scan_size.referenced("select * from orders", None)[1] is None
    assert scan_size.referenced("select id, count(*) from (select * from orders) t group by id", None)[1] is None


# ---------- 테이블 목록·함수 지원 기억 ----------

def test_sql_check_uses_server_table_names_and_forgets(client, monkeypatch):
    import app
    assert not hasattr(app, "KNOWN_TABLES")
    server.state.update(con=object(), opened={"kind": "folder", "path": "x"})
    names = [["a"]]
    monkeypatch.setattr(source, "list_tables", lambda con, schema=None: names[0])
    check = lambda sql: client.post("/api/sql/check", json={"sql": sql}).get_json()
    assert check("select * from b")["ok"] is False
    names[0] = ["a", "b"]
    server.forget_tables()
    assert check("select * from b")["ok"] is True


def test_forget_tables_clears_support_cache():
    server.SUPPORT.update(key=("x",), found={"f": 1})
    server.forget_tables()
    assert not server.SUPPORT


# ---------- 퍼널 시도 규칙 ----------

T0 = dt.datetime(2026, 1, 1)


def funnel_table(rows):
    df = pl.DataFrame({"uid": [r[0] for r in rows], "ev": [r[1] for r in rows], "ts": [T0 + dt.timedelta(seconds=r[2]) for r in rows]},
                      schema={"uid": pl.Int64, "ev": pl.String, "ts": pl.Datetime("us")})
    return ibis.memtable(df.to_arrow())


def test_funnel_window_counts_any_attempt():
    rows = [(1, "a", 0), (1, "b", 1000), (1, "a", 2000), (1, "b", 2050),  # 첫 시도는 늦었지만 둘째 시도가 기간 안
            (2, "a", 0), (2, "b", 500),                                   # 어느 시도도 100초 안에 못 감
            (3, "a", 0), (3, "b", 50), (3, "a", 1000), (3, "b", 1050), (3, "c", 1080),  # 둘째 시도로 c까지
            (5, "a", 0), (5, "a", 10), (5, "b", 20)]                      # 두 시도 모두 b에 닿아도 한 사람은 한 번
    t = funnel_table(rows)
    got = funnel.compute(t, "uid", "ev", "ts", ["a", "b", "c"], 100)
    assert [s["users"] for s in got["steps"]] == [4, 3, 1]
    free = funnel.compute(t, "uid", "ev", "ts", ["a", "b", "c"], None)  # 기간 제한이 없으면 지금과 같다
    assert [s["users"] for s in free["steps"]] == [4, 4, 1]


# ---------- Parquet 단일값 판정 ----------

def test_parquet_constant_needs_stats_in_every_row_group(tmp_path):
    import pyarrow.parquet as pq
    nan = float("nan")
    path = tmp_path / "c.parquet"
    pq.write_table(pa.table({"x": [nan, nan, 5.0, 5.0], "y": [nan, nan, nan, nan], "z": [7.0, 7.0, 7.0, 7.0], "w": [1.0, 1.0, 2.0, 2.0]}), path, row_group_size=2)
    got = profiler.parquet_facts(path)["columns"]
    assert got["x"]["constant"] is False and got["y"]["constant"] is False  # 통계 없는 행 묶음이 있으면 단일값이라 하지 않는다
    assert got["z"]["constant"] is True and got["w"]["constant"] is False


# ---------- 내려받기의 날짜 변환 오류 ----------

def test_download_date_conversion_error_is_friendly(monkeypatch):
    import prep_app  # noqa: F401
    monkeypatch.setattr(result, "export", lambda expr, path, guard=True: (_ for _ in ()).throw(RuntimeError("Conversion Error: invalid date field value")))
    with pytest.raises(ValueError, match="날짜로 바꿀 수 없는 값이 있어요"):
        server.download(object(), "csv")
