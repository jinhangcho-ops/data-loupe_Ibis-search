"""계산(탐색 6번)·조인 종류(semi·anti·asof)·이어붙이기(intersect·except)·피벗. 값은 파이썬으로 따로 계산해 맞춘다.
지어낸 DuckDB 파일(창 함수를 DB에서), 같은 데이터의 Parquet 파일(Polars 연결: 창 함수를 PC에서), SQLite 파일(asof 대신 식)에 실제로 연결한다."""
import datetime as dt
import os
import sqlite3

import duckdb
import pytest

N = 20
ORDERS = [{"id": i, "store": i % 3, "day": dt.date(2026, 1, 1) + dt.timedelta(days=i % 10), "amt": float(i * 37 % 100), "memo": f" m{i} "} for i in range(N)]
PRICES = [{"store": i % 3, "day": dt.date(2025, 12, 30) + dt.timedelta(days=i), "px": float(i)} for i in range(12)]


def make_duckdb(path):
    raw = duckdb.connect(str(path))
    raw.execute("create table orders (id bigint, store bigint, day date, amt double, memo varchar, tags varchar[])")
    raw.executemany("insert into orders values (?, ?, ?, ?, ?, ?)", [(o["id"], o["store"], o["day"], o["amt"] if o["id"] != 4 else None, o["memo"], ["a", "b"][: o["id"] % 3]) for o in ORDERS])
    raw.execute("create table prices (store bigint, day date, px double)")
    raw.executemany("insert into prices values (?, ?, ?)", [(p["store"], p["day"], p["px"]) for p in PRICES])
    raw.execute("create table early as select * exclude (tags) from orders where id < 5")
    raw.execute("create table plain as select * exclude (tags) from orders")
    raw.close()


def amt(o):
    return None if o["id"] == 4 else o["amt"]


def client(connect):
    import server
    from app import app
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json=connect).status_code == 200
    return c


