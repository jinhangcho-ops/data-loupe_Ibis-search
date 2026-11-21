"""대시보드 5단계(쪽·값/비율): 쪽(탭) 이름 목록(pages)과 카드가 놓인 쪽(view.page)의 저장 규칙. 값/비율 바꾸기는 화면에서만 하고 저장하지 않는다."""
import pytest

from core import dash_store

pytestmark = pytest.mark.func


def test_pages_are_kept_in_order_and_trimmed() -> None:
    assert dash_store.check_pages([" 현황 ", "조치"]) == ["현황", "조치"]


def test_missing_pages_is_empty_list() -> None:
    assert dash_store.check_pages(None) == []
    assert dash_store.check_dashboard({"name": "가"})["pages"] == []


def test_same_page_name_twice_is_refused() -> None:
    with pytest.raises(ValueError, match="같은 이름의 쪽"):
        dash_store.check_pages(["현황", "현황 "])


def test_too_many_or_bad_pages_are_refused() -> None:
    with pytest.raises(ValueError, match=f"{dash_store.MAX_PAGES}개까지"):
        dash_store.check_pages([str(i) for i in range(dash_store.MAX_PAGES + 1)])
    with pytest.raises(ValueError, match="쪽 이름"):
        dash_store.check_pages([""])
    with pytest.raises(ValueError, match="쪽 이름"):
        dash_store.check_pages(["가" * (dash_store.MAX_PAGE_NAME + 1)])


def test_card_page_is_kept_only_when_set() -> None:
    assert dash_store.check_view({"page": "조치"})["page"] == "조치"
    assert "page" not in dash_store.check_view({})
    assert "page" not in dash_store.check_view({"page": ""})
