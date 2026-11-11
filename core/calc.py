"""계산(탐색 6번)과 피벗(집계 탭). 계산은 한 줄씩 적은 것을 검색 식에 차례로 건다: 창 함수(순위·이동 평균/합계·누적 합계·이전 값·차이·그룹별 상위 N),
여러 컬럼에 같은 계산(ibis.selectors의 across), 목록 컬럼을 행으로 펼치기(UNNEST), 컬럼들을 행으로 세우기(UNPIVOT).
연결이 하지 못하는 계산이면 부르는 쪽(core.runnable, app의 검색)이 기간·조건까지의 결과를 이 PC(held의 DuckDB)에 받아 그 위에서 한다.
세로로 세우기는 못 하는 연결에서 UNION ALL로 대신한다. 피벗은 값마다 조건부 집계(sum(where=…))로 만들어 모든 연결에서 같은 SQL 모양이다."""
import fnmatch
import json
import re
from collections.abc import Iterator

from lazy import ibis

from core import crosstab
from core import parse
from core import sql_funcs

GUIDE = ("순위 기준 금액 desc [그룹 매장코드] / 이동평균·이동합계 금액 3행(또는 7일) 기준 주문일 / 누적합 금액 기준 주문일 / "
         "이전값·차이 금액 기준 주문일 / 상위 3 기준 금액 desc 그룹 매장코드 / 반올림 0 숫자 / 곱하기 1.1 금액, 수량 / 빈값채우기 0 숫자 / "
         "공백지우기 글자 / 펼치기 목록컬럼 / 세로로 컬럼1, 컬럼2 / 그룹집계 sum(금액) 그룹 매장코드(또는 테이블 주문 키 고객ID=customer_id) [기준 …] [조건 …] / 전처리 type 금액 {\"to\": \"float\"} · "
         "끝에 '이름 새이름'으로 새 컬럼 이름 · 빈칸이나 이름·기준·그룹·조건 낱말이 든 컬럼은 `고객 이름`처럼 백틱으로 감싸요")
EXAMPLES = ["순위 기준 금액 desc 그룹 매장코드", "이동평균 금액 3행 기준 주문일 그룹 매장코드", "이동합계 금액 7일 기준 주문일", "누적합 금액 기준 주문일 그룹 매장코드",
            "이전값 금액 기준 주문일", "차이 금액 기준 주문일", "상위 3 기준 금액 desc 그룹 매장코드", "반올림 0 숫자", "곱하기 1.1 금액",
            "빈값채우기 0 숫자", "공백지우기 글자", "펼치기 태그", "세로로 금액, 수량"]  # 화면의 [예시 넣기]
WINDOWS = ("순위", "이동평균", "이동합계", "누적합", "이전값", "차이", "상위")
NEW_WINDOWS = tuple(i for i in sql_funcs.ids_for("calc") if sql_funcs.FUNCS[i]["window"])  # 함수 사전의 창 함수 id(dense_rank·lead·cum_mean·ntile …): 줄을 id로 시작한다
TABLE_WINDOWS = sql_funcs.SEQUENCES  # 함수 사전의 시퀀스 함수 id(session_id·cum_distinct·ewma·streak·event_path): 줄을 id로 시작하고 표를 바꾼다(table_window)
COLUMNS = ("순위", "이동평균", "이동합계", "누적합", "이전값", "차이", *NEW_WINDOWS)  # 새 컬럼 하나를 만드는 창 함수. 서로 기대지 않으면 mutate 하나로 묶는다
EACH = ("반올림", "곱하기", "빈값채우기", "공백지우기")
FORMULAS = ("식", "SQL식", "창거르기")  # 식 = 계산 컬럼의 식(함수 사전의 행 함수 전부), SQL식 = DB 고유 함수(직접 쓰기), 창거르기 = QUALIFY
LINE_KINDS = ("그룹집계", "전처리")  # 줄을 낱말로 나누지 않는 종류(조건 글·JSON 값이 들어간다)
KINDS = (*WINDOWS, *EACH, "펼치기", "세로로", *FORMULAS, *NEW_WINDOWS, *TABLE_WINDOWS, *LINE_KINDS)
MAX_GROUPS = 3  # 그룹집계의 그룹 컬럼 수
CALL_HEAD = re.compile(r"([A-Za-z_]\w*)\(")  # 그룹집계 본문의 '집계id(' (닫는 괄호는 call_parts가 찾는다)
TOKEN = re.compile(r"`([^`]*)`|`|[^\s`]+")  # 낱말 줄의 낱말: 백틱으로 감싼 컬럼 이름(빈칸·칸 낱말 포함), 짝 없는 백틱, 백틱·빈칸이 아닌 글자 덩어리
MARKERS = ("테이블", "키", "그룹", "기준", "조건", "이름")  # 그룹집계 나머지의 칸 낱말(이 차례로 한 번씩)
GROUP_USAGE = ("그룹집계 <집계id>(<값컬럼>[, 인자]) [테이블 대상 키 내컬럼=대상컬럼] [그룹 컬럼1, 컬럼2] [기준 컬럼 asc|desc] [조건 조건문] 이름 새이름. "
               "빈칸이나 이름·기준·그룹·조건 낱말이 든 컬럼은 `고객 이름`처럼 백틱으로 감싸요. 예: 그룹집계 count_distinct(고객) 그룹 매장 이름 매장고객수")
