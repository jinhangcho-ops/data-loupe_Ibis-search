"""결과 계산과 파일 저장. 이 단계에서 처음으로 연산이 실행된다."""
import math
from decimal import Decimal
from pathlib import Path

from lazy import ibis

from core import sql_funcs
from core.parse import check_name, column as field_of, struct_fields, type_info

SUMMARY_COLS = ["name", "type", "count", "nulls", "unique", "min", "max", "mean"]
MAX_GROUPS = 50  # 차트에 표시할 최대 그룹 수
MAX_SPLITS = 8  # 차트에서 한 컬럼의 값별로 나눌 때 최대 값 개수
MAX_VALUES = 200  # 조건을 만들 때 보여 줄 컬럼 값 목록의 최대 개수
MAX_POINTS = 5_000  # 산점도·버블의 점 수 상한(분석의 산점도와 대시보드가 같이 쓴다)
EXCEL_MAX_ROWS = 1_048_575  # Excel 시트 한 장의 행 수(제목 줄 제외)
CHUNK = 100_000  # 큰 결과를 한 번에 받는 행 수(저장·결과 받아 두기)


def json_text(df):
    """중첩 컬럼(구조·목록·맵)을 JSON 글자로 바꾼 Polars 표(CSV·Excel은 중첩 타입을 쓸 수 없다). LazyFrame도 된다."""
    import polars as pl
    nested = [name for name, dtype in df.collect_schema().items() if dtype.is_nested()]
    # {"v": 값}으로 감싸 JSON으로 만든 뒤 앞의 {"v": 와 끝의 }를 뗀다(목록·구조 모두 한 번에).
    return df.with_columns(pl.struct(pl.col(c).alias("v")).struct.json_encode().str.slice(5).str.head(-1).alias(c) for c in nested) if nested else df


def has_nested(expr):
    return any(t.is_nested() for t in expr.schema().values())


def lazy(expr):
    """파일 연결(Polars)이면 식의 LazyFrame, 아니면 None. Polars는 ibis의 묶음 받기·저장이 전체를 메모리에 올린 뒤 나눠서, LazyFrame의 스트리밍을 직접 쓴다."""
    backend = ibis.get_backend(expr)
    return backend.compile(expr) if backend.name == "polars" else None


def whole_fetch(backend):
    """Ibis의 묶음 받기가 결과 전체를 한 번에 받은 뒤 나누는 연결인지(그 연결의 to_pyarrow_batches가 _fetch_from_cursor로 다 읽는다.
    ibis 12에서 SQLite·MySQL·SingleStore). 이런 연결은 cursor_batches로 커서에서 묶음씩 받는다. DB 이름이 아니라 Ibis의 받는 방식으로 고른다."""
    import inspect
    method = inspect.unwrap(getattr(type(backend), "to_pyarrow_batches", None) or (lambda: None))
    code = getattr(method, "__code__", None)
    return code is not None and "_fetch_from_cursor" in code.co_names and all(
        hasattr(backend, name) for name in ("_safe_raw_sql", "_fetch_from_cursor", "compile"))


class Rows(list):
    """커서에서 받은 한 묶음의 행. 연결의 변환(_fetch_from_cursor)이 커서처럼 다룬다(돌기·fetchall·description)."""
    def __init__(self, rows, cursor):
        super().__init__(rows)
        self.description = cursor.description

    def fetchall(self):
        return list(self)


def cursor_batches(backend, expr, size):
    """커서에서 size행씩 받아(fetchmany) 묶음마다 그 연결의 Ibis 변환(타입 맞추기)을 거친 Arrow 묶음으로. 전체를 한 번에 올리지 않는다."""
    import pyarrow as pa
    schema = expr.as_table().schema()
    arrow = schema.to_pyarrow()
    getattr(backend, "_run_pre_execute_hooks", lambda e: None)(expr)  # memtable 등록 등 Ibis가 실행 전에 하는 일
    with backend._safe_raw_sql(backend.compile(expr)) as cursor:
        while rows := cursor.fetchmany(size):
            yield from pa.Table.from_pandas(backend._fetch_from_cursor(Rows(rows, cursor), schema), schema=arrow, preserve_index=False).to_batches(size)


