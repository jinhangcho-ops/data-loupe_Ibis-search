"""SQL 기능 빠짐 검사(10-5): 아래 표(계획 10-1의 분류와 항목 전부)의 모든 기능이
(1) 함수 사전(sql_funcs)에 있고, (2) 그 기능을 쓸 화면마다 서버 쪽 목록·상수로 고를 수 있고 실제로 그 화면의 계산 경로가 돌며,
(3) 문장 틀 하나 이상에서 쓰이고, (4) DuckDB 메모리 표에서 실제로 실행되고, (5) 다른 DB 방언으로 SQL을 만들 수 있거나(못 만들면) 대체 식·PC 사본 경로가 있다.
기능을 사전에 더하고 이 표에 올리지 않으면 마지막 시험(표에 없는 함수)이 실패하고, 표에 올리고 화면에 안 달면 해당 시험이 실패한다."""
import json

import duckdb
import pytest
from lazy import ibis

from core import calc
from dashboard import dash_calc
from core import metric_file
from prep import prep_expr
from core import prep_derive
from core import result
from query import sentences
from core import server
from core import sql_funcs as S

AGG = ("pivot", "chart", "metrics", "dash", "prep", "sentence")  # 집계를 고르는 화면
WIN = ("calc", "prep", "sentence")  # 창 함수를 고르는 화면
ROW = ("calc", "prep", "sentence")  # 행마다 계산하는 함수를 고르는 화면