COMMA_NAME = "'{name}' 컬럼 이름에는 쉼표가 있어 계산 줄로는 못 써요. 전처리 화면에서 이름을 바꾸거나 식 줄의 col(\"…\")로 쓰세요."  # 백틱 안 쉼표(names가 쉼표로 컬럼을 나눈다). 화면 NewCol.bq와 같은 글
OPTIONS = ("기준", "그룹", "이름")  # 줄 안에서 뒤따르는 값을 묶는 낱말
LONG_NAMES = ("항목", "값")  # 세로로 세울 때 만드는 컬럼(원래 컬럼 이름, 값)
SIZE = re.compile(r"(\d+)(행|일)")
TOP = "__ibis_top"  # 상위 N을 거를 때 잠깐 쓰는 행 번호
MAX_PIVOT = 30  # 피벗에서 열로 펼칠 값의 최대 개수
MAX_ROWS = 500  # 피벗 결과의 최대 행 수
OLD_HOWS = ("count", "sum", "mean", "min", "max")  # 처음부터 있던 피벗 집계(목록의 앞에 둔다)
HOWS = {i: sql_funcs.label_of(i) for i in (*OLD_HOWS, *[i for i in sql_funcs.ids_for("pivot") if i not in OLD_HOWS and sql_funcs.FUNCS[i]["aggregate"]])}  # 함수 사전의 집계 전부
SUBTOTALS = ("rollup", "cube", "sets")  # 피벗의 소계·총계 방식(ROLLUP·CUBE·GROUPING SETS)
SHOWS = ("value", "row_pct", "col_pct", "total_pct", "lift")  # 피벗 칸 보기: 값, 행·열·전체 합계 대비 비율(0~1), 향상도
EMPTY = "(빈 값)"  # 열로 펼칠 컬럼이 빈 값인 행의 칸 이름


def takes_main(kind):
    """줄의 본문에 컬럼(이나 값)을 받는 종류인지. 순위·dense_rank처럼 기준·그룹만 받는 종류는 아니다."""
    if kind == "순위":
        return False
    if kind in NEW_WINDOWS or kind in TABLE_WINDOWS:
        return any(a["name"] not in sql_funcs.HAS_ARGS for a in sql_funcs.FUNCS[kind]["args"])
    return True


def split_words(line, columns=()):
    """(종류, 본문 낱말들, {기준·그룹·이름: 낱말들}). columns(이 표의 컬럼 이름들)를 주면, 본문을 받는 종류의 첫 낱말이 '이름'·'그룹'·'기준'이고
    그 이름의 컬럼이 있을 때 칸 낱말이 아니라 컬럼으로 읽는다('lag 이름 기준 일자', '식 이름 || '님' 이름 호칭'). 백틱으로 감싼 낱말은 옵션으로 읽지 않는다."""
    toks = list(tokens(line))
    words = [w for w, _, _ in toks]
    quoted = [q for _, q, _ in toks]
    if not words or quoted[0] or words[0] not in KINDS:
        raise ValueError(f"계산 종류는 {', '.join(KINDS[:len(WINDOWS) + len(EACH) + 2 + len(FORMULAS)])} 중 하나로 시작해요(함수 사전의 창 함수 id도 돼요: {', '.join(NEW_WINDOWS[:4])} …). 형식: {GUIDE}")
    main, opts, at = [], {}, None
    lead = words[0] in ("곱하기", "빈값채우기") or words[0] == "반올림" and len(words) > 1 and words[1].lstrip("-").isdigit()  # 값이 앞에 오는 줄(반올림의 자리 수는 생략할 수 있다)
    spot = 1 if lead else 0  # 반올림 0 이름·곱하기 2 그룹: 대상 컬럼은 둘째 낱말
    first = takes_main(words[0]) and len(words) > spot + 1 and (quoted[spot + 1] or words[spot + 1] in columns)
    for k, (w, q) in enumerate(zip(words[1:], quoted[1:])):
        if k == spot and first:
            main.append(w)
        elif not q and w in OPTIONS and not (at is not None and not at):  # 칸 낱말 바로 뒤의 '그룹'·'기준'·'이름'은 그 칸의 값(컬럼 이름이 그 낱말)
            at = opts.setdefault(w, [])
        else:
            (main if at is None else at).append(w)
    return words[0], main, opts


def names(words, expr, what="컬럼"):
    """'a, b' 낱말들을 컬럼 이름 목록으로(있는지 검사)."""
    found = [n.strip() for n in " ".join(words).split(",") if n.strip()]
    for n in found:
        parse.check_name(n, expr.columns, what)
    return found


def window(expr, opts, need_order=True):
    """기준(정렬)·그룹으로 창을 만든다. 기준이 필요한 계산에 없으면 ValueError."""
    order = opts.get("기준") or []
    if need_order and not order:
        raise ValueError("이 계산은 순서가 필요해요. '기준 컬럼 [asc|desc]'를 넣으세요.")
    keys = [parse.parse_sort(part.strip(), expr) for part in " ".join(order).split(",") if part.strip()]  # '기준 금액 desc, id': 쉼표로 2차 키(같은 값의 순서를 정한다)
    groups = [expr[n] for n in names(opts.get("그룹") or [], expr)]
    return keys, groups or None


def new_name(expr, opts, default, taken=()):
    """새 컬럼 이름(이름 옵션 또는 default). 이미 있거나 같은 묶음에서 만든 이름이면 ValueError."""
    name = " ".join(opts.get("이름") or []) or default
    if name in expr.columns or name in taken:
        raise ValueError(f"'{name}' 컬럼이 이미 있어요. 끝에 '이름 다른이름'을 붙이세요.")
    return name


def number(text, what):
    try:
        return float(text)
    except (TypeError, ValueError):
        raise ValueError(f"{what}에는 숫자를 넣으세요.") from None


def targets(words, expr):
    """여러 컬럼에 같은 계산을 할 대상: 숫자·글자·날짜, 이름 패턴(amt_*), 또는 컬럼 목록. ibis.selectors로."""
    import ibis.selectors as s
    text = " ".join(words).strip()
    picks = {"숫자": s.numeric(), "글자": s.of_type("string"), "날짜": s.of_type("date") | s.of_type("timestamp")}
    if text in picks:
        return picks[text]
    if any(ch in text for ch in "*?"):
        return s.matches("^" + fnmatch.translate(text).replace(r"\Z", "$"))
    if not text:
        raise ValueError("대상을 넣으세요: 숫자, 글자, 날짜, 이름 패턴(amt_*), 또는 컬럼1, 컬럼2")
    return s.cols(*names(words, expr))