def batches(expr, size=None):
    """식의 결과를 size(없으면 CHUNK)행 안팎의 Arrow 묶음(RecordBatch)으로 차례로 준다. 전체를 한 번에 메모리에 올리지 않는다.
    파일 연결은 LazyFrame에서, 결과 전체를 받아 버리는 연결(whole_fetch)은 커서에서 묶음씩. 커서 받기가 첫 묶음 전에 Ibis 내부가 달라
    안 되면(AttributeError·TypeError) Ibis의 묶음 받기로 돌아간다. DB의 오류는 그대로 낸다."""
    size = size or CHUNK
    frame = lazy(expr)
    if frame is not None:
        for df in frame.collect_batches(chunk_size=size):
            yield from df.to_arrow().to_batches()
        return
    backend = ibis.get_backend(expr)
    if whole_fetch(backend):
        found = cursor_batches(backend, expr, size)
        try:
            first = next(found, None)
        except (AttributeError, TypeError):
            found = first = None
        if found is not None:
            if first is not None:
                yield first
                yield from found
            return
    with expr.to_pyarrow_batches(chunk_size=size) as reader:
        yield from reader


def write_parquet(expr, path):
    """Parquet으로 저장. 파일 연결은 LazyFrame에서 흘려 쓰고, 결과 전체를 받아 버리는 연결은 묶음씩 받아 이어 쓴다."""
    frame = lazy(expr)
    if frame is not None:
        return frame.sink_parquet(path)
    if not whole_fetch(ibis.get_backend(expr)):
        return expr.to_parquet(path)
    import pyarrow.parquet as pq
    with pq.ParquetWriter(path, expr.schema().to_pyarrow()) as writer:
        for batch in batches(expr):
            writer.write_batch(batch)


def write_csv(expr, path):
    """CSV로 저장. 중첩 컬럼이 있으면 묶음(batch)마다 JSON 글자로 바꿔 이어 쓴다. 파일 연결은 LazyFrame에서 바로 흘려 쓴다."""
    frame = lazy(expr)
    if frame is not None:
        return json_text(frame).sink_csv(path)
    if not has_nested(expr) and not whole_fetch(ibis.get_backend(expr)):
        return expr.to_csv(path)
    if not has_nested(expr):  # 결과 전체를 받아 버리는 연결: Ibis의 to_csv와 같은 쓰기(pyarrow)를 묶음마다
        import pyarrow.csv as pcsv
        with pcsv.CSVWriter(path, expr.schema().to_pyarrow()) as writer:
            for batch in batches(expr):
                writer.write_batch(batch)
        return
    import polars as pl
    with open(path, "wb") as f:
        wrote = False
        for batch in batches(expr):
            json_text(pl.from_arrow(batch)).write_csv(f, include_header=not wrote)
            wrote = True
        if not wrote:  # 행이 없으면 머리글만
            f.write((",".join(expr.columns) + "\n").encode("utf-8"))


