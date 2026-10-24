"""9차 9D 데이터: CSV 행 수 고정 형식, fill by + outlier, Slack 오류 기록, 통계 그룹·분포."""
import ibis
import polars as pl
import pytest

from analysis import stats
from collect import collect_store
from core import server
from integrations import commands
from integrations.adapters import slack_bot
from prep import prep_expr


# ---------- 9D1: csv_rows는 DuckDB가 쓴 모양(쉼표·큰따옴표)으로 고정해 읽는다 ----------
def written(tmp_path, df):
    path = tmp_path / "r.csv"
    ibis.duckdb.connect().to_csv(ibis.memtable(df), path)
    return path


def test_csv_rows_late_multiline_value(tmp_path):
    values = ["x"] * 25000 + ["첫 줄\n둘째 줄"]
    assert collect_store.csv_rows(written(tmp_path, pl.DataFrame({"a": list(range(25001)), "b": values}))) == 25001


@pytest.mark.parametrize("sep", ["|", ";"])
def test_csv_rows_one_column_with_separator_chars(tmp_path, sep):
    df = pl.DataFrame({"v": [f"a{sep}b"] * 30000 + ["c"] * 5})
    assert collect_store.csv_rows(written(tmp_path, df)) == 30005


# ---------- 9D2: outlier가 있는 fill by 컬럼도 먼저 변환한 값으로 정렬 ----------
def test_fill_by_column_with_outlier_sorts_by_converted_value(tmp_path):
    path = tmp_path / "f.parquet"
    pl.DataFrame({"d": ["9", "10", "100", "11"], "a": [1.0, None, None, None]}).write_parquet(path)
    con = ibis.duckdb.connect()
    recipe = {"base": "t", "columns": {
        "d": {"ops": {"type": {"to": "int"}, "outlier": {"how": "clip", "by": "range", "low": 0, "high": 1000}}},
        "a": {"ops": {"fill": {"how": "prev", "by": "d"}, "outlier": {"how": "drop", "by": "range", "low": -5, "high": 5}}}}}
    out, _ = prep_expr.shape(con, recipe, table=con.read_parquet(str(path)))
    assert out.order_by("d").to_polars().select("d", "a").rows() == [(9, 1.0), (10, 1.0), (11, 1.0), (100, 1.0)]


# ---------- 9D3: Slack 명령이 실패하면 까닭이 봇 기록(메모리)에 남고 Slack에는 마스킹 글만 간다 ----------
def test_bot_log_keeps_error_but_slack_gets_masked_text():
    sent = []
    secret = "SECRET42"
    server.state["passwords"] = ["pw-9z"]

    def answer(text):
        try:
            raise Exception(f"Conversion Error: 'SECRET42' pw-9z")
        except Exception as e:
            commands.failure.error = commands.detail(e)
            return commands.error_text(e)

    bot = slack_bot.SlackBot("b", "a", [], [], answer, make=lambda b, a: (None, None), send=lambda url, out: sent.append(out))
    entry = {"text": "검색 x"}
    try:
        bot.respond("u", "검색 x", entry)
    finally:
        server.state["passwords"] = []
    assert secret not in sent[0] and "pw-9z" not in sent[0]
    assert "Conversion Error" in entry["error"] and "pw-9z" not in entry["error"]


def test_real_answer_records_failure():
    commands.failure.error = ""
    out = commands.answer("검색 없는검색이름")
    assert out.startswith("할 수 없습니다") and commands.failure.error


# ---------- 9D4~9D6: 통계 ----------
def engine(name, df, tmp_path):
    path = tmp_path / "s.parquet"
    df.write_parquet(path)
    return getattr(ibis, name).connect().read_parquet(str(path), table_name="s")


@pytest.mark.parametrize("name", ["duckdb", "polars"])
def test_groups_float_nan_and_null_are_one_blank_group(name, tmp_path):
    df = pl.DataFrame({"g": [1.0, None, float("nan"), None], "x": [1, 2, 3, 4]})
    t = engine(name, df, tmp_path)
    out = stats.groups(t, "g", "x", "sum")
    assert out["labels"].count("(빈 값)") == 1
    assert sorted(out["counts"]) == [1, 3]


@pytest.mark.parametrize("name", ["duckdb", "polars"])
def test_groups_user_column_named_finite_is_not_summed(name, tmp_path):
    df = pl.DataFrame({"g": ["a", "a", "b"], "x": [1.0, 2.0, 3.0], "__finite": [100, 200, 300]})
    t = engine(name, df, tmp_path)
    out = stats.groups(t, "g", "x", "sum")
    assert sorted(out["values"]) == [3.0, 3.0]


@pytest.mark.parametrize("message", ["Arithmetic overflow error converting expression", "ORA-01426: numeric overflow", "value is OUT OF RANGE"])
def test_distribution_retries_without_std_on_overflow(monkeypatch, message):
    t = ibis.memtable(pl.DataFrame({"x": [1.0, 2.0, 3.0, 4.0]}))
    real = stats.values
    calls = []

    def values(t_, c, more=None, top=None):
        calls.append(more)
        if more is not None and "std" in more:
            raise Exception(message)
        return real(t_, c, more, top)

    monkeypatch.setattr(stats, "values", values)
    out = stats.distribution(t, "x")
    assert out["stats"]["std"] is None and out["stats"]["n"] == 4
