"""대시보드 4단계(카드 눌러 서로 거르기): 카드 보기 설정의 "누르면 거르기"(view.cross) 저장 규칙. 거르는 계산은 3단계의 길(test_dash_filters)을 그대로 쓴다."""
import pytest

from core import dash_store


def test_cross_true_is_kept() -> None:
    assert dash_store.check_view({"cross": True})["cross"] is True


def test_cross_false_or_missing_is_dropped() -> None:
    assert "cross" not in dash_store.check_view({"cross": False})
    assert "cross" not in dash_store.check_view({})


def test_cross_must_be_bool() -> None:
    with pytest.raises(ValueError, match="cross"):
        dash_store.check_view({"cross": "yes"})