def each(kind, main, expr):
    """여러 컬럼에 같은 계산(across). 반올림 [자리] 대상 / 곱하기 수 대상 / 빈값채우기 값 대상 / 공백지우기 대상."""
    import ibis.selectors as s
    if kind == "공백지우기":
        return expr.mutate(s.across(targets(main, expr) & s.of_type("string"), lambda c: c.strip()))
    if not main:
        raise ValueError(f"{kind} 뒤에 값과 대상을 넣으세요. 예: {next(e for e in EXAMPLES if e.startswith(kind))}")
    value, rest = main[0], main[1:]
    if kind == "반올림":
        digits = int(number(value, "자리 수"))
        return expr.mutate(s.across(targets(rest, expr) & s.numeric(), lambda c: sql_funcs.half_up(c, digits)))
    if kind == "곱하기":
        times = number(value, "곱할 수")
        return expr.mutate(s.across(targets(rest, expr) & s.numeric(), lambda c: c * times))
    return expr.mutate(s.across(targets(rest, expr), lambda c: c.fill_null(parse.convert(c.type(), value))))


def longer(expr, cols):
    """컬럼들을 행으로 세운다(UNPIVOT): 항목(원래 컬럼 이름)과 값. 값 타입은 모두 숫자면 실수, 아니면 글자.
    연결이 하지 못하면 컬럼마다 뽑아 UNION ALL로 잇는다."""
    import ibis.selectors as s
    numeric = all(expr[c].type().is_numeric() for c in cols)
    kind = "float64" if numeric else "string"
    for n in LONG_NAMES:
        if n in expr.columns:
            raise ValueError(f"세로로 세우면 '{n}' 컬럼을 만드는데 이미 있어요. 그 컬럼 이름을 먼저 바꾸세요.")
    typed = expr.mutate(**{c: expr[c].cast(kind) for c in cols})
    found = typed.pivot_longer(s.cols(*cols), names_to=LONG_NAMES[0], values_to=LONG_NAMES[1])
    from core import source
    if source.compiles(found):
        return found
    rest = [c for c in expr.columns if c not in cols]
    parts = [typed.select(*rest, **{LONG_NAMES[0]: ibis.literal(c), LONG_NAMES[1]: typed[c]}) for c in cols]
    return ibis.union(*parts, distinct=False)


def call_parts(text):
    """'집계id(인자) 나머지'를 {id, args, rest}로. 닫는 괄호는 괄호 짝과 따옴표(인자 맨 앞에서 연 것)를 따라 찾는다: 금액(원)·\"금액(원)\"도 이름.
    정규식 대신 한 번 훑는다(따옴표가 많은 줄에서 정규식이 되짚기를 오래 하지 않게). 모양이 아니면 None."""
    m = CALL_HEAD.match(text)
    if not m:
        return None
    depth, quote, start = 1, None, True  # start: 인자 맨 앞(괄호·쉼표 뒤의 빈칸까지)이라 따옴표를 열 수 있는 자리
    for i in range(m.end(), len(text)):
        ch = text[i]
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"" and start:
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return {"id": m[1], "args": text[m.end():i], "rest": text[i + 1:]}
        start = ch in "(," or (start and ch.isspace())
    return None


def tokens(line: str) -> Iterator[tuple[str, bool, tuple[int, int]]]:
    """[SPECIFICATION] plans/phase-10.md#tokens
    낱말 줄을 (낱말, 백틱으로 감쌌는지, 원래 줄의 자리)로 하나씩 내는 생성기다."""
    for m in TOKEN.finditer(line):
        if m[0] == "`":
            raise ValueError("백틱(`)이 닫히지 않았어요. 빈칸이나 이름·기준·그룹·조건 낱말이 든 컬럼은 `고객 이름`처럼 앞뒤를 백틱으로 감싸요.")
        if m[1] is None:
            yield m[0], False, m.span()
            continue
        if m[1] == "":
            raise ValueError("백틱 안이 비었어요. 컬럼 이름을 넣으세요.")
        if "," in m[1]:
            raise ValueError(COMMA_NAME.format(name=m[1]))
        yield m[1], True, m.span()


def group_fields(rest: str) -> dict[str, str]:
    """[SPECIFICATION] plans/phase-10.md#group_fields
    그룹집계 본문의 닫는 괄호 뒤 글을 테이블·키·그룹·기준·조건·이름 칸으로 나눈다."""
    field, last, after_marker, cond = None, -1, False, None
    values: dict[str, list[tuple[str, int, int]]] = {}
    for w, quoted, (s, e) in tokens(rest):
        if not quoted and w in MARKERS and not after_marker:
            at = MARKERS.index(w)
            if at <= last:
                raise ValueError(GROUP_USAGE)
            last = at
            if w == "조건":
                cond = rest[e:]
                break
            field, values[w], after_marker = w, [], True
        else:
            if field is None:
                raise ValueError(GROUP_USAGE)
            values[field].append((w, s, e))
            after_marker = False
    if not all(values.values()):
        raise ValueError(GROUP_USAGE)
    fields = {k: "" for k in MARKERS}
    for k, found in values.items():
        fields[k] = "".join(w if i == 0 else (w if found[i - 1][2] == s else " " + w) for i, (w, s, _) in enumerate(found))
    if fields["테이블"] and len(values["테이블"]) != 1:
        raise ValueError(GROUP_USAGE)
    name = fields["이름"]
    if name and (len(values["이름"]) != 1 or not sql_funcs.NAME.fullmatch(name) or name.lower() in ("asc", "desc")):
        raise ValueError(GROUP_USAGE)
    if cond is not None:
        c = cond.strip()
        head, sep, nm = c.rpartition(" 이름 ")
        if sep and sql_funcs.NAME.fullmatch(nm.strip()) and nm.strip().lower() not in ("asc", "desc"):
            c, name = head.strip(), nm.strip()
        if not c:
            raise ValueError(GROUP_USAGE)
        fields["조건"] = c
    return {"table": fields["테이블"], "key": fields["키"], "group": fields["그룹"], "order": fields["기준"], "cond": fields["조건"], "name": name}


