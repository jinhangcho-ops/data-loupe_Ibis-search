"""B9 줄 표시(plans/dash-blocks.md "B9 세부 명세"): 목록 카드의 표시 칸 설정 검사(dash_store.check_row_marks), 표시 세기 요소·할 일(check_frame),
표시 파일 읽고 쓰기·암호화·다시 쓰기·내보내기·가져오기·주인 없는 표시(dashboard.dash_marks), 열쇠 등록(dash_pull.parquet_key),
목록 값에 표시 붙이기·표시로 거르기·세기(dash_calc.list_data), 지금 데이터의 열쇠(list_keys), 라우트. 데이터는 시험 안에서 지어낸다(주문·매장).
xfail(strict): 기존 함수를 고쳐야 통과하는 시험. reason의 절이 끝나면 그 절의 strict-coder가 표시를 지운다."""
import base64

import duckdb
import polars as pl
import pyarrow.parquet as pq
import pytest

from lazy import ibis
from core import dash_store
from core import result
from core import server
from dashboard import dash_calc
from dashboard import dash_keys
from dashboard import dash_marks
from dashboard import dash_pull

ROWS = [("A", "x", 10.0), ("A", "y", 20.0), ("B", "x", 5.0), ("B", "y", 50.0), ("C", "x", 1.0)]  # store, kind, amt → 합계: A 30, B 55, C 1
SUM = [{"y": "amt", "agg": "sum"}]
SUM_NAME = "합계 (amt)"  # dash_calc.series_name(SUM[0])
FIELDS = [{"id": "f1", "name": "단계", "kind": "choice", "choices": ["할 일", "끝"]}, {"id": "f2", "name": "메모", "kind": "text", "choices": []},
          {"id": "f3", "name": "배송일", "kind": "date", "choices": []}, {"id": "f4", "name": "점수", "kind": "number", "choices": []}]
MARKS = {"keys": ["store"], "fields": FIELDS, "edit": "cell", "clash": "keep"}
NAMES = [dash_marks.KEY_COL, "단계", "메모", "배송일", "점수"]
DASH = {"id": "d1", "pull": None}  # 저장하지 않은 대시보드 모양: 암호화 없음(PULL_DEFAULT)
KEY = dash_marks.KEY_COL
KA, KB, KC = '{"0":"A"}', '{"0":"B"}', '{"0":"C"}'  # 열쇠 ["store"]의 열쇠 글자(dash_marks.key_expr)
KAX, KAY = '{"0":"A","1":"x"}', '{"0":"A","1":"y"}'  # 열쇠 ["store", "kind"]


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


def list_card(card_id="l1", marks=MARKS, series=SUM, groups=("store",), **view):
    return {"id": card_id, "kind": "list", "title": "목록", "source": {"schema": "", "table": "orders", "body": {"base": "orders", "series": list(series)}},
            "view": {"group_cols": list(groups), **({"row_marks": marks} if marks is not None else {}), **view}}


TWO = {**MARKS, "keys": ["store", "kind"]}  # 묶음 둘·열쇠 둘


# ---------- dash_marks.key_expr ----------

def test_key_expr():
    frame = pl.DataFrame({"store": ["A", 'B"', None], "kind": ["x", "y,z", "w"]})
    assert frame.select(dash_marks.key_expr(["store", "kind"]).alias("k"))["k"].to_list() == [KAX, '{"0":"B\\"","1":"y,z"}', '{"0":null,"1":"w"}']
    assert frame.select(dash_marks.key_expr(["store"]).alias("k"))["k"].to_list()[0] == KA  # 컬럼 하나도 같은 규칙
    tricky = pl.DataFrame({"a": ['x","1":"y', "x"], "b": ["z", '","1":"y'], "c": ["", ""]})  # 값에 구분 글자가 있어도 서로 다른 줄은 다른 열쇠
    got = tricky.select(dash_marks.key_expr(["a", "b"]).alias("k"))["k"].to_list()
    assert got[0] != got[1]
    with pytest.raises(ValueError, match="열쇠 컬럼"):
        dash_marks.key_expr([])


