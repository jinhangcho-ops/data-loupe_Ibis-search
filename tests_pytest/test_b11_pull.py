"""B11 데이터 받기·저장·갱신(plans/dash-blocks.md "B11 세부 명세"): 받기 설정 검사(dash_store.check_pull), 대시보드의 테이블별 필요(dash_pull.dash_needs),
빠진 기간(uncovered)·마지막 30줄 뒤 새 줄(new_rows), 칸 받기(pull_slot·ensure: 대시보드끼리 같이 쓰기, 이미 받은 줄은 다시 받지 않음), 이 PC DuckDB 계산(on_pc·compute, 연결 없이 포함),
암호화, [전체 갱신]·기간 다시 받기, 더는 안 쓰는 칸, 계산 줄의 테이블(calc.used_tables). "DB"는 메모리 DuckDB에 지어낸 표이고 held.remote를 바꿔 끼워 원격 DB처럼 다룬다."""
import pyarrow.parquet as pq
import pytest

from lazy import ibis
from core import calc
from core import dash_store
from core import held
from core import result
from core import server
from dashboard import dash_calc
from dashboard import dash_files
from dashboard import dash_keys
from dashboard import dash_pull
from dashboard import dash_refresh

PULL_ALL = {"calc": "pc", "every": None, "range": "all", "marks": [], "crypt": "none"}
NEW_ID = {**PULL_ALL, "range": "new", "marks": [{"schema": "", "table": "orders", "column": "id"}]}
ORDERS = [("", "orders")]
OCT = {"range": {"start": "2026-10-01", "end": "2026-10-03"}}


