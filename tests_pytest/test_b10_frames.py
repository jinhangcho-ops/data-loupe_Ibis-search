"""B10 틀 요소(plans/dash-blocks.md "B10 세부 명세"): 요소 카드의 설정(view.frame) 검사와 요소 카드의 저장 꼴. 값은 지어낸 것만 쓴다."""
import pytest

from core import dash_store

BUTTON = {"label": "새로 고침", "icon": "", "action": "refresh", "arg": "", "ambient": False}


def test_check_frame_button_fills_defaults() -> None:
    assert dash_store.check_frame({"label": "새로 고침"}, "button") == {"label": "새로 고침", "icon": "", "action": "", "arg": "", "ambient": False}
    assert dash_store.check_frame({"icon": "clock", "action": "page", "arg": "둘째 쪽", "ambient": True}, "button") == {
        "label": "", "icon": "clock", "action": "page", "arg": "둘째 쪽", "ambient": True}
    assert dash_store.check_frame(BUTTON, "button") == BUTTON
    for action in ("easy", "filters_clear"):  # 받는 값이 없는 할 일
        assert dash_store.check_frame({"label": "가", "action": action}, "button")["action"] == action
        with pytest.raises(ValueError, match="arg"):
            dash_store.check_frame({"label": "가", "action": action, "arg": "x"}, "button")


def test_check_frame_button_needs_label_or_icon() -> None:
    for frame in (None, {}, {"label": "", "icon": ""}, {"label": "   "}, {"action": "refresh"}):
        with pytest.raises(ValueError, match="글자나 아이콘 중 하나"):
            dash_store.check_frame(frame, "button")


def test_check_frame_rejects_bad_values() -> None:
    bad = [({"label": "가", "icon": "rocket"}, "아이콘"), ({"label": "가", "action": "help"}, "할 일"),({"label": "가", "action": "page"}, "arg"),
           ({"label": "가", "action": "zoom", "arg": "카드 하나"}, "arg"), ({"label": "가", "action": "refresh", "arg": "x"}, "arg"),
           ({"label": "가", "ambient": "yes"}, "ambient"), ({"label": "가", "color": "#fff"}, "쓸 수 없는 항목"),
           ({"label": "가" * (dash_store.MAX_FRAME_LABEL + 1)}, "단추 글자"), ("단추", "객체")]
    for frame, why in bad:
        with pytest.raises(ValueError, match=why):
            dash_store.check_frame(frame, "button")
    with pytest.raises(ValueError, match="요소 종류"):
        dash_store.check_frame({}, "chart")


def test_check_frame_rails_and_plain_kinds() -> None:
    item = {"label": "첫 쪽", "icon": "", "action": "page", "arg": "첫 쪽"}
    assert dash_store.check_frame(None, "nav_rail") == {"items": [], "ambient": False}
    assert dash_store.check_frame({"items": [{"label": "첫 쪽", "action": "page", "arg": "첫 쪽"}], "ambient": True}, "nav_rail") == {"items": [item], "ambient": True}
    assert dash_store.check_frame(None, "top_strip") == {"parts": list(dash_store.FRAME_PARTS), "items": []}
    assert dash_store.check_frame({"parts": ["theme", "name"], "items": [item]}, "top_strip") == {"parts": ["theme", "name"], "items": [item]}
    for kind in ("period_pick", "asof_field", "unit_seg", "page_tabs"):
        assert dash_store.check_frame(None, kind) == {}
        with pytest.raises(ValueError, match="쓸 수 없는 항목"):
            dash_store.check_frame({"label": "가"}, kind)
    many = [item] * (dash_store.MAX_FRAME_ITEMS + 1)
    for frame, kind, why in [({"items": many}, "nav_rail", "항목"), ({"items": [{"label": ""}]}, "nav_rail", "글자나 아이콘 중 하나"),
                             ({"items": [{"label": "가", "ambient": True}]}, "nav_rail", "쓸 수 없는 항목"), ({"items": "첫 쪽"}, "nav_rail", "항목"),
                             ({"parts": ["name", "name"]}, "top_strip", "보일 칸"), ({"parts": ["logo"]}, "top_strip", "보일 칸"), ({"ambient": True}, "top_strip", "쓸 수 없는 항목")]:
        with pytest.raises(ValueError, match=why):
            dash_store.check_frame(frame, kind)


def test_cards_keep_two_cell_minimum() -> None:
    """기존 카드의 자리 규칙은 그대로다(저장된 대시보드가 그대로 열린다)."""
    assert dash_store.check_layout({"x": 0, "y": 0, "w": 2, "h": 2}) == {"x": 0, "y": 0, "w": 2, "h": 2}
    with pytest.raises(ValueError, match="layout"):
        dash_store.check_layout({"x": 0, "y": 0, "w": 1, "h": 2})
    with pytest.raises(ValueError, match="layout"):
        dash_store.check_card({"kind": "kpi", "title": "값", "layout": {"x": 0, "y": 0, "w": 2, "h": 1}})


def test_check_card_takes_frame_cards() -> None:
    card = dash_store.check_card({"id": "f1", "kind": "button", "title": "단추", "layout": {"x": 11, "y": 0, "w": 1, "h": 1}, "view": {"frame": BUTTON, "page": "첫 쪽"}})
    assert card["kind"] == "button" and card["layout"] == {"x": 11, "y": 0, "w": 1, "h": 1}
    assert card["view"]["frame"] == BUTTON and card["view"]["page"] == "첫 쪽"
    assert dash_store.check_card({"kind": "unit_seg", "title": "기간 단위"})["view"]["frame"] == {}
    with pytest.raises(ValueError, match="글자나 아이콘 중 하나"):
        dash_store.check_card({"kind": "button", "title": "단추"})
    with pytest.raises(ValueError, match="틀 요소"):
        dash_store.check_card({"kind": "kpi", "title": "값", "view": {"frame": BUTTON}})
    with pytest.raises(ValueError, match="layout"):
        dash_store.check_card({"kind": "button", "title": "단추", "layout": {"x": 0, "y": 0, "w": 0, "h": 1}, "view": {"frame": BUTTON}})
    assert "frame" not in dash_store.check_card({"kind": "kpi", "title": "값"})["view"]