# ---------- dash_store.check_row_marks ----------

def test_check_row_marks_defaults():
    assert dash_store.check_row_marks({}, ["store"]) == {}  # 없으면 키를 만들지 않는다(옛 목록의 def_hash 그대로)
    assert dash_store.ROW_MARKS_DEFAULT == {"keys": [], "fields": [], "edit": "cell", "clash": "keep"}
    empty = dash_store.check_row_marks({"row_marks": {}}, [])
    assert empty == {"row_marks": dash_store.ROW_MARKS_DEFAULT} and empty["row_marks"] is not dash_store.ROW_MARKS_DEFAULT
    assert empty["row_marks"]["keys"] is not dash_store.ROW_MARKS_DEFAULT["keys"]
    got = dash_store.check_row_marks({"row_marks": {"keys": ["store"], "fields": [{"id": "f1", "name": " 단계 ", "kind": "choice", "choices": [" 할 일 ", "끝"]},
                                                                                  {"id": "f2", "name": "메모", "kind": "text"}], "edit": "pop", "clash": "fill"}}, ["store"])
    assert got == {"row_marks": {"keys": ["store"], "fields": [{"id": "f1", "name": "단계", "kind": "choice", "choices": ["할 일", "끝"]},
                                                              {"id": "f2", "name": "메모", "kind": "text", "choices": []}], "edit": "pop", "clash": "fill"}}
    two = dash_store.check_row_marks({"row_marks": {"keys": ["kind", "store"], "fields": [FIELDS[0]]}}, ["store", "kind"])
    assert two["row_marks"]["keys"] == ["kind", "store"]  # 고른 차례 그대로(열쇠 글자의 차례)


def test_check_row_marks_errors():
    one = {"id": "f1", "name": "단계", "kind": "choice", "choices": ["a"]}
    bad = [
        ({"row_marks": "x"}, "객체"),
        ({"row_marks": {"color": 1}}, "쓸 수 없는 항목"),
        ({"row_marks": {"keys": ["store"], "fields": [{**one, "id": "F1"}]}}, "id"),
        ({"row_marks": {"keys": ["store"], "fields": [{**one, "size": 1}]}}, "쓸 수 없는 항목"),
        ({"row_marks": {"keys": ["store"], "fields": [{**one, "name": " "}]}}, "이름"),
        ({"row_marks": {"keys": ["store"], "fields": [{**one, "kind": "list"}]}}, "kind"),
        ({"row_marks": {"keys": ["store"], "fields": [{**one, "choices": []}]}}, "choices"),
        ({"row_marks": {"keys": ["store"], "fields": [{**one, "choices": ["a", " a "]}]}}, "choices"),
        ({"row_marks": {"keys": ["store"], "fields": [{**one, "kind": "text"}]}}, "고르기 칸에만"),
        ({"row_marks": {"keys": ["store"], "fields": [one, {**one, "name": "둘"}]}}, "id가 겹쳐요"),
        ({"row_marks": {"keys": ["store"], "fields": [one, {**one, "id": "f2"}]}}, "이름이 겹쳐요"),
        ({"row_marks": {"keys": ["store"], "fields": [{**one, "name": "store"}]}}, "묶음 컬럼과 같아요"),
        ({"row_marks": {"fields": [one]}}, "하나 이상"),
        ({"row_marks": {"keys": ["kind"], "fields": [one]}}, "열쇠 컬럼"),
        ({"row_marks": {"keys": ["store", "store"], "fields": [one]}}, "겹치지 않게"),
        ({"row_marks": {"keys": "store", "fields": [one]}}, "열쇠 컬럼"),
        ({"row_marks": {"key": "store"}}, "쓸 수 없는 항목"),  # 옛 이름(글자 하나)은 받지 않는다
        ({"row_marks": {"edit": "side"}}, "edit"),
        ({"row_marks": {"clash": "merge"}}, "clash"),
        ({"row_marks": {"keys": ["store"], "fields": [{**one, "id": f"f{i}", "name": f"칸{i}"} for i in range(dash_store.MAX_ROW_MARK_FIELDS + 1)]}}, "fields"),
    ]
    for view, word in bad:
        with pytest.raises(ValueError, match=word):
            dash_store.check_row_marks(view, ["store"])


