"""결과 계산과 파일 저장. 이 단계에서 처음으로 연산이 실행된다."""
from decimal import Decimal
from pathlib import Path

from lazy import ibis

from parse import check_name, type_info

SUMMARY_COLS = ["name", "type", "count", "nulls", "unique", "min", "max", "mean"]
MAX_GROUPS = 50  # 차트에 표시할 최대 그룹 수
MAX_SPLITS = 8  # 차트에서 한 컬럼의 값별로 나눌 때 최대 값 개수
MAX_VALUES = 200  # 조건을 만들 때 보여 줄 컬럼 값 목록의 최대 개수
EXCEL_MAX_ROWS = 1_048_575  # Excel 시트 한 장의 행 수(제목 줄 제외)


def write_excel(expr, path):
    if get_count(expr) > EXCEL_MAX_ROWS:
        raise ValueError(f"Excel은 {EXCEL_MAX_ROWS:,}행까지 저장할 수 있습니다. CSV나 Parquet으로 저장하세요.")
    expr.to_polars().write_excel(path)


# 확장자: 저장 함수
EXPORTS = {
    ".csv": lambda expr, path: expr.to_csv(path),
    ".xlsx": write_excel,
    ".parquet": lambda expr, path: expr.to_parquet(path),
}


def columns(expr):
    """[(컬럼, 한글 타입)]"""
    return [(name, type_info(dtype)[0]) for name, dtype in expr.schema().items()]


def get_rows(expr, limit, offset=0):
    """offset행을 건너뛰고 limit행."""
    return expr.limit(limit, offset=offset).to_polars()


def get_sql(expr, limit, offset=0):
    """get_rows가 실행할 조회를 SQL 글로. 실행하지는 않는다. SQL을 쓰지 않는 연결(Parquet·CSV 파일)은 보여 줄 수 없다."""
    try:
        return str(ibis.to_sql(expr.limit(limit, offset=offset)))
    except NotImplementedError:
        raise ValueError("이 연결의 조회는 SQL로 보여 줄 수 없습니다.") from None


def get_count(expr):
    return expr.count().to_pyarrow().as_py()


def plain(value):
    """JSON으로 보낼 수 있는 값. 숫자와 빈 값은 그대로, Decimal은 실수로, 나머지(날짜 등)는 글자로."""
    return value if value is None or isinstance(value, (int, float)) else float(value) if isinstance(value, Decimal) else str(value)


def get_summary(expr, columns=()):
    """컬럼별 집계를 조회 한 번으로 계산해 SUMMARY_COLS 모양의 줄들로 돌려준다. columns를 주면 그 컬럼만 그 순서로.
    개수(빈 값 제외)·빈 값·고유값은 모든 컬럼, 최소·최대는 숫자와 날짜, 평균은 숫자만 계산한다."""
    for column in columns:
        check_name(column, expr.columns)
    if columns:
        expr = expr.select(list(columns))
    kinds = {name: (t.is_numeric() or t.is_temporal(), t.is_numeric()) for name, t in expr.schema().items()}
    metrics = {}
    for i, (name, (ordered, numeric)) in enumerate(kinds.items()):
        col = expr[name]
        metrics.update({f"count{i}": col.count(), f"nulls{i}": col.isnull().sum(), f"unique{i}": col.nunique()})
        if ordered:
            metrics.update({f"min{i}": col.min(), f"max{i}": col.max()})
        if numeric:
            metrics[f"mean{i}"] = col.mean()
    found = expr.aggregate(**metrics).to_polars().row(0, named=True)
    return [{"name": name, "type": str(expr.schema()[name]).lstrip("!"), "count": found[f"count{i}"], "nulls": found[f"nulls{i}"] or 0,
             "unique": found[f"unique{i}"], **{k: plain(found.get(f"{k}{i}")) for k in ("min", "max", "mean")}}
            for i, name in enumerate(kinds)]


