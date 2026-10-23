"""검색 조건의 세 보기(단계 카드·흐름·문장)가 같은 검색 body를 본다: 단계 카드 body → 흐름 → body, 흐름을 고쳐도 단계 카드가 쓰는 body, 문장이 만든 요청이 같은 테이블·조인·기간·조건을 쓰는지.
문장 틀 목록 라우트(/api/sentences)와 질문 묶음 저장 라우트(/api/question_sets)의 모양·한도·차례를 기다리지 않는지. 데이터는 테스트 안에서 지어낸다."""
import pytest

from query import flow
from query import flow_app  # noqa: F401
from query import sentence_run
from query import sentences
from core import server

BODY = {"schema": "", "base": "sales", "joins": ["shops inner shop_id=shop_id"], "periods": ["day 2026-03-01 ~ 2026-03-31"],
        "conditions": ["amt >= 20", {"any": ["status = 완료", {"line": "status like 배송", "quotes": False}]}], "calcs": ["식 amt * 2 이름 두배"], "sorts": ["amt desc"], "columns": ["id, amt"], "limit": "20"}
COLUMNS = {"id": "num", "shop_id": "num", "amt": "num", "day": "date", "status": "text", "country": "text"}


def post(client, path, **body):
    resp = client.post(path, json=body)
    return resp.status_code, resp.get_json()


def test_cards_flow_cards_round_trip_same_body(client):
    """단계 카드의 body → 흐름(from_body) → body(to_body)가 처음 body와 같고(정렬·표시 컬럼·행 수 포함), 흐름 단계 종류와 차례가 body의 단계 차례와 같다."""
    code, found = post(client, "/api/flow/from_body", body=BODY)
    assert code == 200
    kinds = [n["kind"] for n in found["graph"]["nodes"]]
    assert kinds == ["table", "join", "table", "period", "condition", "condition", "calc"]  # 조건 두 줄(둘째는 or로 묶인 한 단계)
    assert found["graph"]["extra"] == {"sorts": ["amt desc"], "columns": ["id, amt"], "limit": "20"}
    code, back = post(client, "/api/flow/to_body", graph=found["graph"])
    assert code == 200 and back["body"] == BODY


def test_flow_edits_come_back_to_the_cards(client):
    """흐름에서 조건 단계의 글을 고치고 단계를 지우면, 단계 카드로 돌아올 때 그 고친 body가 된다. 집계(꼬리)를 붙여도 target으로 끊으면 같은 body다."""
    graph = flow.from_body(BODY)
    node = next(n for n in graph["nodes"] if n["kind"] == "condition")
    node["params"]["cond"] = "amt >= 99"
    code, back = post(client, "/api/flow/to_body", graph=graph)
    assert code == 200 and back["body"]["conditions"][0] == "amt >= 99"
    tail = {"id": "agg", "kind": "aggregate", "params": {"by": ["country"], "series": [{"agg": "sum", "y": "amt", "name": "합"}]}}
    last = [n for n in graph["nodes"] if not flow.outputs(graph, n["id"])][-1]["id"]
    with_tail = {**graph, "nodes": [*graph["nodes"], tail], "edges": [*graph["edges"], [last, "agg"]]}  # extra(정렬·표시 컬럼·행 수)는 그대로
    code, cut = post(client, "/api/flow/to_body", graph=with_tail, target=last)
    assert code == 200 and "aggregate" not in cut["body"] and cut["body"] == back["body"]
    code, whole = post(client, "/api/flow/to_body", graph=with_tail)
    assert code == 200 and whole["body"]["aggregate"]["by"] == ["country"]


def test_flow_that_cannot_be_cards_gives_server_reason(client):
    """단계 카드가 나타낼 수 없는 흐름(조인 앞 가지에 조건)은 서버가 이유를 주고(화면이 그대로 보인다) 400이다."""
    graph = {"nodes": [{"id": "t1", "kind": "table", "params": {"table": "a"}}, {"id": "t2", "kind": "table", "params": {"table": "b"}}, {"id": "c", "kind": "condition", "params": {"cond": "x = 1"}},
                       {"id": "j", "kind": "join", "params": {"how": "inner", "keys": [["id", "id"]]}}], "edges": [["t1", "j"], ["t2", "c"], ["c", "j"]]}
    code, body = post(client, "/api/flow/to_body", graph=graph)
    assert code == 400 and body["error"] == flow.body_problem(graph)


