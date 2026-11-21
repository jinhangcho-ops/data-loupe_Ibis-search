"""B11 라우트(plans/dash-blocks.md "B11 세부 명세"): 받은 데이터 상태·암호화 바꾸기·[전체 갱신]·기간 다시 받기·주인 없는 칸·더는 안 쓰는 칸, 카드 구하기·필터 값의 dash(이 PC 계산),
대시보드 지우기와 받은 데이터, 상수(dash.pull), 없앤 라우트(최신화·실시간·원래 행 조회). Flask 시험 클라이언트라 스켈레톤 오류가 응답으로 바뀌므로 xfail로 둔다."""
import pytest

from lazy import ibis
from core import dash_store
from core import held
from core import server
from dashboard import dash_files
from dashboard import dash_keys
from dashboard import dash_pull


@pytest.fixture
def remote(client, tmp_path, monkeypatch):
    """client 위에 원격 DB처럼 다루는 메모리 DuckDB(orders 3줄)를 끼운다."""
    con = ibis.duckdb.connect()
    con.raw_sql("create table orders as select * from (values (1, date '2026-10-01', 10.0, 'a'), (2, date '2026-10-02', 20.0, 'b'), (3, date '2026-10-03', 5.0, 'a')) v(id, day, amount, cls)")
    server.state.update(con=con, opened={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "db.duckdb")})
    monkeypatch.setattr(held, "remote", lambda c: c is con)
    monkeypatch.setitem(dash_pull.FRESHED, "token", "")
    monkeypatch.setitem(dash_pull.FRESHED, "slots", set())
    monkeypatch.setattr(dash_keys, "PASS_ROUNDS", 1000)
    yield client
    dash_pull.close_pc()


def card(kind="chart"):
    return {"id": "c1", "kind": kind, "title": "합계", "view": {}, "data": {},
            "source": {**dash_store.current_source(), "date": "day", "body": {"base": "orders", "joins": [], "periods": [], "conditions": [], "x": "day", "series": [{"y": "amount", "agg": "sum"}]}}}


def saved_with_card(**more):
    return dash_store.save_dashboard({"name": "받기", "cards": [card()], **more})


def pulled(client, dash_id):
    client.post("/api/dash/card/compute", json={"card": card(), "ctx": None, "dash": dash_id})


def test_pull_state_is_free(remote):
    saved = saved_with_card()
    assert remote.get("/api/dash/pull/state?id=" + saved["id"]).get_json() == {"bytes": 0, "text": "0 B", "slots": []}
    pulled(remote, saved["id"])
    got = remote.get("/api/dash/pull/state?id=" + saved["id"]).get_json()
    assert got["bytes"] > 0 and [(s["table"], s["shared"], s["lost"]) for s in got["slots"]] == [("orders", False, None)]
    assert remote.get("/api/dash/pull/state?id=없음").status_code == 400


def test_bell_read_marks_are_kept_only_for_current_alerts(client):
    assert "/api/dash/bell" in server.FREE
    assert client.get("/api/dash/bell").get_json() == {"read": []}
    got = client.post("/api/dash/bell", json={"read": ["orphan:a", "lost:b:2026"], "current": ["orphan:a", "lost:b:2026", "unused:d:c"]}).get_json()
    assert got == {"read": ["orphan:a", "lost:b:2026"]}
    got = client.post("/api/dash/bell", json={"read": [], "current": ["orphan:a"]}).get_json()
    assert got == {"read": ["orphan:a"]}  # 해결돼 사라진 알림(lost:b)의 기록은 지운다
    assert client.get("/api/dash/bell").get_json() == {"read": ["orphan:a"]}
    assert client.post("/api/dash/bell", json={"read": "x", "current": []}).status_code == 400


def test_pull_crypt_rewrites_or_repulls(remote):
    saved = saved_with_card()
    pulled(remote, saved["id"])
    got = remote.post("/api/dash/pull/crypt", json={"id": saved["id"], "crypt": "pass", "pass": "봄비 내리는 밤", "how": "rewrite"})
    assert "봄비" not in got.get_data(as_text=True) and got.get_json()["dashboard"]["pull"]["crypt"] == "pass"
    assert [m["crypt"]["group"].startswith("pass:") for m in dash_files.slots(saved["id"]).values()] == [True]
    assert remote.post("/api/dash/pull/crypt", json={"id": saved["id"], "crypt": "none", "pass": "", "how": "pull"}).get_json()["dashboard"]["pull"]["crypt"] == "none"


def test_delete_dashboard_keeps_slots_others_use(remote):
    one, two = saved_with_card(), dash_store.save_dashboard({"name": "둘", "cards": [card()]})
    pulled(remote, one["id"])
    pulled(remote, two["id"])
    assert remote.post("/api/dash/delete", json={"id": one["id"]}).status_code == 200
    assert list(dash_files.slots(two["id"])) and not dash_files.slots(one["id"])
    remote.post("/api/dash/delete", json={"id": two["id"]})
    assert dash_files.all_slots() == {}


