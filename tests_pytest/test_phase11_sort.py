"""Phase 11: 전처리 데이터 탭의 머리글 정렬(테이블 전체를 다시 조회). plans/phase-11.md#view_keys·#sorted_head. 데이터는 여기서 지어낸다."""
import sqlite3

import pyarrow as pa
import pytest

from lazy import ibis


def keys_of(t, column, desc):
    from prep import prep_app
    return t.order_by(prep_app.view_keys(t, column, desc)).to_polars()


def test_view_keys_nulls_last_both_ways():
    t = ibis.memtable({"v": [2.0, None, 1.0, 3.0], "k": ["b", "n", "a", "c"]})
    assert keys_of(t, "v", False)["v"].to_list() == [1.0, 2.0, 3.0, None]
    assert keys_of(t, "v", True)["v"].to_list() == [3.0, 2.0, 1.0, None]  # 빈 값은 방향과 상관없이 맨 뒤


def test_view_keys_ties_by_other_columns():
    t = ibis.memtable({"g": [1, 1, 1, 0], "k": ["c", "a", "b", "z"]})
    assert keys_of(t, "g", True)["k"].to_list() == ["a", "b", "c", "z"]  # 같은 g끼리는 나머지 컬럼 오름차순
    from prep import prep_app
    keys = prep_app.view_keys(t, "g", False)
    assert len(keys) == 2  # 주 키 + 나머지 정렬 가능 컬럼 하나


def test_view_keys_rejects_missing_and_nested():
    from prep import prep_app
    t = ibis.memtable(pa.table({"v": [1, 2], "s": pa.array([{"a": 1}, {"a": 2}])}))
    with pytest.raises(ValueError, match="비슷한 이름"):
        prep_app.view_keys(t, "vv", False)
    with pytest.raises(ValueError, match="정렬할 수 없어요"):
        prep_app.view_keys(t, "s", False)


@pytest.fixture
def remote(folder, tmp_path):
    from core import server
    from query.search_app import app
    from prep import prep_app  # noqa: F401
    raw = sqlite3.connect(tmp_path / "s.db")
    raw.execute("create table t (id integer, name text, v real)")
    raw.executemany("insert into t values (?, ?, ?)", [(1, "다", 2.0), (2, "가", None), (3, "나", 5.0), (4, "라", 1.0)])
    raw.commit()
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "url", "url": f"sqlite://{tmp_path / 's.db'}"}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})
    from core import held
    held.store["items"].clear()


def test_sorted_route_orders_whole_table(remote):
    r = remote.post("/api/prep/sorted", json={"base": "t", "view_sort": {"column": "v", "dir": "desc"}})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert [row["v"] for row in d["rows"]["rows"]] == [5.0, 2.0, 1.0, None]
    assert d["view_sort"] == {"column": "v", "dir": "desc"}
    assert len(d["types"]) == len(d["sources"]) == len(d["derived"]) == 3
    assert "before" not in d and "after" not in d  # 행 수는 세지 않는다


def test_sorted_route_applies_recipe_first(remote):
    body = {"base": "t", "filters": ["id >= 2"], "columns": {"name": {"rename": "이름"}}, "sorts": ["id desc"],
            "view_sort": {"column": "이름", "dir": "asc"}}
    r = remote.post("/api/prep/sorted", json=body)
    assert r.status_code == 200, r.get_json()
    assert [row["이름"] for row in r.get_json()["rows"]["rows"]] == ["가", "나", "라"]  # 거른 뒤, 바꾼 이름으로, ⑥ 정렬보다 앞서


def test_sorted_route_rejects_bad_sort(remote):
    for vs in (None, {"column": "v", "dir": "up"}, {"column": "", "dir": "asc"}, "v"):
        r = remote.post("/api/prep/sorted", json={"base": "t", "view_sort": vs})
        assert r.status_code == 400 and "view_sort" in r.get_json()["error"], vs
    r = remote.post("/api/prep/sorted", json={"base": "t", "view_sort": {"column": "vv", "dir": "asc"}})
    assert r.status_code == 400 and "비슷한 이름" in r.get_json()["error"]


def test_sorted_route_goes_through_scan_gate(remote, monkeypatch):
    from core import scan_size
    from core import server
    from prep import prep_app
    seen = []

    def gate(body, make_sql, rows=None):
        seen.append(make_sql())
        return server.jsonify(needs_confirm=True, text="큼")

    monkeypatch.setattr(server, "scan_gate", gate)
    monkeypatch.setattr(prep_app, "local_mode", lambda recipe: False)  # DB가 직접 정렬하는 경우
    monkeypatch.setattr(scan_size, "dry_runs", lambda con: True)  # 읽는 양을 미리 재는 연결로 본다
    r = remote.post("/api/prep/sorted", json={"base": "t", "view_sort": {"column": "v", "dir": "asc"}})
    assert r.get_json().get("needs_confirm") is True and len(seen) == 1
    assert "ORDER BY" in seen[0][0].upper()
    monkeypatch.setattr(scan_size, "dry_runs", lambda con: False)  # 미리 잴 수 없는 연결은 묻지 않는다
    seen.clear()
    r = remote.post("/api/prep/sorted", json={"base": "t", "view_sort": {"column": "v", "dir": "asc"}})
    assert r.status_code == 200 and seen == []
