"""Phase 4(plans/phase-4.md) A: 행 단위 수동 라벨(태그) — 이름 검사, 저장(DPAPI 암호문·한 테이블 한 파일), 고유 키 검사, 달기·떼기, 태그 붙이기(left join)·거르기, 태그별 행 수, 라우트.
데이터는 시험 안에서 지어낸 메모리 표다. 새 함수가 스켈레톤이면 실패하는 것이 정상이다.
기존 함수 수정((수정) 절)이 끝나야 통과하는 시험은 xfail(strict=True, reason="phase-4 <항목>")로 표시했다."""
import json
import types

import polars as pl
import pytest
from lazy import ibis

from core import dash_store
from core import labels
from core import querying
from core import server
from core import settings
from query import label_app  # noqa: F401  라우트를 첫 요청 전에 단다

REF = {"source": "folder:c:/data", "schema": "", "table": "t"}
ROWS = {"id": [1, 2, 3, 4, 5], "code": ["a", "b", "c", "d", "e"], "txt": ["좋아요", "별로", "그냥", "최고", None], "score": [1.5, 2.0, 2.0, 3.0, 4.0]}
DISK = {"source": REF["source"], "schema": "", "table": "t", "key": "id", "key_type": "int", "text": "txt", "vocab": ["긍정", "부정"],
        "keys": [1, 2, 3], "tags": [["긍정"], ["부정"], ["긍정", "부정"]], "rev": 4}  # 본 파일에 쓰는 모양


@pytest.fixture
def store():
    return labels.from_json(DISK)  # 메모리 모양(쌍 표)


def disk():
    return labels.to_json(labels.load(REF))  # 저장 내용을 목록 모양으로 견준다


@pytest.fixture(params=["memory", "polars"])
def table(request):
    df = pl.DataFrame(ROWS, schema={"id": pl.Int64, "code": pl.String, "txt": pl.String, "score": pl.Float64})
    return ibis.memtable(df.to_arrow()) if request.param == "memory" else ibis.polars.connect().create_table("t", df)


def rows_by_id(expr):
    return sorted(expr.to_pyarrow().to_pylist(), key=lambda r: r["id"])


# ---------- 이름·요청 검사 ----------
def test_check_tag():
    assert labels.check_tag("긍정") == "긍정"
    assert labels.check_tag("가 " * 300 + "끝") == "가 " * 300 + "끝"  # 길이 상한 없음, 가운데 공백은 된다
    for bad, words in (("", "비어"), (" ", "비어"), (3, "비어"), (" 긍정", "앞뒤"), ("긍정 ", "앞뒤"), ("a,b", "쉼표"), ("a\nb", "줄바꿈"), ("a\tb", "줄바꿈")):
        with pytest.raises(ValueError, match=words):
            labels.check_tag(bad)


def test_check_use():
    assert labels.check_use(None) is None
    assert labels.check_use({}) == {} and labels.check_use({"none": False}) == {}
    assert labels.check_use({"any": ["긍정", "부정", "긍정"]}) == {"any": ["긍정", "부정"]}
    assert labels.check_use({"all": ["긍정", "부정"]}) == {"all": ["긍정", "부정"]}
    assert labels.check_use({"none": True}) == {"none": True}
    for bad, words in (([], "객체"), ({"some": ["a"]}, "객체"), ({"any": "긍정"}, "목록"), ({"all": []}, "목록"), ({"any": ["a"], "none": True}, "하나만"),
                       ({"any": ["a"], "all": ["b"]}, "하나만"), ({"none": "yes"}, "none"), ({"any": [" a"]}, "앞뒤")):
        with pytest.raises(ValueError, match=words):
            labels.check_use(bad)


