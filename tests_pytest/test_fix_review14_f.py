"""14차 리뷰 묶음 F: 달마다 목표가 있는 목표 파일을 대시보드 기간(달·분기·연)으로 고르기(A8), 원격 연결의 실시간 간격 기본 최소(D11). 값은 시험 안에서 지어낸다."""
import pytest

from core import server
from core import dash_store as store
from core import targets
from dashboard import dash_calc
from dashboard import dash_refresh as refresh
from test_dash_calc import SUM, compute, db, value_card  # noqa: F401  진짜 DuckDB 파일에 연결한 시험 클라이언트


@pytest.fixture
def monthly(db, tmp_path):
    path = tmp_path / "목표.csv"
    path.write_text("이름,값,기간\n매출,1억,2026-10\n매출,1.2억,2026-11\n분기,9,2026-Q4\n연간,12,2026\n", encoding="utf-8")
    assert db.post("/api/targets", json={"path": str(path)}).get_json()["error"] == ""
    return db


def test_target_follows_dashboard_month_quarter_year(monthly):
    pick = lambda name, period, as_of: dash_calc.resolve_target({"name": name}, refresh.context({"period": period, "as_of": as_of}))
    assert pick("매출", "2026-10", "2026-10-15")["value"] == 1e8  # 기준일로 잘린 이번 달
    assert pick("매출", "2026-11", "2026-12-20")["value"] == 1.2e8  # 다 지난 달
    assert pick("분기", "2026-Q4", "2026-11-03")["value"] == 9 and pick("연간", "2026", "2026-10-23")["value"] == 12
    assert "2026-12 목표를 찾지 못했어요" in pick("매출", "2026-12", "2026-12-20")["error"]  # 맞는 목표가 없으면 이유
    assert "어느 기간인지 알 수 없어요" in pick("매출", "최근 7일", "2026-10-22")["error"]  # 달·분기·연이 아니면 지금처럼
    assert "어느 기간인지 알 수 없어요" in dash_calc.resolve_target({"name": "매출"})["error"]
    # 카드를 구하는 길(/api/dash/card/compute)도 대시보드 기간을 쓴다
    ctx = refresh.context({"period": "2026-11", "as_of": "2026-12-20"})
    assert compute(monthly, card=value_card("kpi", SUM, view={"target": {"name": "매출"}}), ctx=ctx)["data"]["target"]["value"] == 1.2e8


def test_ambiguous_message_josa():
    rows = targets.drop_overlaps([targets.make_row("매출", 1, p, None, None, i) for i, p in enumerate(("2026-10", "2026-11"), 1)], [])[0]
    got = targets.lookup(rows, "매출")["error"]
    assert "기간을 정해 주세요" in got and "기간를" not in got


REMOTE = "url:postgres://db.example:5432/shop"


def test_remote_live_default_min(folder):
    sqlite = store.source_key({"kind": "url", "url": "sqlite:///C:/data/shop.db"})
    folder_key = store.source_key({"kind": "folder", "path": "C:/data/sales"})
    assert refresh.live_min(REMOTE) == 60 and refresh.live_min(folder_key) == 0 and refresh.live_min(sqlite) == 0  # 이 PC 파일은 그대로
    card = {"source": {"key": REMOTE, "label": "shop"}}
    with pytest.raises(ValueError, match="1분 이상"):
        refresh.check_dashboard_every({"cards": [card], "live": {"every": "10초"}})
    refresh.set_live_min(REMOTE, 5)  # 연결별로 정했으면 그 값
    assert refresh.live_min(REMOTE) == 5
    refresh.check_dashboard_every({"cards": [card], "live": {"every": "10초"}})
    refresh.set_live_min(REMOTE, 0)
    assert refresh.live_min(REMOTE) == 60


def test_period_every_route_uses_remote_default(client, monkeypatch):
    monkeypatch.setitem(server.state, "opened", {"kind": "url", "url": "postgres://db.example:5432/shop"})
    got = client.post("/api/period/every", json={"text": "10초", "kind": "live"}).get_json()
    assert got["seconds"] is None and "1분" in got["error"]
    assert client.post("/api/period/every", json={"text": "1분", "kind": "live"}).get_json()["seconds"] == 60
    assert client.get("/api/dash/live_min").get_json()["seconds"] == 60
