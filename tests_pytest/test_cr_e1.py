"""E1: 데이터 엔지니어 실측 보고(500만 행)에서 나온 저장·표본·메모리 한도·취소·CSV 추론·시간대·값 목록·held 사본 문제. 데이터는 시험 안에서 만든다."""
import datetime as dt
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from lazy import ibis

ROOT = Path(__file__).resolve().parent.parent


# ---------- DE4 수식 막기는 CSV·TSV·고정폭만 ----------

def test_DE4_xlsx는_값을_그대로_쓰고_CSV는_수식을_막는다(tmp_path):
    import polars as pl
    from core import result
    raw = ["+82-10-1234", "-3", "=1+1", " =1+1", "ok"]
    t = ibis.memtable({"s": raw})
    result.export(t, tmp_path / "a.xlsx")
    assert pl.read_excel(tmp_path / "a.xlsx")["s"].to_list() == raw  # fastexcel로 셀 값을 읽는다
    result.export(t, tmp_path / "a.parquet")
    assert pl.read_parquet(tmp_path / "a.parquet")["s"].to_list() == raw
    result.export(t, tmp_path / "a.csv")
    text = (tmp_path / "a.csv").read_text("utf-8")
    assert "'+82-10-1234" in text and "'-3" in text and "'=1+1" in text and "' =1+1" in text  # 앞에 빈칸이 있는 수식도 막는다


# ---------- DE1 파일(Polars) 연결의 표본은 표 전체를 읽지 않는다 ----------

def peak_growth(work):
    """work()를 도는 동안 이 프로세스 RSS가 시작보다 가장 많이 늘어난 바이트와 work의 결과."""
    import psutil
    me, stop, peak = psutil.Process(), threading.Event(), [0]
    base = me.memory_info().rss

    def watch():
        while not stop.is_set():
            peak[0] = max(peak[0], me.memory_info().rss - base)
            time.sleep(0.005)
    watcher = threading.Thread(target=watch)
    watcher.start()
    try:
        found = work()
    finally:
        stop.set()
        watcher.join()
    return peak[0], found


def test_DE1_Polars_블록_표본은_일부만_읽는다(tmp_path):
    import polars as pl
    from core import profiler, source
    rows = 2_000_000
    path = tmp_path / "big.parquet"
    pl.DataFrame({"i": pl.int_range(rows, eager=True), "s": pl.int_range(rows, eager=True).cast(pl.String) + "-가나다라마바사아자차카타파하",
                  "f": pl.int_range(rows, eager=True).cast(pl.Float64)}).write_parquet(path, row_group_size=100_000)
    con = source.open_flat_files([path])
    t = con.table("big")
    small, found = peak_growth(lambda: profiler.block(con, t, None, "big", 5000, rows, None))
    full, _ = peak_growth(lambda: t.to_pyarrow())
    assert found is not None and found.num_rows == 5000
    assert len(set(found.column("i").to_pylist())) == 5000  # 구간이 겹치지 않는다
    assert small < full / 3, (small, full)


# ---------- DE2 메모리 한도가 held DuckDB와 Polars 추정에 걸린다 ----------

def test_DE2_held_DuckDB에_메모리_한도를_건다(folder):
    import duckdb
    from core import held, settings, source
    settings.save({"memory": 512})

    def work():
        held.clear()
        try:
            return held.local().con.execute("select current_setting('memory_limit')").fetchone()[0]
        finally:
            held.clear()
    want = duckdb.connect(config={"memory_limit": "512MB"}).execute("select current_setting('memory_limit')").fetchone()[0]
    assert source.within(None, work, "") == want


def test_DE2_Polars_연결은_추정의_3배로_견준다(tmp_path, monkeypatch):
    import polars as pl
    from core import scan_size, source
    path = tmp_path / "t.parquet"
    pl.DataFrame({"a": list(range(10_000))}).write_parquet(path)
    con = source.open_flat_files([path])
    opened = {"kind": "folder", "files": [str(path)]}
    sql, dialect = scan_size.sql_of(con, con.table("t"))
    est = scan_size.estimate(con, sql, opened, dialect)["bytes"]
    monkeypatch.setattr(scan_size, "limit", lambda config=None: (est * 2, "기준"))  # 추정보다 크고 3배보다 작은 기준
    found = scan_size.limit_check(con, sql, opened, dialect, {})
    assert found["over"] is True and found["bytes"] == est