@pytest.fixture
def db(folder, tmp_path, monkeypatch):
    """원격 DB 대신 쓰는 메모리 DuckDB: orders(id, day, amount, cls, cid) 3줄, customers(cid, name) 2줄."""
    con = ibis.duckdb.connect()
    con.raw_sql("create table orders as select * from (values (1, date '2026-10-01', 10.0, 'a', 1), (2, date '2026-10-02', 20.0, 'b', 2), (3, date '2026-10-03', 5.0, 'a', 1)) v(id, day, amount, cls, cid)")
    con.raw_sql("create table customers as select * from (values (1, '가'), (2, '나')) v(cid, name)")
    monkeypatch.setitem(server.state, "con", con)
    monkeypatch.setitem(server.state, "opened", {"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "db.duckdb")})
    monkeypatch.setitem(server.state, "names", {})
    monkeypatch.setitem(server.state, "tables", {})
    monkeypatch.setattr(held, "remote", lambda c: c is con)  # 이 연결만 원격 DB로 본다(이 PC DuckDB는 아님)
    monkeypatch.setitem(dash_pull.FRESHED, "token", "")
    monkeypatch.setitem(dash_pull.FRESHED, "slots", set())
    monkeypatch.setattr(dash_keys, "PASS_ROUNDS", 1000)
    yield con
    dash_pull.close_pc()


def chart_card(card_id="c1", **body):
    """날짜 X축 합계 차트 카드(지금 연결 열쇠). body로 조인·조건 등을 더한다."""
    base = {"base": "orders", "joins": [], "periods": [], "conditions": [], "x": "day", "series": [{"y": "amount", "agg": "sum"}]}
    return dash_store.check_card({"id": card_id, "kind": "chart", "title": "합계", "view": {},
                                  "source": {**dash_store.current_source(), "body": {**base, **body}, "date": "day"}})


def dash_of(*cards, pull=PULL_ALL, dash_id="d1", filters=()):
    return {"id": dash_id, "name": dash_id, "cards": list(cards), "filters": list(filters), "pull": pull}


def spy_reads(monkeypatch):
    """받기가 DB 결과를 묶음으로 읽는 횟수(result.batches 부르기)를 센다(그대로 쓴다)."""
    calls = []
    real = result.batches
    monkeypatch.setattr(result, "batches", lambda expr, size=None: calls.append(1) or real(expr, size))
    return calls


def rows_of(files, table="orders"):
    """받은 조각들의 줄 수(암호화 없는 조각만)."""
    return sum(pq.read_metadata(p).num_rows for p in files[("", table)])


# ---------- dash_store.check_pull ----------

def test_check_pull_defaults_for_old_dashboards():
    assert dash_store.check_pull(None) == {"calc": "pc", "every": None, "range": "all", "marks": [], "crypt": "none"}
    first = dash_store.check_pull(None)
    first["marks"].append("x")
    assert dash_store.check_pull(None)["marks"] == []


def test_check_pull_keeps_valid_values():
    given = {"calc": "db", "every": {"n": 10, "unit": "s"}, "range": "new", "marks": [{"schema": "", "table": "orders", "column": "id"}], "crypt": "aes256"}
    assert dash_store.check_pull(given) == given
    assert dash_store.check_pull({"every": {"n": 168, "unit": "h"}})["every"] == {"n": 168, "unit": "h"}


@pytest.mark.parametrize("bad, words", [
    ("pc", "객체"), ({"calc": "cloud"}, "계산 방식"), ({"zip": True}, "쓸 수 없는 항목"),
    ({"every": {"n": 9, "unit": "s"}}, "10초 이상 168시 이하"), ({"every": {"n": 169, "unit": "h"}}, "10초 이상 168시 이하"),
    ({"every": {"n": 1.5, "unit": "m"}}, "1 이상의 정수"), ({"every": {"n": True, "unit": "m"}}, "1 이상의 정수"), ({"every": {"n": 3, "unit": "d"}}, "1 이상의 정수"),
    ({"range": "some"}, "받는 범위"), ({"crypt": "rot13"}, "암호화"), ({"marks": [{"table": "orders"}]}, "새 줄 컬럼"),
    ({"marks": [{"schema": "", "table": "t", "column": "a"}, {"schema": "", "table": "t", "column": "b"}]}, "두 번"),
])
def test_check_pull_rejects(bad, words):
    with pytest.raises(ValueError, match=words):
        dash_store.check_pull(bad)


def test_frame_actions_has_settings():
    assert dash_store.FRAME_ACTIONS["settings"] == ""


def test_dashboard_saves_pull_and_drops_old_periods(folder):
    saved = dash_store.save_dashboard({"name": "받기", "refresh": {"every": "1일"}, "live": {"every": "30초"}})
    assert saved["pull"] == dash_store.PULL_DEFAULT and "refresh" not in saved and "live" not in saved
    pull = {"calc": "db", "every": {"n": 5, "unit": "m"}, "range": "all", "marks": [], "crypt": "none"}
    assert dash_store.update_settings(saved["id"], {"pull": pull})["pull"] == pull
    with pytest.raises(ValueError, match="10초 이상"):
        dash_store.update_settings(saved["id"], {"pull": {"every": {"n": 1, "unit": "s"}}})
    assert not hasattr(dash_store, "MIN_REFRESH") and "refresh" not in dash_store.SETTING_KEYS


def test_rows_cards_are_gone_but_old_ones_still_load(folder):
    old = {"name": "옛것", "cards": [{"id": "r1", "kind": "recent_rows", "title": "최근 행", "data": {"columns": ["id"]}}]}
    saved = dash_store.save_dashboard(old)  # 옛 저장본은 열리고 저장된다
    assert saved["cards"][0]["kind"] == "recent_rows" and "recent_rows" in dash_store.GONE_KINDS and "recent_rows" not in dash_store.KINDS
    with pytest.raises(ValueError, match="더는 지원하지 않는 카드"):
        dash_calc.compute(saved["cards"][0], "2026-10-05T00:00:00")


# ---------- dash_refresh ----------

def test_check_pull_every_uses_connection_min_only_for_db(folder):
    remote = dash_store.source_key({"kind": "url", "url": "postgresql://h:5432/db"})
    cards = [{"source": {"key": remote, "label": "h/db"}}]
    dash_refresh.check_pull_every({"cards": cards, "pull": {**PULL_ALL, "every": {"n": 10, "unit": "s"}}})
    with pytest.raises(ValueError, match="1분 이상"):
        dash_refresh.check_pull_every({"cards": cards, "pull": {**PULL_ALL, "calc": "db", "every": {"n": 10, "unit": "s"}}})
    dash_refresh.check_pull_every({"cards": cards})


def test_old_refresh_and_live_code_is_gone():
    for name in ("start", "work", "status", "stop", "wait", "inside", "skip_reason", "every_seconds", "check_dashboard_every", "set_live_min", "live_mins",
                 "REFRESHABLE", "MIN_REFRESH", "LIVE_FILE", "JOB", "flush_gap"):
        assert not hasattr(dash_refresh, name), name


# ---------- 순수: uncovered · new_rows · calc.used_tables ----------

@pytest.mark.parametrize("window, covered, gaps", [
    (["2026-10-01", "2026-10-10"], [], [["2026-10-01", "2026-10-10"]]),
    (["2026-10-01", "2026-10-10"], [["2026-10-03", "2026-10-05"]], [["2026-10-01", "2026-10-02"], ["2026-10-06", "2026-10-10"]]),
    (["2026-10-04", "2026-10-05"], [["2026-10-03", "2026-10-05"]], []),
    (None, [["2026-10-03", "2026-10-05"]], [[None, "2026-10-02"], ["2026-10-06", None]]),
    (["2026-10-01", None], [[None, "2026-10-05"]], [["2026-10-06", None]]),
    (None, [[None, None]], []),
    (["2026-10-01", "2026-10-10"], [["2026-10-01", "2026-10-03"], ["2026-10-04", "2026-10-06"]], [["2026-10-07", "2026-10-10"]]),
])
def test_uncovered(window, covered, gaps):
    assert dash_pull.uncovered(window, covered) == gaps


def test_new_rows_after_the_remembered_tail():
    fetched = ibis.memtable({"id": [3, 4, 5, 6], "v": ["c", "d", "e", "f"]})
    tail = ibis.memtable({"id": [3, 4], "v": ["c", "d"]})
    assert dash_pull.new_rows(fetched, tail, ["id", "v"]).to_pyarrow().to_pydict() == {"id": [5, 6], "v": ["e", "f"]}
    assert dash_pull.new_rows(fetched, ibis.memtable({"id": [5, 6], "v": ["e", "f"]}), ["id", "v"]).count().to_pyarrow().as_py() == 0
    assert dash_pull.new_rows(fetched, ibis.memtable({"id": [3, 4], "v": ["c", "X"]}), ["id", "v"]) is None


def test_calc_used_tables_reads_group_lines_without_running():
    lines = ["그룹집계 count() 테이블 주문 키 고객ID=customer_id 이름 주문수", "금액2 = 금액 * 2", "그룹집계 sum(금액) 그룹 매장 이름 매장합", "그룹집계 count() 테이블 반품 키 id=id 이름 반품수"]
    assert calc.used_tables(lines) == ["주문", "반품"]
    assert calc.used_tables([]) == []


# ---------- dash_needs ----------

def test_needs_one_per_table_with_or_of_card_terms(db):
    a = chart_card("a", joins=["customers left cid=cid"], conditions=["amount >= 10", "name = 가"])
    b = chart_card("b", conditions=["cls = a"])
    needs = dash_pull.dash_needs(dash_of(a, b, filters=[{"column": "cls", "label": ""}]), OCT)
    key = a["source"]["key"]
    assert [(n["table"], n["columns"], n["terms"], n["window"], n["group"]) for n in needs] == [
        ("orders", ["amount", "cid", "cls", "day"], [{"lines": [], "conds": ["amount >= 10"], "date": "day"}, {"lines": [], "conds": ["cls = a"], "date": "day"}], ["2026-10-01", "2026-10-03"], "none"),
        ("customers", ["cid", "name"], [{"lines": [], "conds": ["name = 가"], "date": ""}], ["2026-10-01", "2026-10-03"], "none"),
    ]
    assert needs[0]["source"] == key and needs[0]["slot"] == dash_store.def_hash({"source": key, "schema": "", "table": "orders", "group": "none", "owner": ""})


def test_needs_left_join_keeps_null_keeping_conditions_on_pc(db):
    card = chart_card(joins=["customers left cid=cid"], conditions=["name is null"])
    customers = dash_pull.dash_needs(dash_of(card), None)[1]
    assert customers["terms"] == [{"lines": [], "conds": [], "date": ""}]  # 빈 값을 남기는 조건은 PC에서만(사용자 답 2-14)


def test_needs_new_mode_needs_a_good_mark(db):
    with pytest.raises(ValueError, match="새 줄을 알아볼 컬럼"):
        dash_pull.dash_needs(dash_of(chart_card(), pull={**PULL_ALL, "range": "new"}), None)
    with pytest.raises(ValueError, match="정수·날짜·시각"):
        dash_pull.dash_needs(dash_of(chart_card(), pull={**PULL_ALL, "range": "new", "marks": [{"schema": "", "table": "orders", "column": "cls"}]}), None)
    assert dash_pull.dash_needs(dash_of(chart_card(), pull=NEW_ID), None)[0]["mark"] == "id"


# ---------- pull_slot · ensure ----------

def test_ensure_pulls_once_per_token_and_all_mode_replaces(db, monkeypatch):
    reads = spy_reads(monkeypatch)
    dash = dash_of(chart_card(joins=["customers left cid=cid"], conditions=["amount >= 10"]))
    tables = [("", "orders"), ("", "customers")]
    files = dash_pull.ensure(dash, None, "", tables)
    assert set(files) == set(tables) and rows_of(files) == 2 and len(reads) == 2
    dash_pull.ensure(dash, None, "", tables)
    assert len(reads) == 2  # 이미 받은 것
    dash_pull.ensure(dash, None, "t1", tables)
    files = dash_pull.ensure(dash, None, "t1", tables)
    assert len(reads) == 4 and len(files[("", "orders")]) == 1  # 갱신 한 번에 칸마다 한 번, 전부 다시 받기는 바꿔 넣는다


def test_two_dashboards_share_one_slot_without_duplicates(db):
    one = dash_of(chart_card(conditions=["amount >= 10"]), dash_id="d1")
    two = dash_of(chart_card(conditions=["cls = a"]), dash_id="d2")
    first = dash_pull.ensure(one, None, "", ORDERS)
    second = dash_pull.ensure(two, None, "", ORDERS)
    slot = first[("", "orders")][0].parent.name
    assert second[("", "orders")][0].parent.name == slot and sorted(dash_files.read_manifest(slot)["users"]) == ["d1", "d2"]
    assert rows_of(second) == 3  # d1이 받은 1·2번 줄에 d2만 쓰는 3번 줄만 더 받는다(1번은 두 번 받지 않는다)


def test_different_crypt_does_not_share(db):
    one = dash_pull.ensure(dash_of(chart_card(), dash_id="d1"), None, "", ORDERS)
    two = dash_pull.ensure(dash_of(chart_card(), dash_id="d2", pull={**PULL_ALL, "crypt": "aes128"}), None, "", ORDERS)
    assert one[("", "orders")][0].parent != two[("", "orders")][0].parent  # 암호화 설정이 같은 대시보드끼리만(사용자 답 2-7b)


def test_window_change_fetches_only_missing_rows(db, monkeypatch):
    dash = dash_of(chart_card(), pull=NEW_ID)
    files = dash_pull.ensure(dash, {"range": {"start": "2026-10-02", "end": "2026-10-03"}}, "", ORDERS)
    assert rows_of(files) == 2
    files = dash_pull.ensure(dash, OCT, "", ORDERS)
    assert rows_of(files) == 3 and len(files[("", "orders")]) == 2  # 이미 받은 기간은 두고 10-01만 더 받는다


def test_new_mode_refresh_adds_rows_after_the_last_thirty(db):
    dash = dash_of(chart_card(), pull=NEW_ID)
    dash_pull.ensure(dash, None, "a", ORDERS)
    db.raw_sql("insert into orders values (4, date '2026-10-04', 7.0, 'b', 2)")
    files = dash_pull.ensure(dash, None, "b", ORDERS)
    assert rows_of(files) == 4
    assert len(dash_pull.ensure(dash, None, "c", ORDERS)[("", "orders")]) == len(files[("", "orders")])  # 새 줄이 없으면 조각을 더하지 않는다


def test_lost_tail_is_an_error_and_a_range_can_be_fetched(db):
    dash = dash_of(chart_card(), pull=NEW_ID)
    files = dash_pull.ensure(dash, None, "a", ORDERS)
    slot = files[("", "orders")][0].parent.name
    db.raw_sql("update orders set amount = 99 where id = 3")  # 기억한 마지막 줄이 고쳐졌다
    with pytest.raises(ValueError, match="마지막으로 받은 줄을 DB에서 찾지 못했어요"):
        dash_pull.ensure(dash, None, "b", ORDERS)
    assert dash_files.read_manifest(slot)["lost"] == "day"  # 기간 컬럼이 하나면 그 이름(알림에서 기간을 정해 받는다)
    dash_pull.refetch_range(dash, slot, "2026-10-03", "2026-10-03")
    man = dash_files.read_manifest(slot)
    assert man["lost"] is None and man["rows"] == 3


def test_full_on_a_shared_slot_moves_this_dashboard_to_its_own_slot(db):
    one = dash_of(chart_card(conditions=["amount >= 10"]), dash_id="d1")
    two = dash_of(chart_card(conditions=["cls = a"]), dash_id="d2")
    dash_pull.ensure(one, None, "", ORDERS)
    shared = dash_pull.ensure(two, None, "", ORDERS)[("", "orders")][0].parent.name
    db.raw_sql("update orders set amount = 50.0 where id = 3")  # 조건 경계를 넘는 변경(5 → 50)
    own = dash_pull.full(one, None)
    assert len(own) == 1 and own[0] != shared  # d1은 그때부터 자기 칸을 따로(사용자 답 4-2)
    assert list(dash_files.read_manifest(shared)["users"]) == ["d2"] and list(dash_files.read_manifest(own[0])["users"]) == ["d1"]
    files = dash_pull.ensure(one, None, "", ORDERS)
    assert files[("", "orders")][0].parent.name == own[0]
    with dash_pull.on_pc(files, "d1") as local:
        rows = local.table("orders").select("day", "amount").order_by("day").to_pyarrow().to_pydict()
    assert [str(d) for d in rows["day"]] == ["2026-10-01", "2026-10-02", "2026-10-03"] and [float(a) for a in rows["amount"]] == [10.0, 20.0, 50.0]  # 겹침·빠짐 없이 새 값


def test_full_on_an_unshared_slot_refetches_it(db):
    one = dash_of(chart_card(), dash_id="d1")
    slot = dash_pull.ensure(one, None, "", ORDERS)[("", "orders")][0].parent.name
    assert dash_pull.full(one, None) == [slot]  # 같이 쓰는 대시보드가 없으면 그 칸을 전부 다시


def test_full_refetches_every_slot(db, monkeypatch):
    reads = spy_reads(monkeypatch)
    dash = dash_of(chart_card(joins=["customers left cid=cid"]), pull=NEW_ID)
    dash_pull.ensure(dash, None, "", [("", "orders"), ("", "customers")])
    assert len(dash_pull.full(dash, None)) == 2 and len(reads) == 4


def test_over_the_limit_is_always_an_error(db, monkeypatch):
    from core import scan_size
    monkeypatch.setattr(scan_size, "limit_check", lambda *a, **k: {"over": True, "unknown": False, "blocked": False, "text": "너무 커요"})
    with pytest.raises(ValueError, match="너무 커요"):
        dash_pull.ensure(dash_of(chart_card()), None, "", ORDERS)


# ---------- on_pc · compute ----------

def test_compute_on_pc_matches_db(db):
    card = chart_card(conditions=["amount >= 10"])
    saved = dash_store.save_dashboard({"name": "받기", "cards": [card]})
    now = "2026-10-05T00:00:00"
    assert dash_pull.compute(card, now, None, saved["id"], "")["by_unit"] == dash_calc.compute_filtered(card, now, None)["by_unit"]
    assert server.state["con"] is db and "pc" not in server.state
    assert dash_pull.uses_pc(saved["id"]) and not dash_pull.uses_pc("")


def test_one_pc_connection_while_the_dashboard_is_open(db):
    card = chart_card()
    saved = dash_store.save_dashboard({"name": "받기", "cards": [card]})
    dash_pull.compute(card, "2026-10-05T00:00:00", None, saved["id"], "")
    first = dash_pull.OPEN["con"]
    dash_pull.compute(card, "2026-10-05T00:00:00", None, saved["id"], "")
    assert first is not None and dash_pull.OPEN["con"] is first and dash_pull.OPEN["dash"] == saved["id"]  # 카드마다 열고 닫지 않는다(사용자 답 2-19)
    dash_pull.close_pc()
    assert dash_pull.OPEN["con"] is None


def test_compute_without_connection_marks_missing_period(db, monkeypatch):
    card = chart_card()
    saved = dash_store.save_dashboard({"name": "받기", "cards": [card]})
    dash_pull.compute(card, "2026-10-05T00:00:00", {"range": {"start": "2026-10-02", "end": "2026-10-03"}}, saved["id"], "")
    monkeypatch.setitem(server.state, "con", None)
    got = dash_pull.compute(card, "2026-10-05T00:00:00", OCT, saved["id"], "")
    assert dash_store.GAP_NOTE in (got.get("note") or "")  # 받아 둔 만큼만 계산하고 알린다(사용자 답 2-12)


def test_on_pc_restores_connection_on_error(db):
    files = dash_pull.ensure(dash_of(chart_card()), None, "", ORDERS)
    with pytest.raises(RuntimeError), dash_pull.on_pc(files, "d1") as local:
        assert server.state["con"] is local and local.table("orders").count().execute() == 3
        raise RuntimeError("멈춤")
    assert server.state["con"] is db and "pc" not in server.state


def test_virtual_tables_hidden_on_pc(db, monkeypatch):
    from core import sql_exec
    monkeypatch.setattr(sql_exec, "virtual", lambda key: {"v1": "select 1"})
    files = dash_pull.ensure(dash_of(chart_card()), None, "", ORDERS)
    assert server.virtual_tables() != {}
    with dash_pull.on_pc(files, "d1"):
        assert server.virtual_tables() == {}


def test_connection_key_on_pc_without_connection(db, monkeypatch):
    files = dash_pull.ensure(dash_of(chart_card()), None, "", ORDERS)
    monkeypatch.setitem(server.state, "con", None)
    monkeypatch.setitem(server.state, "opened", None)
    with dash_pull.on_pc(files, "d1"):
        assert server.connection_key().startswith("pc:")  # 연결 없이도 계산 줄의 통계 캐시 열쇠를 만든다(사용자 답 3-12)
    with pytest.raises(ValueError, match="먼저 연결"):
        server.connection_key()


def test_pc_connection_closes_after_idle(db, monkeypatch):
    from core import settings
    files = dash_pull.ensure(dash_of(chart_card()), None, "", ORDERS)
    with dash_pull.on_pc(files, "d1"):
        pass
    assert dash_pull.OPEN["con"] is not None
    monkeypatch.setattr(settings, "get", lambda name: 1 if name == "pc_idle" else settings.DEFAULTS.get(name))
    monkeypatch.setitem(dash_pull.OPEN, "last", dash_pull.OPEN["last"] - 61)
    dash_pull.close_idle()
    assert dash_pull.OPEN["con"] is None  # 1분 넘게 쓰지 않았으면 닫는다(사용자 답 3-4)


def test_pc_idle_setting():
    from core import settings
    assert settings.DEFAULTS["pc_idle"] == 10 and settings.valid("pc_idle", 120) and not settings.valid("pc_idle", 0) and not settings.valid("pc_idle", 121)  # 1~120분(사용자 답 4-1)


def test_encrypted_parts_need_the_key(db):
    dash = dash_of(chart_card(), pull={**PULL_ALL, "crypt": "aes256"})
    files = dash_pull.ensure(dash, None, "", ORDERS)
    with pytest.raises(Exception):
        pq.read_table(files[("", "orders")][0])
    with dash_pull.on_pc(files, "d1") as local:
        assert local.table("orders").count().execute() == 3


def test_rewrite_crypt_moves_rows_to_the_new_group(db):
    saved = dash_store.save_dashboard({"name": "받기", "cards": [chart_card()]})
    dash_pull.ensure(dash_store.get_dashboard(saved["id"]), None, "", ORDERS)
    old_keys = {s: None for s in dash_files.slots(saved["id"])}  # 암호화 없음의 열쇠는 None
    dash_store.update_settings(saved["id"], {"pull": {**PULL_ALL, "crypt": "aes128"}})
    new = dash_pull.rewrite_crypt(dash_store.get_dashboard(saved["id"]), old_keys)
    assert len(new) == 1 and dash_files.read_manifest(new[0])["crypt"]["group"] == "aes128" and dash_files.read_manifest(new[0])["rows"] == 3


# ---------- card_tables · unused · mark_tables ----------

def test_card_tables_include_joins_and_calc_tables(db):
    card = chart_card(joins=["customers left cid=cid"])
    assert dash_pull.card_tables(card) == [("", "orders"), ("", "customers")]


def test_unused_lists_tables_no_card_reads(db):
    joined = chart_card(joins=["customers left cid=cid"])
    dash_pull.ensure(dash_of(joined), None, "", [("", "orders"), ("", "customers")])
    assert dash_pull.unused(dash_of(joined)) == []
    left = dash_pull.unused(dash_of(chart_card()))
    assert [(u["schema"], u["table"], u["shared"]) for u in left] == [("", "customers", False)] and left[0]["bytes"] > 0


def test_mark_tables_lists_int_and_date_columns(db):
    assert dash_pull.mark_tables(dash_of(chart_card(joins=["customers left cid=cid"]))) == [
        {"schema": "", "table": "orders", "columns": ["id", "day", "cid"], "join": False},
        {"schema": "", "table": "customers", "columns": ["cid"], "join": True},
    ]
