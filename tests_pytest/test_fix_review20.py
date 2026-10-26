"""20차 리뷰: 설치 파일을 받는 동안의 설치 막기, 넓은·지수 DECIMAL 조건, 큰 DECIMAL 값으로 펼친 피벗."""
import threading
import time

import duckdb
import ibis
import pytest

from collect import collect_job
from core import calc
from core import features
from core import parse
from test_fix_review11_a import Spy, installed  # noqa: F401


def test_install_blocks_while_downloading(installed, monkeypatch):
    """[지금 업데이트]가 설치 파일을 받는 동안 [적용]·새 수집은 거절된다(설치 프로그램을 띄우기 전에도)."""
    spy = Spy(monkeypatch)
    real, inside, go = features.fetch, threading.Event(), threading.Event()

    def slow(url, out):
        inside.set()
        go.wait(5)
        real(url, out)
    monkeypatch.setattr(features, "fetch", slow)
    first = threading.Thread(target=features.install, args=(["query", "collect"],))
    first.start()
    assert inside.wait(5)
    try:
        with pytest.raises(ValueError, match="설치 프로그램이 진행 중"):
            features.install(["query", "collect"])
        with pytest.raises(ValueError, match="설치 프로그램이 진행 중"):
            collect_job.start({})
    finally:
        go.set()
        first.join(5)
    assert len(spy.run) == 1
    spy.clean()


def test_install_lock_released_on_refusal(installed, monkeypatch):
    """확인에서 거절해도(잘못된 버전) 잠금이 풀려 다음 시도가 된다."""
    spy = Spy(monkeypatch)
    with pytest.raises(ValueError, match="버전"):
        features.install(["query"], "../x")
    assert not features.installing()
    features.install(["query", "collect"])
    assert len(spy.run) == 1
    spy.clean()


@pytest.mark.parametrize("dtype, text", [("decimal(76,38)", "5"), ("decimal(76,0)", "0.5"), ("decimal(38,0)", "1000")])
def test_decimal_condition_compiles_for_wide_and_sqlserver(dtype, text):
    """BIGNUMERIC(76,38)·Decimal256도 조건을 만들고, SQL Server에는 지수 글자('1E+3')를 쓰지 않는다."""
    t = ibis.table({"a": dtype}, name="x")
    for dialect in ("mssql", "bigquery", "postgres", "duckdb"):
        sql = ibis.to_sql(t.filter(t.a == parse.convert(t.a.type(), text)), dialect=dialect)
        assert "E+" not in sql.upper().split("WHERE")[1]


def test_decimal_condition_exact_and_too_long():
    raw = duckdb.connect()
    raw.execute("create table t as select * from (values (12345678901234567891::decimal(38,0)), (12345678901234567890::decimal(38,0)), (1000::decimal(38,0))) v(id)")
    t = ibis.duckdb.from_connection(raw).table("t")
    assert t.filter(t.id == parse.convert(t.id.type(), "1E+3")).count().execute() == 1
    with pytest.raises(ValueError, match="'1e40'은"):
        parse.convert(t.id.type(), "1e40")


def test_pivot_spread_on_big_decimal():
    """큰 DECIMAL ID로 펼쳐도 넘치지 않고 이웃 ID와 섞이지 않는다."""
    raw = duckdb.connect()
    raw.execute("create table t as select * from (values ('a', 12345678901234567891::decimal(38,0)), ('a', 12345678901234567890::decimal(38,0)), ('b', 12345678901234567890::decimal(38,0))) v(g, id)")
    t = ibis.duckdb.from_connection(raw).table("t")
    out = calc.pivot(t, {"rows": ["g"], "column": "id", "how": "count"}, lambda e: e.to_pyarrow().column("v").to_pylist())
    got = out.order_by("g").to_pyarrow().to_pylist()
    assert [(r["g"], r["12345678901234567890"], r["12345678901234567891"]) for r in got] == [("a", 1, 1), ("b", 1, 0)]


def test_sentence_text_uses_agg_label_and_particles():
    """문장 글은 집계 id(sum) 대신 이름(합계)을 쓰고, 빈칸 뒤 조사를 값의 받침에 맞춘다."""
    from query import sentence_run
    assert sentence_run.render("e13", {"무엇": "매장", "값": "금액", "집계": {"agg": "sum"}, "N": 5}) == "매장별 금액의 합계 상위 5개를 봐요"
    assert sentence_run.render("e14", {"무엇": "매장", "대상": "고객ID", "정확도": "정확"}) == "매장별 고유 고객ID 수를 정확으로 구해요"
    assert sentence_run.render("e03", {"컬럼": "상태", "값": "취소", "컬럼들": "주문번호"}) == "상태가 취소인 행의 주문번호를 봐요"
    assert sentence_run.render("e45", {"값 A": "금액", "값 B": "수량", "종류": "피어슨"}).startswith("금액과 수량이 ")
    assert sentence_run.render("e13", {}) == "{무엇}별 {값}의 {집계} 상위 {N}개를 봐요"