def test_ref_of_and_file_name(monkeypatch):
    monkeypatch.setitem(server.state, "con", object())
    monkeypatch.setattr(dash_store, "current_source", lambda: {"key": REF["source"], "label": "data"})
    assert labels.ref_of({"schema": "", "base": "t"}) == REF
    assert labels.ref_of({"base": "t"}) == REF
    for body in ({"base": "@탭"}, {"base": "t", "union": {"tables": ["u"]}}, {"base": None}):
        with pytest.raises(ValueError, match="연결의 테이블에만"):
            labels.ref_of(body)
    name = labels.file_name(REF)
    assert name.startswith(labels.FILE_PREFIX) and name.endswith(".json") and len(name) == len(labels.FILE_PREFIX) + 16 + len(".json")
    assert name == labels.FILE_PREFIX + dash_store.def_hash([REF["source"], "", "t"]) + ".json"
    assert labels.file_name({**REF, "table": "u"}) != name


# ---------- 고유 키 검사 ----------
def test_check_key(table):
    assert labels.check_key(table, "id") == "int" and labels.check_key(table, "code") == "string"
    with pytest.raises(ValueError, match="정수나 글자"):
        labels.check_key(table, "score")
    with pytest.raises(ValueError, match="키 컬럼이 없어요"):
        labels.check_key(table, "idd")
    dup = table.mutate(k=ibis.literal(1, "int64"))
    with pytest.raises(ValueError, match="고유하지"):
        labels.check_key(dup, "k")
    with pytest.raises(ValueError, match="빈 값"):
        labels.check_key(table, "txt")  # 빈 값이 있으면(Polars의 nunique는 빈 값도 센다) 고유 검사 앞에서 거부


# ---------- 저장·달기·떼기 ----------
def test_setup_apply_vocab_roundtrip(folder):
    t = ibis.memtable(pl.DataFrame(ROWS).to_arrow())
    made = labels.setup(REF, t, "id", "txt", ["긍정", "부정", "긍정"])
    assert (made["key"], made["key_type"], made["text"], made["vocab"], made["rev"], made["labeled"]) == ("id", "int", "txt", ["긍정", "부정"], 1, 0)
    assert made["pairs"].columns == [labels.KEY_COLUMN, labels.TAG_NAME] and made["pairs"].height == 0  # 메모리에는 쌍 표
    assert (disk()["keys"], disk()["tags"]) == ([], [])
    assert labels.load(REF)["vocab"] == ["긍정", "부정"]
    assert labels.load({**REF, "table": "u"}) is None
    raw = (folder / labels.file_name(REF)).read_text(encoding="utf-8")
    assert json.loads(raw)["v"] == labels.VERSION and "blob" in json.loads(raw) and "긍정" not in raw  # 암호문(DPAPI)
    assert labels.apply(REF, [1, 2, 3], ["긍정"], []) == {"added": 3, "removed": 0, "labeled": 3, "vocab": ["긍정", "부정"]}
    assert labels.apply(REF, [2], ["부정"], ["긍정"]) == {"added": 1, "removed": 1, "labeled": 3, "vocab": ["긍정", "부정"]}
    assert labels.apply(REF, [4, 4], ["중립"], [])["vocab"] == ["긍정", "부정", "중립"]  # 사전에 없는 태그는 끝에 더한다, 같은 키는 한 번
    assert labels.apply(REF, [3], ["부정"], [])["added"] == 1
    got = labels.frame(labels.load(REF)).sort(labels.KEY_COLUMN)
    assert got[labels.KEY_COLUMN].to_list() == [1, 2, 3, 4]
    assert got[labels.TAG_COLUMN].to_list() == ["긍정", "부정", "긍정, 부정", "중립"]  # 사전 차례대로
    labels.set_vocab(REF, ["부정", "긍정", "중립"])
    assert labels.frame(labels.load(REF)).sort(labels.KEY_COLUMN)[labels.TAG_COLUMN].to_list() == ["긍정", "부정", "부정, 긍정", "중립"]
    assert labels.apply(REF, [4], [], ["중립"])["labeled"] == 3  # 태그를 모두 뗀 키는 빠진다
    with pytest.raises(ValueError, match="뺄 수 없어요"):
        labels.set_vocab(REF, ["부정"])
    with pytest.raises(ValueError, match="바꿀 수 없어요"):
        labels.setup(REF, t, "code", "", [])
    assert labels.setup(REF, t, "id", "", ["긍정", "부정", "중립", "새"])["vocab"] == ["긍정", "부정", "중립", "새"]  # 같은 키면 사전·글 컬럼만
    assert (folder / settings.backup_of(folder / labels.file_name(REF)).name).exists()  # .bak 한 세대
    assert labels.load(REF)["rev"] == 8  # 저장할 때마다 1씩: setup 1 + apply 4 + set_vocab 1 + apply 1 + setup 1(실패한 셋은 쓰지 않았다)


