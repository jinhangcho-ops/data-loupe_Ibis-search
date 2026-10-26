"""기간 직접 입력: 사람이 쓴 기간·날짜·주기 글자를 읽어 시작·끝·일수로 바꾼다(화면은 이 결과만 보여 주므로 규칙은 여기 한 곳에만 둔다).
기간 글자(앞뒤 공백 무시): `시작 ~ 끝`(한쪽이 비면 열린 범위), `YYYY-MM`, `YYYY-Qn`, `YYYY`, `최근 N(일|주|달|개월|년)`, 지난주·지난달·작년·어제·오늘·이번 주·이번 달·올해,
날짜 `YYYY-MM-DD`·`YYYY.MM.DD`·`YYYY/MM/DD`·`YYYYMMDD`(뒤에 시각 `HH:MM`을 붙일 수 있고, 시각만 쓰면 기준일의 그 시각이다).
- 결과 {start, end, days, label, error}: start·end는 "YYYY-MM-DD"(시각이 있으면 "YYYY-MM-DD HH:MM") 글자이고 end는 포함한다. 열린 쪽은 None, 일수도 None. 읽을 수 없으면 error에 한국어 문장이 들어가고 나머지는 비어 있다.
- 기준일(today): 최근 N일(어제까지 N일, 오늘은 빼고)·어제·지난주·이번 달 같은 상대 표현의 기준. 없으면 이 PC의 오늘. 대시보드의 `as_of`가 여기로 온다.
- 이번 주·이번 달·올해는 단위 시작(월요일 시작 주)부터 기준일까지다. 결과에 to_date(week·month·year)가 붙고 previous가 직전 단위의 같은 위치 기간(라벨 "지난달 같은 기간" 등)을 돌려준다. 지난주·지난달·작년·2026-10 같은 달력 입력은 단위 전체다."""
import calendar
import re
from datetime import date, datetime, timedelta

from core.compare import ending, particle

UNITS = {"일": "day", "주": "week", "달": "month", "개월": "month", "년": "year"}
EVERY_SECONDS = {"초": 1, "분": 60, "시간": 3600, "일": 86400, "주": 7 * 86400, "달": 30 * 86400, "개월": 30 * 86400, "년": 365 * 86400}
TO_DATE_BEFORE = {"week": "지난주 같은 기간", "month": "지난달 같은 기간", "year": "작년 같은 기간"}
WEEKDAYS = "월화수목금토일"
HINT = "예: 최근 7일, 2026-10, 2026-Q3, 2026-01-01 ~ 2026-03-31"

DATE = re.compile(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})|(\d{4})(\d{2})(\d{2})")
MONTH = re.compile(r"(\d{4})[-./](\d{1,2})")
QUARTER = re.compile(r"(\d{4})[-./ ]?[Qq]\s*(\d+)")
YEAR = re.compile(r"\d{4}")
CLOCK = re.compile(r"(\d{1,2}):(\d{2})")
RECENT = re.compile(r"최근\s*(\d+)\s*(\S*)")
EVERY = re.compile(r"(\d+(?:\.\d+)?)\s*(초|시간|분|일|주|달|개월|년)")


def as_date(value):
    """date·datetime·"YYYY-MM-DD" 글자를 date로. None이면 이 PC의 오늘."""
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def add_months(day, n):
    """day에서 n달 뒤(앞은 음수). 그 달에 없는 날이면 그 달의 마지막 날로 맞춘다(3-31의 한 달 전은 2-28 또는 2-29).
    달력 범위(1~9999년)를 벗어나면 한국어 ValueError."""
    index = day.year * 12 + day.month - 1 + n
    year, month = divmod(index, 12)
    if not 1 <= year <= 9999:
        raise ValueError("달력 범위(1~9999년)를 벗어나요. 기간을 1년 2월 이후로 잡아 주세요.")
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def month_end(year, month):
    return date(year, month, calendar.monthrange(year, month)[1])


def day_text(day, clock=None):
    return day.isoformat() + (f" {clock[0]:02d}:{clock[1]:02d}" if clock else "")


def failed(message):
    return {"start": None, "end": None, "days": None, "label": "", "error": message}


