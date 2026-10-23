"""계산(탐색 6번)과 피벗(집계 탭). 계산은 한 줄씩 적은 것을 검색 식에 차례로 건다: 창 함수(순위·이동 평균/합계·누적 합계·이전 값·차이·그룹별 상위 N),
여러 컬럼에 같은 계산(ibis.selectors의 across), 목록 컬럼을 행으로 펼치기(UNNEST), 컬럼들을 행으로 세우기(UNPIVOT).
연결이 하지 못하는 계산이면 부르는 쪽(core.runnable, app의 검색)이 기간·조건까지의 결과를 이 PC(held의 DuckDB)에 받아 그 위에서 한다.
세로로 세우기는 못 하는 연결에서 UNION ALL로 대신한다. 피벗은 값마다 조건부 집계(sum(where=…))로 만들어 모든 연결에서 같은 SQL 모양이다."""
import fnmatch
import re

from lazy import ibis

import parse
import sql_funcs

GUIDE = ("순위 기준 금액 desc [그룹 매장코드] / 이동평균·이동합계 금액 3행(또는 7일) 기준 주문일 / 누적합 금액 기준 주문일 / "
         "이전값·차이 금액 기준 주문일 / 상위 3 기준 금액 desc 그룹 매장코드 / 반올림 0 숫자 / 곱하기 1.1 금액, 수량 / 빈값채우기 0 숫자 / "
         "공백지우기 글자 / 펼치기 목록컬럼 / 세로로 컬럼1, 컬럼2 · 끝에 '이름 새이름'으로 새 컬럼 이름")
EXAMPLES = ["순위 기준 금액 desc 그룹 매장코드", "이동평균 금액 3행 기준 주문일 그룹 매장코드", "이동합계 금액 7일 기준 주문일", "누적합 금액 기준 주문일 그룹 매장코드",
            "이전값 금액 기준 주문일", "차이 금액 기준 주문일", "상위 3 기준 금액 desc 그룹 매장코드", "반올림 0 숫자", "곱하기 1.1 금액",
            "빈값채우기 0 숫자", "공백지우기 글자", "펼치기 태그", "세로로 금액, 수량"]  # 화면의 [예시 넣기]
WINDOWS = ("순위", "이동평균", "이동합계", "누적합", "이전값", "차이", "상위")
NEW_WINDOWS = tuple(i for i in sql_funcs.ids_for("calc") if sql_funcs.FUNCS[i]["window"])  # 함수 사전의 창 함수 id(dense_rank·lead·cum_mean·ntile …): 줄을 id로 시작한다
COLUMNS = ("순위", "이동평균", "이동합계", "누적합", "이전값", "차이", *NEW_WINDOWS)  # 새 컬럼 하나를 만드는 창 함수. 서로 기대지 않으면 mutate 하나로 묶는다
EACH = ("반올림", "곱하기", "빈값채우기", "공백지우기")
FORMULAS = ("식", "SQL식", "창거르기")  # 식 = 계산 컬럼의 식(함수 사전의 행 함수 전부), SQL식 = DB 고유 함수(직접 쓰기), 창거르기 = QUALIFY
KINDS = (*WINDOWS, *EACH, "펼치기", "세로로", *FORMULAS, *NEW_WINDOWS)
OPTIONS = ("기준", "그룹", "이름")  # 줄 안에서 뒤따르는 값을 묶는 낱말
LONG_NAMES = ("항목", "값")  # 세로로 세울 때 만드는 컬럼(원래 컬럼 이름, 값)
SIZE = re.compile(r"(\d+)(행|일)")
TOP = "__ibis_top"  # 상위 N을 거를 때 잠깐 쓰는 행 번호
MAX_PIVOT = 30  # 피벗에서 열로 펼칠 값의 최대 개수
MAX_ROWS = 500  # 피벗 결과의 최대 행 수
OLD_HOWS = ("count", "sum", "mean", "min", "max")  # 처음부터 있던 피벗 집계(목록의 앞에 둔다)
HOWS = {i: sql_funcs.label_of(i) for i in (*OLD_HOWS, *[i for i in sql_funcs.ids_for("pivot") if i not in OLD_HOWS and sql_funcs.FUNCS[i]["aggregate"]])}  # 함수 사전의 집계 전부
SUBTOTALS = ("rollup", "cube", "sets")  # 피벗의 소계·총계 방식(ROLLUP·CUBE·GROUPING SETS)