def test_row_marks_card_check():
    assert "row_marks" in dash_store.EXTRA_VIEW
    assert dash_store.check_card(list_card())["view"]["row_marks"] == MARKS
    assert "row_marks" not in dash_store.check_card(list_card(marks=None))["view"]
    with pytest.raises(ValueError, match="목록 카드에만"):
        dash_store.check_card({**list_card(), "kind": "table"})


def test_mark_count_frame_and_actions():
    assert "mark_count" in dash_store.FRAME_KINDS and dash_store.FRAME_KINDS[-3:] == ("segment", "chip_row", "filter_bar")
    assert "mark_count" in dash_store.LINK_FRAMES and "mark_count" in dash_store.SENDER_KINDS
    assert dash_store.FRAME_ACTIONS["marks_save"] == "card" and dash_store.FRAME_ACTIONS["marks_load"] == "card"
    assert dash_store.check_frame({}, "mark_count") == {"ambient": False, "label": "", "list": "", "field": "", "values": [], "look": "number"}
    got = dash_store.check_frame({"label": " 끝난 것 ", "list": "l1", "field": "f1", "values": ["끝", ""], "look": "bar", "ambient": True}, "mark_count")
    assert got == {"ambient": True, "label": "끝난 것", "list": "l1", "field": "f1", "values": ["끝", ""], "look": "bar"}  # "" = 표시 없음
    for frame, word in [({"look": "pie"}, "look"), ({"list": "a b"}, "카드 id"), ({"field": "F!"}, "표시 칸"), ({"values": ["a", "a"]}, "values"), ({"values": "a"}, "values"),
                        ({"color": 1}, "쓸 수 없는 항목")]:
        with pytest.raises(ValueError, match=word):
            dash_store.check_frame(frame, "mark_count")
    assert dash_store.check_frame({"label": "가", "action": "marks_load", "arg": "l1"}, "button")["arg"] == "l1"
    with pytest.raises(ValueError, match="표시 저장의 받는 값"):
        dash_store.check_frame({"label": "가", "action": "marks_save"}, "button")
    with pytest.raises(ValueError, match="크게 보기의 받는 값"):  # 옛 문구 그대로
        dash_store.check_frame({"label": "가", "action": "zoom"}, "button")


# ---------- dash_pull.parquet_key ----------

def test_parquet_key(tmp_path):
    con = ibis.duckdb.connect()
    known: set[str] = set()
    assert dash_pull.parquet_key(con, None, known) == ({}, {}) and known == set()
    k = base64.b64encode(bytes(range(16))).decode()
    w, r = dash_pull.parquet_key(con, k, known)
    name = dash_pull.KEY_PREFIX + dash_store.def_hash(k)
    assert w == {"encryption_config": {"footer_key": name}} and known == {name}
    path = tmp_path / "x.parquet"
    con.to_parquet(ibis.memtable({"a": [1, 2]}), path, **w)
    assert con.read_parquet([str(path)], **r).count().to_pyarrow().as_py() == 2
    assert dash_pull.parquet_key(con, k, known)[0] == w  # 두 번째는 등록하지 않고 같은 인자
    with pytest.raises(ValueError, match="열쇠 모양"):
        dash_pull.parquet_key(con, "짧음", set())


# ---------- dash_marks ----------

def test_formats():
    assert set(dash_marks.SAVE_NAMES) == set(result.EXPORTS)
    assert set(dash_marks.READS) <= set(result.EXPORTS)


