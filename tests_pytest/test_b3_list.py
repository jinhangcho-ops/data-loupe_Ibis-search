"""B3 목록 카드(plans/dash-blocks.md "B3 세부 명세 (2차)"): 목록 카드 종류(kind list)와 그 설정 검사(dash_store.check_list), 값 구하기(dash_calc.list_data: 사용자 정렬·줄 수),
줄 고르기의 거를 컬럼을 받는 쪽이 받아 둠(linked_columns), 단추 요소의 할 일 "목록을 CSV로 저장"(FRAME_ACTIONS csv). 데이터는 시험 안에서 지어낸다."""
import duckdb
import pytest

from core import dash_store
from core import server
from dashboard import dash_calc

ROWS = [("A", "x", 10.0), ("A", "y", 20.0), ("B", "x", 5.0), ("B", "y", 50.0), ("C", "x", 1.0)]  # store, kind, amt → 합계: A 30, B 55, C 1
SUM = [{"y": "amt", "agg": "sum"}]
SUM_NAME = "합계 (amt)"  # dash_calc.series_name(SUM[0])
DEFAULT_LIST = {"parts": [], "pick": "none", "key": "", "swaps": [], "report": "screen"}
PART = {"kind": "bar", "column": "amt", "swap": False, "format": "number", "rules": [], "basis": "max", "side": "start"}


@pytest.fixture
def db(folder, tmp_path):
    from dashboard import dash_app  # noqa: F401  대시보드 라우트
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "f.duckdb"))
    raw.execute("create table orders (store varchar, kind varchar, amt double)")
    raw.executemany("insert into orders values (?, ?, ?)", ROWS)
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "f.duckdb")}).status_code == 200
    yield c
    c.post("/api/disconnect", json={})


def list_card(card_id="l1", groups=("store",), series=SUM, **view):
    return {"id": card_id, "kind": "list", "title": "목록", "source": {"schema": "", "table": "orders", "body": {"base": "orders", "series": list(series)}},
            "view": {"group_cols": list(groups), **view}}


# ---------- check_list ----------

def test_check_list_defaults():
    assert dash_store.check_list({}, []) == {"list": DEFAULT_LIST, "list_sort": {"column": "", "desc": False}, "list_rows": dash_store.LIST_ROWS_DEFAULT}
    found = dash_store.check_list({"list": {"parts": [{"kind": "bar", "column": " amt "}]}, "list_sort": {"column": SUM_NAME, "desc": True}, "list_rows": 3}, ["store"])
    assert found["list"]["parts"] == [PART]  # 없는 항목은 처음 값
    assert found["list_sort"] == {"column": SUM_NAME, "desc": True} and found["list_rows"] == 3


def test_check_list_rules():
    ok = dash_store.check_list({"list": {"parts": [{"kind": "tag", "column": "", "swap": True, "rules": [{"match": "*B*", "color": "#e03131"}], "side": "end"}],
                                         "pick": "many", "key": "store", "swaps": [{"label": " 합계 ", "value": SUM_NAME}, {"label": "개수", "value": "개수"}], "report": "table"}}, ["store"])
    assert ok["list"]["swaps"][0] == {"label": "합계", "value": SUM_NAME} and ok["list"]["parts"][0]["rules"] == [{"match": "*B*", "color": "#e03131"}]
    bad = [
        ({"list": "x"}, "list"),
        ({"list": {"color": 1}}, "쓸 수 없는 항목"),
        ({"list": {"parts": [{"kind": "pie", "column": "a"}]}}, "kind"),
        ({"list": {"parts": [{"kind": "text", "column": "a", "size": 1}]}}, "쓸 수 없는 항목"),
        ({"list": {"parts": [{"kind": "text", "column": "a"}] * (dash_store.MAX_LIST_PARTS + 1)}}, "parts"),
        ({"list": {"parts": [{"kind": "text", "column": ""}]}}, "컬럼"),  # 값 바꾸기를 따르지 않는 부분은 컬럼을 꼭 고른다
        ({"list": {"parts": [{"kind": "text", "column": "", "swap": True}]}}, "값 바꾸기"),  # 단추가 없는데 따르는 부분
        ({"list": {"parts": [{"kind": "bar", "column": "a", "basis": "avg"}]}}, "basis"),
        ({"list": {"parts": [{"kind": "text", "column": "a", "rules": [{"match": "x", "color": "red"}]}]}}, "색"),
        ({"list": {"pick": "all"}}, "pick"),
        ({"list": {"pick": "one", "key": "kind"}}, "묶음 컬럼"),  # 거를 컬럼은 묶음 컬럼 가운데
        ({"list": {"pick": "one"}}, "묶음 컬럼"),
        ({"list": {"swaps": [{"label": "", "value": "a"}]}}, ""),
        ({"list": {"report": "pdf"}}, "report"),
        ({"list_sort": {"column": "a", "desc": "yes"}}, "desc"),
        ({"list_rows": 0}, "list_rows"),
        ({"list_rows": dash_store.MAX_LIST_ROWS + 1}, "list_rows"),
    ]
    for view, word in bad:
        with pytest.raises(ValueError, match=word):
            dash_store.check_list(view, ["store"])


