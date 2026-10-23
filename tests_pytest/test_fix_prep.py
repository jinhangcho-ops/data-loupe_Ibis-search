"""묶음 2(전처리) 리뷰 지적별 시험. 값은 손으로 계산했다."""
import decimal
import sqlite3

import pyarrow as pa
import pytest

from lazy import ibis

from prep import prep_expr


def build(data, columns, **more):
    """data(dict)를 메모리 표로 만들어 recipe(columns, …)를 적용한 결과를 파이썬 dict로."""
    t = ibis.memtable(pa.table(data))
    expr, _ = prep_expr.build(None, {"base": "t", "columns": columns, **more}, t)
    return expr.to_pyarrow().to_pydict(), expr


def test_retype_to_string_only_casts():
    import datetime as dt
    out, expr = build({"a": ["abc", " 1 ", None], "d": [dt.date(2026, 1, 5), None, dt.date(2026, 2, 1)], "f": [1.5, 2.0, None]},
                      {c: {"ops": {"type": {"to": "string"}}} for c in "adf"})
    assert out["a"] == ["abc", " 1 ", None]  # 글자는 그대로(숫자 모양이 아니라고 빈 값이 되지 않고, 공백도 지우지 않는다)
    assert out["d"] == ["2026-01-05", None, "2026-02-01"]
    assert out["f"] == ["1.5", "2.0", None]
    assert all(expr.schema()[c].is_string() for c in "adf")


def test_outlier_iqr_on_decimal_column():
    vals = [decimal.Decimal(v) for v in ("1.0", "2.0", "3.0", "4.0", "100.0")]
    out, _ = build({"a": pa.array(vals, pa.decimal128(10, 1))}, {"a": {"ops": {"outlier": {"how": "drop", "by": "iqr"}}}})
    # 사분위 2.0·4.0, 간격 3.0 → 범위 -1..7. 100은 빠진다.
    assert [float(v) for v in out["a"]] == [1.0, 2.0, 3.0, 4.0]


def test_ln_log_sqrt_empty_for_out_of_domain():
    from core import sql_funcs
    t = ibis.memtable(pa.table({"x": [-1.0, 0.0, 1.0, 100.0, None]}))
    cols = {"ln": sql_funcs.get("ln"), "log10": sql_funcs.get("log10"), "sqrt": sql_funcs.get("sqrt")}
    got = {n: t.select(v=f["build"](sql_funcs.resolve(f, t, {"x": t.x}))).v.to_pyarrow().to_pylist() for n, f in cols.items()}
    assert got["ln"] == [None, None, 0.0, pytest.approx(4.605170185988092), None]
    assert got["log10"] == [None, None, 0.0, 2.0, None]
    assert got["sqrt"] == [None, 0.0, 1.0, 10.0, None]  # 제곱근은 0이 0
    f = sql_funcs.get("log")
    log2 = t.select(v=f["build"](sql_funcs.resolve(f, t, {"x": t.x, "base": 2}))).v.to_pyarrow().to_pylist()
    assert log2 == [None, None, 0.0, pytest.approx(6.643856189774724), None]


def test_formula_ln_sqrt_use_same_guard():
    from core import prep_derive
    t = ibis.memtable(pa.table({"x": [-4.0, 0.0, 4.0]}))
    t2, _ = prep_derive.apply_formulas(t, [{"name": "a", "expr": "ln(x)"}, {"name": "b", "expr": "sqrt(x)"}, {"name": "c", "expr": "log10(x)"}])
    out = t2.to_pyarrow().to_pydict()
    assert out["a"] == [None, None, pytest.approx(1.3862943611198906)]
    assert out["b"] == [None, 0.0, 2.0]
    assert out["c"] == [None, None, pytest.approx(0.6020599913279624)]


def test_prune_with_column_named_count():
    from prep import prep_profile
    n = 100
    arrow = pa.table({"count": [1] * 99 + [2], "other": list(range(n))})
    found = prep_profile.prune(arrow)
    assert [f["column"] for f in found] == ["count"]  # 한 값이 99%
    assert "99.0%" in found[0]["reason"]


def test_downcast_uint64_beyond_int64_is_skipped():
    big = 2**63 + 5
    out, expr = build({"a": pa.array([1, big], pa.uint64()), "b": pa.array([1, 2], pa.int64())}, {}, downcast=True)
    assert out["a"] == [1, big]
    assert str(expr.schema()["a"]) == "uint64"
    assert str(expr.schema()["b"]) == "int8"


def test_fill_prev_next_fixed_by_ties():
    """기준 값이 같은 행들의 순서가 물리 순서에 따라 달라지지 않는다."""
    rows = [(1, 10.0, 1), (1, None, 2), (1, None, 3), (2, None, 4), (2, 7.0, 5)]

    def run(order, how):
        r = [rows[i] for i in order]
        data = {"by": [x[0] for x in r], "v": [x[1] for x in r], "id": [x[2] for x in r]}
        out, _ = build(data, {"v": {"ops": {"fill": {"how": how, "by": "by"}}}})
        return dict(zip(out["id"], out["v"]))

    for how in ("prev", "next"):
        a, b = run([0, 1, 2, 3, 4], how), run([4, 3, 2, 1, 0], how)
        assert a == b


def test_pca_offset_data_is_stable():
    from core import prep_derive
    base = 1e9
    t = ibis.memtable(pa.table({"x": [base + i for i in range(4)], "y": [base + 2 * i for i in range(4)]}))
    info = {}
    prep_derive.pca(t, {"columns": ["x", "y"], "n": 1}, info)
    # 분산 1.25·5, 공분산 2.5 → 상관 1, 고유값 2·0 → 첫 성분이 분산의 100%
    assert info["pca"]["ratio"][0] == pytest.approx(1.0, abs=1e-9)


# ---- 원격 사본 용량 확인과 없는 날짜 ----

@pytest.fixture
def remote(folder, tmp_path):
    from core import server
    from query.search_app import app
    from prep import prep_app  # noqa: F401
    raw = sqlite3.connect(tmp_path / "s.db")
    raw.execute("create table t (id integer, d text, v real)")
    raw.executemany("insert into t values (?, ?, ?)", [(1, "2026-02-30", 1.0), (2, "2026-01-05", 2.0), (3, "2026-01-06", 2.0)])
    raw.commit()
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "url", "url": f"sqlite://{tmp_path / 's.db'}"}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def test_new_local_copy_goes_through_scan_gate(remote, monkeypatch):
    from core import held
    from core import server
    seen = []

    def gate(body, make_sql, rows=None):
        seen.append(make_sql())
        return server.jsonify(needs_confirm=True, text="큼")

    monkeypatch.setattr(server, "scan_gate", gate)
    base = {"base": "t"}
    for url, body in (("/api/preview", base), ("/api/dupes", {**base, "dedupe": ["v"]}), ("/api/analyze", {"recipe": base, "how": {}, "full": True})):
        seen.clear()
        r = remote.post(url, json=body)
        assert r.get_json().get("needs_confirm") is True and len(seen) == 1, url
        assert "FROM" in seen[0][0].upper()
    seen.clear()  # 표본으로 하는 분석은 사본을 받지 않으므로 묻지 않는다.
    remote.post("/api/analyze", json={"recipe": base, "how": {}, "full": False})
    assert seen == []
    held.store["items"].clear()


def test_missing_date_gets_friendly_message(remote):
    r = remote.post("/api/preview", json={"base": "t", "columns": {"d": {"ops": {"type": {"to": "date"}}}}})
    assert r.status_code == 400
    assert "날짜로 바꿀 수 없는 값이 있어요" in r.get_json()["error"]