def test_pick_kind():
    assert server.PICK_KINDS[dash_marks.PICK_KIND] == ("표시 파일 (*.csv;*.tsv;*.parquet;*.xlsx)", "모든 파일 (*.*)")


def test_set_mark_and_named(folder):
    card = list_card()
    first = dash_marks.named("d1", card)
    assert first.height == 0 and first.columns == NAMES
    assert dash_marks.set_mark(DASH, card, KA, {"f1": "끝"}) == {"key": KA, "values": {"f1": "끝", "f2": None, "f3": None, "f4": None}}
    assert dash_marks.set_mark(DASH, card, KB, {"f2": " 두 번째 ", "f3": "2026-03-01", "f4": 3}) == {"key": KB, "values": {"f1": None, "f2": "두 번째", "f3": "2026-03-01", "f4": 3.0}}
    assert dash_marks.set_mark(DASH, card, KA, {"f4": 1.5})["values"] == {"f1": "끝", "f2": None, "f3": None, "f4": 1.5}  # 다른 칸은 그대로
    got = dash_marks.named("d1", card).sort(KEY)
    assert got.columns == NAMES and got.schema["점수"] == pl.Float64
    assert got.rows() == [(KA, "끝", None, None, 1.5), (KB, None, "두 번째", "2026-03-01", 3.0)]
    dash_marks.set_mark(DASH, card, KA, {"f1": None, "f4": ""})  # 모든 칸이 비면 그 줄을 뺀다
    assert dash_marks.named("d1", card)[KEY].to_list() == [KB]
    assert (dash_marks.folder("d1") / "l1.parquet").is_file() and dash_marks.manifest("d1") == {"v": dash_marks.VERSION, "crypt": {"group": "none", "salt": ""}}
    assert dash_marks.named("", card).height == 0  # 저장하지 않은 대시보드
    with pytest.raises(ValueError, match="대시보드 id"):
        dash_marks.folder("../x")


def test_set_mark_errors(folder):
    card = list_card()
    for values, word in [({"f1": "다른 값"}, "가운데 하나"), ({"f3": "2026-02-30"}, "날짜"), ({"f3": "3월"}, "날짜"), ({"f4": "x"}, "숫자"), ({"f4": True}, "숫자"),
                         ({"f2": "가" * (dash_store.MAX_ROW_MARK_TEXT + 1)}, f"{dash_store.MAX_ROW_MARK_TEXT}자"), ({"zz": 1}, "없어요"), ({}, "values"), ("끝", "values")]:
        with pytest.raises(ValueError, match=word):
            dash_marks.set_mark(DASH, card, "A", values)
    with pytest.raises(ValueError, match="열쇠"):
        dash_marks.set_mark(DASH, card, "", {"f1": "끝"})
    assert dash_marks.named("d1", card).height == 0  # 틀린 요청은 아무것도 쓰지 않는다


def test_marks_encrypted_and_recrypt(folder):
    card = list_card()
    enc = {"id": "d1", "pull": {"crypt": "aes128"}}
    dash_marks.set_mark(enc, card, "A", {"f1": "끝"})
    path = dash_marks.folder("d1") / "l1.parquet"
    with pytest.raises(Exception):  # 열쇠 없이는 못 읽는다
        pq.read_table(path)
    assert dash_marks.key_of("d1") == dash_keys.app_key("aes128")
    assert dash_marks.named("d1", card)["단계"].to_list() == ["끝"]
    with pytest.raises(ValueError, match="암호화"):  # 설정과 파일의 암호화가 다르면 쓰지 않는다
        dash_marks.set_mark({"id": "d1", "pull": {"crypt": "none"}}, card, "B", {"f1": "끝"})
    assert dash_marks.recrypt({"id": "d1", "pull": {"crypt": "none"}}, dash_keys.app_key("aes128")) == ["l1"]
    assert dash_marks.key_of("d1") is None and pq.read_table(path).num_rows == 1
    assert dash_marks.recrypt({"id": "d2", "pull": None}, None) == []  # 표시가 없는 대시보드


