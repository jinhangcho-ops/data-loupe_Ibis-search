"""Phase 6(plans/phase-6.md) 새 컬럼: 행 단위 압축·거리 식(row_*), K-Means(표 집계 반복), 전처리 recipe의 kmeans·통계 판정·화면 상수.
데이터는 시험 안에서 지어낸 memtable. 기존 함수 수정이 끝나야 통과하는 시험은 xfail(strict=True, reason="phase-6 <항목>")이다."""
import math

import pytest

from lazy import ibis
from core import prep_derive
from core import prep_ops


def mem(**cols):
    n = len(next(iter(cols.values())))
    return ibis.memtable({"id": list(range(n)), **cols})


def got(t, *names):
    d = t.order_by("id").select(*names).to_pyarrow().to_pydict()
    return d[names[0]] if len(names) == 1 else d


def col_of(t, expr):
    return got(t.mutate(v=expr), "v")


ABC = dict(a=[1.0, None, 3.0, None], b=[2.0, 5.0, None, None], c=[3.0, 1.0, 1.0, None])


def test_row_stat_direct():
    t = mem(**ABC)
    vs = [t.a, t.b, t.c]
    assert col_of(t, prep_derive.row_stat("sum", vs)) == [6.0, 6.0, 4.0, None]
    assert col_of(t, prep_derive.row_stat("mean", vs)) == [2.0, 3.0, 2.0, None]
    assert col_of(t, prep_derive.row_stat("count", vs)) == [3, 2, 2, 0]
    assert col_of(t, prep_derive.row_stat("positive", vs)) == [3, 2, 2, 0]
    assert col_of(t, prep_derive.row_stat("min", vs)) == [1.0, 1.0, 1.0, None]
    assert col_of(t, prep_derive.row_stat("max", vs)) == [3.0, 5.0, 3.0, None]
    sd = col_of(t, prep_derive.row_stat("std", vs))
    assert sd[:3] == pytest.approx([1.0, math.sqrt(8), math.sqrt(2)]) and sd[3] is None
    assert col_of(t, prep_derive.row_pick(vs, ["a", "b", "c"], True)) == ["c", "b", "a", None]
    assert col_of(t, prep_derive.row_pick(vs, ["a", "b", "c"], False)) == ["a", "c", "c", None]
    d = mem(p=[0.0, 1.0], q=[0.0, 1.0], r=[3.0, 0.0], s=[4.0, 1.0])
    assert col_of(d, prep_derive.row_distance([d.p, d.q, d.r, d.s], "euclid")) == pytest.approx([5.0, 1.0])
    cos = col_of(d, prep_derive.row_distance([d.p, d.q, d.r, d.s], "cosine"))
    assert cos[0] is None and cos[1] == pytest.approx(1 / math.sqrt(2))


def test_row_funcs():
    t = mem(**ABC, p=[0.0, 1.0, 2.0, 3.0], q=[0.0, 1.0, 2.0, 3.0])
    build = lambda text: col_of(t, prep_derive.Formula(t, text).build())
    assert build("row_mean(a, b, c)") == [2.0, 3.0, 2.0, None]
    assert build("row_argmax(a, b, col(\"c\"))") == ["c", "b", "a", None]
    assert build("row_dist(p, q, 3, 4)") == pytest.approx([5.0, math.sqrt(13), math.sqrt(5), 1.0])
    assert build("safe_divide(row_max(a, c), row_sum(a, c))")[0] == pytest.approx(0.75)
    for bad, word in (("row_sum(a)", "row_sum"), ("row_argmax(a + 1, b)", "컬럼 이름만"), ("row_dist(a, b, c)", "앞 절반")):
        with pytest.raises(ValueError, match=word):
            prep_derive.Formula(t, bad).build()


def test_nearest():
    t = mem(x=[0.0, 0.1, 5.0, 4.9], y=[0.0, 0.0, 5.0, 5.0])
    assert col_of(t, prep_derive.nearest([t.x, t.y], [[0.0, 0.0], [5.0, 5.0]])) == [0, 0, 1, 1]


def blobs():
    xs = [i * 0.01 for i in range(20)] + [10 + i * 0.01 for i in range(20)] + [None]
    ys = [i * 0.01 for i in range(20)] + [10 + i * 0.01 for i in range(20)] + [1.0]
    return mem(x=xs, y=ys)


