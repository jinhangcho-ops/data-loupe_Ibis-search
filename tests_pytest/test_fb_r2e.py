"""F18 컬럼 요약을 전체 값으로(표본과 크게 다른 항목 표시), F30 데이터가 가장 많은 기간 찾기. 데이터는 tmp_path에서 지어낸 진짜 DuckDB 파일이다."""
import datetime as dt
import re
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parent.parent
N = 3000
START = dt.date(2026, 1, 1)


@pytest.fixture
def api(folder, tmp_path, monkeypatch):
    """3000행 DuckDB 파일에 연결한 클라이언트. 표본은 앞 1000행(head)만 읽게 해서 표본과 전체가 결정적으로 달라진다."""
    from core import profiler, server
    from prep import prep_app  # noqa: F401  전처리 라우트
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "x.duckdb"))
    raw.execute(f"""create table t as select i as id, i % 5 as flat,
        case when i < 2990 then null else i end as late,
        case when i < 1000 then 'a' || (i % 3) else 'b' || i end as tag,
        date '{START}' + (case when i < 2000 then i % 40 when i < 2400 then 100 + i % 3 else 200 + i % 30 end)::int as d
        from range({N}) r(i)""")
    raw.close()
    monkeypatch.setattr(profiler, "FALLBACK", [("head", profiler.head)])
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "x.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def profile_full(c, **more):
    c.post("/api/profile", json={"base": "t", "sample": 1000})
    return c.post("/api/profile/full", json={"base": "t", "sample": 1000, **more}).get_json()


def test_전체로_구한_값과_표본과_크게_다른_항목(api):
    got = profile_full(api)
    assert got["rows"] == N
    cols = {c["name"]: c for c in got["columns"]}
    assert (cols["id"]["max"], cols["id"]["unique"]) == (N - 1, N)
    assert cols["late"]["nulls"] == 2990 and cols["late"]["diff"] == ["unique", "min", "max"]  # 표본(앞 1000행)은 전부 빈 값, 전체는 99.7%라 빈 값 비율은 같게 본다
    assert cols["id"]["diff"] == ["max"]  # 모두 다른 번호는 값 종류가 달라도 같은 모양이라 표시하지 않는다
    assert cols["tag"]["diff"] == ["unique"] and cols["flat"]["diff"] == [] and cols["flat"]["unique"] == 5


def test_빈칸_비율_차이는_5퍼센트포인트부터(api):
    from prep import prep_app
    assert prep_app.differs({"nulls": 0, "unique": 3, "min": 1, "max": 3}, {"nulls": 60, "unique": 3, "min": 1, "max": 3}, 1000, 1000) == ["nulls"]
    assert prep_app.differs({"nulls": 0, "unique": 3, "min": 1, "max": 3}, {"nulls": 40, "unique": 3, "min": 1, "max": 3}, 1000, 1000) == []
    assert prep_app.differs({"nulls": 1, "unique": 3, "min": "2026-01-01", "max": "2026-01-09"}, {"nulls": 1, "unique": 3, "min": "2025-12-01", "max": "2026-01-09"}, 10, 10) == ["min"]


def test_표본_통계가_없으면_안내(api):
    assert "표본" in api.post("/api/profile/full", json={"base": "t", "sample": 1000}).get_json()["error"]


def test_가장_많은_기간_상위(api):
    got = api.post("/api/analyze", json={"recipe": {"base": "t"}, "how": {"kind": "busiest", "column": "d", "unit": "month", "top": 2}}).get_json()
    assert got["kind"] == "busiest" and got["data"] == {"full": True}  # 표본이 아니라 늘 전체로
    assert sum(p["count"] for p in got["periods"]) <= N
    first = got["periods"][0]
    assert first["start"] == "2026-01-01" and first["end"] == "2026-01-31" and len(got["periods"]) == 2
    assert got["periods"][0]["count"] >= got["periods"][1]["count"]


def test_기간_길이별_끝날과_거르기_반영(api):
    week = api.post("/api/analyze", json={"recipe": {"base": "t", "filters": ["id >= 2400"]}, "how": {"kind": "busiest", "column": "d", "unit": "week", "top": 1}}).get_json()["periods"][0]
    assert dt.date.fromisoformat(week["end"]) - dt.date.fromisoformat(week["start"]) == dt.timedelta(days=6) and dt.date.fromisoformat(week["start"]).weekday() == 0
    day = api.post("/api/analyze", json={"recipe": {"base": "t"}, "how": {"kind": "busiest", "column": "d", "unit": "day"}}).get_json()["periods"]
    assert len(day) == 5 and all(p["start"] == p["end"] for p in day)  # 기본 5개


@pytest.mark.parametrize("how, message", [({"unit": "year"}, "단위"), ({"top": 0}, "개수"), ({"column": "id"}, "날짜 컬럼")])
def test_가장_많은_기간_잘못된_입력(api, how, message):
    got = api.post("/api/analyze", json={"recipe": {"base": "t"}, "how": {"kind": "busiest", "column": "d", "unit": "day", **how}}).get_json()
    assert message in got["error"]


def test_화면_글자_확인():
    html = (ROOT / "static/prep/prep.html").read_text(encoding="utf-8")
    assert 'id="full-stats"' in html and "/api/profile/full" in html and "표본과 달라요" in html
    assert 'id="peak-find"' in html and 'kind: "busiest"' in html and "between" in html
    for button in ("full-stats", "peak-find"):
        assert re.search(rf'<button id="{button}"[^>]*title="[^"]+"', html)
