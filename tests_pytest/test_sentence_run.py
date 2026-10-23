"""문장 검사·만들기(sentence_run.py): 빈칸 이유(bad·warn·info), 틀에서 기존 라우트가 받는 요청으로 바꾸고 실제로 돌려 손으로 센 값과 맞춘다. 데이터는 테스트 안에서 지어낸다.
ev: id 0~11, store = A(id 0~4)·B(5~8)·C(9~11), amt = (id+1)*10 → A 합 150(5건), B 합 300(4건), C 합 330(3건). uid = 'u' + id % 4, day = 2026-01-01 + id(2026-01-01은 목요일)."""
import duckdb
import pytest

from query import sentence_run as sr
from query import sentences
from core import server
from core import sql_funcs

COLS = {"id": "num", "uid": "text", "store": "text", "amt": "num", "day": "date", "tags": "array", "url": "text"}
BASE = {"base": "ev", "joins": [], "periods": [], "conditions": [], "calcs": []}


@pytest.fixture
def c(folder, tmp_path):
    from dashboard import dash_app  # noqa: F401
    from query import flow_app  # noqa: F401
    from prep import prep_app  # noqa: F401
    from query.search_app import app
    path = tmp_path / "s.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("""create table ev as select i::int as id, 'u' || (i % 4) as uid, case when i < 5 then 'A' when i < 9 then 'B' else 'C' end as store,
                   ((i + 1) * 10)::double as amt, date '2026-01-01' + i::int as day, ['a', 'b'] as tags, 'p' || i || '?x=1' as url from range(12) t(i)""")
    raw.close()
    client = app.test_client()
    client.get("/?key=" + server.KEY)
    assert client.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(path)}).status_code == 200
    yield client
    client.post("/api/disconnect", json={})


def out(tid, slots, **kw):
    done = sr.compile(tid, slots, kw.get("body", BASE), COLS)
    assert done["ok"], done
    return done


def go(c, done, n=0):
    run = done["runs"][n]
    resp = c.post(run["route"], json=run["body"])
    assert resp.status_code == 200, resp.get_json()
    j = resp.get_json()
    return j["rows"]["rows"] if "rows" in j else j


def table(rows):
    return {r[next(iter(r))]: r for r in rows}


# ---------- 검사 ----------

def test_check_gives_reasons():
    def first(tid, slots):
        return [(i["slot"], i["level"], i["text"]) for i in sr.check(tid, slots, COLS)]
    assert first("e03", {"컬럼": "zz", "값": "A", "컬럼들": ["id"]}) == [("컬럼", "bad", "‘zz’ 컬럼이 이 테이블에 없어요.")]
    assert first("e03", {"컬럼": "amt", "값": "abc", "컬럼들": ["id"]}) == [("값", "bad", "‘abc’은 숫자 값으로 읽을 수 없어요(‘amt’은 숫자 컬럼이에요).")]
    assert first("e08", {}) == [("무엇", "bad", "‘나눌 기준 컬럼’ 칸을 채워 주세요."), ("대상", "bad", "‘대상(사람·물건) 컬럼’ 칸을 채워 주세요.")]
    assert first("e09", {"무엇": "store", "N": 0}) == [("N", "bad", "‘N 이상’에는 1 이상의 정수를 넣어 주세요.")]
    assert first("e13", {"무엇": "store", "값": "store", "집계": "sum", "N": 3}) == [("집계", "bad", "‘store’은 글자 컬럼이라 합계을(를) 구할 수 없어요. 숫자 컬럼을 고르거나 개수·고유 개수를 쓰세요.")]
    assert first("e05", {"컬럼": "store", "글자": {"match": "으로 끝", "text": "A"}}) == []
    assert first("e05", {"컬럼": "amt", "글자": "A"}) == [("컬럼", "bad", "‘amt’은 숫자 컬럼이라 쓸 수 없어요(글자 컬럼이 필요해요)."), ("글자", "warn", "대소문자를 가리지 않고 찾아요.")]
    assert first("e14", {"무엇": "store", "대상": "uid", "정확도": "빠르게(근사)"}) == [("정확도", "info", "근사 값이에요. 정확한 값과 조금 다를 수 있어요.")]
    assert first("e07", {"조건": "zz > 1", "대상": "uid"}) == [("조건", "bad", "조건의 ‘zz’ 컬럼이 이 테이블에 없어요.")]
    assert first("e36", {"정렬 기준": "day", "값": "amt", "종류": "이동 평균"}) == []  # 이동 크기 N은 선택(기본 3)
    assert first("e26", {"무엇": "store", "값": "amt", "종류": "분위수 N"}) == []
    assert first("e29", {"무엇": "store", "컬럼": "uid", "대표 값 고르기": "first"}) == []  # 정렬 기준은 선택 칸이라 compile에서 알린다
    assert sr.compile("e29", {"무엇": "store", "컬럼": "uid", "대표 값 고르기": "first"}, BASE, COLS)["reason"] == "‘정렬 기준’ 칸을 채워 주세요."
    assert sr.compile("e08", {}, BASE, COLS)["ok"] is False  # bad가 있으면 만들지 않는다
    assert sr.compile("e08", {}, BASE, COLS)["issues"][0]["level"] == "bad"


def test_every_explore_template_is_compiled_or_refused_with_a_reason():
    """50개 틀 모두 문장 글을 만들고, 못 만드는 것은 이유를 낸다(빈칸이 비었을 때는 검사 이유)."""
    for t in sentences.EXPLORE:
        done = sr.compile(t["id"], {}, BASE, COLS)
        required = [x for x in t["slots"] if not x["optional"] and x["default"] is None and not x["when"]]
        assert done["ok"] is not bool(required) and (done["ok"] or done["reason"]), t["id"]  # 빈칸이 있으면 이유, 빈칸이 없는 틀(1·6)은 바로 만든다
        assert sr.render(t["id"], {}) == t["text"]


# ---------- 만들기: 실제로 돌려 손으로 센 값과 ----------

def test_counts_and_groups(c):
    done = out("e06", {})
    assert go(c, done) == [{"전체 개수": 12}]
    assert [{r["store"]: r["전체 고유 개수"]} for r in go(c, out("e08", {"무엇": "store", "대상": "uid"}))] == [{"A": 4}, {"B": 4}, {"C": 3}]
    assert go(c, out("e07", {"조건": "amt >= 100", "대상": "uid"})) == [{"전체 고유 개수": 3}]  # id 9~11의 uid u1·u2·u3
    rows = go(c, out("e09", {"무엇": "store", "N": 4}))
    assert [(r["store"], r["전체 개수"]) for r in rows] == [("A", 5), ("B", 4)]  # 건수 4 이상만, 많은 순
    rows = go(c, out("e10", {"조건": "amt >= 60", "무엇": "store"}))
    assert [(r["store"], r["전체 개수"]) for r in rows] == [("B", 4), ("C", 3)]
    rows = go(c, out("e13", {"무엇": "store", "값": "amt", "집계": "sum", "N": 2}))
    assert [(r["store"], r["전체 합계"]) for r in rows] == [("C", 330.0), ("B", 300.0)]
    assert [(r["store"], r["전체 개수"]) for r in go(c, out("e11", {"컬럼": "store"}))] == [("A", 5), ("B", 4), ("C", 3)]
    assert [(r["store"], r["전체 개수"]) for r in go(c, out("e12", {"컬럼": "store", "값 목록": ["A", "C"]}))] == [("A", 5), ("C", 3)]


def test_spread_and_representatives(c):
    assert {r["store"]: r["전체 중앙값"] for r in go(c, out("e26", {"무엇": "store", "값": "amt", "종류": "중앙값"}))} == {"A": 30.0, "B": 75.0, "C": 110.0}
    assert {r["store"]: r["전체 P90"] for r in go(c, out("e26", {"무엇": "store", "값": "amt", "종류": "P90"}))}["A"] == pytest.approx(46.0)
    quantile = go(c, out("e26", {"무엇": "store", "값": "amt", "종류": "분위수 N", "P": 0.25}))
    assert {r["store"]: r["전체 분위수"] for r in quantile}["A"] == pytest.approx(20.0)
    spread = {r["store"]: r[f"전체 {sql_funcs.label_of('range')}"] for r in go(c, out("e27", {"무엇": "store", "값": "amt", "종류": "범위"}))}
    assert spread == {"A": 40.0, "B": 30.0, "C": 20.0}
    first = go(c, out("e29", {"무엇": "store", "컬럼": "uid", "대표 값 고르기": "first", "정렬 기준": "day"}))
    assert {r["store"]: r["전체 첫 값"] for r in first} == {"A": "u0", "B": "u1", "C": "u1"}
    joined = go(c, out("e30", {"무엇": "store", "컬럼": "uid", "구분자": "|", "정렬 기준": "day", "중복": "두고"}))
    assert {r["store"]: r["전체 이어 붙이기"] for r in joined}["A"] == "u0|u1|u2|u3|u0"
    distinct = go(c, out("e30", {"무엇": "store", "컬럼": "uid", "구분자": "|", "중복": "빼고"}))
    assert {r["store"]: r["전체 이어 붙이기"] for r in distinct}["A"] == "u0|u1|u2|u3"
    assert {r["store"]: r["전체 모두 참"] for r in go(c, out("e32", {"무엇": "store", "조건": "amt >= 30", "범위": "모두"}))} == {"A": False, "B": True, "C": True}
    assert {r["store"]: r["전체 하나라도 참"] for r in go(c, out("e32", {"무엇": "store", "조건": "amt >= 60", "범위": "하나라도"}))} == {"A": False, "B": True, "C": True}
    assert go(c, out("e45", {"값 A": "amt", "값 B": "id", "종류": "상관계수"}))[0]["전체 상관계수"] == pytest.approx(1.0)


def test_window_sentences_become_calc_lines(c):
    done = out("e33", {"무엇": "store", "정렬 기준": "day", "방향": "가장 이른"})
    assert done["body"]["calcs"] == ["창거르기 row_number <= 1 기준 day asc 그룹 store"]
    assert sorted(r["id"] for r in go(c, done)) == [0, 5, 9]
    assert sorted(r["id"] for r in go(c, out("e33", {"무엇": "store", "정렬 기준": "day", "방향": "가장 늦은"}))) == [4, 8, 11]
    assert sorted(r["id"] for r in go(c, out("e34", {"무엇": "store", "값": "amt", "N": 1}))) == [4, 8, 11]  # 가게마다 amt가 가장 큰 행
    diff = go(c, out("e35", {"무엇": "store", "정렬 기준": "day", "값": "amt", "보기": "차이"}))
    assert {r["id"]: r["amt_diff"] for r in diff}[1] == 10.0 and {r["id"]: r["amt_diff"] for r in diff}[0] is None and {r["id"]: r["amt_diff"] for r in diff}[5] is None
    cum = go(c, out("e36", {"정렬 기준": "day", "값": "amt", "종류": "누적 합"}))
    assert {r["id"]: r["amt_cum_sum"] for r in cum}[4] == 150.0 and {r["id"]: r["amt_cum_sum"] for r in cum}[11] == 780.0
    moving = go(c, out("e36", {"정렬 기준": "day", "값": "amt", "종류": "이동 평균", "N": 3}))
    assert {r["id"]: r["amt_moving_mean"] for r in moving}[5] == pytest.approx((40 + 50 + 60) / 3)
    ranked = go(c, out("e38", {"값": "amt", "방식": "백분위 순위"}))
    assert {r["id"]: r["percent_rank"] for r in ranked}[0] == 0.0 and {r["id"]: r["percent_rank"] for r in ranked}[11] == 1.0


def test_new_columns_from_transforms(c):
    done = out("e41", {"컬럼": "amt", "조건1": "amt >= 100", "값1": "high", "조건2": "amt >= 50", "값2": "mid", "값3": "low", "새 이름": "level"})
    assert done["body"]["calcs"] == ["식 case(col('amt') >= 100, 'high', col('amt') >= 50, 'mid', 'low') 이름 level"]
    levels = {r["id"]: r["level"] for r in go(c, done)}
    assert [levels[i] for i in range(12)] == ["low"] * 4 + ["mid"] * 5 + ["high"] * 3
    rounded = out("e40", {"새 이름": "half", "컬럼": "amt", "가공": {"func": "round", "args": {"digits": 1}}})
    assert rounded["body"]["calcs"] == ["식 round(col('amt'), 1) 이름 half"]
    assert go(c, rounded)[0]["half"] == 10.0
    first_n = out("e40", {"새 이름": "head", "컬럼": {"column": "url", "prep": "after_q"}, "가공": {"func": "upper"}})
    assert first_n["body"]["calcs"] == ["식 split_part(col('url'), '?', 1) 이름 url_after_q", "식 upper(col('url_after_q')) 이름 head"]
    assert go(c, first_n)[3]["head"] == "P3"  # ? 뒤를 자른 뒤 대문자로
    gap = out("e42", {"날짜 A": "day", "날짜 B": "day", "단위": "day"})
    assert go(c, gap)[0]["날짜차이"] == 0
    cast = out("e44", {"컬럼": "amt", "바꿀 형": "글자"})
    assert go(c, cast)[0]["amt_string"] == "10.0"
    sql = out("e47", {"새 이름": "dbl", "식": "amt * 2"})
    assert sql["body"]["calcs"] == ["SQL식 amt * 2 이름 dbl"]
    assert go(c, sql)[2]["dbl"] == 60.0


def test_time_units_and_combined_sentences(c):
    done = out("e22", {"날짜": "day", "단위": "week", "대상": "uid"})
    assert done["body"]["calcs"] == ["식 truncate(col('day'), 'week') 이름 기간"]
    rows = go(c, done)
    assert [(r["기간"], r["전체 고유 개수"]) for r in rows] == [("2025-12-29", 4), ("2026-01-05", 4), ("2026-01-12", 1)]  # 월요일 시작
    both = out("e23", {"날짜": "day", "단위": "month", "대상": "uid", "값": "amt"})
    assert [r["전체 고유 개수"] for r in go(c, both, 0)] == [4] and [r["전체 합계"] for r in go(c, both, 1)] == [780.0]
    assert both["combine"] == {"op": "ratio", "a": 1, "b": 0, "by": ["기간"], "show_b": True}  # 대상 1명당 평균 = 합계 ÷ 대상 수 = 195
    pct = out("e17", {"조건": "amt >= 60"})
    assert (go(c, pct, 0)[0]["전체 개수"], go(c, pct, 1)[0]["전체 개수"]) == (7, 12) and pct["combine"] == {"op": "percent", "a": 0, "b": 1}
    share = out("e18", {"무엇": "store"})
    assert [(r["store"], r["전체 개수"]) for r in go(c, share, 0)] == [("A", 5), ("B", 4), ("C", 3)] and go(c, share, 1) == [{"전체 개수": 12}]
    per = out("e20", {"무엇": "store", "대상": "uid", "값": "amt"})
    assert [r["전체 합계"] for r in go(c, per, 0)] == [150.0, 300.0, 330.0] and [r["전체 고유 개수"] for r in go(c, per, 1)] == [4, 4, 3]
    sub = go(c, out("e39", {"무엇": "store", "나눌 것": "uid", "값": "amt", "집계": "sum", "방식": "소계·총계"}))
    assert any(r["store"] == "(전체)" and r["전체 합계"] == 780.0 for r in sub) and any(r["store"] == "A" and r["uid"] == "(소계)" and r["전체 합계"] == 150.0 for r in sub)


def test_period_prefix_and_filters(c):
    done = out("e06", {"기간": "day 2026-01-05 ~ 2026-01-08"})
    assert done["body"]["periods"] == ["day 2026-01-05 ~ 2026-01-08"] and go(c, done) == [{"전체 개수": 4}]
    by_dict = out("e06", {"기간": {"column": "day", "start": "2026-01-10", "end": ""}})
    assert go(c, by_dict) == [{"전체 개수": 3}]
    assert sorted(r["id"] for r in go(c, out("e03", {"컬럼": "store", "값": "B", "컬럼들": ["id"]}))) == [5, 6, 7, 8]
    assert sorted(r["id"] for r in go(c, out("e04", {"컬럼": "store", "값 목록": ["A", "C"], "컬럼들": ["id"]}))) == [0, 1, 2, 3, 4, 9, 10, 11]
    assert len(go(c, out("e05", {"컬럼": "uid", "글자": {"match": "으로 시작", "text": "u1"}}))) == 3
    assert len(go(c, out("e05", {"컬럼": "uid", "글자": "u"}))) == 12
    assert len(go(c, out("e02", {"컬럼들": ["store"], "중복": "두고"}))) == 12
    assert [(r["store"], r["전체 개수"]) for r in go(c, out("e02", {"컬럼들": ["store"], "중복": "빼고"}))] == [("A", 5), ("B", 4), ("C", 3)]


def test_unsupported_templates_say_why(c):
    assert sr.compile("e41", {"컬럼": "amt", "조건1": "hello", "값1": "x", "값3": "y", "새 이름": "z"}, BASE, COLS)["reason"] == "조건의 ‘hello’ 컬럼이 이 테이블에 없어요."
    assert sr.compile("e05", {"컬럼": "uid", "글자": {"match": "엉뚱", "text": "1"}}, BASE, COLS)["reason"] == "글자 비교는 포함, 으로 시작, 으로 끝 중 하나예요."
    assert sr.compile("e41", {"컬럼": "amt", "값1": "x", "값3": "y", "새 이름": "z"}, BASE, COLS)["reason"] == "‘조건1’ 칸을 채워 주세요."
    assert sr.compile("m05", {"지표 A": "없는", "지표 B": "없는2"}, BASE, COLS)["reason"] == "지표 ‘없는’를 지표 목록에서 찾지 못했어요. 지표 파일의 지표나 내 지표를 골라 주세요."