def test_rename_and_drop_tag(folder):
    t = ibis.memtable(pl.DataFrame(ROWS).to_arrow())
    labels.setup(REF, t, "id", "", ["긍정", "부정"])
    labels.apply(REF, [1, 2], ["긍정"], [])
    labels.apply(REF, [2], ["부정"], [])
    renamed = labels.rename_tag(REF, "긍정", "좋음")
    assert renamed["vocab"] == ["좋음", "부정"] and labels.to_json(renamed)["tags"] == [["좋음"], ["좋음", "부정"]]  # 사전 자리 그대로, 달린 행 모두
    for old, new, words in (("없는", "x", "사전에 없는"), ("좋음", "부정", "이미 있는"), ("좋음", " x", "앞뒤"), ("좋음", "좋음", "이미 있는")):
        with pytest.raises(ValueError, match=words):
            labels.rename_tag(REF, old, new)
    assert labels.drop_tag(REF, "좋음") == {"removed": 2, "labeled": 1, "vocab": ["부정"]}  # 태그가 모두 지워진 키 1은 빠진다
    assert disk()["keys"] == [2] and disk()["tags"] == [["부정"]]
    with pytest.raises(ValueError, match="사전에 없는"):
        labels.drop_tag(REF, "좋음")
    labels.set_vocab(REF, ["부정", "새1", "새2"])  # 더하기
    assert labels.load(REF)["vocab"] == ["부정", "새1", "새2"]


def log_of(folder):
    return folder / (labels.file_name(REF)[: -len(".json")] + labels.LOG_SUFFIX)


def fresh_read():
    labels.CACHE.clear()  # 다른 프로세스가 연 것처럼 파일에서 다시 읽는다
    return labels.load(REF)


def test_log_replay_equals_compacted(folder):
    t = ibis.memtable(pl.DataFrame(ROWS).to_arrow())
    labels.setup(REF, t, "id", "", ["긍정", "부정"])  # 켜기는 본 파일로 바로 쓴다
    assert not log_of(folder).exists()
    labels.apply(REF, [1, 2, 3], ["긍정"], [])
    labels.apply(REF, [2], ["부정"], ["긍정"])
    labels.rename_tag(REF, "부정", "나쁨")
    labels.set_vocab(REF, ["나쁨", "긍정", "새"])
    labels.apply(REF, [4], ["새"], [])
    labels.drop_tag(REF, "새")
    lines = log_of(folder).read_text(encoding="ascii").splitlines()
    assert len(lines) == 6 and all(lines) and "긍정" not in log_of(folder).read_text(encoding="ascii")  # 한 줄 = 한 변경, 줄마다 암호문
    live = labels.to_json(labels.load(REF))  # 이 프로세스의 메모리(재생 없이 쌓은 것)
    replayed = labels.to_json(fresh_read())
    assert (replayed["rev"], replayed["vocab"], replayed["keys"], replayed["tags"]) == (7, ["나쁨", "긍정"], [1, 2, 3], [["긍정"], ["나쁨"], ["긍정"]])
    assert replayed == live
    assert labels.compact_all() == 1
    assert not log_of(folder).exists()  # 합친 뒤 기록을 비운다
    assert labels.to_json(fresh_read()) == replayed
    assert labels.compact_all() == 0