def side(text, today):
    """범위의 한쪽 글자를 읽어 {first, last, first_clock, last_clock, label}. 읽을 수 없으면 ValueError."""
    text = text.strip()
    time_only = CLOCK.fullmatch(text)
    if time_only:
        return clocked(today, today, time_only, text, text)
    for_date = re.fullmatch(r"(\S+)\s+(\d{1,2}:\d{2})", text)
    if for_date and DATE.fullmatch(for_date.group(1)):
        found = one_date(for_date.group(1))
        return clocked(found, found, CLOCK.fullmatch(for_date.group(2)), found.isoformat(), text)
    if DATE.fullmatch(text):
        found = one_date(text)
        return {"first": found, "last": found, "first_clock": None, "last_clock": None, "label": found.isoformat(), "unit": "day"}
    named = MONTH.fullmatch(text)
    if named:
        year, month = calendar_year(named.group(1), text), int(named.group(2))
        if not 1 <= month <= 12:
            raise ValueError(f"{particle(text, '은', '는')} 없는 달이에요. 월은 1~12만 쓸 수 있어요.")
        return whole(date(year, month, 1), month_end(year, month), f"{year}년 {month}월", "month")
    named = QUARTER.fullmatch(text)
    if named:
        year, quarter = calendar_year(named.group(1), text), int(named.group(2))
        if not 1 <= quarter <= 4:
            raise ValueError(f"{particle(text, '은', '는')} 없는 분기예요. 1~4분기만 있어요.")
        first = 3 * quarter - 2
        return whole(date(year, first, 1), month_end(year, first + 2), f"{year}년 {quarter}분기", "quarter")
    if YEAR.fullmatch(text):
        year = calendar_year(text, text)
        return whole(date(year, 1, 1), date(year, 12, 31), f"{year}년", "year")
    return relative(text, today)


def clocked(first, last, found, label, shown):
    """날짜(first~last)에 시각 HH:MM을 붙인 한쪽. 시각이 시계에 없으면 ValueError."""
    hour, minute = int(found.group(1)), int(found.group(2))
    if hour > 23 or minute > 59:
        raise ValueError(f"{particle(found.group(0), '은', '는')} 없는 시각이에요. 00:00~23:59로 써 주세요.")
    clock = (hour, minute)
    return {"first": first, "last": last, "first_clock": clock, "last_clock": clock, "label": shown, "unit": "time"}


def calendar_year(digits, text):
    """네 자리 연도 글자를 숫자로. 0년은 달력에 없어 ValueError(date가 영어 오류를 내기 전에)."""
    if int(digits) < 1:
        raise ValueError(f"{particle(text, '은', '는')} 없는 해예요. 연도는 1년부터 쓸 수 있어요.")
    return int(digits)


def whole(first, last, label, unit):
    return {"first": first, "last": last, "first_clock": None, "last_clock": None, "label": label, "unit": unit}


def one_date(text):
    """YYYY-MM-DD 계열 글자 하나를 date로. 없는 날짜(2026-13-01, 2023-02-29)면 ValueError."""
    found = DATE.fullmatch(text.strip())
    year, month, day = (int(n) for n in (found.group(1, 2, 3) if found.group(1) else found.group(4, 5, 6)))
    try:
        return date(year, month, day)
    except ValueError:
        raise ValueError(f"{particle(text.strip(), '은', '는')} 없는 날짜예요. 달력에 있는 날로 써 주세요.") from None


def relative(text, today):
    """최근 N단위와 지난주·이번 달 같은 말을 기준일(today) 기준의 한쪽으로. 모르는 글자면 ValueError."""
    compact = re.sub(r"\s+", " ", text)
    named = RECENT.fullmatch(compact)
    if named:
        count, unit = int(named.group(1)), UNITS.get(named.group(2))
        if unit is None:
            raise ValueError(f"‘{compact}’에서 단위를 알 수 없어요. 일·주·달(개월)·년 중 하나를 붙여 주세요. 예: 최근 3개월")
        if count < 1:
            raise ValueError(f"‘{compact}’{ending(compact, '은', '는')} 1 이상으로 써 주세요. 예: 최근 7일")
        try:
            last = today - timedelta(days=1)  # 최근 N단위는 어제까지(오늘은 아직 덜 찬 날이라 뺀다. 오늘을 보려면 '오늘'·'이번 주')
            first = {"day": lambda: last - timedelta(days=count - 1), "week": lambda: last - timedelta(days=7 * count - 1),
                     "month": lambda: add_months(last, -count) + timedelta(days=1), "year": lambda: add_months(last, -12 * count) + timedelta(days=1)}[unit]()
        except (OverflowError, ValueError):  # 달력 범위(1~9999년)를 벗어나는 큰 숫자
            raise ValueError(f"‘{compact}’{ending(compact, '은', '는')} 너무 먼 과거예요. 더 작은 숫자로 써 주세요. 예: 최근 3개월") from None
        return whole(first, last, f"최근 {count}{named.group(2)}", "recent")
    if compact == "오늘":
        return whole(today, today, "오늘", "day")
    if compact == "어제":
        return whole(today - timedelta(days=1), today - timedelta(days=1), "어제", "day")
    if compact in ("이번 주", "이번주"):
        return whole(today - timedelta(days=today.weekday()), today, "이번 주", "week_to_date")
    if compact == "지난주":
        monday = today - timedelta(days=today.weekday() + 7)
        return whole(monday, monday + timedelta(days=6), "지난주", "week")
    if compact in ("이번 달", "이번달"):
        return whole(today.replace(day=1), today, "이번 달", "month_to_date")
    if compact == "지난달":
        before = add_months(today.replace(day=1), -1)
        return whole(before, month_end(before.year, before.month), "지난달", "month")
    if compact == "올해":
        return whole(date(today.year, 1, 1), today, "올해", "year_to_date")
    if compact == "작년":
        return whole(date(today.year - 1, 1, 1), date(today.year - 1, 12, 31), "작년", "year")
    raise ValueError(f"‘{compact}’{ending(compact, '은', '는')} 읽을 수 없는 기간이에요. {HINT}")


