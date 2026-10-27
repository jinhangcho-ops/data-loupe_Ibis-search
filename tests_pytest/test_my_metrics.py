"""내 지표(my_metrics.py): scope(같은 scope 이름 겹침 금지·다른 scope 사용 금지와 맞춤 구절 예외), 의존 순환, 정의 변경(def_hash·영향받는 지표·카드), 이 테이블에 맞춰 복사,
구조 변경(쓸 수 없음 + 이유), 예전 지표 파일의 공통 정의, 캐시 열쇠, 목표 모호함. 정의만 평문 my_metrics.json에 둔다. 데이터는 테스트 안에서 지어낸다."""
import json
import re

import pytest

from core import metric_file
from metrics import my_metrics as mm

S1 = {"key": "folder:c:/data", "label": "데이터", "table": "sales"}
S2 = {"key": "folder:c:/data", "label": "데이터", "table": "orders"}
C1 = {"amt": "num", "user": "text", "day": "date", "status": "text"}
C2 = {"price": "num", "user": "text", "day": "date", "status": "text"}


def make(name, template, slots=None, scope=S1, columns=C1, **extra):
    return mm.create({"name": name, "scope": scope, "template": template, "slots": slots or {}, **extra}, columns)


def test_create_stores_only_the_definition(folder):
    m = make("ARPU", "m09", {"값": "amt", "대상": "user"}, unit="원", better="up")
    assert re.fullmatch(r"[0-9a-f]{16}", m["id"]) and re.fullmatch(r"[0-9a-f]{16}", m["def_hash"])
    assert m["slots"] == {"값": "amt", "대상": "user"} and m["deps"] == [] and m["cols"] == {"amt": "num", "user": "text"} and m["unit"] == "원" and m["better"] == "up"
    assert mm.scope_text(m) == "ARPU · sales (데이터)"
    text = (folder / mm.FILE).read_text(encoding="utf-8")
    assert json.loads(text)["metrics"][0]["name"] == "ARPU"
    assert "password" not in text and "token" not in text
    assert mm.get(m["id"])["def_hash"] == m["def_hash"]


def test_slot_checks_give_reasons(folder):
    with pytest.raises(ValueError, match="‘값’ 칸: ‘user’은 글자 컬럼이라 쓸 수 없어요\\(숫자 컬럼이 필요해요\\)."):
        make("나쁨", "m09", {"값": "user", "대상": "user"})
    with pytest.raises(ValueError, match="‘값’ 칸: ‘zz’ 컬럼이 이 테이블에 없어요."):
        make("나쁨", "m09", {"값": "zz", "대상": "user"})
    with pytest.raises(ValueError, match="‘대상’ 칸을 채워 주세요."):
        make("나쁨", "m02", {})
    with pytest.raises(ValueError, match="틀에 없는 칸이에요: 엉뚱"):
        make("나쁨", "m02", {"대상": "user", "엉뚱": 1})
    with pytest.raises(ValueError, match="합계을\\(를\\) 구할 수 없어요|합계를 구할 수 없어요"):
        make("나쁨", "m03", {"값": "status", "집계": "sum"})
    with pytest.raises(ValueError, match=r"지표 틀을 골라 주세요\(‘zz’ 틀은 지표 틀이 아니에요\)."):
        make("나쁨", "zz")
    with pytest.raises(ValueError, match="지표 이름은 1~100자"):
        make("", "m01")
    assert mm.load() == []  # 실패한 것은 저장되지 않는다


def test_names_are_unique_per_scope_only(folder):
    make("건수", "m01")
    with pytest.raises(ValueError, match="이 테이블에 ‘건수’ 지표가 이미 있어요"):
        make("건수", "m02", {"대상": "user"})
    other = make("건수", "m01", scope=S2, columns=C2)  # 다른 scope는 같은 이름이 된다
    assert other["scope"]["table"] == "orders" and len(mm.load()) == 2


def test_other_scope_metric_needs_a_copy_or_an_align_clause(folder):
    base = make("사용자 수", "m02", {"대상": "user"})
    mine = make("주문 수", "m01", scope=S2, columns=C2)
    attempt = {"name": "주문당 사용자", "scope": S2, "template": "m05", "slots": {"지표 A": mine["id"], "지표 B": base["id"]}}
    with pytest.raises(ValueError, match="‘사용자 수’은\\(는\\) 다른 테이블의 지표라 그대로 쓸 수 없어요"):
        mm.create(attempt, C2)
    # 예외: ‘{기간 단위}로 맞춰’ 또는 ‘{공통 키}로 맞춰’ 구절을 넣은 조합
    ok = mm.create({**attempt, "align": {"kind": "period", "unit": "month"}}, C2)
    assert ok["align"] == {"kind": "period", "unit": "month"} and ok["deps"] == [mine["id"], base["id"]]
    key = mm.create({**attempt, "name": "키로 맞춤", "align": {"kind": "key", "keys": [["user", "user"]]}}, C2)
    assert key["align"]["keys"] == [["user", "user"]]
    with pytest.raises(ValueError, match="기간 단위는 day, week, month 중 하나예요."):
        mm.create({**attempt, "name": "나쁜 단위", "align": {"kind": "period", "unit": "hour"}}, C2)
    with pytest.raises(ValueError, match="공통 키는 \\[\\[A 컬럼, B 컬럼\\], …\\] 모양"):
        mm.create({**attempt, "name": "나쁜 키", "align": {"kind": "key", "keys": []}}, C2)