def test_torn_last_line_and_broken_middle(folder):
    t = ibis.memtable(pl.DataFrame(ROWS).to_arrow())
    labels.setup(REF, t, "id", "", ["긍정"])
    labels.apply(REF, [1], ["긍정"], [])
    with log_of(folder).open("ab") as f:
        f.write(b"QUJDRE")  # 쓰다 끊긴 마지막 줄(줄바꿈 없음)
    got = labels.to_json(fresh_read())
    assert got["keys"] == [1] and got["rev"] == 2  # 깨진 마지막 줄은 무시
    labels.apply(REF, [2], ["긍정"], [])  # 깨진 줄이 있으면 먼저 합친 뒤 쓴다(깨진 줄 뒤에 붙이지 않는다)
    assert labels.to_json(fresh_read())["keys"] == [1, 2] and fresh_read()["rev"] == 3
    good = log_of(folder).read_bytes()
    assert good.count(b"\n") == 1  # 합친 뒤 [2] 한 줄만 남았다
    log_of(folder).write_bytes(b"QUJDRE\n" + good)  # 끝이 아닌 줄이 깨짐
    with pytest.raises(ValueError, match="가운데 줄이 깨져"):
        fresh_read()
    assert log_of(folder).read_bytes() == b"QUJDRE\n" + good  # 건드리지 않았다


def test_merged_lines_are_skipped(folder):
    t = ibis.memtable(pl.DataFrame(ROWS).to_arrow())
    labels.setup(REF, t, "id", "", ["긍정"])
    labels.apply(REF, [1], ["긍정"], [])
    labels.apply(REF, [2], ["긍정"], [])
    left = log_of(folder).read_bytes()
    labels.compact_all()
    log_of(folder).write_bytes(left)  # 합치기 도중 꺼진 것처럼: 본 파일은 썼는데 기록이 남음
    got = labels.to_json(fresh_read())
    assert got["rev"] == 3 and got["keys"] == [1, 2]  # rev가 본 파일보다 작거나 같은 줄은 이미 합친 것이라 건너뛴다


def test_compact_when_log_full(folder, monkeypatch):
    t = ibis.memtable(pl.DataFrame(ROWS).to_arrow())
    labels.setup(REF, t, "id", "", ["긍정"])
    monkeypatch.setattr(labels, "MAX_LOG_LINES", 3)
    labels.apply(REF, [1], ["긍정"], [])
    labels.apply(REF, [2], ["긍정"], [])
    assert len(log_of(folder).read_text(encoding="ascii").splitlines()) == 2
    labels.apply(REF, [3], ["긍정"], [])
    assert not log_of(folder).exists() and fresh_read()["rev"] == 4  # 세 줄이 되면 합친다
    labels.apply(REF, list(range(10, 10 + labels.MAX_APPLY + 1)), ["긍정"], [])
    assert not log_of(folder).exists()  # MAX_APPLY보다 큰 변경은 기록에 쓰지 않고 바로 합친다
    assert fresh_read()["rev"] == 5


def test_revision(folder, monkeypatch):
    t = ibis.memtable(pl.DataFrame(ROWS).to_arrow())
    monkeypatch.setattr(labels, "ref_of", lambda body: REF)
    assert labels.revision({"base": "t"}) is None  # 태그를 켜지 않은 요청
    labels.setup(REF, t, "id", "", ["긍정"])
    assert labels.revision({"base": "t", "labels": {}}) == 1
    labels.apply(REF, [1], ["긍정"], [])
    assert labels.revision({"base": "t", "labels": {"any": ["긍정"]}}) == 2