def group_line(text, expr, schema=None):
    """그룹집계 <집계id>(<값컬럼>[, 인자…]) [테이블 대상 키 내컬럼=대상컬럼, …] [그룹 컬럼, …] [기준 컬럼 asc|desc] [조건 조건문] 이름 새이름: 그룹마다 집계해 left join으로 행마다 붙인다
    (창 함수가 아니라 묶어 집계 → 이어 붙이기라 Postgres의 count distinct도 되고 모든 연결에서 같은 SQL). 그룹이 없으면 표 전체 값을 모든 행에(cross join).
    테이블을 주면 대상 테이블(@탭도, querying.table_of)을 키의 오른쪽 컬럼으로 묶어 집계하고(값·기준·조건은 대상의 컬럼) 키 왼쪽 컬럼으로 붙인다.
    그룹·키 값이 빈 행이나 짝이 없는 행은 빈 값이다(SQL의 = 비교)."""
    m = call_parts(text.strip())
    if not m:
        raise ValueError(GROUP_USAGE)
    parts = group_fields(m["rest"])
    f = sql_funcs.get(m["id"])
    if not f["aggregate"]:
        raise ValueError(f"'{m['id']}'은 집계 함수가 아니에요. 그룹집계에는 함수 사전의 집계(sum·count_distinct·median …)를 써요.")
    src, pairs = expr, []
    if parts["table"] or parts["key"]:
        if not (parts["table"] and parts["key"]) or parts["group"]:
            raise ValueError("다른 테이블의 집계는 '테이블 대상 키 내컬럼=대상컬럼'으로 써요(그룹 대신 키로 묶어요). 예: 그룹집계 count() 테이블 주문 키 고객ID=customer_id 이름 주문수")
        from core import querying  # querying이 이 모듈을 부른다
        src = querying.table_of(parts["table"], schema or None)  # 검색·구성의 스키마에서(조인 줄과 같게)
        pairs = [(left.strip(), (right or left).strip()) for left, _, right in (k.partition("=") for k in parts["key"].split(",") if k.strip())]
        for left, right in pairs:
            parse.check_name(left, expr.columns, "키 컬럼")
            parse.check_name(right, src.columns, f"'{parts['table']}'의 키 컬럼")
        if src.get_backend() is not expr.get_backend():
            raise ValueError("이 PC에 받아 둔 결과(원격 DB·연결이 못 하는 계산) 위에서는 다른 테이블의 집계를 붙일 수 없어요. 조인 줄로 붙인 뒤 그룹집계하세요.")
    words = [parse.unquote(a) for a in parse.split(parse.COMMA_RE, m["args"]) if a.strip()]  # 따옴표 안의 쉼표·괄호는 이름의 일부("금액(원)")
    value = words.pop(0) if words and any(a["name"] == "x" for a in f["args"]) else None
    if value is not None:
        parse.check_name(value, src.columns, "값 컬럼")
    specs = [a for a in f["args"] if a["name"] != "x" and a["name"] not in sql_funcs.HAS_ARGS]
    if len(words) > len(specs):
        raise ValueError(f"{f['id']}의 인자: 값 컬럼{''.join(', ' + s['label'] for s in specs)}. 예: {f['example']}")
    opts = {s["name"]: word_value(s, w) for s, w in zip(specs, words)}
    keys, _ = window(src, {"기준": [parts["order"]] if parts["order"] else []}, need_order=False)
    if keys:
        if not any(a["name"] == "order" for a in f["args"]):
            raise ValueError(f"{f['id']}는 '기준'을 쓰지 않아요. 예: {f['example']}")
        opts["order"] = keys
    groups = names([parts["group"]] if parts["group"] else [], expr, "그룹 컬럼")
    if len(groups) > MAX_GROUPS:
        raise ValueError(f"그룹은 {MAX_GROUPS}개까지예요.")
    name = new_name(expr, {"이름": [parts["name"]] if parts["name"] else []}, f"{value or '행'}_{f['id']}")
    measure = {name: sql_funcs.aggregate(f["id"], src, value, parts["cond"] or None, opts)}
    if pairs:
        found = src.group_by([right for _, right in pairs]).aggregate(**measure)
        return expr.left_join(found, [expr[left] == found[right] for left, right in pairs]).select(*expr.columns, name)
    if not groups:
        return expr.cross_join(expr.aggregate(**measure))
    return expr.left_join(expr.group_by(groups).aggregate(**measure), groups).select(*expr.columns, name)


def prep_parts(text: str, expr: "ibis.expr.types.Table") -> tuple[str, str, object]:
    """[SPECIFICATION] plans/phase-1.md#prep_parts
    전처리 줄 본문 '<단계id> <컬럼> <JSON 값>'을 (단계id, 컬럼, 값)으로 나눈다."""
    from core import prep_ops  # 늦은 import(calc.prep_line과 같이)
    op, _, rest = text.strip().partition(" ")
    column = parse.head_name(rest, expr.columns) or rest.split(" ", 1)[0]  # 공백 있는 컬럼 이름도
    try:
        value = json.loads(rest[len(column):])
    except ValueError:
        raise ValueError('전처리 줄의 값은 JSON으로 써요. 예: 전처리 type 금액 {"to": "float"} / 전처리 trim 이름 true') from None
    parse.check_name(column, expr.columns)
    if isinstance(value, dict) and "known" in value:
        raise ValueError(prep_ops.KNOWN_REFUSED)
    return op, column, value


def stat_step(line: str, expr: "ibis.expr.types.Table") -> tuple[str, str, object] | None:
    """[SPECIFICATION] plans/phase-1.md#stat_step
    계산 줄이 표 전체 통계를 먼저 구해야 하는 전처리 단계면 (단계id, 컬럼, 값), 아니면 None을 돌려준다."""
    head, _, text = line.strip().partition(" ")
    if head != "전처리":
        return None
    op, column, value = prep_parts(text, expr)
    return (op, column, value) if reads_stats(op, value, expr[column].type()) else None


def prep_line(text, expr, pin=None):
    """전처리 <단계id> <컬럼> <JSON 값>: 전처리 단계 하나(prep_ops.apply_ops)를 건다. 쿼리 화면의 머리글 우클릭 메뉴가 계산 줄로 쓰는 것(계산 줄로 안 되는 단계).
    표 전체 통계를 먼저 읽는 단계(reads_stats)는 pin(querying.pinned·prep_derive.apply_formulas가 연결 스레드에서 미리 구한 prep_ops.Known)으로 건다. pin이 없으면 거절한다(식을 만들 때 조회가 나가지 않게)."""
    from core import prep_ops
    op, column, value = prep_parts(text, expr)
    value = value if pin is None else pin
    if reads_stats(op, value, expr[column].type()):
        raise ValueError(f"'{op}'은 표 전체의 통계를 먼저 구해 넣어야 해서 여기서는 못 써요. 쿼리 화면의 계산 줄로 거세요.")
    return prep_ops.apply_ops(expr, column, {op: value})


