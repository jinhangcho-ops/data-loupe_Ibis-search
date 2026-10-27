"""28차 리뷰: 지도 카드 서경 점, 닫을 때 봇 끄기, MySQL UNION 받는 양, BigQuery 날짜 값, 답장 실패가 원래 오류를 덮던 것."""
import json
import threading
import time
import types

import polars as pl

from core import scan_size
from geo import mapcard
from integrations import bot_app


def box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


def test_map_card_keeps_west_points_when_another_region_crosses_dateline():
    fiji = {"type": "MultiPolygon", "coordinates": [[box(177, -19, 180, -16)], [box(-180, -19, -178, -16)]]}
    usa = {"type": "Polygon", "coordinates": [box(-125, 25, -66, 49)]}
    regions = pl.DataFrame({"name": ["fiji", "usa"], "geometry": [json.dumps(fiji), json.dumps(usa)]})
    out = mapcard.build(regions, pl.DataFrame({"lat": [40.0, -17.5], "lon": [-100.0, -179.0]}), {"name": "name"})["values"]
    assert out["points"] == [[-100.0, 40.0], [181.0, -17.5]] and [r["value"] for r in out["regions"]] == [1, 1]


def test_stop_on_close_does_not_wait_for_slow_start():
    bot_app.switch.acquire()  # 켜는 중
    try:
        threading.Thread(target=lambda: (time.sleep(5), None)).start()
        began = time.time()
        bot_app.stop(wait=False)
        assert time.time() - began < 3
    finally:
        bot_app.switch.release()


def test_mysql_union_rows_are_added_not_multiplied():
    cols = ["id", "select_type", "table", "rows"]
    rows = [(1, "PRIMARY", "a", 100000), (2, "UNION", "b", 100000), (3, "SIMPLE", "s", 50), (3, "SIMPLE", "o", 20)]
    cursor = type("C", (), {"description": [(c,) for c in cols], "fetchall": lambda self: rows, "close": lambda self: None})
    con = types.SimpleNamespace(name="mysql", raw_sql=lambda sql: cursor())
    assert scan_size.plan_rows(con, "SELECT 1", "mysql") == 100000 + 100000 + 50 * 20


def test_bigquery_timestamp_and_date_parts_are_read():
    for value in ("DATETIME('2025-01-01T00:00:00')", "TIMESTAMP('2025-01-01 00:00:00')", "DATE(2025, 1, 1)"):
        assert str(scan_size.date_bounds(f"SELECT * FROM t WHERE d >= {value}", "bigquery", "d")[0]) == "2025-01-01", value


def test_reply_failure_keeps_command_error():
    from integrations.adapters import slack_bot

    def fail(url, text):
        raise ValueError("답장 실패: 404 expired_url")

    def answer(text):
        from integrations import commands
        commands.failure.error = "DB 오류"
        return "실패"

    bot = slack_bot.SlackBot("xoxb-x", "xapp-x", ["U1"], [], answer, make=lambda b, a: (None, None), send=fail)
    entry = {}
    bot.respond("https://hooks.slack.test/r", "도움", entry)
    assert entry["error"] == "DB 오류; 답장 실패: 404 expired_url"
