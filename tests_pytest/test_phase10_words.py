"""Phase 10 서버(plans/phase-10.md): 낱말 줄의 백틱 인용(결정 4: calc.tokens·group_fields·split_words·group_line)과
찾기 ③ 최근 값·조인 겹친 이름 대상_컬럼(결정 10: group_line 기준, parse.rname_for). 데이터는 시험 안에서 지어낸다."""
import datetime as dt

import pytest

from lazy import ibis
from core import calc, parse

D = dt.date


def run(lines, t):
    return ibis.duckdb.connect().to_polars(calc.apply(lines, t))


def test_tokens_quotes_spans_and_errors():
    line = "lag `고객 이름` 1 기준 `기준  일자` desc 키 a=`b c`"
    got = list(calc.tokens(line))
    assert [(w, q) for w, q, _ in got] == [("lag", False), ("고객 이름", True), ("1", False), ("기준", False), ("기준  일자", True), ("desc", False),
                                           ("키", False), ("a=", False), ("b c", True)]
    assert all(line[s:e] == (f"`{w}`" if q else w) for w, q, (s, e) in got)  # 자리는 원래 줄(감싼 낱말은 백틱까지)
    for bad, word in (("순위 기준 `고객 이름", "닫히지"), ("순위 기준 ``", "비었"), ("순위 기준 `a,b`", "쉼표")):
        with pytest.raises(ValueError, match=word):
            list(calc.tokens(bad))


def test_tokens_is_lazy_after_bad_backtick():
    """생성기라 앞 낱말은 뒤의 짝 없는 백틱과 상관없이 나온다(그룹집계가 조건 뒤 글을 훑지 않게)."""
    found = calc.tokens("그룹 a 조건 메모 like '`'")
    assert next(found)[0] == "그룹" and next(found)[0] == "a" and next(found)[0] == "조건"


def test_group_fields_markers_outside_backticks():
    assert calc.group_fields(" 테이블 주문 키 `고객 ID`=customer_id 기준 `기준 일자` desc 조건 상태 = '조건  미달' 이름 n") == {
        "table": "주문", "key": "고객 ID=customer_id", "group": "", "order": "기준 일자 desc", "cond": "상태 = '조건  미달'", "name": "n"}
    assert calc.group_fields(" 그룹 `판매 조건`, g2 기준 이름 desc 이름 합계") == {
        "table": "", "key": "", "group": "판매 조건, g2", "order": "이름 desc", "cond": "", "name": "합계"}  # 칸 낱말 바로 뒤의 칸 낱말은 값
    assert calc.group_fields(" 조건 이름 = 'a' 이름 n")["cond"] == "이름 = 'a'" and calc.group_fields(" 조건 이름 = 'a' 이름 n")["name"] == "n"
    assert calc.group_fields(" 그룹 조건 코드 이름 n")["group"] == "조건 코드"
    assert calc.group_fields("") == {"table": "", "key": "", "group": "", "order": "", "cond": "", "name": ""}
    assert calc.group_fields(" 조건 메모 like '`'")["cond"] == "메모 like '`'"  # 조건 뒤는 훑지 않는다


def test_group_fields_rejects_bad_shapes():
    for bad in (" 기준 a 그룹 b", " x 그룹 a", " 그룹 a 이름 1등", " 그룹", " 그룹 a 그룹 b", " 테이블 a b 키 k", " 이름 n 그룹 g", " 그룹 a 조건 "):
        with pytest.raises(ValueError, match="그룹집계 <집계id>"):
            calc.group_fields(bad)


def test_word_lines_with_backtick_columns_run():
    t = ibis.memtable({"판매 금액": [1.0, 2.0, 4.0], "기준 일자": [D(2024, 1, 1), D(2024, 1, 2), D(2024, 1, 3)], "그룹": ["a", "a", "b"], "이름": ["x", "y", "z"]})
    assert calc.split_words("lag `판매 금액` 1 기준 `기준 일자` 그룹 `그룹` 이름 직전", t.columns) == (
        "lag", ["판매 금액", "1"], {"기준": ["기준 일자"], "그룹": ["그룹"], "이름": ["직전"]})
    out = run(["누적합 `판매 금액` 기준 `기준 일자` 그룹 `그룹` 이름 누적", "순위 기준 `이름` desc 이름 등수", "곱하기 2 `판매 금액`"], t).sort("기준 일자")
    assert out["누적"].to_list() == [1.0, 3.0, 4.0] and out["등수"].to_list() == [3, 2, 1] and out["판매 금액"].to_list() == [2.0, 4.0, 8.0]


def test_formula_body_keeps_backtick_text():
    """식 줄 본문은 원래 줄에서 잘라 넘긴다: 글자 상수 안의 짝 맞는 백틱·'이름' 낱말이 그대로 남는다(고치기 전·후 모두)."""
    out = run(["식 concat(s, '`이름`') 이름 y"], ibis.memtable({"s": ["a", "b"]})).sort("s")
    assert out["y"].to_list() == ["a`이름`", "b`이름`"]


def test_group_line_quoted_group_and_order():
    t = ibis.memtable({"판매 조건": ["a", "a", "b"], "기준 일자": [D(2024, 1, 1), D(2024, 1, 2), D(2024, 1, 3)], "v": [1.0, 2.0, 5.0]})
    out = run(["그룹집계 last(v) 그룹 `판매 조건` 기준 `기준 일자` 이름 최근"], t).sort("v")
    assert out["최근"].to_list() == [2.0, 2.0, 5.0]


def test_group_line_old_forms_still_work():
    t = ibis.memtable({"이름": ["b", "a", "c"], "g": ["x", "x", "y"], "v": [1.0, 2.0, 3.0]})
    assert run(["그룹집계 first(v) 그룹 g 기준 이름 desc 이름 첫"], t).sort("v")["첫"].to_list() == [1.0, 1.0, 3.0]
    assert run(["그룹집계 sum(v) 조건 이름 = 'a' 이름 s"], t)["s"].to_list() == [2.0] * 3


def test_lookup_recent_value_with_quoted_keys(monkeypatch):
    """찾기 ③ '최근 값': 대상 테이블을 키로 묶어 기준 시각의 마지막 값(last)을 붙인다. 빈칸 든 키·기준 컬럼은 백틱."""
    from core import querying
    o = ibis.memtable({"고객 ID": [1, 2, 3]})
    r = ibis.memtable({"cid": [1, 1, 2], "주문 일자": [D(2024, 1, 1), D(2024, 3, 1), D(2024, 2, 1)], "v": [10, 30, 20]})
    monkeypatch.setattr(querying, "table_of", lambda name, schema=None: r)
    out = run(["그룹집계 last(v) 테이블 주문 키 `고객 ID`=cid 기준 `주문 일자` 이름 최근"], o).sort("고객 ID")
    assert out["최근"].to_list() == [30, 20, None]


def test_join_clash_names_are_table_prefixed():
    o = ibis.memtable({"cid": [1], "name": ["o"]})
    c = ibis.memtable({"cid": [1], "name": ["kim"]})
    tables = {"customers": c, "@탭2": c}
    a = parse.apply_joins(o, [("customers", "left", [("cid", "cid")], None)], tables.__getitem__)
    b = parse.apply_joins(o, [("@탭2", "left", [("cid", "cid")], None)], tables.__getitem__)
    assert a.columns == ("cid", "name", "customers_cid", "customers_name") and b.columns == ("cid", "name", "탭2_cid", "탭2_name")
    assert parse.rname_for(["k", "r_k"], ["k"], "r") == "r_{name}_"
