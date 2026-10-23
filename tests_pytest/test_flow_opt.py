"""흐름 최적화(flow_opt.py): 규칙마다 결과가 같은지(왼쪽 조인의 오른쪽 조건은 내리지 않음, 고유가 확인된 조인만 뺌, 고유 개수·중앙값은 먼저 집계하지 않음),
다르게 나오는 규칙은 빼고, 원격 DB는 값을 읽는 조회를 확인 없이 보내지 않는다. 값은 손으로 센 작은 표와 맞춘다. 데이터는 테스트 안에서 지어낸다.
sales: id 0~11, shop_id = id % 3, amt = (id+1)*10 → 가게 0: 10·40·70·100(합 220), 가게 1: 20·50·80·110(합 260), 가게 2: 30·60·90·120(합 300).
shops: 가게 0·1은 KR(이름 A·B), 가게 2는 US(C). KR 합 480(8건, 평균 60, 최소 10, 최대 110), US 합 300(4건, 평균 75, 최소 30, 최대 120)."""
import duckdb
import pytest
from lazy import ibis

from query import flow
from query import flow_opt
from core import held
from conftest import spy_queries


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "o.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("create table sales as select i as id, (i % 3)::int as shop_id, ((i + 1) * 10)::double as amt, date '2026-01-01' + i::int as day, repeat('m', 20) as memo from range(12) t(i)")
    raw.execute("create table shops as select i::int as shop_id, chr(65 + i::int) as name, case when i < 2 then 'KR' else 'US' end as country from range(3) t(i)")
    raw.execute("create table dupshops as select * from (values (0, 'x'), (0, 'y'), (1, 'z'), (2, 'w')) t(shop_id, label)")
    raw.execute("create table lk as select * from (values (1, 10), (1, 20), (2, 30)) t(k, v)")
    raw.execute("create table rk as select * from (values (1, 'a'), (1, 'b'), (3, 'c')) t(k, w)")
    raw.close()
    con = ibis.duckdb.connect(str(path))
    yield con
    con.disconnect()


def getter(con):
    return lambda name, schema=None: con.table(name)


TOTAL = [{"agg": "sum", "y": "amt", "name": "total"}]


def flow_of(joins, aggregate, conditions=()):
    return flow.from_body({"base": "sales", "joins": joins, "conditions": list(conditions), "aggregate": aggregate})


def by_country(result):
    return {r["country"]: r for r in result.to_dicts()}


def rule(found, no):
    return next(r for r in found["rules"] if r["no"] == no)


def test_left_join_right_side_condition_is_not_pushed(db):
    graph = flow_of(["shops left shop_id=shop_id"], {"by": ["country"], "series": TOTAL}, ["country = KR"])
    found = flow_opt.propose(graph, getter(db), db, source_key="k")
    first = rule(found, 1)
    assert first["items"] == [] and first["status"] == "skipped"
    assert first["refused"][0]["text"] == "조건 ‘country = KR’은 left 조인의 오른쪽 컬럼을 써서 내리지 않았어요(내리면 짝 없는 행의 처리가 달라져 결과가 바뀌어요)."
    # 억지로 내리면 결과가 달라진다(US 가게의 판매가 country가 빈 값인 줄로 남는다)
    naive = flow.duplicate(graph)
    cond = next(n for n in naive["nodes"] if n["kind"] == "condition")
    users = next(n for n in naive["nodes"] if n["kind"] == "table" and n["params"]["table"] == "shops")
    join = next(n for n in naive["nodes"] if n["kind"] == "join")
    naive = flow_opt.splice_in(flow_opt.splice_out(naive, cond["id"]), cond, users["id"], join["id"])
    target = flow_opt.target_of(graph)
    right, wrong = flow_opt.result_of(graph, target, getter(db)), flow_opt.result_of(naive, target, getter(db))
    assert by_country(right) == {"KR": {"country": "KR", "total": 480.0}}
    assert by_country(wrong)["KR"]["total"] == 480.0 and by_country(wrong)[None]["total"] == 300.0
    same, why = flow_opt.same_result(right, wrong)
    assert not same and why == "행 수가 1행과 2행으로 달라요"


