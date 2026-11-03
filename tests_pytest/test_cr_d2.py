"""D2 묶음(문장 틀·조건 언어·집계) 고침: 빈 값에 안전한 != / except, 조건 문법(not like·matches·=~·not between), 상대 날짜 기간, 피벗 여러 값·비율 보기·빈 값 열,
퍼널 세그먼트·단계 사이 시간, 문장 틀 13·19·20·22·24·34·36·53과 지표 틀 13·14, 새 틀 54~56, 지표 사전 결과 표(JS). 데이터는 시험 안에서 만든다.
ev: id 0~11, store = A(0~4)·B(5~8)·C(9~11), amt = (id+1)*10, uid = 'u' + id % 4, day = 2026-01-01 + id. ev3: 집단(flag x) 대상 a·b·c는 5·6·7행, 나머지 d·e·f는 1·2·1행."""
import datetime as dt
import json
import shutil
import subprocess

import duckdb
import polars as pl
import pytest
from lazy import ibis

from core import calc
from core import funnel
from core import metric_file
from core import parse
from core import server
from query import sentence_exec as ex
from query import sentence_run as sr
from conftest import ROOT

COLS = {"id": "num", "uid": "text", "store": "text", "amt": "num", "day": "date", "url": "text"}
BASE = {"base": "ev", "joins": [], "periods": [], "conditions": [], "calcs": []}


@pytest.fixture
def c(folder, tmp_path):
    from dashboard import dash_app  # noqa: F401
    from query import flow_app  # noqa: F401
    from prep import prep_app  # noqa: F401
    from query.search_app import app
    path = tmp_path / "x.duckdb"
    raw = duckdb.connect(str(path))
    raw.execute("""create table ev as select i::int as id, 'u' || (i % 4) as uid, case when i < 5 then 'A' when i < 9 then 'B' else 'C' end as store,
                   ((i + 1) * 10)::double as amt, date '2026-01-01' + i::int as day, 'p' || i as url from range(12) t(i)""")
    raw.execute("""create table ev3 as select * from (values ('a', 'x'), ('a', 'x'), ('a', 'x'), ('a', 'x'), ('a', 'x'), ('b', 'x'), ('b', 'x'), ('b', 'x'), ('b', 'x'), ('b', 'x'), ('b', 'x'),
                   ('c', 'x'), ('c', 'x'), ('c', 'x'), ('c', 'x'), ('c', 'x'), ('c', 'x'), ('c', 'x'), ('d', 'y'), ('e', 'y'), ('e', 'y'), ('f', 'y')) t(who, flag)""")
    raw.close()
    (tmp_path / "m.json").write_text(json.dumps({"metrics": {"노출": {"numerator": {"agg": "count"}}}}), encoding="utf-8")
    metric_file.save_file_path(str(tmp_path / "m.json"))
    client = app.test_client()
    client.get("/?key=" + server.KEY)
    assert client.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(path)}).status_code == 200
    yield client
    client.post("/api/disconnect", json={})


def go(tid, slots, body=BASE, cols=COLS):
    return ex.execute_compiled(ex.prepare(tid, slots, body, cols))


def pairs(done, key, value):
    return [(r[key], r[value]) for r in done["rows"]]


def cut(body=BASE, *conds):
    return {**body, "conditions": list(conds)}


def count(t, line):
    return t.filter(parse.predicate(line, t)).count().execute()


# ---------- 조건 언어 ----------

ST = ibis.memtable(pl.DataFrame({"s": ["취소", "완료", None, "Done"], "n": [1.0, 2.0, 3.0, None], "d": [dt.date(2026, 1, 1), None, dt.date(2026, 1, 2), dt.date(2026, 1, 3)]}).to_arrow())


def test_not_equal_and_except_keep_empty_values():
    assert count(ST, "s != 취소") == 3 and count(ST, "s except 취소") == 3  # 빈 값 행도 '취소가 아님'
    assert count(ST, "n != 1") == 3 and count(ST, "d != 2026-01-01") == 3
    assert count(ST, "s = 취소") == 1 and count(ST, "s in 취소, 완료") == 2  # 같음·in은 그대로


