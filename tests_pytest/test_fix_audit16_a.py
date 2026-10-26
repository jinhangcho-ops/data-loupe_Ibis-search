"""감사 16차 16A: 상태·경고·배치도 카드는 엔티티마다 가장 늦은 행을 본다(앞 2000행이 아니라), 산점도·버블은 NaN·무한을 빈 값으로 뺀다.
데이터는 테스트 안에서 지어낸다."""
import datetime as dt

import duckdb
import polars as pl
import pytest
from lazy import ibis

from dashboard import dash_calc

START = dt.datetime(2026, 1, 1)


def servers(extra=()):
    """서버 10대가 1분마다 쓴 3000행(00:00~04:59), 04:10부터 cpu 99. extra는 덧붙일 행."""
    rows = [(f"s{i}", START + dt.timedelta(minutes=m), 99.0 if m >= 250 else 10.0) for m in range(300) for i in range(10)]
    rows += list(extra)
    return pl.DataFrame({"name": [r[0] for r in rows], "seen": [r[1] for r in rows], "cpu": [r[2] for r in rows]})


@pytest.fixture(params=["duckdb", "polars"])
def backend(request):
    return request.param


def watch(monkeypatch, backend, df, time_col="seen"):
    if backend == "duckdb":  # 진짜 DuckDB 표(ibis duckdb create_table은 메모리 데이터에서 sqlglot 30과 맞지 않아 원래 연결로 만든다)
        raw = duckdb.connect()
        raw.execute("create table nodes as select * from df")
        t = ibis.duckdb.from_connection(raw).table("nodes")
    else:
        t = ibis.polars.connect().create_table("nodes", df)
    monkeypatch.setattr(dash_calc.querying, "runnable", lambda body, **kw: t)
    monkeypatch.setattr(dash_calc, "run", lambda work: work())
    card = {"kind": "status", "source": {"body": {"base": "nodes"}},
            "view": {"entity": "name", "time_col": time_col, "stale": 30, "rules": [{"column": "cpu", "op": ">=", "value": 95, "level": "bad"}]}}
    return dash_calc.watch_data(card, (START + dt.timedelta(minutes=300)).isoformat(), None)


def test_watch_reads_latest_row_per_entity(monkeypatch, backend):
    found = watch(monkeypatch, backend, servers())
    got = {e["name"]: e for e in found["values"]["entities"]}
    assert len(got) == 10 and all(e["last"] == "2026-01-01T04:59:00" for e in got.values())
    assert all(e["level"] == "bad" and any("cpu" in r for r in e["reasons"]) for e in got.values())  # 끊김이 아니라 cpu
    assert found["values"]["more"] is False and found["note"] == ""


def test_watch_keeps_quiet_entity_behind_many_new_rows(monkeypatch, backend):
    found = watch(monkeypatch, backend, servers([("old", START, 10.0)]))  # 일찍 끊긴 서버도 보인다(새 행 3000개에 밀리지 않게)
    got = {e["name"]: e for e in found["values"]["entities"]}
    assert got["old"]["level"] == "bad" and got["old"]["last"] == "2026-01-01T00:00:00" and len(got) == 11


def test_watch_without_time_column_keeps_last_read_row(monkeypatch, backend):
    df = pl.DataFrame({"name": ["a", "a", "b"], "seen": [None, None, None], "cpu": [10.0, 99.0, 10.0]}, schema_overrides={"seen": pl.Datetime})
    got = {e["name"]: e["level"] for e in watch(monkeypatch, backend, df, None)["values"]["entities"]}
    assert got == {"a": "bad", "b": "ok"}
    got = {e["name"]: e["level"] for e in watch(monkeypatch, backend, df)["values"]["entities"]}  # 시각이 모두 빈 엔티티도 빠지지 않는다
    assert set(got) == {"a", "b"}


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_points_skip_nan_and_inf(bad):
    t = ibis.memtable(pl.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, bad], "y": [2.0, 4.0, 5.0, 9.0, 1.0], "s": [1.0, 2.0, 3.0, bad, 5.0]}).to_arrow())
    d = dash_calc.points_data(t, "x", "y")
    assert d["points"] == [[1, 2], [2, 4], [3, 5], [4, 9]] and d["total"] == 4
    assert d["n"] == 4 and d["b"] == pytest.approx(2.2) and d["a"] == pytest.approx(-0.5) and d["r"] == pytest.approx(11 / 130 ** 0.5)
    d = dash_calc.points_data(t, "y", "x", "s")  # 크기에 NaN·무한이 있어도 범위는 유한한 값으로
    assert d["total"] == 3 and d["size_range"] == [1, 3]


def test_yearly_only_target_still_found_for_month_dashboard(folder, tmp_path):
    from core import settings
    path = tmp_path / "목표.csv"
    path.write_text("이름,기간,값\n매출,2026,12억\n월별,2026-09,1\n월별,2026-10,2\n", encoding="utf-8")
    settings.write(dash_calc.TARGETS_FILE, {"path": str(path)})
    october = {"range": {"start": "2026-10-01", "end": "2026-10-31"}}
    assert dash_calc.resolve_target({"name": "매출"})["value"] == 12e8
    assert dash_calc.resolve_target({"name": "매출"}, october)["value"] == 12e8  # 그 달 줄이 없으면 기간 없이(예전처럼)
    assert dash_calc.resolve_target({"name": "월별"}, october)["value"] == 2  # 달마다 목표가 있으면 그 달
    november = {"range": {"start": "2026-11-01", "end": "2026-11-30"}}
    assert "2026-11 목표를 찾지 못했어요" in dash_calc.resolve_target({"name": "월별"}, november)["error"]