def test_inner_join_pushes_conditions_to_each_side_and_result_is_equal(db):
    graph = flow_of(["shops inner shop_id=shop_id"], {"by": ["country"], "series": TOTAL}, ["country = KR", "amt > 50"])
    found = flow_opt.propose(graph, getter(db), db, source_key="k", only=[1])
    first = rule(found, 1)
    assert first["status"] == "kept" and first["equal"] is True
    assert [(i["side"], i["text"]) for i in first["items"]] == [("right", "조건 ‘country = KR’을 조인 오른쪽 테이블 쪽으로 내렸어요."),
                                                                ("left", "조건 ‘amt > 50’을 조인 왼쪽 테이블 쪽으로 내렸어요.")]
    now = by_country(flow_opt.result_of(found["graph"], found["target"], getter(db)))
    assert now == {"KR": {"country": "KR", "total": 70.0 + 80 + 100 + 110}}  # KR 가게(0·1)의 amt > 50: id 6·7·9·10
    # 조인 뒤에 남은 조건이 없다: 조건 단계가 모두 조인의 입력 쪽에 있다
    join = next(n for n in found["graph"]["nodes"] if n["kind"] == "join")
    kinds = {n["id"]: n["kind"] for n in found["graph"]["nodes"]}
    assert all(kinds[i] in ("condition", "table") for i in flow.inputs(found["graph"], join["id"]))
    assert flow_opt.target_of(found["graph"]) == found["target"]


def test_join_elimination_needs_unused_columns_and_a_verified_unique_key(db):
    unused = flow_of(["shops left shop_id=shop_id"], {"by": ["shop_id"], "series": TOTAL})
    found = flow_opt.propose(unused, getter(db), db, source_key="k", only=[2])
    second = rule(found, 2)
    assert second["status"] == "kept" and second["equal"] is True and "오른쪽 키가 고유해서" in second["items"][0]["text"]
    assert [n["kind"] for n in found["graph"]["nodes"]] == ["table", "aggregate"] and found["graph"]["edges"] == [["n1", "n4"]]
    assert {r["shop_id"]: r["total"] for r in flow_opt.result_of(found["graph"], found["target"], getter(db)).to_dicts()} == {0: 220.0, 1: 260.0, 2: 300.0}
    # 오른쪽 컬럼을 아래에서 쓰면 빼지 않는다
    used = flow_of(["shops left shop_id=shop_id"], {"by": ["country"], "series": TOTAL})
    refused = rule(flow_opt.propose(used, getter(db), db, source_key="k", only=[2]), 2)
    assert refused["status"] == "skipped" and "오른쪽 컬럼(country)을 아래에서 써서 빼지 않았어요" in refused["refused"][0]["text"]
    # 오른쪽 키가 고유하지 않으면 빼지 않는다: 그대로 두면 가게 0의 합이 두 번 세어진다
    dup = flow_of(["dupshops left shop_id=shop_id"], {"by": ["shop_id"], "series": TOTAL})
    refused = rule(flow_opt.propose(dup, getter(db), db, source_key="k", only=[2]), 2)
    assert refused["status"] == "skipped" and refused["items"] == []
    assert "오른쪽 키가 고유하지 않아요" in refused["refused"][0]["text"]
    joined = flow_opt.result_of(dup, flow_opt.target_of(dup), getter(db))
    assert {r["shop_id"]: r["total"] for r in joined.to_dicts()} == {0: 440.0, 1: 260.0, 2: 300.0}  # 빼면 220이 되어 결과가 달라진다


def test_inner_join_is_never_eliminated(db):
    graph = flow_of(["shops inner shop_id=shop_id"], {"by": ["shop_id"], "series": TOTAL})
    found = flow_opt.propose(graph, getter(db), db, source_key="k", only=[2])
    assert rule(found, 2)["status"] == "skipped" and not found["changed"]