def test_apply_rejects_bad_input(folder):
    t = ibis.memtable(pl.DataFrame(ROWS).to_arrow())
    with pytest.raises(ValueError, match="아직 태그를 켜지"):
        labels.apply(REF, [1], ["긍정"], [])
    labels.setup(REF, t, "id", "", ["긍정"])
    for keys, add, remove, words in (([1], [], [], "고르세요"), ([1], ["x"], ["x"], "함께"), ([1], [], ["없는"], "사전에 없는"), (["a"], ["긍정"], [], "정수"),
                                     ([True], ["긍정"], [], "정수"), ([], ["긍정"], [], "키"), ([1], ["a,b"], [], "쉼표")):
        with pytest.raises(ValueError, match=words):
            labels.apply(REF, keys, add, remove)
    assert len(labels.apply(REF, [1], [f"t{i}" for i in range(60)], [])["vocab"]) == 61  # 태그 종류 수에는 상한이 없다
    with pytest.raises(ValueError, match="고유하지"):
        labels.setup({**REF, "table": "u"}, t.mutate(k=ibis.literal(1, "int64")), "k", "", [])
    with pytest.raises(ValueError, match="글자 컬럼"):
        labels.setup({**REF, "table": "v"}, t, "id", "score", [])


def test_store_size_limit(folder, monkeypatch):
    t = ibis.memtable(pl.DataFrame(ROWS).to_arrow())
    labels.setup(REF, t, "id", "", ["긍정"])
    monkeypatch.setattr(labels, "MAX_LABELED", 2)
    labels.apply(REF, [1, 2], ["긍정"], [])
    with pytest.raises(ValueError, match="넘어요"):
        labels.apply(REF, [3], ["긍정"], [])  # 달린 키가 상한을 넘으면 commit이 기록에 쓰기 전에 거부
    assert disk()["keys"] == [1, 2]  # 쓰지 않았다


# ---------- 붙이기·거르기·세기 ----------
def test_memory_shape_and_frame(store):
    p = store["pairs"].sort([labels.KEY_COLUMN, labels.TAG_NAME])
    assert p.rows() == [(1, "긍정"), (2, "부정"), (3, "긍정"), (3, "부정")] and p.schema[labels.KEY_COLUMN] == pl.Int64
    assert store["labeled"] == 3 and store["rev"] == 4 and "keys" not in store and "tags" not in store  # 메모리에는 목록이 없다
    assert DISK["keys"] == [1, 2, 3]  # 받은 것은 고치지 않는다
    back = labels.to_json({**store, "vocab": ["부정", "긍정"], "pairs": p.reverse()})  # 쌍의 차례와 상관없이 키 차례·사전 차례로
    assert (back["keys"], back["tags"], back["vocab"]) == ([1, 2, 3], [["긍정"], ["부정"], ["부정", "긍정"]], ["부정", "긍정"])
    assert "pairs" not in back and "labeled" not in back and back["key"] == "id"
    assert labels.to_json(store) == DISK  # 왕복
    f = labels.frame(store).sort(labels.KEY_COLUMN)
    assert f.columns == [labels.KEY_COLUMN, labels.TAG_COLUMN] and f.rows() == [(1, "긍정"), (2, "부정"), (3, "긍정, 부정")]  # 사전 차례로 잇는다
    assert labels.frame({**store, "vocab": ["부정", "긍정"]}).sort(labels.KEY_COLUMN)[labels.TAG_COLUMN].to_list() == ["긍정", "부정", "부정, 긍정"]
    s = labels.from_json({**DISK, "key_type": "string", "keys": ["a"], "tags": [["부정"]]})
    assert s["pairs"].schema[labels.KEY_COLUMN] == pl.String and labels.frame(s).rows() == [("a", "부정")]


