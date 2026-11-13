"""(Phase 20) 설정 창 [메모리]: 붙잡고 있는 데이터의 목록과 고른 것 비우기. 데이터는 시험 안에서 지어낸다."""
import pyarrow as pa
import pytest

from core import memory
from core import server
from prep import prep_app
from test_fix_audit_js import between, read

ROWS = 5
OPENED = {"file": r"C:\data\sales.duckdb"}


def test_list_shows_only_what_is_held(client):
    assert client.get("/api/memory").json["items"] == []
    prep_app.SAMPLES["k"] = (ROWS, pa.table({"a": list(range(ROWS))}), {}, None)
    server.state.update(con=object(), opened=OPENED)
    got = client.get("/api/memory").json
    by = {i["id"]: i for i in got["items"]}
    assert set(by) == {"sample", "connection"} and by["connection"]["detail"] == "sales.duckdb"  # 폴더 경로는 보이지 않는다
    assert by["sample"]["detail"].startswith(f"{ROWS}행 · ") and got["mb"] > 0 and "pyarrow" in got["modules"]


def test_drop_releases_only_the_chosen(client):
    prep_app.SAMPLES["k"] = (ROWS, pa.table({"a": list(range(ROWS))}), {}, None)
    server.state.update(con=object(), opened=OPENED)
    got = client.post("/api/memory/drop", json={"ids": ["sample"]}).json
    assert prep_app.SAMPLES == {} and server.state["con"] is not None
    assert [i["id"] for i in got["items"]] == ["connection"] and got["before"] > 0
    client.post("/api/memory/drop", json={"ids": ["connection"]})
    assert server.state["con"] is None and client.get("/api/memory").json["items"] == []


@pytest.mark.parametrize("ids", [[], None, ["없는것"], ["sample", "없는것"]])
def test_drop_refuses_bad_ids_and_drops_nothing(client, ids):
    prep_app.SAMPLES["k"] = (ROWS, pa.table({"a": list(range(ROWS))}), {}, None)
    assert client.post("/api/memory/drop", json={"ids": ids}).status_code == 400
    assert "k" in prep_app.SAMPLES
    prep_app.SAMPLES.clear()


def test_lazy_modules_are_not_listed(monkeypatch):
    monkeypatch.setitem(memory.sys.modules, "duckdb", type(memory.LAZY, (), {})())
    assert "duckdb" not in memory.modules()


def test_screen_has_the_memory_tab():
    page, code = read("status.html"), read("common.js")
    assert 'data-set="memory" aria-selected="false">메모리</button>' in page and '<section class="set-page hidden" data-set="memory"' in page
    assert '"calc", "memory", "sample"' in between("common.js", "const SET_TABS", ";")
    assert 'if (tab === "memory") api("/api/memory").then(showMemory)' in code and 'api("/api/memory/drop", { ids })' in code
    assert "내릴 수 없어요" in between("common.js", "function showMemory(d)", "\n}\n")