# ---------- 카드 검사 ----------

def test_list_card_check():
    assert "list" in dash_store.KINDS and dash_store.KINDS.index("list") == dash_store.KINDS.index("table") + 1
    assert "list" in dash_store.SENDER_KINDS
    saved = dash_store.check_card(list_card(list={"pick": "one", "key": "store"}, list_rows=5, link_to=["c2"]))
    assert saved["view"]["list"] == {**DEFAULT_LIST, "pick": "one", "key": "store"} and saved["view"]["list_rows"] == 5 and saved["view"]["link_to"] == ["c2"]
    assert dash_store.check_card(list_card())["view"]["list"] == DEFAULT_LIST  # 설정이 없어도 처음 값이 들어간다
    with pytest.raises(ValueError, match="목록 카드에만"):
        dash_store.check_card({**list_card(list={}), "kind": "table"})


def test_csv_button_action():
    assert dash_store.FRAME_ACTIONS["csv"] == "card"
    assert dash_store.check_frame({"label": "가", "action": "csv", "arg": "l1"}, "button")["arg"] == "l1"
    with pytest.raises(ValueError, match="카드 id"):
        dash_store.check_frame({"label": "가", "action": "csv"}, "button")


# ---------- list_data ----------

def test_list_data_sort_and_rows(db):
    def values(**view):
        return dash_calc.list_data(list_card(**view), None)["values"]
    top = values(list_sort={"column": SUM_NAME, "desc": True}, list_rows=2)
    assert top["columns"] == [{"name": "store", "kind": "group"}, {"name": SUM_NAME, "kind": "value", "agg": "sum"}]
    assert top["data"] == [["B", "A"], [55.0, 30.0]] and top["more"] is True  # 컬럼별 값, 정한 줄 수까지
    assert values(list_sort={"column": "", "desc": False}, list_rows=10)["data"] == [["A", "B", "C"], [30.0, 55.0, 1.0]]  # 비우면 묶음 컬럼 순
    assert values(list_sort={"column": "store", "desc": True}, list_rows=10)["data"][0] == ["C", "B", "A"]
    assert values(list_rows=10)["more"] is False


def test_list_data_shapes(db):
    plain = dash_calc.list_data(list_card(series=(), list_sort={"column": "", "desc": False}, list_rows=10), None)["values"]
    assert plain["columns"] == [{"name": "store", "kind": "group"}] and plain["data"] == [["A", "B", "C"]]  # 값 없이 묶음만: 서로 다른 값
    whole = dash_calc.list_data(list_card(groups=(), list_sort={"column": "", "desc": False}, list_rows=10), None)["values"]
    assert whole["data"] == [[86.0]]  # 묶음 없이 값만: 한 줄
    with pytest.raises(ValueError, match="하나 이상"):
        dash_calc.list_data(list_card(groups=(), series=(), list_rows=10), None)
    with pytest.raises(ValueError, match="정렬 컬럼"):
        dash_calc.list_data(list_card(list_sort={"column": "nope", "desc": False}, list_rows=10), None)


# ---------- linked_columns ----------

def test_linked_columns_from_list_key():
    other = {"id": "c2", "kind": "chart", "view": {}, "source": {"body": {"base": "shops", "x": "area", "series": []}}}
    picked = {**DEFAULT_LIST, "pick": "many", "key": "store"}
    dash = {"cards": [list_card(list=picked, link_to=["c2"], link_map={"c2": {"store": "area"}}), other]}
    assert dash_calc.linked_columns(dash, other) == {"area"}  # 맞춘 상대 컬럼
    assert dash_calc.linked_columns({"cards": [list_card(list=picked, link_to=["c2"]), other]}, other) == {"store"}  # 안 고르면 같은 이름
    assert dash_calc.linked_columns({"cards": [list_card(list={**picked, "pick": "none"}, link_to=["c2"]), other]}, other) == set()  # 고르기를 안 하면 보내지 않는다
