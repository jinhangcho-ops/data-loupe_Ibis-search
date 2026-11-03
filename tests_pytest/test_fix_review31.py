"""31차 리뷰: 기준일만 정한 대시보드에서 다시 정한 카드 값은 저장한다(기간을 건 값만 화면에만), 거꾸로 누적은 받은 값이 모자라면 막는다, 복사의 긴 이름."""
import pytest

from metrics import my_metrics
from query import sentence_exec


def test_reverse_cumsum_refuses_partial_values():
    a = {"keys": ["day"], "col": "v", "more": True, "rows": [{"day": 1, "v": 1}]}
    with pytest.raises(ValueError, match="거꾸로"):
        sentence_exec.combine({"op": "cumsum", "a": "a", "reverse": True}, {"a": a})


def test_rename_keeps_longer_name_and_unquoted_apostrophe():
    whole = {"order date": "order date", "date": "dt"}
    assert my_metrics.rename_in("order date >= '2024-01-01' and date < '2024-02-01'", whole) == "order date >= '2024-01-01' and dt < '2024-02-01'"
    assert my_metrics.rename_in("nick = it's or status = 'paid'", {"status": "st"}) == "nick = it's or st = 'paid'"
