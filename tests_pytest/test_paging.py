"""쪽 넘기기: 결과 받아 두기(held), SQL 탭 쪽 넘기기, 건너뛰기를 못 쓰는 DB의 행 번호 쪽, 행 수·쪽 동시 실행, 계산 코어 수.
주소로 접속한 DB 대신 지어낸 SQLite 파일(sqlite://)에, PC 안의 연결은 지어낸 DuckDB 파일에 실제로 연결한다."""
import os
import sqlite3
import threading

import duckdb
import pytest

ROWS = 23  # 지어낸 행 수. 정렬 값(grp)이 5개뿐이라 같은 값끼리 순서가 정해지지 않는다


def made():
    return [(i, i % 5, f"n{i}") for i in range(ROWS)]


@pytest.fixture
def remote(folder, tmp_path):
    """sqlite:// 주소로 연결한 시험 클라이언트(held.remote가 참인 연결)."""
    import server
    from app import app
    raw = sqlite3.connect(tmp_path / "shop.db")
    raw.execute("create table orders (id integer, grp integer, name text)")
    raw.executemany("insert into orders values (?, ?, ?)", made())
    raw.commit()
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "url", "url": f"sqlite://{tmp_path / 'shop.db'}"}).status_code == 200
    yield lambda api, **body: c.post(api, json=body).get_json()
    c.post("/api/disconnect", json={})


@pytest.fixture
def local(folder, tmp_path):
    """지어낸 DuckDB 파일에 연결한 시험 클라이언트(PC 안에서 계산하는 연결)."""
    import server
    from app import app
    raw = duckdb.connect(str(tmp_path / "shop.duckdb"))
    raw.execute(f"create table orders as select i as id, i % 5 as grp, 'n' || i as name from range({ROWS}) t(i)")
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "shop.duckdb")}).status_code == 200
    yield lambda api, **body: c.post(api, json=body).get_json()
    c.post("/api/disconnect", json={})


def pages(got, size, **body):
    """쪽을 끝까지 넘기며 받은 id들과 마지막 응답."""
    ids, offset, found = [], 0, None
    while True:
        found = got("/api/query", base="orders", limit=size, offset=offset, fresh=offset == 0, **body)
        ids += [r["id"] for r in found["rows"]["rows"]]
        if len(found["rows"]["rows"]) < size:
            return ids, found
        offset += size


def test_held_pages_cover_every_row_once(remote, monkeypatch):
    import held
    fetched = []
    real = held.fetch
    monkeypatch.setattr(held, "fetch", lambda *a, **k: fetched.append(1) or real(*a, **k))
    ids, last = pages(remote, 4, sorts=["grp desc"], count=False)
    assert sorted(ids) == list(range(ROWS)) and len(ids) == ROWS  # 정렬 값이 같은 행이 많아도 쪽마다 겹치거나 빠지지 않는다
    assert last["held"] and last["count"] == ROWS and not last["note"]  # 행 수를 세지 않아도 받아 둔 결과로 안다
    assert fetched == [1]  # DB에서는 한 번만 받았다
    again, _ = pages(remote, 4, sorts=["grp desc"], count=False)
    assert again == ids and fetched == [1, 1]  # [검색 실행](fresh)마다 새로 받고, 같은 결과면 같은 순서


def test_held_sort_and_columns_without_refetch(remote, monkeypatch):
    import held
    first = remote("/api/query", base="orders", limit=5, fresh=True)
    monkeypatch.setattr(held, "fetch", lambda *a, **k: pytest.fail("머리글 정렬·표시 컬럼은 받아 둔 결과에서 한다"))
    found = remote("/api/query", base="orders", limit=5, sorts=["id desc"], columns=["id, name"])
    assert first["held"] and found["rows"]["columns"] == ["id", "name"] and [r["id"] for r in found["rows"]["rows"]] == [22, 21, 20, 19, 18]
    assert "__ibis_row" not in remote("/api/query", base="orders", limit=1)["rows"]["columns"]


def test_held_new_conditions_are_fetched(remote):
    found = remote("/api/query", base="orders", limit=50, conditions=["grp = 1"], fresh=True)
    assert found["held"] and found["count"] == len([i for i in range(ROWS) if i % 5 == 1])
    assert {r["grp"] for r in found["rows"]["rows"]} == {1}


def test_too_big_falls_back_to_db_pages(remote, monkeypatch):
    import held
    monkeypatch.setattr(held, "MAX_ROWS", 3)
    found = remote("/api/query", base="orders", limit=5, offset=5, sorts=["id asc"], fresh=True)
    assert not found["held"] and "받아 두지 않고" in found["note"]
    assert [r["id"] for r in found["rows"]["rows"]] == [5, 6, 7, 8, 9] and found["count"] == ROWS  # 센 행 수는 다시 세지 않고 쓴다
    streamed = remote("/api/query", base="orders", limit=5, count=False, fresh=True)  # 세지 않으면 받다가 상한에서 멈춘다
    assert not streamed["held"] and streamed["count"] is None and len(streamed["rows"]["rows"]) == 5
    assert "받아 두고" not in remote("/api/sql", base="orders", limit=5, offset=5)["sql"]  # DB에서 쪽마다 가져오는 조회를 보여 준다


