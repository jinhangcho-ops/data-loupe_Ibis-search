"""Phase 6(plans/phase-6.md) 전처리 단계: 표시(flags)·그룹별 채우기(gfill)·거듭제곱/Box-Cox/Yeo-Johnson(power)·자동 구간(cut)·Others·빈도·타겟·순서형 인코딩과
기존 단계(scale·outlier·bins·onehot)의 새 선택, 쿼리 계산 줄의 미리 구한 통계(Phase 1 Known). 데이터는 시험 안에서 지어낸 memtable·임시 DuckDB.
새 함수만 부르는 시험은 표시가 없고(스켈레톤이면 NotImplementedError로 실패), 기존 함수 수정이 끝나야 통과하는 시험은 xfail(strict=True, reason="phase-6 <항목>")이다."""
import datetime as dt
import math

import duckdb
import numpy as np
import pytest

from lazy import ibis
from core import calc
from core import prep_ops
from core.prep_ops import Known

from test_query_stats import body, connect


def mem(**cols):
    """id(0부터) 컬럼을 붙인 메모리 표."""
    n = len(next(iter(cols.values())))
    return ibis.memtable({"id": list(range(n)), **cols})


def got(t, *names):
    """id 차례로 컬럼 값들."""
    d = t.order_by("id").select(*names).to_pyarrow().to_pydict()
    return d[names[0]] if len(names) == 1 else d


