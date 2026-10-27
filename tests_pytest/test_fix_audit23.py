"""23차 감사: SQL 탭이 소수 0자리 DECIMAL(ID)을 실수로 바꿔 끝자리가 바뀌던 것, 날짜 컬럼에 시·분·초 뽑기가 영어 오류를 내던 것."""
import duckdb
import pytest
from lazy import ibis

from core import calc, server


def test_sql_tab_keeps_big_decimal_ids_exact(tmp_path):
    from query.search_app import app
    path = tmp_path / "d.duckdb"
    with duckdb.connect(str(path)) as raw:
        raw.execute("create table t as select 9007199254740993::DECIMAL(19,0) as id, 12.34::DECIMAL(10,2) as amt")
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(path)}).status_code == 200
    sql = c.post("/api/sql/run", json={"sql": "select id, amt from t", "limit": 10, "offset": 0}).get_json()["rows"]["rows"][0]
    search = c.post("/api/query", json={"base": "t", "limit": 10, "offset": 0, "fresh": True}).get_json()["rows"]["rows"][0]
    assert str(sql["id"]) == "9007199254740993" == str(search["id"])  # 탐색 탭과 같은 정확한 값
    assert sql["amt"] == 12.34  # 소수 자리 있는 값은 지금처럼 숫자


@pytest.mark.parametrize("fn", ["hour", "minute", "second"])
def test_time_parts_on_date_column_explain_in_korean(fn):
    t = ibis.memtable({"d": ["2026-10-27"]}).mutate(d=ibis._.d.cast("date"))
    with pytest.raises(ValueError, match="시각이 있는 컬럼"):
        calc.apply([f"식 {fn}(d) 이름 r"], t)
    ts = ibis.memtable({"d": ["2026-10-27 13:45:07"]}).mutate(d=ibis._.d.cast("timestamp"))
    assert calc.apply([f"식 {fn}(d) 이름 r"], ts).r.execute().tolist() == [{"hour": 13, "minute": 45, "second": 7}[fn]]
