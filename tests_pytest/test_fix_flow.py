"""리뷰 반영 묶음 5(문장·공통 키·연동, 흐름 보기 테스트는 흐름 보기와 함께 지웠다): 지적마다 그 지적을 재현하는 테스트. 값은 테스트 안에서 지어낸 작은 표를 손으로 센 것과 맞춘다.
sales: id 0~5, shop_id = id % 3, amt = (id+1)*10 → 가게 0: 10+40=50, 가게 1: 20+50=70, 가게 2: 30+60=90. shops: 가게 0~2(이름 A~C), 모두 country KR."""
import pytest
from lazy import ibis

from integrations import bot_app  # noqa: F401  라우트 모듈은 첫 요청 전에 모두 불러 둔다
from collect import collect_app  # noqa: F401
from dashboard import dash_app  # noqa: F401
from query import flow_app  # noqa: F401
from integrations import integrations_app  # noqa: F401
from integrations import notify  # noqa: F401
from prep import prep_app  # noqa: F401
from integrations import schedule_app  # noqa: F401


def comp_top(*pairs):
    return [{"a": a, "b": b, "name": 1.0, "type": 0.8, "cast": False} for a, b in pairs]


def test_m12_composite_casts_right_to_left_types_int_float():
    import polars as pl
    from query import join_keys
    b = ibis.memtable(pl.DataFrame({"x": [1.0, 2.0, 3.0], "y": [10.0, 20.0, 30.0]}))
    a = ibis.memtable(pl.DataFrame({"x": [1, 2, 9], "y": [10, 20, 90]}))
    fa = pl.DataFrame({"x": [1, 2, 9], "y": [10, 20, 90]})
    found = join_keys.composites(a, b, comp_top(("x", "x"), ("y", "y")), fa, "a", "b")
    assert len(found) == 1 and found[0]["contain"] == pytest.approx(2 / 3, abs=1e-4)  # (1,10)·(2,20)이 B에 있고 (9,90)은 없다
    halves = ibis.memtable(pl.DataFrame({"x": [1.5, 2.5], "y": [10.0, 20.0]}))  # 소수는 정수로 잘려 맞으면 안 된다
    assert not join_keys.composites(a, halves, comp_top(("x", "x"), ("y", "y")), fa, "a", "b") or join_keys.composites(a, halves, comp_top(("x", "x"), ("y", "y")), fa, "a", "b")[0]["contain"] == 0


def test_m12_composite_casts_timestamp_to_date():
    import datetime as dt

    import polars as pl

    from query import join_keys
    b = ibis.memtable(pl.DataFrame({"d": [dt.datetime(2026, 1, 1, 0, 0), dt.datetime(2026, 1, 2, 0, 0)], "n": [1, 2]}))
    a = ibis.memtable(pl.DataFrame({"d": [dt.date(2026, 1, 1), dt.date(2026, 1, 5)], "n": [1, 5]}))
    fa = pl.DataFrame({"d": [dt.date(2026, 1, 1), dt.date(2026, 1, 5)], "n": [1, 5]})
    found = join_keys.composites(a, b, comp_top(("d", "d"), ("n", "n")), fa, "a", "b")
    assert len(found) == 1 and found[0]["contain"] == pytest.approx(0.5)


# ---------- 연동: /api/destinations, GA4 ----------

def test_destinations_route_does_not_wait_for_the_turn():
    from core import server
    assert "/api/destinations" in server.FREE and "/api/destinations/check" in server.FREE


