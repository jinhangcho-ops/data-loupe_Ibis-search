"""Phase 13 중복 제거 개선(prep_expr): 글자 다듬어 비교·문장 포함 묶음(포함 컬럼·상한)·겹친 행 예시. 데이터는 시험 안에서 지어내고 DuckDB 엔진으로 돈다(plans/phase-13.md)."""
from collections import Counter

import polars as pl
import pytest
from lazy import ibis

from prep import prep_expr

# 다듬으면 ab · abc · abcxyz · xyz · qq · "" · "" — ab ⊂ abc ⊂ abcxyz ⊃ xyz는 한 묶음(xyz와 ab는 abcxyz를 거쳐서만 이어진다). 서로 다른 다듬은 값 6개
CHAIN = {"t": ["ab", "ABC!", "abc xyz", "xyz", "qq", "!!!", "?"], "n": [1, 2, 3, 4, 5, 6, 7]}
L0 = prep_expr.LOOSE + "0"
BOTH = {"dedupe_loose": True, "dedupe_contain": ["t"]}


def mem(data):
    return ibis.memtable(pl.DataFrame(data))


def built(data, recipe):
    expr, _ = prep_expr.build(ibis.duckdb.connect(), {"base": "x", **recipe}, mem(data))
    return expr


def by_n(expr):
    return sorted(expr.to_pyarrow().to_pylist(), key=lambda r: r["n"])


def loose_chain():
    t = mem(CHAIN)
    return t.mutate(**{L0: t.t.re_replace(prep_expr.LOOSE_PATTERN, "").lower()})


def groups_by_n(t):
    return {r["n"]: r[prep_expr.GROUP] for r in t.to_pyarrow().to_pylist()}


# ---- dedupe_options ----

def test_dedupe_options():
    assert prep_expr.dedupe_options({}) == (False, [], None)
    assert prep_expr.dedupe_options({"dedupe_loose": None, "dedupe_contain": None, "dedupe_limit": None}) == (False, [], None)
    assert prep_expr.dedupe_options({"dedupe_loose": True}) == (True, [], None)
    assert prep_expr.dedupe_options({"dedupe_contain": ["t", "u"]}) == (False, ["t", "u"], None)  # 포함은 다듬기를 켜지 않는다
    assert prep_expr.dedupe_options({"dedupe_contain": [], "dedupe_limit": 5}) == (False, [], 5)
    for bad in ({"dedupe_loose": "yes"}, {"dedupe_contain": True}, {"dedupe_contain": "t"}, {"dedupe_contain": [1]}, {"dedupe_contain": ["t", "t"]},
                {"dedupe_limit": 0}, {"dedupe_limit": -1}, {"dedupe_limit": 1.5}, {"dedupe_limit": True}, {"dedupe_limit": "5"}):
        with pytest.raises(ValueError):
            prep_expr.dedupe_options(bad)


# ---- loose_text ----

def test_loose_text():
    con = ibis.duckdb.connect()
    for raw, want in ((" Hello, World!? ", "helloworld"), ("안녕 하세요~!", "안녕하세요"), ("A-1 / b_2", "a1b2"), ("!!!", "")):
        assert con.execute(prep_expr.loose_text(ibis.literal(raw))) == want, raw
    assert con.to_pyarrow(prep_expr.loose_text(ibis.literal(None, type="string"))).as_py() is None  # execute는 빈 글자 값을 nan으로 준다


# ---- text_contains ----

def test_text_contains():
    assert prep_expr.text_contains("hello world", "world") is True
    assert prep_expr.text_contains("abc", "abc") is True
    assert prep_expr.text_contains("ab", "abc") is False
    assert prep_expr.text_contains(None, "a") is False
    assert prep_expr.text_contains("a", None) is False


# ---- contain_groups ----

def test_contain_groups_chain():
    out = prep_expr.contain_groups(loose_chain(), [L0], [L0], None)
    assert prep_expr.GROUP in out.columns and {"t", "n", L0} <= set(out.columns)
    got = groups_by_n(out)
    assert got[1] == got[2] == got[3] == got[4]  # 이어진 포함 관계는 한 묶음
    assert got[6] == got[7]  # 다 지워진 빈 글자끼리는 같은 묶음
    assert len({got[1], got[5], got[6]}) == 3  # 빈 글자는 모든 글자에 들어 있어도 다른 묶음과 섞이지 않는다