def moment(day, clock, end):
    """비교용 시각. 시각이 없으면 시작은 0시, 끝은 그날 끝."""
    if clock:
        return datetime(day.year, day.month, day.day, clock[0], clock[1])
    return datetime(day.year, day.month, day.day, 23, 59) if end else datetime(day.year, day.month, day.day)


def parse(text, today=None):
    """기간 글자를 {start, end, days, label, error}로 읽는다. today는 기준일(없으면 이 PC의 오늘). 읽을 수 없으면 error에 문장을 담는다."""
    try:
        today = as_date(today)
    except ValueError:
        return failed("기준일을 읽을 수 없어요. 기준일은 YYYY-MM-DD 형식이어야 해요.")
    text = (text or "").strip()
    if not text:
        return failed(f"기간을 입력해 주세요. {HINT}")
    try:
        if "~" in text:
            return parse_range(text, today)
        found = side(text, today)
    except ValueError as e:
        return failed(str(e))
    start, end = day_text(found["first"], found["first_clock"]), day_text(found["last"], found["last_clock"])
    result = {"start": start, "end": end, "days": (found["last"] - found["first"]).days + 1, "label": found["label"], "error": None}
    if found["unit"].endswith("_to_date"):
        result["to_date"] = found["unit"][:-len("_to_date")]  # week·month·year: previous가 직전 단위의 같은 위치를 구한다
    return result


def parse_range(text, today):
    """`시작 ~ 끝`을 읽는다. 시작은 그 표현의 첫날, 끝은 그 표현의 마지막 날이다(2026-01 ~ 2026-03은 1-1부터 3-31까지)."""
    if text.count("~") > 1:
        raise ValueError("~는 한 번만 쓸 수 있어요. 예: 2026-01-01 ~ 2026-03-31")
    left, right = (part.strip() for part in text.split("~"))
    if not left and not right:
        raise ValueError("~ 앞이나 뒤에 시작이나 끝을 적어 주세요. 예: 2026-01-01 ~ (끝 없음), ~ 2026-03-31")
    start = side(left, today) if left else None
    end = side(right, today) if right else None
    start_text = day_text(start["first"], start["first_clock"]) if start else None
    end_text = day_text(end["last"], end["last_clock"]) if end else None
    if start and end and moment(start["first"], start["first_clock"], False) > moment(end["last"], end["last_clock"], True):
        raise ValueError(f"시작({start_text})이 끝({end_text})보다 뒤예요. 시작과 끝을 바꿔 보세요.")
    days = (end["last"] - start["first"]).days + 1 if start and end else None
    label = f"{start_text or ''} ~ {end_text or ''}".strip()
    return {"start": start_text, "end": end_text, "days": days, "label": label, "error": None}


def split_text(value):
    """parse가 돌려준 start·end 글자를 (date, (시, 분) 또는 None)으로."""
    day = date.fromisoformat(value[:10])
    clock = (int(value[11:13]), int(value[14:16])) if len(value) > 10 else None
    return day, clock


def previous(span):
    """span({start, end, ...})의 직전 같은 길이 기간. 달 단위로 꼭 맞는 기간(월·분기·연 포함)은 직전 같은 개월 수 전체, 그 밖의 날짜 기간은 바로 앞 같은 일수, 시각이 든 기간은 바로 앞 같은 시간이다.
    열린 범위·읽지 못한 기간, 직전 기간이 달력(1년~) 앞으로 넘어가는 기간(0001-01)은 error가 든 결과를 돌려준다."""
    try:
        return previous_span(span)
    except (ValueError, OverflowError):
        return failed("직전 기간이 1년 1월 1일보다 앞이라 구할 수 없어요.")


