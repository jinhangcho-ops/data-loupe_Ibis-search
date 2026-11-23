"""분석 설정 저장: 사용자가 이름을 붙여 저장한 것(개수 제한 없음)과 [구하기] 때 자동으로 남긴 것(최근 20개)을 따로 둔다. 데이터는 여기서 지어낸다."""
from pathlib import Path

import pytest

from core import dash_store, settings
from conftest import prep_page

ROOT = Path(__file__).resolve().parent.parent
OVER = 5  # 자동 상한을 넘겨 넣어 보는 개수


def item(base: str = "orders", kind: str = "groups", at: str = "2026-01-01 09:00") -> dict:
    """화면이 보내는 분석 설정 한 개(전처리 구성 + 분석 칸)."""
    return {"config": {"schema": "", "base": base, "edits": {}}, "ana": {"prep": True, "kind": kind, "fields": {"a-gby": "매장"}, "corr": [], "flow": {"steps": [], "stages": []}, "aggs": {}},
            "base": base, "kind_name": "묶음끼리 비교", "at": at}


@pytest.fixture
def api(folder):
    from core import server
    from prep import prep_app  # noqa: F401  분석 설정 라우트
    from query.search_app import app
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    return c


def test_saved_and_recent_are_separate(folder):
    assert settings.analyses() == {"saved": {}, "recent": []}
    settings.save_analysis("주간 매출", item())
    settings.keep_analysis(item("customers"))
    got = settings.analyses()
    assert list(got["saved"]) == ["주간 매출"] and [r["base"] for r in got["recent"]] == ["customers"]  # 저장한 것은 자동 목록에 섞이지 않는다
    assert settings.named(settings.ANALYSES) == got["saved"] and settings.named(settings.ANALYSES_AUTO) == {"list": got["recent"]}  # 파일도 따로
    assert settings.save_analysis("주간 매출", None)["saved"] == {}  # 지우기
    assert len(settings.analyses()["recent"]) == 1


def test_recent_keeps_latest_only(folder):
    for i in range(settings.MAX_AUTO_ANALYSES + OVER):
        settings.keep_analysis(item(f"t{i}"))
    recent = settings.analyses()["recent"]
    assert len(recent) == settings.MAX_AUTO_ANALYSES == 20
    assert recent[0]["base"] == f"t{settings.MAX_AUTO_ANALYSES + OVER - 1}" and recent[-1]["base"] == f"t{OVER}"  # 최근 것이 앞, 오래된 것부터 버린다
    again = settings.keep_analysis(item(f"t{OVER}", at="2026-02-02 10:00"))["recent"]  # 같은 설정(config·ana)을 다시 구하면 새 줄이 아니라 맨 앞으로
    assert len(again) == settings.MAX_AUTO_ANALYSES and again[0]["base"] == f"t{OVER}" and again[0]["at"] == "2026-02-02 10:00"


def test_saved_has_no_count_limit(folder):
    for i in range(settings.MAX_SAVED + OVER):  # 검색·구성의 상한(MAX_SAVED)을 넘겨도 저장된다
        settings.save_analysis(f"설정 {i}", item(f"t{i}"))
    assert len(settings.analyses()["saved"]) == settings.MAX_SAVED + OVER


def test_bad_items_rejected(folder):
    for bad in ("글자", {}, {"config": {}}, {"config": [], "ana": {}}):
        with pytest.raises(ValueError, match="올바르지 않아요"):
            settings.keep_analysis(bad)
    with pytest.raises(ValueError, match="이름"):
        settings.save_analysis("", item())
    with pytest.raises(ValueError) as e:  # 탭(@이름)을 쓰는 구성은 앱을 다시 켜면 깨지므로 거절(구성 저장과 같은 규칙)
        settings.save_analysis("탭", item("@작업"))
    assert str(e.value) == dash_store.TAB_SAVE
    big = item()
    big["ana"]["fields"]["a-gby"] = "가" * settings.MAX_ANALYSIS_SIZE
    with pytest.raises(ValueError, match="너무 커요"):
        settings.keep_analysis(big)
    with pytest.raises(ValueError, match="너무 커요"):
        settings.save_analysis("큰 것", big)
    assert settings.analyses() == {"saved": {}, "recent": []}


def test_routes(api):
    assert api.get("/api/analyses").get_json() == {"saved": {}, "recent": []}
    saved = api.post("/api/analyses", json={"name": "내 분석", "item": item()})
    assert saved.status_code == 200 and saved.get_json()["saved"]["내 분석"] == item()
    kept = api.post("/api/analyses/recent", json={"item": item("customers")})
    assert kept.status_code == 200 and [r["base"] for r in kept.get_json()["recent"]] == ["customers"] and list(kept.get_json()["saved"]) == ["내 분석"]
    assert api.post("/api/analyses/recent", json={"item": None}).status_code != 200
    assert api.post("/api/analyses", json={"name": "내 분석", "item": None}).get_json()["saved"] == {}


def test_screen_wiring():
    prep, ana = prep_page(), (ROOT / "static/prep/analysis.js").read_text(encoding="utf-8")
    for i in ("a-saved", "a-saved-del", "a-save-name", "a-save", "a-recent", "a-recent-max"):
        assert f'id="{i}"' in prep, i
    assert "if (!full) keepAnalysis();" in ana  # 표본으로 구하기에 성공했을 때만 자동으로 남긴다
    assert 'api("/api/analyses/recent", { item: anaItem() })' in prep and 'api("/api/analyses", { name, item: anaItem() })' in prep
    assert "SERVER.analysis.max_auto_saved" in prep  # 20을 화면에 따로 적지 않는다
    assert "await openConfig(item.config);" in prep and "await restoreAna(item.ana);" in prep