def test_condition_formula_keeps_empty_values_too():
    for line in ("s != 취소", "s except 취소"):
        made = calc.apply([f"식 {sr.formula_of(line, {'s': 'text', 'n': 'num'})} 이름 ok"], ST)
        assert made.filter(made.ok).count().execute() == 3, line


def test_new_condition_words():
    assert count(ST, "s not like 취") == 3  # 빈 값도 '포함하지 않음'
    assert count(ST, "s matches ^D") == 1
    assert count(ST, "s =~ done") == 1 and count(ST, "s =~ '완료'") == 1
    assert count(ST, "n not between 2 3") == 2  # 1과 빈 값
    for word in ("[not] like", "matches", "=~", "[not] between"):
        assert word in parse.CONDITION_GUIDE
    made = calc.apply([f"식 {sr.formula_of('s not like 취', {'s': 'text'})} 이름 ok"], ST)
    assert made.filter(made.ok).count().execute() == 3


def test_period_line_takes_relative_dates():
    today = dt.date.today()
    t = ibis.memtable(pl.DataFrame({"d": [today - dt.timedelta(days=k) for k in range(40)]}).to_arrow())
    assert t.filter(parse.parse_period("d 최근 7일", t)).count().execute() == 7
    first = today.replace(day=1)
    last_month = (first - dt.timedelta(days=1)).replace(day=1)
    want = sum(1 for k in range(40) if last_month <= today - dt.timedelta(days=k) < first)
    assert t.filter(parse.parse_period("d 지난달", t)).count().execute() == want
    with pytest.raises(ValueError):
        parse.parse_period("d 아무글", t)


# ---------- 피벗·상위 N ----------

PV = ibis.memtable(pl.DataFrame({"g": ["a", "a", "b", "b", "b"], "k": ["x", None, "x", "y", "y"], "v": [1.0, 2.0, 3.0, 4.0, 5.0]}).to_arrow())
vals = lambda e: e.to_pyarrow()["v"].to_pylist()


def test_pivot_many_values_shares_and_empty_column():
    one = calc.pivot(PV, {"rows": ["g"], "column": "k", "how": "count"}, vals).execute()
    assert list(one.columns) == ["g", "전체 개수", "x", "y", "(빈 값)"] and one["(빈 값)"].tolist() == [1, 0]
    many = calc.pivot(PV, {"rows": ["g"], "values": [{"value": "v", "how": "sum"}, {"how": "count"}]}, vals).execute()
    assert many["v 합계"].tolist() == [3.0, 12.0] and many["개수"].tolist() == [2, 3]
    row = calc.pivot(PV, {"rows": ["g"], "column": "k", "value": "v", "how": "sum", "show": "row_pct"}, vals).execute()
    assert row["x"].tolist() == pytest.approx([1 / 3, 3 / 12]) and row["전체 합계"].tolist() == [1.0, 1.0]
    col = calc.pivot(PV, {"rows": ["g"], "column": "k", "value": "v", "how": "sum", "show": "col_pct"}, vals).execute()
    assert col["x"].tolist() == pytest.approx([0.25, 0.75]) and col["전체 합계"].tolist() == pytest.approx([0.2, 0.8])


def test_top_n_ties_break_by_name():
    t = ibis.memtable(pl.DataFrame({"g": ["c", "b", "a", "d"], "v": [1, 1, 1, 0]}).to_arrow())
    got = calc.pivot(t, {"rows": ["g"], "value": "v", "how": "sum", "order": "전체 합계 desc", "top": 2}, vals).execute()
    assert got["g"].tolist() == ["a", "b"]


# ---------- 퍼널 ----------

