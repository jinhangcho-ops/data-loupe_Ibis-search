"""감사 30차: 복사한 광고 지표의 맞추는 구절, 다른 스키마 테이블의 처리 용량, 카드 설정 패널을 접고 펼 때 초점."""
from lazy import ibis
from core import scan_size
from metrics import my_metrics as mm
from test_fix_audit_js import between

S1 = {"key": "k1", "label": "L", "schema": "", "table": "events"}
SLOTS = {"사용자": "uid", "시각": "ts", "광고 테이블": "ads", "광고비": "spend", "광고 날짜": "d"}


def test_copied_ad_metric_keeps_align(folder):
    cols = {"uid": "text", "ts": "date", "user_id": "text"}
    made = mm.create({"name": "CAC", "scope": S1, "template": "catalog:cac", "slots": SLOTS, "align": {"kind": "period", "unit": "day"}}, cols)
    copy = mm.copy_to(made["id"], {**S1, "table": "events2"}, {}, cols)["metrics"][0]
    assert copy["align"] == {"kind": "period", "unit": "day"}  # 예전엔 None이라 복사본은 늘 '맞추는 방법을 골라야 해요'
    keyed = mm.create({"name": "CAC2", "scope": S1, "template": "catalog:cac", "slots": SLOTS, "align": {"kind": "key", "keys": [["uid", "uid"]]}}, cols)
    moved = mm.copy_to(keyed["id"], {**S1, "table": "events3"}, {"uid": "user_id"}, cols)["metrics"][0]
    assert moved["align"]["keys"] == [["user_id", "uid"]]  # 이 테이블 쪽 키만 짝으로


def test_schema_table_is_counted():
    con = ibis.duckdb.connect()
    con.raw_sql("create schema s; create table s.big as select range a from range(100000)")
    sql = str(ibis.to_sql(con.table("big", database="s")))
    found = scan_size.limit_check(con, sql, config={"memory": 0.1, "scan_unknown": "block"})
    assert found["bytes"] and found["over"]  # 예전엔 0 B(읽는 테이블 없음)라 묻지 않았다


def test_panel_fold_moves_focus_to_the_other_button():
    js = between("dash_panel.js", "    const head = ", "  // 데이터:")
    assert 'this.fold(true); this.root.querySelector(":scope > .side-strip")?.focus();' in js
    assert 'this.fold(false); this.root.querySelector(".fold")?.focus();' in js


def test_copy_asks_for_missing_align_key_column(folder):
    import pytest
    cols = {"uid": "text", "ts": "date", "user_id": "text"}
    made = mm.create({"name": "CAC3", "scope": S1, "template": "catalog:cac", "slots": SLOTS, "align": {"kind": "key", "keys": [["user_id", "uid"]]}}, cols)
    with pytest.raises(ValueError, match="user_id"):  # 예전엔 복사가 끝나고 구할 때마다 없는 컬럼 오류
        mm.copy_to(made["id"], {**S1, "table": "events4"}, {}, {"uid": "text", "ts": "date"})


def test_schema_tables_named_once_and_attached_db(tmp_path):
    con = ibis.duckdb.connect()
    con.raw_sql("create schema s; create table s.big as select range a from range(10)")
    one = scan_size.estimate(con, "select a from s.big")["bytes"]
    assert scan_size.estimate(con, "select * from s.big x join s.big y using (a)")["bytes"] == one  # 두 번 세지 않는다
    assert "s.big" in scan_size.limit_check(con, "select a from s.big", config={"memory": 1000})["text"]
    other = str(tmp_path / "o.duckdb")
    con.raw_sql(f"attach '{other}' as o; create schema o.s; create table o.s.big as select range a from range(20)")
    assert scan_size.estimate(con, "select a from o.s.big")["bytes"] == 2 * one  # 붙인 DB의 같은 이름 스키마·테이블
