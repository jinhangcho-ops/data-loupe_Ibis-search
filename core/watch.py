"""모니터링 규칙(3-4): 엔티티(서버·장비·매장…)별 최근 행을 상태 규칙과 끊김 규칙으로 정상·주의·이상으로 나누고, 나빠진 지 얼마나 됐는지(since)와 영향받는 대상을 구한다.
순수 계산이다(DB·화면을 모른다). 상태·경고·배치도·상태 카드가 함께 쓴다.
- 상태 규칙 {column, op(>=, >, <=, <, =, !=), value, level(warn|bad)}: 엔티티의 최근 행에서 맞으면 그 level. 한 엔티티에 여러 규칙이 맞으면 가장 나쁜 것.
- 끊김 규칙: 지금 − 엔티티의 마지막 시각 > N분이면 bad(시각 컬럼이 있을 때).
- since: 나빠지기 시작한 시각을 카드 data에 엔티티별로 두고, 계속 나쁘면 그대로 두고 좋아지면 지운다. 경고 글은 "N분째".
- 영향받는 대상: 연결선(출발 → 도착)으로 한 단계 아래(BFS 한 단계)."""
import datetime as dt
import math
import operator

LEVELS = ("ok", "warn", "bad")
RANK = {"ok": 0, "warn": 1, "bad": 2}
OPS = {">=": operator.ge, ">": operator.gt, "<=": operator.le, "<": operator.lt, "=": operator.eq, "!=": operator.ne}
MAX_RULES = 20


def check_rules(rules):
    """상태 규칙 목록을 검사해 정리한다(ValueError: 몇 번째 규칙이 왜 틀렸는지)."""
    if rules is None:
        return []
    if not isinstance(rules, list) or len(rules) > MAX_RULES:
        raise ValueError(f"상태 규칙은 {MAX_RULES}개까지 쓸 수 있어요.")
    out = []
    for n, rule in enumerate(rules, 1):
        if not isinstance(rule, dict) or not isinstance(rule.get("column"), str) or not rule["column"].strip():
            raise ValueError(f"{n}번째 규칙에 컬럼 이름이 없어요.")
        if rule.get("op") not in OPS:
            raise ValueError(f"{n}번째 규칙의 비교({rule.get('op')})는 {', '.join(OPS)} 중 하나여야 해요.")
        if rule.get("level") not in ("warn", "bad"):
            raise ValueError(f"{n}번째 규칙의 단계는 warn(주의)이나 bad(이상)여야 해요.")
        if type(rule.get("value")) not in (int, float, str) or isinstance(rule.get("value"), bool):
            raise ValueError(f"{n}번째 규칙의 값은 숫자나 글자여야 해요.")
        out.append({"column": rule["column"].strip(), "op": rule["op"], "value": rule["value"], "level": rule["level"]})
    return out