def test_pre_aggregation_of_decomposable_aggregates(db):
    series = [{"agg": "sum", "y": "amt", "name": "total"}, {"agg": "mean", "y": "amt", "name": "avg"}, {"agg": "count", "name": "n"},
              {"agg": "min", "y": "amt", "name": "lo"}, {"agg": "max", "y": "amt", "name": "hi"}]
    graph = flow_of(["shops inner shop_id=shop_id"], {"by": ["country"], "series": series})
    found = flow_opt.propose(graph, getter(db), db, source_key="k", only=[3])
    third = rule(found, 3)
    assert third["status"] == "kept" and third["equal"] is True
    assert third["items"][0]["text"] == "조인 전에 왼쪽 테이블을 ‘shop_id’별로 먼저 집계해요(조인할 행이 ‘shop_id’ 종류 수만큼으로 줄어요)."
    pre = next(n for n in found["graph"]["nodes"] if n["id"].startswith("pre"))
    assert pre["params"]["by"] == ["shop_id"]
    now = by_country(flow_opt.result_of(found["graph"], found["target"], getter(db)))
    assert now["KR"] == {"country": "KR", "total": 480.0, "avg": 60.0, "n": 8, "lo": 10.0, "hi": 110.0}
    assert now["US"] == {"country": "US", "total": 300.0, "avg": 75.0, "n": 4, "lo": 30.0, "hi": 120.0}
    # 먼저 집계한 쪽이 조인할 행이 적다: 12행 → 3행
    left_rows = flow.to_expr(found["graph"], flow.join_sides(found["graph"], next(n["id"] for n in found["graph"]["nodes"] if n["kind"] == "join"))[0], getter(db)).count().execute()
    assert left_rows == 3


@pytest.mark.parametrize("agg,word", [("count_distinct", "고유 개수는"), ("median", "중앙값은"), ("p90", "P90는"), ("std", "표준편차(표본)는")])
def test_pre_aggregation_is_refused_for_non_decomposable_aggregates(db, agg, word):
    graph = flow_of(["shops inner shop_id=shop_id"], {"by": ["country"], "series": [{"agg": agg, "y": "amt", "name": "v"}]})
    found = flow_opt.propose(graph, getter(db), db, source_key="k", only=[3])
    third = rule(found, 3)
    assert third["status"] == "skipped" and third["items"] == []
    assert f"{word} 나눠서 집계했다가 합칠 수 없어서 먼저 집계하지 않았어요(합계·개수·최소·최대·평균만 돼요)." in third["refused"][0]["text"]
    assert not found["changed"]


def test_pre_aggregation_needs_a_unique_dimension_key(db):
    graph = flow_of(["dupshops inner shop_id=shop_id"], {"by": ["label"], "series": TOTAL})
    third = rule(flow_opt.propose(graph, getter(db), db, source_key="k", only=[3]), 3)
    assert third["status"] == "skipped" and "오른쪽(차원) 키가 고유하지 않아" in third["refused"][0]["text"]


def test_prune_columns_reads_only_what_the_flow_uses(db):
    graph = flow_of(["shops inner shop_id=shop_id"], {"by": ["country"], "series": TOTAL})
    found = flow_opt.propose(graph, getter(db), db, source_key="k", only=[4])
    tables = {n["params"]["table"]: n["params"].get("columns") for n in found["graph"]["nodes"] if n["kind"] == "table"}
    assert tables == {"sales": ["shop_id", "amt"], "shops": ["shop_id", "country"]}
    assert rule(found, 4)["status"] == "kept"
    assert by_country(flow_opt.result_of(found["graph"], found["target"], getter(db)))["KR"]["total"] == 480.0
    each = flow.from_body({"base": "sales", "calcs": ["반올림 0 숫자"], "aggregate": {"by": [], "series": TOTAL}})  # 타입으로 컬럼을 고르는 계산은 컬럼을 확신할 수 없다
    skipped = rule(flow_opt.propose(each, getter(db), db, source_key="k", only=[4]), 4)
    assert skipped["status"] == "skipped" and "타입·이름 패턴으로 컬럼을 고르는 계산" in skipped["refused"][0]["text"]


