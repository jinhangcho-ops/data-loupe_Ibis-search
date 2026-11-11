"""Phase 9(plans/phase-9.md): 시각화 가드레일의 서버 계산(core/viz_rules.py)과 view.auto 검사(dash_store.check_auto).
데이터는 시험 안에서 지어낸 메모리 표이고 기대값은 손으로 셌다. 새 함수가 스켈레톤이면 실패하는 것이 정상이다.
기존 함수 수정((수정) 절)이 끝나야 통과하는 시험은 xfail(strict=True, reason="phase-9 <항목>")로 표시했다."""
import datetime as dt

import polars as pl
import pytest
from lazy import ibis

from core import dash_store
from core import result
from core import viz_rules
from dashboard import dash_calc


def table(**cols):
    return ibis.memtable(pl.DataFrame(cols).to_arrow())


# ---------- 구간 수 ----------
def test_auto_bins_fd_and_sturges():
    fd = lambda n, iqr, span: viz_rules.auto_bins(n, 0.0, iqr, 0.0, span, "fd", viz_rules.MIN_BINS, viz_rules.MAX_BINS)  # noqa: E731
    assert fd(1000, 40.0, 100.0) == 13  # h = 2·40·1000^(-1/3) = 8 → 12.5 → 13
    assert fd(8000, 40.0, 110.0) == 28  # h = 4 → 27.5 → 28
    assert fd(1_000_000, 40.0, 100.0) == 50  # 137.5 → 위 끝
    assert fd(10, 40.0, 100.0) == 10  # 3 → 아래 끝
    assert viz_rules.auto_bins(1000, None, None, 0.0, 100.0, "sturges", 10, 50) == 11  # ⌈log2 1000⌉ + 1
    assert viz_rules.auto_bins(100_000, 1.0, 2.0, 0.0, 100.0, "sturges", 10, 50) == 18
    assert viz_rules.auto_bins(1000, 5.0, 5.0, 0.0, 100.0, "fd", 10, 50) == 11  # IQR 0이면 Sturges
    assert viz_rules.auto_bins(1000, 5.0, 5.0, 3.0, 3.0, "fd", 10, 50) == 1  # 값이 모두 같다
    assert viz_rules.auto_bins(1000, 0.0, 40.0, 0.0, 100.0, "fd", 20, 30) == 20
    for bad in [(0, 0.0, 1.0, 0.0, 1.0, "fd", 10, 50), (10, 0.0, 1.0, 0.0, 1.0, "scott", 10, 50), (10, 0.0, 1.0, 0.0, 1.0, "fd", 50, 10)]:
        with pytest.raises(ValueError):
            viz_rules.auto_bins(*bad)


def test_tail_histogram_clips_tails():
    t = table(v=[float(i) for i in range(100)] + [1000.0])
    h = viz_rules.tail_histogram(t, "v", None, "fd", 10, 50, (0.01, 0.99))
    assert set(h) == {"edges", "counts", "under", "over", "clip"}
    assert len(h["edges"]) == len(h["counts"]) + 1 and 10 <= len(h["counts"]) <= 50
    assert h["under"] + h["over"] + sum(h["counts"]) == 101
    assert h["over"] >= 1 and h["edges"][-1] < 1000
    assert h["clip"] == [h["edges"][0], h["edges"][-1]]
    whole = viz_rules.tail_histogram(t, "v", 5, "fd", 10, 50, None)
    assert len(whole["counts"]) == 5 and whole["under"] == whole["over"] == 0 and whole["clip"] is None
    assert whole["edges"][0] == 0 and whole["edges"][-1] == 1000 and sum(whole["counts"]) == 101
    empty = viz_rules.tail_histogram(table(v=pl.Series([], dtype=pl.Float64)), "v", None, "fd", 10, 50, (0.01, 0.99))
    assert empty == {"edges": [], "counts": [], "under": 0, "over": 0, "clip": None}


def test_bin_args():
    assert viz_rules.bin_args({}, None, 20) == (None, "fd", viz_rules.MIN_BINS, viz_rules.MAX_BINS, (viz_rules.CLIP_LO, viz_rules.CLIP_HI))
    assert viz_rules.bin_args({}, 7, 20)[0] == 7  # 사용자 구간 수가 먼저
    assert viz_rules.bin_args({"bins": False, "tail": False}, None, 20) == (20, "fd", viz_rules.MIN_BINS, viz_rules.MAX_BINS, None)
    assert viz_rules.bin_args({"bins": {"rule": "sturges", "max": 30}, "tail": {"hi": 0.95}}, None, 20) == (None, "sturges", viz_rules.MIN_BINS, 30, (viz_rules.CLIP_LO, 0.95))