def test_orphans_and_drop(folder):
    card = list_card()
    for k in ("Z", "A", "B", "Y"):
        dash_marks.set_mark(DASH, card, k, {"f1": "끝"})
    assert dash_marks.orphans("d1", "l1", ["A", "B", "C"]) == ["Y", "Z"]  # 열쇠 글자 순
    assert dash_marks.drop_keys(DASH, "l1", ["Y", "Z"]) == 2
    assert sorted(dash_marks.named("d1", card)[KEY].to_list()) == ["A", "B"]
    assert dash_marks.orphans("d1", "l1", ["A", "B"]) == [] and dash_marks.drop_keys(DASH, "l1", []) == 0


def test_save_file(folder, tmp_path, monkeypatch):
    card = list_card()
    dash_marks.set_mark(DASH, card, KA, {"f1": "끝", "f4": 2})
    target = tmp_path / "표시.csv"
    asked = []
    monkeypatch.setattr(server, "ask_save", lambda name, types: asked.append((name, types)) or str(target))
    assert dash_marks.save_file(DASH, card) == str(target)
    assert asked == [("목록.csv", tuple(f"{dash_marks.SAVE_NAMES[s]} (*{s})" for s in result.EXPORTS))]  # 형식은 대화상자에서 고른다
    got = pl.read_csv(target)
    assert got.columns == ["store", "단계", "메모", "배송일", "점수"] and got.row(0)[:2] == ("A", "끝")
    monkeypatch.setattr(server, "ask_save", lambda name, types: None)
    assert dash_marks.save_file(DASH, card) is None  # 취소
    monkeypatch.setattr(server, "ask_save", lambda name, types: str(target))
    with pytest.raises(ValueError, match="아직 붙인 표시"):
        dash_marks.save_file(DASH, list_card(card_id="l2"))
    monkeypatch.setattr(server, "ask_save", None)
    with pytest.raises(ValueError, match="파일 창"):
        dash_marks.save_file(DASH, card)


def test_save_and_load_two_keys(folder, tmp_path, monkeypatch):
    """묶음 둘·열쇠 둘: 파일에는 열쇠 컬럼마다 한 컬럼(사람이 읽는 값), 가져오면 같은 열쇠 글자로 다시 잇는다."""
    card = list_card(card_id="l3", marks=TWO, groups=("store", "kind"))
    dash_marks.set_mark(DASH, card, KAY, {"f1": "끝"})
    target = tmp_path / "둘.csv"
    monkeypatch.setattr(server, "ask_save", lambda name, types: str(target))
    dash_marks.save_file(DASH, card)
    got = pl.read_csv(target)
    assert got.columns == ["store", "kind", "단계", "메모", "배송일", "점수"] and got.row(0)[:3] == ("A", "y", "끝")
    src = tmp_path / "in2.csv"
    src.write_text("kind,store,단계\nx,A,할 일\ny,A,할 일\n", encoding="utf-8")  # 파일의 컬럼 차례는 상관없다
    monkeypatch.setattr(server, "ask_open", lambda kind: str(src))
    assert dash_marks.load_file(DASH, card) == {"path": str(src), "added": 1, "clashed": 1, "clash": "keep"}
    assert dict(dash_marks.named("d1", card).select(KEY, "단계").rows()) == {KAY: "끝", KAX: "할 일"}  # 같은 매장이어도 종류가 다르면 다른 줄
    src.write_text("store,단계\nA,끝\n", encoding="utf-8")
    with pytest.raises(ValueError, match="열쇠 컬럼 'kind'"):
        dash_marks.load_file(DASH, card)