def test_dependency_cycle_is_refused(folder):
    a = make("A", "m01")
    b = make("B", "m07", {"지표 A": a["id"], "지표 B": a["id"]})
    c = make("C", "m08", {"지표 A": b["id"], "지표 B": a["id"]})
    assert c["deps"] == [b["id"], a["id"]]
    with pytest.raises(ValueError, match="지표가 서로를 계산에 써서 순환해요: A → B → A."):
        mm.update(a["id"], {"template": "m05", "slots": {"지표 A": c["id"], "지표 B": b["id"]}}, C1)
    with pytest.raises(ValueError, match="지표가 서로를 계산에 써서 순환해요: A → A."):
        mm.update(a["id"], {"template": "m05", "slots": {"지표 A": a["id"], "지표 B": a["id"]}}, C1)
    assert mm.get(a["id"])["template"] == "m01"  # 거부하면 그대로


def test_definition_change_updates_hashes_and_lists_what_is_affected(folder):
    a = make("사용자 수", "m02", {"대상": "user"})
    b = make("1인당", "m09", {"값": "amt", "대상": "user"})
    ratio = make("비율", "m05", {"지표 A": b["id"], "지표 B": a["id"]})
    before = {m["id"]: m["def_hash"] for m in mm.load()}
    assert len(set(before.values())) == 3
    dashboards = [{"id": "d1", "name": "매출", "cards": [{"id": "c1", "title": "비율 카드", "source": {"metric_id": ratio["id"]}, "data": {"def_hash": before[ratio["id"]]}},
                                                     {"id": "c2", "title": "오래된 카드", "source": {"metric_id": a["id"]}, "data": {"def_hash": before[a["id"]]}}]}]
    done = mm.update(a["id"], {"slots": {"대상": "status"}}, C1)
    assert done["changed"] is True and done["metric"]["slots"] == {"대상": "status"}
    assert [d["name"] for d in done["dependents"]] == ["비율 · sales (데이터)"]  # 사용자 수를 쓰는 지표
    now = {m["id"]: m["def_hash"] for m in mm.load()}
    assert now[a["id"]] != before[a["id"]] and now[ratio["id"]] != before[ratio["id"]] and now[b["id"]] == before[b["id"]]  # 쓰는 쪽 지문도 바뀐다
    used = mm.used_by(a["id"], dashboards=dashboards)
    assert used["cards"] == [{"dashboard": "d1", "dashboard_name": "매출", "card": "c2", "title": "오래된 카드", "stale": True}]  # 카드가 저장한 def_hash와 다르다
    assert mm.used_by(ratio["id"], dashboards=dashboards)["cards"][0]["stale"] is True
    assert mm.is_stale(mm.get(a["id"]), before[a["id"]]) and not mm.is_stale(mm.get(a["id"]), now[a["id"]]) and not mm.is_stale(mm.get(a["id"]), None)
    renamed = mm.update(b["id"], {"name": "사람당"}, C1)  # 이름·단위는 정의가 아니다
    assert renamed["changed"] is False and renamed["metric"]["name"] == "사람당"
    with pytest.raises(ValueError, match="‘사용자 수’을\\(를\\) 쓰는 지표가 있어 지울 수 없어요: 비율"):
        mm.remove(a["id"])
    mm.remove(ratio["id"])
    assert mm.remove(a["id"])["removed"] == a["id"]


def test_copy_to_another_table_with_a_column_mapping(folder):
    a = make("사용자 수", "m02", {"대상": "user"})
    b = make("1인당", "m09", {"값": "amt", "대상": "user"})
    ratio = make("비율", "m05", {"지표 A": b["id"], "지표 B": a["id"]}, unit="배", better="up")
    with pytest.raises(ValueError, match="새 테이블에서 컬럼 짝을 골라 주세요: amt"):
        mm.copy_to(ratio["id"], S2, {}, C2)
    done = mm.copy_to(ratio["id"], S2, {"amt": "price"}, C2)
    assert [m["name"] for m in done["metrics"]] == ["1인당", "사용자 수", "비율"]  # 의존하는 지표도 함께, 의존이 먼저
    copied = {m["name"]: m for m in done["metrics"]}
    assert copied["1인당"]["slots"] == {"값": "price", "대상": "user"} and copied["1인당"]["scope"]["table"] == "orders"
    assert copied["비율"]["deps"] == [copied["1인당"]["id"], copied["사용자 수"]["id"]]  # 새 id로 이어진다
    assert set(done["id_map"]) == {a["id"], b["id"], ratio["id"]} and not set(done["id_map"].values()) & set(done["id_map"])
    assert copied["비율"]["unit"] == "배" and copied["비율"]["better"] == "up" and copied["비율"]["id"] != ratio["id"]
    assert mm.get(ratio["id"])["scope"]["table"] == "sales"  # 원래 지표는 그대로
    again = mm.copy_to(a["id"], S2, {}, C2, name="사용자 수")  # 새 scope에 같은 이름이 있으면 이름을 구분한다
    assert again["metrics"][0]["name"] == "사용자 수 (복사)"