# ---------- 육각 밀도 ----------
def test_hex_cells_counts_all_rows():
    t = table(x=[0.0, 0.0, 0.0, 1.0, None], y=[0.0, 0.0, 0.0, 1.0, 2.0])
    h = viz_rules.hex_cells(t, "x", "y", 10)
    assert h["cols"] == 10 and h["x"] == [0.0, 1.0] and h["y"] == [0.0, 1.0]
    assert sum(c[2] for c in h["cells"]) == 4  # 빈 값이 있는 행은 뺀다
    assert max(h["cells"], key=lambda c: c[2])[:3] == [0.0, 0.0, 3]
    assert viz_rules.hex_cells(table(x=pl.Series([], dtype=pl.Float64), y=pl.Series([], dtype=pl.Float64)), "x", "y", 10)["cells"] == []


# ---------- 상위 그룹 밖 값 ----------
def test_rest_values_outside_labels():
    t = table(x=["a", "b", "c", "d", None], g=["p", "q", "p", "q", "p"], v=[1.0, 2.0, 3.0, 4.0, 5.0])
    series = [{"y": "v", "agg": "sum"}, {"y": "v", "agg": "mean"}, {"y": None, "agg": "count"}]
    assert viz_rules.rest_values(t, "x", series, None, None, ["a", "b"]) == [12.0, None, 3]
    assert viz_rules.rest_values(t, "x", series, None, None, ["a", "b", None]) == [7.0, None, 2]
    assert viz_rules.rest_values(t, "x", series[:1], "g", ["p", "q"], ["a", "b"]) == [8.0, 4.0]  # 계열 × 나눈 값 차례


# ---------- view.auto 검사 ----------
def test_check_auto_accepts_and_rejects():
    ok = {"topn": {"v": 10}, "pin": False, "spaghetti": {"mode": "small"}, "refs": {"lines": ["mean", "p80"]}, "sort": {"group_by": "매출"}, "bins": {"rule": "sturges"}}
    assert dash_store.check_auto(ok) == ok
    assert dash_store.check_auto({}) == {}
    bad = ["x", {"nope": False}, {"topn": {"w": 3}}, {"topn": {"v": 1}}, {"topn": {"v": True}}, {"topn": True}, {"round": {"max": 5, "keep": 5}},
           {"bins": {"min": 40, "max": 20}}, {"tail": {"lo": 0.4, "hi": 0.6, "x": 1}}, {"refs": {"lines": "mean"}}, {"refs": {"lines": ["avg"]}},
           {"spaghetti": {"mode": "dots"}}, {"sort": {"group_by": "가" * 101}}, {"spaghetti": {"max": 5}}]
    for value in bad:
        with pytest.raises(ValueError):
            dash_store.check_auto(value)


# ---------- 시각화 설정(낱말 목록·쿼리 화면 자동 규칙 기본값) ----------
def test_prefs_default_save_and_reject():
    assert viz_rules.load_prefs() == {"words": viz_rules.DEFAULT_WORDS, "query_auto": {}}  # conftest가 설정 폴더를 비운 임시 폴더로
    mine = {"words": {"good": ["흑자"], "bad": [], "other": ["나머지"], "null": ["미입력"]}, "query_auto": {"topn": False, "pin": False}}
    assert viz_rules.save_prefs(mine) == mine
    assert viz_rules.load_prefs() == mine
    bad = ["x", {"words": mine["words"]}, {"words": {**mine["words"], "good": "흑자"}, "query_auto": {}}, {"words": {k: v for k, v in mine["words"].items() if k != "null"}, "query_auto": {}},
           {"words": {**mine["words"], "bad": ["가" * (viz_rules.MAX_WORD_LEN + 1)]}, "query_auto": {}}, {"words": {**mine["words"], "bad": ["a", "a"]}, "query_auto": {}},
           {"words": {**mine["words"], "bad": [" a"]}, "query_auto": {}}, {"words": {**mine["words"], "bad": [""]}, "query_auto": {}},
           {"words": {**mine["words"], "bad": [str(i) for i in range(viz_rules.MAX_WORDS + 1)]}, "query_auto": {}},
           {"words": mine["words"], "query_auto": {"topn": {"v": 3}}}, {"words": mine["words"], "query_auto": {"nope": False}}, {**mine, "extra": 1}]
    for value in bad:
        with pytest.raises(ValueError):
            viz_rules.check_prefs(value)
    assert viz_rules.load_prefs() == mine  # 거부한 것은 쓰지 않았다


def test_check_view_keeps_auto_and_scale():
    view = dash_store.check_view({"type": "", "auto": {"topn": False}, "scale": "log"})
    assert view["auto"] == {"topn": False} and view["scale"] == "log"
    with pytest.raises(ValueError):
        dash_store.check_view({"type": "", "scale": "ln"})