# 기능 표: (기능 이름, [사전 id], 고를 화면들). 계획 10-1의 분류와 항목 그대로.
FEATURES = [
    # 집계
    ("count", ["count"], AGG), ("count_distinct 정확·근사", ["count_distinct", "approx_count_distinct"], AGG),
    ("sum", ["sum"], AGG), ("mean", ["mean"], AGG), ("min", ["min"], AGG), ("max", ["max"], AGG), ("median", ["median"], AGG),
    ("quantile P직접·P50·P90·P95·P99", ["quantile", "p50", "p90", "p95", "p99"], AGG), ("mode", ["mode"], AGG),
    ("std 표본·모집단", ["std", "std_pop"], AGG), ("var 표본·모집단", ["var", "var_pop"], AGG), ("corr", ["corr"], AGG),
    ("cov 표본·모집단", ["cov", "cov_pop"], AGG), ("string_agg 구분자·정렬·중복 빼기", ["string_agg"], AGG), ("collect 목록", ["collect"], AGG),
    ("all 모두 참", ["all"], AGG), ("any 하나라도 참", ["any"], AGG), ("count_if 조건 개수", ["count_if"], AGG), ("sum_if·mean_if 조건부", ["sum_if", "mean_if"], AGG),
    ("arg_min·arg_max", ["arg_min", "arg_max"], AGG), ("first·last 정렬 기준", ["first", "last"], AGG), ("any_value", ["any_value"], AGG), ("범위 max−min", ["range"], AGG),
    # 대표 값 고르기(10-2)
    ("대표 값: 아무 값·첫·마지막·최소·최대·최빈·이어 붙이기·여러 개면 알리기", ["any_value", "first", "last", "min", "max", "mode", "string_agg", "unique_or_null"], AGG),
    # 창
    ("row_number", ["row_number"], WIN), ("rank", ["rank"], WIN), ("dense_rank", ["dense_rank"], WIN), ("percent_rank", ["percent_rank"], WIN),
    ("cume_dist", ["cume_dist"], WIN), ("ntile(N)", ["ntile"], WIN), ("lag·lead(N, 기본값)", ["lag", "lead"], WIN),
    ("first_value·last_value·nth_value", ["first_value", "last_value", "nth_value"], WIN), ("누적 합·평균·최소·최대", ["cum_sum", "cum_mean", "cum_min", "cum_max"], WIN),
    ("이동 합·평균(N행·N일)", ["moving_sum", "moving_mean", "moving_min", "moving_max"], WIN), ("묶음 안 비율", ["share_of_group"], WIN), ("차이·변화율", ["diff", "pct_change"], WIN),
    # 글자
    ("length", ["length"], ROW), ("upper·lower", ["upper", "lower"], ROW), ("첫 글자 대문자", ["capitalize"], ROW), ("trim·ltrim·rtrim", ["trim", "ltrim", "rtrim"], ROW),
    ("substr(시작·길이)", ["substr"], ROW), ("left·right", ["left", "right"], ROW), ("replace", ["replace"], ROW),
    ("regex_contains·regex_extract(그룹)·regex_replace", ["regex_contains", "regex_extract", "regex_replace"], ROW), ("split(목록 또는 N번째)", ["split", "split_part"], ROW),
    ("concat(여러 컬럼·글자)", ["concat"], ROW), ("lpad·rpad", ["lpad", "rpad"], ROW), ("contains·startswith·endswith", ["contains", "startswith", "endswith"], ROW),
    ("find(위치)", ["find"], ROW), ("reverse", ["reverse"], ROW), ("repeat", ["repeat"], ROW),
    # 숫자
    ("round(자릿수)", ["round"], ROW), ("ceil·floor", ["ceil", "floor"], ROW), ("abs", ["abs"], ROW), ("mod", ["mod"], ROW), ("power", ["power"], ROW), ("sqrt", ["sqrt"], ROW),
    ("exp·ln·log10·log(밑)", ["exp", "ln", "log10", "log"], ROW), ("sign", ["sign"], ROW), ("clip(하한·상한)", ["clip"], ROW), ("구간 나누기(경계 목록 → 라벨)", ["bucket"], ROW),
    ("나눗셈 0 → 빈 값", ["safe_divide"], ROW),
    # 날짜
    ("truncate(단위)", ["truncate"], ROW), ("연·분기·월·주·일·요일·시·분·초 뽑기", ["year", "quarter", "month", "week", "day", "weekday", "hour", "minute", "second"], ROW),
    ("epoch", ["epoch"], ROW), ("날짜 더하기·빼기(N 단위)", ["date_add", "date_sub"], ROW), ("두 날짜 차이(단위)", ["date_diff"], ROW), ("형식으로 글자(strftime)", ["strftime"], ROW),
    ("글자에서 날짜 읽기(형식)", ["parse_date"], ROW), ("날짜만·시각만", ["to_date", "to_time"], ROW), ("오늘·지금 기준 차이", ["diff_now", "diff_today"], ROW),
    # 조건
    ("CASE", ["case"], ROW), ("coalesce", ["coalesce"], ROW), ("nullif", ["nullif"], ROW), ("ifelse", ["ifelse"], ROW), ("greatest·least", ["greatest", "least"], ROW),
    ("isin", ["isin"], ROW), ("between", ["between"], ROW), ("isnull·notnull", ["isnull", "notnull"], ROW),
    # 형 변환
    ("cast", ["cast"], ROW), ("try_cast(실패 → 빈 값)", ["try_cast"], ROW),
    # 구조
    ("JSON 경로 값", ["json_get"], ROW), ("구조 필드", ["struct_field"], ROW), ("목록 길이·N번째", ["array_length", "array_get"], ROW), ("펼치기", ["unnest"], ("calc", "sentence")),
    # 좌표
    ("좌표: 두 점 거리·반경 안·범위 상자·격자 칸 id·칸 중심", ["haversine", "within_km", "in_box", "grid_id", "grid_lat", "grid_lon"], ROW),
    # 묶음
    ("HAVING", ["having"], ("pivot", "sentence")), ("QUALIFY", ["qualify"], ("calc", "sentence")),
    ("ROLLUP·CUBE·GROUPING SETS", ["rollup", "cube", "grouping_sets"], ("pivot", "sentence")), ("DISTINCT", ["distinct"], ("prep", "sentence")),
    # DB 고유 함수
    ("SQL 식 직접 쓰기", ["sql_expr"], ("calc", "sentence")),
]
FEATURE_IDS = sorted({i for _, ids, _ in FEATURES for i in ids})
DIALECTS = ["postgres", "mysql", "mssql", "bigquery", "snowflake", "oracle", "sqlite", "trino"]


@pytest.fixture(scope="module")
def px():
    """모든 종류의 컬럼이 든 DuckDB 메모리 표 px. s는 날짜 글자, j는 JSON이라 글자·날짜·JSON 함수가 모두 돈다."""
    raw = duckdb.connect()
    raw.execute("create table px (n double, i bigint, j json, s varchar, d timestamp, dd date, b boolean, a varchar[], st struct(k varchar))")
    for k in range(6):
        raw.execute("insert into px values (?, ?, ?, ?, timestamp '2026-03-05 10:30:00' + ? * interval 1 day, date '2026-03-05' + ?, ?, ['p', 'q'], {'k': 'v'})",
                    [float(k + 1), k, '{"a": %d}' % k, f"2026-03-0{k + 1}", k, k, k % 2 == 0])
    yield ibis.duckdb.from_connection(raw).table("px")
    raw.close()


