"""요약 문장: 지표의 현재·직전 값, 그룹별 현재·직전 값, 목표를 받아 규칙으로 짧은 문장을 만든다(LLM·외부 호출 없음, 받은 숫자만 쓰고 지어내지 않는다).
문장 순서: ① 변화(compare.py의 문장) ② 기여 분해: 전체 변화와 부호가 같은 그룹 중 변화가 가장 큰 그룹의 몫(0 < 몫 ≤ 1일 때만) ③ 목표 대비.
숫자가 없어 만들 수 없는 문장은 빼고, 비율 값(percent)은 그룹 비율을 더할 수 없으므로 기여 분해를 하지 않는다."""
import compare

SHARE_MIN = 0.005  # 몫이 이보다 작으면(반올림하면 0%) 말하지 않는다


def pair(entry):
    """그룹 값 하나를 (현재, 직전)으로. (현재, 직전) 목록이나 {"cur", "prev"} 객체를 받는다. 읽을 수 없으면 (None, None)."""
    if isinstance(entry, dict):
        return compare.finite(entry.get("cur")), compare.finite(entry.get("prev"))
    if isinstance(entry, (list, tuple)) and len(entry) == 2:
        return compare.finite(entry[0]), compare.finite(entry[1])
    return None, None


def change_sentence(name, cur, prev, fmt, mode, label, better, partial):
    """① 변화 문장. 현재 값이 없으면 None."""
    if compare.finite(cur) is None:
        return None
    return f"{compare.particle(name, '은', '는')} " + compare.compare(cur, prev, fmt, mode, label, better, partial)["text"] + "."


def share_sentence(cur, prev, groups, fmt):
    """② 기여 분해 문장. 전체 변화와 부호가 같은 그룹 중 |변화|가 가장 큰 그룹의 몫이 0 초과 1 이하일 때만. 아니면 None."""
    cur, prev = compare.finite(cur), compare.finite(prev)
    if fmt == "percent" or cur is None or prev is None or not groups or cur == prev:
        return None
    total = cur - prev
    best = None
    for group, entry in groups.items():
        g_cur, g_prev = pair(entry)
        if g_cur is None or g_prev is None:
            continue
        delta = g_cur - g_prev
        if delta * total > 0 and (best is None or abs(delta) > abs(best[1])):
            best = (group, delta)
    if best is None:
        return None
    share = best[1] / total
    if not SHARE_MIN <= share <= 1:
        return None
    group = str(best[0])
    return f"{'늘어난' if total > 0 else '줄어든'} 몫의 {compare.trimmed(share * 100, 0)}%가 {group}{compare.ending(group, '이에요', '예요')}."


def target_sentence(cur, target, fmt):
    """③ 목표 대비 문장. 현재 값이나 목표가 숫자가 아니거나 목표가 0 이하면 None."""
    cur, target = compare.finite(cur), compare.finite(target)
    if cur is None or target is None or target <= 0:
        return None
    pct = cur / target * 100
    base = f"목표 {compare.format_value(target, fmt)} 대비 {compare.trimmed(pct, 1)}%"
    if pct > 100:
        return base + "로 목표를 넘었어요."
    return base + ("로 목표를 채웠어요." if pct == 100 else "예요.")


def summarize(name, cur, prev=None, fmt="number", mode="span", label="직전 기간", better="up", partial=False, groups=None, target=None):
    """지표 name의 요약 {text, sentences}. sentences는 위 ①②③ 중 만들 수 있는 문장 목록, text는 공백으로 이은 글.
    groups는 {그룹 이름: (현재, 직전)} 또는 {그룹 이름: {"cur", "prev"}}, target은 목표 숫자(없으면 None)."""
    found = [change_sentence(name, cur, prev, fmt, mode, label, better, partial), share_sentence(cur, prev, groups, fmt), target_sentence(cur, target, fmt)]
    sentences = [s for s in found if s]
    return {"text": " ".join(sentences), "sentences": sentences}
