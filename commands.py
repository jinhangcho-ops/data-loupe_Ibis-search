"""채팅 명령(코어): 다른 앱(Slack 봇 등)에서 온 글 한 줄을 읽어 답할 글을 만든다. 어느 앱에서 왔는지 모른다.
명령(앞의 낱말, 값에 빈칸이 있으면 따옴표로 감싼다):
  도움                                          명령 안내
  지표                                          지표 목록(많이 쓴 순)
  지표 <이름> 검색=<저장한 검색> [나누기=<컬럼>]   저장한 검색 위에서 지표 값(나누면 값마다 표)
  검색                                          저장한 검색 목록(많이 쓴 순)
  검색 <저장한 검색> [행=<1~50>]                 행 수와 앞 행들
SQL 직접 실행처럼 저장해 두지 않은 조회는 받지 않는다. 데이터는 core.using()으로 화면과 같은 차례를 잡고 읽는다."""
import shlex

import core
import metrics
import server
import settings
import source
import use_counts
from notify import table_text

MAX_ROWS = 50  # 검색 결과로 답할 최대 행 수
DEFAULT_ROWS = 20
MAX_LIST = 30  # 목록으로 답할 최대 개수
HELP = """쓸 수 있는 명령:
• 지표 — 지표 목록
• 지표 <이름> 검색=<저장한 검색> [나누기=<컬럼>] — 지표 값
• 검색 — 저장한 검색 목록
• 검색 <저장한 검색> [행=20] — 행 수와 앞 행들
값에 빈칸이 있으면 "따옴표"로 감싸세요."""


def split(text):
    """글을 (명령, 위치 값들, {키: 값})으로. 따옴표가 맞지 않으면 ValueError."""
    try:
        words = shlex.split(text.strip())
    except ValueError:
        raise ValueError("따옴표가 닫히지 않았습니다.") from None
    if not words:
        return "도움", [], {}
    plain, named = [], {}
    for word in words[1:]:
        key, eq, value = word.partition("=")
        if eq and key in ("검색", "나누기", "행"):
            named[key] = value
        else:
            plain.append(word)
    return words[0], plain, named


def number(value, low, high, label):
    if not value.isdigit() or not low <= int(value) <= high:
        raise ValueError(f"{label}는 {low}~{high} 사이의 정수로 쓰세요.")
    return int(value)


def listed(names, more_word):
    shown = names[:MAX_LIST]
    return "\n".join(f"• {n}" for n in shown) + (f"\n… 외 {len(names) - MAX_LIST}개" if len(names) > MAX_LIST else "") if shown else more_word


def metric_list():
    found = metrics.registry()
    if not found:
        return "지표 파일을 고르지 않았습니다. 프로그램의 [지표] 탭에서 지표 파일을 읽으세요."
    order = use_counts.order("metrics", list(found))
    return "지표:\n" + listed([f"{n}" + (f" — {found[n]['description']}" if found[n]["description"] else "") for n in order], "")


def metric_answer(plain, named):
    name = " ".join(plain)
    if not named.get("검색"):
        raise ValueError('어느 저장한 검색 위에서 구할지 검색=<이름>으로 쓰세요. 예: 지표 CTR 검색="이번 달"')
    body = core.saved_search(named["검색"])
    with core.using():
        found, labels, values, more = core.metric_values(body, [name], named.get("나누기"))
    fmt = found[name]["format"]
    text = lambda v: "—" if v is None else f"{v * 100:,.2f}%" if fmt == "percent" else f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else f"{v:,}" if isinstance(v, int) else str(v)
    if labels is None:
        return f"{name} ({named['검색']}): {text(values[name][0])}"
    rows = [{named["나누기"]: "(빈 값)" if label is None else label, name: text(v)} for label, v in zip(labels, values[name])]
    note = f"\n값이 많아 앞 {metrics.MAX_GROUPS}개만" if more else ""
    return f"{name} ({named['검색']}, {named['나누기']}별)\n```\n{table_text({'columns': [named['나누기'], name], 'rows': rows})}\n```{note}"


def search_list():
    found = settings.searches()
    return "저장한 검색:\n" + listed(use_counts.order("searches", list(found)), "") if found else "저장한 검색이 없습니다. 프로그램의 [탐색]에서 검색을 저장하세요."


def search_answer(plain, named):
    name = " ".join(plain)
    limit = number(named.get("행", str(DEFAULT_ROWS)), 1, MAX_ROWS, "행")
    body = core.saved_search(name)
    with core.using():
        table, count = core.search_rows(body, limit)
    use_counts.use("searches", name)
    return f"{name}: {count:,}행" + (f" (앞 {len(table['rows'])}행)" if count > len(table["rows"]) else "") + f"\n```\n{table_text(table)}\n```"


def answer(text):
    """글 한 줄에 답할 글. 잘못 쓴 명령·오류는 안내 글로 돌려준다(예외를 내지 않는다)."""
    try:
        command, plain, named = split(text or "")
        if command in ("도움", "help", "?"):
            return HELP
        if command == "지표":
            return metric_answer(plain, named) if plain else metric_list()
        if command == "검색":
            return search_answer(plain, named) if plain else search_list()
        return f"모르는 명령입니다: {command}\n{HELP}"
    except Exception as e:  # 연결 오류 등의 문구에 비밀번호가 있으면 가린다
        return f"할 수 없습니다: {source.mask(str(e), server.state['passwords'])}"