def split_words(line):
    """(종류, 본문 낱말들, {기준·그룹·이름: 낱말들})."""
    words = line.split()
    if not words or words[0] not in KINDS:
        raise ValueError(f"계산 종류는 {', '.join(KINDS[:len(WINDOWS) + len(EACH) + 2 + len(FORMULAS)])} 중 하나로 시작합니다(함수 사전의 창 함수 id도 됩니다: {', '.join(NEW_WINDOWS[:4])} …). 형식: {GUIDE}")
    main, opts, at = [], {}, None
    for w in words[1:]:
        if w in OPTIONS:
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
        raise ValueError("이 계산은 순서가 필요합니다. '기준 컬럼 [asc|desc]'를 넣으세요.")
    keys = [parse.parse_sort(" ".join(order), expr)] if order else []
    groups = [expr[n] for n in names(opts.get("그룹") or [], expr)]
    return keys, groups or None


def new_name(expr, opts, default, taken=()):
    """새 컬럼 이름(이름 옵션 또는 default). 이미 있거나 같은 묶음에서 만든 이름이면 ValueError."""
    name = " ".join(opts.get("이름") or []) or default
    if name in expr.columns or name in taken:
        raise ValueError(f"'{name}' 컬럼이 이미 있습니다. 끝에 '이름 다른이름'을 붙이세요.")
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
        return s.matches(fnmatch.translate(text).replace(r"\Z", "$"))
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
        return expr.mutate(s.across(targets(rest, expr) & s.numeric(), lambda c: c.round(digits)))
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
            raise ValueError(f"세로로 세우면 '{n}' 컬럼을 만드는데 이미 있습니다. 그 컬럼 이름을 먼저 바꾸세요.")
    typed = expr.mutate(**{c: expr[c].cast(kind) for c in cols})
    found = typed.pivot_longer(s.cols(*cols), names_to=LONG_NAMES[0], values_to=LONG_NAMES[1])
    import source
    if source.compiles(found):
        return found
    rest = [c for c in expr.columns if c not in cols]
    parts = [typed.select(*rest, **{LONG_NAMES[0]: ibis.literal(c), LONG_NAMES[1]: typed[c]}) for c in cols]
    return ibis.union(*parts, distinct=False)


def apply_line(line, expr):
    """계산 한 줄을 식에 건다."""
    kind, main, opts = split_words(line)
    if kind in EACH:
        return each(kind, main, expr)
    if kind == "펼치기":
        col = names(main, expr)
        if len(col) != 1 or not expr[col[0]].type().is_array():
            raise ValueError("펼치기는 목록(배열) 컬럼 하나를 넣습니다.")
        return expr.unnest(col[0])
    if kind == "세로로":
        cols = names(main, expr)
        if len(cols) < 2:
            raise ValueError("세로로 세울 컬럼을 두 개 이상 넣으세요. 예: 세로로 금액, 수량")
        return longer(expr, cols)
    if kind in FORMULAS:
        return formula_line(kind, main, opts, expr)
    if kind == "상위":
        keys, groups = window(expr, opts)
        n = int(number(main[0] if main else None, "상위 몇 개"))
        if n < 1:
            raise ValueError("상위 몇 개는 1 이상입니다.")
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
    col = names(main[:1], expr)[0] if main else None
    if col is None or not expr[col].type().is_numeric() and kind != "이전값":
        raise ValueError(f"{kind}할 숫자 컬럼을 넣으세요.")
    value = expr[col]
    if kind == "누적합":
        win = ibis.window(group_by=groups, order_by=keys, preceding=None, following=0)
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
            raise ValueError("'일' 단위는 기준이 날짜·날짜시간 컬럼이어야 합니다.")
        win = ibis.window(group_by=groups, order_by=keys, preceding=ibis.interval(days=n - 1), following=0)
    else:
        win = ibis.window(group_by=groups, order_by=keys, preceding=n - 1, following=0)
    how = value.mean() if kind == "이동평균" else value.sum()
    return new_name(expr, opts, f"{col}_{kind}{n}{unit}", taken), how.over(win)


def word_value(spec, word):
    """줄의 낱말 하나를 함수 사전 칸의 값으로: 컬럼은 이름 그대로, 숫자 칸은 수, 그 밖은 글자(숫자로 읽히면 값 칸은 수)."""
    if spec["kind"] in ("number", "int") or (spec["kind"] == "value" and re.fullmatch(r"-?\d+(\.\d+)?", word)):
        value = number(word, spec["label"])
        return int(value) if spec["kind"] == "int" or value == int(value) else value
    return word