def test_funnel_segments_and_median_time():
    t0 = dt.datetime(2026, 1, 1)
    rows = [(1, "a", 0, "web"), (1, "b", 60, "web"), (2, "a", 0, "web"), (2, "b", 180, "web"), (3, "a", 0, "app"), (4, "a", 0, "app"), (4, "b", 600, "app")]
    t = ibis.memtable(pl.DataFrame({"u": [r[0] for r in rows], "e": [r[1] for r in rows], "t": [t0 + dt.timedelta(seconds=r[2]) for r in rows], "ch": [r[3] for r in rows]}).to_arrow())
    plain = funnel.compute(t, "u", "e", "t", ["a", "b"])
    assert [s["users"] for s in plain["steps"]] == [4, 3] and plain["steps"][1]["median_sec"] == 180 and plain["steps"][0]["median_sec"] is None
    seg = funnel.compute(t, "u", "e", "t", ["a", "b"], by="ch")
    assert {s["name"]: [x["users"] for x in s["steps"]] for s in seg["segments"]} == {"web": [2, 2], "app": [2, 1]}


# ---------- 문장 틀 ----------

def test_e24_counts_the_chosen_column_and_last_touch(c):
    first = go("e24", {"대상": "uid", "컬럼": "store", "정렬 기준": "day desc", "집계": "count_distinct"})
    assert pairs(first, "처음_store", "전체 고유 개수") == [("B", 1), ("C", 3)]  # 고유 개수는 대상 수(예전엔 store 고유 수 1)
    last = go("e24", {"대상": "uid", "컬럼": "store", "정렬 기준": "day desc", "집계": "sum", "값": "amt", "접점": "마지막"})
    assert pairs(last, "마지막_store", "전체 합계") == [("A", 780.0)]  # 사용자마다 가장 이른 가게는 모두 A


def test_e36_moving_mean_by_calendar_unit_fills_empty_days(c):
    done = go("e36", {"정렬 기준": "day", "값": "amt", "종류": "이동 평균", "N": 2, "단위": "일"}, cut(BASE, "id != 5"))
    got = {r["기간"][:10]: r["이동 평균"] for r in done["rows"]}
    assert len(got) == 12 and got["2026-01-06"] == 25.0 and got["2026-01-07"] == 35.0  # 1-06은 0으로 채운 날: (50 + 0) ÷ 2, (0 + 70) ÷ 2


def test_e22_fills_empty_periods(c):
    done = go("e22", {"날짜": "day", "단위": "week", "대상": "uid"}, cut(BASE, "id < 4 or id > 10"))
    assert [(k[:10], v) for k, v in pairs(done, "기간", "전체 고유 개수")] == [("2025-12-29", 4), ("2026-01-05", 0), ("2026-01-12", 1)]


def test_m13_mean_fills_empty_and_drops_open_period(c):
    assert go("m13", {"단위": "week", "지표": "노출", "기간": {"column": "day"}})["rows"][0]["평균"] == 5.5  # 4·7건(1-12 주는 진행 중이라 뺀다)
    assert go("m13", {"단위": "week", "지표": "노출", "기간": {"column": "day"}}, cut(BASE, "id < 4 or id > 10"))["rows"][0]["평균"] == 2.0  # 4·0건


def test_m14_takes_relative_period(c):
    made = sr.compile("m14", {"지표": "노출", "기간": "day 지난달"}, BASE, COLS)
    assert made["ok"] and made["runs"][1]["body"]["periods"][-1].startswith("day "), made


def test_m14_change_uses_size_of_negative_base():
    a, b = {"keys": [], "col": "x", "rows": [{"x": -50}], "more": False}, {"keys": [], "col": "x", "rows": [{"x": -100}], "more": False}
    assert ex.combine({"op": "change", "a": 0, "b": 1}, [a, b])["rows"][0]["변화율"] == 0.5


