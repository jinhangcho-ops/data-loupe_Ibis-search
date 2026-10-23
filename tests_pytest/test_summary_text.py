"""요약 문장: 변화 → 기여 분해 → 목표 대비. 분해 규칙(부호·최대 그룹·몫 범위), 숫자가 없으면 문장을 빼는 것."""
import pytest

from dashboard import summary_text
from dashboard.summary_text import summarize


def test_full_sentence_with_decomposition_and_target():
    got = summarize("매출", 1500, 1000, "currency", "span", "직전 7일", groups={"강남점": (800, 500), "홍대점": (700, 500)}, target=2000)
    assert got["sentences"] == ["매출은 직전 7일보다 50% 늘었어요 (+₩500).", "늘어난 몫의 60%가 강남점이에요.", "목표 ₩2,000 대비 75%예요."]
    assert got["text"] == " ".join(got["sentences"])


def test_share_is_the_largest_same_sign_group_over_total_change():
    # 전체 +100: A +61, B +39, C -10 → 몫은 A의 61/100
    groups = {"A": (161, 100), "B": (139, 100), "C": (90, 100)}
    assert summary_text.share_sentence(1000, 900, groups, "number") == "늘어난 몫의 61%가 A예요."
    # 줄어든 경우: 전체 -100, 부호가 같은 그룹 중 가장 큰 쪽
    down = {"강남점": (30, 80), "서울": (140, 150), "대구": (130, 100)}
    assert summary_text.share_sentence(900, 1000, down, "number") == "줄어든 몫의 50%가 강남점이에요."
    assert summary_text.share_sentence(900, 1000, {"대구": (10, 100)}, "number") == "줄어든 몫의 90%가 대구예요."


def test_share_only_when_between_zero_and_one():
    # 한 그룹이 전체 변화보다 크게 늘면(다른 그룹이 줄어 상쇄) 몫이 1을 넘으므로 말하지 않는다
    assert summary_text.share_sentence(1100, 1000, {"A": (300, 100), "B": (0, 100)}, "number") is None
    # 전체 변화와 부호가 같은 그룹이 없다
    assert summary_text.share_sentence(1100, 1000, {"A": (90, 100)}, "number") is None
    # 몫이 정확히 1이면 말한다
    assert summary_text.share_sentence(1100, 1000, {"A": (200, 100), "B": (50, 50)}, "number") == "늘어난 몫의 100%가 A예요."
    # 반올림하면 0%인 몫은 말하지 않는다
    assert summary_text.share_sentence(2000, 1000, {"A": (1001, 1000)}, "number") is None


def test_share_skipped_without_change_or_numbers():
    assert summary_text.share_sentence(100, 100, {"A": (5, 1)}, "number") is None  # 변화 없음
    assert summary_text.share_sentence(100, None, {"A": (5, 1)}, "number") is None  # 직전 없음
    assert summary_text.share_sentence(None, 100, {"A": (5, 1)}, "number") is None
    assert summary_text.share_sentence(120, 100, None, "number") is None
    assert summary_text.share_sentence(120, 100, {}, "number") is None
    # 직전이 없는 그룹은 변화를 알 수 없어 건너뛴다(0으로 지어내지 않는다)
    assert summary_text.share_sentence(120, 100, {"새 지점": (20, None), "A": (110, 100)}, "number") == "늘어난 몫의 50%가 A예요."
    assert summary_text.share_sentence(120, 100, {"새 지점": (20, None)}, "number") is None
    assert summary_text.share_sentence(120, 100, {"A": "가", "B": (1,)}, "number") is None


def test_share_not_for_percent_format():
    assert summary_text.share_sentence(0.3, 0.2, {"A": (0.3, 0.2)}, "percent") is None
    got = summarize("전환율", 0.3, 0.2, "percent", groups={"A": (0.3, 0.2)})
    assert got["sentences"] == ["전환율은 직전 기간보다 10%p 늘었어요 (20% → 30%)."]


def test_group_entries_can_be_dicts_or_pairs():
    a = summary_text.share_sentence(150, 100, {"A": {"cur": 130, "prev": 100}}, "number")
    b = summary_text.share_sentence(150, 100, {"A": [130, 100]}, "number")
    assert a == b == "늘어난 몫의 60%가 A예요."


def test_target_sentence_variants():
    assert summary_text.target_sentence(750, 1000, "number") == "목표 1,000 대비 75%예요."
    assert summary_text.target_sentence(1120, 1000, "number") == "목표 1,000 대비 112%로 목표를 넘었어요."
    assert summary_text.target_sentence(1000, 1000, "number") == "목표 1,000 대비 100%로 목표를 채웠어요."
    assert summary_text.target_sentence(0.4, 0.5, "percent") == "목표 50% 대비 80%예요."
    assert summary_text.target_sentence(333, 1000, "number") == "목표 1,000 대비 33.3%예요."
    assert summary_text.target_sentence(1, 3, "number") == "목표 3 대비 33.3%예요."
    for target in (None, 0, -5, "가", float("nan")):
        assert summary_text.target_sentence(100, target, "number") is None
    assert summary_text.target_sentence(None, 1000, "number") is None


def test_missing_numbers_drop_sentences_instead_of_inventing():
    assert summarize("매출", None, 100) == {"text": "", "sentences": []}
    only_target = summarize("매출", 500, None, target=1000)
    assert only_target["sentences"][0] == "매출은 직전 기간에는 값이 없었어요 (+500)."
    assert only_target["sentences"][1:] == ["목표 1,000 대비 50%예요."]
    no_prev_groups = summarize("매출", 500, None, groups={"A": (500, 0)})
    assert len(no_prev_groups["sentences"]) == 1  # 직전이 없으면 분해하지 않는다
    assert summarize("매출", 500, 500)["sentences"] == ["매출은 직전 기간과 같아요."]


def test_name_particle_and_label():
    assert summarize("CTR", 2, 1, label="어제")["sentences"][0].startswith("CTR은 어제보다")  # R(알)은 받침이 있다
    assert summarize("KPI", 2, 1)["sentences"][0].startswith("KPI는 ")
    assert summarize("방문자", 2, 1)["sentences"][0].startswith("방문자는 ")
    assert summarize("매출", 2, 1)["sentences"][0].startswith("매출은 ")


def test_mode_and_partial_are_passed_through():
    got = summarize("매출", 120, 100, mode="point", label="7일 전", partial=True)
    assert got["sentences"][0] == "매출은 7일 전보다 20% 늘었어요 (+20) (비교 시점 값이 일부만 있음)."


def test_bad_values_do_not_crash():
    got = summarize("매출", "가", "나", groups={"A": "x"}, target="y")
    assert got == {"text": "", "sentences": []}
    with pytest.raises(ValueError):
        summarize("매출", 1, 2, fmt="hex")