def test_sql_shows_the_held_query(remote):
    remote("/api/query", base="orders", limit=5, fresh=True)
    shown = remote("/api/sql", base="orders", limit=5, offset=5)["sql"]
    assert shown.startswith("-- 이 조회로 결과를 한 번 받아 두고") and "OFFSET" not in shown.upper()


def test_sql_tab_pages(remote):
    run = lambda offset, fresh=False: remote("/api/sql/run", sql="SELECT id FROM orders ORDER BY grp, id", rows=10, offset=offset, fresh=fresh)
    first, second, third = run(0, True), run(10), run(20)
    got = [r["id"] for d in (first, second, third) for r in d["rows"]["rows"]]
    assert got == sorted(range(ROWS), key=lambda i: (i % 5, i))  # SQL의 ORDER BY 순서 그대로
    assert first["held"] and first["count"] == ROWS and first["more"] and second["more"] and not third["more"]


def test_local_connection_pages_from_db(local):
    ids, last = pages(local, 4, sorts=["id asc"])
    assert ids == list(range(ROWS)) and not last["held"] and last["count"] == ROWS


def test_row_number_pages_match_offset(local, monkeypatch):
    import result
    want = local("/api/query", base="orders", limit=4, offset=8, sorts=["grp desc", "id asc"], columns=["id"])
    monkeypatch.setattr(result, "can_offset", lambda expr: False)  # 건너뛰기를 못 쓰는 DB(Oracle 등)처럼
    got = local("/api/query", base="orders", limit=4, offset=8, sorts=["grp desc", "id asc"], columns=["id"])
    assert got["rows"] == want["rows"] and got["count"] == ROWS
    assert "ROW_NUMBER" in local("/api/sql", base="orders", limit=4, offset=8, sorts=["id asc"])["sql"].upper()


def test_oracle_sql_uses_row_numbers(monkeypatch):
    from lazy import ibis
    import core
    import result
    t = ibis.table({"id": "int64", "grp": "int64"}, name="orders")
    monkeypatch.setattr(core, "filtered", lambda body: t)
    sql = " ".join(ibis.to_sql(core.numbered({"sorts": ["grp desc"]}, 4, 8), dialect="oracle").split()).upper()
    assert "ROW_NUMBER() OVER (ORDER BY" in sql and "BETWEEN 8 AND 11" in sql and "OFFSET" not in sql
    unsorted = " ".join(ibis.to_sql(core.numbered({}, 4, 8), dialect="oracle").split()).upper()  # 정렬이 없어도 Oracle은 ORDER BY가 있어야 한다
    assert 'ROW_NUMBER() OVER (ORDER BY "T0"."ID" ASC, "T0"."GRP" ASC)' in unsorted
    with pytest.raises(ibis.common.exceptions.UnsupportedArgumentError):  # can_offset가 알아보는 오류
        ibis.to_sql(t.limit(4, offset=8), dialect="oracle")
    assert result.can_offset(t)  # SQL로 바꿀 연결이 없는 식은 막지 않는다


def test_count_and_rows_run_together(local, monkeypatch):
    import result
    import source
    seen = []
    real = result.get_count
    monkeypatch.setattr(result, "get_count", lambda expr, backend=None: seen.append((threading.current_thread().name, backend is not None)) or real(expr, backend))
    found = local("/api/query", base="orders", limit=3, sorts=["id asc"])
    assert found["count"] == ROWS and [r["id"] for r in found["rows"]["rows"]] == [0, 1, 2]
    assert seen and seen[0][0].startswith("side") and seen[0][1]  # 행 수는 다른 스레드에서 커서(side)로 셌다
    assert source.side(object.__new__(type("Remote", (), {"name": "postgres"}))) is None  # 주소로 접속한 DB는 함께 돌리지 않는다


def test_threads_setting_applies_to_duckdb(folder, tmp_path):
    import server
    from app import app
    duckdb.connect(str(tmp_path / "a.duckdb")).close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/settings", json={"threads": 2}).status_code == 200
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "a.duckdb")}).status_code == 200
    try:
        assert server.state["con"].con.execute("select current_setting('threads')").fetchone()[0] == 2
    finally:
        c.post("/api/disconnect", json={})
    assert c.post("/api/settings", json={"threads": os.cpu_count() + 1}).status_code != 200  # 이 PC의 코어 수까지만


def test_held_clear_and_cancel(remote):
    import held
    remote("/api/query", base="orders", limit=5, fresh=True)
    folder = held.store["folder"]
    assert folder and os.path.isdir(folder)
    remote("/api/disconnect")
    assert held.store["con"] is None and not os.path.isdir(folder)  # 연결을 끊으면 받아 둔 파일도 지운다


def test_fetch_stops_on_cancel(tmp_path):
    import held
    import source
    from lazy import ibis
    stop = threading.Event()
    stop.set()

    def fetch():  # SQLite 연결은 만든 스레드에서만 쓸 수 있어 연결 스레드에서 만들고 쓴다
        con = ibis.sqlite.connect(tmp_path / "x.db")
        con.raw_sql("create table t as select 1 as a")
        return held.fetch("k", con.table("t"), stop)
    try:
        with pytest.raises(InterruptedError):
            source.on_worker(fetch)()
        assert "k" not in held.store["items"]
        assert not source.on_worker(lambda: held.local().list_tables())()  # 받던 테이블은 지웠다
    finally:
        source.on_worker(held.clear)()