def test_all_rules_together_keep_the_result_and_report_bytes(db):
    graph = flow_of(["shops inner shop_id=shop_id"], {"by": ["country"], "series": [{"agg": "sum", "y": "amt", "name": "total"}]}, ["amt > 50", "country = KR"])
    found = flow_opt.propose(graph, getter(db), db, source_key="k")
    assert found["ok"] and found["verified"] and found["changed"]
    assert [(r["no"], r["status"]) for r in found["rules"]] == [(1, "kept"), (2, "skipped"), (3, "kept"), (4, "kept")]  # 조건을 내린 뒤에야 집계 바로 앞이 조인이 되어 먼저 집계도 된다
    original, now = flow_opt.result_of(graph, found["target"], getter(db)), flow_opt.result_of(found["graph"], found["target"], getter(db))
    assert flow_opt.same_result(original, now) == (True, "")
    assert by_country(now) == {"KR": {"country": "KR", "total": 360.0}}
    assert found["bytes"]["text"].startswith("읽는 양 약 ") and "추정" in found["bytes"]["text"]  # '약' 표시


def test_a_rule_whose_result_differs_is_dropped(db, monkeypatch):
    graph = flow_of(["shops left shop_id=shop_id"], {"by": ["country"], "series": TOTAL}, ["country = KR"])

    def wrong(g, ctx):  # 일부러 틀리게: 왼쪽 조인의 오른쪽 조건을 내린다
        cond = next(n for n in g["nodes"] if n["kind"] == "condition")
        right = next(n for n in g["nodes"] if n["kind"] == "table" and n["params"]["table"] == "shops")
        join = next(n for n in g["nodes"] if n["kind"] == "join")
        return flow_opt.splice_in(flow_opt.splice_out(g, cond["id"]), cond, right["id"], join["id"]), {"items": [{"node": cond["id"], "text": "틀리게 내렸어요."}], "refused": []}
    monkeypatch.setattr(flow_opt, "push_filters", wrong)
    found = flow_opt.propose(graph, getter(db), db, source_key="k", only=[1])
    first = rule(found, 1)
    assert first["status"] == "dropped" and first["equal"] is False and first["why"] == "결과가 달라서 뺐어요(행 수가 1행과 2행으로 달라요)."
    assert found["changed"] is False and found["graph"] == flow.check_shape(graph)  # 틀린 규칙은 흐름에 남지 않는다


def test_same_result_compares_keys_sorted_and_floats_relatively():
    import polars as pl
    a = pl.DataFrame({"k": ["x", "y"], "v": [1.0, 2e9]})
    assert flow_opt.same_result(a, pl.DataFrame({"k": ["y", "x"], "v": [2e9 * (1 + 1e-10), 1.0]})) == (True, "")
    same, why = flow_opt.same_result(a, pl.DataFrame({"k": ["x", "y"], "v": [1.0, 2e9 * (1 + 1e-6)]}))
    assert not same and why.startswith("‘v’의 값이")
    assert flow_opt.same_result(a, pl.DataFrame({"k": ["x", "y"], "w": [1.0, 2.0]})) == (False, "컬럼이 달라요")
    assert flow_opt.same_result(pl.DataFrame({"k": ["x"], "v": [None]}), pl.DataFrame({"k": ["x"], "v": [1.0]}))[1] == "‘v’에 빈 값이 한쪽에만 있어요"


def test_not_an_aggregate_result_gets_no_proposal(db):
    graph = flow.from_body({"base": "sales", "joins": ["shops inner shop_id=shop_id"]})
    found = flow_opt.propose(graph, getter(db), db, source_key="k")
    assert found == {"ok": False, "reason": "집계(지표·집계 단계)가 결과인 흐름에서만 최적화를 제안해요.", "rules": [], "warnings": [], "changed": False}


