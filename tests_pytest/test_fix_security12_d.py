"""보안 12D: 목표 파일의 문제 줄이 많아도 버리지 않고, 서버는 한 쪽(TARGET_PAGE개)씩 돌려주며 화면은 [보기]에서 [이전]/[다음]으로 넘겨 본다.
파일은 모두 tmp_path 안에서 만든다."""
import shutil
import time
from pathlib import Path

import pytest

from core import targets
from dashboard import dash_app
from test_fix_audit_js import run_node

DASH_HTML = (Path(__file__).resolve().parents[1] / "static" / "dashboard" / "dashboard.html").read_text(encoding="utf-8")


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


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_screen_view_button_pages_with_next(tmp_path):
    a = DASH_HTML.index("function targetsNote")
    code = DASH_HTML[a:DASH_HTML.index("async function loadTargets", a)]
    body = """
globalThis.document.body = mk("body");
const asked = [];
globalThis.api = async url => {
  asked.push(url);
  const offset = +(new URL(url, "http://x").searchParams.get("offset") ?? 0);
  const errors = Array.from({ length: Math.max(0, Math.min(100, 250 - offset)) }, (_, i) => ({ line: offset + i + 1, message: `${offset + i + 1}번째 줄: <b>틀림</b>` }));
  return { path: "p", rows: [], errors, error_total: 250, offset, limit: 100, error: "" };
};
(async () => {
  const note = mk("div");
  targetsNote(note, await api("/api/targets"), "비었음");
  const view = note.children.find(c => c.textContent === "보기");
  await view.onclick(); await sleep(0);
  const box = document.body.children[0], inner = box.children[0], list = inner.children[1], [prev, next] = inner.children[2].children;
  const first = { prev: prev.disabled, next: next.disabled, n: list.children.length, text: list.children[0].textContent };
  await next.onclick(); await next.onclick();
  const last = { prev: prev.disabled, next: next.disabled, n: list.children.length };
  const empty = mk("div"); targetsNote(empty, { path: "", rows: [], errors: [], error_total: 0 }, "비었음");
  console.log(JSON.stringify({ note: note.textContent, asked, first, last, empty: empty.textContent }));
})();
"""
    got = run_node(tmp_path, body, parts=[code])
    assert got["note"].startswith("목표 0개, 읽지 못한 줄 250개(1번째 줄: <b>틀림</b>)")
    assert got["asked"] == ["/api/targets", "/api/targets?offset=0", "/api/targets?offset=100", "/api/targets?offset=200"]
    assert got["first"] == {"prev": True, "next": False, "n": 100, "text": "1번째 줄: <b>틀림</b>"}  # 글자 그대로(textContent)
    assert got["last"] == {"prev": False, "next": True, "n": 50} and got["empty"] == "비었음"