@pytest.mark.parametrize("clash, want_a", [("keep", ("끝", "가", None)), ("replace", ("할 일", "가", 1.0)), ("fill", ("끝", "가", 1.0))])
def test_load_file_rules(folder, tmp_path, monkeypatch, clash, want_a):
    card = list_card(marks={**MARKS, "clash": clash})
    dash_marks.set_mark(DASH, card, KA, {"f1": "끝", "f2": "가"})
    src = tmp_path / "in.csv"
    src.write_text("store,단계,점수,다른 컬럼\nA,할 일,1,x\nC,끝,2,y\n", encoding="utf-8")
    monkeypatch.setattr(server, "ask_open", lambda kind: str(src) if kind == dash_marks.PICK_KIND else None)
    assert dash_marks.load_file(DASH, card) == {"path": str(src), "added": 1, "clashed": 1, "clash": clash}
    got = {r[0]: (r[1], r[2], r[4]) for r in dash_marks.named("d1", card).rows()}
    assert got == {KA: want_a, KC: ("끝", None, 2.0)}


def test_load_file_errors(folder, tmp_path, monkeypatch):
    card = list_card()
    cases = [("a.txt", "x", "가져올 파일은"), ("b.csv", "가게,단계\nA,끝\n", "열쇠 컬럼 'store'"), ("c.csv", "store,다른\nA,1\n", "표시 칸 이름"),
             ("d.csv", "store,단계\nA,없는 값\n", "고를 값"), ("e.csv", "store,점수\nA,많이\n", "숫자"), ("f.csv", "store,배송일\nA,2026-13-01\n", "YYYY-MM-DD"),
             ("g.csv", "store,단계\nA,끝\nA,할 일\n", "겹쳐요"), ("h.csv", "store,단계\n,끝\n", "빈 줄")]
    for name, text, word in cases:
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        monkeypatch.setattr(server, "ask_open", lambda kind, p=p: str(p))
        with pytest.raises(ValueError, match=word):
            dash_marks.load_file(DASH, card)
    assert dash_marks.named("d1", card).height == 0
    monkeypatch.setattr(server, "ask_open", lambda kind: None)
    assert dash_marks.load_file(DASH, card) is None


def test_forget(folder):
    dash_marks.set_mark(DASH, list_card(), "A", {"f1": "끝"})
    stranger = dash_marks.folder("d1") / "모르는 파일.txt"
    stranger.write_text("x", encoding="utf-8")
    dash_marks.forget("d1")
    assert not (dash_marks.folder("d1") / "l1.parquet").exists() and stranger.exists()  # 모르는 것은 남긴다
    stranger.unlink()
    dash_marks.set_mark(DASH, list_card(), "A", {"f1": "끝"})
    dash_marks.forget("d1")
    assert not dash_marks.folder("d1").exists()
    dash_marks.forget("d1")  # 없어도 된다


def test_list_card():
    d = {"id": "d1", "cards": [list_card(), list_card(card_id="l2", marks=None), {**list_card(card_id="t1"), "kind": "table"}]}
    assert dash_marks.list_card(d, "l1")["id"] == "l1"
    with pytest.raises(ValueError, match="표시 칸이 없어요"):
        dash_marks.list_card(d, "l2")
    for missing in ("t1", "nope", None):
        with pytest.raises(ValueError, match="찾지 못했어요"):
            dash_marks.list_card(d, missing)


# ---------- dash_calc ----------

def test_list_keys(db):
    assert sorted(dash_calc.list_keys(list_card())) == [KA, KB, KC]
    cut = list_card()
    cut["source"]["body"]["conditions"] = ["amt >= 10"]  # 카드 자신의 조건은 건다: A(10, 20)·B(50)
    assert sorted(dash_calc.list_keys(cut)) == [KA, KB]
    two = sorted(dash_calc.list_keys(list_card(marks=TWO, groups=("store", "kind"))))
    assert two == [KAX, KAY, '{"0":"B","1":"x"}', '{"0":"B","1":"y"}', '{"0":"C","1":"x"}']
    with pytest.raises(ValueError, match="열쇠 컬럼"):
        dash_calc.list_keys(list_card(marks={**MARKS, "keys": []}))