# ---------- DE3 취소 ----------

@pytest.fixture
def polars_state(folder, monkeypatch):
    from core import server
    monkeypatch.setitem(server.state, "con", ibis.polars.connect())
    monkeypatch.setitem(server.state, "opened", {"kind": "folder", "files": []})
    monkeypatch.setitem(server.state, "cancel", threading.Event())
    monkeypatch.setitem(server.state, "left", None)
    return server


def cancel_after(server, seconds):
    timer = threading.Timer(seconds, lambda: server.state["cancel"].set())
    timer.start()
    return timer


def test_DE3_취소하면_받아둔_결과_DuckDB에도_멈추라고_알린다(polars_state):
    from core import held, source
    server = polars_state
    source.on_worker(held.clear)()
    seen = {}

    def work():
        try:
            held.local().con.execute("select sum(hash(range)) from range(100000000000)").fetchall()
        except Exception as e:
            seen["error"] = type(e).__name__
    try:
        cancel_after(server, 0.5)
        with pytest.raises(ValueError) as e:
            server.run(work)
        assert "error" in seen, "held DuckDB가 멈추지 않아 버린 스레드가 아직 계산한다"
        assert "Interrupt" in seen["error"]
        assert "아직" not in str(e.value)
    finally:
        if held.store["con"] is not None:
            held.store["con"].con.interrupt()
        source.on_worker(held.clear)()


def test_DE3_멈출_수_없는_작업을_버리면_끝나기_전에는_다음_작업을_겹쳐_돌리지_않는다(polars_state, monkeypatch):
    from core import source
    server = polars_state
    monkeypatch.setattr(source, "STOP_WAIT", 0.2)
    running = threading.Event()

    def slow():
        running.set()
        time.sleep(1.5)
        running.clear()
    cancel_after(server, 0.3)
    with pytest.raises(ValueError):
        server.run(slow)
    overlap = []
    with pytest.raises(ValueError, match="아직 끝나지 않았어요"):
        server.run(lambda: overlap.append(running.is_set()))
    assert overlap == []
    time.sleep(1.5)
    assert server.run(lambda: running.is_set()) is False  # 끝난 뒤에는 그대로 돈다


# ---------- DE7 로컬 Excel 저장은 먼저 센다 ----------