def no_queries(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("미리 구한 값이 있으면 조회하지 않는다")
    monkeypatch.setattr(prep_ops, "stats", boom)
    monkeypatch.setattr(prep_ops, "quantiles", boom)


# ---------- 작은 도우미 ----------

def test_with_known():
    chk = prep_ops.with_known(lambda v: isinstance(v, dict) and set(v) <= {"a"})
    assert chk({"a": 1}) is True
    assert chk(Known({"a": 1, "known": {"x": 1}})) is True
    assert chk({"a": 1, "known": {"x": 1}}) is False  # JSON에서 온 known은 받지 않는다
    assert chk(Known({"b": 1, "known": 1})) is False


def test_needs_stats():
    ns = prep_ops.needs_stats
    assert ns("flags", {"zero": True, "cmp": [">=", 3], "between": [1, 2], "isin": [1]}) is False
    assert ns("flags", {"top": True}) is True and ns("flags", {"above": "median"}) is True and ns("flags", {"outlier": {"by": "iqr"}}) is True
    assert ns("power", {"how": "boxcox"}) is True and ns("power", {"how": "yeojohnson", "lambda": 0.5}) is False and ns("power", {"how": "power", "p": 2}) is False
    assert all(ns(op, v) for op, v in (("cut", {"how": "width"}), ("others", {"how": "top"}), ("freq", {"how": "count"}), ("target", {"target": "y"}),
                                       ("gfill", {"how": "mean", "group": ["g"]})))
    with pytest.raises(ValueError):
        ns("fill", {"how": "mean"})


def test_more_stats_refuses():
    t = mem(x=[1.0, 2.0])
    with pytest.raises(ValueError, match="미리 구할 통계가 없어요"):
        prep_ops.more_stats(t, "x", "flags", {"zero": True})
    with pytest.raises(ValueError):
        prep_ops.more_stats(t, "x", "fill", {"how": "mean"})


def test_validators():
    assert prep_ops.is_edges([0, 10, 100]) and not prep_ops.is_edges([10, 0]) and not prep_ops.is_edges([1])
    f = prep_ops.is_flags
    assert f({"zero": True}) and f({"cmp": [">=", 60]}) and f({"top": True}) and f({"top": [1, 5]}) and f({"isin": ["KR", "US"]}) and f({"between": [1, 3]})
    assert f({"outlier": {"by": "mad", "k": 2}}) and f({"above": "mean"})
    assert not f({}) and not f({"zero": False}) and not f({"cmp": ["=>", 1]}) and not f({"top": [0]}) and not f({"top": [100]}) and not f({"between": [3, 1]})
    assert not f({"outlier": {"by": "x"}}) and not f({"outlier": {"by": "iqr", "k": 0}}) and not f({"above": "max"}) and not f({"nope": 1})
    assert prep_ops.is_gfill({"how": "mean", "group": ["g"]}) and not prep_ops.is_gfill({"how": "mean", "group": []}) and not prep_ops.is_gfill({"how": "sum", "group": ["g"]})
    assert not prep_ops.is_gfill({"how": "mean", "group": ["a", "b", "c", "d"]}) and not prep_ops.is_gfill({"how": "mean", "group": ["a", "a"]})
    p = prep_ops.is_power
    assert p({"how": "power", "p": 2}) and p({"how": "boxcox"}) and p({"how": "yeojohnson", "lambda": -0.5})
    assert not p({"how": "power", "p": 0}) and not p({"how": "power"}) and not p({"how": "boxcox", "p": 2}) and not p({"how": "log"})
    c = prep_ops.is_cut
    assert c({"how": "width"}) and c({"how": "quantile", "n": 10, "closed": "right", "label": "index"}) and c({"how": "floor", "width": 10})
    assert c({"how": "tails", "tail": 5, "n": 3}) and c({"how": "best", "target": "y", "method": "chi"})
    assert not c({"how": "floor"}) and not c({"how": "width", "width": 10}) and not c({"how": "best"}) and not c({"how": "width", "n": 1}) and not c({"how": "tails", "tail": 50})
    assert not c({"how": "log", "n": 3}) and not c({"how": "width", "closed": "both"})
    o = prep_ops.is_others
    assert o({"how": "top", "k": 3}) and o({"how": "share", "share": 1.5, "label": "ETC"}) and o({"how": "pareto", "cum": 80})
    assert not o({"how": "top", "share": 1}) and not o({"how": "top", "k": 0}) and not o({"how": "pareto", "cum": 100})
    assert prep_ops.is_target({"target": "y"}) and prep_ops.is_target({"target": "y", "smooth": 2}) and not prep_ops.is_target({"target": ""}) and not prep_ops.is_target({"target": "y", "smooth": -1})
    assert prep_ops.is_ordinal({"map": {"하": 1, "중": 2}}) and prep_ops.is_ordinal({"map": {"하": 1}, "else": 0})
    assert not prep_ops.is_ordinal({"map": {}}) and not prep_ops.is_ordinal({"map": {"하": "1"}})


# ---------- flags ----------

def test_flags_simple():
    t = mem(x=[0.0, 1.0, 5.0, 10.0, None, 100.0], s=["KR", "US", "JP", "CN", None, "KR"])
    out = prep_ops.flags(t, "x", {"zero": True, "cmp": [">=", 5], "between": [1, 10], "isin": [1, 100]})
    assert got(out, "x_is_zero", "x_thr", "x_between", "x_isin") == {
        "x_is_zero": [1, 0, 0, 0, 0, 0], "x_thr": [0, 0, 1, 1, 0, 1], "x_between": [0, 1, 1, 1, 0, 0], "x_isin": [0, 1, 0, 0, 0, 1]}
    assert got(prep_ops.flags(t, "s", {"isin": ["KR", "US", "JP"]}), "s_isin") == [1, 1, 1, 0, 0, 1]


def test_flags_stats():
    t = mem(x=[float(v) for v in range(1, 101)])
    out = prep_ops.flags(t, "x", {"top": [1, 10, 0.5], "above": "mean"})
    d = got(out, "x_top1", "x_top10", "x_top0_5", "x_above_mean")
    assert (sum(d["x_top1"]), sum(d["x_top10"]), sum(d["x_top0_5"]), sum(d["x_above_mean"])) == (1, 10, 1, 50)  # P99 = 99.01, P90 = 90.1, 평균 50.5
    out = prep_ops.flags(t, "x", {"top": True})
    assert [sum(got(out, f"x_top{p}")) for p in prep_ops.FLAG_TOP] == [1, 5, 10]


def test_flags_outlier_like_diag(folder):
    from prep import prep_diag
    assert set(prep_ops.FLAG_K_OPTION.values()) <= set(prep_diag.OPTION_RULES) and prep_ops.MAD_C == prep_diag.MAD_C
    t = mem(y=[float(v) for v in range(1, 21)] + [1000.0])
    for by in ("iqr", "std", "mad"):
        flag = got(prep_ops.flags(t, "y", {"outlier": {"by": by}}), "y_is_outlier")  # 저장한 진단 기준이 없으면 기본 1.5·3·3.5
        assert flag == [0] * 20 + [1], by


def test_flags_k_from_saved_diag(folder):
    from core import settings
    from prep import prep_diag
    t = mem(y=[float(v) for v in range(1, 21)] + [40.0])  # Q1 6, Q3 16: k 1.5면 위 경계 31, k 3이면 46
    run = lambda arg: sum(got(prep_ops.flags(t, "y", {"outlier": arg}), "y_is_outlier"))
    assert run({"by": "iqr"}) == 1
    settings.write(prep_diag.OPTIONS_FILE, {"iqr_k": 3.0})  # 사용자가 [진단 기준]에서 저장한 값
    assert run({"by": "iqr"}) == 0
    assert run({"by": "iqr", "k": 1.5}) == 1  # 값에 적은 k가 먼저


def test_flags_known_no_query(monkeypatch):
    t = mem(x=[float(v) for v in range(1, 101)])
    no_queries(monkeypatch)
    out = prep_ops.flags(t, "x", Known({"top": [10], "outlier": {"by": "iqr"}, "known": {"top": [90.1], "outlier": {"low": 0.0, "high": 95.0}}}))
    assert sum(got(out, "x_top10")) == 10 and sum(got(out, "x_is_outlier")) == 5


def test_flags_errors(folder):
    with pytest.raises(ValueError, match="정수·실수"):
        prep_ops.flags(mem(s=["a", "b"]), "s", {"top": [1]})
    with pytest.raises(ValueError, match="MAD가 0"):
        prep_ops.flags(mem(x=[1.0, 1.0, 1.0, 1.0, 5.0]), "x", {"outlier": {"by": "mad"}})
    with pytest.raises(ValueError, match="표준편차가 0"):
        prep_ops.flags(mem(x=[2.0, 2.0, 2.0]), "x", {"outlier": {"by": "std"}})


# ---------- gfill ----------

def test_group_fill():
    t = mem(g=["a", "a", "b", "b", None, "c"], x=[1.0, None, 3.0, None, None, None])
    assert got(prep_ops.group_fill(t, "x", {"how": "mean", "group": ["g"]}), "x") == [1.0, 1.0, 3.0, 3.0, None, None]
    assert got(prep_ops.group_fill(t, "x", {"how": "median", "group": ["g"]}), "x") == [1.0, 1.0, 3.0, 3.0, None, None]
    s = mem(g=["a", "a", "a", "b"], s=["p", "p", None, None])
    assert got(prep_ops.group_fill(s, "s", {"how": "mode", "group": ["g"]}), "s") == ["p", "p", "p", None]
    n = mem(g=["a", "a", "a"], x=[1, 2, None])
    assert got(prep_ops.group_fill(n, "x", {"how": "mean", "group": ["g"]}), "x") == [1.0, 2.0, 1.5]


def test_group_fill_keeps_order():
    t = ibis.memtable({"id": list(range(8)), "g": ["b", "a", "b", "a", "c", "a", "b", "c"], "x": [1.0, None, None, 4.0, 5.0, None, 3.0, None]})
    out = prep_ops.group_fill(t, "x", {"how": "mean", "group": ["g"]}).to_pyarrow().to_pydict()  # order_by 없이: 조인하지 않으니 행 차례 그대로
    assert out["id"] == list(range(8)) and out["x"] == [1.0, 4.0, 2.0, 4.0, 5.0, 4.0, 3.0, 5.0]


def test_group_fill_known_no_query(monkeypatch):
    t = mem(g=["a", "b", "a"], h=[1, 1, 2], x=[None, None, None])
    no_queries(monkeypatch)
    arg = Known({"how": "mean", "group": ["g", "h"], "known": {"groups": [["a", 1], ["b", 1]], "values": [10.0, 20.0]}})
    assert got(prep_ops.group_fill(t, "x", arg), "x") == [10.0, 20.0, None]


def test_group_fill_errors():
    t = mem(g=["a"], x=[1.0], s=["p"])
    with pytest.raises(ValueError, match="묶음 컬럼"):
        prep_ops.group_fill(t, "x", {"how": "mean", "group": ["x"]})
    with pytest.raises(ValueError, match="정수·실수"):
        prep_ops.group_fill(t, "s", {"how": "mean", "group": ["g"]})
    with pytest.raises(ValueError):
        prep_ops.group_fill(t, "x", {"how": "mean", "group": ["없는컬럼"]})


# ---------- power ----------

def bc_expected(x):
    grid = np.round(np.linspace(*prep_ops.LAMBDA_GRID), 10)
    z = x / np.exp(np.mean(np.log(x)))
    llf = [-len(x) / 2 * np.log(np.var(np.log(z) if abs(lam) < 1e-9 else (z ** lam - 1) / lam)) for lam in grid]
    return float(grid[int(np.nanargmax(llf))])


def yj(x, lam):
    pos, out = x >= 0, np.empty_like(x)
    out[pos] = np.log1p(x[pos]) if abs(lam) < 1e-9 else ((x[pos] + 1) ** lam - 1) / lam
    out[~pos] = -np.log1p(-x[~pos]) if abs(lam - 2) < 1e-9 else -((1 - x[~pos]) ** (2 - lam) - 1) / (2 - lam)
    return out


def yj_expected(x):
    grid = np.round(np.linspace(*prep_ops.LAMBDA_GRID), 10)
    s = np.sum(np.sign(x) * np.log1p(np.abs(x)))
    llf = [-len(x) / 2 * np.log(np.var(yj(x, lam))) + (lam - 1) * s for lam in grid]
    return float(grid[int(np.nanargmax(llf))])


def test_boxcox_lambda_matches_numpy():
    rng = np.random.default_rng(1)
    pos = rng.lognormal(0, 0.5, 200)
    got_bc = prep_ops.boxcox_lambda(mem(x=pos.tolist()), "x", "boxcox")
    assert abs(got_bc - bc_expected(pos)) <= 0.05 + 1e-9  # 같은 격자에서 한 칸 안(DB와 numpy의 반올림 차이)
    sq = rng.uniform(1, 10, 200) ** 2
    assert abs(prep_ops.boxcox_lambda(mem(x=sq.tolist()), "x", "boxcox") - bc_expected(sq)) <= 0.05 + 1e-9
    mixed = rng.normal(0, 1, 200) ** 3
    assert abs(prep_ops.boxcox_lambda(mem(x=mixed.tolist()), "x", "yeojohnson") - yj_expected(mixed)) <= 0.05 + 1e-9


def test_power_values():
    t = mem(x=[1.0, 4.0, 0.0, -1.0])
    run = lambda arg: got(prep_ops.power(t, "x", arg), "x")
    bc0 = run({"how": "boxcox", "lambda": 0})  # 0 이하는 빈 값
    assert bc0[2:] == [None, None] and bc0[0] == pytest.approx(0.0) and bc0[1] == pytest.approx(math.log(4))
    assert run({"how": "boxcox", "lambda": 2})[:2] == pytest.approx([0.0, 7.5])
    assert run({"how": "power", "p": 0.5}) == [pytest.approx(1.0), pytest.approx(2.0), pytest.approx(0.0), None]
    assert run({"how": "power", "p": 2}) == pytest.approx([1.0, 16.0, 0.0, 1.0])
    assert run({"how": "yeojohnson", "lambda": 1}) == pytest.approx([1.0, 4.0, 0.0, -1.0])
    assert run({"how": "yeojohnson", "lambda": 0.5}) == pytest.approx(yj(np.array([1.0, 4.0, 0.0, -1.0]), 0.5).tolist())


def backend_tables(cols):
    """같은 값의 표를 DuckDB(memtable)·Polars·SQLite(메모리) 연결에 만든다."""
    import polars as pl
    polars_con, sqlite_con = ibis.polars.connect(), ibis.sqlite.connect()
    return {"duckdb": ibis.memtable(cols), "polars": polars_con.create_table("t", pl.DataFrame(cols)), "sqlite": sqlite_con.create_table("t", ibis.memtable(cols))}


@pytest.mark.parametrize("backend", ["duckdb", "polars", "sqlite"])
def test_power_inf_backends(backend):
    """넘친 결과는 빈 값이 아니라 inf 그대로(사용자 결정 13). 어느 연결에서 오류·빈 값이면 구현을 바꾸지 말고 보고한다."""
    t = backend_tables({"id": [0, 1], "x": [10.0, 1e200]})[backend]
    run = lambda arg: prep_ops.power(t, "x", arg).order_by("id")["x"].to_pyarrow().to_pylist()
    assert run({"how": "power", "p": 400})[0] == math.inf  # 10^400
    assert run({"how": "boxcox", "lambda": 3})[1] == math.inf  # (1e600 − 1) / 3
    assert run({"how": "yeojohnson", "lambda": 3})[1] == math.inf


def test_power_errors():
    with pytest.raises(ValueError, match="0 이하"):
        prep_ops.power(mem(x=[0.0, 1.0, 2.0, 3.0]), "x", {"how": "boxcox"})
    with pytest.raises(ValueError, match="값이 모두 같거나"):
        prep_ops.power(mem(x=[2.0, 2.0, 2.0, 2.0]), "x", {"how": "yeojohnson"})


# ---------- 구간 ----------

def test_labeled_bins():
    t = mem(x=[0.0, 5.0, 10.0, 50.0, 100.0, -1.0])
    lb = lambda closed, label, start=0: got(t.mutate(b=prep_ops.labeled_bins(t.x, [0, 10, 100], closed, label, start)), "b")
    assert lb("left", "range") == ["0 ~ 10", "0 ~ 10", "10 ~ 100", "10 ~ 100", "10 ~ 100", None]  # 천 단위 쉼표 꼴(사용자 결정 2)
    assert lb("right", "range") == ["0 ~ 10", "0 ~ 10", "0 ~ 10", "10 ~ 100", "10 ~ 100", None]
    assert lb("left", "index", 1) == [1, 1, 2, 2, 2, None]
    assert lb("left", "mid") == [5.0, 5.0, 55.0, 55.0, 55.0, None]
    big = mem(x=[5000.0, 20000.0, -1500.0, 1234.5])
    texts = got(big.mutate(b=prep_ops.labeled_bins(big.x, [-2000, 0, 1234.5, 10000, 50000], "left", "range")), "b")
    assert texts == ["1,234.5 ~ 10,000", "10,000 ~ 50,000", "-2,000 ~ 0", "1,234.5 ~ 10,000"]
    assert got(t.mutate(b=prep_ops.bins(t.x, [0, 10, 100])), "b")[:3] == ["0–10", "0–10", "10–100"]  # 옛 목록 꼴 bins 글자는 그대로


def test_best_edges():
    pos, neg = [0, 0, 0, 10, 10, 10], [10, 10, 10, 0, 0, 0]
    for method in prep_ops.BEST_METHODS:
        assert prep_ops.best_edges([0, 1, 2, 3, 4, 5, 6], pos, neg, 2, method) == [0, 3, 6], method
    assert prep_ops.best_edges([0, 1, 2, 3, 4, 5, 6], pos, neg, 1, "iv") == [0, 6]
    with pytest.raises(ValueError, match="한 값뿐"):
        prep_ops.best_edges([0, 1, 2], [0, 0], [3, 4], 2, "chi")


def counts(values):
    out = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def test_cut_width_quantile():
    t = mem(x=[float(v) for v in range(100)])
    want = {"0 ~ 24.75": 25, "24.75 ~ 49.5": 25, "49.5 ~ 74.25": 25, "74.25 ~ 99": 25}
    assert counts(got(prep_ops.cut(t, "x", {"how": "width", "n": 4}), "x")) == want
    assert counts(got(prep_ops.cut(t, "x", {"how": "quantile", "n": 4}), "x")) == want
    idx = got(prep_ops.cut(t, "x", {"how": "width", "n": 4, "label": "index"}), "x")
    assert (idx[0], idx[-1]) == (0, 3)


def test_cut_zero_log_floor_tails():
    z = mem(x=[0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 4.0, -5.0, None])
    assert got(prep_ops.cut(z, "x", {"how": "zero", "n": 2}), "x") == [prep_ops.ZERO_LABEL] * 3 + ["1 ~ 2.5", "1 ~ 2.5", "2.5 ~ 4", "2.5 ~ 4", prep_ops.NEG_LABEL, None]
    assert got(prep_ops.cut(z, "x", {"how": "zero", "n": 2, "label": "index"}), "x") == [0, 0, 0, 1, 1, 2, 2, prep_ops.SPECIAL_INDEX, None]
    assert got(prep_ops.cut(z, "x", {"how": "zero", "n": 2, "label": "mid"}), "x")[7] == -2.5  # 최솟값 −5와 0의 가운데
    lg = mem(x=[1.0, 5.0, 10.0, 50.0, 100.0, 0.0, 0.5, -3.0])
    assert got(prep_ops.cut(lg, "x", {"how": "log"}), "x") == ["1 ~ 10", "1 ~ 10", "10 ~ 100", "10 ~ 100", "10 ~ 100", prep_ops.NONPOS_LABEL, "0.1 ~ 1", prep_ops.NONPOS_LABEL]
    assert got(prep_ops.cut(mem(x=[0.0, -1.0]), "x", {"how": "log"}), "x") == [prep_ops.NONPOS_LABEL] * 2  # 양수가 없으면 0 이하 구간만
    ages = mem(x=[3, 15, 20, 29, 41])
    assert got(prep_ops.cut(ages, "x", {"how": "floor", "width": 10}), "x") == ["0 ~ 10", "10 ~ 20", "20 ~ 30", "20 ~ 30", "40 ~ 50"]
    tails = counts(got(prep_ops.cut(mem(x=[float(v) for v in range(1, 101)]), "x", {"how": "tails", "n": 2}), "x"))
    assert tails.pop("~하위5%") == 5 and tails.pop("상위5%~") == 5 and sorted(tails.values()) == [45, 45]


def test_cut_best():
    xs = [float(v) for v in range(100)]
    t = mem(x=xs, y=[1 if v >= 60 else 0 for v in xs])
    for method in prep_ops.BEST_METHODS:
        idx = got(prep_ops.cut(t, "x", {"how": "best", "n": 2, "target": "y", "method": method, "label": "index"}), "x")
        assert idx == [0] * 60 + [1] * 40, method
    with pytest.raises(ValueError, match="0/1"):
        prep_ops.cut(mem(x=[1.0, 2.0], y=[0, 2]), "x", {"how": "best", "target": "y"})


def test_cut_known_no_query(monkeypatch):
    t = mem(x=[10.0, 60.0])
    no_queries(monkeypatch)
    assert got(prep_ops.cut(t, "x", Known({"how": "width", "known": {"edges": [0, 50, 100], "min": 0.0, "max": 100.0}})), "x") == ["0 ~ 50", "50 ~ 100"]


def test_cut_special():
    z = mem(x=[0.0, 1.0, 2.0, -5.0, -1.0, None])
    for label in prep_ops.CUT_LABELS:
        arg = {"how": "zero", "n": 2, "label": label}
        assert prep_ops.cut_special(prep_ops.cut(z, "x", arg), "x", arg) == 2, label
        arg = {"how": "log", "label": label}
        assert prep_ops.cut_special(prep_ops.cut(z, "x", arg), "x", arg) == 3, label  # 0·−5·−1
    assert prep_ops.cut_special(prep_ops.cut(z, "x", {"how": "width"}), "x", {"how": "width"}) is None


# ---------- 범주 ----------

def test_others():
    t = mem(s=["a"] * 5 + ["b"] * 3 + ["c", "d", None])
    run = lambda arg: got(prep_ops.others(t, "s", arg), "s")
    ab = ["a"] * 5 + ["b"] * 3 + ["기타", "기타", None]
    assert run({"how": "top", "k": 2}) == ab and run({"how": "share", "share": 15}) == ab and run({"how": "pareto", "cum": 80}) == ab
    assert run({"how": "share", "share": 40}) == ["a"] * 5 + ["기타"] * 5 + [None]
    assert run({"how": "pareto", "cum": 50, "label": "ETC"}) == ["a"] * 5 + ["ETC"] * 5 + [None]
    with pytest.raises(ValueError, match="값이 없어"):
        prep_ops.others(ibis.memtable({"s": [None, None]}, schema={"s": "string"}), "s", {"how": "top"})


def test_freq_target_ordinal():
    t = mem(s=["a", "a", "b", None])
    assert got(prep_ops.freq(t, "s", {"how": "count"}), "s_freq") == [2, 2, 1, None]
    assert got(prep_ops.freq(t, "s", {"how": "pct"}), "s_freq")[:3] == pytest.approx([200 / 3, 200 / 3, 100 / 3])
    e = mem(s=["a", "a", "b", "b", "c"], y=[1, 0, 1, 1, None])
    assert got(prep_ops.target_encode(e, "s", {"target": "y"}), "s_te") == pytest.approx([0.5, 0.5, 1.0, 1.0, 0.75])
    assert got(prep_ops.target_encode(e, "s", {"target": "y", "smooth": 2}), "s_te")[:3] == pytest.approx([0.625, 0.625, 0.875])
    o = mem(lv=["하", "중", "상", "특", None])
    assert got(prep_ops.ordinal(o, "lv", {"map": {"하": 1, "중": 2, "상": 3}}), "lv_ord") == [1, 2, 3, None, None]
    assert got(prep_ops.ordinal(o, "lv", {"map": {"하": 1, "중": 2, "상": 3}, "else": 0}), "lv_ord") == [1, 2, 3, 0, None]


def test_target_errors():
    e = mem(s=["a"], y=[1], z=["x"])
    with pytest.raises(ValueError, match="달라야"):
        prep_ops.target_encode(e, "s", {"target": "s"})
    with pytest.raises(ValueError, match="숫자"):
        prep_ops.target_encode(e, "s", {"target": "z"})


# ---------- 기존 함수 수정(xfail) ----------

def test_known_stats_phase6():
    t = mem(x=[float(v) for v in range(11)], s=["a", "a", "a", "b", "b", "c", "d", "e", "f", "g", "h"])
    assert prep_ops.known_stats(t, "x", "cut", {"how": "width", "n": 2})["edges"] == [0.0, 5.0, 10.0]
    assert prep_ops.known_stats(t, "x", "scale", "robust") == {"a": 5.0, "b": 5.0}
    assert prep_ops.known_stats(t, "s", "onehot", {"top": 2}) == ["a", "b"]


def test_outlier_pct():
    t = mem(x=[float(v) for v in range(11)])
    out = prep_ops.outlier(t, "x", {"how": "clip", "by": "pct", "p_low": 10, "p_high": 90})
    assert got(out, "x") == [1.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 9.0]


def test_scale_new_hows():
    t = mem(x=[1.0, 2.0, 3.0, 4.0, 5.0])
    assert got(prep_ops.scale(t, "x", "robust"), "x") == pytest.approx([-1.0, -0.5, 0.0, 0.5, 1.0])
    assert got(prep_ops.scale(t, "x", "minmax100"), "x") == pytest.approx([0.0, 25.0, 50.0, 75.0, 100.0])
    assert got(prep_ops.scale(t, "x", "log10"), "x") == pytest.approx([math.log10(1 + v) for v in (1, 2, 3, 4, 5)])
    with pytest.raises(ValueError, match="IQR이 0"):
        prep_ops.scale(mem(x=[1.0, 1.0, 1.0]), "x", "robust")


def test_bins_dict():
    t = mem(x=[0.0, 10.0, 100.0])
    assert got(t.mutate(b=prep_ops.bins(t.x, {"edges": [0, 10, 100], "closed": "right"})), "b") == ["0 ~ 10", "0 ~ 10", "10 ~ 100"]
    assert got(t.mutate(b=prep_ops.bins(t.x, {"edges": [0, 10, 100], "label": "index"})), "b") == [0, 1, 1]


def test_onehot_top():
    t = mem(s=["a", "a", "a", "b", "b", "c"])
    out = prep_ops.onehot(t, "s", {"top": 2})
    assert [c for c in out.columns if c.startswith("s_")] == ["s_a", "s_b"]
    assert got(out, "s_b") == [0, 0, 0, 1, 1, 0] and "s_기타" not in out.columns  # 기타 컬럼은 기본 끔
    more = prep_ops.onehot(mem(s=["a", "a", "a", "b", "b", "c", None]), "s", {"top": 2, "others": True})
    assert got(more, "s_기타") == [0, 0, 0, 0, 0, 1, 0]


def test_label_sorts_new_bins():
    t = mem(c=["10,000 ~ 50,000", "2,000 ~ 10,000", "0 ~ 2,000", None])
    assert got(prep_ops.label(t, "c", True), "c_label") == [2, 1, 0, None]  # 시작 숫자 순. 지금 코드는 BIN_START가 맞지 않아 글자 차례("0", "10,", "2,")라 [1, 2, 0]


def test_validators_modified():
    io = prep_ops.is_outlier
    assert io({"how": "clip", "by": "pct"}) and io({"how": "drop", "by": "pct", "p_low": 5, "p_high": 95})
    assert not io({"how": "clip", "by": "pct", "p_low": 95, "p_high": 5}) and not io({"how": "clip", "by": "iqr", "p_low": 5})
    assert not io({"how": "clip", "by": "pct", "low": 1})


def test_ops_order():
    assert prep_ops.OPS_NAMES == ["trim", "squeeze", "nulls", "remove", "case", "replace", "regex", "type", "flag", "flags", "gfill", "fill", "outlier", "power", "scale", "round",
                                  "bins", "cut", "rare", "others", "parts", "label", "onehot", "freq", "target", "ordinal", "extract", "has", "counts", "expand"]
    assert prep_ops.OUTLIER_AT == prep_ops.OPS_NAMES.index("outlier")


def test_apply_ops_new_ops():
    t = mem(x=[0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0], g=["a"] * 5 + ["b"] * 5)
    out = prep_ops.apply_ops(t, "x", {"flags": {"zero": True}, "cut": {"how": "width", "n": 2, "label": "index"}})
    assert got(out, "x_is_zero") == [1] + [0] * 9 and got(out, "x") == [0] * 5 + [1] * 5
    assert got(prep_ops.apply_ops(t, "x", {"power": {"how": "power", "p": 2}}), "x")[3] == pytest.approx(9.0)
    assert got(prep_ops.apply_ops(t, "g", {"others": {"how": "top", "k": 1}}), "g") == ["a"] * 5 + ["기타"] * 5
    with pytest.raises(ValueError, match="정수·실수"):
        prep_ops.apply_ops(t, "g", {"cut": {"how": "width"}})
    with pytest.raises(ValueError, match="형식으로 쓰세요"):
        prep_ops.apply_ops(t, "x", {"cut": {"how": "nope"}})
    with pytest.raises(ValueError, match="known"):
        prep_ops.apply_ops(t, "x", {"cut": {"how": "width", "known": {"edges": [0, 1]}}})  # Phase 1 KNOWN_REFUSED


def test_derived_flags_rest():
    t = mem(x=[float(v) for v in range(1, 11)] + [None])
    ops = {"flags": {"top": [10]}, "fill": {"how": "drop"}}
    assert "x_top10" not in prep_ops.apply_ops(t, "x", ops, "rows").columns  # 통계를 쓰는 표시는 행 빼기 뒤(rest)
    assert "x_top10" in prep_ops.apply_ops(t, "x", ops, "rest").columns


def test_pin_value_phase6():
    t = mem(x=[float(v) for v in range(11)])
    pinned = prep_ops.pin_value(t, "x", "cut", {"how": "width", "n": 2})
    assert isinstance(pinned, Known) and pinned["how"] == "width" and pinned["known"]["edges"] == [0.0, 5.0, 10.0]
    flagged = prep_ops.pin_value(t, "x", "flags", {"top": [10]})
    assert flagged["known"]["top"] == [pytest.approx(9.0)]


def test_labels_fields():
    assert [o["id"] for o in prep_ops.LABELS] == prep_ops.OPS_NAMES
    fields = {o["id"]: [f["name"] for f in o["fields"]] for o in prep_ops.LABELS}
    assert fields["flags"] == ["zero", "cmp_op", "cmp", "top", "top_pct", "above", "low", "high", "isin", "out_by", "out_k"]
    assert fields["gfill"] == ["how", "g1", "g2", "g3"] and fields["power"] == ["how", "p", "lambda"]
    assert fields["cut"] == ["how", "n", "width", "tail", "target", "method", "closed", "label"]
    assert fields["others"] == ["how", "k", "share", "cum", "label"] and fields["freq"] == ["how"] and fields["target"] == ["target", "smooth"] and fields["ordinal"] == ["map", "else"]
    assert fields["onehot"] == ["top", "others"] and fields["bins"][-2:] == ["closed", "label"] and fields["outlier"][-2:] == ["p_low", "p_high"]
    scale = next(o for o in prep_ops.LABELS if o["id"] == "scale")["fields"][0]["options"]
    assert [o[0] for o in scale] == list(prep_ops.SCALES)


def test_target_leak_note():
    assert "누수" in prep_ops.TARGET_LEAK_NOTE and prep_ops.SCREEN6["target_note"] == prep_ops.TARGET_LEAK_NOTE
    notes = {o["id"]: o.get("note") for o in prep_ops.LABELS}
    assert notes["target"] == prep_ops.TARGET_LEAK_NOTE and all(v is None for k, v in notes.items() if k != "target")


def test_reads_stats_phase6():
    dt_ = ibis.dtype("float64")
    rs = calc.reads_stats
    assert rs("cut", {"how": "floor", "width": 10}, dt_) and rs("flags", {"top": [1]}, dt_) and rs("outlier", {"how": "clip", "by": "pct"}, dt_)
    assert rs("others", {"how": "top"}, dt_) and rs("freq", {"how": "count"}, dt_) and rs("target", {"target": "y"}, dt_) and rs("power", {"how": "boxcox"}, dt_)
    assert rs("gfill", {"how": "mean", "group": ["g"]}, dt_)  # 사용자 결정 6: 미리 구한 묶음별 값(행 차례 그대로)
    assert not rs("flags", {"zero": True}, dt_) and not rs("power", {"how": "power", "p": 2}, dt_)
    assert not rs("ordinal", {"map": {"a": 1}}, dt_) and not rs("flags", Known({"top": [1], "known": {"top": [1.0]}}), dt_)
    assert not rs("cut", "형식이 틀린 값", dt_)


def test_stats_key_follows_diag(folder, monkeypatch):
    import types
    from core import querying, settings
    from prep import prep_diag
    monkeypatch.setattr(querying, "get_con", lambda: types.SimpleNamespace())  # 연결 없이 열쇠만 본다
    monkeypatch.setattr(querying.server, "connection_key", lambda: "conn")  # held_key가 연결 열쇠를 읽는다
    b = body(calcs=['전처리 flags 금액 {"outlier": {"by": "iqr"}}'])
    first = querying.stats_key(b, 0)
    settings.write(prep_diag.OPTIONS_FILE, {"skew_log": 2.0})  # flags가 쓰지 않는 기준: 열쇠 그대로
    assert querying.stats_key(b, 0) == first
    settings.write(prep_diag.OPTIONS_FILE, {"skew_log": 2.0, "iqr_k": 3.0})  # 이상치 배수: 열쇠가 바뀌어 다시 구한다
    assert querying.stats_key(b, 0) != first


@pytest.fixture
def shop6(folder, tmp_path, monkeypatch):
    from core import querying
    from query.search_app import app
    monkeypatch.setattr(querying, "PINS", {})
    raw = duckdb.connect(str(tmp_path / "s6.duckdb"))
    raw.execute('create table orders ("주문일" date, "채널" varchar, "금액" double)')
    raw.executemany("insert into orders values (?, ?, ?)", [(dt.date(2025, 1, 1) + dt.timedelta(days=i), "앱웹"[i % 2], 1000.0 + i) for i in range(60)])
    raw.close()
    c = connect(app.test_client(), {"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "s6.duckdb")})
    yield c
    c.post("/api/disconnect", json={})


def test_query_route_phase6_pins(shop6):
    calcs = ['전처리 flags 금액 {"top": [10]}', '전처리 others 채널 {"how": "top", "k": 1}', '전처리 cut 금액 {"how": "quantile", "n": 4}']
    r = shop6.post("/api/query", json=body(calcs=calcs, limit=100)).get_json()
    assert "error" not in r, r.get("error")
    rows = r["rows"]["rows"]
    assert sum(row["금액_top10"] for row in rows) == 6  # P90 = 1053.1 → 1054~1059
    assert {row["채널"] for row in rows} == {"앱", "기타"} and len({row["금액"] for row in rows}) == 4


def test_prep_formulas_line_phase6():
    from core import prep_derive
    t = mem(s=["a", "a", "b"])
    out, made = prep_derive.apply_formulas(t, [{"line": '전처리 others s {"how": "top", "k": 1}'}, {"line": '전처리 freq s {"how": "count"}'}])
    assert got(out, "s") == ["a", "a", "기타"] and made == ["s_freq"] and got(out, "s_freq") == [2, 2, 1]
