"""조건·정렬 글에서 컬럼 이름을 읽는 공통 도우미(문장·내 지표·지표 사전이 쓴다). 흐름(다이어그램) 보기는 없앴고 이 파일에는 함께 쓰던 도우미만 남았다."""
import re

from core import parse

KIND_TEXT = {"num": "숫자", "text": "글자", "date": "날짜", "bool": "참/거짓", "json": "JSON", "array": "목록", "struct": "구조", "other": "기타"}
COND_HEAD = re.compile(r"^\s*([^\s=!<>]+)")  # 조건·기간 줄의 첫 낱말(컬럼)


def bare(name):
    """a.b 같은 구조 필드 이름의 컬럼 부분."""
    return str(name).split(".")[0]


def head_name(text, columns, condition=False):
    """글 앞의 공백을 뗀 뒤 parse.head_name으로 공백 있는 컬럼 이름을 읽는다(규칙은 parse.spaced와 한 곳)."""
    return parse.head_name(text.lstrip(), columns, condition)


def cond_columns(cond, columns=None):
    """조건(글자·{line}·{any})이 가리키는 컬럼 이름들. columns(알려진 컬럼 이름들)를 주면 공백 있는 이름도 한 낱말로 읽는다."""
    if isinstance(cond, str):
        try:
            pieces = parse.split(parse.OR_RE, cond.strip())  # 한 줄 안의 or(따옴표 안은 값)마다 컬럼을 모은다
        except ValueError:
            pieces = [cond.strip()]
        found = set()
        for piece in pieces:
            geo = parse.GEO_RE.match(piece.strip())
            if geo and len(geo.group(2).replace(",", " ").split()) == parse.GEO_COUNTS[geo.group(1).lower()]:  # 좌표 조건 near·box는 앞의 두 낱말이 위도·경도 컬럼
                found |= {bare(w) for w in geo.group(2).replace(",", " ").split()[:2]}
            elif name := head_name(piece, columns, condition=True):
                found.add(name)
            elif m := COND_HEAD.match(piece):
                found.add(bare(m.group(1)))
        return found
    if isinstance(cond, dict):
        if "any" in cond:
            return {c for item in cond["any"] for c in cond_columns(item, columns)}
        return cond_columns(cond.get("line", ""), columns)
    return set()


def kind_text(kind):
    return KIND_TEXT.get(kind, kind)