def test_DE7_로컬_Excel은_행_수를_먼저_세어_바로_거절한다(tmp_path, monkeypatch):
    from core import result
    monkeypatch.setattr(result, "EXCEL_MAX_ROWS", 10)
    calls = []
    real = result.batches
    monkeypatch.setattr(result, "batches", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    with pytest.raises(ValueError, match="10행까지"):
        result.write_excel(ibis.memtable({"a": list(range(20))}), tmp_path / "a.xlsx")
    assert calls == [] and not (tmp_path / "a.xlsx").exists()
    result.write_excel(ibis.memtable({"a": list(range(5))}), tmp_path / "b.xlsx")  # 상한 안이면 그대로 쓴다
    assert (tmp_path / "b.xlsx").exists()


# ---------- DE10 CSV 타입 추론 ----------

def test_DE10_CSV는_앞_만_행으로_타입을_읽고_날짜를_날짜로(tmp_path):
    from core import source
    lines = ["code,d"] + [f"{i},2026-01-{i % 28 + 1:02d}" for i in range(3000)] + [f"A{i},2026-02-01" for i in range(500)]
    path = tmp_path / "mixed.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    con = source.open_flat_files([path])
    t = con.table("mixed")
    assert t.schema()["code"].is_string()
    assert t.schema()["d"].is_date()
    assert t.count().to_pyarrow().as_py() == 3500
    assert t.to_polars()["code"][-1] == "A499"


# ---------- DE12·DA24 차트 x_unit과 시간대 ----------

def tz_table(tmp_path, engine):
    import polars as pl
    path = tmp_path / "tz.parquet"
    utc = dt.timezone.utc
    pl.DataFrame({"ts": [dt.datetime(2026, 1, 1, 20, 0, tzinfo=utc), dt.datetime(2026, 1, 1, 10, 0, tzinfo=utc)], "v": [1, 2]}).write_parquet(path)
    con = ibis.duckdb.connect() if engine == "duckdb" else ibis.polars.connect()
    return con.read_parquet(path)


@pytest.mark.parametrize("engine", ["duckdb", "polars"])
def test_DE12_시간대_있는_시각을_설정_시간대의_날짜로_묶는다(tmp_path, monkeypatch, folder, engine):
    from core import result, source
    monkeypatch.setattr(source, "pc_zone", lambda: "Asia/Seoul")
    labels, datasets, _ = result.get_chart(tz_table(tmp_path, engine), "ts", [{"agg": "count"}], x_unit="day")
    assert [str(v)[:10] for v in labels] == ["2026-01-01", "2026-01-02"]
    assert datasets[0]["values"] == [1, 1]


@pytest.mark.parametrize("engine", ["duckdb", "polars"])
def test_DA24_x_unit으로_묶고_빈_기간은_0(tmp_path, folder, engine):
    import polars as pl
    from core import result
    path = tmp_path / "d.parquet"
    pl.DataFrame({"d": [dt.date(2026, 1, 1), dt.date(2026, 1, 1), dt.date(2026, 1, 4), dt.date(2026, 3, 9)], "v": [1, 2, 3, 4]}).write_parquet(path)
    con = ibis.duckdb.connect() if engine == "duckdb" else ibis.polars.connect()
    t = con.read_parquet(path)
    labels, datasets, _ = result.get_chart(t.filter(t.d < dt.date(2026, 2, 1)), "d", [{"agg": "count"}, {"y": "v", "agg": "sum"}, {"y": "v", "agg": "mean"}], x_unit="day")
    assert [str(v)[:10] for v in labels] == ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
    assert datasets[0]["values"] == [2, 0, 0, 1] and datasets[1]["values"] == [3, 0, 0, 3]
    assert datasets[2]["values"] == [1.5, None, None, 3.0]  # 평균은 빈 기간을 0으로 만들지 않는다
    labels, datasets, _ = result.get_chart(t, "d", [{"agg": "count"}], x_unit="month")
    assert [str(v)[:10] for v in labels] == ["2026-01-01", "2026-02-01", "2026-03-01"] and datasets[0]["values"] == [3, 0, 1]
    labels, datasets, _ = result.get_chart(t, "d", [{"agg": "count"}], x_unit="week")  # 주는 월요일 시작
    assert [str(v)[:10] for v in labels][:3] == ["2025-12-29", "2026-01-05", "2026-01-12"] and datasets[0]["values"][:3] == [3, 0, 0]
    labels, _, _ = result.get_chart(t, "d", [{"agg": "count"}])  # x_unit이 없으면 지금처럼
    assert len(labels) == 3


# ---------- DE16 값 목록은 앞부분에서만 ----------

def test_DE16_값_목록은_앞_행에서만_찾고_일부임을_알린다(monkeypatch):
    from core import result
    monkeypatch.setattr(result, "VALUE_SCAN", 5)
    t = ibis.memtable({"g": list("abcdefghij")})
    values, more = result.get_values(t, "g")
    assert len(values) == 5 and not more
    assert result.values_partial(t) is True
    assert result.values_partial(ibis.memtable({"g": list("abc")})) is False


# ---------- DE17 threads가 Polars에도 ----------

def test_DE17_설정_threads가_Polars_스레드_수가_된다(tmp_path):
    (tmp_path / "DataLoupe").mkdir()
    (tmp_path / "DataLoupe" / "settings.json").write_text(json.dumps({"threads": 1}), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "POLARS_MAX_THREADS"}
    env["APPDATA"] = str(tmp_path)
    code = "import lazy, polars; print(polars.thread_pool_size())"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True).stdout
    assert out.strip() == "1"


# ---------- DE18 held 사본을 다 지우면 파일도 닫고 지운다 ----------

def test_DE18_사본을_다_지우면_held_DuckDB를_닫고_파일을_지운다(folder):
    from core import held, source

    def work():
        held.clear()
        held.fetch("k", ibis.memtable({"a": [1, 2, 3]}), threading.Event(), cap=False)
        place = held.store["folder"]
        assert Path(place).exists()
        held.forget("k")
        return place, held.store["con"]
    place, con = source.within(None, work, "")
    assert con is None and not Path(place).exists()