def window_args(f, main, opts, expr):
    """함수 사전의 창 함수 줄 '<id> [컬럼] [인자…] 기준 … 그룹 …'을 함수의 인자로. 본문 낱말은 칸 차례(정렬·묶음·조건 칸 제외), 기준·그룹은 옵션에서."""
    specs = [a for a in f["args"] if a["name"] not in sql_funcs.HAS_ARGS]
    if len(main) > len(specs):
        raise ValueError(f"{f['id']}의 인자는 {', '.join(s['label'] for s in specs) or '없음'}입니다. 형식: {f['example']} 기준 컬럼 [그룹 컬럼]")
    raw = {s["name"]: word_value(s, w) for s, w in zip(specs, main)}
    keys, groups = window(expr, opts, need_order=False)
    have = {a["name"] for a in f["args"]}
    for key, word, found in (("order", "기준", keys), ("group", "그룹", groups)):
        if found and key not in have:
            raise ValueError(f"{f['id']}는 '{word}'을 쓰지 않습니다. 형식: {f['example']}")
    return {**raw, **({"order": keys} if keys else {}), **({"group": groups} if groups else {})}


def catalog_window(kind, main, opts, expr, taken):
    """함수 사전의 창 함수 줄의 (이름, 식)."""
    f = sql_funcs.FUNCS[kind]
    raw = window_args(f, main, opts, expr)
    first = next((w for w in main if w in expr.columns), "")
    return new_name(expr, opts, f"{first}_{kind}".lstrip("_"), taken), f["build"](sql_funcs.resolve(f, expr, raw))


def formula_line(kind, main, opts, expr):
    """식·SQL식·창거르기 줄을 건다. 식 <식> 이름 새이름 [기준 …] [그룹 …] / SQL식 <SQL 식> 이름 새이름 / 창거르기 <창 함수 id> [인자…] <비교> <값> 기준 … [그룹 …]."""
    if kind == "창거르기":
        if len(main) < 3 or main[0] not in NEW_WINDOWS and not (main[0] in sql_funcs.FUNCS and sql_funcs.FUNCS[main[0]]["window"]):
            raise ValueError("창거르기 <창 함수 id> [인자…] <비교(<= 등)> <값> 기준 컬럼 [그룹 컬럼]. 예: 창거르기 rank <= 3 기준 금액 desc 그룹 매장")
        f = sql_funcs.FUNCS[main[0]]
        raw = window_args(f, main[1:-2], opts, expr)
        return sql_funcs.apply("qualify", expr, {"func": main[0], "func_args": raw, "op": main[-2], "value": number(main[-1], "비교 값")})
    name = " ".join(opts.get("이름") or [])
    if not name:
        raise ValueError(f"{kind}에는 끝에 '이름 새이름'을 넣으세요. 예: {kind} 금액 * 수량 이름 합계금액")
    text = " ".join(main)
    if kind == "SQL식":
        try:
            return sql_funcs.raw_expr(expr, name, text)
        except sql_funcs.NeedsLocal:  # 파일 연결: 컬럼만 보이는 자리 표시, 값은 PC 사본에서
            return sql_funcs.local_only(expr, name)
    import prep_derive
    prep_derive.check_name(name, expr, "계산 컬럼")
    keys, _ = window(expr, opts, need_order=False)
    return expr.mutate(**{name: prep_derive.Formula(expr, text, keys, names(opts.get("그룹") or [], expr)).build()})


def mentions(main, opts):
    """줄이 가리키는 컬럼 이름들(본문의 첫 낱말, 기준, 그룹)."""
    found = set(" ".join(main[:1]).split(",")) | set((opts.get("기준") or [""])[:1]) | set(" ".join(opts.get("그룹") or []).split(","))
    return {n.strip() for n in found if n.strip()}


def apply(lines, expr):
    """계산 줄들을 차례로 건다. 비었으면 그대로. 이어진 창 함수 줄(COLUMNS)은 앞 줄이 만든 컬럼을 쓰지 않는 한 mutate 하나로 묶는다
    (줄마다 mutate하면 SELECT가 한 겹씩 늘어난다)."""
    batch = {}
    for line in lines or []:
        kind, main, opts = split_words(line)
        if kind in COLUMNS and not mentions(main, opts) & set(batch):
            name, value = column(kind, main, opts, expr, batch)
            batch[name] = value
            continue
        if batch:
            expr, batch = expr.mutate(**batch), {}
        if kind in COLUMNS:
            name, value = column(kind, main, opts, expr)
            batch[name] = value
        else:
            expr = apply_line(line, expr)
    return expr.mutate(**batch) if batch else expr