def reads_stats(op, value, dtype):
    """전처리 단계가 표 전체의 통계·값 목록을 먼저 읽는지: fill mean·median·mode, scale, outlier iqr·std, label, onehot, rare, 구조가 아닌 컬럼의 expand(키 찾기, 찾아 둔 키를 주면 아님).
    counts는 행 안에서 세거나(못 하면 번호표 조인) 식만 만들고 표를 읽지 않아 된다. 미리 구한 값(prep_ops.Known)이면 아님.
    outlier pct, Phase 6 단계(prep_ops.needs_stats: flags의 상위 %·평균/중앙값·이상치, power 자동 λ, gfill·cut·others·freq·target)도 읽는다."""
    from core import prep_ops
    if isinstance(value, prep_ops.Known):
        return False
    if op == "fill":
        return isinstance(value, dict) and value.get("how") in ("mean", "median", "mode")
    if op == "outlier":
        return isinstance(value, dict) and value.get("by") in ("iqr", "std", "pct")
    if op == "expand":
        return not dtype.is_struct() and not isinstance(value, dict)  # 찾아 둔 키({"keys": …})를 주면 읽지 않는다
    if op in prep_ops.STAT_OPS6:
        return isinstance(value, dict) and prep_ops.needs_stats(op, value)
    return op in ("scale", "label", "onehot", "rare")


def apply_line(line, expr, schema=None, pin=None):
    """계산 한 줄을 식에 건다."""
    head, _, text = line.strip().partition(" ")
    if head == "그룹집계":
        return group_line(text, expr, schema)
    if head == "전처리":
        return prep_line(text, expr, pin)
    kind, main, opts = split_words(line, expr.columns)
    if kind in EACH:
        return each(kind, main, expr)
    if kind == "펼치기":
        col = names(main, expr)
        if len(col) != 1 or not expr[col[0]].type().is_array():
            raise ValueError("펼치기는 목록(배열) 컬럼 하나를 넣어요.")
        return expr.unnest(col[0])
    if kind == "세로로":
        cols = names(main, expr)
        if len(cols) < 2:
            raise ValueError("세로로 세울 컬럼을 두 개 이상 넣으세요. 예: 세로로 금액, 수량")
        return longer(expr, cols)
    if kind in FORMULAS:
        spans = [span for _, _, span in tokens(line)]  # 본문 낱말은 종류 바로 뒤부터 이어진다(split_words): 원래 줄에서 자른다
        return formula_line(kind, main, opts, expr, line[spans[1][0]:spans[len(main)][1]] if main else "")
    if kind in TABLE_WINDOWS:
        return table_window(kind, main, opts, expr)
    if kind == "상위":
        keys, groups = window(expr, opts)
        n = int(number(main[0] if main else None, "상위 몇 개"))
        if n < 1:
            raise ValueError("상위 몇 개는 1 이상이에요.")
        ranked = expr.mutate(**{TOP: ibis.row_number().over(ibis.window(group_by=groups, order_by=keys))})
        return ranked.filter(ranked[TOP] < n).select(*expr.columns)  # drop은 SELECT를 한 겹 더 만든다
    return expr.mutate(**dict([column(kind, main, opts, expr)]))


def column(kind, main, opts, expr, taken=()):
    """새 컬럼 하나를 만드는 창 함수 줄(COLUMNS)의 (이름, 식). 식은 expr의 컬럼만 쓴다. taken은 같은 mutate에서 이미 만든 이름."""
    if kind in NEW_WINDOWS:
        return catalog_window(kind, main, opts, expr, taken)
    keys, groups = window(expr, opts)
    order = (opts.get("기준") or [""])[0]
    if kind == "순위":
        return new_name(expr, opts, f"{order}_순위", taken), ibis.rank().over(ibis.window(group_by=groups, order_by=keys)) + 1
    main = spaced_first(main, expr)  # 공백 있는 컬럼 이름('판매 금액')
    col = names(main[:1], expr)[0] if main else None
    if col is None or not expr[col].type().is_numeric() and kind != "이전값":
        raise ValueError(f"{kind}할 숫자 컬럼을 넣으세요.")
    value = expr[col]
    if kind == "누적합":
        win = ibis.range_window(group_by=groups, order_by=keys, preceding=None, following=0)  # 같은 기준 값의 행은 같은 합(ROWS면 행마다 부분합이 달라진다)
        return new_name(expr, opts, f"{col}_누적합", taken), value.sum().over(win)
    if kind in ("이전값", "차이"):
        prev = value.lag(1).over(ibis.window(group_by=groups, order_by=keys))
        return new_name(expr, opts, f"{col}_{kind}", taken), prev if kind == "이전값" else value - prev
    size = SIZE.fullmatch(main[1]) if len(main) > 1 else None
    if not size or int(size[1]) < 1:
        raise ValueError(f"{kind}의 크기를 '3행'이나 '7일'처럼 넣으세요.")
    n, unit = int(size[1]), size[2]
    if unit == "일":
        order_type = expr[order].type() if order in expr.columns else None
        if not (order_type and (order_type.is_date() or order_type.is_timestamp())):
            raise ValueError("'일' 단위는 기준이 날짜·날짜시간 컬럼이어야 해요.")
        win = ibis.window(group_by=groups, order_by=keys, preceding=ibis.interval(days=n - 1), following=0)
    else:
        win = ibis.window(group_by=groups, order_by=keys, preceding=n - 1, following=0)
    how = value.mean() if kind == "이동평균" else value.sum()
    return new_name(expr, opts, f"{col}_{kind}{n}{unit}", taken), how.over(win)


def word_value(spec, word):
    """줄의 낱말 하나를 함수 사전 칸의 값으로: 컬럼은 이름 그대로, 숫자 칸은 수, 그 밖은 글자(숫자로 읽히면 값 칸은 수)."""
    if spec["kind"] == "list":
        return [word_value({"kind": "value", "label": spec["label"]}, w) for w in word.split(",") if w]
    if spec["kind"] in ("number", "int") or (spec["kind"] == "value" and re.fullmatch(r"-?\d+(\.\d+)?", word)):
        value = number(word, spec["label"])
        return int(value) if spec["kind"] == "int" or value == int(value) else value
    return word


