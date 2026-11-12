"""감사 48(분석·전처리·지표): 전환 0인 A/B 검정, 값이 빈 기간이 낀 추이·시계열 분해, 빈 시간 구간의 시간대, 원격 읽기 횟수, 이어진 문장 포함 중복 제거, 코호트가 받는 행. 데이터는 여기서 지어낸다."""

import polars as pl

from lazy import ibis
from analysis import insight, stats
from core import settings
from metrics import metric_calc
from prep import prep_diag, prep_expr


def test_abtest_all_zero_has_no_adjusted_p(folder):
    t = ibis.memtable(pl.DataFrame({"g": ["A"] * 50 + ["B"] * 50 + ["C"] * 50, "y": [0] * 100 + [1] * 50}))  # A·B는 전환 0
    assert insight.ab_test(t, "g", "rate", "y", conf=95.0)["pairs"][0]["p_adj"] is None


def test_null_sum_day_is_zero_and_seasonal_runs(folder):
    con = ibis.duckdb.connect()
    con.raw_sql("""create table s as select date '2026-01-05' + cast(i // 3 as int) as d,
      case when dayofweek(date '2026-01-05' + cast(i // 3 as int)) = 0 then null else 100 + (i % 7) end as sales from range(180) r(i)""")  # 일요일은 행은 있고 값이 빈다
    t = con.table("s")
    assert None not in stats.trend(t, "d", "day", "sales", "sum")["values"]
    for agg in ("sum", "mean"):
        insight.seasonal(t, "d", "day", "sales", agg)  # 오류 없이


def test_time_gaps_use_setting_timezone(folder, monkeypatch):
    monkeypatch.setattr(settings, "load", lambda: {"timezone": "Asia/Seoul"})
    con = ibis.duckdb.connect()
    con.raw_sql("create table e as select * from (values (timestamptz '2026-01-01 01:00:00+00'), (timestamptz '2026-01-02 23:30:00+00')) v(ts)")  # 서울 1월 1일, 1월 3일
    assert prep_diag.time_gaps(con.table("e"), "ts", "day", prep_diag.options(None))["missing"] == 1  # 서울 날짜로 1월 2일이 빈다


def test_remote_reads_cover_distribution_and_scatter():
    assert insight.READS["distribution"] == 5 and insight.READS["scatter"] == 5


def test_contain_chain_compares_pairs_once(folder, monkeypatch):
    calls = [0]
    real = prep_expr.text_contains

    def counted(hay: str, needle: str) -> bool:
        calls[0] += 1
        return real(hay, needle)
    monkeypatch.setattr(prep_expr, "text_contains", counted)
    words = [w for i in range(6) for w in (f"<{i}>", f"<{i}><{i + 1}>")]  # <0> ⊂ <0><1> ⊃ <1> ⊂ <1><2> … 12개가 한 사슬
    got = prep_expr.contain_groups(ibis.memtable(pl.DataFrame({"s": words})), ["s"], ["s"], None).to_polars()
    assert got[prep_expr.GROUP].n_unique() == 1
    assert calls[0] <= 3 * len(words) ** 2  # 쌍 비교는 한 번만(고치기 전에는 회차마다 다시 해 4만 번이 넘었다)


def test_cohort_counts_only_shown_cells(folder, monkeypatch):
    con = ibis.duckdb.connect()
    con.raw_sql("create table a as select i % 50 as u, date '2026-01-01' + cast(i // 25 as int) as d from range(5000) r(i)")  # 사용자마다 200일 중 100일 활동
    seen = []
    real = metric_calc.rows
    monkeypatch.setattr(metric_calc, "rows", lambda e: seen.append(len(got := real(e))) or got)
    metric_calc.cohort_matrix(con.table("a"), "u", "d", "day", "classic", n_max=7)
    assert max(seen) <= 2 * 8  # 코호트(첫날 2개) × (n_max + 1)칸 안. 고치기 전에는 경과 200일 치를 다 받았다
