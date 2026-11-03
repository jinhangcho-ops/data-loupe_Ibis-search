"""E2: 스캔·조회 비용. BigQuery 처리 용량 확인(DE5), 전체 프로파일 나눠 집계(DE6), 공통 키 겹침 한 번에(DE8), 쪽 넘김 행 수(DE9),
가장 많은 기간 전체에서(DE11), 펼치기 키 저장(DE13), 정렬 동률 쪽 경계만(DE14), 적재 키 건너뛰기 한 번에(DE15), 전처리 추천·품질 점검(DA20).
원격 DB는 지어낸 SQLite 파일에 실제로 연결하고 BigQuery는 dry run만 흉내 내는 가짜 클라이언트를 붙인다(접속 없음). 그 밖은 DuckDB로."""
import datetime as dt
import sqlite3
import sys
import types

import duckdb
import pyarrow as pa
import pytest
from lazy import ibis

from conftest import spy_queries

ROWS = 23


@pytest.fixture
def remote(folder, tmp_path):
    from core import server
    from query.search_app import app
    raw = sqlite3.connect(tmp_path / "shop.db")
    raw.execute("create table orders (id integer, grp integer, name text)")
    raw.executemany("insert into orders values (?, ?, ?)", [(i, i % 5, f"n{i}") for i in range(ROWS)])
    raw.commit()
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "url", "url": f"sqlite://{tmp_path / 'shop.db'}"}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


@pytest.fixture
def bigquery(remote, monkeypatch):
    """remote 연결을 BigQuery처럼: 이름과 dry run(처리 바이트 10 TB)만 가짜."""
    from core import scan_size
    from core import server
    cloud = types.ModuleType("google.cloud")
    cloud.bigquery = types.SimpleNamespace(QueryJobConfig=lambda **k: types.SimpleNamespace(**k))
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", cloud.bigquery)
    monkeypatch.setattr(scan_size, "limit", lambda config=None: (10 ** 9, "메모리 한도 테스트"))

    class Client:
        def __init__(self):
            self.dry, self.runs = [], []

        def query(self, sql, job_config=None, project=None):
            if job_config is not None and getattr(job_config, "dry_run", False):
                self.dry.append(sql)
                return types.SimpleNamespace(total_bytes_processed=10 * 1024 ** 4)
            self.runs.append(sql)
            raise RuntimeError("실제 조회는 보내지 않아요")
    con = server.state["con"]
    monkeypatch.setattr(con, "name", "bigquery", raising=False)
    con.client = Client()
    yield con.client
    del con.client


# ---------- DE5 BigQuery: DB 안의 집계도 처리 용량을 묻는다 ----------

CHART = {"base": "orders", "x": "grp", "series": [{"y": "id", "agg": "sum"}]}


@pytest.mark.parametrize("api, extra", [("/api/chart", {}), ("/api/values", {"column": "grp"}), ("/api/pivot", {"pivot": {"how": "count"}}),
                                        ("/api/chart/points", {"x": "id", "y": "grp"})])
def test_bigquery_db_side_reads_ask_first(bigquery, remote, api, extra):
    r = remote.post(api, json={**CHART, **extra})
    assert r.headers.get("X-Needs-Confirm") == "1", r.get_json()
    assert bigquery.dry and "읽어요" in r.get_json()["text"]  # 집계라 받는 양이 아니라 읽는 양(dry run)
    assert remote.post(api, json={**CHART, **extra, "confirmed": True}).status_code == 200  # 확인하면 그대로(이 SQLite 연결로 돈다)
    assert bigquery.runs == []


def card():
    return {"kind": "chart", "title": "t", "source": {"schema": "", "table": "orders", "body": CHART}}


def test_bigquery_dash_compute_asks(bigquery, remote):
    r = remote.post("/api/dash/card/compute", json={"card": card()})
    assert r.headers.get("X-Needs-Confirm") == "1" and bigquery.dry
    assert remote.post("/api/dash/card/compute", json={"card": card(), "confirmed": True}).get_json()["data"]
    live = remote.post("/api/dash/cards/compute", json={"cards": [{**card(), "id": "c1"}]}).get_json()["results"]
    assert live[0]["id"] == "c1" and "data" not in live[0] and live[0]["error"]  # 실시간 갱신은 묻지 않고 그 카드를 건너뛴다