def json_args(f, t):
    """시험 인자(S.probe_args)를 화면이 보내는 JSON 모양으로: 조건은 조건 줄, 정렬은 줄."""
    raw = S.probe_args(f, t)
    assert raw is not None, f"{f['id']}: 시험할 컬럼을 못 찾음"
    out = {}
    for spec in f["args"]:
        name = spec["name"]
        if name not in raw:
            continue
        kind = spec["kind"]
        out[name] = "n > 0" if kind == "cond" else ["i"] if kind == "order" else [{"when": "n > 0", "then": 1}] if kind == "cases" else raw[name]
    return out


def column_arg(f, raw):
    """집계 칸 {y, opts}: 첫 컬럼 칸(x)은 y로, 나머지는 opts로."""
    opts = {k: v for k, v in raw.items() if k not in ("x", "where")}
    return raw.get("x"), opts


# ---------- (1) 사전에 있다 ----------

def test_table_matches_catalog():
    """사전의 모든 함수가 이 표에 있고(표에 안 올린 기능 = 빠짐 검사 밖), 표의 모든 id가 사전에 있다."""
    missing = [i for i in FEATURE_IDS if i not in S.FUNCS]
    assert not missing, f"사전에 없는 기능: {missing}"
    unlisted = [i for i in S.FUNCS if i not in FEATURE_IDS]
    assert not unlisted, f"표에 올리지 않은 함수(표에 더해 화면 연결까지 검사하세요): {unlisted}"


def test_every_function_has_text_and_example():
    for f in S.FUNCS.values():
        assert f["label"] and f["help"] and f["example"] and f["category"] in dict(S.CATEGORIES), f["id"]


def test_catalog_goes_to_screens(client):
    """화면이 받는 사전(GET /api/funcs)이 사전 전체를 담고, 작은 목록은 상수(CONSTANTS)로 간다."""
    funcs = client.get("/api/funcs").get_json()
    json.dumps(server.CONSTANTS)
    assert [f["id"] for f in funcs["funcs"]] == list(S.FUNCS) and set(funcs["screens"]) == set(S.SCREENS)
    assert set(server.CONSTANTS["agg_names"]) >= set(S.aggregate_ids())


# ---------- (2) 화면마다 고를 수 있다 ----------

@pytest.mark.parametrize("name, ids, screens", FEATURES, ids=[f[0] for f in FEATURES])
def test_screen_lists_offer_the_feature(client, name, ids, screens):
    """화면의 서버 쪽 목록·상수가 그 기능을 담는다(화면 목록은 모두 사전에서 나온다)."""
    served = client.get("/api/funcs").get_json()["screens"]
    lists = {
        "pivot": set(calc.HOWS) | set(S.ids_for("pivot")), "chart": set(result.chart_aggs()), "metrics": set(metric_file.AGGS), "dash": set(dash_calc.AGGS),
        "calc": set(calc.NEW_WINDOWS) | set(S.ids_for("calc")), "prep": set(S.ids_for("prep")), "sentence": {i for ids_ in sentences.uses_map().values() for i in ids_},
    }
    for i in ids:
        alias = next((a for a, target in S.ALIASES.items() if target == i), None)
        for screen in screens:
            assert i in lists[screen] or alias in lists[screen], f"{i}가 {screen} 화면 목록에 없음"
            assert i in served[screen], f"{i}가 /api/funcs의 {screen} 목록에 없음"


def test_screens_that_must_not_offer_wrong_kinds():
    assert "rank" not in calc.HOWS and "upper" not in result.chart_aggs() and "sum" not in S.ids_for("calc")
    assert set(S.ids_for("chart")) == set(S.aggregate_ids()) == set(S.ids_for("dash")) == set(S.ids_for("metrics"))


AGGREGATES = [i for i in S.FUNCS if S.FUNCS[i]["aggregate"]]
WINDOWS = [i for i in S.FUNCS if S.FUNCS[i]["window"]]
ROWS = [i for i, f in S.FUNCS.items() if not (f["aggregate"] or f["window"] or f["table"] or f["raw"])]


