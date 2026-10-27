"""27차 감사: 봇 켜기 중 [멈춤]이 봇을 못 끄던 것 등. 바깥 서비스는 가짜로 바꿔 끼운다."""
import threading
import time

from integrations import bot_app


class FakeBot:
    made = []

    def __init__(self, bot_token, app_token, users, channels, answer):
        self.on, self.team, self.log = False, "T", []
        FakeBot.made.append(self)

    def start(self):
        time.sleep(0.3)  # Slack에 붙는 시간
        self.on = True

    def stop(self):
        self.on = False

    def running(self):
        return self.on


def test_stop_during_start_leaves_no_bot_running(monkeypatch):
    from query.search_app import app
    monkeypatch.setattr(bot_app.slack_bot, "SlackBot", FakeBot)
    monkeypatch.setattr(bot_app, "tokens", lambda: ({"users": ["U1"]}, {"bot_token": "xoxb-x", "app_token": "xapp-x"}))
    FakeBot.made.clear()

    def start():
        with app.app_context():
            bot_app.start_bot()

    t = threading.Thread(target=start)
    t.start()
    time.sleep(0.1)
    with app.app_context():
        bot_app.stop_bot()
    t.join()
    assert not any(b.on for b in FakeBot.made) and bot_app.BOT["bot"] is None


def test_failed_slack_reply_is_kept_in_bot_log(monkeypatch):
    import slack_sdk.webhook
    from integrations.adapters import slack_bot, transport
    sent = []

    class Hook:
        def __init__(self, url):
            pass

        def send(self, text, response_type):
            sent.append(text)
            return type("R", (), {"status_code": 404, "body": "expired_url"})()

    monkeypatch.setattr(slack_sdk.webhook, "WebhookClient", Hook)
    monkeypatch.setattr(transport, "log", lambda *a: None)
    bot = slack_bot.SlackBot("xoxb-x", "xapp-x", ["U1"], [], lambda text: "x" * 50_000, make=lambda b, a: (None, None))
    entry = {}
    bot.respond("https://hooks.slack.test/r", "도움", entry)
    assert entry["error"] == "답장 실패: 404 expired_url"
    assert len(sent[0]) <= 40_000


def test_mysql_join_plan_rows_multiplies_tables():
    import types
    from core import scan_size
    cols = ["id", "select_type", "table", "type", "rows", "filtered"]
    rows = [(1, "SIMPLE", "s", "ALL", 50, 100.0), (1, "SIMPLE", "o", "ref", 20000, 100.0)]
    cursor = type("C", (), {"description": [(c,) for c in cols], "fetchall": lambda self: rows, "close": lambda self: None})
    con = types.SimpleNamespace(name="mysql", raw_sql=lambda sql: cursor())
    assert scan_size.plan_rows(con, "SELECT * FROM s JOIN o ON o.sid = s.id", "mysql") == 1_000_000


def test_bigquery_partition_rows_unknown_for_month_partitions_and_counts_unpartitioned(monkeypatch):
    import types
    from core import profiler, scan_size
    monkeypatch.setattr(profiler, "spec_table", lambda schema, base: {"partition": {"column": "day"}})
    monkeypatch.setattr(scan_size, "bigquery_name", lambda con, name: "p.d." + name)

    def count(parts, sql="SELECT * FROM events WHERE day >= '2026-01-01'"):
        job = types.SimpleNamespace(result=lambda: parts)
        con = types.SimpleNamespace(name="bigquery", client=types.SimpleNamespace(query=lambda q: job))
        return scan_size.partition_rows(con, sql, "bigquery", ["events"])

    assert count([{"partition_id": "202601", "total_rows": 5}]) is None
    assert count([{"partition_id": "20260102", "total_rows": 5}, {"partition_id": "__UNPARTITIONED__", "total_rows": 7}]) == 12


def test_date_bounds_ignores_dates_inside_arithmetic():
    from core import scan_size
    assert scan_size.date_bounds("SELECT * FROM t WHERE d >= DATE_SUB(DATE '2025-01-31', INTERVAL 30 DAY)", "bigquery", "d") == (None, None)
    assert scan_size.date_bounds("SELECT * FROM t WHERE d >= DATE '2025-01-01' AND d < '2025-02-01'", "bigquery", "d")[0].isoformat() == "2025-01-01"


def test_unsplit_dateline_polygon_assigns_points_on_both_sides():
    import numpy as np
    from geo import geojson, spatial
    ring = [[170, -10], [-170, -10], [-170, 10], [170, 10], [170, -10]]  # 변경선을 자르지 않고 건너 적은 고리
    regions = [[[spatial.closed(r) for r in poly] for poly in geojson.polygons({"type": "Polygon", "coordinates": [ring]})]]
    assert spatial.assign(np.zeros(3), np.array([0.0, 175.0, -175.0]), regions).tolist() == [-1, 0, 0]