def test_e19_e20_show_denominator_sort_and_minimum(c):
    e19 = go("e19", {"무엇": "store", "조건": "amt >= 40", "대상": "uid", "최소 N": 4})
    assert [(r["store"], r["대상 수"], r["값"]) for r in e19["rows"]] == [("B", 4, 1.0), ("A", 4, 0.5)]  # C는 3명이라 뺀다
    e20 = go("e20", {"무엇": "store", "대상": "uid", "값": "amt"})
    assert [(r["store"], r["대상 수"]) for r in e20["rows"]] == [("C", 3), ("B", 4), ("A", 4)]


def test_e13_adds_rest_row_and_breaks_ties_by_name(c):
    assert pairs(go("e13", {"무엇": "store", "값": "amt", "집계": "sum", "N": 2}), "store", "전체 합계") == [("C", 330.0), ("B", 300.0), ("기타", 150.0)]
    assert pairs(go("e13", {"무엇": "uid", "값": "id", "집계": "count", "N": 2}), "uid", "전체 개수") == [("u0", 3), ("u1", 3), ("기타", 6)]


def test_e34_keeps_n_rows_on_ties(c):
    body = {**BASE, "calcs": ["식 floor(col('amt') / 50) 이름 band"]}
    done = go("e34", {"무엇": "store", "값": "band", "N": 1}, body, {**COLS, "band": "num"})
    assert sorted(r["id"] for r in done["rows"]) == [4, 5, 9]


def test_new_templates_54_55_56(c):
    assert pairs(go("e54", {"값": "amt", "구간 폭": 50}), "구간", "전체 개수") == [(0.0, 4), (50.0, 5), (100.0, 3)]
    abc = go("e55", {"무엇": "store", "값": "amt"})
    assert [(r["store"], round(r["누적 비중"], 3), r["등급"]) for r in abc["rows"]] == [("C", 0.423, "A"), ("B", 0.808, "A"), ("A", 1.0, "B")]
    out = go("e56", {"날짜": "day", "단위": "일", "값": "amt", "방법": "z-점수", "k": 1.5})
    assert [r["기간"][:10] for r in out["rows"]] == ["2026-01-01", "2026-01-12"]
    assert go("e56", {"날짜": "day", "단위": "일", "값": "amt", "방법": "IQR"})["rows"] == []


def test_e53_has_welch_p_value(c):
    from metrics import metric_calc
    body = {**BASE, "base": "ev3"}
    done = go("e53", {"조건": "flag = x", "대상": "who"}, body, {"who": "text", "flag": "text"})
    row = next(r for r in done["rows"] if r["지표"] == "대상당 평균")
    want = metric_calc.welch_from_stats(4 / 3, (1 / 3) ** 0.5, 3, 6.0, 1.0, 3)["p"]
    assert row[done["columns"][-1]] == pytest.approx(want)


# ---------- 지표 사전 결과 표(JS) ----------

@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_catalog_table_shows_all_rows_latest_first_and_rates_as_percent(tmp_path):
    script = tmp_path / "t.js"
    src = "".join(next((ROOT / "static").rglob(f)).read_text(encoding="utf-8") for f in ("sentences.js", "metric_catalog.js"))
    cols = [{"key": "period", "label": "기간"}, {"key": "retention", "label": "유지율"}, {"key": "users", "label": "사용자"}, {"key": "x", "label": "전환율"}]
    rows = [{"period": f"p{i}", "retention": 0.5, "users": 1234, "x": 3.5} for i in range(30)]
    script.write_text("globalThis.SERVER = {};\n" + src + "\nconst cols = %s, rows = %s;\nconst pct = mcPctCols(cols, rows);\n"
                      "console.log(JSON.stringify({ order: mcRows(rows).map(r => r.period), cells: cols.map(c => mcCell(c, rows[0][c.key], pct)) }));" % (json.dumps(cols), json.dumps(rows)), encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    got = json.loads(done.stdout.strip().splitlines()[-1])
    assert len(got["order"]) == 30 and got["order"][0] == "p29"
    assert got["cells"] == ["p0", "50%", "1,234", "3.5"]  # 전환율이지만 값이 1을 넘으면 %로 바꾸지 않는다
