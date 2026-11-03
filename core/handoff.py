"""'@이름' 등록부: 쿼리 화면의 탭(가공한 결과)과 전처리로 넘긴 검색을 이름 → 검색 요청(body)으로 둔다. 데이터는 읽지 않는다.
querying이 기준 테이블·조인·행 합치기 자리의 @이름을 그 검색의 식으로 풀고, 전처리는 그 식에서 시작한다.
이 프로그램이 켜져 있는 동안 메모리에만 두고, 연결이 바뀌면 비운다(검색은 그 연결의 테이블을 읽는다)."""
from core import server

PREFIX = "@"  # 등록한 이름의 앞글자. 연결의 테이블 대신 그 검색의 식이다
MAX_NAME = 81  # @를 뺀 80자
ITEMS = {}  # {@이름: {"body": 검색 요청, "rows": 세어 둔 행 수, "shown": 쿼리 화면에 보이던 쪽 또는 None}}
server.ON_SWITCH.append(ITEMS.clear)


def register(name, body, shown=None):
    """name(@로 시작)에 검색 요청 body를 둔다. 같은 이름이면 바꾼다(세어 둔 행 수는 버린다). 이름을 돌려준다."""
    if not isinstance(name, str) or not name.startswith(PREFIX) or not 1 < len(name) <= MAX_NAME:
        raise ValueError(f"이름은 {PREFIX}로 시작하고 {MAX_NAME - 1}자까지예요. 예: {PREFIX}탭1")
    if not isinstance(body, dict) or not isinstance(body.get("base"), str):
        raise ValueError("등록할 검색 요청(body)에 기준 테이블(base)이 없어요.")
    ITEMS[name] = {"body": body, "rows": None, "shown": shown if isinstance(shown, dict) else None}
    return name


def get(name):
    """등록한 항목 {body, rows, shown}. 없으면 None."""
    return ITEMS.get(name)


def forget(name):
    """등록을 지운다. 없으면 아무 일도 하지 않는다."""
    ITEMS.pop(name, None)


def names():
    """등록한 이름들(등록 차례)."""
    return list(ITEMS)