def test_attach_text_and_filters(table, store):
    got = rows_by_id(labels.attach(table, store, {}))
    assert [r[labels.TAG_COLUMN] for r in got] == ["긍정", "부정", "긍정, 부정", None, None]
    assert list(labels.attach(table, store, {}).columns) == [*table.columns, labels.TAG_COLUMN]
    assert [r["id"] for r in rows_by_id(labels.attach(table, store, {"any": ["부정"]}))] == [2, 3]
    assert [r["id"] for r in rows_by_id(labels.attach(table, store, {"all": ["긍정", "부정"]}))] == [3]
    assert [r["id"] for r in rows_by_id(labels.attach(table, store, {"none": True}))] == [4, 5]
    empty = labels.from_json({**DISK, "keys": [], "tags": []})
    assert [r["id"] for r in rows_by_id(labels.attach(table, empty, {}))] == [1, 2, 3, 4, 5]  # 빈 라벨
    with pytest.raises(ValueError, match="이미 있어"):
        labels.attach(table.mutate(**{labels.TAG_COLUMN: ibis.literal("x")}), store, {})
    with pytest.raises(ValueError, match="키 컬럼이 없어요"):
        labels.attach(table.select("code"), store, {})


def test_attach_int32_key(store):
    t = ibis.memtable(pl.DataFrame({"id": [1, 2]}, schema={"id": pl.Int32}).to_arrow())
    assert [r[labels.TAG_COLUMN] for r in rows_by_id(labels.attach(t, store, {}))] == ["긍정", "부정"]


def test_tagged_and_tag_counts(table, store):
    assert [r["id"] for r in rows_by_id(labels.tagged(table, store, ["긍정"], False))] == [1, 3]
    assert [r["id"] for r in rows_by_id(labels.tagged(table, store, ["긍정", "부정"], False))] == [1, 2, 3]  # 하나라도
    assert [r["id"] for r in rows_by_id(labels.tagged(table, store, ["긍정", "부정"], True))] == [3]  # 모두
    assert [r["id"] for r in rows_by_id(labels.tagged(table, store, [], False))] == [1, 2, 3]
    assert labels.tag_counts(table, store, []) == [{"w": "긍정", "n": 2}, {"w": "부정", "n": 2}]  # 같은 수는 사전 차례
    assert labels.tag_counts(table.filter(table.id <= 2), store, ["부정"]) == [{"w": "부정", "n": 1}]
    with pytest.raises(ValueError, match="사전에 없는"):
        labels.tag_counts(table, store, ["없는"])


def test_replay_ops(store):
    p = store["pairs"]
    df, vocab, added, removed = labels.replay(p, store["vocab"], {"op": "apply", "keys": [3, 4, 4], "add": ["중립"], "remove": ["부정"]})
    assert (vocab, added, removed, df.height) == (["긍정", "부정", "중립"], 2, 1, 5) and p.height == 4  # 받은 표는 그대로
    df, vocab, added, removed = labels.replay(p, store["vocab"], {"op": "rename", "old": "부정", "new": "나쁨"})
    assert (vocab, added, removed) == (["긍정", "나쁨"], 0, 0) and sorted(df[labels.TAG_NAME].to_list()) == ["긍정", "긍정", "나쁨", "나쁨"]
    df, vocab, added, removed = labels.replay(p, store["vocab"], {"op": "drop", "name": "긍정"})
    assert (vocab, added, removed, df.height) == (["부정"], 0, 2, 2)
    df, vocab, added, removed = labels.replay(p, store["vocab"], {"op": "vocab", "vocab": ["부정", "긍정", "새"]})
    assert (vocab, added, removed) == (["부정", "긍정", "새"], 0, 0) and df is p
    with pytest.raises(ValueError, match="가운데 줄이 깨져"):
        labels.replay(p, store["vocab"], {"op": "zap"})


def test_keys_of(table, monkeypatch):
    assert sorted(labels.keys_of(table, "id")) == [1, 2, 3, 4, 5]
    assert sorted(labels.keys_of(table.mutate(k=ibis.literal(7, "int64")), "k")) == [7]
    monkeypatch.setattr(labels, "MAX_LABELED", 3)
    with pytest.raises(ValueError, match="넘어"):
        labels.keys_of(table, "id")


