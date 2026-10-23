"""비교 문장: 지표의 현재 값과 직전 값을 "직전 7일보다 12.3% 늘었어요 (+1,234)" 같은 문장과 방향·좋고 나쁨(tone)으로 바꾼다. 값 모양(숫자·비율·통화) 글자 만들기와 받침에 맞는 조사도 여기서 한다.
- span: 현재 기간 값과 직전 기간 값, point: 끝 날짜의 값과 끝 − 길이의 값. 문장은 같고, 일부만 있다는 덧말만 다르다.
- 비율 값(format percent)은 0~1 비율이고 화면에 ×100 %로 보인다. 변화는 %p로 쓴다.
- 직전 값이 없거나 0이면 비율은 구하지 않는다(비율 값은 0도 값이라 %p로 쓴다). 숫자가 없으면 지어내지 않는다."""
import math
from decimal import ROUND_HALF_UP, Context, Decimal

from core import settings

FORMATS = ("number", "percent", "currency")
MODES = ("span", "point")
PARTIAL_NOTES = {"span": "직전 기간 일부만 있음", "point": "비교 시점 값이 일부만 있음"}
DIGIT_READING = "영일이삼사오육칠팔구"


def reading(word):
    """받침 판단용으로 word의 끝 글자를 읽는 소리로 바꾼 글자. 숫자는 영·일·이…, %는 퍼센트, 영문 L·M·N·R은 엘·엠·엔·알(받침 있음), 나머지 영문은 받침 없음."""
    last = word[-1:]
    if last.isdigit() and last in "0123456789":
        return word[:-1] + DIGIT_READING[int(last)]
    if last in "LMNRlmnr":
        return word[:-1] + "알"
    return word[:-1] + "퍼센트" if last == "%" else word


def particle(word, with_final, without_final):
    """settings.josa와 같되, 끝이 숫자·%여도 읽는 소리로 받침을 따진다(2026-01-01은, 10일이)."""
    return word + ending(word, with_final, without_final)


def ending(word, with_final, without_final):
    """word 뒤에 붙일 조사만."""
    spoken = reading(word)
    return settings.josa(spoken, with_final, without_final)[len(spoken):]


def finite(value):
    """숫자(bool 제외)이고 유한하면 float, 아니면 None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def trimmed(value, digits):
    """digits 자리까지 반올림해 끝의 0을 뗀 글자(천 단위 쉼표 포함). -0은 0."""
    text = f"{Decimal(repr(value)).quantize(Decimal(1).scaleb(-digits), ROUND_HALF_UP, context=Context(prec=400)):,.{digits}f}"  # 5를 올리는 반올림(2.5 → 3). 정밀도 400은 float 최댓값(309자리)+소수 자리까지 담는다
    text = text.rstrip("0").rstrip(".") if "." in text else text
    return "0" if text in ("-0", "") else text


def format_value(value, fmt="number"):
    """값 하나를 글자로. number는 1,234.5, currency는 ₩1,234, percent는 비율을 ×100 한 12.3%. 숫자가 아니면 빈 글자."""
    value = finite(value)
    if value is None:
        return ""
    if fmt == "percent":
        return trimmed(value * 100, 2) + "%"
    if fmt == "currency":
        shown = trimmed(abs(value), 0)
        return ("-" if value < 0 and shown != "0" else "") + "₩" + shown
    return trimmed(value, 2)


def format_delta(delta, fmt="number"):
    """변화량을 부호를 붙인 글자로(+1,234 / -₩500 / +1.5%p). 0은 부호 없이."""
    delta = finite(delta)
    if delta is None:
        return ""
    if fmt == "percent":
        body, tail = trimmed(abs(delta) * 100, 2), "%p"
    elif fmt == "currency":
        body, tail = "₩" + trimmed(abs(delta), 0), ""
    else:
        body, tail = trimmed(abs(delta), 2), ""
    if body.lstrip("₩") == "0":
        return body + tail
    return ("+" if delta > 0 else "-") + body + tail


def percent_change(cur, prev):
    """(cur − prev) / |prev| × 100. prev가 0이면 None."""
    return None if prev == 0 else (cur - prev) / abs(prev) * 100


def percent_text(pct):
    """변화율 글자. 1% 미만은 소수 둘째 자리까지."""
    return trimmed(abs(pct), 2 if abs(pct) < 1 else 1) + "%"


def tone_of(direction, better):
    """방향과 더 나은 쪽(up·down)으로 good·bad·neutral."""
    if direction == "flat" or better not in ("up", "down"):
        return "neutral"
    return "good" if direction == better else "bad"


def compare(cur, prev, fmt="number", mode="span", label="직전 기간", better="up", partial=False):
    """현재 값 cur와 직전 값 prev(없으면 None)를 비교해 {text, delta_text, pct, direction, tone}을 돌려준다.
    label은 비교 대상 이름("직전 7일", "7일 전"), better는 오르는 쪽이 좋으면 "up", 내리는 쪽이 좋으면 "down"(그 밖은 좋고 나쁨 없음).
    pct는 변화율(%) 숫자이고 직전 값이 없거나 0이면 None. direction은 up·down·flat."""
    if fmt not in FORMATS:
        raise ValueError(f"값 모양은 {', '.join(FORMATS)} 중 하나여야 합니다.")
    if mode not in MODES:
        raise ValueError(f"비교 방식은 {', '.join(MODES)} 중 하나여야 합니다.")
    cur, prev = finite(cur), finite(prev)
    note = f" ({PARTIAL_NOTES[mode]})" if partial else ""
    if cur is None:
        return {"text": f"이번 값이 없어 {particle(label, '과', '와')} 비교할 수 없어요" + note, "delta_text": "", "pct": None, "direction": "flat", "tone": "neutral"}
    ratio = fmt == "percent"
    base = 0.0 if prev is None else prev  # 없는 직전은 0에서 늘어난 것으로 센다
    delta = cur - base
    flat = math.isclose(cur, base, rel_tol=1e-12, abs_tol=1e-12)
    direction = "flat" if flat else "up" if delta > 0 else "down"
    delta_text = format_delta(0 if flat else delta, fmt)
    tone = tone_of(direction, better)
    pct = None if prev is None else percent_change(cur, prev)
    if prev is None or (prev == 0 and not ratio and not flat):
        text = f"{label}에는 값이 없었어요 ({delta_text})"
    elif flat:
        text = f"{particle(label, '과', '와')} 같아요"
    elif ratio:
        text = f"{label}보다 {delta_text.lstrip('+-')} {'늘었어요' if delta > 0 else '줄었어요'} ({format_value(prev, fmt)} → {format_value(cur, fmt)})"
    else:
        text = f"{label}보다 {percent_text(pct)} {'늘었어요' if delta > 0 else '줄었어요'} ({delta_text})"
    return {"text": text + note, "delta_text": delta_text, "pct": pct, "direction": direction, "tone": tone}