@pytest.mark.parametrize("id", AGGREGATES)
def test_aggregate_runs_in_pivot_chart_metrics_and_dash(px, id):
    """집계 함수 하나가 피벗·차트·지표·대시보드 카드의 계산 경로에서 실제로 돈다."""
    f = S.FUNCS[id]
    y, opts = column_arg(f, json_args(f, px))
    spec = {"rows": ["b"], "how": id, "value": y, "opts": opts or None, "where": "n > 0"}
    table = calc.pivot(px, spec, lambda e: e.to_polars()["v"].to_list()).to_polars()
    assert table.height == 2 and f"전체 {f['label']}" in table.columns
    labels, datasets, _ = result.get_chart(px, "b", [{"y": y, "agg": id, "opts": opts or None}])
    assert len(labels) == 2 and len(datasets[0]["values"]) == 2
    found = {"m": metric_file.metric("m", {"numerator": {"agg": id, "column": y, **({"opts": opts} if opts else {}), "where": "n > 0"}})}
    _, values, _ = metric_file.compute(px, ["m"], found, by="b")
    assert len(values["m"]) == 2
    series = dash_calc.clean_series([{"y": y, "agg": id, **({"opts": opts} if opts else {})}])
    data = dash_calc.time_data(px, "d", series, None)
    assert data["by_unit"]["day"]["labels"] and data["by_unit"]["day"]["series"][0]["agg"] == id
    if not dash_calc.merges(id):
        assert "merge" in data["by_unit"]["day"]["series"][0] and "month" in data["by_unit"]


def test_old_aggregate_name_still_runs_everywhere(px):
    table = calc.pivot(px, {"rows": ["b"], "how": "nunique", "value": "s"}, lambda e: []).to_polars()
    assert "전체 고유 개수" in table.columns and sorted(table["전체 고유 개수"].to_list()) == [3, 3]
    assert result.get_chart(px, "b", [{"y": "s", "agg": "nunique"}])[1][0]["values"] == [3, 3]
    assert dash_calc.clean_series([{"y": "s", "agg": "nunique"}])[0]["agg"] == "nunique"
    assert metric_file.AGG_NAMES["nunique"] == "고유 개수"


FORMULA_SKIP = {"sql_expr"}


def formula_text(f, t):
    """함수 사전 id로 쓴 계산 컬럼의 식 글자(첫 컬럼 칸부터 칸 차례대로)."""
    raw = json_args(f, t)
    if f["id"] == "case":
        return "case(n > 0, 1, 0)"
    parts = []
    for spec in f["args"]:
        name = spec["name"]
        if name in S.HAS_ARGS or name not in raw:
            continue
        v, kind = raw[name], spec["kind"]
        parts.append("n > 0" if kind == "cond" else ", ".join(v) if kind == "columns" else ", ".join(map(repr, v)) if kind == "list" else v if kind == "column" else repr(v))
    return f"{f['id']}({', '.join(parts)})"


@pytest.mark.parametrize("id", [i for i in S.ids_for("prep") if S.FUNCS[i]["table"] is False and i not in FORMULA_SKIP])
def test_prep_formula_runs_every_function(px, id):
    """전처리의 계산 컬럼 식(함수 사전의 id)이 행·창·집계 함수 전부에서 돈다."""
    f = S.FUNCS[id]
    text = formula_text(f, px)
    value = prep_derive.Formula(px, text, [px.i.asc()], ["b"] if f["window"] or f["aggregate"] else []).build()
    got = px.select(v=value).to_polars()["v"]
    assert got.len() == 6, text


def test_prep_formula_window_needs_order_and_unknown_function_is_refused(px):
    with pytest.raises(ValueError, match="정렬 기준"):
        prep_derive.Formula(px, "rank()").build()
    with pytest.raises(ValueError, match="쓸 수 없는|함수"):
        prep_derive.Formula(px, "nope(n)").build()
    with pytest.raises(ValueError, match="표를 바꾸는"):
        prep_derive.Formula(px, "having(n > 1)").build()
    t, made = prep_derive.apply_formulas(px, [{"name": "r", "expr": "rank()", "order": ["n desc"]}, {"name": "g", "expr": "sum(n)", "group": ["b"]}])
    frame = t.select("n", "r", "g").order_by("n").to_polars()
    assert made == ["r", "g"] and frame["r"].to_list() == [6, 5, 4, 3, 2, 1]
    assert sorted(set(frame["g"].to_list())) == [9.0, 12.0]  # b가 참(n = 1, 3, 5)과 거짓(n = 2, 4, 6)의 합


@pytest.mark.parametrize("id", WINDOWS)
def test_calc_line_runs_every_window_function(px, id):
    """탐색 계산 줄이 함수 사전의 창 함수 id로 시작해 돈다."""
    f = S.FUNCS[id]
    raw = json_args(f, px)
    words = [str(raw[a["name"]]) for a in f["args"] if a["name"] in raw and a["name"] not in S.HAS_ARGS and a["kind"] in ("column", "number", "int", "choice", "text")]
    have = {a["name"] for a in f["args"]}
    line = f"{id} {' '.join(words)}{' 기준 i' if 'order' in have else ''}{' 그룹 b' if 'group' in have else ''} 이름 out".replace("  ", " ")
    out = calc.apply([line], px).select("out").to_polars()
    assert out.height == 6, line


