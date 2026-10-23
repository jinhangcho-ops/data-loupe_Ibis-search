"""3차 감사 수정 3B(core·쿼리·지표): 고치기 전에는 실패하던 것들을 하나씩 재현한다. 데이터는 테스트 안에서 지어내고 값은 손으로 센다."""
import polars as pl
import pytest
from lazy import ibis

from core import sql_funcs


def both_engines(data, name="t"):
    """같은 표를 DuckDB와 Polars 연결에 올린 {엔진: 표}. Polars는 파일 연결이 쓰는 같은 보정(source.fix_polars_text)을 건다."""
    from core import source
    source.fix_polars_text()
    return {n: c.create_table(name, ibis.memtable(pl.DataFrame(data))) for n, c in (("duckdb", ibis.duckdb.connect()), ("polars", ibis.polars.connect()))}


# ---------- 3B1 half_up 큰 값·경계 ----------

def test_half_up_big_values_and_just_below_half():
    data = {"x": [5e13 + 0.123456, 4e13 + 0.123456, 0.49999999999999994, -0.49999999999999994, 2.5, 5e15 + 0.5]}
    for name, t in both_engines(data).items():
        two = t.select(r=sql_funcs.half_up(t.x, 2)).to_polars()["r"].to_list()
        assert two[0] == pytest.approx(5e13 + 0.12, abs=1e-6) and two[0] != 5e13 + 0.123456, name  # 스케일한 값이 2^52를 넘어도 반올림
        assert two[1] == pytest.approx(4e13 + 0.12, abs=0.02), name
        zero = t.select(r=sql_funcs.half_up(t.x, 0)).to_polars()["r"].to_list()
        assert zero[2] == 0.0 and zero[3] == 0.0 and zero[4] == 3.0, name  # 0.4999…는 0(+0.5가 올림되지 않는다)
        assert zero[5] == 5e15 + 0.5 or zero[5] in (5e15, 5e15 + 1), name  # 2^52 이상은 이미 정수


# ---------- 3B3 find(…, 시작) 빈 값 ----------

def test_find_with_start_on_null_matches_between_engines():
    data = {"s": ["a,b", None, "abc"]}
    got = {name: t.select(a=t.s.find(",", 2), b=t.s.find(",")).to_polars().to_dict(as_series=False) for name, t in both_engines(data).items()}
    assert got["polars"] == got["duckdb"]  # 시작을 주면 빈 값도 -1(DuckDB), 안 주면 빈 값


# ---------- 3B2 CP949 CSV 다시 변환 ----------

def test_cp949_csv_is_reconverted_after_the_original_changes(tmp_path):
    from core import result, source
    path = tmp_path / "a.csv"
    path.write_bytes("이름,값\n가나,1\n".encode("cp949"))
    con = source.open_flat_files([path])
    t = con.table("a")
    assert result.lazy(t).collect()["이름"].to_list() == ["가나"]
    path.write_bytes("이름,값\n다라마,2\n바사,3\n".encode("cp949"))  # 크기가 바뀌므로 (mtime, 크기)로 알아챈다
    assert result.lazy(t).collect()["이름"].to_list() == ["다라마", "바사"]
    source.close(con)


def test_utf16_csv_gets_a_clear_korean_error(tmp_path):
    from core import source
    path = tmp_path / "u.csv"
    path.write_bytes("a,b\n1,2\n".encode("utf-16"))
    with pytest.raises(ValueError, match="UTF-16"):
        source.open_flat_files([path])


# ---------- 3B4 e41 공백 있는 컬럼 조건 ----------

@pytest.mark.parametrize("cond, want", [
    ("unit price >= 100", "col('unit price') >= 100"),
    ("unit price in 1, 2", "isin(col('unit price'), [1, 2])"),
    ("unit price is null", "isnull(col('unit price'))"),
    ("unit price between 1 5", "between(col('unit price'), 1, 5)"),
    ("unit price like ab", "contains(col('unit price'), 'ab')"),
])
def test_e41_conditions_accept_columns_with_spaces(cond, want):
    from query import sentence_run as sr
    cols = {"unit price": "num", "store": "text"}
    done = sr.compile("e41", {"조건1": cond, "값1": "high", "값3": "low", "새 이름": "level"}, {"base": "t"}, cols)
    assert done["ok"], done
    assert done["body"]["calcs"] == [f"식 case({want}, 'high', 'low') 이름 level"]


# ---------- 3B 지표 ----------

import datetime as dt  # noqa: E402