def test_check_data_accepts_new_keys():
    data = dash_store.check_data({"bins": {"edges": [0, 1], "counts": [3], "under": 1, "over": 2, "clip": [0, 1]}, "x_scale": "ordinal", "x_levels": ["a", "b"],
                                  "values": {"labels": ["a"], "series": [], "rest": [1.0]}}, "chart")
    assert data["bins"]["over"] == 2 and data["x_levels"] == ["a", "b"]


# ---------- 기존 함수 수정 ----------
def test_group_chart_auto_orders_text_by_value():
    t = table(x=[f"g{i:02d}" for i in range(60)], v=[float(i) for i in range(60)])
    labels, datasets, _ = result.group_chart(t, "x", [{"y": "v", "agg": "sum"}], None, "auto")
    assert labels[0] == "g59" and len(labels) == result.MAX_GROUPS and datasets[0]["truncated"]
    nums = table(x=list(range(60)), v=[float(i) for i in range(60)])
    assert result.group_chart(nums, "x", [{"y": "v", "agg": "sum"}], None, "auto")[0][0] == 0  # 숫자 X는 X 순
    small, cut, _ = result.group_chart(t, "x", [{"y": "v", "agg": "sum"}], None, "value_asc")  # 값이 작은 50개, 작은 순
    assert small[:2] == ["g00", "g01"] and small[-1] == "g49" and cut[0]["truncated"]
    rev = table(x=[f"g{i:02d}" for i in range(60)], v=[float(60 - i) for i in range(60)])
    assert result.group_chart(rev, "x", [{"y": "v", "agg": "sum"}], None, "name")[0] == [f"g{i:02d}" for i in range(50)]  # 이름 차례 앞 50개(값과 상관없이)
    assert result.group_chart(nums, "x", [{"y": "v", "agg": "sum"}], None, "name")[0] == list(range(50))  # 숫자 X는 숫자 차례
    days = table(x=[dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(60)], v=[1.0] * 60)
    assert result.group_chart(days, "x", [{"y": "v", "agg": "sum"}], None, "name")[0][0] == dt.date(2026, 1, 1)  # 이름 순은 앞 50개(x는 최근 50개)
    assert result.group_chart(days, "x", [{"y": "v", "agg": "sum"}], None, "x")[0][0] == dt.date(2026, 1, 11)
    with pytest.raises(ValueError):
        result.group_chart(t, "x", [{"y": "v", "agg": "sum"}], None, "random")


def test_analysis_histogram_and_scatter_use_rules():
    from analysis import stats
    t = table(v=[float(i) for i in range(1000)] + [1e6])
    h = stats.analyze(t, {"kind": "distribution", "column": "v", "auto": {}})["histogram"]
    assert {"under", "over", "clip"} <= set(h) and h["over"] >= 1
    assert stats.analyze(t, {"kind": "distribution", "column": "v", "auto": {"tail": False, "bins": False}})["histogram"]["clip"] is None
    n = viz_rules.HEX_FROM + 1
    xy = table(x=[float(i % 97) for i in range(n)], y=[float(i % 89) for i in range(n)])
    assert sum(c[2] for c in stats.analyze(xy, {"kind": "scatter", "x": "x", "y": "y", "auto": {}})["hex"]["cells"]) == n


def test_histogram_data_auto_bins_and_tail():
    t = table(v=[float(i) for i in range(1000)] + [1e6])
    b = dash_calc.histogram_data(t, "v", None, {})["bins"]
    assert {"under", "over", "clip"} <= set(b) and b["over"] >= 1 and 10 <= len(b["counts"]) <= 50
    fixed = dash_calc.histogram_data(t, "v", 7, {"tail": False})["bins"]
    assert len(fixed["counts"]) == 7 and fixed["clip"] is None


def test_points_data_adds_hex_over_limit():
    n = viz_rules.HEX_FROM + 1
    t = table(x=[float(i % 97) for i in range(n)], y=[float(i % 89) for i in range(n)])
    found = dash_calc.points_data(t, "x", "y", None, None, {})
    assert sum(c[2] for c in found["hex"]["cells"]) == n
    assert "hex" not in dash_calc.points_data(t, "x", "y", None, None, {"density": False})


def test_boxplot_data_adds_tail_quantiles():
    t = table(g=["a"] * 101, v=[float(i) for i in range(100)] + [1e6])
    box = dash_calc.boxplot_data(t, "g", "v")["values"]["groups"][0]
    assert {"p01", "p99", "below", "above"} <= set(box) and box["above"] >= 1


def test_dash_constants_sends_rule_and_order_names(client):
    dash = client.get("/api/dash/constants").get_json()["dash"]
    assert set(dash["auto_names"]) == set(viz_rules.AUTO_PARAMS)
    assert set(dash["order_names"]) == set(viz_rules.ORDERS)
    assert dash["auto_names"]["tail"] == "꼬리 자르기" and dash["order_names"]["value_asc"] == "값 작은 순"