def previous_span(span):
    if not span or span.get("error") or not span.get("start") or not span.get("end"):
        return failed("끝이 열린 기간이나 읽지 못한 기간은 직전 기간을 구할 수 없어요.")
    (first, first_clock), (last, last_clock) = split_text(span["start"]), split_text(span["end"])
    if span.get("to_date") in TO_DATE_BEFORE:
        return previous_to_date(span["to_date"], first, last)
    if first_clock or last_clock:
        begin, finish = moment(first, first_clock, False), moment(last, last_clock, True)
        before_end = begin - timedelta(minutes=1)
        before_start = before_end - (finish - begin)
        start = day_text(before_start.date(), (before_start.hour, before_start.minute))  # 한쪽만 시각이어도 양쪽에 시각을 붙인다(날짜만 쓰면 그날 전체가 되어 겹치거나 빈다)
        end = day_text(before_end.date(), (before_end.hour, before_end.minute))
        return {"start": start, "end": end, "days": (before_end.date() - before_start.date()).days + 1, "label": f"{start} ~ {end}", "error": None}
    months = (last.year - first.year) * 12 + last.month - first.month + 1
    if first.day == 1 and last == month_end(last.year, last.month):
        before_start = add_months(first, -months)
        before_end = first - timedelta(days=1)
    else:
        length = (last - first).days + 1
        before_start, before_end = first - timedelta(days=length), first - timedelta(days=1)
    return {"start": before_start.isoformat(), "end": before_end.isoformat(), "days": (before_end - before_start).days + 1,
            "label": f"{before_start.isoformat()} ~ {before_end.isoformat()}", "error": None}


def previous_to_date(unit, first, last):
    """이번 주·달·해(단위 시작부터 기준일까지)의 직전 단위에서 같은 위치의 기간. 그 달에 없는 날은 달 끝으로 맞춘다(03-31 → 02-28)."""
    if unit == "week":
        start, end = first - timedelta(days=7), last - timedelta(days=7)
    elif unit == "month":
        start = add_months(first, -1)
        end = min(start + (last - first), month_end(start.year, start.month))
    else:
        start, end = date(first.year - 1, 1, 1), add_months(last, -12)
    return {"start": start.isoformat(), "end": end.isoformat(), "days": (end - start).days + 1, "label": TO_DATE_BEFORE[unit], "error": None}


def parse_day(text, today=None):
    """기준일 글자 하나를 {date, label, error}로 읽는다. YYYY-MM-DD·YYYY.MM.DD·YYYY/MM/DD·YYYYMMDD와 오늘·어제·그제·그저께·지난달 말·작년 말. 비면 오늘이다.
    today는 이 PC의 오늘(없으면 그대로 구한다). 오늘보다 뒤의 날은 막는다."""
    try:
        today = as_date(today)
    except ValueError:
        return {"date": None, "label": "", "error": "오늘 날짜를 읽을 수 없어요. YYYY-MM-DD 형식이어야 해요."}
    text = re.sub(r"\s+", " ", (text or "").strip())
    try:
        if not text or text == "오늘":
            found = today
        elif text == "어제":
            found = today - timedelta(days=1)
        elif text in ("그제", "그저께"):
            found = today - timedelta(days=2)
        elif text in ("지난달 말", "지난달말"):
            before = add_months(today.replace(day=1), -1)
            found = month_end(before.year, before.month)
        elif text in ("작년 말", "작년말"):
            found = date(today.year - 1, 12, 31)
        elif DATE.fullmatch(text):
            found = one_date(text)
        else:
            raise ValueError(f"‘{text}’{ending(text, '은', '는')} 날짜로 읽을 수 없어요. 예: 2026-10-01, 2026.10.01, 20261001, 어제, 지난달 말")
    except ValueError as e:
        return {"date": None, "label": "", "error": str(e)}
    if found > today:
        return {"date": None, "label": "", "error": f"아직 오지 않은 날이에요. 오늘({today.isoformat()})까지만 고를 수 있어요."}
    return {"date": found.isoformat(), "label": f"{found.isoformat()}({WEEKDAYS[found.weekday()]}) 기준", "error": None}


def parse_every(text):
    """주기 글자("30분", "2시간", "1주")를 초로. 단위는 초·분·시간·일·주·달·개월·년(달·개월은 30일, 년은 365일). 읽을 수 없으면 ValueError."""
    compact = (text or "").strip()
    found = EVERY.fullmatch(compact)
    if not found:
        raise ValueError(f"‘{compact}’{ending(compact, '은', '는')} 읽을 수 없는 주기예요. 숫자와 단위(초·분·시간·일·주·달·개월·년)로 써 주세요. 예: 30분, 2시간, 1주" if compact else "주기를 입력해 주세요. 예: 30분, 2시간, 1주")
    try:
        seconds = round(float(found.group(1)) * EVERY_SECONDS[found.group(2)])
    except OverflowError:  # 수백 자리 숫자는 float에서 무한대가 된다
        raise ValueError(f"‘{compact}’{ending(compact, '은', '는')} 너무 길어요. 더 작은 숫자로 써 주세요. 예: 30분, 2시간, 1주") from None
    if seconds < 1:
        raise ValueError(f"‘{compact}’{ending(compact, '은', '는')} 너무 짧아요. 1초 이상으로 써 주세요.")
    return seconds