def test_orphans_are_asked_then_dropped(client):
    dash_files.write_manifest("0123456789abcdef", {"v": dash_files.VERSION, "source": "k", "schema": "", "table": "t", "virtual": False, "crypt": {"group": "none", "salt": ""},
                                                   "columns": None, "held": [], "users": {"gone": {"columns": None, "terms": [], "window": None, "mark": ""}}, "parts": [], "rows": 0, "pulled_at": "", "lost": None})
    got = client.get("/api/dash/pull/orphans").get_json()
    assert got["orphans"] == [{"slot": "0123456789abcdef", "schema": "", "table": "t", "bytes": 0}] and got["text"] == "0 B"
    assert client.post("/api/dash/pull/orphans/drop", json={}).get_json() == {"dropped": ["0123456789abcdef"]}


def test_card_compute_pulls_for_pc_dashboard(remote):
    saved = saved_with_card()
    got = remote.post("/api/dash/card/compute", json={"card": card(), "ctx": None, "dash": saved["id"], "fresh": "t1"}).get_json()
    assert got["data"]["by_unit"]["day"]["labels"] == ["2026-10-01", "2026-10-02", "2026-10-03"] and dash_files.size_of(saved["id"]) > 0
    assert remote.post("/api/dash/card/compute", json={"card": card(), "ctx": None}).get_json()["data"]["by_unit"] == got["data"]["by_unit"]


def test_cards_compute_pulls_for_pc_dashboard(remote):
    saved = saved_with_card()
    got = remote.post("/api/dash/cards/compute", json={"cards": [card()], "ctx": None, "dash": saved["id"], "fresh": "t2"}).get_json()
    assert [r.get("error") for r in got["results"]] == [None] and dash_files.size_of(saved["id"]) > 0


def test_filter_values_come_from_held_files(remote):
    saved = saved_with_card(filters=[{"column": "cls", "label": ""}])  # 필터 컬럼은 받을 컬럼에 든다(dash_needs)
    pulled(remote, saved["id"])
    server.state.update(con=None)
    got = remote.post("/api/dash/filter/values", json={"id": saved["id"], "column": "cls"}).get_json()
    assert got.get("values") == ["a", "b"], got


def test_pull_full_refetches(remote):
    saved = saved_with_card()
    pulled(remote, saved["id"])
    assert len(remote.post("/api/dash/pull/full", json={"id": saved["id"], "ctx": None}).get_json()["slots"]) == 1


def test_unused_is_listed_then_dropped_only_when_still_unused(remote):
    saved = saved_with_card()
    pulled(remote, saved["id"])
    slot = next(iter(dash_files.slots(saved["id"])))
    assert remote.post("/api/dash/pull/unused", json={"id": saved["id"]}).get_json() == {"unused": []}
    assert remote.post("/api/dash/pull/unused/drop", json={"id": saved["id"], "slots": [slot]}).get_json() == {"dropped": [], "kept": [slot]}
    dash_store.save_dashboard({**dash_store.get_dashboard(saved["id"]), "cards": []})
    assert [u["slot"] for u in remote.post("/api/dash/pull/unused", json={"id": saved["id"]}).get_json()["unused"]] == [slot]
    assert remote.post("/api/dash/pull/unused/drop", json={"id": saved["id"], "slots": [slot]}).get_json() == {"dropped": [slot], "kept": []}


def test_pull_tables_lists_mark_candidates(remote):
    saved = saved_with_card()
    got = remote.post("/api/dash/pull/tables", json={"id": saved["id"]}).get_json()
    assert got == {"remote": True, "tables": [{"schema": "", "table": "orders", "columns": ["id", "day"], "join": False}]}


def test_constants_have_pull_and_no_min_refresh(client):
    dash = client.get("/api/dash/constants").get_json()["dash"]
    assert dash["pull"] == {"calcs": ["pc", "db"], "ranges": ["all", "new"], "crypts": ["none", "aes128", "aes256", "pass"], "units": {"s": 1, "m": 60, "h": 3600},
                            "unit_names": {"s": "초", "m": "분", "h": "시"}, "min_every": 10, "max_every": 604800,
                            "default": {"calc": "pc", "every": None, "range": "all", "marks": [], "crypt": "none"}, "gap_note": "데이터가 없는 구간이 있어요."}
    assert dash["gone_kinds"] == ["recent_rows"] and "더는 지원하지 않는 카드" in dash["gone_text"]
    assert "min_refresh" not in dash


def test_old_routes_are_gone(client):
    rules = {r.rule for r in server.app.url_map.iter_rules()}
    assert not {"/api/dash/refresh/start", "/api/dash/refresh/status", "/api/dash/refresh/stop", "/api/period/every", "/api/dash/card/filtered"} & rules
    assert client.post("/api/dash/live_min", json={"seconds": 5}).status_code == 405
    assert client.get("/api/dash/live_min").get_json() == {"source": None, "seconds": 0}
