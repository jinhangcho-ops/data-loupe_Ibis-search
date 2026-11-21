"""(B10b) KPI 큰 창의 시간 눈금 값: 카드의 기간 기준 날짜 컬럼을 시(時)로 잘라 첫 계열을 집계한다.
큰 창을 열 때 한 번만 부른다(라우트 dash_app.card_hours, /api/dash/card/hours). 읽는 범위는 일 값(value_data)과 같다(dash_calc.kpi_window)."""
from analysis import stats
from core import parse
from core.server import run
from dashboard import dash_calc

MAX_HOURS = 4416  # 돌려줄 시간 칸 수(24시 × 184일 = 분기 + 직전 분기). 넘으면 가장 최근 것만
HOUR_FORMAT = "%Y-%m-%dT%H"  # 시간 칸 라벨(화면 kpi_trend.hourTrend가 앞 10자를 날짜로 읽는다)
HOUR_DATE_ONLY = "'{column}'에는 시각이 없어 일 눈금으로 보여요."
HOUR_NO_DATE = "기간 기준 날짜 컬럼을 고른 카드만 시간 눈금을 볼 수 있어요."
HOUR_METRIC = "지표로 만든 KPI는 시간 눈금을 볼 수 없어요."
HOUR_NOT_NUMBER = "숫자로 집계한 KPI만 시간 눈금을 볼 수 있어요."


def hour_values(card: dict, ctx: dict | None) -> dict[str, object]:
    """[SPECIFICATION] plans/dash-blocks.md#hour_values
    카드 첫 계열의 시간별 값 {labels, values, reason}을 돌려준다(시각 없는 날짜 컬럼이면 빈 목록과 이유)."""
    body = card["source"].get("body")
    date = card["source"].get("date")
    if not date or not isinstance(body, dict):
        raise ValueError(HOUR_NO_DATE)
    if body.get("names"):
        raise ValueError(HOUR_METRIC)
    s = dash_calc.clean_series(body.get("series"))[0]
    read, window = dash_calc.kpi_window(card, ctx)  # 일 값(value_data)과 같은 읽는 범위
    expr = dash_calc.card_expr(read, ctx, window)
    parse.check_name(date, expr.columns, "날짜 컬럼")
    kind = expr[date].type()
    if kind.is_date():
        return {"labels": [], "values": [], "reason": HOUR_DATE_ONLY.format(column=date)}
    if not kind.is_timestamp():
        raise ValueError(f"'{date}'은 날짜 컬럼이 아니에요.")
    t = expr.filter(expr[date].notnull())
    v = dash_calc.final_expr(t, s)
    if not v.type().is_numeric():
        raise ValueError(HOUR_NOT_NUMBER)
    q = t.group_by(stats.local_start(t[date], "h").name("x")).aggregate(v=v.cast("float64"))
    q = q.order_by(q.x.desc()).limit(MAX_HOURS)
    frame = run(lambda: q.to_polars()).reverse()
    return {"labels": frame["x"].dt.strftime(HOUR_FORMAT).to_list(), "values": frame["v"].to_list(), "reason": ""}
