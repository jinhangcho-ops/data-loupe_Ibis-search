"""채팅 명령(코어): 다른 앱(Slack 봇 등)에서 온 글 한 줄을 읽어 답할 글을 만든다. 어느 앱에서 왔는지 모른다.
명령(앞의 낱말, 값에 빈칸이 있으면 따옴표로 감싼다):
  도움                                          명령 안내
  지표                                          지표 목록(많이 쓴 순)
  지표 <이름> 검색=<저장한 검색> [나누기=<컬럼>]   저장한 검색 위에서 지표 값(나누면 값마다 표)
  검색                                          저장한 검색 목록(많이 쓴 순)
  검색 <저장한 검색> [행=<1~50>]                 행 수와 앞 행들
  질문 <자연어>                                  LLM으로 SQL을 만들어 보여 준다(실행하지 않음, nl2sql)
SQL 직접 실행처럼 저장해 두지 않은 조회는 받지 않는다. 데이터는 querying.using()으로 화면과 같은 차례를 잡고 읽는다."""
import re
import shlex
import threading

from core import querying
from core import metric_file
from core import scan_size
from core import server
from core import settings
from core import source
from core import use_counts
from integrations.notify import table_text
from integrations.schedule import VALUE_ERRORS, notice_text

PYTHON_VALUE = re.compile(r"invalid literal|could not convert|cannot convert|unconverted data|time data|Out of bounds", re.I)  # 파이썬이 데이터 값을 넣는 ValueError 문구

