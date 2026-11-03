"""지표 기간·비교(R5 P13): POST /api/metrics/run이 period({start, end, column?})와 compare(prev·same_weekday·yoy·mom·wow)를 받을 때의 계산.
기간 조건은 검색 body의 periods에 줄("컬럼 시작 ~ 끝")로 걸고(같은 컬럼의 옛 기간 줄은 대신한다), 비교는 비교 기간으로 같은 지표를 한 번 더 구해
지표마다 [{value, previous, delta, delta_pct}](나눈 값 순서)로 돌려준다. delta_pct는 (값 - 이전) / |이전| × 100이고 이전이 0이거나 없으면 빈 값이다.
비교 기간은 dashboard.period.previous(prev·same_weekday·yoy)를 쓰고, mom(한 달 전)·wow(한 주 전)는 시작·끝을 각각 옮겨 여기서 구한다."""
import datetime as dt
import re

from core import metric_file
from core import parse
from core import querying
from core.server import run
from dashboard import period

MODES = ("prev", "same_weekday", "yoy", "mom", "wow")
SHIFTED = {"mom": "한 달 전 같은 기간", "wow": "한 주 전 같은 기간"}  # period.previous에 없는 방식과 그 이름
DAY = re.compile(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?")


def read_period(raw):
    """요청의 period를 (시작, 끝, 컬럼 또는 None) 글자로. 둘 중 하나는 비어도 되지만 둘 다 비면, 형식이 틀리면, 시작이 끝보다 뒤면 ValueError."""
    if not isinstance(raw, dict):
        raise ValueError('기간은 {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} 모양으로 보내세요.')
    start, end = (raw.get(k) or None for k in ("start", "end"))
    if any(v is not None and not (isinstance(v, str) and DAY.fullmatch(v)) for v in (start, end)):
        raise ValueError("기간의 시작과 끝은 YYYY-MM-DD 모양이어야 해요(시각은 YYYY-MM-DD HH:MM).")
    if not (start or end):
        raise ValueError("기간의 시작이나 끝을 넣으세요.")
    if start and end and start > end:
        raise ValueError(f"시작({start})이 끝({end})보다 뒤예요. 시작과 끝을 바꿔 보세요.")
    return start, end, raw.get("column") or None


def date_column(body, name):
    """기간을 걸 날짜 컬럼: 이름을 주면 그 컬럼(날짜가 아니면 ValueError), 안 주면 검색 식의 날짜 컬럼이 하나일 때 그 컬럼."""
    expr = querying.joined(body)[0]
    dates = [c for c, kind in expr.schema().items() if kind.is_date() or kind.is_timestamp()]
    if name:
        parse.check_name(name, dates, "날짜 컬럼")
        return name
    if len(dates) == 1:
        return dates[0]
    raise ValueError("날짜 컬럼이 없어요." if not dates else f"날짜 컬럼이 여러 개예요({', '.join(dates)}). period.column에 기간을 걸 컬럼을 적어 주세요.")


def with_period(body, column, start, end):
    """body에 기간 줄을 건 사본. 같은 컬럼의 기존 기간 줄은 빼고(비교 기간과 겹쳐 비는 것을 막는다) 다른 컬럼의 기간 줄은 그대로 둔다."""
    kept = [line for line in body.get("periods") or [] if not (isinstance(line, str) and line.startswith(column + " "))]
    return {**body, "periods": [*kept, f"{column} {start or ''} ~ {end or ''}"]}


def previous_span(span, mode):
    """span({start, end})과 견줄 기간 {start, end, label}. 끝이 열린 기간이거나 모르는 방식이면 ValueError. mom은 시작·끝을 각각 한 달 앞으로(없는 날은 달 끝, 기간이 달 단위면 끝은 그 달 끝), wow는 7일 앞으로."""
    if mode not in MODES:
        raise ValueError(f"비교는 {', '.join(MODES)} 중 하나예요.")
    if not (span.get("start") and span.get("end")):
        raise ValueError("비교하려면 기간의 시작과 끝이 모두 있어야 해요.")
    if mode not in SHIFTED:
        found = period.previous(span, mode)
        if found["error"]:
            raise ValueError(found["error"])
        return found
    (first, first_clock), (last, last_clock) = period.split_text(span["start"]), period.split_text(span["end"])
    shift = (lambda d: period.add_months(d, -1)) if mode == "mom" else (lambda d: d - dt.timedelta(days=7))  # mom은 시작·끝을 각각 한 달 앞으로(없는 날은 달 끝), wow는 7일 앞으로
    end = shift(last)
    if mode == "mom" and not (first_clock or last_clock) and period.month_count(first, last):  # 달 단위 기간이면 지난달도 달 끝까지(2-01~2-28 → 1-01~1-31, yoy와 같은 규칙)
        end = period.month_end(end.year, end.month)
    return {"start": period.day_text(shift(first), first_clock), "end": period.day_text(end, last_clock), "label": SHIFTED[mode], "error": None}


def previous_values(body, names, by, ask):
    """querying.metric_values와 같되 사용 횟수를 세지 않는다(비교로 두 번 구해도 지표는 한 번만 센 것으로 둔다). (나눈 값들 또는 None, {이름: 값들})."""
    found = metric_file.registry()
    parts = [p for n in names if n in found for p in (found[n]["numerator"], found[n]["denominator"]) if p]
    expr = querying.runnable(body, select=False, extra=[(p["agg"], p["column"], p.get("opts")) for p in parts], ask=ask)
    labels, values, _ = run(lambda: metric_file.compute(expr, names, found, by or None))
    return labels, values


def change(value, previous):
    """{value, previous, delta, delta_pct}."""
    delta = None if value is None or previous is None else value - previous
    return {"value": value, "previous": previous, "delta": delta, "delta_pct": None if delta is None or not previous else delta / abs(previous) * 100}


def compute(body, names, by, ask, raw_period, compare):
    """기간(과 비교)을 건 지표 값. 응답은 /api/metrics/run의 기존 꼴(labels·values·formats·more·max_groups)에 period {column, start, end}를 더하고,
    compare를 주면 compare {mode, start, end, label}와 changes {지표: [{value, previous, delta, delta_pct}…]}(나눈 값 labels 순서, 나누지 않으면 하나)를 더한다."""
    if compare and compare not in MODES:
        raise ValueError(f"비교는 {', '.join(MODES)} 중 하나예요.")
    if compare and not raw_period:
        raise ValueError("비교하려면 기간(period)을 함께 보내세요.")
    start, end, name = read_period(raw_period)
    before = previous_span({"start": start, "end": end}, compare) if compare else None
    column = date_column(body, name)
    found, labels, values, more = querying.metric_values(with_period(body, column, start, end), names, by, ask=ask)
    out = {"labels": labels, "values": values, "formats": {n: found[n]["format"] for n in names}, "more": more, "max_groups": metric_file.MAX_GROUPS,
           "period": {"column": column, "start": start, "end": end}}
    if not compare:
        return out
    before_labels, before_values = previous_values(with_period(body, column, before["start"], before["end"]), names, by, ask)
    keys = labels if labels is not None else [None]
    old = {n: dict(zip(before_labels if before_labels is not None else [None], before_values[n])) for n in names}
    return {**out, "compare": {"mode": compare, "start": before["start"], "end": before["end"], "label": before["label"]},
            "changes": {n: [change(v, old[n].get(k)) for k, v in zip(keys, values[n])] for n in names}}
