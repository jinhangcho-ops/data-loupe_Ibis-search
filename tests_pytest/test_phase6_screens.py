"""Phase 6(plans/phase-6.md) 화면 쪽: 새 컬럼 상자(newcol.js)의 새 방식(row·flag·bin)과 stat·encode의 새 종류 → 계산 줄, 그 줄을 DuckDB에 걸기,
전처리 단계 칸 상자(prepmenu.js)의 입력 상태 → 서버 값. node로 순수 함수만 부른다. 모두 기존 화면 파일 수정이 끝나야 통과하므로 xfail(strict=True, reason="phase-6 <항목>")이다."""
import json


from lazy import ibis
from test_r5_q2 import needs_node, vm_run

FORMS6 = {
    "row_mean": ({"how": "row", "kind": "row_mean", "cols": ["a", "b", "c"], "name": "m"}, ["식 row_mean(a, b, c) 이름 m"]),
    "weighted": ({"how": "row", "kind": "weighted", "cols": ["a", "b"], "weights": "0.5, 2", "name": "w"}, ["식 0.5 * a + 2 * b 이름 w"]),
    "ratio": ({"how": "row", "kind": "ratio", "cols": ["a", "b"], "name": "r"}, ["식 safe_divide(a, row_sum(a, b)) 이름 r_a", "식 safe_divide(b, row_sum(a, b)) 이름 r_b"]),
    "dominant": ({"how": "row", "kind": "dominant", "cols": ["a", "b"], "name": "d"}, ["식 safe_divide(row_max(a, b), row_sum(a, b)) 이름 d", "식 row_argmax(a, b) 이름 d_컬럼"]),
    "dist": ({"how": "row", "kind": "row_dist", "cols": ["a", "b"], "consts": "1.5, 2", "others": [], "name": "dd"}, ["식 row_dist(a, b, 1.5, 2) 이름 dd"]),
    "cosine": ({"how": "row", "kind": "row_cosine", "cols": ["a", "b"], "consts": "", "others": ["c", "x"], "name": "cs"}, ["식 row_cosine(a, b, c, x) 이름 cs"]),
    "zero": ({"how": "flag", "kind": "zero", "col": "x", "name": "f"}, ['전처리 flags x {"zero":true}']),
    "top": ({"how": "flag", "kind": "top", "col": "x", "tops": "", "name": "f"}, ['전처리 flags x {"top":true}']),
    "cmp": ({"how": "flag", "kind": "cmp", "col": "x", "op": ">=", "value": "60", "name": "f"}, ['전처리 flags x {"cmp":[">=",60]}']),
    "isin": ({"how": "flag", "kind": "isin", "col": "s", "values": "KR, US", "text": True, "name": "f"}, ['전처리 flags s {"isin":["KR","US"]}']),
    "outlier": ({"how": "flag", "kind": "outlier", "col": "x", "by": "mad", "k": "", "name": "f"}, ['전처리 flags x {"outlier":{"by":"mad"}}']),
    "above_group": ({"how": "flag", "kind": "above", "col": "x", "stat": "mean", "groups": ["g", "", ""], "name": "f2"},
                    ["그룹집계 mean(x) 그룹 g 이름 f2_기준", "식 iif(x > f2_기준, 1, 0) 이름 f2"]),
    "combo_and": ({"how": "flag", "kind": "combo", "join": "and", "name": "heavy",
                   "rules": [{"col": "a", "op": ">=", "value": "60", "not": False, "text": False}, {"col": "b", "op": "=", "value": "0", "not": False, "text": False}]},
                  ["식 iif((a >= 60) and (b == 0), 1, 0) 이름 heavy"]),
    "combo_xor": ({"how": "flag", "kind": "combo", "join": "xor", "name": "xo",
                   "rules": [{"col": "a", "op": ">", "value": "1", "not": False, "text": False}, {"col": "b", "op": "<", "value": "2", "not": True, "text": False}]},
                  ["식 iif((iif((a > 1), 1, 0) + iif(not (b < 2), 1, 0)) % 2 == 1, 1, 0) 이름 xo"]),
    "change": ({"how": "flag", "kind": "change", "col": "grade", "order": "t", "dir": "asc", "group": "u", "name": "ch"},
               ["lag grade 1 기준 t 그룹 u 이름 ch_prev", "식 iif(isnull(ch_prev), 0, iif(grade != ch_prev, 1, 0)) 이름 ch"]),
    "last": ({"how": "flag", "kind": "last", "col": "", "order": "t", "dir": "asc", "group": "u", "name": "ls"}, ["row_number 기준 t desc 그룹 u 이름 ls_n", "식 iif(ls_n == 1, 1, 0) 이름 ls"]),
    "bin_q": ({"how": "bin", "kind": "quantile", "col": "x", "n": "10", "closed": "left", "label": "range", "name": "b"}, ["식 x 이름 b", '전처리 cut b {"how":"quantile","n":10}']),
    "bin_floor": ({"how": "bin", "kind": "floor", "col": "x", "n": "", "width": "10", "closed": "right", "label": "index", "name": "age"},
                  ["식 x 이름 age", '전처리 cut age {"how":"floor","width":10,"closed":"right","label":"index"}']),
    "bin_custom": ({"how": "bin", "kind": "custom", "col": "a", "bounds": "0, 50, 100", "closed": "left", "label": "mid", "name": "bc"},
                   ["식 a 이름 bc", '전처리 bins bc {"edges":[0,50,100],"label":"mid"}']),
    "bin_best": ({"how": "bin", "kind": "best", "col": "x", "n": "", "target": "y", "method": "chi", "closed": "left", "label": "range", "name": "bb"},
                 ["식 x 이름 bb", '전처리 cut bb {"how":"best","target":"y","method":"chi"}']),
    "robust": ({"how": "stat", "kind": "robust", "col": "x", "groups": [], "name": "r"}, ["식 (x - median(x)) / (quantile(x, 0.75) - quantile(x, 0.25)) 이름 r"]),
    "log10": ({"how": "stat", "kind": "log10", "col": "x", "groups": [], "name": "l"}, ["식 log10(x + 1) 이름 l"]),
    "minmax100": ({"how": "stat", "kind": "minmax100", "col": "x", "groups": [], "name": "mm"}, ["식 (x - min(x)) / (max(x) - min(x)) * 100 이름 mm"]),
    "boxcox": ({"how": "stat", "kind": "boxcox", "col": "x", "groups": [], "lam": "", "name": "bx"}, ["식 x 이름 bx", '전처리 power bx {"how":"boxcox"}']),
    "winsor": ({"how": "stat", "kind": "winsor", "col": "x", "groups": [], "p_low": "1", "p_high": "", "name": "w2"}, ["식 x 이름 w2", '전처리 outlier w2 {"how":"clip","by":"pct","p_low":1}']),
    "pareto": ({"how": "encode", "kind": "others_pareto", "col": "s", "cum": "90", "label": "", "name": "o"}, ["식 s 이름 o", '전처리 others o {"how":"pareto","label":"기타","cum":90}']),
    "freq": ({"how": "encode", "kind": "freq_pct", "col": "s", "name": ""}, ['전처리 freq s {"how":"pct"}']),
    "target": ({"how": "encode", "kind": "target", "col": "s", "target": "y", "smooth": "", "name": ""}, ['전처리 target s {"target":"y"}']),
    "ordinal": ({"how": "encode", "kind": "ordinal", "col": "s", "map": "하=1, 중=2, 상=3", "else": "", "name": ""}, ['전처리 ordinal s {"map":{"하":1,"중":2,"상":3}}']),
    "onehot_top": ({"how": "encode", "kind": "onehot_top", "col": "s", "k": "3", "others": False, "name": ""}, ['전처리 onehot s {"top":3}']),
    "onehot_others": ({"how": "encode", "kind": "onehot_top", "col": "s", "k": "3", "others": True, "name": ""}, ['전처리 onehot s {"top":3,"others":true}']),
}
BAD6 = {
    "one_col": ({"how": "row", "kind": "row_sum", "cols": ["a"], "name": "x1"}, "2개 이상"),
    "weights": ({"how": "row", "kind": "weighted", "cols": ["a", "b"], "weights": "1", "name": "x2"}, "가중치는"),
    "combo1": ({"how": "flag", "kind": "combo", "join": "and", "rules": [{"col": "a", "op": ">", "value": "1"}], "name": "x3"}, "둘 이상"),
    "floor": ({"how": "bin", "kind": "floor", "col": "x", "width": "", "name": "x4"}, "단위 폭"),
    "ordinal": ({"how": "encode", "kind": "ordinal", "col": "s", "map": "하1", "name": "x5"}, "값=번호"),
    "power": ({"how": "stat", "kind": "power", "col": "x", "groups": [], "p": "0", "name": "x6"}, "0이 아닌"),
}


