"""문장 틀 데이터(sentences.py): 탐색 1~50번(48~50은 좌표 틀)·지표 1~15번이 모두 있고, 글의 빈칸이 빈칸 정의와 맞고, 함수 사전의 함수와 이어진다."""
import json
import re

import pytest

from query import sentences as S
from core import sql_funcs

EXPLORE_NAMES = {  # 대화에서 정한 탐색 틀 1~25
    1: "모든 데이터 보기", 2: "고른 컬럼만 보기", 3: "특정 값인 행 보기", 4: "목록 중 하나에 해당하는 행", 5: "글자가 들어간 행 찾기", 6: "전체 행 수", 7: "조건에 맞는 대상 수",
    8: "항목별 개수", 9: "개수가 N 이상인 항목", 10: "조건에 맞는 행의 항목별 개수", 11: "두 번 이상 나오는 값", 12: "목록 값마다 행 수",
    13: "항목별 집계 상위 N개", 14: "항목별 고유 대상 수", 15: "단계별 고유 대상 수", 16: "다른 테이블과 가장 많이 연결된 행", 17: "조건에 맞는 비율",
    18: "항목별 개수와 비중", 19: "항목별 조건에 맞는 대상 비율", 20: "항목별 대상 1명당 평균", 21: "두 지표 나누기", 22: "일·주·월별 고유 대상 수",
    23: "일·주·월별 대상 수와 1명당 평균", 24: "대상별 첫 값으로 집계", 25: "목록을 펼쳐 항목별 집계 상위 N개"}
METRIC_NAMES = {1: "행 수", 2: "고유 대상 수", 3: "값 집계", 4: "첫 값이 특정 값인 대상 수", 5: "두 지표 나누기", 6: "한 지표가 다른 지표에서 차지하는 비율", 7: "두 지표 빼기", 8: "두 지표 더하기", 9: "대상 1명당 값 합계",
                10: "대상별로 구한 값의 평균", 11: "조건에 N번 이상 맞는 대상 비율", 12: "단계 전환율", 13: "일·주·월별 평균", 14: "직전 기간 대비 변화율", 15: "누적 값"}


def test_counts_and_numbers():
    explore, metric = [t for t in S.TEMPLATES if t["kind"] == "explore"], [t for t in S.TEMPLATES if t["kind"] == "metric"]
    assert [t["no"] for t in explore] == list(range(1, 51)) and [t["no"] for t in metric] == list(range(1, 16))
    assert len({t["id"] for t in S.TEMPLATES}) == len(S.TEMPLATES) == 65
    assert {t["no"]: t["name"] for t in explore if t["no"] <= 25} == EXPLORE_NAMES and {t["no"]: t["name"] for t in metric} == METRIC_NAMES
    assert S.get("e26")["name"] == "중앙값과 분위수" and S.get("m15")["name"] == "누적 값"
    with pytest.raises(ValueError, match="문장 틀이 없"):
        S.get("e99")


def test_every_template_may_be_prefixed_by_period():
    assert all(t["period"] for t in S.TEMPLATES) and S.PERIOD["type"] == "period" and S.PERIOD["optional"]


@pytest.mark.parametrize("t", S.TEMPLATES, ids=[t["id"] for t in S.TEMPLATES])
def test_template_slots_match_its_text(t):
    names = [s["name"] for s in t["slots"]]
    assert len(names) == len(set(names)), "빈칸 이름이 겹침"
    placeholders = re.findall(r"\{([^{}]+)\}", t["text"])
    assert set(placeholders) <= set(names), f"글에 있는데 빈칸이 없음: {set(placeholders) - set(names)}"
    for s in t["slots"]:
        assert s["type"] in S.SLOT_TYPES, s
        if s["name"] not in placeholders:  # 글에 안 나오는 빈칸은 다른 빈칸의 값에 따라 나타나는 곁가지
            assert s["when"] and set(s["when"]) <= set(names), (t["id"], s["name"])
        if s["type"] in ("choice", "unit") or s["type"] == "text_match":
            assert s["options"] and s["default"] in s["options"], (t["id"], s["name"])
    for use in t["uses"]:
        assert use in sql_funcs.FUNCS, (t["id"], use)


def test_variants_point_at_their_original():
    base = S.get("e46")
    assert base["extends"] == 14 and base["preset"] == {"정확도": "빠르게(근사)"} and "approx_count_distinct" in S.funcs_for("e46")
    assert S.get("e14")["slots"][2]["options"] == ["정확", "빠르게(근사)"] and "approx_count_distinct" in S.funcs_for("e14")


def test_aggregate_and_transform_slots_take_the_whole_catalog():
    aggs = S.funcs_for("e13")  # {집계} 칸
    assert set(sql_funcs.aggregate_ids()) <= set(aggs) and set(S.funcs_for("m03")) >= set(sql_funcs.aggregate_ids())
    assert set(S.row_ids()) <= set(S.funcs_for("e40"))  # {가공} 칸
    assert "upper" in S.funcs_for("e40") and "rank" not in S.funcs_for("e40") and "sum" not in S.funcs_for("e40")


def test_each_new_template_names_its_functions():
    expected = {26: {"median", "quantile", "p90", "p99"}, 27: {"std", "var", "range"}, 28: {"mode"}, 29: {"any_value", "first", "last", "min", "max", "mode", "string_agg", "unique_or_null"},
                30: {"string_agg"}, 31: {"collect"}, 32: {"all", "any"}, 33: {"arg_min", "arg_max", "first", "last"}, 34: {"rank", "qualify"}, 35: {"lag", "lead", "diff", "pct_change"},
                36: {"cum_sum", "moving_mean"}, 37: {"share_of_group"}, 38: {"percent_rank", "cume_dist", "ntile"}, 39: {"rollup", "cube", "grouping_sets"},
                41: {"case", "ifelse"}, 42: {"date_diff"}, 43: {"json_get"}, 44: {"cast", "try_cast"}, 45: {"corr", "cov", "cov_pop"}, 47: {sql_funcs.RAW}, 49: {"within_km"}, 50: {"grid_id", "grid_lat", "grid_lon"}}
    for no, ids in expected.items():
        assert ids <= set(S.funcs_for(f"e{no}")), no


def test_every_function_is_used_and_catalog_is_json():
    assert [i for i in sql_funcs.FUNCS if i not in {i for ids in S.uses_map().values() for i in ids}] == []  # 어느 틀에서도 안 쓰는 함수가 없다
    cat = S.catalog()
    json.dumps(cat)
    assert set(cat["uses"]) == {t["id"] for t in S.TEMPLATES} and cat["special"] == S.SPECIAL
