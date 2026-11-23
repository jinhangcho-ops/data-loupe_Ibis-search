"""B6 소수 자릿수 칸(plans/dash-blocks.md "B6 세부 명세"): 값 글자(compare.trimmed·format_value·format_delta·compare), 요약 문장(summary_text.summarize),
저장 검사(dash_store.check_digits·check_extra·check_list·check_more), 라우트(/api/compare·/api/dash/constants). 데이터는 시험 안에서 지어낸다.
xfail(strict): 기존 함수를 고쳐야 통과하는 시험. reason의 절이 끝나면 그 절의 strict-coder가 표시를 지운다. check_digits(새 함수) 시험은 표시 없음."""
import pytest

from core import compare
from core import dash_store
from core import server
from core import settings
from dashboard import summary_text

DIGITS_RANGE = (0, 6)  # 사용자 결정(B6): 자릿수 0~6


@pytest.fixture
def client(folder):
    from dashboard import dash_app  # noqa: F401  대시보드 라우트
    from query.search_app import app
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    return c


# ---------- core/compare.py ----------

def test_trimmed_zeros():
    assert compare.trimmed(12.5, 2) == "12.5"
    assert compare.trimmed(12.5, 2, True) == "12.50"
    assert compare.trimmed(-0.001, 2, True) == "0.00"
    assert compare.trimmed(-0.001, 2) == "0"
    assert compare.trimmed(1234.5, 0) == "1,235"
    assert compare.trimmed(3, 1, True) == "3.0"


def test_format_value_digits():
    assert (compare.MIN_DIGITS, compare.MAX_DIGITS) == DIGITS_RANGE
    assert compare.format_value(0.12345, "percent", 1) == "12.3%"
    assert compare.format_value(0.125, "percent", 0) == "13%"
    assert compare.format_value(12.5, "number", 2, True) == "12.50"
    assert compare.format_value(12.5, "number", 2) == "12.5"
    assert compare.format_value(1234.5, "number", 0) == "1,235"
    symbol = settings.get("currency_symbol")
    assert compare.format_value(1234.5, "currency", 2, True) == symbol + "1,234.50"
    assert compare.format_value(-0.001, "currency", 2, True) == symbol + "0.00"
    # 비우면 지금 규칙 그대로
    assert compare.format_value(1.005) == "1.01"
    assert compare.format_value(0.125, "percent") == "12.5%"
    assert compare.format_value(1234.5, "currency") == symbol + "1,235"


def test_format_delta_digits():
    assert compare.format_delta(0.0123, "percent", 1) == "+1.2%p"
    assert compare.format_delta(-0.001, "number", 2, True) == "0.00"
    assert compare.format_delta(2.25, "number", 0) == "+2"
    assert compare.format_delta(-2.5, "number", 1, True) == "-2.5"
    assert compare.format_delta(2.25) == "+2.25"


def test_compare_digits():
    assert compare.compare(12.25, 10, "number", digits=0)["delta_text"] == "+2"
    assert compare.compare(1000.004, 1000, "number", digits=2)["direction"] == "flat"  # 변화량 0, 변화율 0.0004% → 0%
    assert compare.compare(1000.004, 1000, "number", digits=3)["delta_text"] == "+0.004"
    said = compare.compare(0.25, 0.2, "percent", digits=1, zeros=True)
    assert said["delta_text"] == "+5.0%p"
    assert "(20.0% → 25.0%)" in said["text"]
    assert compare.compare(12.25, 10)["delta_text"] == "+2.25"


def test_compare_bad_digits():
    with pytest.raises(ValueError, match=r"^소수 자릿수는 0~6 사이의 정수여야 해요\.$"):
        compare.compare(1, 0, digits=7)
    with pytest.raises(ValueError, match="소수 자릿수는"):
        compare.compare(1, 0, digits=True)
    with pytest.raises(ValueError, match=r"^끝자리 0 채우기는 켬\(true\)이나 끔\(false\)이어야 해요\.$"):
        compare.compare(1, 0, digits=1, zeros="y")
    with pytest.raises(ValueError, match=r"^끝자리 0 채우기는 소수 자릿수를 정했을 때만 써요\.$"):
        compare.compare(1, 0, zeros=True)