def test_sentence_runs_use_the_same_body(client):
    """어느 문장이든 만든 요청(runs)의 body가 단계 카드 body의 테이블·조인·기간·조건을 그대로 이어받는다(문장 보기도 같은 body)."""
    code, found = post(client, "/api/sentence/compile", template="e13", slots={"무엇": "country", "값": "amt", "집계": {"agg": "sum"}, "N": 3}, body=BODY, columns=COLUMNS)
    assert code == 200 and found["ok"], found
    for run in found["runs"]:
        body = run["body"]
        assert body["base"] == BODY["base"] and body["joins"] == BODY["joins"] and body["periods"][0] == BODY["periods"][0]
        assert body["conditions"][:2] == BODY["conditions"][:2]
    assert found["runs"][0]["route"] == "/api/pivot"


def test_sentence_period_slot_is_added_to_the_body(client):
    """문장의 {기간}(날짜 컬럼 + 읽은 시작·끝)은 body의 기간 줄이 되어 단계 카드의 기간과 같은 모양이다."""
    slots = {"무엇": "country", "값": "amt", "집계": {"agg": "sum"}, "N": 3, "기간": {"column": "day", "start": "2026-04-01", "end": "2026-04-30"}}
    code, found = post(client, "/api/sentence/compile", template="e13", slots=slots, body={"base": "sales"}, columns=COLUMNS)
    assert code == 200 and found["body"]["periods"] == ["day 2026-04-01 ~ 2026-04-30"]
    code, bad = post(client, "/api/sentence/check", template="e13", slots={**slots, "무엇": "없는컬럼"}, columns=COLUMNS)
    assert bad["ok"] is False and any(i["level"] == "bad" and "없어요" in i["text"] for i in bad["issues"])


# ---------- 문장 틀 목록 · 질문 묶음 ----------

def test_sentence_catalog_route(client):
    """문장 틀 목록은 모든 화면에 싣지 않고 이 라우트로 한 번 받는다. 탐색 47개 + 지표 15개, 컬럼 가공 옵션, 다른 작업이 연결을 쓰는 중에도 답한다."""
    assert "/api/sentences" in server.FREE and "sentences" not in server.CONSTANTS
    assert server.turn.acquire(timeout=1)
    try:
        resp = client.get("/api/sentences")
    finally:
        server.turn.release()
    data = resp.get_json()
    assert resp.status_code == 200 and len(data["templates"]) == 65 and sum(t["kind"] == "explore" for t in data["templates"]) == 50
    assert data["preps"] == sentence_run.PREPS and data["max_top"] == sentence_run.MAX_TOP and data["slot_types"] == list(sentences.SLOT_TYPES)


def test_question_sets_save_list_overwrite_delete(client, folder):
    """질문 묶음: 이름으로 저장·덮어쓰기·지우기, 설정 폴더의 question_sets.json, 차례를 기다리지 않는다."""
    item = {"shared": True, "period": {"column": "day", "text": "이번 달"}, "items": [{"template": "e06", "slots": {}}, {"template": "e13", "slots": {"무엇": "country"}, "period": {"column": "day", "text": "지난달"}}]}
    assert "/api/question_sets" in server.FREE
    assert client.get("/api/question_sets").get_json() == {"sets": {}}
    code, saved = post(client, "/api/question_sets", name="  월간 점검 ", set=item)
    assert code == 200 and saved["sets"] == {"월간 점검": item}
    assert (folder / "question_sets.json").exists()
    item2 = {**item, "shared": False}
    assert post(client, "/api/question_sets", name="월간 점검", set=item2)[1]["sets"]["월간 점검"]["shared"] is False
    assert client.get("/api/question_sets").get_json()["sets"]["월간 점검"]["shared"] is False
    assert post(client, "/api/question_sets", name="월간 점검", set=None)[1] == {"sets": {}}


@pytest.mark.parametrize("name, item, text", [
    ("", {"items": [{"template": "e06"}]}, "이름은 1자 이상 50자 이하"),
    ("묶음", {"items": []}, "1개 이상 30개 이하"),
    ("묶음", {"items": [{"template": "e06"}] * 31}, "1개 이상 30개 이하"),
    ("묶음", {"items": [{"template": "zz99"}]}, "문장 틀이 없습니다"),
    ("묶음", {"items": ["e06"]}, "올바르지 않아요"),
    ("묶음", {"no": 1}, "1개 이상 30개 이하"),
    ("묶음", {"items": [{"template": "e06", "slots": {"x": "가" * 40_000}}]}, "너무 큽니다"),
])
def test_question_sets_reject_bad_input(client, name, item, text):
    code, body = post(client, "/api/question_sets", name=name, set=item)
    assert code == 400 and text in body["error"]
    assert client.get("/api/question_sets").get_json() == {"sets": {}}