def spaced_first(main, expr):
    """본문 낱말이 공백 있는 컬럼 이름으로 시작하면 그 낱말들을 이름 하나로 합친다(줄은 공백으로 나뉜다)."""
    name = parse.head_name(" ".join(main), expr.columns)
    return [name, *main[len(name.split()):]] if name else main


def window_args(f, main, opts, expr):
    """함수 사전의 창 함수 줄 '<id> [컬럼] [인자…] 기준 … 그룹 …'을 함수의 인자로. 본문 낱말은 칸 차례(정렬·묶음·조건 칸 제외), 기준·그룹은 옵션에서."""
    specs = [a for a in f["args"] if a["name"] not in sql_funcs.HAS_ARGS]
    if len(main) > len(specs):
        raise ValueError(f"{f['id']}의 인자: {', '.join(s['label'] for s in specs) or '없음'}. 형식: {f['example']} 기준 컬럼 [그룹 컬럼]")
    raw = {s["name"]: word_value(s, w) for s, w in zip(specs, main)}
    keys, groups = window(expr, opts, need_order=False)
    have = {a["name"] for a in f["args"]}
    for key, word, found in (("order", "기준", keys), ("group", "그룹", groups)):
        if found and key not in have:
            raise ValueError(f"{f['id']}는 '{word}'을 쓰지 않아요. 형식: {f['example']}")
    return {**raw, **({"order": keys} if keys else {}), **({"group": groups} if groups else {})}


def catalog_window(kind, main, opts, expr, taken):
    """함수 사전의 창 함수 줄의 (이름, 식)."""
    f, main = sql_funcs.FUNCS[kind], spaced_first(main, expr)
    raw = window_args(f, main, opts, expr)
    first = next((w for w in main if w in expr.columns), "")
    return new_name(expr, opts, f"{first}_{kind}".lstrip("_"), taken), f["build"](sql_funcs.resolve(f, expr, raw))


def table_window(kind: str, main: list[str], opts: dict[str, list[str]], expr: "ibis.expr.types.Table") -> "ibis.expr.types.Table":
    """[SPECIFICATION] plans/phase-7.md#table_window
    함수 사전의 시퀀스 함수 줄 '<id> [컬럼] [인자…] 기준 … 그룹 … 이름 …'을 걸어 새 컬럼이 붙은 표를 돌려준다."""
    f = sql_funcs.FUNCS[kind]
    main = spaced_first(main, expr)
    raw = window_args(f, main, opts, expr)
    first = next((w for w in main if w in expr.columns), "")
    name = new_name(expr, opts, f"{first}_{kind}".lstrip("_"))
    c = sql_funcs.resolve(f, expr, raw)
    c["name"] = name
    return f["build"](c)


def formula_line(kind, main, opts, expr, body=None):
    """식·SQL식·창거르기 줄을 건다. 식 <식> 이름 새이름 [기준 …] [그룹 …] / SQL식 <SQL 식> 이름 새이름 / 창거르기 <창 함수 id> [인자…] <비교> <값> 기준 … [그룹 …]."""
    if kind == "창거르기":
        if len(main) < 3 or main[0] not in NEW_WINDOWS and not (main[0] in sql_funcs.FUNCS and sql_funcs.FUNCS[main[0]]["window"]):
            raise ValueError("창거르기 <창 함수 id> [인자…] <비교(<= 등)> <값> 기준 컬럼 [그룹 컬럼]. 예: 창거르기 rank <= 3 기준 금액 desc 그룹 매장")
        f = sql_funcs.FUNCS[main[0]]
        raw = window_args(f, spaced_first(main[1:-2], expr), opts, expr)
        return sql_funcs.apply("qualify", expr, {"func": main[0], "func_args": raw, "op": main[-2], "value": number(main[-1], "비교 값")})
    name = " ".join(opts.get("이름") or [])
    if not name:
        raise ValueError(f"{kind}에는 끝에 '이름 새이름'을 넣으세요. 예: {kind} 금액 * 수량 이름 합계금액")
    text = " ".join(main) if body is None else body  # body: 원래 줄에서 자른 본문(따옴표 안 빈칸 그대로)
    if kind == "SQL식":
        try:
            return sql_funcs.raw_expr(expr, name, text)
        except sql_funcs.NeedsLocal:  # 파일 연결: 컬럼만 보이는 자리 표시, 값은 PC 사본에서
            return sql_funcs.local_only(expr, name)
    from core import prep_derive
    prep_derive.check_name(name, expr, "계산 컬럼")
    keys, _ = window(expr, opts, need_order=False)
    return expr.mutate(**{name: prep_derive.Formula(expr, text, keys, names(opts.get("그룹") or [], expr)).build()})


def mentions(main, opts):
    """줄이 가리키는 컬럼 이름들(본문의 첫 낱말, 기준, 그룹)."""
    found = set(" ".join(main[:1]).split(",")) | set((opts.get("기준") or [""])[:1]) | set(" ".join(opts.get("그룹") or []).split(","))
    return {n.strip() for n in found if n.strip()}


def apply(lines, expr, schema=None, pins=None):
    """계산 줄들을 차례로 건다. 비었으면 그대로. 이어진 창 함수 줄(COLUMNS)은 앞 줄이 만든 컬럼을 쓰지 않는 한 mutate 하나로 묶는다
    (줄마다 mutate하면 SELECT가 한 겹씩 늘어난다). pins는 {줄 번호: 미리 구한 통계 값}(querying.pinned)"""
    batch = {}
    for i, line in enumerate(lines or []):
        kind, main, opts = (None, [], {}) if (line.split() or [""])[0] in LINE_KINDS else split_words(line, expr.columns)
        if kind in COLUMNS and not any(n in line for n in batch):  # 앞 줄이 만든 컬럼(공백 있는 이름 포함)을 글 어디에서든 쓰면 나눈다
            name, value = column(kind, main, opts, expr, batch)
            batch[name] = value
            continue
        if batch:
            expr, batch = expr.mutate(**batch), {}
        if kind in COLUMNS:
            name, value = column(kind, main, opts, expr)
            batch[name] = value
        else:
            expr = apply_line(line, expr, schema, (pins or {}).get(i))
    return expr.mutate(**batch) if batch else expr