def metric(expr, how, value, where, opts=None):
    """피벗 칸 하나의 집계 식(where를 만족하는 행만). how는 함수 사전의 집계 id, opts는 그 집계의 나머지 인자(분위수 p, 구분자 sep …)."""
    return sql_funcs.aggregate(how, expr, value, where, opts)


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
    where: 조건 한 줄, subtotals: rollup·cube·sets(소계·총계 줄), sets: 소계 조합(sets일 때), having: 집계한 뒤 거르는 조건 한 줄(결과 컬럼 이름으로),
    order: 결과 컬럼 순서 한 줄('전체 개수 desc', 없으면 행 기준 순), top: 정렬한 뒤 앞 N행만}.
    values_of(식)은 열로 펼칠 값 목록을 구하는 조회(MAX_PIVOT+1개까지)를 돌린다. 값마다 조건부 집계 하나와 '전체'를 만든다."""
    if not isinstance(spec, dict):
        raise ValueError("피벗 설정이 없습니다.")
    rows = [r for r in spec.get("rows") or [] if r]
    for r in rows:
        parse.check_name(r, expr.columns, "행 기준 컬럼")
    how, value, column = spec.get("how", "count"), spec.get("value") or None, spec.get("column") or None
    if how not in HOWS and how not in sql_funcs.ALIASES:
        raise ValueError(f"집계는 {', '.join(list(HOWS)[:8])} … 중 하나입니다(함수 사전의 집계 {len(HOWS)}가지).")
    opts = spec.get("opts")
    if opts is not None and not isinstance(opts, dict):
        raise ValueError("집계의 옵션(opts)은 객체여야 합니다.")
    if value is not None:
        parse.check_name(value, expr.columns, "값 컬럼")
        if how in ("sum", "mean") and not expr[value].type().is_numeric():
            raise ValueError("합계·평균은 숫자 컬럼에만 됩니다.")
    elif sql_funcs.needs_column(how):
        raise ValueError("값 컬럼을 고르세요(개수는 비워도 됩니다).")
    cond = parse.predicate(spec["where"], expr) if (spec.get("where") or "").strip() else None
    total = f"전체 {sql_funcs.label_of(how)}"
    aggs = {total: metric(expr, how, value, cond, opts)}
    if column is not None:
        parse.check_name(column, expr.columns, "열로 펼칠 컬럼")
        found = values_of(expr.filter(expr[column].notnull()).select(v=expr[column]).distinct().order_by("v").limit(MAX_PIVOT + 1))
        if len(found) > MAX_PIVOT:
            raise ValueError(f"'{column}'의 값이 {MAX_PIVOT}개보다 많아 열로 펼칠 수 없습니다. 조건으로 줄이거나 다른 컬럼을 고르세요.")
        for v in found:
            name = str(v)
            if name in aggs or name in rows:
                name = f"{column}={v}"
            is_v = expr[column] == v
            aggs[name] = metric(expr, how, value, is_v if cond is None else is_v & cond, opts)
    subtotals = spec.get("subtotals")
    if subtotals and (subtotals not in SUBTOTALS or not rows):
        raise ValueError(f"소계는 행 기준 컬럼이 있어야 하고 {', '.join(SUBTOTALS)} 중 하나입니다.")
    if subtotals:  # 소계·총계 줄: 기준 조합마다 집계해 잇는다(기준 칸은 글자, 빠진 칸은 '(소계)'·'(전체)')
        table = sql_funcs.grouping(expr, rows, aggs, sql_funcs.group_sets("rollup" if subtotals == "rollup" else "cube" if subtotals == "cube" else "grouping_sets", rows, spec.get("sets")))
        table = table.order_by(sql_funcs.grouped_order(table, rows))
    else:
        table = expr.group_by(rows).aggregate(**aggs).order_by(rows) if rows else expr.aggregate(**aggs)
    if (spec.get("having") or "").strip():  # HAVING: 집계한 뒤 결과 컬럼으로 거른다
        table = having(table, spec["having"])
    if (spec.get("order") or "").strip():  # 결과 컬럼 순서('전체 개수 desc'), top이 있으면 앞 N행만(많은 순 상위 N)
        key, _, way = spec["order"].strip().rpartition(" ")
        key, way = (key, way.lower()) if way.lower() in ("asc", "desc") else (spec["order"].strip(), "asc")
        parse.check_name(key, table.columns, "정렬 컬럼")
        table = table.order_by(table[key].desc() if way == "desc" else table[key].asc())
    if spec.get("top"):
        table = table.limit(min(parse.whole_number(spec["top"], 1, MAX_ROWS, f"상위 N은 1 이상 {MAX_ROWS} 이하의 정수예요."), MAX_ROWS))
    return table.limit(MAX_ROWS + 1)