def get_values(expr, column, limit=MAX_VALUES):
    """컬럼의 서로 다른 값(빈 값 제외)을 정렬해 limit개까지 문자로 돌려준다. 더 있으면 두 번째 값이 True."""
    check_name(column, expr.columns)
    values = expr.filter(expr[column].notnull()).select(column).distinct().order_by(column).limit(limit + 1)
    values = [str(v) for v in values.to_polars()[column].to_list()]
    return values[:limit], len(values) > limit


def chart_metric(expr, y, agg):
    """계열 하나의 집계 식. count는 y 없이 행 수, sum·mean은 숫자 컬럼 y가 있어야 한다."""
    if agg == "count":
        return expr.count()
    if agg not in ("sum", "mean"):
        raise ValueError("집계 방식은 count, sum, mean 중 하나입니다.")
    if y not in expr.columns or not expr[y].type().is_numeric():
        raise ValueError("합계·평균은 숫자 컬럼을 Y로 골라야 합니다.")
    return getattr(expr[y], agg)()


def get_chart(expr, x, series, split=None, order="x"):
    """x별로 계열들을 집계한다. series는 [{"y", "agg"}]. order가 "x"면 x 순, "value"면 첫 계열 값이 큰 순으로 최대 MAX_GROUPS개.
    split을 주면 그 컬럼의 값마다(행이 많은 순으로 최대 MAX_SPLITS개) 계열을 나눈다.
    (x 값들, [{"series": 계열 번호, "split": 나눈 값 또는 None, "values": x 값 순서의 값들}], 나눈 값이 더 있는지)를 돌려준다."""
    check_name(x, expr.columns)
    if not series:
        raise ValueError("계열을 하나 이상 넣으세요.")
    # 집계 식은 집계할 그 테이블 식에서 만들어야 한다(행 수는 다른 식에서 만든 것을 쓸 수 없다).
    metrics = lambda table: {f"v{i}": chart_metric(table, s.get("y"), s.get("agg")) for i, s in enumerate(series)}
    top = expr.group_by(x).aggregate(**metrics(expr))
    top = top.order_by([top.v0.desc(), top[x]] if order == "value" else [top[x]]).limit(MAX_GROUPS).to_polars()
    labels = top[x].to_list()
    if not split:
        return labels, [{"series": i, "split": None, "values": top[f"v{i}"].to_list()} for i in range(len(series))], False
    check_name(split, expr.columns)
    filled = expr.filter(expr[split].notnull())
    sizes = filled.group_by(split).aggregate(n=filled.count())
    parts = sizes.order_by([sizes.n.desc(), sizes[split]]).limit(MAX_SPLITS + 1).to_polars()[split].to_list()
    more, parts = len(parts) > MAX_SPLITS, parts[:MAX_SPLITS]
    known = [v for v in labels if v is not None]
    inside = expr.filter(expr[x].isin(known), expr[split].isin(parts))
    cells = inside.group_by([x, split]).aggregate(**metrics(inside)).to_polars()
    found = {(row[x], row[split]): row for row in cells.iter_rows(named=True)}
    return labels, [{"series": i, "split": str(part), "values": [found.get((label, part), {}).get(f"v{i}") for label in labels]}
                    for i in range(len(series)) for part in parts], more


def export(expr, path, guard=True):
    """조건에 맞는 전체 행을 파일로 저장한다. 형식은 확장자(.csv, .xlsx, .parquet)로 정한다.
    guard가 False면 수식 막기를 하지 않는다(다시 데이터로 읽을 파일)."""
    suffix = Path(path).suffix.lower()
    if suffix not in EXPORTS:
        raise ValueError(f"파일 이름은 {', '.join(EXPORTS)} 중 하나로 끝나야 합니다.")
    if guard and suffix != ".parquet":  # 수식으로 읽힐 글자로 시작하는 문자 값은 앞에 '를 붙여 스프레드시트가 실행하지 않게 한다.
        texts = [name for name, t in expr.schema().items() if t.is_string()]
        expr = expr.mutate(**{name: expr[name].re_search(r"^[=+\-@\t\r]").ifelse("'" + expr[name], expr[name]) for name in texts})
    EXPORTS[suffix](expr, path)
