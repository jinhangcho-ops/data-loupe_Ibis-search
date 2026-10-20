"""중첩 구조(구조·목록) 컬럼: 한글 타입 이름, 구조 안의 필드(user.name)를 조건·정렬·표시 컬럼·값 목록에, CSV·Excel 저장은 JSON 글자로.
데이터는 테스트 안에서 지어낸다."""
import json

import polars as pl
import pytest
from lazy import ibis

import parse
import result

DF = pl.DataFrame({"id": [1, 2, 3],
                   "user": [{"name": "가람", "age": 30, "addr": {"city": "부산"}}, {"name": "나래", "age": None, "addr": {"city": "서울"}},
                            {"name": "다솜", "age": 25, "addr": {"city": "부산"}}],
                   "tags": [["a", "b"], [], ["c"]]})


@pytest.fixture(params=["memory", "polars"])
def t(request):
    return ibis.memtable(DF.to_arrow()) if request.param == "memory" else ibis.polars.connect().create_table("t", DF)


def test_type_names_and_fields(t):
    assert dict(result.columns(t)) == {"id": "정수", "user": "구조{name, age, addr}", "tags": "목록<문자>"}
    assert result.fields(t) == [("user.name", "문자"), ("user.age", "정수"), ("user.addr", "구조{city}"), ("user.addr.city", "문자")]


def test_conditions_sort_columns(t):
    expr = parse.apply(t, [parse.predicate("user.addr.city = 부산", t), parse.predicate("user.age >= 26 or user.name like 솜", t)],
                       [parse.parse_sort("user.name desc", t)], parse.parse_columns("id, user.name", t))
    assert expr.to_pyarrow().to_pylist() == [{"id": 3, "user.name": "다솜"}, {"id": 1, "user.name": "가람"}]
    assert parse.apply(t, [parse.predicate("user.age is null", t)], [], ["id"]).to_pyarrow().to_pylist() == [{"id": 2}]
    with pytest.raises(ValueError, match="비교할 수 없습니다"):
        parse.predicate("user = x", t)
    with pytest.raises(ValueError, match="user.name"):  # 비슷한 이름을 알려 준다
        parse.predicate("user.nmae = 가람", t)
    assert result.get_values(t, "user.addr.city") == (["부산", "서울"], False)


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
def test_export_nested_as_json(t, tmp_path, suffix):
    path = tmp_path / ("out" + suffix)
    result.export(t, path)
    back = pl.read_csv(path) if suffix == ".csv" else pl.read_excel(path)
    assert json.loads(back["user"][1]) == {"name": "나래", "age": None, "addr": {"city": "서울"}}
    assert [json.loads(v) for v in back["tags"]] == [["a", "b"], [], ["c"]]
