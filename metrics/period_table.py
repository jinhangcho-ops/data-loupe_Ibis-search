"""기간별 표(R5 P3): 서로 다른 테이블(또는 한 테이블의 집계·종류 컬럼)의 건수·고유 수·합계를 기간 단위(일·주·월·분기·연)로 묶어 한 표로 나란히 놓는다.
spec = {tables: [{schema?, base, date, label?, how: count|count_distinct|sum, column?, split?}], unit, start?, end?}.
테이블마다 `group_by(기간).aggregate` 한 번만 DB에 보내고, 기간 키로 파이썬에서 합친다(빈 기간은 0). 날짜는 설정 timezone·week_start·fiscal_start_month를 따른다(sql_funcs.period_start).
응답 {columns: ["기간", 열 이름…], rows: [[기간 글자, 값…]], ratios: {share: {열: [비중 %…]}, change: {열: [전 기간 대비 %…]}}}.
split이 있으면 그 컬럼의 값마다 열("라벨 · 값")이 생기고, 빈 값은 "(빈 값)" 열이다."""
import datetime as dt

from core import parse
from core import querying
from core import result
from core import sql_funcs
from dashboard import period

MAX_TABLES = 8  # 한 번에 나란히 놓을 테이블(집계) 수
MAX_BUCKETS = 600  # 기간 칸 수 상한
MAX_SPLITS = 20  # split으로 나눌 값 수 상한
UNITS = ("day", "week", "month", "quarter", "year")
HOWS = {"count": "개수", "count_distinct": "고유 개수", "sum": "합계"}
HEAD = "기간"  # 첫 열 이름
EMPTY = "(빈 값)"  # split 컬럼이 빈 값인 열
TOO_MANY = "기간을 줄이거나 단위를 키우세요."
TOTAL = "합계"  # (Phase 8) 총계 줄의 첫 칸


def check(spec):
    """요청 spec을 검사해 정리한 사본({tables, unit, start, end, total}, 테이블마다 label이 채워짐). 잘못이면 한국어 ValueError."""
    if not isinstance(spec, dict):
        raise ValueError("요청이 올바르지 않아요.")
    unit = spec.get("unit") or "month"
    if unit not in UNITS:
        raise ValueError(f"단위는 {', '.join(UNITS)} 중 하나예요.")
    tables = spec.get("tables")
    if not isinstance(tables, list) or not tables:
        raise ValueError("테이블을 하나 이상 넣으세요.")
    if len(tables) > MAX_TABLES:
        raise ValueError(f"테이블은 {MAX_TABLES}개까지 넣을 수 있어요.")
    kept = []
    for t in tables:
        if not isinstance(t, dict) or not t.get("base") or not t.get("date"):
            raise ValueError("테이블마다 테이블 이름(base)과 날짜 컬럼(date)을 넣으세요.")
        how = t.get("how", "count")
        if how not in HOWS:
            raise ValueError(f"집계는 {', '.join(HOWS)} 중 하나예요.")
        if how != "count" and not t.get("column"):
            raise ValueError(f"‘{HOWS[how]}’는 집계할 컬럼을 골라야 해요.")
        kept.append({**t, "how": how, "label": t.get("label") or t["base"]})
    start, end = (spec.get(k) or None for k in ("start", "end"))
    if any(v is not None and not isinstance(v, str) for v in (start, end)):
        raise ValueError("start·end는 YYYY-MM-DD 모양의 글자여야 해요.")
    total = spec.get("total") or False
    if not isinstance(total, bool):
        raise ValueError("total은 참·거짓이에요.")
    return {"tables": kept, "unit": unit, "start": start, "end": end, "total": total}


def grouped(spec, t, by_period: bool = True):
    """테이블 t를 기간(과 split 값)별로 집계한 Ibis 식: 컬럼 p(기간 시작), s(split 값, 있을 때만), v(값). 날짜가 빈 행은 뺀다. 처리 용량 확인이 이 식의 SQL을 센다."""
    expr = querying.joined({"schema": t.get("schema") or None, "base": t["base"]})[0]
    dates = [c for c, kind in expr.schema().items() if kind.is_date() or kind.is_timestamp()]
    parse.check_name(t["date"], dates, "날짜 컬럼")
    preds = [expr[t["date"]].notnull()]
    if spec["start"] or spec["end"]:
        preds += parse.parse_period(f"{t['date']} {spec['start'] or ''} ~ {spec['end'] or ''}", expr)  # 검색 화면의 기간 줄과 같은 규칙(날짜시각 컬럼의 끝 날짜는 그날 끝까지)
    expr = expr.filter(preds)
    if t["how"] == "count":
        value = expr.count()
    else:
        if t["how"] == "sum" and not parse.column(expr, t["column"]).type().is_numeric():
            raise ValueError(f"‘{t['column']}’은 숫자 컬럼이 아니라 합계를 구할 수 없어요.")
        value = sql_funcs.aggregate(t["how"], expr, t["column"])
    keys = {}
    if by_period:
        keys["p"] = sql_funcs.period_start(result.local_time(expr[t["date"]]), sql_funcs.UNITS[spec["unit"]])
    if t.get("split"):
        keys["s"] = parse.column(expr, t["split"])
    return expr.group_by(**keys).aggregate(v=value) if keys else expr.aggregate(v=value)


def day_of(value):
    """기간 시작 값(날짜 또는 날짜시각)의 날짜."""
    return value.date() if isinstance(value, dt.datetime) else value