def test_copy_renames_columns_inside_conditions_and_orders(folder):
    m = make("처음 값 기준", "m04", {"컬럼": "status", "값": "paid", "대상": "user", "정렬 기준": "day desc"})
    done = mm.copy_to(m["id"], S2, {"status": "state", "day": "date2"}, {"state": "text", "user": "text", "date2": "date"})
    assert done["metrics"][0]["slots"] == {"컬럼": "state", "값": "paid", "대상": "user", "정렬 기준": "date2 desc"}


def test_structure_change_makes_a_metric_unusable_with_a_reason(folder):
    a = make("사용자 수", "m02", {"대상": "user"})
    b = make("1인당", "m09", {"값": "amt", "대상": "user"})
    ratio = make("비율", "m05", {"지표 A": b["id"], "지표 B": a["id"]})
    assert mm.status(ratio, C1) == {"usable": True, "reason": ""}
    gone = {k: v for k, v in C1.items() if k != "amt"}
    assert mm.status(b, gone) == {"usable": False, "reason": "‘amt’ 컬럼이 테이블에서 없어졌어요."}
    assert mm.status(b, {**C1, "amt": "text"}) == {"usable": False, "reason": "‘amt’의 타입이 숫자에서 글자(으)로 바뀌었어요."}
    assert mm.status(ratio, gone) == {"usable": False, "reason": "의존하는 지표 ‘1인당’을(를) 쓸 수 없어요(‘amt’ 컬럼이 테이블에서 없어졌어요)."}
    assert mm.status(a, gone)["usable"] is True  # amt를 안 쓰는 지표는 영향 없다


def test_validate_catches_cycles_without_saving(folder):
    a = make("A", "m01")
    made = mm.build({"name": "B", "scope": S1, "template": "m07", "slots": {"지표 A": a["id"], "지표 B": a["id"]}}, C1, mm.load())
    assert made["deps"] == [a["id"]] and len(mm.load()) == 1


def test_legacy_file_metrics_are_common_definitions(folder, tmp_path):
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps({"metrics": {"CTR": {"numerator": {"agg": "count", "where": "status = click"}, "denominator": {"agg": "count", "where": "status = view"}, "format": "percent"},
                                            "매출": {"numerator": {"agg": "sum", "column": "amt"}}}}), encoding="utf-8")
    metric_file.save_file_path(str(path))
    listed = mm.legacy_list()
    assert [(m["id"], m["name"], m["scope"], m["legacy"]) for m in listed] == [("legacy:CTR", "CTR", None, True), ("legacy:매출", "매출", None, True)]



def test_goal_lookup_can_be_ambiguous(folder):
    m = make("매출", "m03", {"값": "amt", "집계": "sum"})
    rows = [{"name": "매출", "value": 100, "period": "2026-01", "column": "", "scope": "", "line": 2}, {"name": "매출", "value": 200, "period": "2026-02", "column": "", "scope": "", "line": 3}]
    from core import targets
    found = targets.lookup(rows, m["name"])  # 대시보드 목표 카드가 쓰는 찾기(dash_calc)
    assert found["row"] is None and found["ambiguous"] is True and "어느 기간인지 알 수 없어요 (2026-01, 2026-02)" in found["error"]
    assert targets.lookup(rows, m["name"], "2026-02")["row"]["value"] == 200
    assert mm.used_by(m["id"], dashboards=[], goals=rows)["goals"] == rows  # 정의를 고치면 이름이 같은 목표 줄도 알린다


def test_scope_must_name_connection_and_table(folder):
    with pytest.raises(ValueError, match="지표가 속한 연결\\(scope.key\\)이 필요해요."):
        mm.check_scope({"table": "sales"})
    with pytest.raises(ValueError, match="지표가 속한 테이블\\(scope.table\\)이 필요해요."):
        mm.check_scope({"key": "k"})
    with pytest.raises(ValueError, match="계정"):
        mm.check_scope({"key": "k", "table": "t", "password": "secret"})
    with pytest.raises(ValueError, match="흐름 보기가 없어져"):  # 흐름 보기를 없애 흐름(flow_id)에 새로 묶지 않는다
        mm.check_scope({"key": "k", "flow_id": "f1"})