def write_excel(expr, path):
    """Excel로 저장. 묶음씩 받아 xlsxwriter의 메모리 절약 모드(constant_memory: 행을 바로 파일에 씀)로 쓴다.
    글자는 수식·숫자·주소로 해석하지 않는다(수식 막기는 export가 따로 한다). 날짜·날짜시간은 그 형식으로."""
    import polars as pl
    import xlsxwriter
    options = {"constant_memory": True, "strings_to_formulas": False, "strings_to_numbers": False, "strings_to_urls": False, "remove_timezone": True}
    try:
        with xlsxwriter.Workbook(str(path), options) as book:  # 행 수는 따로 세지 않고(원격 DB에 COUNT를 보내지 않는다) 쓰면서 상한을 넘는지 본다
            sheet = book.add_worksheet()
            bold = book.add_format({"bold": True})
            shapes = {pl.Date: book.add_format({"num_format": "yyyy-mm-dd"}), pl.Datetime: book.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"}),
                      pl.Time: book.add_format({"num_format": "hh:mm:ss"})}  # 날짜·시각 타입의 칸 형식
            sheet.write_row(0, 0, expr.columns, bold)
            row = 1
            for batch in batches(expr):
                frame = json_text(pl.from_arrow(batch))
                kinds = [shapes.get(t.base_type()) for t in frame.dtypes]
                for values in frame.iter_rows():
                    if row > EXCEL_MAX_ROWS:
                        raise ValueError(f"Excel은 {EXCEL_MAX_ROWS:,}행까지 저장할 수 있습니다. CSV나 Parquet으로 저장하세요.")
                    for col, value in enumerate(values):
                        value = number(value)  # NaN·무한은 빈 칸(xlsxwriter가 쓰지 못한다), Decimal은 실수로
                        if value is None:
                            continue
                        sheet.write(row, col, value, kinds[col]) if kinds[col] else sheet.write(row, col, value)
                    row += 1
    except BaseException:
        Path(path).unlink(missing_ok=True)  # 쓰다 만 파일은 남기지 않는다
        raise


# 확장자: 저장 함수
EXPORTS = {
    ".csv": write_csv,
    ".xlsx": write_excel,
    ".parquet": write_parquet,
}


def columns(expr):
    """[(컬럼, 한글 타입)]"""
    return [(name, type_info(dtype)[0]) for name, dtype in expr.schema().items()]


def fields(expr):
    """구조 컬럼 안의 필드 [(경로 user.name, 한글 타입)]. 조건·정렬·표시 컬럼에 컬럼처럼 쓴다."""
    return [(path, type_info(sub)[0]) for name, dtype in expr.schema().items() for path, sub in struct_fields(name, dtype)]


def get_rows(expr, limit, offset=0):
    """offset행을 건너뛰고 limit행."""
    return expr.limit(limit, offset=offset).to_polars()


def can_offset(expr):
    """expr의 연결이 LIMIT·OFFSET(건너뛰기)을 SQL로 쓸 수 있는지. 못 쓰는 DB(Oracle 등)는 Ibis가 SQL을 만들 때 막는다.
    SQL을 쓰지 않는 연결(Parquet·CSV 등 파일)은 쓸 수 있는 것으로 본다."""
    try:
        ibis.to_sql(expr.limit(1, offset=1))
    except ibis.common.exceptions.UnsupportedArgumentError:
        return False
    except Exception:
        pass
    return True


def get_sql(expr, limit=None, offset=0):
    """get_rows가 실행할 조회를 SQL 글로(limit이 None이면 행 수를 자르지 않은 식 그대로). 실행하지는 않는다.
    SQL을 쓰지 않는 연결(Parquet·CSV·JSON·Excel 파일)은 보여 줄 수 없다."""
    try:
        return str(ibis.to_sql(expr if limit is None else expr.limit(limit, offset=offset)))
    except NotImplementedError:
        raise ValueError("이 연결의 조회는 SQL로 보여 줄 수 없습니다.") from None


def get_count(expr, backend=None):
    """전체 행 수. backend를 주면 그 연결(같은 DB를 다른 스레드에서 읽는 연결, source.side)에서 센다."""
    return (backend.to_pyarrow(expr.count()) if backend else expr.count().to_pyarrow()).as_py()


def number(value):
    """숫자 값을 JSON으로 보낼 수 있게: NaN·무한은 빈 값(None), Decimal은 실수로. 그 밖의 값은 그대로."""
    if isinstance(value, Decimal):
        value = float(value)
    return None if isinstance(value, float) and not math.isfinite(value) else value