# ---------- dashboard/summary_text.py ----------

def test_summarize_digits():
    found = summary_text.summarize("값", 10, None, "number", target=20, digits=1, zeros=True)
    assert any(s.startswith("목표 20.0 대비 50%") for s in found["sentences"])
    found = summary_text.summarize("값", 12.25, 10, "number", digits=0)
    assert "(+2)" in found["sentences"][0]
    plain = summary_text.summarize("값", 10, None, "number", target=20)
    assert any(s.startswith("목표 20 대비 50%") for s in plain["sentences"])


# ---------- core/dash_store.py: check_digits (새 함수, 표시 없음) ----------

def test_check_digits():
    assert dash_store.check_digits({}, "카드") == {}
    assert dash_store.check_digits({"format": "number"}, "카드") == {}
    assert dash_store.check_digits({"digits": 2, "zeros": True}, "카드") == {"digits": 2, "zeros": True}
    assert dash_store.check_digits({"digits": 0, "zeros": False}, "카드") == {"digits": 0}
    assert dash_store.check_digits({"digits": 6}, "줄 부분") == {"digits": 6}
    assert dash_store.check_digits({"zeros": False}, "카드") == {}


def test_check_digits_errors():
    for bad in (7, -1, 1.0, True, "2"):
        with pytest.raises(ValueError, match=r"^카드의 소수 자릿수\(digits\)는 0~6 사이의 정수예요\.$"):
            dash_store.check_digits({"digits": bad}, "카드")
    with pytest.raises(ValueError, match=r"^줄 부분의 끝자리 0 채우기\(zeros\)는 켬\(true\)이나 끔\(false\)이에요\.$"):
        dash_store.check_digits({"digits": 1, "zeros": "yes"}, "줄 부분")
    with pytest.raises(ValueError, match=r"^카드의 끝자리 0 채우기\(zeros\)는 소수 자릿수\(digits\)를 정했을 때만 써요\.$"):
        dash_store.check_digits({"zeros": True}, "카드")


# ---------- core/dash_store.py: 저장 검사에 붙이기 ----------

def test_check_extra_keeps_digits():
    out = dash_store.check_extra({"format": "percent", "digits": 1, "zeros": True})
    assert (out["format"], out["digits"], out["zeros"]) == ("percent", 1, True)
    assert "digits" not in dash_store.check_extra({"format": "percent"})
    assert "digits" in dash_store.EXTRA_VIEW and "zeros" in dash_store.EXTRA_VIEW
    with pytest.raises(ValueError, match="카드의 소수 자릿수"):
        dash_store.check_extra({"digits": 9})


def test_check_list_part_digits():
    view = {"list": {"parts": [{"kind": "number", "column": "v", "digits": 3, "zeros": True}, {"kind": "text", "column": "g"}]}}
    parts = dash_store.check_list(view, ["g"])["list"]["parts"]
    assert (parts[0]["digits"], parts[0]["zeros"]) == (3, True)
    assert "digits" not in parts[1] and "zeros" not in parts[1]
    with pytest.raises(ValueError, match="줄 부분의 소수 자릿수"):
        dash_store.check_list({"list": {"parts": [{"kind": "number", "column": "v", "digits": 8}]}}, ["g"])


# ---------- dashboard/dash_app.py ----------

def test_compare_route_digits(client):
    r = client.post("/api/compare", json={"items": [{"cur": 12.25, "prev": 10, "fmt": "number", "digits": 0}, {"cur": 12.25, "prev": 10}]})
    assert r.status_code == 200
    results = r.get_json()["results"]
    assert results[0]["delta_text"] == "+2"
    assert results[1]["delta_text"] == "+2.25"
    assert client.post("/api/compare", json={"items": [{"cur": 1, "prev": 0, "digits": 9}]}).status_code != 200


def test_dash_constants_digits(client):
    found = client.get("/api/dash/constants").get_json()
    assert found["dash"]["digits"] == {"min": DIGITS_RANGE[0], "max": DIGITS_RANGE[1]}
