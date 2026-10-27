"""감사 31차: 좌표 단계 작은 표가 연결에 쌓이지 않기, SQL 화면의 load_aws_credentials, 글자 '2.9'를 정수로(문서를 설계에 맞춤)."""
import polars as pl

from lazy import ibis
from core import prep_ops
from geo import prep_geo


def test_geo_step_keeps_one_small_table_per_step():
    con = ibis.duckdb.connect()
    con.raw_sql("create table t as select range::double a, range::double b from range(5)")
    t = con.table("t")
    for k in range(4):
        frame = pl.DataFrame({"__k0": [0.0, 1.0], "__k1": [0.0, 1.0], "v": [k, k + 1]})
        prep_geo.attach(t, ["a", "b"], frame, ["v"]).execute()
    views = con.raw_sql("select count(*) from duckdb_views() where view_name like 'ibis_%memtable%'").fetchone()[0]
    assert views == 1  # 예전엔 돌릴 때마다 하나씩(4개)


def test_text_decimal_to_int_is_empty_as_documented():
    t = ibis.memtable({"s": ["2.9", "7", "9223372036854775807"]})
    assert prep_ops.to_number(t.s, "int").name("n").to_pyarrow().to_pylist() == [None, 7, 9223372036854775807]  # 소수 모양 글자는 빈 값(tests.py 설계, 문서 prep.md)
    assert prep_ops.to_number(prep_ops.to_number(t.s, "float"), "int").name("n").to_pyarrow().to_pylist()[0] == 2  # 실수로 먼저 바꾸면 0 쪽으로 버림