def metric(expr, how, value, where, opts=None):
    """피벗 칸 하나의 집계 식(where를 만족하는 행만). how는 함수 사전의 집계 id, opts는 그 집계의 나머지 인자(분위수 p, 구분자 sep …)."""
    return sql_funcs.aggregate(how, expr, value, where, opts)


def pivot_measure(expr, value, how, opts):
    """피벗 값 하나 (집계 id, 값 컬럼, 옵션)을 검사한다."""
    if how not in HOWS and how not in sql_funcs.ALIASES:
        raise ValueError(f"집계는 {', '.join(list(HOWS)[:8])} … 중 하나예요(함수 사전의 집계 {len(HOWS)}가지).")
    if opts is not None and not isinstance(opts, dict):
        raise ValueError("집계의 옵션(opts)은 객체여야 해요.")
    if value is not None:
        parse.check_name(value, expr.columns, "값 컬럼")
        if how in ("sum", "mean") and not expr[value].type().is_numeric():
            raise ValueError("합계·평균은 숫자 컬럼에만 돼요.")
    elif sql_funcs.needs_column(how):
        raise ValueError("값 컬럼을 고르세요(개수는 비워도 돼요).")
    return how, value, opts


def having(table, line):
    """집계한 결과 표를 조건 한 줄로 거른다(HAVING). 결과 컬럼 이름에 공백이 있으면('전체 합계') 조건 줄에도 그대로 쓸 수 있게, 잠깐 밑줄로 바꿔 읽는다."""
    plain = {name: name.replace(" ", "_") for name in table.columns if " " in name}
    for name in sorted(plain, key=len, reverse=True):
        line = line.replace(name, plain[name])
    shown = table.rename({new: old for old, new in plain.items()}) if plain else table
    shown = shown.filter(parse.predicate(line, shown))
    return shown.rename({old: new for old, new in plain.items()}) if plain else shown