def test_ga4_fetch_has_no_unused_halt_and_collects_pages_as_frames(monkeypatch):
    import inspect

    import polars as pl

    from integrations.adapters import ga4
    assert "halt" not in inspect.signature(ga4.fetch).parameters
    pages = [{"rowCount": 3, "metricHeaders": [{"name": "n", "type": "TYPE_INTEGER"}],
              "rows": [{"dimensionValues": [{"value": d}], "metricValues": [{"value": v}]} for d, v in rows]} for rows in ([("a", "1"), ("b", "2")], [("c", "3")])]
    monkeypatch.setattr(ga4, "read_key", lambda p: {})
    monkeypatch.setattr(ga4, "access_token", lambda k: "t")
    monkeypatch.setattr(ga4, "PAGE_ROWS", 2)
    monkeypatch.setattr(ga4, "run_report", lambda token, spec, offset, limit: pages[offset // 2])
    joined = []
    real = pl.concat
    monkeypatch.setattr(pl, "concat", lambda items, *a, **k: (joined.append(len(items)), real(items, *a, **k))[1])
    frame, total = ga4.fetch("k", {"property": "1", "start": "today", "end": "today", "dimensions": "d", "metrics": "n"})
    assert total == 3 and frame["d"].to_list() == ["a", "b", "c"] and frame["n"].to_list() == [1, 2, 3] and joined == [2]


# ---------- 문장 실행: 1000행 알림·빈 결과 컬럼·처리 용량 확인·내 지표 scope ----------

from test_sentence_exec import BASE, COLS, c  # noqa: E402,F401  (ev 표 12행을 만들어 연결하는 fixture)
from test_slack_adapters import shop  # noqa: E402,F401  (orders 3행 + 저장한 검색·지표 파일)


def test_sentence_rows_more_uses_one_extra_row(c, monkeypatch):
    from query import sentence_exec as ex
    monkeypatch.setattr(ex, "QUERY_ROWS", 5)
    done = ex.execute_compiled(ex.prepare("e01", {}, BASE, COLS))
    assert len(done["rows"]) == 5 and done["more"] is True  # 12행 중 앞 5행만
    monkeypatch.setattr(ex, "QUERY_ROWS", 12)
    done = ex.execute_compiled(ex.prepare("e01", {}, BASE, COLS))
    assert len(done["rows"]) == 12 and done["more"] is False  # 딱 12행이면 더 없다


def test_sentence_empty_rows_keep_their_columns(c):
    from query import sentence_exec as ex
    done = ex.execute_compiled(ex.prepare("e03", {"컬럼": "store", "값": "Z", "컬럼들": ["id", "amt"]}, BASE, COLS))
    assert done["rows"] == [] and done["columns"] == ["id", "amt"]


def test_sentence_run_route_asks_before_a_big_scan(c, monkeypatch):
    from core import server
    asked = []

    def gate(body, make_sql, rows=None):
        asked.append(make_sql()[0])
        return None if body.get("confirmed") else server.jsonify(needs_confirm=True, text="큼")

    monkeypatch.setattr(server, "scan_gate", gate)
    data = {"template": "e20", "slots": {"무엇": "store", "대상": "uid", "값": "amt"}, "body": BASE}
    first = c.post("/api/sentence/run", json=data)
    assert first.get_json()["needs_confirm"] is True and "rows" not in first.get_json() and len(asked) == 1 and "FROM" in asked[0].upper()  # 요청이 둘이어도 같은 body는 한 번만 묻는다
    again = c.post("/api/sentence/run", json={**data, "confirmed": True})
    assert [r["store"] for r in again.get_json()["rows"]] == ["A", "B", "C"]


def test_my_metric_of_another_table_is_refused_at_run_time(c):
    from metrics import my_metrics as mm
    from query import sentence_exec as ex
    from core import server
    from core.dash_store import current_source
    key = current_source()["key"]
    here = mm.create({"name": "건수", "scope": {"key": key, "table": "ev"}, "template": "m01", "slots": {}}, COLS)
    there = mm.create({"name": "건수", "scope": {"key": key, "table": "ev2"}, "template": "m01", "slots": {}}, COLS)
    assert ex.execute_compiled(ex.prepare("m08", {"지표 A": here["id"], "지표 B": here["id"]}, BASE, COLS))["rows"][0]["합"] == 24
    with pytest.raises(ValueError, match="다른 테이블"):
        ex.execute_compiled(ex.prepare("m08", {"지표 A": here["id"], "지표 B": there["id"]}, BASE, COLS))
    other = mm.create({"name": "남의 연결", "scope": {"key": "k-other", "table": "ev"}, "template": "m01", "slots": {}}, COLS)
    with pytest.raises(ValueError, match="다른 테이블"):
        ex.execute_compiled(ex.prepare("m08", {"지표 A": here["id"], "지표 B": other["id"]}, BASE, COLS))
    assert server.state["con"] is not None


def test_slack_commands_do_not_run_big_or_unknown_scans(shop, monkeypatch):
    from integrations import commands
    from core import scan_size
    for answer in ({"over": True, "unknown": False, "text": "크다"}, {"over": False, "unknown": True, "text": "모른다"}):
        monkeypatch.setattr(scan_size, "limit_check", lambda *a, _r=answer, **k: _r)
        for text in ('검색 "전체 주문"', '지표 취소율 검색="전체 주문"'):
            out = commands.answer(text)
            assert out.startswith("할 수 없습니다") and "프로그램에서 실행하세요" in out, (answer, text, out)


# ---------- 문장 틀: e23 대상 수 컬럼, e33 정렬 방향, e41 컬럼 칸 ----------

def test_e23_shows_the_target_count_next_to_the_average(c):
    from query import sentence_exec as ex
    done = ex.execute_compiled(ex.prepare("e23", {"날짜": "day", "단위": "week", "대상": "uid", "값": "amt"}, BASE, COLS))
    # 월요일 시작 주: 12-29주 id 0~3(합 100, 4명), 01-05주 id 4~10(합 560, 4명), 01-12주 id 11(합 120, 1명)
    assert done["columns"] == ["기간", "전체 합계", "전체 고유 개수", "값"]
    assert sorted((r["기간"], r["전체 합계"], r["전체 고유 개수"], r["값"]) for r in done["rows"]) == [("2025-12-29", 100.0, 4, 25.0), ("2026-01-05", 560.0, 4, 140.0), ("2026-01-12", 120.0, 1, 120.0)]


def test_e33_direction_comes_from_the_choice_even_if_the_order_has_one():
    from query import sentence_run as sr
    done = sr.compile("e33", {"무엇": "store", "정렬 기준": "day desc", "방향": "가장 이른"}, BASE, COLS)
    assert done["body"]["calcs"] == ["창거르기 row_number <= 1 기준 day asc 그룹 store"]


def test_e41_has_no_unused_column_slot():
    from query import sentence_run as sr
    from query import sentences
    t = sentences.get("e41")
    assert "컬럼" not in [s["name"] for s in t["slots"]] and "{컬럼}" not in t["text"]
    done = sr.compile("e41", {"조건1": "amt >= 100", "값1": "high", "값3": "low", "새 이름": "level"}, BASE, COLS)
    assert done["ok"] and done["body"]["calcs"] == ["식 case(col('amt') >= 100, 'high', 'low') 이름 level"]


# ---------- 내 지표: 지문의 scope, 옮기기 거부, 빈칸 모양, 정렬 글, 쓰기 잠금 ----------

from metrics import my_metrics as mm  # noqa: E402

MM_COLS = {"id": "num", "store": "text", "amt": "num", "day": "date"}


def scoped(table):
    return {"key": "k", "label": "k", "table": table}


def test_def_hash_includes_the_scope(folder):
    a = mm.create({"name": "건수", "scope": scoped("ev"), "template": "m01", "slots": {}}, MM_COLS)
    b = mm.create({"name": "건수", "scope": scoped("ev2"), "template": "m01", "slots": {}}, MM_COLS)
    assert a["def_hash"] != b["def_hash"]


def test_moving_a_metric_to_another_table_is_refused_while_others_use_it(folder):
    a = mm.create({"name": "건수", "scope": scoped("ev"), "template": "m01", "slots": {}}, MM_COLS)
    mm.create({"name": "두 배", "scope": scoped("ev"), "template": "m08", "slots": {"지표 A": a["id"], "지표 B": a["id"]}}, MM_COLS)
    with pytest.raises(ValueError, match="옮길 수 없어요"):
        mm.update(a["id"], {"scope": scoped("ev2")}, MM_COLS)
    assert mm.get(a["id"])["scope"]["table"] == "ev"
    free = mm.create({"name": "혼자", "scope": scoped("ev"), "template": "m01", "slots": {}}, MM_COLS)
    assert mm.update(free["id"], {"scope": scoped("ev2")}, MM_COLS)["metric"]["scope"]["table"] == "ev2"


def test_check_slot_accepts_agg_with_opts_and_column_with_prep(folder):
    made = mm.create({"name": "p90", "scope": scoped("ev"), "template": "m03",
                      "slots": {"값": {"column": "amt", "prep": "lower"}, "집계": {"agg": "quantile", "opts": {"p": 0.9}}}}, MM_COLS)
    assert made["slots"]["집계"] == {"agg": "quantile", "opts": {"p": 0.9}} and made["slots"]["값"] == {"column": "amt", "prep": "lower"} and made["cols"] == {"amt": "num"}
    with pytest.raises(ValueError, match="컬럼 가공은"):
        mm.create({"name": "x", "scope": scoped("ev"), "template": "m03", "slots": {"값": {"column": "amt", "prep": "엉뚱"}, "집계": "sum"}}, MM_COLS)
    with pytest.raises(ValueError, match="amt2"):
        mm.create({"name": "y", "scope": scoped("ev"), "template": "m03", "slots": {"값": {"column": "amt2"}, "집계": "sum"}}, MM_COLS)
    copied = mm.copy_to(made["id"], scoped("other"), {"amt": "money"}, {"money": "num"})["metrics"][0]
    assert copied["slots"]["값"] == {"column": "money", "prep": "lower"}


def test_order_slot_without_a_column_is_a_value_error_not_an_attribute_error(folder):
    slot = next(s for s in sentences_slots("m15") if s["name"] == "정렬 기준")
    with pytest.raises(ValueError, match="정렬 기준"):
        mm.check_slot(slot, ", desc", MM_COLS, [])
    assert mm.used_columns({"slots": [slot]}, {"정렬 기준": ", desc"}) == set()


def sentences_slots(tid):
    from query import sentences
    return sentences.get(tid)["slots"]


def test_writes_to_my_metrics_do_not_overwrite_each_other(folder, monkeypatch):
    import threading
    import time
    real = mm.refresh_hashes
    monkeypatch.setattr(mm, "refresh_hashes", lambda items: (time.sleep(0.05), real(items))[1])  # 읽고 쓰는 사이를 벌려 겹치게 한다
    threads = [threading.Thread(target=mm.create, args=({"name": f"건수{i}", "scope": scoped("ev"), "template": "m01", "slots": {}}, MM_COLS)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(m["name"] for m in mm.load()) == ["건수0", "건수1", "건수2", "건수3"]