def test_kmeans_two_blobs():
    info = {}
    out, made = prep_derive.kmeans(blobs(), {"columns": ["x", "y"], "k": 2}, info)
    assert made == [prep_derive.KMEANS_NAME]
    ids = got(out, prep_derive.KMEANS_NAME)
    assert len(set(ids[:20])) == 1 and len(set(ids[20:40])) == 1 and ids[0] != ids[20] and ids[40] is None
    km = info["kmeans"]
    assert sorted(km["sizes"]) == [20, 20] and km["converged"] is True and km["rows"] == 40 and km["k"] == 2
    centers = sorted(km["centers"])
    assert centers[0] == pytest.approx([0.095, 0.095], abs=1e-6) and centers[1] == pytest.approx([10.095, 10.095], abs=1e-6)


def test_kmeans_errors():
    t = mem(x=[1.0, 2.0, 3.0], y=[1.0, 1.0, 1.0], s=["a", "b", "c"], z=[3.0, 1.0, 2.0])
    for spec, word in (({"columns": ["x", "z"], "k": 1}, "군집 수"), ({"columns": ["x"], "k": 2}, "2~"), ({"columns": ["x", "s"], "k": 2}, "숫자"),
                       ({"columns": ["x", "y"], "k": 2}, "값이 모두 같은"), ({"columns": ["x", "z"], "k": 2, "bad": 1}, "모양")):
        with pytest.raises(ValueError, match=word):
            prep_derive.kmeans(t, spec, {})


def test_shape_kmeans():
    from prep import prep_expr
    info = {}
    out, sources = prep_expr.shape(None, {"base": "t", "kmeans": {"columns": ["x", "y"], "k": 2, "name": "군집"}}, table=blobs(), info=info)
    assert "군집" in out.columns and sources["군집"] == ("", True) and info["kmeans"]["name"] == "군집"


def test_shape_cut_notes():
    from prep import prep_expr
    info = {}
    t = mem(x=[0.0, 1.0, 2.0, -5.0, -1.0, None], y=[0.0, 1.0, 10.0, -2.0, 3.0, 4.0])
    recipe = {"base": "t", "columns": {"x": {"ops": {"cut": {"how": "zero", "n": 2}}}, "y": {"ops": {"cut": {"how": "log"}}}}}
    prep_expr.shape(None, recipe, table=t, info=info)
    assert info["cut"] == [{"column": "x", "label": prep_ops.NEG_LABEL, "rows": 2}, {"column": "y", "label": prep_ops.NONPOS_LABEL, "rows": 2}]


def test_prep_app_recipe_helpers():
    from prep import prep_app
    cs = prep_app.computes_stats
    assert cs({"columns": {"x": {"ops": {"cut": {"how": "width"}}}}}) and cs({"kmeans": {"columns": ["x", "y"], "k": 2}})
    assert cs({"columns": {"x": {"ops": {"scale": "robust"}}}}) and cs({"columns": {"x": {"ops": {"gfill": {"how": "mean", "group": ["g"]}}}}})
    assert cs({"columns": {"x": {"ops": {"outlier": {"how": "clip", "by": "pct"}}}}})
    assert not cs({"columns": {"s": {"ops": {"ordinal": {"map": {"a": 1}}}}}})
    texts = prep_app.named_texts({"columns": {"x": {"ops": {"gfill": {"how": "mean", "group": ["등급"]}, "target": {"target": "이탈"}, "cut": {"how": "best", "target": "구매"}}}},
                                  "kmeans": {"columns": ["키", "몸무게"], "k": 2}})
    assert {"등급", "이탈", "구매", "키", "몸무게"} <= set(texts)


def test_screen_constants(folder):
    from core import server
    from prep import prep_app  # noqa: F401  late_constants를 단다
    from query import search_app  # noqa: F401
    prep, query = server.constants("prep"), server.constants("query")
    assert prep["prep6"] == prep_ops.SCREEN6 and query["prep6"] == prep_ops.SCREEN6
    assert prep["kmeans"] == {"k": prep_derive.KMEANS_K, "max_k": prep_derive.KMEANS_MAX_K, "max_cols": prep_derive.KMEANS_MAX_COLS, "name": prep_derive.KMEANS_NAME,
                              "iters": prep_derive.KMEANS_ITERS, "tol": prep_derive.KMEANS_TOL, "init_rows": prep_derive.KMEANS_INIT_ROWS}
