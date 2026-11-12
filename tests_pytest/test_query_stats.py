"""결정 1(plans/phase-1.md): 쿼리 계산 줄의 통계 단계(fill mean·median·mode, outlier iqr·std, scale, label, onehot, rare, 글자 expand)를
연결 스레드에서 미리 구해 캐시하고 prep_line에 상수로 넣는다. 데이터는 시험 안에서 지어낸 임시 DuckDB·SQLite·CSV·memtable."""
import datetime as dt
import math
import re
import sqlite3
import types
from pathlib import Path

import duckdb
import pytest

from lazy import ibis
from core import calc
from core import prep_ops
from core import querying
from core import server

ROOT = Path(__file__).resolve().parent.parent


def body(**extra):
    return {"schema": "", "base": "orders", "joins": [], "periods": [], "conditions": [], "calcs": [], "sorts": [], "columns": [], **extra}


def rows(client, **extra):
    r = client.post("/api/query", json=body(limit=100, **extra)).get_json()
    assert "error" not in r, r.get("error")
    return r["rows"]["rows"]


def connect(c, payload):
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json=payload).status_code == 200
    return c


@pytest.fixture
def shop(folder, tmp_path, monkeypatch):
    from query.search_app import app
    monkeypatch.setattr(querying, "PINS", {})
    raw = duckdb.connect(str(tmp_path / "s.duckdb"))
    raw.execute('create table orders ("주문일" date, "채널" varchar, "금액" double, "고객" varchar, "메모" varchar)')
    raw.executemany("insert into orders values (?, ?, ?, ?, ?)",
                    [(dt.date(2025, 1, 1) + dt.timedelta(days=i), "앱웹"[i % 2], 1000.0 + i, f"c{i % 5}", '{"a": %d, "b": "x"}' % i) for i in range(60)])
    raw.close()
    c = connect(app.test_client(), {"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "s.duckdb")})
    yield c
    c.post("/api/disconnect", json={})


@pytest.fixture
def lite(folder, tmp_path, monkeypatch):
    from query.search_app import app
    monkeypatch.setattr(querying, "PINS", {})
    raw = sqlite3.connect(tmp_path / "s.db")
    raw.execute("create table e (u text, s text, n real)")
    raw.executemany("insert into e values (?, ?, ?)", [("a", "x", 1.0), ("a", None, None), ("b", "y", 3.0)])
    raw.execute("create table o (n real, s text)")
    raw.executemany("insert into o values (?, ?)", [(1.0, "a"), (2.0, "a"), (3.0, "b"), (4.0, "b"), (100.0, "c")])
    raw.commit()
    raw.close()
    c = connect(app.test_client(), {"kind": "url", "url": f"sqlite://{tmp_path / 's.db'}"})
    yield c
    c.post("/api/disconnect", json={})


@pytest.fixture
def csvs(folder, tmp_path, monkeypatch):
    from query.search_app import app
    monkeypatch.setattr(querying, "PINS", {})
    (tmp_path / "orders.csv").write_text("id,cust,amt\n" + "".join(f"o{i},c{i % 3},{i + 1}\n" for i in range(9)), encoding="utf-8")
    c = connect(app.test_client(), {"kind": "folder", "path": str(tmp_path), "files": [str(tmp_path / "orders.csv")]})
    assert server.state["con"].name == "polars"
    yield c
    c.post("/api/disconnect", json={})


# ---------- prep_ops.known_stats ----------

def test_known_stats_values():
    t = ibis.memtable({"n": [1.0, None, 3.0], "s": ["b", "a", None], "m": ["x", "x", "y"], "j": ['{"a": 1}', '{"b": 2}', None]})
    assert prep_ops.known_stats(t, "n", "fill", {"how": "mean"}) == 2.0
    assert prep_ops.known_stats(t, "m", "fill", {"how": "mode"}) == "x"
    assert prep_ops.known_stats(t, "n", "scale", "standard") == {"a": 2.0, "b": pytest.approx(math.sqrt(2))}
    assert prep_ops.known_stats(t, "n", "scale", "minmax") == {"a": 1.0, "b": 3.0}
    assert prep_ops.known_stats(t, "n", "scale", {"how": "log"}) == {"a": 1.0}
    assert prep_ops.known_stats(t, "s", "label", True) == ["a", "b"]
    assert prep_ops.known_stats(t, "s", "onehot", True) == ["a", "b"]
    assert prep_ops.known_stats(t, "m", "rare", {"min": 2}) == ["y"]
    assert prep_ops.known_stats(t, "j", "expand", True) == ["a", "b"]
    bins = ibis.memtable({"g": ["10–20", "5–10", "10–20"]})
    assert prep_ops.known_stats(bins, "g", "label", True) == ["5–10", "10–20"]  # 구간 글자는 시작 숫자 순


def test_known_stats_outlier_bounds():
    t = ibis.memtable({"n": [1.0, 2.0, 3.0, 4.0, 100.0]})
    q = prep_ops.stats(t, a=t.n.quantile(0.25), b=t.n.quantile(0.75))
    gap = 1.5 * (q["b"] - q["a"])
    assert prep_ops.known_stats(t, "n", "outlier", {"how": "clip", "by": "iqr"}) == {"low": pytest.approx(q["a"] - gap), "high": pytest.approx(q["b"] + gap)}
    s = prep_ops.stats(t, a=t.n.mean(), b=t.n.std())
    assert prep_ops.known_stats(t, "n", "outlier", {"how": "drop", "by": "std", "k": 2}) == {"low": pytest.approx(s["a"] - 2 * s["b"]), "high": pytest.approx(s["a"] + 2 * s["b"])}


def test_known_stats_errors():
    t = ibis.memtable({"n": [1.0, 1.0], "s": ["a", "b"]})
    with pytest.raises(ValueError, match="정수·실수"):
        prep_ops.known_stats(t, "s", "fill", {"how": "mean"})
    with pytest.raises(ValueError, match="표준편차"):
        prep_ops.known_stats(t, "n", "scale", "standard")
    with pytest.raises(ValueError, match="범위가 0"):
        prep_ops.known_stats(t, "n", "scale", "minmax")
    with pytest.raises(ValueError, match="미리 구할 통계"):
        prep_ops.known_stats(t, "s", "trim", True)
    with pytest.raises(ValueError, match="미리 구할 통계"):
        prep_ops.known_stats(t, "n", "fill", {"how": "value", "value": 0})


# ---------- prep_ops.pin_value ----------

AFTER = lambda name: pytest.mark.xfail(strict=True, reason=f"phase-1 {name}")  # noqa: E731  기존 함수 수정((수정) 절)이 끝나야 통과: 그 마지막 항목
PIN_CASES = [("n", "fill", {"how": "mean"}), ("n", "fill", {"how": "median"}),
             ("s", "fill", {"how": "mode"}), ("n", "scale", "standard"),
             ("n", "scale", "minmax"), ("n", "scale", "log"),
             ("s", "label", True), ("s", "onehot", True),
             ("s", "rare", {"min": 2, "label": "etc"}),
             ("n", "outlier", {"how": "clip", "by": "iqr"}), ("n", "outlier", {"how": "drop", "by": "std", "k": 1}), ("j", "expand", True)]  # 직접 범위·keys 꼴은 지금 OPS가 받는다


def grid():
    return ibis.memtable({"id": [1, 2, 3, 4, 5], "n": [1.0, None, 3.0, 10.0, float("nan")], "s": ["a", "a", "b", None, "a"],
                          "j": ['{"a": 1}', '{"b": 2}', None, '{"a": 3}', "1"]})


@pytest.mark.parametrize("column,op,value", PIN_CASES)
def test_pin_value_same_result(column, op, value):
    """미리 구한 값으로 건 결과는 전처리 화면처럼 그 자리에서 구한 결과와 같다(NaN은 빈 값으로 본 뒤 통계)."""
    t = grid()
    pin = prep_ops.pin_value(t, column, op, value)
    assert not calc.reads_stats(op, pin, t[column].type())
    got = prep_ops.apply_ops(t, column, {op: pin}).order_by("id").to_pyarrow().to_pylist()
    want = prep_ops.apply_ops(t, column, {op: value}).order_by("id").to_pyarrow().to_pylist()
    assert got == want


def test_pin_value_forms():
    t = grid()
    assert prep_ops.pin_value(t, "n", "fill", {"how": "mean"}) == {"how": "mean", "known": pytest.approx((1 + 3 + 10) / 3)}  # NaN은 빈 값
    assert prep_ops.pin_value(t, "n", "scale", "minmax") == {"how": "minmax", "known": {"a": 1.0, "b": 10.0}}
    assert prep_ops.pin_value(t, "s", "label", True) == {"known": ["a", "b"]}
    assert prep_ops.pin_value(t, "s", "rare", {"min": 2}) == {"min": 2, "known": ["b"]}
    assert prep_ops.pin_value(t, "j", "expand", True) == {"keys": ["a", "b"]}
    out = prep_ops.pin_value(t, "n", "outlier", {"how": "clip", "by": "iqr", "k": 1})
    assert set(out) == {"how", "by", "low", "high"} and out["by"] == "range" and out["how"] == "clip" and out["low"] <= out["high"]
    assert all(isinstance(prep_ops.pin_value(t, c, op, v), prep_ops.Known) for c, op, v in [("n", "fill", {"how": "mean"}), ("s", "label", True), ("j", "expand", True)])


def test_pin_value_checks_like_apply_ops():
    t = grid()
    with pytest.raises(ValueError, match="정수·실수 컬럼에만"):
        prep_ops.pin_value(t, "s", "scale", "standard")
    with pytest.raises(ValueError, match="형식으로 쓰세요"):
        prep_ops.pin_value(t, "s", "rare", {"min": 1})
    with pytest.raises(ValueError, match="전처리 항목"):
        prep_ops.pin_value(t, "s", "nope", True)


# ---------- calc ----------

def test_prep_parts():
    t = ibis.memtable({"판매 채널": ["a"], "n": [1.0]})
    assert calc.prep_parts('fill 판매 채널 {"how": "mean"}', t) == ("fill", "판매 채널", {"how": "mean"})
    assert calc.prep_parts("label n true", t) == ("label", "n", True)
    with pytest.raises(ValueError, match="JSON"):
        calc.prep_parts("type n to=float", t)
    with pytest.raises(ValueError):
        calc.prep_parts("trim 없는컬럼 true", t)


def test_prep_line_uses_pins():
    t = ibis.memtable({"n": [1.0, None]})
    out = calc.apply(['전처리 fill n {"how": "mean"}'], t, None, {0: prep_ops.Known({"how": "mean", "known": 9.0})})
    assert sorted(out.n.to_pyarrow().to_pylist()) == [1.0, 9.0]


def test_prep_line_unpinned_refused():
    t = ibis.memtable({"n": [1.0, None], "s": ["a", "b"]})
    for line in ('전처리 fill n {"how": "mean"}', '전처리 scale n "standard"', "전처리 label s true", "전처리 expand s true"):
        with pytest.raises(ValueError, match="통계를 먼저 구해"):
            calc.apply([line], t)
    assert list(calc.apply(['전처리 expand s {"keys": ["a"]}'], ibis.memtable({"s": ['{"a": 1}']})).columns) == ["s", "s.a"]  # 찾아 둔 키는 그대로 된다


def test_reads_stats_known():
    dtype, K = ibis.dtype("float64"), prep_ops.Known
    assert calc.reads_stats("fill", {"how": "mean"}, dtype)
    assert calc.reads_stats("fill", {"how": "mean", "known": 1.0}, dtype)  # JSON에서 온 dict의 known은 미리 구한 값이 아니다
    assert not calc.reads_stats("fill", K({"how": "mean", "known": 1.0}), dtype)
    assert not calc.reads_stats("scale", K({"how": "standard", "known": {"a": 0, "b": 1}}), dtype)
    assert not calc.reads_stats("label", K({"known": ["a"]}), ibis.dtype("string"))


# ---------- 전처리 화면 컬럼 단계의 known(결정 9) ----------

def test_recipe_ops_known_refused():
    t = ibis.memtable({"n": [1.0, None], "s": ["a", "b"]})
    for op, value in (("fill", {"how": "mean", "known": 5}), ("scale", {"how": "standard", "known": {"a": 0, "b": 1}}), ("label", {"known": ["a"]}), ("rare", {"min": 2, "known": ["a"]})):
        with pytest.raises(ValueError, match="'known'은 프로그램이"):
            prep_ops.apply_ops(t, "s" if op in ("label", "rare") else "n", {op: value})
    from prep import prep_expr
    with pytest.raises(ValueError, match="'known'은 프로그램이"):
        prep_expr.build(None, {"base": "t", "columns": {"n": {"ops": {"fill": {"how": "mean", "known": 5}}}}}, t)


# ---------- server.run ----------

def test_run_inside_run_keeps_cancel(shop):
    """연결 스레드 안에서 부른 run은 그 자리에서 돈다(취소 Event를 바꾸지 않아 밖의 run의 취소가 그대로 닿는다)."""
    def outer():
        before = server.state["cancel"]
        assert server.run(lambda: 7) == 7
        return server.state["cancel"] is before
    assert server.run(outer) is True


# ---------- querying.stats_key · pinned ----------

def test_stats_key(shop, monkeypatch):
    a, b = '전처리 fill 금액 {"how": "mean"}', '전처리 scale 금액 "standard"'
    one, two = body(calcs=[a]), body(calcs=[a, b])
    assert querying.stats_key(one, 0) == querying.stats_key(two, 0)  # 그 줄까지의 계산 줄이 같으면 같다
    assert querying.stats_key(two, 1) != querying.stats_key(two, 0)
    assert querying.stats_key(body(calcs=[a], conditions=["금액 > 1"]), 0) != querying.stats_key(one, 0)
    assert querying.stats_key(body(calcs=[a], sorts=["금액 desc"], limit=5, offset=10), 0) == querying.stats_key(one, 0)  # 정렬·쪽은 통계를 바꾸지 않는다
    plain = querying.stats_key(one, 0)
    monkeypatch.setattr(querying, "tab_bodies", lambda b: {"@x": {"base": "orders", "conditions": ["금액 > 5"]}})
    with_tab = querying.stats_key(one, 0)
    assert with_tab != plain  # 쓰는 @탭의 검색 요청이 열쇠에 들어간다
    monkeypatch.setattr(querying, "tab_bodies", lambda b: {"@x": {"base": "orders", "conditions": ["금액 > 6"]}})
    assert querying.stats_key(one, 0) != with_tab


def dry_con(dry):
    """dry면 읽는 양을 미리 잴 수 있는(dry run을 받는 client.query가 있는) 가짜 연결. DB 이름은 주지 않는다."""
    return types.SimpleNamespace(client=types.SimpleNamespace(query=lambda *a, **k: None)) if dry else types.SimpleNamespace()


def gate_stubs(monkeypatch, dry, gate):
    monkeypatch.setattr(querying, "PINS", {})
    monkeypatch.setattr(querying, "FRESHED", {})
    monkeypatch.setitem(server.state, "turn_no", 0)
    monkeypatch.setattr(querying, "get_con", lambda: dry_con(dry))
    monkeypatch.setattr(querying, "stats_key", lambda b, i: f"k{i}")
    monkeypatch.setattr(querying, "run", lambda work: work())
    monkeypatch.setattr(server, "scan_gate", gate)
    monkeypatch.setattr(server, "expr_sql", lambda con, expr: ("select 1", None))


def test_pinned_values_and_cache(monkeypatch):
    gate_stubs(monkeypatch, False, lambda *a, **k: pytest.fail("미리 잴 수 없는 연결이면 묻지 않는다"))
    t = ibis.memtable({"n": [1.0, None, 3.0], "s": ["a", "b", "a"]})
    lines = ["전처리 trim s true", '전처리 fill n {"how": "mean"}', '전처리 scale n "minmax"', "전처리 label s true"]
    assert querying.pinned(body(calcs=lines), t, ask={}) == {1: {"how": "mean", "known": 2.0}, 2: {"how": "minmax", "known": {"a": 1.0, "b": 3.0}}, 3: {"known": ["a", "b"]}}  # minmax는 채운 뒤의 표로
    calls = []
    monkeypatch.setattr(prep_ops, "known_stats", lambda *a: calls.append(a))
    querying.pinned(body(calcs=lines), t)
    assert calls == []  # 같은 열쇠는 다시 구하지 않는다
    assert querying.pinned(body(calcs=["전처리 trim s true"]), t) == {}


def test_pinned_cache_limit(monkeypatch):
    gate_stubs(monkeypatch, False, None)
    monkeypatch.setattr(querying, "stats_key", lambda b, i: repr(b["conditions"]))
    t = ibis.memtable({"n": [1.0, None]})
    for i in range(querying.MAX_PINS + 3):
        querying.pinned(body(calcs=['전처리 fill n {"how": "mean"}'], conditions=[str(i)]), t)
    assert len(querying.PINS) == querying.MAX_PINS and "['0']" not in querying.PINS


def make_sql():
    return "select 1", "bigquery"


def test_dry_runs():
    """DB 이름이 아니라 능력: 조회를 실행하지 않고 읽는 양을 받는 클라이언트(client.query)가 있는 연결."""
    from core import scan_size
    assert scan_size.dry_runs(dry_con(True))
    assert not scan_size.dry_runs(dry_con(False))
    assert not scan_size.dry_runs(types.SimpleNamespace(name="bigquery"))  # 이름만으로는 아니다
    assert not scan_size.dry_runs(ibis.duckdb.connect())


def test_stats_gate(monkeypatch):
    """미리 잴 수 있는 연결: ask가 있으면 scan_gate 확인(Confirm), 없으면 Slack 명령과 같은 limit_check로 넘거나 알 수 없으면 안내 오류. 다른 연결은 묻지 않는다."""
    from core import scan_size
    monkeypatch.setattr(querying, "get_con", lambda: dry_con(True))
    monkeypatch.setattr(querying, "run", lambda work: work())
    monkeypatch.setattr(server, "scan_gate", lambda ask, make, rows=None: "확인 응답")
    with pytest.raises(querying.Confirm) as e:
        querying.stats_gate({}, make_sql)
    assert e.value.response == "확인 응답"
    monkeypatch.setattr(server, "scan_gate", lambda ask, make, rows=None: None)
    assert querying.stats_gate({"confirmed": True}, make_sql) is None
    for found in ({"over": True, "unknown": False, "text": "약 9GB를 읽어요."}, {"over": False, "unknown": True, "text": "크기를 알 수 없어요."}):
        monkeypatch.setattr(scan_size, "limit_check", lambda *a, _f=found, **k: _f)
        with pytest.raises(ValueError, match="확인 없이는") as e:
            querying.stats_gate(None, make_sql)
        assert found["text"] in str(e.value)
    monkeypatch.setattr(scan_size, "limit_check", lambda *a, **k: {"over": False, "unknown": False, "text": ""})
    assert querying.stats_gate(None, make_sql) is None
    monkeypatch.setattr(querying, "get_con", lambda: dry_con(False))
    monkeypatch.setattr(server, "scan_gate", lambda *a, **k: pytest.fail("묻지 않는다"))
    monkeypatch.setattr(scan_size, "limit_check", lambda *a, **k: pytest.fail("세지 않는다"))
    assert querying.stats_gate({}, make_sql) is None and querying.stats_gate(None, make_sql) is None


def test_runnable_asks_by_capability(shop, monkeypatch):
    """runnable의 DB 안 집계 확인도 DB 이름이 아니라 scan_size.dry_runs로 정한다(결정 8)."""
    con = server.state["con"]
    monkeypatch.setattr(con, "client", types.SimpleNamespace(query=lambda *a, **k: None), raising=False)
    monkeypatch.setattr(server, "scan_gate", lambda ask, make, rows=None: "확인 응답")
    with pytest.raises(querying.Confirm):
        querying.runnable(body(), select=False, ask={})


def test_pinned_dry_run_gate(monkeypatch):
    from core import scan_size
    gate_stubs(monkeypatch, True, lambda ask, make, rows=None: "확인 응답")
    t = ibis.memtable({"n": [1.0, None]})
    with pytest.raises(querying.Confirm):
        querying.pinned(body(calcs=['전처리 fill n {"how": "mean"}']), t, ask={})
    assert querying.PINS == {}
    monkeypatch.setattr(scan_size, "limit_check", lambda *a, **k: {"over": True, "unknown": False, "text": "커요."})
    with pytest.raises(ValueError, match="확인 없이는"):
        querying.pinned(body(calcs=['전처리 fill n {"how": "mean"}']), t)  # ask가 없는 길(대시보드·Slack·탭 안)
    assert querying.PINS == {}
    monkeypatch.setattr(scan_size, "limit_check", lambda *a, **k: {"over": False, "unknown": False, "text": ""})
    assert querying.pinned(body(calcs=['전처리 fill n {"how": "mean"}']), t) == {0: {"how": "mean", "known": 1.0}}


def test_pinned_fresh_once_per_key(monkeypatch):
    """[새로 받기](fresh)면 그 차례(요청) 안에서 열쇠마다 한 번 다시 구한다: 본문이 복사돼도(대시보드 with_period 등) 같은 열쇠면 한 번."""
    gate_stubs(monkeypatch, False, None)
    calls, real = [], prep_ops.known_stats
    monkeypatch.setattr(prep_ops, "known_stats", lambda *a: (calls.append(a[2]), real(*a))[1])
    t, lines = ibis.memtable({"n": [1.0, None, 3.0]}), ['전처리 fill n {"how": "mean"}']
    plain = body(calcs=lines)
    querying.pinned(plain, t)
    querying.pinned(dict(plain), t)
    assert calls == ["fill"]
    querying.pinned(body(calcs=lines, fresh=True), t)
    querying.pinned(body(calcs=lines, fresh=True), t)  # 같은 차례 안의 복사된 본문
    assert calls == ["fill", "fill"]
    server.state["turn_no"] += 1  # 다음 요청
    querying.pinned(body(calcs=lines, fresh=True), t)
    assert calls == ["fill", "fill", "fill"]
    querying.pinned(body(calcs=lines), t)  # fresh가 아니면 다시 구하지 않는다
    assert calls == ["fill", "fill", "fill"]


# ---------- 중앙값·사분위수를 못 하는 연결 ----------

def test_quantiles_pc_path(monkeypatch):
    t = ibis.memtable({"n": [1.0, 2.0, 3.0, 4.0, 100.0, None]})
    db = prep_ops.quantiles(t, "n", [0.25, 0.5, 0.75])
    assert db == pytest.approx([2.0, 3.0, 4.0])
    monkeypatch.setattr(prep_ops.source, "compiles", lambda expr: False)  # 연결이 median·quantile을 컴파일하지 못하는 경우
    assert prep_ops.quantiles(t, "n", [0.25, 0.5, 0.75]) == pytest.approx(db)  # 이 PC(Polars, linear)에서도 같은 값
    assert prep_ops.quantiles(ibis.memtable({"n": [None, None]}, schema={"n": "float64"}), "n", [0.5]) == [None]


def test_quantiles_pc_too_big(monkeypatch, folder):
    from core import settings
    monkeypatch.setattr(prep_ops.source, "compiles", lambda expr: False)
    monkeypatch.setitem(settings.DEFAULTS, "pc_stat_rows", 3)  # 설정이 없으면 기본값(settings.DEFAULTS)
    with pytest.raises(ValueError, match="3행"):
        prep_ops.quantiles(ibis.memtable({"n": [1.0, 2.0, 3.0, 4.0]}), "n", [0.5])
    assert prep_ops.quantiles(ibis.memtable({"n": [1.0, 2.0, 3.0]}), "n", [0.5]) == [2.0]  # 상한 안이면 전체로 정확히


def test_pc_stat_rows_setting(monkeypatch, folder):
    """설정 pc_stat_rows(결정 7)를 바꾸면 상한이 바뀐다. 범위 밖 값은 저장을 거절한다."""
    from core import settings
    monkeypatch.setattr(prep_ops.source, "compiles", lambda expr: False)
    low, high = settings.SIZES["pc_stat_rows"]
    big = ibis.memtable({"n": [float(i) for i in range(low + 1)]})
    settings.save({"pc_stat_rows": low})
    with pytest.raises(ValueError, match=f"{low:,}행"):
        prep_ops.quantiles(big, "n", [0.5])
    settings.save({"pc_stat_rows": low + 1})
    assert prep_ops.quantiles(big, "n", [0.5]) == [low / 2]
    for bad in (low - 1, high + 1):
        with pytest.raises(ValueError, match="pc_stat_rows"):
            settings.save({"pc_stat_rows": bad})
    settings.save({"pc_stat_rows": 0})  # 0은 기본값으로
    assert "pc_stat_rows" not in settings.load() and settings.get("pc_stat_rows") == 1_000_000


def test_pc_stat_rows_screen_field():
    status = (ROOT / "static/core/status.html").read_text(encoding="utf-8")
    common = (ROOT / "static/core/common.js").read_text(encoding="utf-8")
    assert 'id="pc-stat-rows"' in status
    assert "SERVER.sizes.pc_stat_rows" in common and "SERVER.defaults.pc_stat_rows" in common and "saveSetting({ pc_stat_rows" in common and 'saved("pc_stat_rows")' in common
    assert server.CONSTANTS["defaults"]["pc_stat_rows"] == 1_000_000  # 화면은 기본값을 서버에서 받는다(숫자를 화면에 따로 적지 않는다)


def test_query_stats_sqlite_median_iqr(lite):
    """SQLite(ibis 12)는 median·quantile을 못 해 그 컬럼만 이 PC로 받아 구한다."""
    assert sorted(r["n"] for r in rows(lite, base="e", calcs=['전처리 fill n {"how": "median"}'])) == [1.0, 2.0, 3.0]
    assert sorted(r["n"] for r in rows(lite, base="o", calcs=['전처리 outlier n {"how": "clip", "by": "iqr"}'])) == [1.0, 2.0, 3.0, 4.0, 7.0]


# ---------- 전처리 구성의 계산 줄 {line} ----------

def test_prep_formulas_line_stats(tmp_path):
    from core import prep_derive
    t = ibis.memtable({"n": [1.0, None, 3.0], "s": ["a", "b", "a"]})
    out, made = prep_derive.apply_formulas(t, [{"line": '전처리 fill n {"how": "median"}'}, {"line": "전처리 label s true"}, {"line": '전처리 scale n "minmax"'}])
    assert made == ["s_label"]
    assert sorted(out.n.to_pyarrow().to_pylist()) == [0.0, 0.5, 1.0]
    raw = sqlite3.connect(tmp_path / "p.db")
    raw.execute("create table o (n real, s text)")
    raw.executemany("insert into o values (?, ?)", [(1.0, "a"), (2.0, "a"), (3.0, "b"), (4.0, "b"), (100.0, "c")])
    raw.commit()
    raw.close()
    lite_t = ibis.sqlite.connect(str(tmp_path / "p.db")).table("o")
    out, made = prep_derive.apply_formulas(lite_t, [{"line": '전처리 outlier n {"how": "clip", "by": "iqr"}'}, {"line": "전처리 rare s {\"min\": 2}"}])
    got = out.to_pyarrow().to_pylist()
    assert sorted(r["n"] for r in got) == [1.0, 2.0, 3.0, 4.0, 7.0] and sorted(r["s"] for r in got) == ["a", "a", "b", "b", "기타"]


def test_prep_formulas_known_refused():
    from core import prep_derive
    with pytest.raises(ValueError, match="'known'은 프로그램이"):
        prep_derive.apply_formulas(ibis.memtable({"n": [1.0, None]}), [{"line": '전처리 fill n {"how": "mean", "known": 5}'}])


# ---------- 사용자가 쓴 known ----------

def test_stat_step():
    t = ibis.memtable({"n": [1.0], "s": ['{"a": 1}']})
    assert calc.stat_step('전처리 fill n {"how": "mean"}', t) == ("fill", "n", {"how": "mean"})
    assert calc.stat_step("  전처리 label s true", t) == ("label", "s", True)
    assert calc.stat_step("전처리 trim s true", t) is None
    assert calc.stat_step('전처리 expand s {"keys": ["a"]}', t) is None
    assert calc.stat_step("누적합 n 기준 n", t) is None


def test_known_written_by_user_refused():
    t = ibis.memtable({"n": [1.0, None]})
    for text in ('fill n {"how": "mean", "known": 5}', 'scale n {"how": "standard", "known": {"a": 0, "b": 1}}', 'label n {"known": [1.0]}'):
        with pytest.raises(ValueError, match="'known'은 프로그램이"):
            calc.prep_parts(text, t)


def test_known_written_by_user_refused_route(shop):
    err = shop.post("/api/query", json=body(calcs=['전처리 fill 금액 {"how": "mean", "known": 0}'], limit=5)).get_json()["error"]
    assert "'known'은 프로그램이" in err


# ---------- 라우트(연결별) ----------

def test_query_stats_duckdb(shop):
    """기간·조건을 건 뒤의 행으로 통계를 구한다(조건 금액 >= 1050 → 1050~1059)."""
    got = rows(shop, conditions=["금액 >= 1050"], calcs=['전처리 scale 금액 "standard"', "전처리 label 채널 true", "전처리 onehot 채널 true",
                                                       '전처리 rare 고객 {"min": 3}', "전처리 expand 메모 true"])
    assert len(got) == 10 and abs(sum(r["금액"] for r in got)) < 1e-9
    assert {r["채널_label"] for r in got} == {0, 1} and {(r["채널"], r["채널_앱"], r["채널_웹"]) for r in got} == {("앱", 1, 0), ("웹", 0, 1)}
    assert {r["고객"] for r in got} == {"기타"}  # 조건 뒤에는 고객마다 2번씩
    assert sorted(int(r["메모.a"]) for r in got) == list(range(50, 60))


def test_query_stats_columns_route(shop):
    r = shop.post("/api/columns", json=body(calcs=["전처리 label 채널 true", '전처리 fill 금액 {"how": "median"}']))
    assert r.status_code == 200, r.get_json()
    assert "채널_label" in [c[0] for c in r.get_json()["columns"]]


def test_query_stats_once_per_key(shop, monkeypatch):
    """쪽을 넘기거나 같은 검색을 다시 해도 통계는 한 번만 구한다."""
    calls, real = [], prep_ops.known_stats
    monkeypatch.setattr(prep_ops, "known_stats", lambda *a: (calls.append(a[2]), real(*a))[1])
    lines = ['전처리 fill 금액 {"how": "mean"}', "전처리 label 채널 true"]
    for offset in (0, 20, 40, 0):
        shop.post("/api/query", json=body(calcs=lines, sorts=["금액"], limit=20, offset=offset))
    assert sorted(calls) == ["fill", "label"]
    shop.post("/api/query", json=body(calcs=lines, conditions=["금액 > 1010"], limit=20))
    assert sorted(calls) == ["fill", "fill", "label", "label"]  # 조건이 바뀌면 다시


def test_query_stats_fresh_route(shop, monkeypatch):
    monkeypatch.setattr(querying, "FRESHED", {})
    calls, real = [], prep_ops.known_stats
    monkeypatch.setattr(prep_ops, "known_stats", lambda *a: (calls.append(a[2]), real(*a))[1])
    lines = ['전처리 fill 금액 {"how": "mean"}']
    for _ in range(2):
        shop.post("/api/query", json=body(calcs=lines, limit=5))
    assert calls == ["fill"]
    shop.post("/api/query", json=body(calcs=lines, limit=5, fresh=True))
    assert calls == ["fill", "fill"]  # 그 요청 안의 여러 build(paged·처리 용량 확인 등)에서 한 번만
    shop.post("/api/query", json=body(calcs=lines, limit=5, fresh=True))
    assert calls == ["fill", "fill", "fill"]  # 다음 요청의 [새로 받기]는 다시


def test_query_stats_sqlite_thread(lite):
    """SQLite는 만든 스레드에서만 쓸 수 있다: 통계 조회가 연결 스레드에서 나가야 한다."""
    got = rows(lite, base="e", calcs=['전처리 fill n {"how": "mean"}', "전처리 label s true"], sorts=["u", "n"])
    assert sorted(r["n"] for r in got) == [1.0, 2.0, 3.0]
    assert {r["s"]: r["s_label"] for r in got if r["s"] is not None} == {"x": 0, "y": 1}


def test_query_stats_polars(csvs):
    got = rows(csvs, conditions=["amt >= 5"], calcs=['전처리 scale amt "minmax"', "전처리 label cust true"])
    assert sorted(r["amt"] for r in got) == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert {r["cust"]: r["cust_label"] for r in got} == {"c0": 0, "c1": 1, "c2": 2}


# ---------- 화면: 쿼리 화면의 흐리기 해제 ----------

def test_query_screen_not_greyed():
    prepmenu = (ROOT / "static/core/prepmenu.js").read_text(encoding="utf-8")
    newcol = (ROOT / "static/core/newcol.js").read_text(encoding="utf-8")
    index = (ROOT / "static/query/index.html").read_text(encoding="utf-8")
    for word in ("statsOnly", "QUERY_OPTIONS", "전처리 화면으로 넘겨"):
        assert word not in prepmenu, word
    for word in ("QUERY_STATS", "TO_PREP", "전처리 화면으로 넘겨"):
        assert word not in newcol, word
    assert not re.search(r"PrepMenu\.items\(\{[^}]*screen", index)
