"""8차 8D 수집·연동: Slack 오류 글, 사용 한도 take, CSV 행 수."""
import json
import threading
import time

import ibis
import polars as pl
import pytest

from collect import collect_limits as L
from collect import collect_store
from core import server, settings
from integrations import commands


# ---------- 8D1: Slack 오류 글은 허용 목록 ----------
@pytest.mark.parametrize("leaky", ["(1366, \"Incorrect integer value: 'SECRET42' for column 'age' at row 1\")", "400 Bad int64 value: SECRET42",
                                   "100038 (22018): Numeric value 'SECRET42' is not recognized"])
def test_error_text_hides_db_values(leaky):
    assert "SECRET42" not in commands.error_text(Exception(leaky))


def test_error_text_hides_values_in_valueerror_subclasses():
    import pyarrow as pa
    try:
        pa.array(["SECRET42"], type=pa.int64())
    except ValueError as e:  # ArrowInvalid는 ValueError의 하위 오류이고 글에 값이 든다
        assert "SECRET42" in str(e)
        assert "SECRET42" not in commands.error_text(e)
    with pytest.raises(ValueError) as info:
        int("SECRET42")
    assert "SECRET42" not in commands.error_text(info.value)


def test_error_text_keeps_app_messages_and_masks_password():
    assert commands.error_text(ValueError('따옴표가 닫히지 않았습니다.')) == "할 수 없습니다: 따옴표가 닫히지 않았습니다."
    server.state["passwords"] = ["pw-9z"]
    try:
        assert "pw-9z" not in commands.error_text(ValueError("연결 실패 pw-9z 모르는 지표입니다"))
        assert "pw-9z" not in commands.error_text(Exception("connection refused pw-9z"))
    finally:
        server.state["passwords"] = []


def test_answer_routes_through_error_text():
    assert commands.answer('검색 "열린').startswith("할 수 없습니다: 따옴표")


# ---------- 8D2: take ----------
def _limit(folder, **kw):
    settings.write(L.GROUPS, {"g": {**L.DEFAULT, **kw}})


def test_take_keeps_only_widest_configured_window(folder):
    _limit(folder, per_second=1000)
    now = time.time()
    L.write({"g": {"sent": [now - 3000 + i * 0.01 for i in range(5000)], "day": [0, 0]}})
    L.take("g", threading.Event())
    data = json.loads((folder / L.USAGE).read_text(encoding="utf-8"))
    assert len(data["g"]["sent"]) == 1  # 1초 창 밖은 보관하지 않는다
    _limit(folder, per_hour=10000)
    L.write({"g": {"sent": [now - 3000 + i * 0.01 for i in range(5000)], "day": [0, 0]}})
    (folder / (L.USAGE + ".bak")).unlink(missing_ok=True)
    L.take("g", threading.Event())
    assert len(json.loads((folder / L.USAGE).read_text(encoding="utf-8"))["g"]["sent"]) == 5001  # per_hour가 있으면 한 시간치를 보관(기존 5000 + 새 1)


# 8차 리뷰: 보관하지 않는 긴 창의 사용량은 0이 아니라 모름(None) — 화면은 그 칸을 빼고 보인다
def test_usage_does_not_report_windows_it_no_longer_keeps(folder):
    now = time.time()
    _limit(folder, per_second=1000)
    L.write({"g": {"sent": [now - 30, now - 0.5], "day": [0, 0]}})
    u = L.usage("g", now)
    assert (u["second"], u["minute"], u["hour"]) == (1, None, None)
    _limit(folder, per_hour=10000)
    u = L.usage("g", now)
    assert (u["second"], u["minute"], u["hour"]) == (1, 2, 2)


def test_take_does_not_copy_bak_but_reset_does(folder):
    _limit(folder, per_minute=100)
    L.take("g", threading.Event())
    L.take("g", threading.Event())
    assert not (folder / (L.USAGE + ".bak")).exists()
    L.reset("g")
    assert (folder / (L.USAGE + ".bak")).exists()


def test_take_limits_unchanged(folder):
    _limit(folder, per_minute=2, per_day=3)
    stop = threading.Event()
    L.take("g", stop)
    L.take("g", stop)
    waited = threading.Event()
    waited.set()  # 세 번째는 분 한도로 기다려야 하므로 stop이 켜져 있으면 멈춘다
    with pytest.raises(L.Halt) as info:
        L.take("g", waited)
    assert info.value.failed is False
    _limit(folder, per_day=1)
    L.reset("g")
    L.take("g", stop)
    with pytest.raises(L.Halt) as info:
        L.take("g", stop)
    assert "오늘 한도" in str(info.value)
    L.write({"g": {"sent": [], "day": [L.period(L.groups()["g"], time.time())[0] - 86400, 1]}})  # 어제 기간의 기록이면 다음 날 이어 받는다
    L.take("g", stop)


def test_take_min_interval_keeps_last_sent(folder):
    _limit(folder, min_interval=30.0)
    L.take("g", threading.Event())
    stop = threading.Event()
    stop.set()
    with pytest.raises(L.Halt):  # 한도가 없어도 최소 간격 안에서는 기다린다(보낸 시각을 잃지 않는다)
        L.take("g", stop)


# ---------- 8D3: CSV 행 수 ----------
class _NoExecute:
    """write_output이 받은 쪽을 다시 조회(execute)하면 실패하는 연결."""

    def __init__(self, con):
        self.con = con

    def to_csv(self, *a, **k):
        return self.con.to_csv(*a, **k)

    def execute(self, *a, **k):
        raise AssertionError("받은 쪽을 다시 조회했다")


def test_write_output_csv_counts_written_file(tmp_path):
    con = ibis.duckdb.connect()
    rows = [("줄\n바꿈", "쉼표,있음"), ('따옴표"안', "x"), ("", None), ("줄\r\n둘", "중복"), ("a", "b"), ("a", "b")]
    table = ibis.memtable(pl.DataFrame({"p": [r[0] for r in rows], "q": [r[1] for r in rows]}))
    for label, expr in (("plain", table), ("dedup", table.distinct())):
        out = tmp_path / f"{label}.csv"
        got = collect_store.write_output(_NoExecute(con), expr, "csv", out, tmp_path)
        assert got == con.execute(expr.count()) == (6 if label == "plain" else 5)
    one = ibis.memtable(pl.DataFrame({"p": ["a", None, "", "b\nc"]}))  # 한 컬럼이고 빈 값
    assert collect_store.write_output(_NoExecute(con), one, "csv", tmp_path / "one.csv", tmp_path) == 4
