"""31차 감사: 공통 키로 맞춘 지표의 키 컬럼, 환경변수 웹훅의 https, 목표 기간 모양, 반올림해 0인 변화, 보낼 곳 세션 값, Notion 크기, 신선도."""
import datetime as dt

import polars as pl
import pytest
from lazy import ibis

from core import compare, credentials, targets, watch
from integrations import notify
from integrations.adapters import notion, transport, webhook
from metrics import metric_catalog as cat

D = dt.datetime


def test_common_key_metric_fetches_key_column():
    users = ibis.memtable(pl.DataFrame({"uid": [1, 2], "ts": [D(2026, 3, 1), D(2026, 3, 2)], "ch": ["A", "B"]}).to_arrow())
    ads = ibis.memtable(pl.DataFrame({"cost": [10.0, 20.0], "ch": ["A", "B"]}).to_arrow())
    slots = {"사용자": "uid", "시각": "ts", "광고 테이블": "ads", "광고비": "cost"}
    got = cat.compute("cac", slots, {}, cat.Ctx(fetch=lambda body, keep: users.select(*keep), other=lambda n: ads), {"kind": "key", "keys": [["ch", "ch"]]})
    assert got["value"] == 15.0


def test_webhook_refuses_plain_http_at_send(monkeypatch):
    monkeypatch.setattr(transport, "post", lambda *a, **k: pytest.fail("보내면 안 된다"))
    m = {"title": "t", "text": "", "rows": 1, "duration": "", "table": "고객 | 금액", "image": None}
    with pytest.raises(ValueError, match="https"):
        webhook.send({"url": "http://example.test/h", "content": "text", "template": "{표}"}, m)


def test_target_periods_normalized():
    rows = [targets.make_row("매출", 100, p, "", "", 1)["period"] for p in ("2026-q4", "2026.10", "2026/1", 2026, 2026.0)]
    assert rows == ["2026-Q4", "2026-10", "2026-01", "2026", "2026"]
    with pytest.raises(ValueError, match="숫자로"):
        targets.make_row("매출", 100, 2026.1, "", "", 1)


def test_tiny_change_is_flat():
    assert compare.compare(100.001, 100)["direction"] == "flat"
    assert compare.compare(0.50001, 0.5, "percent")["direction"] == "flat"
    assert compare.compare(110, 100)["direction"] == "up"


def test_deleting_destination_keeps_similarly_named_secret(monkeypatch):
    monkeypatch.setattr(notify.settings, "save_named", lambda *a, **k: {})
    credentials.SESSION.update({"dest:a:token": "1", "dest:a:b:token": "2"})
    notify.save("a", None)
    assert "dest:a:token" not in credentials.SESSION and credentials.SESSION.pop("dest:a:b:token") == "2"


def test_notion_table_bytes_capped():
    table = "가" * 300_000
    body = notion.blocks({"title": "t", "text": "", "rows": 1, "duration": "", "table": table})
    assert sum(len(p["text"]["content"].encode()) for p in body[-1]["code"]["rich_text"]) <= notion.TABLE_BYTES


def test_freshness_matches_entity_staleness_in_seconds():
    now = D(2026, 1, 1, 12, 0, 0)
    assert "새 데이터가 없어요" in watch.freshness(now - dt.timedelta(minutes=30, seconds=30), 30, now)