MAX_ROWS = 50  # 검색 결과로 답할 최대 행 수
DEFAULT_ROWS = 20
MAX_LIST = 30  # 목록으로 답할 최대 개수
HELP = """쓸 수 있는 명령:
• 지표 — 지표 목록
• 지표 <이름> 검색=<저장한 검색> [나누기=<컬럼>] — 지표 값
• 검색 — 저장한 검색 목록
• 검색 <저장한 검색> [행=20] — 행 수와 앞 행들
• 질문 <자연어> — SQL을 만들어 보여 줌(실행하지 않음)
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


def check_size(body):
    """Slack에서는 확인 팝업을 띄울 수 없어서, 처리 용량이 기준을 넘거나 알 수 없으면 실행하지 않는다(server.scan_gate와 같은 scan_size.limit_check 판정). 차례를 잡은 채로 부른다."""
    con = server.state["con"]
    sql, dialect = server.expr_sql(con, querying.filtered(body))
    found = server.run(lambda: scan_size.limit_check(con, sql, server.state["opened"], dialect))
    if found["over"] or found["unknown"]:
        raise ValueError(f"처리할 데이터가 커서(또는 크기를 알 수 없어서) Slack에서는 실행하지 않습니다. {found['text']} 프로그램에서 실행하세요.")


def metric_list():
    found = metric_file.registry()
    if not found:
        return "지표 파일을 고르지 않았습니다. 프로그램의 [지표] 탭에서 지표 파일을 읽으세요."
    order = use_counts.order("metrics", list(found))
    return "지표:\n" + listed([f"{n}" + (f" — {found[n]['description']}" if found[n]["description"] else "") for n in order], "")


def metric_answer(plain, named):
    name = " ".join(plain)
    if not named.get("검색"):
        raise ValueError('어느 저장한 검색 위에서 구할지 검색=<이름>으로 쓰세요. 예: 지표 CTR 검색="이번 달"')
    body = querying.saved_search(named["검색"])
    with querying.using(fresh=True):
        check_size(body)
        found, labels, values, more = querying.metric_values(body, [name], named.get("나누기"))
    fmt = found[name]["format"]
    text = lambda v: "—" if v is None else f"{v * 100:,.2f}%" if fmt == "percent" else f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else f"{v:,}" if isinstance(v, int) else str(v)
    if labels is None:
        return f"{name} ({named['검색']}): {text(values[name][0])}"
    rows = [{named["나누기"]: "(빈 값)" if label is None else label, name: text(v)} for label, v in zip(labels, values[name])]
    note = f"\n값이 많아 앞 {metric_file.MAX_GROUPS}개만" if more else ""
    return f"{name} ({named['검색']}, {named['나누기']}별)\n```\n{table_text({'columns': [named['나누기'], name], 'rows': rows})}\n```{note}"


def search_list():
    found = settings.searches()
    return "저장한 검색:\n" + listed(use_counts.order("searches", list(found)), "") if found else "저장한 검색이 없습니다. 프로그램의 [탐색]에서 검색을 저장하세요."


def search_answer(plain, named):
    name = " ".join(plain)
    limit = number(named.get("행", str(DEFAULT_ROWS)), 1, MAX_ROWS, "행")
    body = querying.saved_search(name)
    with querying.using(fresh=True):
        check_size(body)
        table, count = querying.search_rows(body, limit)
    use_counts.use("searches", name)
    return f"{name}: {count:,}행" + (f" (앞 {len(table['rows'])}행)" if count > len(table["rows"]) else "") + f"\n```\n{table_text(table)}\n```"


def question_answer(text):
    """자연어 질문으로 SQL을 만들어 보여 준다. 실행하지 않는다(실행은 프로그램 화면에서 사람이)."""
    from query import nl2sql
    from core import sql_exec
    with querying.using():
        con = server.state["con"]
        vts = server.virtual_tables()
        ctx = server.run(lambda: nl2sql.context(con, server.connection_key(), None, vts))
        known = source.list_tables(con) + list(vts)
    sql = nl2sql.ask(text, ctx, sql_exec.dialect_of(con), known)  # LLM에 묻는 동안은 차례를 잡지 않는다
    return f"만든 SQL(실행하지 않았습니다. 프로그램의 SQL 탭에서 확인하고 실행하세요):\n```\n{sql}\n```"


def error_text(e):
    """Slack으로 나갈 오류 글. 이 앱이 직접 만든 안내 오류(정확히 ValueError이고 데이터 값을 넣는 문구가 아닌 것)만 비밀번호를 가려 그대로 보여 준다(무엇을 고칠지 알아야 하므로).
    그 밖의 오류(DB·라이브러리 오류, ValueError의 하위 오류인 pyarrow ArrowInvalid 등)는 DB가 데이터 값을 문장에 넣을 수 있어 예약 알림과 같은 허용 목록(schedule.notice_text)의 정해진 문구만 보낸다."""
    message = str(e)
    if type(e) is ValueError and not VALUE_ERRORS.search(message) and not PYTHON_VALUE.search(message):
        return f"할 수 없습니다: {source.mask(message, server.state['passwords'])}"
    return f"할 수 없습니다: {source.mask(notice_text(message) or '실패했습니다', server.state['passwords'])}"


failure = threading.local()  # answer가 방금 삼킨 오류의 전체 글(이 스레드). Slack에는 안 나가고, 봇 기록(메모리)에만 남기려고 어댑터가 읽는다


def detail(e):
    """로그에 남길 오류 글(비밀번호는 가림). 예약 실행 기록이 오류 글을 그대로 남기는 것과 같다. 밖으로 보내지 않는다."""
    return source.mask(f"{type(e).__name__}: {e}", server.state["passwords"])[:500]


def answer(text):
    """글 한 줄에 답할 글. 잘못 쓴 명령·오류는 안내 글로 돌려준다(예외를 내지 않는다)."""
    try:
        head, _, rest = (text or "").strip().partition(" ")
        if head == "질문":  # 자연어는 따옴표가 섞일 수 있어 나누지 않고 그대로 넘긴다
            return question_answer(rest.strip()) if rest.strip() else "질문을 쓰세요. 예: 질문 지난달 매장별 매출 합계"
        command, plain, named = split(text or "")
        if command in ("도움", "help", "?"):
            return HELP
        if command == "지표":
            return metric_answer(plain, named) if plain else metric_list()
        if command == "검색":
            return search_answer(plain, named) if plain else search_list()
        return f"모르는 명령입니다: {command}\n{HELP}"
    except Exception as e:  # 연결 오류 등의 문구에 비밀번호가 있으면 가린다
        failure.error = detail(e)
        return error_text(e)
