"""8차 감사 수정 8A(core): 고치기 전에는 실패하던 것들. 데이터는 시험 안에서 지어낸다."""
import json
import os
import threading
from pathlib import Path

import duckdb
import polars as pl
import pytest

from core import server, source


def rewrite_panic_table(tmp_path):
    """연결한 뒤 같은 이름으로 다시 쓴 Parquet 표(옛 메타데이터로 읽으면 Polars가 패닉한다)."""
    pq = tmp_path / "b.parquet"
    pl.DataFrame({"x": list(range(100000)), "y": ["a" * 20] * 100000}).write_parquet(pq)
    con = source.open_flat_files([pq])
    t = con.table("b")
    pl.DataFrame({"x": [1, 2], "y": ["q", "r"]}).write_parquet(pq)
    return pq, con, t


# ---------- 8A1 BaseException ----------

def test_within_turns_base_exception_into_runtime_error():
    class Panic(BaseException):
        pass

    def boom():
        raise Panic("range end index out of range")

    with pytest.raises(RuntimeError, match="데이터 엔진 내부 오류"):
        source.within(None, boom, "late")
    assert source.within(5, lambda: 42, "late") == 42  # 작업 스레드가 살아 다음 일이 돈다


def test_within_real_polars_panic():
    with pytest.raises(RuntimeError, match="데이터 엔진 내부 오류"):
        source.within(5, lambda: pl.DataFrame({"a": [[b"x"]]}).write_json(), "late")  # 항상 패닉하는 호출
    assert source.worker.is_alive()
    assert source.within(5, lambda: 7, "late") == 7


# ---------- 8A2 Parquet 다시 쓰기 ----------

def test_parquet_rewrite_is_refreshed(tmp_path):
    from core import result
    pq = tmp_path / "b.parquet"
    pl.DataFrame({"x": list(range(100000)), "y": ["a" * 20] * 100000}).write_parquet(pq)
    con = source.open_flat_files([pq])
    assert con.csv_sources["b"][0] == pq
    assert con.table("b").count().to_polars() == 100000
    pl.DataFrame({"x": [1, 2], "z": ["q", "r"]}).write_parquet(pq)
    os.utime(pq, ns=(1, 1))  # 시각이 같아도 크기가 달라 알아보지만 확실히 다르게
    assert source.refresh_csv(con) == ["b"]
    t = con.table("b")
    assert list(t.columns) == ["x", "z"]
    assert t.count().to_polars() == 2
    assert len(result.get_rows(t, 10)) == 2


def test_truncated_parquet_gives_message_not_panic(tmp_path):
    from core import result
    pq = tmp_path / "b.parquet"
    pl.DataFrame({"x": list(range(1000))}).write_parquet(pq)
    con = source.open_flat_files([pq])
    pq.write_bytes(pq.read_bytes()[:40])  # 쓰다 만 파일
    assert source.refresh_csv(con) == ["b"]
    with pytest.raises(Exception) as ei:
        source.within(10, lambda: result.get_rows(con.table("b"), 3), "late")
    assert not isinstance(ei.value, (TimeoutError, InterruptedError))
    assert source.worker.is_alive()


def test_parquet_survives_close(tmp_path):
    pq = tmp_path / "b.parquet"
    pl.DataFrame({"x": [1]}).write_parquet(pq)
    con = source.open_flat_files([pq])
    source.drop_copy(con, con.csv_sources["b"][2])
    assert pq.exists()
    try:
        source.close(con)
    except Exception:
        pass
    assert pq.exists()


def test_rowcount_api_after_parquet_rewrite(tmp_path):
    from query.search_app import app
    pq = tmp_path / "b.parquet"
    pl.DataFrame({"x": list(range(1000))}).write_parquet(pq)
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    r = c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "files": [str(pq)]})
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    assert c.post("/api/query", json={"base": "b", "limit": 3}).get_json()["count"] == 1000
    pl.DataFrame({"x": [1, 2]}).write_parquet(pq)
    assert c.post("/api/query", json={"base": "b", "limit": 3}).get_json()["count"] == 2
    got = c.post("/api/rowcount", json={"base": "b"})
    assert got.status_code == 200 and got.get_json()["rows"] == 2, got.get_data(as_text=True)[:200]


# ---------- 8A3 Binary ----------

def test_text_wide_hex_encodes_binary():
    df = pl.DataFrame({"id": [1, 2], "raw": [b"\x00\xff", b"x"]})
    assert server.to_json(df)["rows"] == [{"id": 1, "raw": "00ff"}, {"id": 2, "raw": "78"}]
    with server.app.test_request_context():
        body = json.loads(server.json_reply(df, n=1).get_data(as_text=True))
    assert body["rows"]["rows"] == [{"id": 1, "raw": "00ff"}, {"id": 2, "raw": "78"}]


def test_blob_column_queries_return_200(tmp_path):
    from query.search_app import app
    path = tmp_path / "t.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("create table b as select i as id, ('x' || i)::blob as raw from range(5) t(i)")
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    r = c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    for api, body in [("/api/query", {"base": "b", "limit": 10, "offset": 0, "fresh": True}),
                      ("/api/sql/run", {"sql": "select * from b", "limit": 10, "offset": 0})]:
        r = c.post(api, json=body)
        assert r.status_code == 200, (api, r.get_data(as_text=True)[:200])
        assert "78" in r.get_data(as_text=True), api  # 'x0'의 16진수는 7830


# 통합 때 찾음: 7P10의 streaming이 조인·이어붙이기 뒤 행 순서를 지키지 않아 쪽마다 행이 겹치고 빠졌다(좌표 단계 미리보기도 0번 행부터가 아님)
def test_pages_after_join_or_union_on_file_connection_do_not_overlap(tmp_path):
    from core import result
    n = 5000
    pl.DataFrame({"i": list(range(n)), "k": [x % 7 for x in range(n)]}).write_csv(tmp_path / "pts.csv")
    pl.DataFrame({"k": list(range(7)), "nm": [f"n{x}" for x in range(7)]}).write_csv(tmp_path / "dim.csv")
    con = source.open_flat_files([tmp_path / "pts.csv", tmp_path / "dim.csv"])
    t = con.table("pts")
    for e, total in ((t.left_join(con.table("dim"), "k"), n), (t.union(t.mutate(i=t.i + n)), 2 * n), (t.order_by("k"), n)):
        got = [x for o in range(0, total, 500) for x in result.get_rows(e, 500, o)["i"].to_list()]
        assert sorted(got) == list(range(total))
    assert result.get_rows(t.left_join(con.table("dim"), "k"), 5)["i"].to_list() == [0, 1, 2, 3, 4]
    source.close(con)
