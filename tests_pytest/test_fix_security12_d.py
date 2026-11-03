"""보안 12D: 목표 파일의 문제 줄이 많아도 버리지 않고, 서버는 한 쪽(TARGET_PAGE개)씩 돌려주며 화면은 [보기]에서 [이전]/[다음]으로 넘겨 본다.
파일은 모두 tmp_path 안에서 만든다."""
import time

import pytest

from core import targets
from dashboard import dash_app


@pytest.fixture(autouse=True)
def fresh_cache():
    targets.clear()
    yield
    targets.clear()


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_read_keeps_all_errors_and_pages(tmp_path):
    path = write(tmp_path, "dup.txt", "a=1\n" * 1000)
    whole = targets.read(path)
    assert whole["rows"] == [] and whole["error_total"] == len(whole["errors"]) == 1000
    assert [e["line"] for e in whole["errors"]] == list(range(1, 1001))  # 줄 순서
    assert [e["line"] for e in targets.read(path, 900, 100)["errors"]] == list(range(901, 1001))
    assert targets.read(path, 1000, 100)["errors"] == [] and targets.read(path, 0, 0)["error_total"] == 1000
    targets.read(path, 0, 5)["errors"][0]["message"] = "x"
    assert targets.read(path, 0, 1)["errors"][0]["message"].startswith("1번째 줄: ")  # 호출자가 고쳐도 캐시는 그대로


def test_many_overlaps_read_fast(tmp_path):
    path = write(tmp_path, "dup.txt", "a=1\n" * 8000 + "b=2\n")
    start = time.perf_counter()
    got = targets.read(path, 0, 100)
    assert time.perf_counter() - start < 1 and got["error_total"] == 8000 and len(got["errors"]) == 100
    start = time.perf_counter()
    for _ in range(50):  # 캐시에서 쪽만 꺼내므로 다시 읽기는 빠르다
        targets.read(path, 7900, 100)
    assert time.perf_counter() - start < 1


def test_route_returns_one_page(client, tmp_path):
    path = write(tmp_path, "dup.txt", "a=1\n" * 1000)
    got = client.post("/api/targets", json={"path": str(path)}).get_json()
    assert got["error"] == "" and got["error_total"] == 1000 and len(got["errors"]) == 100 and got["errors"][0]["line"] == 1
    page = lambda q: client.get("/api/targets" + q).get_json()
    last = page("?offset=900")
    assert [e["line"] for e in last["errors"]] == list(range(901, 1001)) and last["offset"] == 900 and last["limit"] == 100
    assert page("?offset=1000")["errors"] == [] and page("?offset=1000")["error_total"] == 1000
    assert len(page("?limit=100000")["errors"]) == dash_app.TARGET_PAGE == 100  # 크게 줘도 한 쪽까지
    assert len(page("?offset=-5&limit=3")["errors"]) == 3 and page("?offset=x")["offset"] == 0  # 틀린 값은 기본값