def test_contain_groups_round_limit(monkeypatch):
    monkeypatch.setattr(prep_expr, "CONTAIN_ROUNDS", 2)  # 위 데이터는 셋째 라운드에서야 바뀐 번호가 없다
    with pytest.raises(ValueError, match="2번"):
        prep_expr.contain_groups(loose_chain(), [L0], [L0], None).to_pyarrow()
    monkeypatch.setattr(prep_expr, "CONTAIN_ROUNDS", 3)
    assert prep_expr.contain_groups(loose_chain(), [L0], [L0], None).count().to_pyarrow().as_py() == len(CHAIN["n"])


def test_contain_groups_limit():
    with pytest.raises(ValueError, match="6개.*상한 5개"):  # 서로 다른 다듬은 값 6개
        prep_expr.contain_groups(loose_chain(), [L0], [L0], 5)
    assert prep_expr.contain_groups(loose_chain(), [L0], [L0], 6).count().to_pyarrow().as_py() == len(CHAIN["n"])


def test_contain_other_keys_equal():
    t = mem({"t": ["ab", "abc", "ab", "abc"], "g": [1, 1, 2, 3], "n": [1, 2, 3, 4]})
    got = groups_by_n(prep_expr.contain_groups(t, ["t", "g"], ["t"], None))
    assert got[1] == got[2]  # 같은 g 안에서만 포함으로 묶인다
    assert len({got[1], got[3], got[4]}) == 3


def test_contain_groups_two_texts_same_direction():
    t = mem({"a": ["ab", "abc", "abq"], "b": ["xy", "x", "xyw"], "n": [1, 2, 3]})
    got = groups_by_n(prep_expr.contain_groups(t, ["a", "b"], ["a", "b"], None))
    assert got[1] == got[3]  # (ab, xy)는 (abq, xyw)의 두 칸에 모두 들어 있다
    assert got[2] != got[1]  # (ab, xy)와 (abc, x)는 칸마다 방향이 달라 묶지 않는다


# ---- dedupe_keys ----

def test_dedupe_keys_plain_is_identity():
    t = mem({"a": [1, 1], "b": ["x", "y"]})
    out, keys = prep_expr.dedupe_keys(t, ["a"], {})
    assert out is t and keys == ["a"]


def test_dedupe_keys_loose():
    t = mem({"name": ["Apple!", "apple", "Banana"], "n": [1, 1, 2]})
    out, keys = prep_expr.dedupe_keys(t, ["name", "n"], {"dedupe_loose": True})
    assert keys == [L0, "n"]
    assert list(out.columns) == ["name", "n", L0]
    assert {(r["name"], r[L0]) for r in out.to_pyarrow().to_pylist()} == {("Apple!", "apple"), ("apple", "apple"), ("Banana", "banana")}


def test_dedupe_keys_contain():
    out, keys = prep_expr.dedupe_keys(mem(CHAIN), ["t"], BOTH)
    assert keys == [prep_expr.GROUP]
    assert list(out.columns) == ["t", "n", prep_expr.GROUP]  # 숨은 다듬은 컬럼은 빠진다
    got = groups_by_n(out)
    assert got[1] == got[2] == got[3] == got[4] and got[6] == got[7]


def test_dedupe_keys_contain_raw_without_loose():
    out, keys = prep_expr.dedupe_keys(mem(CHAIN), ["t"], {"dedupe_contain": ["t"]})  # 다듬기를 끄면 원문으로 포함을 본다
    assert keys == [prep_expr.GROUP] and list(out.columns) == ["t", "n", prep_expr.GROUP]
    got = groups_by_n(out)
    assert got[1] == got[3] == got[4]  # ab ⊂ "abc xyz" ⊃ xyz
    assert got[2] != got[1]  # "ABC!"는 원문으로는 어디에도 들어 있지 않다


def test_dedupe_keys_contain_checks():
    t = mem({"t": ["a", "b"], "n": [1, 2]})
    with pytest.raises(ValueError, match="글자 컬럼"):
        prep_expr.dedupe_keys(t, ["t", "n"], {"dedupe_contain": ["n"]})
    with pytest.raises(ValueError, match="중복 기준 컬럼"):
        prep_expr.dedupe_keys(t, ["n"], {"dedupe_contain": ["t"]})


# ---- dedupe_fits ----

def test_dedupe_fits_duckdb_and_polars():
    t = mem({"a": ["x"]})
    assert prep_expr.dedupe_fits(t, {}) is True
    assert prep_expr.dedupe_fits(t, {"dedupe_loose": True}) is True
    assert prep_expr.dedupe_fits(t, {"dedupe_contain": ["a"]}) is True
    pcon = ibis.polars.connect()
    pt = pcon.create_table("x", pl.DataFrame({"a": ["x"]}))
    assert prep_expr.dedupe_fits(pt, {}) is True
    assert prep_expr.dedupe_fits(pt, {"dedupe_contain": ["a"]}) is False  # Polars는 UDF 조건 자기 조인(같음 조건만 됨)을 못 한다 → 이 PC 사본