def fetch(spec, t):
    """테이블 t의 ({열 이름: {기간 날짜: 값}} (split이면 값마다 열), 기간 날짜 집합). 기간 칸·나눌 값이 상한을 넘으면 ValueError."""
    table = grouped(spec, t)
    found = table.limit(MAX_BUCKETS * MAX_SPLITS + 1).to_polars()  # 이보다 많으면 기간이 600칸을 넘거나 나눌 값이 20개를 넘는다
    if found.height > MAX_BUCKETS * MAX_SPLITS:
        raise ValueError(f"‘{t['label']}’의 기간 칸이 너무 많아요. {TOO_MANY}")
    split = t.get("split")
    parts = sorted({r["s"] for r in found.iter_rows(named=True)} if split else [None], key=lambda v: (v is None, str(v)))
    if len(parts) > MAX_SPLITS:
        raise ValueError(f"‘{split}’의 값이 {MAX_SPLITS}개를 넘어 열로 나눌 수 없어요. 값이 적은 컬럼을 고르세요.")
    names = {part: column_name(t, part) for part in parts}
    cells = {name: {} for name in names.values()}
    for r in found.iter_rows(named=True):
        cells[names[r["s"] if split else None]][day_of(r["p"])] = result.number(r["v"]) or 0
    return cells


def step(day, unit):
    """기간 시작 day 다음 기간의 시작."""
    return day + dt.timedelta(days=1 if unit == "day" else 7) if unit in ("day", "week") else period.add_months(day, {"month": 1, "quarter": 3, "year": 12}[unit])


def label_of(day, unit):
    """기간 칸의 글자: 월 2026-03, 분기 2026-Q1, 연 2026(회계연도가 1월 시작이 아니면 시작일), 일·주는 시작일."""
    if unit == "month":
        return f"{day:%Y-%m}"
    if unit == "quarter":
        return f"{day.year}-Q{(day.month - 1) // 3 + 1}"
    return str(day.year) if unit == "year" and (day.month, day.day) == (1, 1) else day.isoformat()


def axis_of(first, last, unit):
    """first~last를 단위마다 채운 기간 시작 목록(칸이 MAX_BUCKETS를 넘으면 ValueError)."""
    days, day = [], first
    while day <= last:
        days.append(day)
        if len(days) > MAX_BUCKETS:
            raise ValueError(f"기간 칸이 {MAX_BUCKETS}개를 넘어요. {TOO_MANY}")
        day = step(day, unit)
    return days


def ratios_of(names, rows, wholes: dict[str, int | float | None] | None = None):
    """열마다 비중 %(열 합 대비. 고유 개수 열은 wholes의 전체 기간 다시 센 값 대비)와 전 기간 대비 %(이전이 0이거나 첫 칸이면 빈 값)."""
    share, change = {}, {}
    for j, name in enumerate(names, start=1):
        column = [r[j] for r in rows]
        total = wholes[name] if wholes and name in wholes else sum(column)
        share[name] = [round(v / total * 100, 2) if total else None for v in column]
        change[name] = [round((v - before) / abs(before) * 100, 2) if before else None for before, v in zip([None, *column], column)]
    return {"share": share, "change": change}


def column_name(t: dict, part: object) -> str:
    """[SPECIFICATION] plans/phase-8.md#column_name
    테이블 줄 t와 split 값 part(split이 없으면 None)의 표 열 이름."""
    if not t.get("split"):
        return t["label"]
    return f"{t['label']} · {EMPTY if part is None else part}"


def whole(spec: dict, t: dict) -> dict[str, int | float | None]:
    """[SPECIFICATION] plans/phase-8.md#whole
    고유 개수 테이블 줄 t를 기간으로 나누지 않고 전체 기간에서 다시 세어 {열 이름: 고유 개수}로 돌려준다(DB에 한 번)."""
    if t["how"] != "count_distinct":
        raise ValueError("전체 기간 다시 세기는 고유 개수에만 해요.")
    split = t.get("split")
    found = grouped(spec, t, by_period=False).limit(MAX_SPLITS + 1).to_polars()
    if found.height > MAX_SPLITS:
        raise ValueError(f"‘{split}’의 값이 {MAX_SPLITS}개를 넘어 열로 나눌 수 없어요. 값이 적은 컬럼을 고르세요.")
    return {column_name(t, r["s"] if split else None): result.number(r["v"]) for r in found.iter_rows(named=True)}


def build(spec):
    """기간별 표 {columns, rows, ratios, total(spec.total일 때만)}. 연결 스레드에서 부른다(server.run). 테이블마다 DB에 한 번, 고유 개수 줄은 전체 기간을 다시 세느라 한 번 더 묻는다."""
    spec = check(spec)
    cells, wholes = {}, {}
    for t in spec["tables"]:
        if t["how"] == "count_distinct":
            wholes.update(whole(spec, t))
        for name, column in fetch(spec, t).items():
            if name in cells or name == HEAD:
                raise ValueError(f"열 이름 ‘{name}’이 겹쳐요. 라벨을 서로 다르게 정하세요.")
            cells[name] = column
    names = list(cells)
    days = {d for column in cells.values() for d in column}
    axis = axis_of(min(days), max(days), spec["unit"]) if days else []
    rows = [[label_of(d, spec["unit"]), *(cells[name].get(d, 0) for name in names)] for d in axis]
    found = {"columns": [HEAD, *names], "rows": rows, "ratios": ratios_of(names, rows, wholes)}
    if spec["total"]:
        found["total"] = [TOTAL, *(wholes[name] if name in wholes else sum(r[j] for r in rows) for j, name in enumerate(names, start=1))]
    return found
