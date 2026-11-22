"""B5 섹션(plans/dash-blocks.md "B5 세부 명세"): 조각 자리(check_spot), 섹션 하나(check_section), 섹션과 조각 맞추기·옛 카드 옮기기·조각 사이 1눈금(check_sections),
글 요소(check_frame text), 그리고 저장(check_card·check_dashboard·add_card 수정)이 섹션을 지키는지 본다. 값은 시험 안에서 지어낸다."""
import pytest

from core import dash_store as store

SUB_X, SUB_Y = store.SECTION_SUB


def lay(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def shell(card_id, layout, **more):
    """옛 모양 카드(섹션 없음, layout은 쪽 격자)를 check_card로 정리한 것."""
    return store.check_card({"id": card_id, "kind": "kpi", "title": f"카드 {card_id}", "layout": layout, **more})


def piece(card_id, section, layout):
    """섹션에 놓인 조각(check_sections가 받는 정리된 카드 모양)."""
    return {**shell(card_id, lay(0, 0, 2, 2)), "section": section, "layout": layout}


def section(section_id, layout, **more):
    return {"id": section_id, "layout": layout, **more}


def test_section_constants():
    assert store.SECTION_SUB == (16, 8) and store.SECTION_GAP == 1 and store.SECTION_FOLLOW is True and store.MAX_SECTIONS == store.MAX_CARDS


def test_check_spot_keeps_four_numbers_within_twelve_cells():
    assert store.check_spot({**lay(0, 0, 12 * SUB_X, 12 * SUB_Y), "z": 1}) == lay(0, 0, 12 * SUB_X, 12 * SUB_Y)
    assert store.check_spot(lay(5, 7, 1, 1)) == lay(5, 7, 1, 1)


@pytest.mark.parametrize("bad", [lay(0, 0, 0, 4), lay(-1, 0, 4, 4), lay(1, 0, 12 * SUB_X, 4), lay(0, 1, 4, 12 * SUB_Y),
                                 {"x": 0, "y": 0, "w": True, "h": 4}, {"x": 0, "y": 0, "w": 4}, "칸", None, {"x": 0, "y": 0, "w": 4.0, "h": 4}])
def test_check_spot_rejects(bad):
    with pytest.raises(ValueError, match="조각 자리"):
        store.check_spot(bad)


def test_check_section_defaults():
    assert store.check_section(section("s1", lay(0, 0, 6, 4))) == {"id": "s1", "title": "", "layout": lay(0, 0, 6, 4), "follow": True, "view": {}}


def test_check_section_keeps_title_follow_page_and_one_cell():
    found = store.check_section(section("s1", lay(1, 2, 1, 1), title=" 매출 ", follow=False, view={"page": "둘째"}))
    assert found == {"id": "s1", "title": "매출", "layout": lay(1, 2, 1, 1), "follow": False, "view": {"page": "둘째"}}
    assert store.check_section(section("s2", lay(0, 0, 2, 2), view={"page": ""}))["view"] == {}


@pytest.mark.parametrize("bad, words", [
    ("섹션", "섹션은 객체"),
    (section("s1", lay(0, 0, 2, 2), color="#fff"), "섹션에 쓸 수 없는 항목"),
    (section("s 1", lay(0, 0, 2, 2)), "섹션 id"),
    ({"layout": lay(0, 0, 2, 2)}, "섹션 id"),
    (section("s1", lay(0, 0, 13, 2)), "카드 자리"),
    (section("s1", None), "카드 자리"),
    (section("s1", lay(0, 0, 2, 2), follow="yes"), "follow"),
    (section("s1", lay(0, 0, 2, 2), title="가" * 101), "섹션 이름"),
    (section("s1", lay(0, 0, 2, 2), view={"page": "가" * 31}), "쪽 이름"),
    (section("s1", lay(0, 0, 2, 2), view={"page": "a", "type": "bar"}), "섹션 보기"),
    (section("s1", lay(0, 0, 2, 2), view=["a"]), "섹션 보기"),
])
def test_check_section_rejects(bad, words):
    with pytest.raises(ValueError, match=words):
        store.check_section(bad)


def test_check_sections_wraps_old_card_filling_its_section():
    card = shell("c1", lay(2, 3, 6, 5), view={"page": "둘째"})
    sections, cards = store.check_sections(None, [card])
    assert sections == [{"id": "c1", "title": "", "layout": lay(2, 3, 6, 5), "follow": True, "view": {"page": "둘째"}}]
    assert cards[0]["section"] == "c1" and cards[0]["layout"] == lay(0, 0, 6 * SUB_X, 5 * SUB_Y) and "page" not in cards[0]["view"]


def test_check_sections_wraps_old_cards_in_card_order_after_given_sections():
    sections, cards = store.check_sections([section("s1", lay(0, 0, 6, 4))], [shell("b", lay(6, 0, 6, 4)), piece("a", "s1", lay(0, 0, 16, 8)), shell("c", lay(0, 4, 12, 2))])
    assert [s["id"] for s in sections] == ["s1", "b", "c"]
    assert [c["section"] for c in cards] == ["b", "s1", "c"] and [c["id"] for c in cards] == ["b", "a", "c"]


def test_check_sections_keeps_pieces_one_gap_apart_and_empty_sections():
    sections, cards = store.check_sections([section("s1", lay(0, 0, 6, 4)), section("s2", lay(6, 0, 6, 4), follow=False)],
                                           [piece("a", "s1", lay(0, 0, 16, 8)), piece("b", "s1", lay(17, 0, 16, 8)), piece("c", "s1", lay(17, 9, 16, 8))])
    assert [s["id"] for s in sections] == ["s1", "s2"] and sections[1]["follow"] is False
    assert [c["layout"] for c in cards] == [lay(0, 0, 16, 8), lay(17, 0, 16, 8), lay(17, 9, 16, 8)]


@pytest.mark.parametrize("found, cards, words", [
    ([section("s1", lay(0, 0, 6, 4))], [piece("a", "s1", lay(90, 0, 7, 8))], "섹션 밖"),
    ([section("s1", lay(0, 0, 6, 4))], [piece("a", "s1", lay(0, 30, 8, 3))], "섹션 밖"),
    ([section("s1", lay(0, 0, 6, 4))], [piece("a", "s1", lay(0, 0, 16, 8)), piece("b", "s1", lay(15, 7, 16, 8))], "붙어"),
    ([section("s1", lay(0, 0, 6, 4))], [piece("a", "s1", lay(0, 0, 16, 8)), piece("b", "s1", lay(16, 0, 16, 8))], "붙어"),
    ([section("s1", lay(0, 0, 6, 4))], [piece("a", "s1", lay(0, 0, 16, 8)), piece("b", "s1", lay(16, 8, 16, 8))], "붙어"),
    ([section("s1", lay(0, 0, 6, 4))], [piece("a", "없음", lay(0, 0, 16, 8))], "놓인 섹션이 없어요"),
    ([section("s1", lay(0, 0, 6, 4)), section("s1", lay(6, 0, 6, 4))], [], "섹션 id가 겹쳐요"),
    ([section("c1", lay(0, 0, 6, 4))], [shell("c1", lay(6, 0, 6, 4))], "섹션 id가 겹쳐요"),
    ("섹션", [], "섹션 목록"),
    ([section(f"s{i}", lay(0, i, 2, 2)) for i in range(store.MAX_SECTIONS + 1)], [], "섹션 목록"),
])
def test_check_sections_rejects(found, cards, words):
    with pytest.raises(ValueError, match=words):
        store.check_sections(found, cards)


def test_check_sections_counts_wrapped_cards_in_the_limit():
    many = [section(f"s{i}", lay(0, i, 2, 2)) for i in range(store.MAX_SECTIONS)]
    with pytest.raises(ValueError, match="섹션 목록"):
        store.check_sections(many, [shell("c1", lay(0, 0, 2, 2))])


def test_text_frame_keeps_its_words_size_and_align():
    assert "text" in store.FRAME_KINDS and store.FRAME_KEYS["text"] == ("text", "size", "align")
    assert store.FRAME_TEXT_SIZES == ("sm", "md", "lg") and store.FRAME_TEXT_ALIGNS == ("left", "center", "right")
    assert store.FRAME_TEXT_DEFAULT == {"size": "md", "align": "left"}
    assert store.check_frame({"text": " 첫 줄\n둘째 줄 "}, "text") == {"text": "첫 줄\n둘째 줄", "size": "md", "align": "left"}
    assert store.check_frame(None, "text") == {"text": "", "size": "md", "align": "left"}
    assert store.check_frame({"text": "a", "size": "lg", "align": "center"}, "text") == {"text": "a", "size": "lg", "align": "center"}
    with pytest.raises(ValueError, match="글은"):
        store.check_frame({"text": "가" * (store.MAX_FRAME_TEXT + 1)}, "text")
    with pytest.raises(ValueError, match="글자 크기"):
        store.check_frame({"text": "a", "size": "xl"}, "text")
    with pytest.raises(ValueError, match="정렬"):
        store.check_frame({"text": "a", "align": "justify"}, "text")
    with pytest.raises(ValueError, match="쓸 수 없는 항목"):
        store.check_frame({"text": "a", "bold": True}, "text")
    found = store.check_card({"id": "t1", "kind": "text", "title": "글", "section": "s1", "layout": lay(0, 0, 8, 4), "view": {"frame": {"text": "안내", "align": "right"}}})
    assert found["view"]["frame"] == {"text": "안내", "size": "md", "align": "right"} and found["data"] == {}


def test_piece_layout_is_a_spot_and_page_belongs_to_the_section():
    found = store.check_card({"id": "a", "kind": "kpi", "title": "A", "section": "s1", "layout": lay(0, 0, 40, 20)})
    assert found["section"] == "s1" and found["layout"] == lay(0, 0, 40, 20)
    with pytest.raises(ValueError, match="조각의 쪽"):
        store.check_card({"id": "a", "kind": "kpi", "title": "A", "section": "s1", "layout": lay(0, 0, 8, 4), "view": {"page": "둘째"}})
    with pytest.raises(ValueError, match="조각의 섹션"):
        store.check_card({"id": "a", "kind": "kpi", "title": "A", "section": "s 1", "layout": lay(0, 0, 8, 4)})


def test_save_old_dashboard_gets_one_section_per_card_once(folder):
    saved = store.save_dashboard({"name": "옛 모양", "cards": [{"id": "c1", "kind": "kpi", "title": "A", "layout": lay(0, 0, 4, 3)}]})
    assert saved["sections"] == [{"id": "c1", "title": "", "layout": lay(0, 0, 4, 3), "follow": True, "view": {}}]
    assert saved["cards"][0]["section"] == "c1" and saved["cards"][0]["layout"] == lay(0, 0, 4 * SUB_X, 3 * SUB_Y)
    again = store.save_dashboard(saved)
    assert again["sections"] == saved["sections"] and again["cards"][0]["layout"] == saved["cards"][0]["layout"]  # 두 번 옮기지 않는다
    assert store.load()["dashboards"][saved["id"]]["sections"] == saved["sections"]


def test_save_rejects_touching_pieces(folder):
    with pytest.raises(ValueError, match="붙어"):
        store.save_dashboard({"name": "붙음", "sections": [section("s1", lay(0, 0, 6, 4))],
                              "cards": [{"id": "a", "kind": "kpi", "title": "A", "section": "s1", "layout": lay(0, 0, 16, 8)},
                                        {"id": "b", "kind": "kpi", "title": "B", "section": "s1", "layout": lay(16, 0, 16, 8)}]})


def test_add_card_goes_below_the_sections(folder):
    saved = store.save_dashboard({"name": "섹션", "sections": [section("s1", lay(0, 0, 12, 7))],
                                  "cards": [{"id": "a", "kind": "kpi", "title": "A", "section": "s1", "layout": lay(0, 0, 12 * SUB_X, 7 * SUB_Y)}]})
    out = store.add_card(saved["id"], "", {"id": "b", "kind": "kpi", "title": "B"})
    new = next(s for s in out["sections"] if s["id"] == "b")
    assert new["layout"] == lay(0, 7, 6, 6)
    assert next(c for c in out["cards"] if c["id"] == "b")["layout"] == lay(0, 0, 6 * SUB_X, 6 * SUB_Y)