def test_list_data_with_marks(db):
    card = list_card(list_sort={"column": SUM_NAME, "desc": True}, list_rows=2)
    dash_marks.set_mark(DASH, card, KA, {"f1": "끝", "f2": "가"})
    dash_marks.set_mark(DASH, card, KC, {"f4": 7})
    v = dash_calc.list_data(card, {"dash": "d1"})["values"]
    assert v["columns"][2:] == [{"name": "단계", "kind": "mark", "field": "f1", "mark": "choice"}, {"name": "메모", "kind": "mark", "field": "f2", "mark": "text"},
                                {"name": "배송일", "kind": "mark", "field": "f3", "mark": "date"}, {"name": "점수", "kind": "mark", "field": "f4", "mark": "number"}]
    assert v["data"] == [["B", "A"], [55.0, 30.0], [None, "끝"], [None, "가"], [None, None], [None, None]]
    assert v["keys"] == [KB, KA] and v["more"] is True
    assert v["row_marks"] == {"total": 3, "counts": {"f1": {"끝": 1, "": 2}}}  # 보이는 두 줄이 아니라 목록 세 줄 전부
    picked = dash_calc.list_data(card, {"dash": "d1", "filters": [{"column": "단계", "in": ["끝"]}]})["values"]
    assert picked["keys"] == [KA] and picked["more"] is False and picked["row_marks"] == {"total": 3, "counts": {"f1": {"끝": 1, "": 2}}}  # 세기는 표시 칸 필터 앞
    assert dash_calc.list_data(card, {"dash": "d1", "filters": [{"column": "단계", "in": [""]}]})["values"]["keys"] == [KB, KC]  # "" = 표시 없음
    assert dash_calc.list_data(card, None)["values"]["data"][2] == [None, None]  # 대시보드를 모르면 표시가 없다
    with pytest.raises(ValueError, match="고르기 칸만"):
        dash_calc.list_data(card, {"dash": "d1", "filters": [{"column": "메모", "in": ["가"]}]})
    with pytest.raises(ValueError, match="컬럼 이름과 같아요"):
        dash_calc.list_data(list_card(marks={**MARKS, "fields": [{**FIELDS[0], "name": SUM_NAME}]}), {"dash": "d1"})
    plain = dash_calc.list_data(list_card(marks=None, list_rows=10), {"dash": "d1"})["values"]
    assert set(plain) == {"columns", "data", "more"}  # 표시 칸이 없으면 B3 그대로


def test_list_data_two_keys(db):
    """묶음 둘(매장·종류)·열쇠 둘: (A,y)에 붙인 표시는 같은 매장 A의 x 줄에 붙지 않는다(손계산: 합계 B,y 50·A,y 20·A,x 10·B,x 5·C,x 1)."""
    card = list_card(marks=TWO, groups=("store", "kind"), list_sort={"column": SUM_NAME, "desc": True}, list_rows=10)
    dash_marks.set_mark(DASH, card, KAY, {"f1": "끝"})
    v = dash_calc.list_data(card, {"dash": "d1"})["values"]
    assert v["keys"] == ['{"0":"B","1":"y"}', KAY, KAX, '{"0":"B","1":"x"}', '{"0":"C","1":"x"}']
    assert v["data"][:4] == [["B", "A", "A", "B", "C"], ["y", "y", "x", "x", "x"], [50.0, 20.0, 10.0, 5.0, 1.0], [None, "끝", None, None, None]]
    assert v["row_marks"] == {"total": 5, "counts": {"f1": {"끝": 1, "": 4}}}
    assert dash_calc.list_data(card, {"dash": "d1", "filters": [{"column": "단계", "in": ["끝"]}]})["values"]["keys"] == [KAY]


def test_with_filters_leaves_mark_fields(db):
    cut, missed = dash_calc.with_filters(list_card(), {"filters": [{"column": "단계", "in": ["끝"]}, {"column": "store", "in": ["A"]}, {"column": "없는 컬럼", "in": ["x"]}]})
    assert missed == ["없는 컬럼"] and cut["source"]["body"]["conditions"] == [{"column": "store", "in": ["A"]}]
    chart = {**list_card(), "kind": "chart"}
    assert dash_calc.with_filters(chart, {"filters": [{"column": "단계", "in": ["끝"]}]})[1] == ["단계"]  # 목록이 아니면 없는 컬럼


