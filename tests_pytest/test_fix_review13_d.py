"""13D: 대시보드 카드 표시(조건 색 목록, 표 ▲▼ 열쇠, 값 카드 글자, 화면·서버 숫자 모양, 실시간 오류, 편차 빈 엔티티, 기간·주기 안내)."""
import datetime as dt
import json
import subprocess

import duckdb
import pytest

from core import compare, server
from dashboard import dash_calc, period
from test_fix_audit7_js import fn, needs_node, page, run


@pytest.fixture
def db(folder, tmp_path):
    from dashboard import dash_app  # noqa: F401  대시보드 라우트
    from prep import prep_app  # noqa: F401  전처리 라우트
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "s.duckdb"))
    raw.execute("create table t (day date, flag boolean, grade double, store varchar, amt double)")
    raw.executemany("insert into t values (?, ?, ?, ?, ?)", [(dt.date(2026, 1, d), d % 2 == 0, float(d % 3), None if d % 5 == 0 else "AB"[d % 2], float(d)) for d in range(1, 29)])
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "s.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def values(c, card, ctx=None):
    return c.post("/api/dash/card/compute", json={"card": card, "ctx": ctx}).get_json()["data"]["values"]


# ---------- 13D2 표 카드 직전 ▲▼: 서버 열쇠가 화면(JS String)의 열쇠와 같다 ----------

def test_13d2_prev_keys_match_js_string(db):
    ctx = {"range": {"start": "2026-01-15", "end": "2026-01-28"}, "prev": {"start": "2026-01-01", "end": "2026-01-14", "text": "직전 14일"}}
    for col, want in (("flag", {"false", "true"}), ("grade", {"0", "1", "2"}), ("store", {"A", "B", "null"})):
        card = {"kind": "table", "title": "t", "source": {"table": "t", "date": "day", "body": {"base": "t", "series": [{"y": "amt", "agg": "sum"}]}}, "view": {"group_cols": [col]}}
        assert set(values(db, card, ctx)["prev"]) == want, col


@needs_node
def test_13d2_float_text_same_as_js(tmp_path):
    nums = [0.5, 1.0, -0.0, 2.25, 1e-05, 1.5e-07, 1e21, 1.5e22, 123456789.125, 0.1 + 0.2]
    script = tmp_path / "s.js"
    script.write_text(f"console.log(JSON.stringify({json.dumps(nums)}.map(String)))", encoding="utf-8")
    js = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8").stdout)
    assert [dash_calc.js_text(n) for n in nums] == js


# ---------- 13D6 편차 표의 빈 엔티티는 "(빈 값)" ----------

def test_13d6_deviation_empty_entity_name(db):
    card = {"kind": "deviation", "title": "t", "source": {"table": "t", "date": "day", "body": {"base": "t", "series": [{"y": "amt", "agg": "sum"}]}}, "view": {"entity": "store", "bucket": "day"}}
    names = [e["name"] for e in values(db, card)["entities"]]
    assert "(빈 값)" in names and "None" not in names


# ---------- 13D3·13D4 값 글자: 숫자가 아닌 값, 서버 format_value와 같은 모양 ----------

@needs_node
def test_13d3_13d4_show_value(tmp_path):
    cards = page("dashboard/dash_cards.js")
    nums = [(0.12345, "percent"), (-1234.5, "currency"), (-0.001, "number"), (2.5, "currency"), (1.005, "number"), (0.256, "percent"), (1234.567, "number"), (-0.4, "currency"), (1e21, "number"), (1e-9, "percent")]
    got = run(tmp_path, f"""
const out = {json.dumps(nums)}.map(([v, f]) => showValue(v, f));
console.log(JSON.stringify([out, showValue("가,나"), showValue(true, "percent"), showValue("2026-01-01", "number", "일")]));
""", fn(cards, "function showValue"), fn(cards, "function roundText"), fake=False)
    assert got[0] == [compare.format_value(v, f) for v, f in nums]
    assert got[1:] == ["가,나", "true", "2026-01-01일"]


# ---------- 13D5 실시간에서 서버가 돌려준 카드 오류를 카드에 적는다 ----------

@needs_node
def test_13d5_live_cards_keeps_error(tmp_path):
    ctl = page("dashboard/dash_ctl.js")
    got = run(tmp_path, r"""
globalThis.DashBoard = { plain: c => c };
const a = { id: "a", source: { key: "k" }, data: {} }, b = { id: "b", source: { key: "k" }, data: {}, rangeNote: "옛 오류" };
const board = { cards: [a, b], ctx: () => undefined, recompare: async () => {} };
const api = async () => ({ results: [{ id: "a", error: "컬럼이 없어요" }, { id: "b", data: { values: {} } }] });
liveCards(board, true).then(() => console.log(JSON.stringify([a.rangeNote, b.rangeNote])));
""", fn(ctl, "function showLive"), fn(ctl, "async function liveCards"), next(x for x in ctl.splitlines() if x.startswith("const cardRequest")), fake=False)
    assert got == ["컬럼이 없어요", ""]


# ---------- 13D7 0년·아주 긴 주기는 한국어 안내 ----------

@pytest.mark.parametrize("text", ["0000", "0000-05", "0000-Q1", "0000-05 ~ 2026-01"])
def test_13d7_year_zero(text):
    error = period.parse(text, "2026-10-01")["error"]
    assert error and "1년부터" in error and "year must" not in error


def test_13d7_previous_before_year_one():
    assert "1년 1월 1일보다 앞" in period.previous(period.parse("0001-01", "2026-10-01"))["error"]


def test_13d7_every_too_long():
    with pytest.raises(ValueError, match="너무 길어요"):
        period.parse_every("9" * 400 + "초")


def test_first_year_dashboard_has_no_prev_instead_of_english_error():
    import datetime as dt
    import pytest
    from dashboard import dash_refresh, period
    for text, day, months in (("0001-01", "0001-01-15", 1), ("0001", "0001-06-01", 12), ("0001-Q1", "0001-02-01", 3)):
        assert dash_refresh.context({"period": text, "as_of": day}) == {"range": {"start": "0001-01-01", "end": day}, "prev": None, "months": months}
    with pytest.raises(ValueError, match="달력 범위"):
        period.add_months(dt.date(1, 1, 5), -1)
