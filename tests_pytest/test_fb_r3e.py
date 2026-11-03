"""피드백 3차 e묶음: F34 대상별 요약표(탐색 틀 52 대상별 활동 요약, 53 집단 대 전체 비교), F11 문장 조건 칸의 `* is (not) null`, F33 경계 칸 검사.
진짜 DuckDB에 시험 안에서 만든 표: votes(투표: u1 4행, u2 1행, u3 3행, uid가 빈 행 1개), blocks(차단: u1·u3·u9는 spam, u2는 abuse).
u1: a·a·b(1일), a(2일) / u2: c(1일) / u3: a(1일)·b(2일)·c(3일). kind는 u1 y·y·x·y, u2 x, u3 z·x·z."""
import duckdb
import pytest

from core import server
from query import sentence_exec as ex
from query import sentence_run as sr
from query import sentences

COLS = {"vid": "num", "uid": "text", "target": "text", "ts": "date", "kind": "text"}
BASE = {"base": "votes", "joins": [], "periods": [], "conditions": [], "calcs": []}


@pytest.fixture
def c(folder, tmp_path):
    from query.search_app import app
    path = tmp_path / "x.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("""create table votes as select row_number() over () as vid, uid, target, ts::timestamp as ts, kind from (values
                   ('u1', 'a', '2026-01-01 09:00', 'y'), ('u1', 'a', '2026-01-01 10:00', 'y'), ('u1', 'b', '2026-01-01 11:00', 'x'), ('u1', 'a', '2026-01-02 09:00', 'y'),
                   ('u2', 'c', '2026-01-01 09:00', 'x'),
                   ('u3', 'a', '2026-01-01 09:00', 'z'), ('u3', 'b', '2026-01-02 09:00', 'x'), ('u3', 'c', '2026-01-03 09:00', 'z'),
                   (null, 'a', '2026-01-01 09:00', 'x')) v(uid, target, ts, kind)""")
    raw.execute("create table blocks as select * from (values ('u1', 'spam'), ('u2', 'abuse'), ('u3', 'spam'), ('u9', 'spam')) v(uid, reason)")
    raw.close()
    client = app.test_client()
    client.get("/?key=" + server.KEY)
    assert client.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(path)}).status_code == 200
    yield client
    client.post("/api/disconnect", json={})


def go(tid, slots):
    return ex.execute_compiled(ex.prepare(tid, slots, BASE, COLS))


def test_new_templates_are_registered():
    assert sentences.get("e52")["name"] == "대상별 활동 요약" and sentences.get("e53")["name"] == "집단 대 전체 비교"
    assert "e52" in sr.COMPILERS and "e53" in sr.COMPILERS


def test_activity_summary_full(c):
    done = go("e52", {"대상": "uid", "상대": "target", "날짜": "ts", "대표 값": "kind", "정렬": "행 수", "N": 10})
    assert done["columns"] == ["uid", "행 수", "고유 target 수", "최다 target 행 수", "전체 중 비중", "같은 target 하루 최대", "활동일", "하루 평균", "하루 최대", "주요 kind", "주요 kind 건수"]
    rows = {r["uid"]: r for r in done["rows"]}
    assert [r["uid"] for r in done["rows"]] == ["u1", "u3", "u2"]  # 행 수 큰 순, 빈 대상은 뺀다
    u1 = rows["u1"]
    assert (u1["행 수"], u1["고유 target 수"], u1["최다 target 행 수"], u1["같은 target 하루 최대"], u1["활동일"], u1["하루 최대"]) == (4, 2, 3, 2, 2, 3)
    assert u1["전체 중 비중"] == pytest.approx(0.75) and u1["하루 평균"] == pytest.approx(2.0) and (u1["주요 kind"], u1["주요 kind 건수"]) == ("y", 3)
    assert (rows["u3"]["고유 target 수"], rows["u3"]["활동일"], rows["u3"]["주요 kind"], rows["u3"]["주요 kind 건수"]) == (3, 3, "z", 2)
    assert rows["u3"]["전체 중 비중"] == pytest.approx(1 / 3)


def test_activity_summary_optional_slots_drop_columns(c):
    done = go("e52", {"대상": "uid", "날짜": "ts", "정렬": "활동일", "N": 2})
    assert done["columns"] == ["uid", "행 수", "활동일", "하루 평균", "하루 최대"]
    assert [(r["uid"], r["활동일"]) for r in done["rows"]] == [("u3", 3), ("u1", 2)]  # 상위 2개
    bad = sr.compile("e52", {"대상": "uid", "정렬": "고유 상대 수"}, BASE, COLS)
    assert bad["ok"] is False and "상대" in bad["reason"]


def test_group_vs_all_with_group_table(c):
    done = go("e53", {"대상": "uid", "조건": "reason = spam", "날짜": "ts", "집단 이름": "spam 차단", "집단 테이블": "blocks", "집단 테이블 컬럼": "uid"})
    assert done["columns"] == ["지표", "전체 uid", "spam 차단", "p-value(집단 vs 나머지)"]
    got = {r["지표"]: (r["전체 uid"], r["spam 차단"]) for r in done["rows"]}
    assert got["uid 수"] == (3, 2) and got["전체 행 수"] == (8, 7)  # u9는 투표가 없어 빠진다
    assert got["하루 평균"] == pytest.approx((8 / 6, 7 / 5)) and got["하루 중앙값"] == (1, 1) and got["하루 최소"] == (1, 1) and got["하루 최대"] == (3, 3)


def test_group_vs_all_same_table_without_date(c):
    done = go("e53", {"대상": "uid", "조건": "target = c"})
    got = {r["지표"]: (r["전체 uid"], r["조건에 맞는 대상"]) for r in done["rows"]}
    assert got["uid 수"] == (3, 2) and got["전체 행 수"] == (8, 4) and got["대상당 최대"] == (4, 3)  # u2·u3


def test_group_table_condition_is_not_checked_against_this_table():
    assert sr.compile("e53", {"대상": "uid", "조건": "reason = spam", "집단 테이블": "blocks"}, BASE, COLS)["ok"] is True
    assert sr.compile("e53", {"대상": "uid", "조건": "reason = spam"}, BASE, COLS)["ok"] is False


def test_star_null_condition_in_sentence(c):
    assert sr.formula_one("* is not null", {"a": "num", "b": "text"}) == "notnull(col('a')) and notnull(col('b'))"
    assert sr.formula_one("* is null", {"a": "num", "b": "text"}) == "isnull(col('a')) or isnull(col('b'))"
    assert sr.check("e10", {"조건": "* is not null", "무엇": "target"}, COLS) == []
    done = go("e41", {"조건1": "* is null", "값1": "빈칸", "값3": "꽉참", "새 이름": "꼴"})
    assert sorted(r["꼴"] for r in done["rows"]) == ["꽉참"] * 8 + ["빈칸"]
    done = go("e10", {"조건": "* is not null", "무엇": "target"})
    assert sum(r["전체 개수"] for r in done["rows"]) == 8


def test_e51_bounds_checked_as_slot_issue():
    issues = sr.check("e51", {"대상": "uid", "경계": "5, 2"}, COLS)
    assert [(i["slot"], i["level"]) for i in issues] == [("경계", "bad")] and "작은 수부터" in issues[0]["text"]
    assert sr.check("e51", {"대상": "uid"}, COLS) == []  # 비우면 기본 경계