def test_filter_values_mark_choices():
    assert dash_calc.filter_values([list_card()], "단계") == (["할 일", "끝"], False, [])
    with pytest.raises(ValueError, match="고르기 칸만"):
        dash_calc.filter_values([list_card()], "메모")


def test_definition_has_row_marks():
    assert dash_calc.definition(dash_store.check_card(list_card()))["view"]["row_marks"] == MARKS


def test_pull_compute_passes_dash(monkeypatch):
    seen = []
    monkeypatch.setattr(dash_calc, "compute_filtered", lambda card, now, ctx=None: seen.append(ctx) or {})
    span = {"range": {"start": "2026-01-01", "end": "2026-01-31"}}
    dash_pull.compute(list_card(), "t", span, "", "")
    dash_pull.compute({**list_card(), "kind": "chart"}, "t", None, "", "")
    assert seen == [{**span, "dash": ""}, None]


# ---------- 라우트 ----------

def test_marks_routes(db, tmp_path, monkeypatch):
    saved = dash_store.save_dashboard({"name": "표시", "cards": [list_card()]})
    body = {"dash": saved["id"], "card": "l1"}
    kz = '{"0":"Z"}'
    r = db.post("/api/dash/marks/set", json={**body, "key": kz, "values": {"f1": "끝"}})
    assert r.status_code == 200 and r.json == {"key": kz, "values": {"f1": "끝", "f2": None, "f3": None, "f4": None}}
    assert db.post("/api/dash/marks/set", json={**body, "key": KA, "values": {"f1": "없는 값"}}).status_code != 200
    assert db.post("/api/dash/marks/orphans", json=body).json == {"count": 1}  # 지금 데이터는 A·B·C
    assert db.post("/api/dash/marks/orphans/drop", json=body).json == {"dropped": 1}
    assert db.post("/api/dash/marks/orphans", json=body).json == {"count": 0}
    db.post("/api/dash/marks/set", json={**body, "key": KA, "values": {"f1": "끝"}})
    target = tmp_path / "out.parquet"
    monkeypatch.setattr(server, "ask_save", lambda name, types: str(target))
    assert db.post("/api/dash/marks/save", json=body).json == {"path": str(target)} and pl.read_parquet(target)["store"].to_list() == ["A"]
    monkeypatch.setattr(server, "ask_open", lambda kind: None)
    assert db.post("/api/dash/marks/load", json=body).json == {"path": None}
    assert {"/api/dash/marks/set", "/api/dash/marks/save", "/api/dash/marks/load"} <= server.FREE
    assert not {"/api/dash/marks/orphans", "/api/dash/marks/orphans/drop"} & server.FREE


def test_dash_app_constants_crypt_delete(db):
    found = server.constants("dashboard")["dash"]["row_marks"]
    assert found["default"] == dash_store.ROW_MARKS_DEFAULT and found["kinds"] == list(dash_store.ROW_MARK_KINDS) and found["max_fields"] == dash_store.MAX_ROW_MARK_FIELDS
    saved = dash_store.save_dashboard({"name": "표시", "cards": [list_card()]})
    dash_marks.set_mark(saved, list_card(), "A", {"f1": "끝"})
    assert db.post("/api/dash/pull/crypt", json={"id": saved["id"], "crypt": "aes256", "how": "pull"}).status_code == 200
    assert dash_marks.key_of(saved["id"]) == dash_keys.app_key("aes256")  # 받은 데이터를 다시 받는 방법이어도 표시는 새 열쇠로 다시 쓴다
    assert dash_marks.named(saved["id"], list_card())["단계"].to_list() == ["끝"]
    db.post("/api/dash/delete", json={"id": saved["id"]})
    assert not dash_marks.folder(saved["id"]).exists()