def lines_of(tmp_path, forms):
    return vm_run(tmp_path, ["newcol.js"], f"(() => {{ const F = {json.dumps(forms, ensure_ascii=False)}, out = {{}};"
                  " for (const k in F) try { out[k] = NewCol.lines(F[k]); } catch (e) { out[k] = 'ERR ' + e.message; } return out; })()")


@needs_node
def test_newcol_lines_phase6(tmp_path):
    got = lines_of(tmp_path, {k: v[0] for k, v in FORMS6.items()})
    for k, (_, want) in FORMS6.items():
        assert got[k] == want, k
    bad = lines_of(tmp_path, {k: v[0] for k, v in BAD6.items()})
    for k, (_, word) in BAD6.items():
        assert isinstance(bad[k], str) and bad[k].startswith("ERR") and word in bad[k], k


@needs_node
def test_newcol_phase6_lines_run_on_duckdb(tmp_path):
    from core import calc
    keys = ["row_mean2", "combo_and", "combo_xor", "change", "last", "zero", "bin_custom"]
    forms = {k: FORMS6[k][0] for k in keys if k in FORMS6}
    forms["row_mean2"] = {"how": "row", "kind": "row_mean", "cols": ["a", "b"], "name": "m"}
    lines = lines_of(tmp_path, forms)
    t = ibis.memtable({"id": [0, 1, 2, 3], "a": [70.0, 10.0, 80.0, 5.0], "b": [0.0, 0.0, 3.0, 1.0], "u": ["p", "p", "p", "q"], "t": [1, 2, 3, 1],
                       "grade": ["A", "A", "B", "C"], "x": [0.0, 1.0, 0.0, 2.0]})
    out = calc.apply([line for k in keys for line in lines[k]], t).order_by("id").to_pyarrow().to_pydict()
    assert out["m"] == [35.0, 5.0, 41.5, 3.0]
    assert out["heavy"] == [1, 0, 0, 0] and out["xo"] == [1, 1, 0, 1]
    assert out["ch"] == [0, 0, 1, 0] and out["ls"] == [0, 0, 1, 1]
    assert out["x_is_zero"] == [1, 0, 1, 0] and out["bc"] == [75.0, 25.0, 75.0, 25.0]


