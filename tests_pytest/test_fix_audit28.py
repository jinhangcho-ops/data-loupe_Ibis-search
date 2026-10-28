"""28차 감사: 시각 키 건너뛰기, label의 NaN, 대소문자만 다른 새 컬럼, 업로드 기록 이름, 긴 테이블 이름, 최신화 [멈추기], 주소 속 비밀번호의 +."""
import datetime as dt
import threading
from urllib.parse import urlsplit

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from lazy import ibis

from collect import load_job
from core import parse, prep_derive, prep_ops, server, source
from dashboard import dash_refresh


def cfg(db, how="append", key=("id",), name="items"):
    return {"load": {"target": "table", "batch": 2, "unit": "batch", "retries": 0, "interval": 0,
                     "table": {"schema": "", "name": name, "key": list(key), "how": how, "load_id": False,
                               "connection": {"kind": "folder", "file": db, "url": "", "user": ""}},
                     "kafka": {"destination": "", "key": ""}}}


def test_append_skips_existing_timestamp_keys_across_time_zones(tmp_path):
    db = str(tmp_path / "w.duckdb")
    with duckdb.connect(db) as c:
        c.execute("create table items (ts timestamp, v varchar)")
        c.execute("insert into items values ('2026-01-01 00:00:00', 'old')")
    out = tmp_path / "r.parquet"
    ts = pa.array([dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc)], pa.timestamp("us", tz="UTC"))
    pq.write_table(pa.table({"ts": ts, "v": ["a", "b"]}), out)
    runs = [load_job.run(cfg(db, key=("ts",)), out, "", threading.Event(), lambda **k: None) for _ in range(2)]
    assert [(r["inserted"], r["skipped"]) for r in runs] == [(1, 1), (0, 2)]
    with duckdb.connect(db) as c:
        assert c.execute("select count(*) from items").fetchone()[0] == 2


def test_label_treats_nan_as_empty():
    con = ibis.duckdb.connect()
    con.raw_sql("create table t as select * from (values (3.0), ('nan'::double), (1.0), (2.0), (5.0), (4.0)) v(x)")
    r = prep_ops.apply_ops(con.table("t"), "x", {"label": True}).order_by("x").to_pyarrow().to_pydict()
    assert dict(zip(r["x"], r["x_label"])) == {1.0: 0, 2.0: 1, 3.0: 2, 4.0: 3, 5.0: 4, None: None}


def test_new_column_differing_only_in_case_is_refused():
    t = ibis.memtable({"amount": [1], "city": ["Seoul"]})
    with pytest.raises(ValueError, match="이미 있"):
        prep_derive.check_name("Amount", t, "계산 컬럼")
    with pytest.raises(ValueError, match="이미 있"):
        prep_ops.check_new(t, ["x_Seoul", "x_seoul"])


def test_long_table_name_refused_only_when_created():
    parse.check_table_name("가" * 30)  # 이미 있는 긴 이름 테이블에 덧붙이기는 된다(28차 리뷰1)
    parse.check_new_length("가" * 17, "_ibis_load")
    with pytest.raises(ValueError, match="너무 길어요"):
        parse.check_new_length("가" * 18, "_ibis_load")
    with pytest.raises(ValueError, match="너무 길어요"):
        parse.check_new_length("가" * 22)


def test_refresh_stop_does_not_cancel_user_query_while_waiting(monkeypatch):
    event = threading.Event()
    monkeypatch.setitem(server.state, "cancel", event)
    monkeypatch.setitem(dash_refresh.JOB, "running", True)
    dash_refresh.stop()
    assert not event.is_set()  # 최신화는 차례를 기다리는 중: 도는 것은 사용자 조회
    with dash_refresh.inside():
        dash_refresh.stop()
    assert event.is_set()
    dash_refresh.STOP.clear()


def test_password_with_plus_in_url_reaches_driver_intact(monkeypatch):
    seen = []
    monkeypatch.setattr(source.drivers, "prepare", lambda url, password="": {})
    monkeypatch.setattr(source.ibis, "connect", lambda url, **kw: seen.append(url) or (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(Exception):
        source.open_url("postgres://me:a+b@db.example/x")
    from urllib.parse import unquote_plus
    assert unquote_plus(urlsplit(seen[0]).password) == "a+b"  # Ibis는 unquote_plus로 읽는다