def test_fanout_warning_red_when_both_keys_are_not_unique(db):
    both = flow.from_body({"base": "lk", "joins": ["rk inner k=k"], "aggregate": {"by": [], "series": [{"agg": "count", "name": "n"}]}})
    warned = flow_opt.propose(both, getter(db), db, source_key="k", only=[])["warnings"]
    assert warned == [{"rule": 5, "node": "n2", "level": "bad", "text": "조인(n2): 양쪽 키가 모두 고유하지 않아 행이 곱해져요. 이대로 합계를 내면 값이 부풀어요."}]
    assert flow_opt.result_of(both, "n4", getter(db)).to_dicts() == [{"n": 4}]  # lk 3행이 이으면 4행(k=1이 2×2)
    right_only = flow.from_body({"base": "shops", "joins": ["dupshops inner shop_id=shop_id"], "aggregate": {"by": [], "series": [{"agg": "count", "name": "n"}]}})
    warned = flow_opt.propose(right_only, getter(db), db, source_key="k", only=[])["warnings"]
    assert [(w["level"], w["text"]) for w in warned] == [("warn", "조인(n2): 오른쪽 키가 겹쳐서 왼쪽 값이 오른쪽 행 수만큼 반복돼요. 왼쪽 값의 합계는 부풀 수 있어요.")]
    clean = flow_of(["shops inner shop_id=shop_id"], {"by": ["country"], "series": TOTAL})
    assert flow_opt.propose(clean, getter(db), db, source_key="k", only=[])["warnings"] == []


def test_apply_runs_only_the_chosen_rules_and_rechecks(db):
    graph = flow_of(["shops inner shop_id=shop_id"], {"by": ["country"], "series": TOTAL}, ["amt > 50"])
    done = flow_opt.apply(graph, [1], getter(db), db, source_key="k")
    assert done["applied"] == [1] and done["dropped"] == []
    assert any(n["id"].startswith("n") and flow.params(n).get("cond") == "amt > 50" for n in done["graph"]["nodes"])
    none = flow_opt.apply(flow_of(["shops left shop_id=shop_id"], {"by": ["country"], "series": TOTAL}, ["country = KR"]), [1], getter(db), db, source_key="k")
    assert none["applied"] == [] and none["dropped"][0]["no"] == 1 and "내리지 않았어요" in none["dropped"][0]["why"]


# ---------- 원격 DB: 값을 읽는 조회를 확인 없이 보내지 않는다 ----------

@pytest.fixture
def sent(monkeypatch):
    return spy_queries(monkeypatch)


def scanning(sqls):
    """보낸 조회 중 데이터를 읽는 것(카탈로그 통계·EXPLAIN·DESCRIBE는 뺀다)."""
    return [q for q in sqls if not q.lstrip().upper().startswith(("EXPLAIN", "DESCRIBE")) and "duckdb_tables" not in q and "SELECT CURRENT_" not in q.upper()]


def test_remote_proposes_only_structural_rules_and_sends_no_scans(db, sent, monkeypatch):
    monkeypatch.setattr(held, "remote", lambda con: True)
    graph = flow_of(["shops left shop_id=shop_id"], {"by": ["shop_id"], "series": TOTAL}, ["amt > 50"])
    found = flow_opt.propose(graph, getter(db), db, remote=True, source_key="k")
    assert found["ok"] and not found["verified"] and found["note"].startswith("원격 DB라")
    assert [(r["no"], r["status"]) for r in found["rules"]] == [(1, "unverified"), (2, "skipped"), (3, "skipped"), (4, "unverified")]
    assert "원격 DB라 고유 여부·결과 비교는 전체를 읽는 조회라 보내지 않았어요" in rule(found, 2)["refused"][0]["text"]
    assert [w["level"] for w in found["warnings"]] == ["info"]  # 부풂도 세지 않고 '확인 못 함'
    assert scanning(sent) == [], sent  # COUNT도, 결과 비교도, 어떤 데이터 조회도 보내지 않았다


def test_remote_runs_checks_only_after_the_user_confirms(db, sent, monkeypatch):
    monkeypatch.setattr(held, "remote", lambda con: True)
    graph = flow_of(["shops left shop_id=shop_id"], {"by": ["shop_id"], "series": TOTAL})
    found = flow_opt.propose(graph, getter(db), db, remote=True, scan_ok=True, source_key="k")
    assert found["verified"] and rule(found, 2)["status"] == "kept"
    assert scanning(sent)  # 확인했으니 고유 확인·결과 비교 조회가 나간다