# ---- dupe_examples ----

def test_dupe_examples_limit():
    t = mem({"k": [i // 2 for i in range(60)], "v": list(range(60))})  # 2행짜리 묶음 30개
    df, nums = prep_expr.dupe_examples(t, ["k"], ["k", "v"])
    assert df.columns == ["k", "v"] and df.height == prep_expr.DUPE_EXAMPLES
    assert df["v"].to_list() == list(range(prep_expr.DUPE_EXAMPLES))
    assert nums == [i // 2 + 1 for i in range(prep_expr.DUPE_EXAMPLES)]  # 묶음 번호는 1부터


def test_dupe_examples_skips_single_rows():
    t = mem({"k": [1, 2, 2, 3], "v": [10, 20, 21, 30]})
    df, nums = prep_expr.dupe_examples(t, ["k"], ["v"])
    assert df["v"].to_list() == [20, 21] and nums == [1, 1]


# ---- dedupe_rows (수정) ----

def test_dedupe_rows_loose_first():
    data = {"name": ["Apple!", "apple", "Banana", "banana  "], "n": [2, 1, 3, 4]}
    out = built(data, {"dedupe": ["name"], "dedupe_loose": True, "dedupe_keep": {"how": "first", "order": ["n"]}})
    assert list(out.columns) == ["name", "n"]  # 숨은 비교 컬럼은 결과에 없다
    assert by_n(out) == [{"name": "apple", "n": 1}, {"name": "Banana", "n": 3}]  # 원래 값이 남는다


def test_dedupe_rows_contain_last():
    out = built(CHAIN, {"dedupe": ["t"], **BOTH, "dedupe_limit": 100, "dedupe_keep": {"how": "last", "order": ["n"]}})
    assert list(out.columns) == ["t", "n"]
    assert [r["n"] for r in by_n(out)] == [4, 5, 7]


def test_dedupe_rows_loose_merge():
    out = built({"name": ["Apple!", "apple", "Banana"], "n": [2, 1, 3]}, {"dedupe": ["name"], "dedupe_loose": True, "dedupe_keep": "max"})
    assert by_n(out) == [{"name": "apple", "n": 2}, {"name": "Banana", "n": 3}]  # 기준 컬럼도 합치기 대상(max)


def test_old_recipe_same_result():
    """옵션 없는 recipe는 예전과 같다(요구 5)."""
    data = {"name": ["a", "a", "A", "b"], "n": [1, 2, 3, 4]}
    out = built(data, {"dedupe": ["name"], "dedupe_keep": {"how": "first", "order": ["n"]}})
    assert by_n(out) == [{"name": "a", "n": 1}, {"name": "A", "n": 3}, {"name": "b", "n": 4}]
    assert built(data, {"dedupe": ["name", "n"]}).count().to_pyarrow().as_py() == 4  # 모든 컬럼이 기준 = DISTINCT


# ---- dupes (수정) ----

def test_dupes_counts_and_examples():
    out = prep_expr.dupes(ibis.duckdb.connect(), {"base": "x", "dedupe": ["name"], "dedupe_loose": True},
                          mem({"name": ["Apple!", "apple", "Banana", "x"], "n": [1, 2, 3, 4]}))
    assert (out["groups"], out["rows"]) == (1, 1)
    assert out["examples"].columns == ["name", "n"] and sorted(out["examples"]["name"].to_list()) == ["Apple!", "apple"]
    assert out["example_groups"] == [1, 1]


def test_dupes_all_columns_when_empty():
    out = prep_expr.dupes(ibis.duckdb.connect(), {"base": "x"}, mem({"a": [1, 1, 2], "b": ["x", "x", "y"]}))
    assert (out["groups"], out["rows"]) == (1, 1) and out["example_groups"] == [1, 1]


def test_dupes_contain_counts():
    out = prep_expr.dupes(ibis.duckdb.connect(), {"base": "x", "dedupe": ["t"], **BOTH}, mem(CHAIN))
    assert (out["groups"], out["rows"]) == (2, 4)  # {ab, abc, abcxyz, xyz}에서 3행, {"", ""}에서 1행
    assert sorted(Counter(out["example_groups"]).values()) == [2, 4]
    assert out["examples"].columns == ["t", "n"]
