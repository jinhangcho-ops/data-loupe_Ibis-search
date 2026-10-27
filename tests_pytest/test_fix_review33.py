"""33차 리뷰: 반올림·곱하기·빈값채우기의 대상(둘째 낱말)이 '이름'·'그룹'·'기준' 컬럼일 때."""
import pytest
from lazy import ibis

from core import calc


@pytest.mark.parametrize("line, col, want", [
    ("반올림 0 이름", "이름", [1.0, 3.0]),
    ("곱하기 2 그룹", "그룹", [2.0, None]),
    ("빈값채우기 0 그룹", "그룹", [1.0, 0.0]),
    ("공백지우기 기준", "기준", ["a", "b"]),
])
def test_each_target_named_like_option_word(line, col, want):
    t = ibis.memtable({"일자": [1, 2], "이름": [1.26, 2.5], "그룹": [1.0, None], "기준": [" a ", "b"]})
    assert calc.apply([line], t).order_by("일자").to_pyarrow().column(col).to_pylist() == want
