"""테이블 목록·스키마는 연결마다 한 번 묻고 기억한다(검색 칸을 바꿀 때마다 DB에 다시 묻지 않게). 전처리 통계를 끄면 테이블을 골라도 데이터를 읽지 않는다.
지어낸 DuckDB 파일에 실제로 연결하고, 목록·스키마를 묻는 함수를 세어 본다."""
import duckdb
import pytest


@pytest.fixture
def got(folder, tmp_path):
    from core import server
    from query.search_app import app
    from prep import prep_app  # noqa: F401  전처리 라우트
    raw = duckdb.connect(str(tmp_path / "s.duckdb"))
    raw.execute("create table orders as select i as id, i % 3 as store from range(30) t(i)")
    raw.execute("create table stores as select i as store, 's' || i as name from range(3) t(i)")
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "s.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def test_tables_asked_once_per_connection(got, monkeypatch):
    from core import source
    asked = {"list": 0, "table": 0}
    real_list, real_table = source.list_tables, source.get_table
    monkeypatch.setattr(source, "list_tables", lambda *a, **k: asked.__setitem__("list", asked["list"] + 1) or real_list(*a, **k))
    monkeypatch.setattr(source, "get_table", lambda *a, **k: asked.__setitem__("table", asked["table"] + 1) or real_table(*a, **k))
    body = {"base": "orders", "joins": ["stores left store=store"], "limit": 5}
    for _ in range(3):
        assert got.post("/api/columns", json=body).status_code == 200
        assert got.post("/api/query", json=body).get_json()["count"] == 30
    assert asked == {"list": 1, "table": 2}  # 목록 한 번, 테이블마다 스키마 한 번
    got.get("/api/tables")  # 화면이 목록을 새로 받으면 다시 묻는다
    got.post("/api/columns", json=body)
    assert asked == {"list": 2, "table": 4}


def test_stats_off_reads_no_rows(got, monkeypatch):
    from core import result
    from core import settings
    assert got.post("/api/settings", json={"stats": "off"}).status_code == 200 and settings.load()["stats"] == "off"
    monkeypatch.setattr(result, "get_count", lambda *a, **k: pytest.fail("통계를 끄면 행 수도 세지 않는다"))
    shape = got.post("/api/shape", json={"base": "orders", "count": False}).get_json()
    assert shape["rows"] is None and [c["name"] for c in shape["columns"]] == ["id", "store"]
    assert got.post("/api/settings", json={"stats": "maybe"}).status_code != 200