VALUES = [
    ("flags", {"zero": True, "cmp_op": ">=", "cmp": "60", "top": True, "top_pct": "", "above": "", "low": "", "high": "", "isin": "", "out_by": "mad", "out_k": ""}, "num",
     {"zero": True, "cmp": [">=", 60], "top": True, "outlier": {"by": "mad"}}),
    ("flags", {"zero": False, "cmp_op": "", "top": False, "above": "", "low": "", "high": "", "isin": "KR, US", "out_by": ""}, "str", {"isin": ["KR", "US"]}),
    ("flags", {"zero": False, "cmp_op": "", "top": False, "above": "", "low": "", "high": "", "isin": "", "out_by": ""}, "num", None),
    ("gfill", {"how": "median", "g1": "g", "g2": "", "g3": ""}, "num", {"how": "median", "group": ["g"]}),
    ("power", {"how": "power", "p": "0", "lambda": ""}, "num", None),
    ("power", {"how": "yeojohnson", "p": "", "lambda": ""}, "num", {"how": "yeojohnson"}),
    ("cut", {"how": "floor", "n": "", "width": "10", "tail": "", "target": "", "method": "iv", "closed": "right", "label": "range"}, "num", {"how": "floor", "width": 10, "closed": "right"}),
    ("others", {"how": "pareto", "k": "", "share": "", "cum": "90", "label": ""}, "str", {"how": "pareto", "label": "기타", "cum": 90}),
    ("freq", {"how": "pct"}, "str", {"how": "pct"}),
    ("target", {"target": "y", "smooth": ""}, "str", {"target": "y"}),
    ("ordinal", {"map": "하=1, 상=3", "else": ""}, "str", {"map": {"하": 1, "상": 3}}),
    ("onehot", {"top": ""}, "str", True),
    ("onehot", {"top": "3"}, "str", {"top": 3}),
    ("onehot", {"top": "3", "others": True}, "str", {"top": 3, "others": True}),
    ("onehot", {"top": "", "others": True}, "str", None),
    ("outlier", {"how": "clip", "by": "pct", "low": "", "high": "", "k": "", "p_low": "", "p_high": "95"}, "num", {"how": "clip", "by": "pct", "p_high": 95}),
    ("bins", {"text": "0, 10", "closed": "left", "label": "index"}, "num", {"edges": [0, 10], "label": "index"}),
    ("bins", {"text": "0, 10", "closed": "left", "label": "range"}, "num", [0, 10]),
]


@needs_node
def test_prepmenu_values(tmp_path):
    got = vm_run(tmp_path, ["prepmenu.js"], f"(() => {json.dumps([v[:3] for v in VALUES], ensure_ascii=False)}.map(([id, s, kind]) => PrepMenu.value(id, s, kind)))()")
    assert got == [v[3] for v in VALUES]