@pytest.mark.parametrize("id", ROWS)
def test_calc_formula_line_runs_every_row_function(px, id):
    out = calc.apply([f"식 {formula_text(S.FUNCS[id], px)} 이름 out"], px).select("out").to_polars()
    assert out.height == 6


def test_calc_qualify_unnest_and_sql_lines(px):
    top = calc.apply(["창거르기 rank <= 2 기준 n desc 그룹 b"], px).select("n").to_polars()
    assert sorted(top["n"].to_list()) == [3.0, 4.0, 5.0, 6.0]  # b가 참(1, 3, 5)·거짓(2, 4, 6) 안에서 큰 2개씩
    assert calc.apply(["펼치기 a"], px).count().execute() == 12
    assert calc.apply(["SQL식 n * 2 이름 double"], px).select("double").to_polars()["double"].to_list()[:2] == [2.0, 4.0]


def test_pivot_subtotals_and_having(px):
    table = calc.pivot(px, {"rows": ["b", "i"], "how": "sum", "value": "n", "subtotals": "rollup"}, lambda e: []).to_polars()
    assert table.height == 6 + 2 + 1 and table.filter(table["b"] == S.TOTAL)["전체 합계"].to_list() == [21.0]
    kept = calc.pivot(px, {"rows": ["b"], "how": "sum", "value": "n", "having": "전체 합계 > 10"}, lambda e: []).to_polars()
    assert kept["전체 합계"].to_list() == [12.0]
    with pytest.raises(ValueError, match="소계"):
        calc.pivot(px, {"rows": [], "how": "sum", "value": "n", "subtotals": "rollup"}, lambda e: [])


def test_prep_dedupe_distinct_and_choices(px):
    t = px.select("n", "b")
    assert prep_expr.dedupe_rows(t, ["b"], {}).count().execute() == 2
    assert prep_expr.dedupe_rows(t, ["n", "b"], {}).count().execute() == 6  # 모든 컬럼이 기준 = DISTINCT


# ---------- (3) 문장 틀에서 쓰인다 ----------

def test_every_function_is_used_by_a_sentence_template():
    assert sentences.unused() == []
    used = {i for ids in sentences.uses_map().values() for i in ids}
    assert set(FEATURE_IDS) <= used


# ---------- (4) DuckDB에서 실제로 실행된다 ----------

@pytest.mark.parametrize("id", [i for i, f in S.FUNCS.items() if not f["raw"]])
def test_function_executes_on_duckdb(px, id):
    f = S.FUNCS[id]
    expr = S.probe_expr(f, px)
    assert expr is not None
    if "j" in expr.columns and len(expr.columns) > 1:  # JSON 컬럼은 Polars로 바꿀 수 없어 뺀다
        expr = expr.drop("j")
    assert expr.limit(5).to_polars().width >= 1


def test_raw_sql_executes_on_duckdb(px):
    assert S.raw_expr(px, "twice", "n * 2").select("twice").to_polars()["twice"].to_list()[:2] == [2.0, 4.0]


def test_support_on_a_real_connection(px):
    found = S.support(px)
    assert set(found) == set(S.FUNCS) and all(v["status"] == "ok" for i, v in found.items() if i != "sql_expr")


# ---------- (5) 다른 DB 방언 ----------

@pytest.mark.parametrize("dialect", DIALECTS)
def test_every_function_builds_sql_or_has_a_fallback(dialect):
    """방언으로 SQL을 만들 수 있거나, 못 만들면 같은 결과의 대체 식(substitute, 설명 있음)이나 이 PC 사본 계산(local)이 있다. 고를 수 없는 함수(unavailable)는 없다."""
    bad = {}
    for id, f in S.FUNCS.items():
        if f["raw"]:
            continue
        got = S.dialect_status(id, dialect)
        if got["status"] == "ok":
            continue
        if got["status"] == "substitute" and got["note"] and S.SUBS.get(id):
            continue
        if got["status"] == "local" and "PC" in got["note"]:
            continue
        bad[id] = got
    assert not bad, f"{dialect}: {bad}"


def test_some_dialect_really_needs_the_fallbacks():
    """방언 시험이 아무것도 못 잡는 헛시험이 아니도록: 어떤 방언은 대체 식을, 어떤 방언은 PC 사본을 실제로 쓴다."""
    got = [S.dialect_status(i, d)["status"] for d in DIALECTS for i in ("any_value", "median", "corr", "first")]
    assert "substitute" in got and "local" in got and "ok" in got