def test_bigquery_refresh_does_not_skip_db_aggregates(bigquery, remote):
    from core import dash_store
    from dashboard import dash_refresh
    checked = dash_store.check_card(card())
    assert dash_refresh.scan_decision(checked, None)  # BigQuery는 집계 카드도 읽는 양에 요금이 나서 묻는다


def test_local_dash_compute_unchanged(folder, tmp_path):
    from core import server
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "s.duckdb"))
    raw.execute("create table orders as select i as id, i % 5 as grp from range(23) t(i)")
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "s.duckdb")}).status_code == 200
    try:
        assert c.post("/api/dash/card/compute", json={"card": card()}).get_json()["data"]
        assert "data" in c.post("/api/dash/cards/compute", json={"cards": [{**card(), "id": "c1"}]}).get_json()["results"][0]
    finally:
        c.post("/api/disconnect", json={})


# ---------- DE6 전체 프로파일은 컬럼 8개씩 ----------

def test_full_facts_chunks_columns(monkeypatch):
    from prep import prep_profile
    t = ibis.memtable({f"c{i}": [j % (i + 2) for j in range(50)] for i in range(20)})
    found = spy_queries(monkeypatch)
    got = prep_profile.full_facts(t)
    assert len(found) == 3  # 20컬럼 → 8·8·4
    assert got["rows"] == 50 and [c["name"] for c in got["columns"]] == [f"c{i}" for i in range(20)]
    assert [c["unique"] for c in got["columns"]] == [min(i + 2, 50) for i in range(20)] and got["columns"][3]["max"] == 4


# ---------- DE8 공통 키 겹침은 B를 한 번 읽는 집계로 ----------

def test_join_key_containment_one_query(monkeypatch):
    from query import join_keys
    con = ibis.duckdb.connect()
    a = con.create_table("a", ibis.memtable({"id": list(range(100)), "x1": list(range(100)), "x2": list(range(100))}))
    counts = []
    for width in (1, 4):
        b = con.create_table(f"b{width}", ibis.memtable({**{"id": list(range(100))}, **{f"y{i}": list(range(100)) for i in range(width)}}))
        found = spy_queries(monkeypatch)
        got = join_keys.suggest(a, b, "a", f"b{width}")
        counts.append(len(found))
        monkeypatch.undo()
        assert got["candidates"][0]["a"] == "id" and got["candidates"][0]["contain"] == 1.0
    assert counts[0] == counts[1] == 3  # 표본 둘 + 겹침 집계 하나(짝 수와 무관)


# ---------- DE9 쪽 넘김은 아는 행 수를 그대로 ----------

@pytest.fixture
def local(folder, tmp_path):
    from core import server
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "shop.duckdb"))
    raw.execute("create table orders as select i as id, i % 5 as grp from range(23) t(i)")
    raw.execute("create table t3 as select i, i % 3 as k from range(30) t(i)")
    raw.execute("create table t4 as select i, i % 4 as k from range(30) t(i)")
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "shop.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def test_known_count_is_returned_without_counting(local, monkeypatch):
    found = spy_queries(monkeypatch)
    page = lambda **k: local.post("/api/query", json={"base": "orders", "limit": 5, "offset": 5, **k}).get_json()
    assert page(count=False, known_count=23)["count"] == 23
    assert not any("count(" in q.lower() for q in found)
    for bad in (-1, "23", True, 2.5):
        assert page(count=False, known_count=bad)["count"] is None  # 맞지 않는 값은 무시(세지 않을 때는 지금처럼 null)
    assert page()["count"] == 23


# ---------- DE11 가장 많은 기간은 전체에서 ----------

def test_busiest_finds_old_peak():
    from analysis import stats
    days = [dt.date(2023, 1, 1) + dt.timedelta(days=i) for i in range(3 * 365)]
    t = ibis.memtable({"d": days + [dt.date(2023, 2, 1)] * 5})
    got = stats.busiest(t, "d", "day", 2)
    assert got["periods"][0] == {"start": "2023-02-01", "end": "2023-02-01", "count": 6}
    assert got["periods"][1] == {"start": "2023-01-01", "end": "2023-01-01", "count": 1}  # 건수가 같으면 앞선 기간
    assert stats.busiest(t, "d", "month", 1)["periods"][0]["start"] == "2023-02-01"