@pytest.fixture
def db(folder, tmp_path):
    make_duckdb(tmp_path / "s.duckdb")
    c = client({"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "s.duckdb")})
    yield lambda api="/api/query", **body: c.post(api, json={"base": "orders", "limit": 100, "sorts": ["id asc"], **body}).get_json()
    c.post("/api/disconnect", json={})


def by_id(found, col):
    return {r["id"]: r[col] for r in found["rows"]["rows"]}


def test_window_calcs(db):
    found = db(calcs=["순위 기준 amt desc 그룹 store", "누적합 amt 기준 id", "이전값 amt 기준 id", "차이 amt 기준 id", "이동평균 amt 3행 기준 id 그룹 store"])
    assert not found.get("error"), found
    rank, cum, prev, diff, move = (by_id(found, c) for c in ("amt_순위", "amt_누적합", "amt_이전값", "amt_차이", "amt_이동평균3행"))
    for o in ORDERS:
        mine = [x for x in ORDERS if x["store"] == o["store"]]
        if amt(o) is not None:  # 빈 값의 순위는 DB마다 앞뒤가 달라 보지 않는다
            assert rank[o["id"]] == 1 + sum(1 for x in mine if amt(x) is not None and amt(x) > amt(o))
        assert cum[o["id"]] == sum(amt(x) or 0 for x in ORDERS if x["id"] <= o["id"])
        before = amt(ORDERS[o["id"] - 1]) if o["id"] else None
        assert prev[o["id"]] == before and diff[o["id"]] == (None if before is None or amt(o) is None else amt(o) - before)
        window = [amt(x) for x in mine if x["id"] <= o["id"]][-3:]
        values = [v for v in window if v is not None]
        assert move[o["id"]] == pytest.approx(sum(values) / len(values))


def test_range_days_and_top(db):
    found = db(calcs=["이동합계 amt 3일 기준 day"])
    got = by_id(found, "amt_이동합계3일")
    for o in ORDERS:
        assert got[o["id"]] == sum(amt(x) or 0 for x in ORDERS if o["day"] - dt.timedelta(days=2) <= x["day"] <= o["day"])
    top = db(calcs=["상위 2 기준 id desc 그룹 store"])
    want = sorted(i for s in range(3) for i in sorted((o["id"] for o in ORDERS if o["store"] == s), reverse=True)[:2])
    assert sorted(r["id"] for r in top["rows"]["rows"]) == want and top["count"] == 6


def test_each_unnest_and_longer(db):
    found = db(calcs=["곱하기 2 amt", "빈값채우기 0 amt", "반올림 -1 amt", "공백지우기 글자"])
    row = {r["id"]: r for r in found["rows"]["rows"]}
    assert row[4]["amt"] == 0 and row[1]["amt"] == round(37 * 2, -1) and row[1]["memo"] == "m1"
    unnested = db(calcs=["펼치기 tags"])
    assert unnested["count"] == sum(o["id"] % 3 for o in ORDERS) and {r["tags"] for r in unnested["rows"]["rows"]} == {"a", "b"}
    longer = db(calcs=["세로로 amt, store"], sorts=[])
    assert longer["count"] == 2 * N and {r["항목"] for r in longer["rows"]["rows"]} == {"amt", "store"}


def test_calc_errors(db):
    assert "중 하나로 시작" in db(calcs=["합치기 amt"])["error"]
    assert "순서가 필요" in db(calcs=["누적합 amt"])["error"]
    assert "이미 있습니다" in db(calcs=["누적합 amt 기준 id 이름 amt"])["error"]
    assert "'3행'이나 '7일'" in db(calcs=["이동평균 amt 기준 id"])["error"]
    assert "날짜" in db(calcs=["이동평균 amt 3일 기준 id"])["error"]
    assert "목록(배열)" in db(calcs=["펼치기 amt"])["error"]
    assert db(calcs=["이동평균 amt 3행 기준 id 이름 평균3"])["rows"]["columns"][-1] == "평균3"


def test_join_kinds_and_set_unions(db):
    have = {p["store"] for p in PRICES}
    assert db(joins=["prices semi store=store"])["count"] == sum(o["store"] in have for o in ORDERS)
    assert db(joins=["prices anti store=store"])["count"] == 0
    asof = db(joins=["prices asof day>=day, store=store"])
    for r in asof["rows"]["rows"]:
        o = ORDERS[r["id"]]
        near = [p for p in PRICES if p["store"] == o["store"] and p["day"] <= o["day"]]
        assert r["px"] == (max(near, key=lambda p: p["day"])["px"] if near else None)
    assert "시각 쌍" in db(joins=["prices asof store=store"])["error"]
    assert db(base="plain", union={"tables": ["early"], "how": "intersect"})["count"] == 5
    assert db(base="plain", union={"tables": ["early"], "how": "except"})["count"] == N - 5
    assert "출처" not in db(base="plain", union={"tables": ["early"], "how": "except"})["rows"]["columns"]


def test_pivot(db):
    found = db("/api/pivot", pivot={"rows": ["store"], "column": "day", "value": "amt", "how": "sum", "where": "amt >= 10"})
    rows = {r["store"]: r for r in found["rows"]["rows"]}
    for s in range(3):
        mine = [o for o in ORDERS if o["store"] == s and amt(o) is not None and amt(o) >= 10]
        assert rows[s]["전체 합계"] == sum(amt(o) for o in mine)
        day = mine[0]["day"]
        assert rows[s][day.isoformat()] == sum(amt(o) for o in mine if o["day"] == day)
    counted = db("/api/pivot", pivot={"how": "count", "where": "store = 1"})
    assert counted["rows"]["rows"] == [{"전체 개수": sum(o["store"] == 1 for o in ORDERS)}]
    assert "숫자 컬럼" in db("/api/pivot", pivot={"how": "sum", "value": "memo"})["error"]


def test_pivot_too_many_values(db, monkeypatch):
    import calc
    monkeypatch.setattr(calc, "MAX_PIVOT", 5)
    assert "5개보다 많아" in db("/api/pivot", pivot={"rows": ["store"], "column": "id", "how": "count"})["error"]


@pytest.fixture
def files(folder, tmp_path):
    """같은 데이터의 Parquet 파일에 Polars로 연결(창 함수·펼치기를 못 하는 연결)."""
    make_duckdb(tmp_path / "s.duckdb")
    raw = duckdb.connect(str(tmp_path / "s.duckdb"))
    raw.execute(f"copy orders to '{(tmp_path / 'orders.parquet').as_posix()}' (format parquet)")
    raw.close()
    os.remove(tmp_path / "s.duckdb")
    c = client({"kind": "folder", "path": str(tmp_path), "files": [str(tmp_path / "orders.parquet")]})
    yield lambda api="/api/query", **body: c.post(api, json={"base": "orders", "limit": 100, "sorts": ["id asc"], **body}).get_json()
    c.post("/api/disconnect", json={})


def test_file_connection_calcs_on_pc(files):
    found = files(calcs=["누적합 amt 기준 id", "펼치기 tags"], fresh=True)
    assert not found.get("error"), found
    assert found["held"] and found["count"] == sum(o["id"] % 3 for o in ORDERS)
    plain = files()
    assert not plain["held"] and plain["count"] == N  # 계산이 없으면 그대로 파일에서
    summary = files("/api/summary", calcs=["누적합 amt 기준 id"])
    assert any(r["name"] == "amt_누적합" for r in summary["summary"]["rows"])
    assert files("/api/pivot", calcs=["상위 1 기준 amt desc 그룹 store"], pivot={"how": "count"})["rows"]["rows"] == [{"전체 개수": 3}]


def test_file_connection_too_big_for_pc(files, monkeypatch):
    import held
    monkeypatch.setattr(held, "MAX_ROWS", 5)
    assert "받지 않았습니다" in files(calcs=["누적합 amt 기준 id"], fresh=True)["error"]


def test_sqlite_asof_falls_back(folder, tmp_path):
    raw = sqlite3.connect(tmp_path / "s.db")
    raw.execute("create table orders (id integer, store integer, day text)")
    raw.executemany("insert into orders values (?, ?, ?)", [(o["id"], o["store"], o["day"].isoformat()) for o in ORDERS])
    raw.execute("create table prices (store integer, day text, px real)")
    raw.executemany("insert into prices values (?, ?, ?)", [(p["store"], p["day"].isoformat(), p["px"]) for p in PRICES])
    raw.commit()
    raw.close()
    c = client({"kind": "url", "url": f"sqlite://{tmp_path / 's.db'}"})
    try:
        found = c.post("/api/query", json={"base": "orders", "limit": 100, "joins": ["prices asof day>=day, store=store"], "sorts": ["id asc"], "fresh": True}).get_json()
        assert not found.get("error"), found
        assert "__ibis_left" not in found["rows"]["columns"] and found["count"] == N
        for r in found["rows"]["rows"]:
            o = ORDERS[r["id"]]
            near = [p for p in PRICES if p["store"] == o["store"] and p["day"] <= o["day"]]
            assert r["px"] == (max(near, key=lambda p: p["day"])["px"] if near else None)
    finally:
        c.post("/api/disconnect", json={})


def test_longer_falls_back_to_union(tmp_path):
    import calc
    import source
    from lazy import ibis

    def run():  # SQLite 연결은 만든 스레드에서만 쓸 수 있어 연결 스레드에서
        con = ibis.sqlite.connect(tmp_path / "x.db")
        con.raw_sql("create table t as select 1 as id, 2 as a, 3 as b")
        expr = calc.longer(con.table("t"), ["a", "b"])
        assert "UNION ALL" in ibis.to_sql(expr).upper()
        return sorted(expr.to_pyarrow().to_pylist(), key=lambda r: r["항목"])
    assert source.on_worker(run)() == [{"id": 1, "항목": "a", "값": 2.0}, {"id": 1, "항목": "b", "값": 3.0}]


# ---------- 함수 사전(sql_funcs)의 줄·피벗 옵션이 라우트까지 ----------

def test_new_calc_lines_through_the_route(db):
    found = db(calcs=["dense_rank 기준 amt desc 그룹 store 이름 dr", "lead amt 1 기준 id 그룹 store 이름 nxt", "cum_mean amt 기준 id 이름 cm", "식 amt * 2 이름 twice", "식 upper(memo) 이름 up"])
    assert not found.get("error"), found
    nxt, twice, up = by_id(found, "nxt"), by_id(found, "twice"), by_id(found, "up")
    for o in ORDERS:
        later = [x for x in ORDERS if x["store"] == o["store"] and x["id"] > o["id"]]
        assert nxt[o["id"]] == (amt(later[0]) if later else None)
        assert twice[o["id"]] == (None if amt(o) is None else amt(o) * 2) and up[o["id"]] == o["memo"].upper()
    assert not db(calcs=["창거르기 row_number <= 2 기준 id 그룹 store"]).get("error")
    assert db(calcs=["창거르기 row_number <= 2 기준 id 그룹 store"])["count"] == 6
    assert "쓰지 않습니다" in db(calcs=["share_of_group amt 기준 id"])["error"]
    assert "끝에 '이름 새이름'" in db(calcs=["식 amt * 2"])["error"]


def test_sql_expression_line_on_duckdb(db):
    found = db(calcs=["SQL식 upper(memo) || '!' 이름 shout"])
    assert not found.get("error"), found
    assert {r["id"]: r["shout"] for r in found["rows"]["rows"]}[1] == " M1 !"
    assert "읽을 수 없습니다" in db(calcs=["SQL식 (1 이름 x"])["error"] or "SQL" in db(calcs=["SQL식 (1 이름 x"])["error"]
    assert "파일" in db(calcs=["SQL식 (select count(*) from read_csv('x.csv')) 이름 x"])["error"]


def test_sql_expression_line_on_file_connection_uses_pc_copy(files):
    found = files(calcs=["SQL식 amt * 2 이름 twice"], fresh=True)
    assert not found.get("error"), found
    assert found["held"] and {r["id"]: r["twice"] for r in found["rows"]["rows"]}[1] == 74


def test_pivot_new_options(db):
    got = db("/api/pivot", pivot={"rows": ["store"], "how": "median", "value": "amt"})
    rows = {r["store"]: r["전체 중앙값"] for r in got["rows"]["rows"]}
    for s in range(3):
        values = sorted(amt(o) for o in ORDERS if o["store"] == s and amt(o) is not None)
        mid = len(values) // 2
        assert rows[s] == (values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2)
    total = db("/api/pivot", pivot={"rows": ["store"], "how": "sum", "value": "amt", "subtotals": "rollup"})
    assert not total.get("error"), total
    assert [r["store"] for r in total["rows"]["rows"]][-1] == "(전체)" and total["rows"]["rows"][-1]["전체 합계"] == sum(amt(o) or 0 for o in ORDERS)
    big = db("/api/pivot", pivot={"rows": ["store"], "how": "count", "having": "전체 개수 > 6"})
    assert [r["store"] for r in big["rows"]["rows"]] == [s for s in range(3) if sum(o["store"] == s for o in ORDERS) > 6]
    assert "소계" in db("/api/pivot", pivot={"how": "count", "subtotals": "rollup"})["error"]
