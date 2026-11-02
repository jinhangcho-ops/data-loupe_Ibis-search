"""F33 횟수 구간별 대상 수(탐색 틀 51번): 대상마다 사건 행 수를 센 뒤 그 횟수를 구간(경계 1, 2, 5, 10, 20 → 0회 / 1회 / 2~4회 / 5~9회 / 10~19회 / 20회 이상)으로 나눠 구간별 대상 수.
진짜 DuckDB에 시험 안에서 만든 표: users(u0~u9), rep(신고: u1 1번, u2 2번, u3 3번, u4 5번, u5 12번, u6 25번, 대상이 빈 행 1개). u0·u7·u8·u9는 신고가 없다."""
import duckdb
import pytest

from core import server
from query import sentence_exec as ex
from query import sentence_run as sr
from query import sentences

COLS = {"rid": "num", "uid": "text"}
BASE = {"base": "rep", "joins": [], "periods": [], "conditions": [], "calcs": []}
LABELS = ["0회", "1회", "2~4회", "5~9회", "10~19회", "20회 이상"]


@pytest.fixture
def c(folder, tmp_path):
    from query.search_app import app
    path = tmp_path / "x.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("create table users as select 'u' || i as id from range(10) t(i)")
    raw.execute("""create table rep as select row_number() over () as rid, uid from (
                   select 'u' || k as uid from (values (1, 1), (2, 2), (3, 3), (4, 5), (5, 12), (6, 25)) v(k, n), range(n) r(j)
                   union all select null)""")
    raw.close()
    client = app.test_client()
    client.get("/?key=" + server.KEY)
    assert client.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(path)}).status_code == 200
    yield client
    client.post("/api/disconnect", json={})


def go(slots):
    return ex.execute_compiled(ex.prepare("e51", slots, BASE, COLS))


def test_template_51_is_registered():
    t = sentences.get("e51")
    assert t["no"] == 51 and t["name"] == "횟수 구간별 대상 수" and set(t["uses"]) == {"count", "bucket"}
    assert {s["name"] for s in t["slots"]} == {"대상", "경계", "단위 글자", "대상 테이블", "대상 테이블 컬럼"}


def test_count_labels_rule():
    assert ex.count_labels([1, 2, 5, 10, 20]) == LABELS
    assert ex.count_labels([1, 2, 5, 10, 20], "건", low=1) == ["1건", "2~4건", "5~9건", "10~19건", "20건 이상"]  # 같은 테이블만: 0회 구간이 없다
    assert ex.count_labels([0, 3]) == ["0~2회", "3회 이상"] and ex.count_labels([3], low=1) == ["1~2회", "3회 이상"]
    assert ex.count_bounds("1, 2, 5") == [1, 2, 5] and ex.count_bounds([1, 10]) == [1, 10]
    for bad in ("2, 1", "1, 1", "1.5", "a", "-1, 2", ""):
        with pytest.raises(sr.Bad):
            ex.count_bounds(bad)


def test_with_entity_table_counts_zero(c):
    done = go({"대상": "uid", "경계": "1, 2, 5, 10, 20", "대상 테이블": "users", "대상 테이블 컬럼": "id"})
    assert done["columns"] == ["횟수", "대상 수"]
    assert [(r["횟수"], r["대상 수"]) for r in done["rows"]] == list(zip(LABELS, [4, 1, 2, 1, 1, 1]))
    assert "uid마다" in done["text"]


def test_same_table_starts_at_one_and_keeps_empty_buckets(c):
    done = go({"대상": "uid", "경계": "1, 2, 5, 10, 20, 30", "단위 글자": "건"})
    assert [(r["횟수"], r["대상 수"]) for r in done["rows"]] == [("1건", 1), ("2~4건", 2), ("5~9건", 1), ("10~19건", 1), ("20~29건", 1), ("30건 이상", 0)]  # 빈 행(uid 없음)은 세지 않는다


def test_bad_bounds_are_reported_before_running():
    made = sr.compile("e51", {"대상": "uid", "경계": "5, 2"}, BASE, COLS)  # /api/sentence/compile과 같은 길(e51은 sentence_exec가 붙인다)
    assert made["ok"] is False and "작은 수부터" in made["reason"]
    assert sr.compile("e51", {"대상": "uid", "경계": "1"}, BASE, COLS)["reason"].startswith("구간이 하나뿐")  # 같은 테이블만이면 1 아래 구간은 없다
