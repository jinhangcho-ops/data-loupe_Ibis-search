"""같은 것을 여러 번 묻지 않는다(N+1 조회): 전처리의 기준 테이블(base_table)과 NL2SQL 문맥은 기억해 둔 목록·스키마를 쓰고,
문장 카드는 빈칸마다가 아니라 기간마다 한 번 집계한다. 지어낸 DuckDB 파일에 실제로 연결하고 묻는 함수를 세어 본다."""
import datetime as dt

import duckdb
import pytest

ROWS = [(dt.date(2026, 1, 1), "A", 10.0), (dt.date(2026, 1, 5), "B", 20.0), (dt.date(2026, 1, 20), "A", 30.0), (dt.date(2026, 2, 1), "B", 40.0)]
CTX = {"range": {"start": "2026-01-12", "end": "2026-02-02"}, "prev": {"start": "2025-12-21", "end": "2026-01-11", "text": "직전 22일"}}  # 이번 70, 직전 30


@pytest.fixture
def got(folder, tmp_path):
    from core import server
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "s.duckdb"))
    raw.execute("create table orders (day date, store varchar, amt double)")
    raw.executemany("insert into orders values (?, ?, ?)", ROWS)
    raw.execute("create table stores as select 'A' as store, '강남' as name")
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "s.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


@pytest.fixture
def asked(got, monkeypatch):
    """연결에 목록(list)·스키마(table)를 물은 횟수."""
    from core import source
    found = {"list": 0, "table": 0}
    real_list, real_table = source.list_tables, source.get_table
    monkeypatch.setattr(source, "list_tables", lambda *a, **k: found.__setitem__("list", found["list"] + 1) or real_list(*a, **k))
    monkeypatch.setattr(source, "get_table", lambda *a, **k: found.__setitem__("table", found["table"] + 1) or real_table(*a, **k))
    return found


def test_prep_base_table_asks_list_and_schema_once(got, asked):
    for _ in range(3):
        shape = got.post("/api/shape", json={"base": "orders", "count": False}).get_json()
        assert [c["name"] for c in shape["columns"]] == ["day", "store", "amt"]
    assert asked == {"list": 1, "table": 1}  # 고치기 전에는 열 때마다(3, 3)
    missing = got.post("/api/shape", json={"base": "order", "count": False})
    assert missing.status_code == 400 and "테이블" in missing.get_json()["error"] and "orders" in missing.get_json()["error"]  # 없는 테이블 안내는 그대로(비슷한 이름)
    assert asked == {"list": 1, "table": 1}


def test_nl2sql_context_asks_each_schema_once(got, asked):
    from core import server
    from query import nl2sql
    con = server.state["con"]
    first = nl2sql.context(con, server.connection_key(), None, server.virtual_tables())
    assert [(t["name"], [c["name"] for c in t["columns"]]) for t in first["tables"]] == [("orders", ["day", "store", "amt"]), ("stores", ["store", "name"])]
    assert asked["table"] == 2  # 테이블마다 한 번
    assert nl2sql.context(con, server.connection_key(), None, server.virtual_tables()) == first
    assert asked["table"] == 2  # 두 번째부터는 묻지 않는다(고치기 전에는 4)


def test_sentence_card_aggregates_once_per_period(got, monkeypatch):
    from dashboard import dash_calc
    calls = []
    real = dash_calc.scalars
    monkeypatch.setattr(dash_calc, "scalars", lambda expr, series: calls.append(len(series)) or real(expr, series))
    amt = {"y": "amt", "agg": "sum"}
    view = {"template": "{매출} {전} {비교} {건수} {기준일} {나쁨}", "compare": {"mode": "span"}, "better": "up", "blanks": [
        {"name": "매출", **amt, "kind": "value", "format": "currency"}, {"name": "전", **amt, "kind": "prev"}, {"name": "비교", **amt, "kind": "delta"},
        {"name": "건수", "agg": "count", "kind": "value"}, {"name": "기준일", "kind": "asof"}, {"name": "나쁨", "y": "store", "agg": "sum", "kind": "value"}]}
    card = {"kind": "sentence", "title": "t", "source": {"schema": "", "table": "orders", "date": "day", "body": {"base": "orders", "series": [amt]}}, "view": view}
    blanks = got.post("/api/dash/card/compute", json={"card": card, "ctx": CTX}).get_json()["data"]["values"]["blanks"]
    assert list(blanks) == ["매출", "전", "비교", "건수", "기준일", "나쁨"]
    assert blanks["매출"] == {"kind": "value", "cur": 70, "prev": None, "format": "currency"}  # 같은 계열을 여러 빈칸이 써도 빈칸마다 제 값
    assert (blanks["전"]["cur"], blanks["전"]["prev"]) == (70, 30) and (blanks["비교"]["cur"], blanks["비교"]["prev"]) == (70, 30) and "직전 22일보다" in blanks["비교"]["text"]
    assert (blanks["건수"]["cur"], blanks["건수"]["prev"]) == (2, None) and blanks["기준일"]["text"] == "2026-02-02"
    assert "숫자 컬럼" in blanks["나쁨"]["error"] and "cur" not in blanks["나쁨"]  # 틀린 빈칸 하나는 이유만, 나머지는 값
    assert calls == [4, 2]  # 이번 기간 한 번(빈칸 4개), 직전 기간 한 번(2개). 고치기 전에는 빈칸마다(7번)