from metrics import metric_calc as mc  # noqa: E402
from metrics import metric_catalog as cat  # noqa: E402

D = dt.datetime
KEY = {"kind": "key", "keys": [["ch", "ch"]]}


def mem(**cols):
    return ibis.memtable(pl.DataFrame(cols).to_arrow())


def run(entry_id, slots, table, align=None, other=None, notes=None):
    ctx = cat.Ctx(table=table, other=(lambda name: other[name]) if other else None)
    return cat.compute(entry_id, slots, {}, ctx, align, None)


# ---------- 3B5 공통 키 ROAS: 매출도 사용자의 첫 키로 ----------

def test_roas_by_key_groups_revenue_under_each_users_first_key():
    # 사용자 1: 3/2 A(금액 0), 3/3 B(100) → 신규는 A, 매출 100도 A. 사용자 2: 3/3 B(10) → 신규 B, 매출 10도 B
    users = mem(uid=[1, 1, 2], ts=[D(2026, 3, 2), D(2026, 3, 3), D(2026, 3, 3)], ch=["A", "B", "B"], amt=[0.0, 100.0, 10.0])
    ads = {"ads": mem(cost=[10.0, 10.0], ch=["A", "B"])}
    s = {"사용자": "uid", "시각": "ts", "금액": "amt", "N일": 7, "광고 테이블": "ads", "광고비": "cost"}
    got = run("roas", s, users, other=ads, align=KEY)
    assert {r["key"]: r["revenue"] for r in got["rows"]} == {"A": 100.0, "B": 10.0}
    ltv = run("ltv_cac", {**s, "N일": 90}, users, other=ads, align=KEY)
    assert {r["key"]: r["ltv"] for r in ltv["rows"]} == {"A": 100.0, "B": 10.0}


def test_common_key_new_user_metrics_are_marked_as_window_metrics():
    for i in ("cac", "cac_paid", "roas", "ltv_cac", "payback"):
        assert cat.get(i)["window"], i  # 첫 키를 row_number(창 함수)로 골라서 원격 연결은 PC 사본에서


# ---------- 3B6 meta·exit_rate 자름 안내 ----------

def test_meta_and_exit_rate_note_when_rows_are_cut():
    n = 65
    items = mem(item=[f"i{k:02d}" for k in range(n)], res=["win"] * n)
    got = run("pick_rate", {"항목": "item", "결과": "res", "성공 값": "win"}, items)
    assert len(got["rows"]) == 60 and any("60개만" in x for x in got["notes"])
    events = mem(uid=[1] * n, ts=[D(2026, 3, 1, 9, k % 60, k // 60) for k in range(n)], ev=[f"p{k:02d}" for k in range(n)])
    ex = run("exit_rate", {"사용자": "uid", "시각": "ts", "페이지": "ev"}, events)
    assert len(ex["rows"]) == 60 and any("60개만" in x for x in ex["notes"])


# ---------- 3B7 MDE 그룹당 1행 ----------

def test_mde_continuous_with_one_row_per_group_is_none_with_a_note():
    t = mem(g=["c", "t"], v=[1.0, 5.0])
    got = run("mde", {"실험군": "g", "지표 값": "v", "대조군 값": "c", "실험군 값": "t"}, t)
    assert got["value"] is None and any("표준편차" in x for x in got["notes"])


# ---------- 3B9 스테이지 정렬 NaN ----------

def test_stage_clear_sorts_stages_with_nan_last():
    nan = float("nan")
    stage = mem(st=[3.0, nan, 1.0, 2.0, nan, 1.0], res=["win"] * 6)
    got = run("stage_clear", {"스테이지": "st", "결과": "res", "성공 값": "win"}, stage)
    assert [r["stage"] for r in got["rows"]] == ["1.0", "2.0", "3.0", "nan"]


# ---------- 3B11 활성 통화량 같은 시각 ----------

def test_money_supply_same_time_takes_the_larger_balance():
    t = mem(uid=[1, 1, 1], ts=[D(2026, 3, 1), D(2026, 3, 2), D(2026, 3, 2)], bal=[10.0, 50.0, 70.0])
    for rows in (t, mem(uid=[1, 1, 1], ts=[D(2026, 3, 1), D(2026, 3, 2), D(2026, 3, 2)], bal=[10.0, 70.0, 50.0])):
        got = run("money_supply", {"사용자": "uid", "시각": "ts", "잔액": "bal", "활성 기준(일)": 30}, rows)
        assert got["value"] == 70.0