def plain(value):
    """JSON으로 보낼 수 있는 값. 숫자와 빈 값은 그대로(NaN·무한은 빈 값), Decimal은 실수로, 나머지(날짜 등)는 글자로."""
    value = number(value)
    return value if value is None or isinstance(value, (int, float)) else str(value)


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
    """컬럼(구조 안의 필드 user.name도)의 서로 다른 값(빈 값 제외)을 정렬해 limit개까지 문자로 돌려준다. 더 있으면 두 번째 값이 True."""
    col = field_of(expr, column)
    values = expr.filter(col.notnull()).select(col.name("v")).distinct().order_by("v").limit(limit + 1)
    values = [str(v) for v in values.to_polars()["v"].to_list()]
    return values[:limit], len(values) > limit


def chart_aggs():
    """차트 계열에서 고를 수 있는 집계 id들(함수 사전의 집계 전부 + 예전 이름 nunique). 화면이 이 목록으로 고른다."""
    return sql_funcs.ids_for("chart") + list(sql_funcs.ALIASES)


def chart_metric(expr, y, agg, opts=None):
    """계열 하나의 집계 식. count는 y 없이 행 수, sum·mean은 숫자 컬럼 y가 있어야 한다. 그 밖의 집계(중앙값·분위수·최빈값·표준편차·이어 붙이기 …)는
    함수 사전(sql_funcs)의 집계이고, opts는 그 집계의 나머지 인자(분위수 p, 구분자 sep …)다. 저장돼 있는 예전 이름(nunique)도 읽는다."""
    if agg == "count":
        return expr.count()
    if agg not in chart_aggs():
        raise ValueError(f"집계 방식 '{agg}'은 쓸 수 없습니다. count, sum, mean, min, max, median, quantile … 중에서 고르세요(함수 사전의 집계).")
    if agg in ("sum", "mean") and (y not in expr.columns or not expr[y].type().is_numeric()):
        raise ValueError("합계·평균은 숫자 컬럼을 Y로 골라야 합니다.")
    return sql_funcs.aggregate(agg, expr, y, None, opts)


def get_chart(expr, x, series, split=None, order="x"):
    """x별로 계열들을 집계한다. series는 [{"y", "agg"}]. order가 "x"면 x 순, "value"면 첫 계열 값이 큰 순으로 최대 MAX_GROUPS개.
    split을 주면 그 컬럼의 값마다(행이 많은 순으로 최대 MAX_SPLITS개) 계열을 나눈다.
    (x 값들, [{"series": 계열 번호, "split": 나눈 값 또는 None, "values": x 값 순서의 값들}], 나눈 값이 더 있는지)를 돌려준다."""
    check_name(x, expr.columns)
    if not series:
        raise ValueError("계열을 하나 이상 넣으세요.")
    # 집계 식은 집계할 그 테이블 식에서 만들어야 한다(행 수는 다른 식에서 만든 것을 쓸 수 없다).
    metrics = lambda table: {f"v{i}": chart_metric(table, s.get("y"), s.get("agg"), s.get("opts")) for i, s in enumerate(series)}
    top = expr.group_by(x).aggregate(**metrics(expr))
    top = top.order_by([top.v0.desc(), top[x]] if order == "value" else [top[x]]).limit(MAX_GROUPS).to_polars()
    labels = top[x].to_list()
    if not split:
        return labels, [{"series": i, "split": None, "values": [number(v) for v in top[f"v{i}"].to_list()]} for i in range(len(series))], False
    check_name(split, expr.columns)
    filled = expr.filter(expr[split].notnull())
    sizes = filled.group_by(split).aggregate(n=filled.count())
    parts = sizes.order_by([sizes.n.desc(), sizes[split]]).limit(MAX_SPLITS + 1).to_polars()[split].to_list()
    more, parts = len(parts) > MAX_SPLITS, parts[:MAX_SPLITS]
    known = [v for v in labels if v is not None]
    inside = expr.filter(expr[x].isin(known), expr[split].isin(parts))
    cells = inside.group_by([x, split]).aggregate(**metrics(inside)).to_polars()
    found = {(row[x], row[split]): row for row in cells.iter_rows(named=True)}
    return labels, [{"series": i, "split": str(part), "values": [number(found.get((label, part), {}).get(f"v{i}")) for label in labels]}
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