def pivot(expr, spec, values_of):
    """피벗(조건부 집계) 식. spec: {rows: [행 기준 컬럼], column: 열로 펼칠 컬럼(없으면 펼치지 않음), value: 값 컬럼(개수는 없어도 됨), how(함수 사전의 집계 id), opts(그 집계의 나머지 인자),
    values: [{value, how, opts}] 여러 값(있으면 value·how 대신. 결과 이름은 '값 집계', 펼친 칸은 '열값 값 집계'), show: value·row_pct·col_pct(칸을 행·열 합계 대비 비율로),
    where: 조건 한 줄, subtotals: rollup·cube·sets(소계·총계 줄), sets: 소계 조합(sets일 때), having: 집계한 뒤 거르는 조건 한 줄(결과 컬럼 이름으로),
    order: 결과 컬럼 순서 한 줄('전체 개수 desc', 없으면 행 기준 순, 같은 값은 행 기준 순), top: 정렬한 뒤 앞 N행만}.
    values_of(식)은 열로 펼칠 값 목록을 구하는 조회(MAX_PIVOT+2개까지)를 돌린다. 값마다 조건부 집계 하나와 '전체'를 만든다. 열 컬럼이 빈 값인 행은 EMPTY 칸."""
    if not isinstance(spec, dict):
        raise ValueError("피벗 설정이 없어요.")
    rows = [r for r in spec.get("rows") or [] if r]
    for r in rows:
        parse.check_name(r, expr.columns, "행 기준 컬럼")
    column = spec.get("column") or None
    many = spec.get("values")
    if many is not None and (not isinstance(many, list) or not many or not all(isinstance(v, dict) for v in many)):
        raise ValueError("여러 값(values)은 [{value, how}] 모양이어야 해요.")
    preset = spec.get("preset")
    if preset is not None and (many is not None or spec.get("value")):
        raise ValueError("프리셋(preset)과 값(values·value)은 함께 쓸 수 없어요.")
    show = spec.get("show") or "value"
    if show not in SHOWS:
        raise ValueError(f"칸 보기는 {', '.join(SHOWS)} 중 하나예요.")
    flags = {k: spec.get(k) for k in ("share", "parent", "total_row", "pareto")}
    for k, v in flags.items():
        if v is not None and not isinstance(v, bool):
            raise ValueError(f"{k}는 참·거짓이에요.")
    if preset is not None:
        found, derived, preset_shares = crosstab.expand(preset, expr)
    else:
        derived, preset_shares = [], []
        found = [{"how": v.get("how", "count"), "value": v.get("value") or None, "opts": v.get("opts", spec.get("opts")),
                  "where": (v.get("where") or None) if many else None, "name": v.get("name") if many else None} for v in many or [spec]]
    measures = [pivot_measure(expr, m["value"], m["how"], m["opts"]) for m in found]
    named = bool(many) or preset is not None
    cond = parse.predicate(spec["where"], expr) if spec.get("where") and (not isinstance(spec["where"], str) or spec["where"].strip()) else None
    totals, conds = [], []
    for (how, value, _), m in zip(measures, found):
        label = sql_funcs.label_of(how)
        name = m["name"] or (f"{value} {label}" if value else label) if named else f"전체 {label}"
        if m["where"] is not None and not m["name"]:
            name += f" ({m['where']})"
        if name.casefold() in {t.casefold() for t in totals}:
            raise ValueError(f"값 이름 '{name}'이 겹쳐요. name을 정하세요.")
        totals.append(name)
        where = m["where"] if preset is not None or m["where"] is None else parse.predicate(m["where"], expr)  # 프리셋의 where는 이미 식
        conds.append(cond if where is None else where if cond is None else cond & where)
    subtotals = spec.get("subtotals")
    if flags["parent"] and len(rows) < 2:
        raise ValueError("부모 대비 비중은 행 기준 컬럼이 둘 이상이어야 해요.")
    if flags["total_row"] and not rows:
        raise ValueError("총계 줄은 행 기준 컬럼이 있어야 해요.")
    if flags["total_row"] and subtotals:
        raise ValueError("총계 줄(total_row)은 소계 없이 쓸 때만 골라요(소계·총계 방식에는 이미 있어요).")
    if flags["pareto"]:
        if not rows:
            raise ValueError("파레토 누적은 행 기준 컬럼이 있어야 해요.")
        if subtotals or flags["total_row"]:
            raise ValueError("파레토 누적은 소계·총계 줄과 함께 쓸 수 없어요.")
        if measures[0][0] not in crosstab.ADDITIVE:
            raise ValueError("파레토 누적은 개수·합계에만 돼요.")
    if show == "lift":
        if column is None:
            raise ValueError("리프트는 열로 펼칠 컬럼이 있어야 해요.")
        if any(how not in crosstab.ADDITIVE for how, _, _ in measures):
            raise ValueError("리프트는 개수·합계에만 돼요.")
    aggs = {t: metric(expr, how, value, c, opts) for t, (how, value, opts), c in zip(totals, measures, conds)}
    cells = {t: [] for t in totals}
    if column is not None:
        parse.check_name(column, expr.columns, "열로 펼칠 컬럼")
        found = values_of(expr.select(v=expr[column]).distinct().order_by("v").limit(MAX_PIVOT + 2))
        found = [v for v in found if v is not None] + ([None] if None in found else [])  # 빈 값은 맨 뒤 칸
        if len(found) - (None in found) > MAX_PIVOT:
            raise ValueError(f"'{column}'의 값이 {MAX_PIVOT}개보다 많아 열로 펼칠 수 없어요. 조건으로 줄이거나 다른 컬럼을 고르세요.")
        taken = {n.casefold() for n in [*aggs, *rows]}  # DuckDB는 컬럼 이름의 대소문자를 가리지 않아 Y와 y가 부딪친다
        used = set(taken)
        clash = any(str(v).casefold() in taken for v in found)
        for v in found:
            is_v = expr[column].isnull() if v is None else expr[column] == ibis.literal(v, type=expr[column].type())  # 타입 없는 Decimal은 DECIMAL(18,3)이 되어 큰 ID가 넘친다
            for t, (how, value, opts), c in zip(totals, measures, conds):
                name = EMPTY if v is None else str(v)
                if clash and v is not None:
                    name = f"{column}={v}"
                if named:
                    name = f"{name} {t}"
                base, i = name, 2
                while name.casefold() in used:
                    name, i = f"{base}_{i}", i + 1
                used.add(name.casefold())
                aggs[name] = metric(expr, how, value, is_v if c is None else is_v & c, opts)
                cells[t].append(name)
    if subtotals and (subtotals not in SUBTOTALS or not rows):
        raise ValueError(f"소계는 행 기준 컬럼이 있어야 하고 {', '.join(SUBTOTALS)} 중 하나예요.")
    # 소계·총계 줄: 기준 조합마다 집계해 잇는다(기준 칸은 글자, 빠진 칸은 '(소계)'·'(전체)')
    sets = (sql_funcs.group_sets("rollup" if subtotals == "rollup" else "cube" if subtotals == "cube" else "grouping_sets", rows, spec.get("sets")) if subtotals
            else [rows, []] if flags["total_row"] else None)
    if flags["parent"]:
        table = crosstab.parent_table(expr, rows, aggs, sets or [rows], totals)
    elif sets:
        table = sql_funcs.grouping(expr, rows, aggs, sets)
    else:
        table = expr.group_by(rows).aggregate(**aggs) if rows else expr.aggregate(**aggs)
    if spec.get("convert") is not None:  # 전환율: 열로 펼쳤으면 전체 칸마다 펼친 칸 사이, 아니면 값 사이
        derived = [*derived, *([d for t in totals for d in crosstab.conversions(cells[t], spec["convert"])] if column is not None else crosstab.conversions(totals, spec["convert"]))]
    table = crosstab.derive(table, derived)
    shares = list(dict.fromkeys([*(totals if flags["share"] else []), *preset_shares, *([totals[0]] if flags["pareto"] else [])]))
    distinct = {t for t, (how, _, _) in zip(totals, measures) if how in crosstab.DISTINCT_HOWS}
    table = crosstab.relative(table, expr, aggs, {t: cells[t] for t in totals}, show, shares, distinct)
    if sets:
        table = table.order_by(sql_funcs.grouped_order(table, rows))
    elif rows:
        table = table.order_by(rows)
    if show == "col_pct" and subtotals:
        raise ValueError("소계·총계 줄이 있으면 열 합계 대비 비율로 볼 수 없어요(소계가 두 번 더해져요).")
    if show == "row_pct":  # 칸 ÷ 그 행의 전체(전체 칸은 1)
        table = table.mutate(**{n: table[n] / table[t] for t in totals for n in [*cells[t], t]})
    elif show == "col_pct" and (plain := {n: table[n] / table[n].sum() for t in totals if t not in distinct for n in [t, *cells[t]]}):  # 더해지는 칸 ÷ 그 열의 합(고유 개수 칸은 relative가 다시 센 분모로)
        table = table.mutate(**plain)
    if (spec.get("having") or "").strip():  # HAVING: 집계한 뒤 결과 컬럼으로 거른다
        table = having(table, spec["having"])
    order = (spec.get("order") or "").strip() or (f"{totals[0]} desc" if flags["pareto"] else "")
    if order:  # 결과 컬럼 순서('전체 개수 desc'), top이 있으면 앞 N행만(많은 순 상위 N). 파레토는 기본이 첫 값 큰 순
        key, _, way = order.rpartition(" ")
        key, way = (key, way.lower()) if way.lower() in ("asc", "desc") else (order, "asc")
        parse.check_name(key, table.columns, "정렬 컬럼")
        table = table.order_by([table[key].desc() if way == "desc" else table[key].asc(), *[table[r] for r in rows if r != key]])  # 같은 값은 행 기준 순(상위 N의 경계가 매번 같게)
    if spec.get("top"):
        table = table.limit(min(parse.whole_number(spec["top"], 1, MAX_ROWS, f"상위 N은 1 이상 {MAX_ROWS} 이하의 정수예요."), MAX_ROWS))
    return table.limit(MAX_ROWS + 1)