# ---------- DE13 펼치기 키를 주면 조회하지 않는다 ----------

def test_expand_with_saved_keys_does_not_query(monkeypatch):
    from core import prep_ops
    con = ibis.duckdb.connect()
    t = con.create_table("j", ibis.memtable({"j": ['{"a": 1, "b": "x"}', '{"a": 2}']}))
    valid = next(o for o in prep_ops.OPS if o[0] == "expand")[2]
    assert valid(True) and valid({"keys": ["a", "b"]}) and not valid({"keys": []}) and not valid({"keys": "a"}) and not valid({"keys": ["a"], "x": 1})
    found = spy_queries(monkeypatch)
    out = prep_ops.expand(t, "j", {"keys": ["a", "b"]})
    assert found == [] and out.columns == ("j", "j.a", "j.b")
    monkeypatch.undo()
    assert out.to_pyarrow().to_pylist() == [{"j": '{"a": 1, "b": "x"}', "j.a": "1", "j.b": "x"}, {"j": '{"a": 2}', "j.a": "2", "j.b": None}]
    assert prep_ops.expand(t, "j", True).columns == ("j", "j.a", "j.b")  # 키가 없으면 지금처럼 앞 행에서 찾는다


# ---------- DE14 정렬 동률은 쪽 경계 두 행만 ----------

def test_tie_note_only_at_page_boundary(local, monkeypatch):
    from query.search_app import LOCAL_PAGES
    found = spy_queries(monkeypatch)
    note = lambda base: local.post("/api/query", json={"base": base, "sorts": ["k"], "limit": 10, "offset": 10}).get_json()["note"]
    assert note("t3") == ""  # 같은 값 10개가 한 쪽에 꼭 맞으면 쪽이 섞이지 않는다
    assert note("t4") == LOCAL_PAGES  # 같은 값이 쪽 경계에 걸침
    assert not any("distinct" in q.lower() for q in found)


# ---------- DE15 적재 키 건너뛰기는 한 번의 조회로 ----------

def test_existing_keys_one_query(tmp_path, monkeypatch):
    from collect import load_table
    from core import source
    con = load_table.connect({"kind": "folder", "file": str(tmp_path / "w.duckdb")}, None)
    try:
        data = pa.table({"k": list(range(2_500)), "g": [i % 3 for i in range(2_500)]})
        load_table.create(con, "", "items", data.schema)
        load_table.insert(con, "", "items", data.slice(0, 1_200))
        found = spy_queries(monkeypatch)
        have = load_table.existing(con, "", "items", ["k", "g"], data)
        monkeypatch.undo()
        assert have == {(i, i % 3) for i in range(1_200)}
        assert len([q for q in found if "items" in q.lower()]) == 1
        assert load_table.look(con, "", "items")[0] == "items" and sorted(source.within(None, lambda: con.list_tables(), "")) == ["items"]  # 올린 임시 표가 남지 않는다
    finally:
        source.close(con)


# ---------- DA20 전처리 추천·품질 점검 ----------

def test_profile_money_fill_and_checks():
    from prep import prep_profile
    n = 100
    t = ibis.memtable(pa.table({
        "amount": [None if i == 0 else float(i) for i in range(n)],
        "score": [None if i == 0 else float(i % 10) for i in range(n - 1)] + [1000.0],
        "id": list(range(n)),
        "d": [dt.date(2026, 1, 1) + dt.timedelta(days=i + (i >= 50) * 3) for i in range(n)],
    }))
    cols = {c["name"]: c for c in prep_profile.profile(t)["columns"]}
    first = cols["amount"]["suggest"][0]
    assert "0으로" in first["text"] and first["ops"] == {"fill": {"how": "value", "value": 0}}
    assert cols["score"]["suggest"][0]["ops"] == {"fill": {"how": "drop"}}  # 금액 단서가 없으면 지금처럼
    assert any("키 후보" in s and "표본 기준" in s for s in cols["id"]["checks"])
    assert any("IQR" in s and "1개" in s for s in cols["score"]["checks"])
    assert any("3일" in s for s in cols["d"]["checks"])
    assert not any("키 후보" in s for s in cols["score"]["checks"])