def test_attached_and_needs_pc(table, store, monkeypatch):
    monkeypatch.setattr(labels, "store_of", lambda body: store)
    assert labels.attached({"base": "t"}, table) is table
    assert labels.TAG_COLUMN in labels.attached({"base": "t", "labels": {}}, table).columns
    monkeypatch.setitem(server.state, "con", types.SimpleNamespace(name="postgres"))
    assert labels.needs_pc({"base": "t", "labels": {}}) is True and labels.needs_pc({"base": "t"}) is False
    monkeypatch.setitem(server.state, "con", types.SimpleNamespace(name="duckdb"))
    assert labels.needs_pc({"base": "t", "labels": {"none": True}}) is False
    assert labels.keep_key({"base": "t", "labels": {}}, ["x"]) == ["x", "id"]
    assert labels.keep_key({"base": "t", "labels": {}}, ["id", "x"]) == ["id", "x"]
    assert labels.keep_key({"base": "t", "labels": {}}, None) is None
    assert labels.keep_key({"base": "t"}, ["x"]) == ["x"]


# ---------- 기존 함수에 붙이기((수정)) ----------
def test_staged_attaches_labels(table, store, monkeypatch):
    monkeypatch.setattr(labels, "store_of", lambda body: store)
    got = querying.staged({"schema": "", "base": "t", "labels": {"any": ["긍정"]}}, table)
    assert [r["id"] for r in rows_by_id(got)] == [1, 3] and labels.TAG_COLUMN in got.columns
    assert labels.TAG_COLUMN not in querying.staged({"schema": "", "base": "t"}, table).columns


def test_stats_key_changes_with_label_revision(monkeypatch):
    monkeypatch.setattr(server, "connection_key", lambda: "conn")
    monkeypatch.setattr(querying, "get_con", lambda: object())  # 연결 없이: 세대 정보(csv_gen)가 없는 가짜
    monkeypatch.setattr(server, "virtual_tables", lambda: {})
    body = {"schema": "", "base": "t", "labels": {}, "calcs": ["z = 태그"]}
    monkeypatch.setattr(labels, "revision", lambda b: 1)
    first = querying.stats_key(body, 0)
    monkeypatch.setattr(labels, "revision", lambda b: 2)
    assert querying.stats_key(body, 0) != first  # 태그를 바꾸면 미리 구한 통계를 다시 구한다


# ---------- 라우트 ----------
def test_apply_route(client, monkeypatch):
    t = ibis.memtable(pl.DataFrame(ROWS).to_arrow())
    monkeypatch.setattr(labels, "ref_of", lambda body: REF)
    labels.setup(REF, t, "id", "txt", ["긍정"])
    r = client.post("/api/labels/apply", json={"schema": "", "base": "t", "keys": [1, 2], "add": ["긍정"], "remove": []})
    assert r.status_code == 200 and r.get_json() == {"added": 2, "removed": 0, "labeled": 2, "vocab": ["긍정"]}
    r = client.post("/api/labels/apply", json={"schema": "", "base": "t", "keys": list(range(labels.MAX_APPLY + 1)), "add": ["긍정"], "remove": []})
    assert r.status_code == 400 and "한 번에" in r.get_json()["error"]
    info = client.get("/api/labels?schema=&base=t").get_json()
    assert info == {"on": True, "key": "id", "key_type": "int", "text": "txt", "vocab": ["긍정"], "labeled": 2, "quick": labels.QUICK_KEYS}
    r = client.post("/api/labels/rename", json={"schema": "", "base": "t", "old": "긍정", "new": "좋음"})
    assert r.status_code == 200 and r.get_json()["vocab"] == ["좋음"]
    r = client.post("/api/labels/drop", json={"schema": "", "base": "t", "name": "좋음"})
    assert r.status_code == 200 and r.get_json() == {"removed": 2, "labeled": 0, "vocab": []}
    monkeypatch.setattr(labels, "ref_of", lambda body: {**REF, "table": "none"})
    assert client.get("/api/labels?schema=&base=none").get_json() == {"on": False, "quick": labels.QUICK_KEYS}
