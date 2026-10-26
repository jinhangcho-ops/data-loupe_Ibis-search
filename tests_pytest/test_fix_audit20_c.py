"""20차 C: 공백 있는 컬럼 이름(가입 일·판매 채널·user id)이 문장 실행·내 지표 검사·정렬 칸에서 깨지지 않는다. 대시보드 카드는 거르는 조건을 버리지 않는다.
ev: id 0~7, user id = 'u' + id % 3, 가입 일 = 2026-01-01 + id, amt = (id+1)*10, 판매 채널 = A(id 0~3)·B(4~7). 값은 시험 안에서 지어낸다."""
import json
import shutil

import duckdb
import pytest

from core import server
from query import sentence_run as sr

COLS = {"id": "num", "user id": "text", "가입 일": "date", "amt": "num", "판매 채널": "text"}
BASE = {"base": "ev", "joins": [], "periods": [], "conditions": [], "calcs": []}


@pytest.fixture
def c(folder, tmp_path):
    from query.search_app import app
    path = tmp_path / "x.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("""create table ev as select i::int as id, 'u' || (i % 3) as "user id", date '2026-01-01' + i::int as "가입 일",
                   ((i + 1) * 10)::double as amt, case when i < 4 then 'A' else 'B' end as "판매 채널" from range(8) t(i)""")
    raw.close()
    client = app.test_client()
    client.get("/?key=" + server.KEY)
    assert client.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(path)}).status_code == 200
    yield client
    client.post("/api/disconnect", json={})


def run(c, tid, slots):
    got = c.post("/api/sentence/run", json={"template": tid, "slots": slots, "body": BASE, "columns": COLS})
    assert got.status_code == 200, got.get_json()
    return got.get_json()["rows"]


def test_spaced_sort_and_generated_names(c):
    latest = run(c, "e33", {"무엇": "user id", "정렬 기준": "가입 일", "방향": "가장 늦은"})
    assert sorted((r["user id"], r["id"]) for r in latest) == [("u0", 6), ("u1", 7), ("u2", 5)]
    first = run(c, "e24", {"대상": "user id", "컬럼": "판매 채널", "정렬 기준": "id", "집계": "count"})
    assert [(r["처음_판매_채널"], r["전체 개수"]) for r in first] == [("A", 8)]  # u0·u1·u2 모두 처음은 A라 8행 모두 A로 센다
    assert len(run(c, "e44", {"컬럼": "판매 채널", "바꿀 형": "글자"})) == 8
    got = c.post("/api/sentence/run", json={"template": "m04", "slots": {"컬럼": "판매 채널", "값": "B", "대상": "user id", "정렬 기준": "가입 일 desc"}, "body": BASE, "columns": COLS}).get_json()
    assert got["rows"][0][got["columns"][-1]] == 3, got  # 가장 늦은 채널이 B인 사용자 u0·u1·u2


def test_my_metric_spaced_condition(c):
    slots = {"조건": "판매 채널 = A", "N": 2, "대상": "user id"}
    d = {"name": "x", "scope": {"table": "ev"}, "template": "m11", "slots": slots, "unit": "", "better": ""}
    got = c.post("/api/my_metrics/validate", json={"definition": d, "columns": COLS}).get_json()
    assert got.get("ok") is True, got


node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


@node
def test_order_field_keeps_spaced_column(tmp_path):
    from test_fix_audit_js import run_node
    body = """
const f = { args: [{ name: "order", kind: "order", label: "정렬 기준" }] };
const a = Funcs.argFields(f, { columns: () => [["가입 일", "날짜"], ["id", "정수"]] });
a.write({ order: ["가입 일 desc"] });
const one = a.read();
a.write({ order: ["id"] });
console.log(JSON.stringify([one, a.read()]));
"""
    assert run_node(tmp_path, body, files=["funcs.js"]) == [{"order": ["가입 일 desc"]}, {"order": ["id asc"]}]


@node
def test_dash_card_refuses_filtered_groups(tmp_path):
    from test_fix_audit_js import between, run_node
    cols = {"store": "text", "amt": "num", "uid": "text"}
    compiled = {k: sr.compile(k, s, BASE, cols) for k, s in {"e11": {"컬럼": "store"}, "e13": {"무엇": "store", "값": "amt", "집계": "sum", "N": 3},
                                                             "e09": {"무엇": "uid", "N": 5}}.items()}
    compiled["plain"] = sr.compile("e02", {"컬럼들": ["store"], "중복": "빼고"}, BASE, cols)
    code = between("sentences.js", "function sentCard", "// ---------- 화면")
    body = f"const c = {json.dumps(compiled, ensure_ascii=False)};\nconsole.log(JSON.stringify(Object.fromEntries(Object.entries(c).map(([k, v]) => [k, sentCard(v, 't')]))));"
    got = run_node(tmp_path, body, parts=[code])
    for k in ("e11", "e13", "e09"):
        assert "reason" in got[k], (k, got[k])
    assert got["plain"]["kind"] == "chart", got["plain"]  # 거르지 않는 묶음은 그대로 카드