def number(value):
    """숫자로 읽을 수 있으면 실수, 아니면 None(참거짓·빈 값·NaN 제외)."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        found = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(found) else found


def rule_hit(row, rule):
    """행이 규칙에 맞는지. 둘 다 숫자면 숫자로, 아니면 글자(대소문자 무시)로 =·!=만 비교한다. 값이 비어 있으면 맞지 않는다."""
    have = row.get(rule["column"])
    if have is None:
        return False
    a, b = number(have), number(rule["value"])
    if a is not None and b is not None:
        return OPS[rule["op"]](a, b)
    if rule["op"] in ("=", "!="):
        return OPS[rule["op"]](str(have).strip().casefold(), str(rule["value"]).strip().casefold())
    return False


def moment(value):
    """datetime·date·ISO 글자를 이 PC의 시각(시간대 없는 datetime)으로(못 읽으면 None). 시간대가 있는 값은 이 PC의 시간대로 바꾼 뒤 시간대를 뗀다."""
    if isinstance(value, dt.datetime):
        found = value
    elif isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time())
    else:
        try:
            found = dt.datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    return found.astimezone().replace(tzinfo=None) if found.tzinfo else found


def entity_level(row, rules, stale_minutes, now, time_column):
    """엔티티의 최근 행 → (level, 이유 목록). 상태 규칙과 끊김 규칙 중 가장 나쁜 것."""
    level, reasons = "ok", []
    for rule in rules:
        if rule_hit(row, rule):
            reasons.append(f"{rule['column']} {rule['op']} {rule['value']}")
            if RANK[rule["level"]] > RANK[level]:
                level = rule["level"]
    if stale_minutes and time_column:
        last = moment(row.get(time_column))
        if last is None or (now - last).total_seconds() > stale_minutes * 60:
            reasons.append(f"{stale_minutes}분 넘게 새 데이터가 없어요")
            level = "bad"
    return level, reasons


def since_text(since, now):
    """나빠진 시각 → 'N분째'(1분 미만은 '방금', 60분 넘으면 'N시간째', 24시간 넘으면 'N일째')."""
    start = moment(since)
    if start is None:
        return ""
    minutes = int((now - start).total_seconds() // 60)
    return "방금" if minutes < 1 else f"{minutes}분째" if minutes < 60 else f"{minutes // 60}시간째" if minutes < 1440 else f"{minutes // 1440}일째"


def affected(names, links):
    """names에서 연결선(출발, 도착)을 한 단계 따라간 도착 대상들 {이름: [도착…]}(이미 names에 든 것은 뺌)."""
    out = {}
    for a, b in links:
        if a in names and b not in names:
            out.setdefault(a, [])
            if b not in out[a]:
                out[a].append(b)
    return out


def freshness(last, stale_minutes, now):
    """데이터 신선도 문장: '마지막 데이터 09:41 · 2분 전', 기준을 넘으면 '30분째 새 데이터가 없어요'. 시각이 없으면 빈 글자."""
    if last is None:
        return ""
    minutes = int((now - last).total_seconds() // 60)
    ago = "방금" if minutes < 1 else f"{minutes}분 전" if minutes < 60 else f"{minutes // 60}시간 전"
    if stale_minutes and (now - last).total_seconds() > stale_minutes * 60:  # 엔티티 상태(evaluate)와 같게 초로 견준다
        return f"{minutes}분째 새 데이터가 없어요 (마지막 {last:%H:%M})"
    return f"마지막 데이터 {last:%H:%M} · {ago}"


def evaluate(rows, rules, entity_column, now, time_column=None, stale_minutes=None, since=None, links=(), kind_column=None):
    """엔티티별 상태. rows는 [{컬럼: 값}](엔티티가 여러 행이면 시각이 가장 늦은 행, 시각이 없으면 마지막 행).
    {entities: [{name, level, reasons, last, since, since_text, kind, impact}], counts, since(다음에 저장할 값), fresh(문장)}."""
    rules = check_rules(rules)
    since = since if isinstance(since, dict) else {}
    latest = {}
    for row in rows:
        name = row.get(entity_column)
        if name is None:
            continue
        name, at = str(name), moment(row.get(time_column)) if time_column else None
        old = latest.get(name)
        if old is None or old[1] is None or (at is not None and at >= old[1]):  # 시각이 빈 행은 시각이 있는 행을 가리지 않는다(시각 컬럼이 없으면 마지막 행)
            latest[name] = (row, at)
    entities, kept = [], {}
    for name, (row, at) in sorted(latest.items()):
        level, reasons = entity_level(row, rules, stale_minutes, now, time_column)
        if level != "ok":
            kept[name] = since.get(name) or now.isoformat(timespec="seconds")  # 계속 나쁘면 처음 나빠진 시각을 그대로
        entities.append({"name": name, "level": level, "reasons": reasons, "last": at.isoformat(timespec="seconds") if at else "", "since": kept.get(name, ""),
                         "since_text": since_text(kept[name], now) if name in kept else "", "kind": str(row.get(kind_column)) if kind_column and row.get(kind_column) is not None else ""})
    impact = affected(set(kept), [tuple(link) for link in links])
    for entity in entities:
        entity["impact"] = impact.get(entity["name"], [])
    counts = {level: sum(1 for e in entities if e["level"] == level) for level in LEVELS}
    last = max((moment(e["last"]) for e in entities if e["last"]), default=None)
    return {"entities": entities, "counts": counts, "since": kept, "fresh": freshness(last, stale_minutes, now)}
