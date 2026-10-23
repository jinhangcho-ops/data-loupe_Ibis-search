"""문장 실행(sentence_exec.py): 탐색 틀 47개와 지표 틀 15개가 모두 만들어지고 실제로 돌아 손으로 센 값과 맞는다(화면이 나누거나 합치지 않는다).
ev: id 0~11, store = A(id 0~4)·B(5~8)·C(9~11), amt = (id+1)*10(합 780), uid = 'u' + id % 4, day = 2026-01-01(목요일) + id, url = 'p<id>?x=1'."""
import json

import duckdb
import pytest

import metrics
import my_metrics as mm
import sentence_exec as ex
import sentences
import server

COLS = {"id": "num", "uid": "text", "store": "text", "amt": "num", "day": "date", "tags": "array", "url": "text"}
BASE = {"base": "ev", "joins": [], "periods": [], "conditions": [], "calcs": []}
SCOPE = {"key": "k", "label": "k", "table": "ev"}


@pytest.fixture
def c(folder, tmp_path):
    import dash_app  # noqa: F401
    import flow_app  # noqa: F401
    import prep_app  # noqa: F401
    from app import app
    path = tmp_path / "x.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("""create table ev as select i::int as id, 'u' || (i % 4) as uid, case when i < 5 then 'A' when i < 9 then 'B' else 'C' end as store,
                   ((i + 1) * 10)::double as amt, date '2026-01-01' + i::int as day, ['a', 'b'] as tags, 'p' || i || '?x=1' as url from range(12) t(i)""")
    raw.execute("create table ev2 as select * from (values (0, 'x'), (0, 'y'), (1, 'z')) t(id, w)")
    raw.execute("""create table jt as select 1::int as id, '{"k": "seven"}'::JSON as js""")
    raw.close()
    (tmp_path / "m.json").write_text(json.dumps({"metrics": {"클릭": {"numerator": {"agg": "count", "where": "store = A"}}, "노출": {"numerator": {"agg": "count"}}}}), encoding="utf-8")
    metrics.save_file_path(str(tmp_path / "m.json"))
    client = app.test_client()
    client.get("/?key=" + server.KEY)
    assert client.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(path)}).status_code == 200
    yield client
    client.post("/api/disconnect", json={})


def go(tid, slots, body=BASE):
    return ex.run_sentence(tid, slots, body, COLS)


def pairs(done, key, value):
    return [(r[key], r[value]) for r in done["rows"]]


def first(done):
    return done["rows"][0][done["columns"][-1]]


A5 = [("A", 5), ("B", 4), ("C", 3)]

# (틀, 빈칸 값, 결과를 확인하는 함수). 손으로 센 값은 확인 함수 옆 주석.
TABLE = [
    ("e01", {}, lambda d: len(d["rows"]) == 12),
    ("e02", {"컬럼들": ["store"], "중복": "빼고"}, lambda d: pairs(d, "store", "전체 개수") == A5),
    ("e03", {"컬럼": "store", "값": "B", "컬럼들": ["id"]}, lambda d: sorted(r["id"] for r in d["rows"]) == [5, 6, 7, 8]),
    ("e04", {"컬럼": "store", "값 목록": ["A", "C"], "컬럼들": ["id"]}, lambda d: len(d["rows"]) == 8),
    ("e05", {"컬럼": "uid", "글자": {"match": "으로 끝", "text": "3"}}, lambda d: sorted(r["id"] for r in d["rows"]) == [3, 7, 11]),  # uid u3: id 3·7·11
    ("e06", {}, lambda d: first(d) == 12),
    ("e07", {"조건": ["amt >= 110", "store = C"], "대상": "uid"}, lambda d: first(d) == 2),  # id 10·11 → u2·u3
    ("e08", {"무엇": "store", "대상": "uid"}, lambda d: pairs(d, "store", "전체 고유 개수") == [("A", 4), ("B", 4), ("C", 3)]),
    ("e09", {"무엇": "store", "N": 4}, lambda d: pairs(d, "store", "전체 개수") == [("A", 5), ("B", 4)]),
    ("e10", {"조건": "amt >= 60", "무엇": "store"}, lambda d: pairs(d, "store", "전체 개수") == [("B", 4), ("C", 3)]),
    ("e11", {"컬럼": "store"}, lambda d: pairs(d, "store", "전체 개수") == A5),
    ("e12", {"컬럼": "store", "값 목록": ["A", "C"]}, lambda d: pairs(d, "store", "전체 개수") == [("A", 5), ("C", 3)]),
    ("e13", {"무엇": "store", "값": "amt", "집계": "sum", "N": 2}, lambda d: pairs(d, "store", "전체 합계") == [("C", 330.0), ("B", 300.0)]),
    ("e14", {"무엇": "store", "대상": "uid", "정확도": "정확"}, lambda d: pairs(d, "store", "전체 고유 개수") == [("A", 4), ("B", 4), ("C", 3)]),
    ("e15", {"단계": "store", "대상": "uid"}, lambda d: pairs(d, "store", "전체 고유 개수") == [("A", 4), ("B", 4), ("C", 3)]),
    ("e17", {"조건": "amt >= 60"}, lambda d: first(d) == pytest.approx(700 / 12)),  # 7 ÷ 12 × 100
    ("e18", {"무엇": "store"}, lambda d: [(r["store"], round(r["비중"], 4)) for r in d["rows"]] == [("A", round(5 / 12, 4)), ("B", round(4 / 12, 4)), ("C", 0.25)]),
    ("e19", {"무엇": "store", "조건": "amt >= 40", "대상": "uid"}, lambda d: pairs(d, "store", "값") == [("A", 0.5), ("B", 1.0), ("C", 1.0)]),  # A: id 3·4 → u3·u0 2명 ÷ 4명
    ("e20", {"무엇": "store", "대상": "uid", "값": "amt"}, lambda d: pairs(d, "store", "값") == [("A", 37.5), ("B", 75.0), ("C", 110.0)]),  # 150÷4, 300÷4, 330÷3
    ("e21", {"지표 A": "클릭", "지표 B": "노출"}, lambda d: first(d) == pytest.approx(5 / 12)),
    ("e22", {"날짜": "day", "단위": "week", "대상": "uid"}, lambda d: pairs(d, "기간", "전체 고유 개수") == [("2025-12-29", 4), ("2026-01-05", 4), ("2026-01-12", 1)]),
    ("e23", {"날짜": "day", "단위": "month", "대상": "uid", "값": "amt"}, lambda d: first(d) == 195.0),  # 780 ÷ 4
    ("e24", {"대상": "uid", "컬럼": "store", "정렬 기준": "day desc", "집계": "count"}, lambda d: pairs(d, "처음_store", "전체 개수") == [("B", 3), ("C", 9)]),  # 사용자별 가장 늦은 가게: u0 B · 나머지 C
    ("e25", {"목록 컬럼": "tags", "무엇": "store", "집계": "count", "N": 10}, lambda d: pairs(d, "store", "전체 개수") == [("A", 10), ("B", 8), ("C", 6)]),
    ("e26", {"무엇": "store", "값": "amt", "종류": "중앙값"}, lambda d: pairs(d, "store", "전체 중앙값") == [("A", 30.0), ("B", 75.0), ("C", 110.0)]),
    ("e27", {"무엇": "store", "값": "amt", "종류": "표준편차", "기준": "표본"}, lambda d: d["rows"][0]["전체 표준편차(표본)"] == pytest.approx(250 ** 0.5)),
    ("e28", {"무엇": "store", "컬럼": "uid"}, lambda d: d["rows"][0]["전체 최빈값"] == "u0"),  # A: u0이 id 0·4
    ("e29", {"무엇": "store", "컬럼": "uid", "대표 값 고르기": "max"}, lambda d: [r["전체 최대"] for r in d["rows"]] == ["u3", "u3", "u3"]),
    ("e30", {"무엇": "store", "컬럼": "uid", "구분자": "|", "정렬 기준": "day", "중복": "두고"}, lambda d: d["rows"][0]["전체 이어 붙이기"] == "u0|u1|u2|u3|u0"),
    ("e31", {"무엇": "store", "컬럼": "uid"}, lambda d: sorted(d["rows"][0]["전체 목록 모으기"]) == ["u0", "u0", "u1", "u2", "u3"]),
    ("e32", {"무엇": "store", "조건": ["amt >= 30", "store != A"], "범위": "모두"}, lambda d: pairs(d, "store", "전체 모두 참") == [("A", False), ("B", True), ("C", True)]),
    ("e33", {"무엇": "store", "정렬 기준": "day", "방향": "가장 이른"}, lambda d: sorted(r["id"] for r in d["rows"]) == [0, 5, 9]),
    ("e34", {"무엇": "store", "값": "amt", "N": 1}, lambda d: sorted(r["id"] for r in d["rows"]) == [4, 8, 11]),
    ("e35", {"무엇": "store", "정렬 기준": "day", "값": "amt", "보기": "차이"}, lambda d: {r["id"]: r["amt_diff"] for r in d["rows"]}[1] == 10.0),
    ("e36", {"정렬 기준": "day", "값": "amt", "종류": "누적 합"}, lambda d: {r["id"]: r["amt_cum_sum"] for r in d["rows"]}[11] == 780.0),
    ("e37", {"무엇": "store", "나눌 것": "uid", "값": "amt"}, lambda d: {(r["store"], r["uid"]): r["비중"] for r in d["rows"]}[("A", "u0")] == pytest.approx(0.4)),  # u0: 10+50 = 60 ÷ 150
    ("e38", {"값": "amt", "방식": "백분위 순위"}, lambda d: {r["id"]: r["percent_rank"] for r in d["rows"]}[11] == 1.0),
    ("e39", {"무엇": "store", "나눌 것": "uid", "값": "amt", "집계": "sum", "방식": "소계·총계"}, lambda d: any(r["store"] == "(전체)" and r["전체 합계"] == 780.0 for r in d["rows"])),
    ("e40", {"새 이름": "half", "컬럼": "amt", "가공": {"func": "round", "args": {"digits": 1}}}, lambda d: d["rows"][0]["half"] == 10.0),
    ("e41", {"컬럼": "amt", "조건1": ["amt >= 100", "store = C"], "값1": "high", "조건2": {"any": ["amt >= 50", "amt <= 10"]}, "값2": "mid", "값3": "low", "새 이름": "level"},
     lambda d: [r["level"] for r in sorted(d["rows"], key=lambda r: r["id"])] == ["mid"] + ["low"] * 3 + ["mid"] * 5 + ["high"] * 3),
    ("e42", {"날짜 A": "day", "날짜 B": "day", "단위": "day"}, lambda d: d["rows"][0]["날짜차이"] == 0),
    ("e44", {"컬럼": "amt", "바꿀 형": "글자"}, lambda d: d["rows"][0]["amt_string"] == "10.0"),
    ("e45", {"값 A": "amt", "값 B": "id", "종류": "상관계수"}, lambda d: first(d) == pytest.approx(1.0)),
    ("e46", {"무엇": "store", "대상": "uid"}, lambda d: pairs(d, "store", "전체 고유 개수(근사)") == [("A", 4), ("B", 4), ("C", 3)]),
    ("e47", {"새 이름": "dbl", "식": "amt * 2"}, lambda d: d["rows"][2]["dbl"] == 60.0),
]


@pytest.mark.parametrize("tid,slots,ok", TABLE, ids=[t[0] for t in TABLE])
def test_explore_template_runs_end_to_end(c, tid, slots, ok):
    done = go(tid, slots)
    assert ok(done), done


def test_templates_that_need_other_tables(c):
    both = go("e16", {"테이블 A": "ev", "테이블 B": "ev2", "연결 컬럼": "id"})
    assert pairs(both, "id", "전체 개수") == [(0, 2), (1, 1)]  # ev2에 id 0이 두 줄, 1이 한 줄
    json_body = {"base": "jt", "joins": [], "periods": [], "conditions": [], "calcs": [], "columns": ["id, json_k"]}
    got = ex.run_sentence("e43", {"JSON 컬럼": "js", "경로": "k"}, json_body, {"id": "num", "js": "json"})
    assert got["rows"] == [{"id": 1, "json_k": "seven"}]


def test_every_explore_template_has_an_end_to_end_case():
    covered = {t[0] for t in TABLE} | {"e16", "e43"}
    assert covered == {t["id"] for t in sentences.EXPLORE}  # 47개 모두


def test_condition_lists_and_or_groups(c):
    assert pairs(go("e32", {"무엇": "store", "조건": {"any": ["amt >= 120", "amt <= 10"]}, "범위": "하나라도"}), "store", "전체 하나라도 참") == [("A", True), ("B", False), ("C", True)]
    assert first(go("e07", {"조건": {"any": ["store = A", "store = B"]}, "대상": "uid"})) == 4
    assert sorted(r["id"] for r in go("e05", {"컬럼": "url", "글자": {"match": "으로 끝", "text": "=1"}})["rows"]) == list(range(12))
    assert len(go("e05", {"컬럼": "uid", "글자": {"match": "으로 시작", "text": "u1"}})["rows"]) == 3
    assert len(go("e05", {"컬럼": "uid", "글자": "u"})["rows"]) == 12


def test_ends_condition_works_in_search_lines(c):
    resp = c.post("/api/query", json={**BASE, "conditions": ["uid ends 3"]})
    assert resp.get_json()["count"] == 3


# ---------- 지표 틀 ----------

def mine(name, template, slots, **kw):
    return mm.create({"name": name, "scope": SCOPE, "template": template, "slots": slots, **kw}, COLS)


PERIOD = {"column": "day", "start": "2026-01-05", "end": "2026-01-08"}
METRICS = [
    ("m01", {}, lambda v: v == 12),
    ("m02", {"대상": "uid"}, lambda v: v == 4),
    ("m03", {"값": "amt", "집계": "sum"}, lambda v: v == 780.0),
    ("m04", {"컬럼": "store", "값": "C", "대상": "uid", "정렬 기준": "day desc"}, lambda v: v == 3),  # 가장 늦은 가게가 C인 사용자 u1·u2·u3
    ("m05", {"지표 A": "클릭", "지표 B": "노출"}, lambda v: v == pytest.approx(5 / 12)),
    ("m06", {"지표 A": "클릭", "지표 B": "노출"}, lambda v: v == pytest.approx(500 / 12)),
    ("m07", {"지표 A": "클릭", "지표 B": "노출"}, lambda v: v == -7),
    ("m08", {"지표 A": "클릭", "지표 B": "노출"}, lambda v: v == 17),
    ("m09", {"값": "amt", "대상": "uid"}, lambda v: v == 195.0),
    ("m10", {"대상": "uid", "값": "amt", "집계": "sum"}, lambda v: v == 195.0),  # 사용자별 150·180·210·240의 평균
    ("m11", {"조건": "store = A", "N": 2, "대상": "uid"}, lambda v: v == 0.25),  # A 행을 두 번 이상 한 사용자는 u0뿐
    ("m12", {"단계 A": "store = A", "단계 B": "store = C", "대상": "uid"}, lambda v: v == 0.75),  # A를 한 4명 중 C도 한 사람 u1·u2·u3
    ("m13", {"단위": "week", "지표": "노출", "기간": {"column": "day"}}, lambda v: v == 4.0),  # 주별 4·7·1건의 평균
    ("m14", {"지표": "클릭", "기간": PERIOD}, lambda v: v == pytest.approx(-0.75)),  # 01-05~08의 A 1건 ÷ 직전 01-01~04의 A 4건 − 1
    ("m15", {"지표": "노출", "정렬 기준": "day"}, None),  # 날짜마다 1건씩 12일 누적
]


@pytest.mark.parametrize("tid,slots,ok", METRICS, ids=[t[0] for t in METRICS])
def test_metric_template_runs_end_to_end(c, tid, slots, ok):
    done = go(tid, slots)
    if tid == "m15":
        assert [r["누적"] for r in done["rows"]] == list(range(1, 13)), done
    else:
        assert ok(first(done)), done


def test_every_metric_template_has_a_case():
    assert {t[0] for t in METRICS} == {t["id"] for t in sentences.METRICS}  # 15개 모두


def test_metric_definitions_are_given_when_one_definition_can_say_it(c):
    import sentence_run as sr
    assert sr.compile("m09", {"값": "amt", "대상": "uid"}, BASE, COLS)["definition"]["denominator"]["agg"] == "count_distinct"
    assert sr.compile("m06", {"지표 A": "클릭", "지표 B": "노출"}, BASE, COLS)["definition"]["format"] == "percent"
    assert sr.compile("m07", {"지표 A": "클릭", "지표 B": "노출"}, BASE, COLS)["definition"] is None  # 빼기는 정의 한 개로 못 나타내서 요청으로만


def test_my_metrics_can_be_used_in_sentences_including_combinations(c):
    users = mine("고유 사용자", "m02", {"대상": "uid"})
    rows = mine("건수", "m01", {})
    share = mine("사용자당 건수", "m05", {"지표 A": rows["id"], "지표 B": users["id"]})
    assert first(go("m05", {"지표 A": users["id"], "지표 B": rows["id"]})) == pytest.approx(4 / 12)
    assert first(go("m08", {"지표 A": share["id"], "지표 B": share["id"]})) == pytest.approx(2 * 3.0)  # 12 ÷ 4 = 3의 두 배(조합을 풀어서 구한다)
    assert first(go("m07", {"지표 A": share["id"], "지표 B": "클릭"})) == pytest.approx(3.0 - 5)
    with pytest.raises(ValueError, match="값마다 나눠 구할 수 없어요"):
        go("m15", {"지표": share["id"], "정렬 기준": "day"})


def test_sentence_run_route(c):
    resp = c.post("/api/sentence/run", json={"template": "e20", "slots": {"무엇": "store", "대상": "uid", "값": "amt"}, "body": BASE})
    assert resp.status_code == 200
    assert [(r["store"], r["값"]) for r in resp.get_json()["rows"]] == [("A", 37.5), ("B", 75.0), ("C", 110.0)]
    bad = c.post("/api/sentence/run", json={"template": "e08", "slots": {}, "body": BASE})
    assert bad.status_code == 400 and bad.get_json()["error"] == "‘나눌 기준 컬럼’ 칸을 채워 주세요."
    assert c.post("/api/sentence/run", json={"template": "e08", "slots": {}, "body": {}}).get_json()["error"] == "기준 테이블이 있는 검색 body가 필요해요."
