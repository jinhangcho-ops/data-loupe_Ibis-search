"""전처리의 새 컬럼: 계산 컬럼(formulas)과 PCA(차원 축소). recipe의 컬럼별 일 뒤에 붙는다(prep.shape).
계산 식은 실행하지 않고 Python ast로 읽어 정해 둔 연산·함수만 Ibis 식으로 바꾼다(SQL 글자를 받지 않는다).
PCA는 고른 숫자 컬럼의 평균, 평균을 뺀 곱의 평균(집계 두 번)으로 상관 행렬을 만들어 numpy로 성분을 구하고, 성분 = Σ 계수 × 표준화한 컬럼 식으로 붙인다."""
import ast
import operator

from lazy import ibis

from core import parse
from core import prep_ops
from core import source
from core import sql_funcs
from core.prep_ops import stats

MAX_FORMULAS = 50  # 계산 컬럼 수
MAX_TEXT = 500  # 식 하나의 글자 수
MAX_NODES = 200  # 식 하나의 요소 수(너무 복잡한 식을 막는다)
PCA_MAX = 30  # PCA에 넣을 컬럼 수(상관 행렬을 구하는 집계가 컬럼 수의 제곱만큼 는다)
NAME = sql_funcs.NAME  # 새 컬럼 이름
FORMULA_GUIDE = ("사칙연산(+ - * / %)과 괄호, 비교(== != > >= < <=)와 and·or·not, '글자', 숫자. 함수: iif(조건, 참일 때, 거짓일 때), "
                 "round(x, 자리), abs, sqrt, ln, log10, upper, lower, length, concat(a, b, …), coalesce(a, b, …), col(\"공백 있는 이름\")."
                 " 행 단위: row_sum·row_mean·row_std·row_min·row_max·row_count(빈 값 아닌 수)·row_positive(0보다 큰 수)(a, b, …), row_argmax·row_argmin(컬럼들 중 가장 큰·작은 컬럼 이름), row_dist·row_cosine(a1, a2, b1, b2: 앞 절반과 뒤 절반의 거리·코사인 유사도, 뒤 절반은 수도 됨). "
                 "나누는 값이 0이면, 로그가 0 이하이면, 제곱근이 음수이면 빈 값. 그 밖의 함수는 함수 사전(sql_funcs)의 id를 그대로 쓴다: 글자(substr·split_part·regex_extract …), 숫자(mod·power·clip·bucket …), "
                 "날짜(year·month·date_add·date_diff·strftime …), 조건(case·nullif·greatest·isin …), 형 변환(cast·try_cast), 구조(json_get·struct_field …), "
                 "집계(median·sum·string_agg …: 묶음 기준 안의 값)과 창 함수(rank·lag·cum_sum·moving_mean …: 정렬 기준·묶음 기준 필요). "
                 "예: 금액 * 수량, iif(금액 >= 10000, '고액', '일반'), substr(이름, 1, 2), case(금액 >= 100, '고액', 금액 >= 10, '중간', '소액')")

ARITH = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Mod: operator.mod}
COMPARE = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Lt: operator.lt, ast.LtE: operator.le}
NUMERIC_FUNCS = {"abs": lambda x: x.abs(), "sqrt": lambda x: sql_funcs.safe_math("sqrt", x), "ln": lambda x: sql_funcs.safe_math("ln", x),
                 "log10": lambda x: sql_funcs.safe_math("log10", x)}  # 정의역 밖(로그 0 이하·제곱근 음수)은 빈 값
STRING_FUNCS = {"upper": lambda x: x.upper(), "lower": lambda x: x.lower(), "length": lambda x: x.length().cast("int64")}


def is_number(value):
    return isinstance(value, ibis.expr.types.NumericValue)


def is_text(value):
    return isinstance(value, ibis.expr.types.StringValue)


class Formula:
    """식 글자 하나를 테이블 t의 Ibis 식으로. 허용하지 않는 것은 ValueError(어디가 안 되는지)."""

    def __init__(self, t, text, order=None, group=None):
        self.t, self.text, self.order, self.group = t, text, order or [], group or []

    def build(self):
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("식을 입력하세요.")
        if len(self.text) > MAX_TEXT:
            raise ValueError(f"식은 {MAX_TEXT}자까지 쓸 수 있어요.")
        try:
            tree = ast.parse(self.text.strip(), mode="eval")
        except SyntaxError as e:
            raise ValueError(f"식을 읽을 수 없어요({e.offset}번째 글자 근처). {FORMULA_GUIDE}") from None
        if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
            raise ValueError("식이 너무 복잡해요. 여러 계산 컬럼으로 나누세요.")
        value = self.node(tree.body)
        if not isinstance(value, ibis.expr.types.Value):
            value = ibis.literal(value)
        return value

    def fail(self, what):
        raise ValueError(f"식에 쓸 수 없는 것이 있어요: {what}. {FORMULA_GUIDE}")

    def column(self, name):
        parse.check_name(name, self.t.columns)
        return self.t[name]

    def node(self, n):
        if isinstance(n, ast.Constant):
            if n.value is None or isinstance(n.value, (bool, int, float, str)):
                return ibis.null() if n.value is None else ibis.literal(n.value)
            self.fail(repr(n.value))
        if isinstance(n, ast.Name):
            return self.column(n.id)
        if isinstance(n, ast.UnaryOp):
            value = self.node(n.operand)
            if isinstance(n.op, ast.Not):
                return ~self.boolean(value)
            if isinstance(n.op, (ast.USub, ast.UAdd)) and is_number(value):
                return -value if isinstance(n.op, ast.USub) else value
            self.fail("부호는 숫자에만")
        if isinstance(n, ast.BinOp):
            return self.binary(n)
        if isinstance(n, ast.BoolOp):
            parts = [self.boolean(self.node(v)) for v in n.values]
            out = parts[0]
            for part in parts[1:]:
                out = (out & part) if isinstance(n.op, ast.And) else (out | part)
            return out
        if isinstance(n, ast.Compare):
            left, out = self.node(n.left), None
            for op, right_node in zip(n.ops, n.comparators):
                if type(op) not in COMPARE:
                    self.fail("in·is 같은 비교")
                right = self.node(right_node)
                part = self.checked(lambda: COMPARE[type(op)](left, right), "서로 비교할 수 없는 값")
                out, left = part if out is None else out & part, right
            return out
        if isinstance(n, ast.Call):
            return self.call(n)
        self.fail(type(n).__name__)

    def boolean(self, value):
        if not isinstance(value, ibis.expr.types.BooleanValue):
            self.fail("and·or·not에는 참거짓(비교) 값만")
        return value

    def checked(self, make, what):
        try:
            return make()
        except Exception:  # Ibis가 타입이 맞지 않는 연산을 거부할 때(숫자 + 글자 등)
            self.fail(what)

    def binary(self, n):
        left, right = self.node(n.left), self.node(n.right)
        if isinstance(n.op, ast.Add) and is_text(left) and is_text(right):
            return left.concat(right)
        if not (is_number(left) and is_number(right)):
            self.fail("사칙연산은 숫자끼리만(글자 잇기는 + 또는 concat)")
        if isinstance(n.op, ast.Div):
            return (right == 0).ifelse(ibis.null(), left / right)  # 0으로 나누면 빈 값(DB마다 오류·무한대로 다르다)
        if isinstance(n.op, ast.Mod):
            if getattr(right.op(), "value", None) == 0:  # 0을 직접 쓰면 Ibis가 식을 만들다 영어 오류를 낸다
                self.fail("% 0: 0으로 나눈 나머지는 구할 수 없어요")
            return sql_funcs.remainder(left, right)
        if type(n.op) not in ARITH:
            self.fail("**·// 같은 연산")
        return ARITH[type(n.op)](left, right)

    def call(self, n):
        if not isinstance(n.func, ast.Name) or n.keywords:
            self.fail("함수는 이름(값, …) 모양으로만")
        name, args = n.func.id, n.args
        if name == "col":
            if len(args) != 1 or not isinstance(args[0], ast.Constant) or not isinstance(args[0].value, str):
                self.fail('col("컬럼 이름")')
            return self.column(args[0].value)
        values = [None if isinstance(a, (ast.List, ast.Tuple)) else self.node(a) for a in args]  # [1, 2, 3] 같은 목록은 상수 칸에서 읽는다
        if name == "iif":
            if len(values) != 3:
                self.fail("iif(조건, 참일 때, 거짓일 때)")
            condition = self.boolean(values[0])
            return self.checked(lambda: condition.ifelse(values[1], values[2]), "iif의 두 값은 같은 종류(숫자끼리·글자끼리)")
        if name == "round":
            if len(values) not in (1, 2) or not is_number(values[0]) or (len(args) == 2 and not (isinstance(args[1], ast.Constant) and type(args[1].value) is int)):
                self.fail("round(숫자, 자리 수)")
            return sql_funcs.half_up(values[0], args[1].value if len(args) == 2 else 0)
        if name in NUMERIC_FUNCS or name in STRING_FUNCS:
            numeric = name in NUMERIC_FUNCS
            if len(values) != 1 or not (is_number(values[0]) if numeric else is_text(values[0])):
                self.fail(f"{name}(하나의 {'숫자' if numeric else '글자'})")
            return (NUMERIC_FUNCS if numeric else STRING_FUNCS)[name](values[0])
        if name == "concat":
            if len(values) < 2:
                self.fail("concat(글자, 글자, …)")
            return sql_funcs.concat_parts(values, "")  # 함수 사전의 concat과 같게 빈 값은 건너뛴다
        if name == "coalesce":
            if len(values) < 2:
                self.fail("coalesce(값, 값, …)")
            return self.checked(lambda: ibis.coalesce(*values), "coalesce의 값은 같은 종류")
        if name == "case":
            return self.case(values)
        if name in ROW_FUNCS + ROW_PICKS + ROW_DISTS:
            if not 2 <= len(values) <= ROW_MAX or not all(is_number(v) for v in values):
                self.fail(f"{name}(숫자, 숫자, …: {ROW_MAX}개까지)")
            if name in ROW_PICKS:
                if not all(isinstance(a, ast.Name) or (isinstance(a, ast.Call) and getattr(a.func, "id", "") == "col") for a in args):
                    self.fail(f"{name}(컬럼, 컬럼, …): 컬럼 이름만")
                return row_pick(values, [v.get_name() for v in values], name == "row_argmax")
            if name in ROW_DISTS:
                if len(values) % 2:
                    self.fail(f"{name}(a1, a2, …, b1, b2, …): 앞 절반과 뒤 절반이 같은 수")
                return row_distance(values, "euclid" if name == "row_dist" else "cosine")
            return row_stat(name[len("row_"):], values)
        if name in sql_funcs.FUNCS or name in sql_funcs.ALIASES:
            return self.catalog(name, args, values)
        self.fail(f"{name}() 함수")

    def case(self, values):
        """case(조건1, 값1, 조건2, 값2, …, 아니면): 조건은 참거짓, 마지막이 홀로 남으면 '아니면' 값."""
        if len(values) < 2:
            self.fail("case(조건, 값, …, 아니면)")
        pairs = [(self.boolean(values[i]), values[i + 1]) for i in range(0, len(values) - 1, 2)]
        return self.checked(lambda: ibis.cases(*pairs, else_=values[-1] if len(values) % 2 else None), "case의 값은 같은 종류(숫자끼리·글자끼리)")

    def constant(self, node, what):
        """숫자·글자(음수 포함) 상수 하나. 컬럼이나 계산식이면 안 된다."""
        try:
            value = ast.literal_eval(node)
        except ValueError:
            value = None
        if not isinstance(value, (int, float, str)) or isinstance(value, bool):
            self.fail(f"{what}에는 숫자나 글자를 직접 쓰세요")
        return value

    def constants(self, nodes, what):
        """목록 칸의 상수들: 인자를 늘어놓거나 [1, 2, 3] 한 덩어리로."""
        if len(nodes) == 1 and isinstance(nodes[0], (ast.List, ast.Tuple)):
            nodes = nodes[0].elts
        return [self.constant(n, what) for n in nodes]

    def catalog(self, name, args, values):
        """함수 사전(sql_funcs)의 함수 호출. 인자는 사전의 칸 차례(조건·정렬·묶음 칸 제외)대로이고, 컬럼 칸은 식, 숫자·글자 칸은 상수, 목록 칸은 남은 상수들이다.
        집계는 묶음 기준(group) 안의 값(없으면 표 전체), 창 함수는 정렬 기준(order)·묶음 기준(group)으로 계산한다(계산 컬럼의 order·group 칸)."""
        f = sql_funcs.get(name)
        if f["table"]:
            self.fail(f"{name}()은 표를 바꾸는 함수라 계산 컬럼에 쓸 수 없어요")
        specs = [a for a in f["args"] if a["name"] not in sql_funcs.HAS_ARGS]
        raw, at = {}, 0
        for spec in specs:
            if at >= len(args):
                break
            kind = spec["kind"]
            if kind == "list" and isinstance(args[at], (ast.List, ast.Tuple)):  # [10, 20] 한 덩어리면 그것만(다음 목록 칸을 이어 쓸 수 있게: bucket(x, [10, 20], ['낮음', '중간', '높음']))
                raw[spec["name"]] = self.constants([args[at]], spec["label"])
                at += 1
            elif kind in ("columns", "list"):
                raw[spec["name"]] = values[at:] if kind == "columns" else self.constants(args[at:], spec["label"])
                at = len(args)
            elif kind in ("column", "cond"):
                raw[spec["name"]] = values[at]
                at += 1
            elif kind == "value" and not isinstance(args[at], ast.Constant):
                raw[spec["name"]] = values[at]
                at += 1
            else:
                raw[spec["name"]] = self.constant(args[at], spec["label"])
                at += 1
        if at < len(args):
            self.fail(f"{name}()의 인자가 너무 많아요. 쓰는 인자: {', '.join(s['label'] for s in specs)}")
        if any(a["name"] == "order" for a in f["args"]) and self.order:
            raw["order"] = self.order
        if any(a["name"] == "group" for a in f["args"]) and self.group:
            raw["group"] = self.group
        try:
            value = f["build"](sql_funcs.resolve(f, self.t, raw))
        except ValueError as e:
            raise ValueError(f"{name}(): {e}") from None
        if f["aggregate"]:  # 묶음 기준 안의 값을 행마다(묶음 기준이 없으면 표 전체의 값)
            value = value.over(ibis.window(group_by=[self.t[g] for g in self.group] or None))
        return value


def check_name(name, t, what):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError(f"{what} 이름은 영문·한글·숫자·밑줄로 쓰고 글자로 시작해야 해요(63자까지).")
    if name.lower() in {c.lower() for c in t.columns}:  # 대소문자만 달라도 같은 컬럼(DuckDB·SQLite·SQL Server)
        raise ValueError(f"'{name}' 컬럼이 이미 있어 {what}을 만들 수 없어요.")


def apply_formulas(t, formulas, schema=None):
    """formulas([{name, expr}] 또는 계산 줄 {line}: 쿼리 계산 줄 문법(그룹집계·창 함수·전처리·식, calc.apply_line))를 차례로 붙인다.
    뒤의 식은 앞에서 만든 계산 컬럼을 쓸 수 있다. (식, 만든 컬럼 이름들)"""
    if not isinstance(formulas, list) or len(formulas) > MAX_FORMULAS:
        raise ValueError(f"계산 컬럼은 {MAX_FORMULAS}개까지의 목록이어야 해요.")
    made = []
    for item in formulas:
        if isinstance(item, dict) and set(item) == {"line"} and isinstance(item["line"], str):
            from core import calc  # calc가 이 모듈을 부른다
            before = set(t.columns)
            try:
                step = calc.stat_step(item["line"], t)  # 통계 단계면 미리 구해 상수로(부르는 쪽이 연결 스레드: prep_app.built)
                t = calc.apply_line(item["line"], t, schema, None if step is None else prep_ops.pin_value(t, step[1], step[0], step[2]))  # 그룹집계 '테이블 대상'은 구성의 스키마에서
            except ValueError as e:
                raise ValueError(f"계산 줄 '{item['line'][:60]}': {e}") from None
            made += [c for c in t.columns if c not in before]
            continue
        if not isinstance(item, dict) or not {"name", "expr"} <= set(item) <= {"name", "expr", "order", "group"}:
            raise ValueError('계산 컬럼은 {"name": "새 컬럼", "expr": "식", "order": ["정렬 기준 [desc]"], "group": ["묶음 기준"]} 모양이어야 해요(order·group은 선택). 계산 줄은 {"line": "그룹집계 …"}.')
        for key in ("order", "group"):
            if not isinstance(item.get(key, []), list) or not all(isinstance(v, str) for v in item.get(key, [])):
                raise ValueError(f"계산 컬럼의 {key}은 컬럼 이름(글자) 목록이어야 해요.")
        check_name(item["name"], t, "계산 컬럼")
        try:
            for g in item.get("group", []):
                parse.check_name(g, t.columns, "묶음 기준")
            value = Formula(t, item["expr"], [parse.parse_sort(line, t) for line in item.get("order", [])], item.get("group", [])).build()
        except ValueError as e:
            raise ValueError(f"계산 컬럼 '{item['name']}': {e}") from None
        t = t.mutate(**{item["name"]: value})
        made.append(item["name"])
    return t, made


def pca(t, spec, info):
    """spec = {columns, n, prefix}. 고른 숫자 컬럼이 모두 있는 행으로 평균·표준편차·상관 행렬을 구하고(집계 두 번),
    상관 행렬의 고유벡터로 prefix_1…prefix_n 컬럼을 붙인다. 고른 컬럼 중 빈 값이 있는 행은 성분도 빈 값.
    info["pca"]에 성분마다 설명된 분산 비율·누적 비율·계수와 계산에 쓴 행 수를 남긴다. (식, 만든 컬럼 이름들)"""
    import numpy as np
    if not isinstance(spec, dict) or not set(spec) <= {"columns", "n", "prefix"}:
        raise ValueError('PCA는 {"columns": [숫자 컬럼들], "n": 성분 수, "prefix": "pca"} 모양이어야 해요.')
    columns, n, prefix = spec.get("columns"), spec.get("n"), spec.get("prefix", "pca")
    if not isinstance(columns, list) or not 2 <= len(columns) <= PCA_MAX or len(set(columns)) != len(columns):
        raise ValueError(f"PCA에는 서로 다른 숫자 컬럼을 2~{PCA_MAX}개 고르세요.")
    for name in columns:
        parse.check_name(name, t.columns)
        if not t[name].type().is_numeric() or t[name].type().is_boolean():
            raise ValueError(f"'{name}' 컬럼은 숫자가 아니라 PCA에 넣을 수 없어요. 먼저 숫자로 바꾸세요.")
    if type(n) is not int or not 1 <= n <= len(columns):
        raise ValueError(f"성분 수는 1~{len(columns)} 사이의 정수로 입력하세요.")
    names = [f"{prefix}_{k + 1}" for k in range(n)]
    for name in names:
        check_name(name, t, "PCA 성분")
    x = [prep_ops.nan_to_null(t[c].cast("float64")) for c in columns]  # 진짜 NaN은 빈 값으로(그 행은 성분도 빈 값)
    whole = t.filter(*[v.notnull() for v in x])
    xs = [whole[c].cast("float64") for c in columns]
    k = len(columns)
    found = stats(whole, rows=whole.count(), **{f"m{i}": xs[i].mean() for i in range(k)})  # 집계 두 번: 평균을 먼저 구한다
    rows = found["rows"] or 0
    if rows < 2:
        raise ValueError("고른 컬럼이 모두 들어 있는 행이 2개보다 적어 PCA를 구할 수 없어요. 빈 값을 먼저 채우세요.")
    mean = np.array([found[f"m{i}"] for i in range(k)], dtype=float)
    gap = [xs[i] - float(mean[i]) for i in range(k)]  # 평균을 뺀 곱의 평균 = 공분산(값이 평균에 비해 작게 흩어져도 자릿수를 잃지 않는다). 모든 연결에서 되는 집계만 쓴다
    found = stats(whole, **{f"p{i}_{j}": (gap[i] * gap[j]).mean() for i in range(k) for j in range(i, k)})
    cov = np.empty((k, k))
    for i in range(k):
        for j in range(i, k):
            cov[i, j] = cov[j, i] = found[f"p{i}_{j}"]
    std = np.sqrt(np.clip(np.diag(cov), 0, None))
    flat = [columns[i] for i in range(k) if not std[i] > 1e-12 * max(1.0, abs(mean[i]))]
    if flat:
        raise ValueError(f"값이 모두 같은 컬럼은 PCA에 넣을 수 없어요: {', '.join(flat)}")
    values, vectors = np.linalg.eigh(cov / np.outer(std, std))  # 상관 행렬(표준화한 컬럼의 공분산)
    order = np.argsort(values)[::-1]
    values, vectors = np.clip(values[order], 0, None), vectors[:, order]
    vectors *= np.where(vectors[np.abs(vectors).argmax(axis=0), range(k)] < 0, -1, 1)  # 부호를 정한다: 계수의 절댓값이 가장 큰 것이 양수
    ratio = values / values.sum()
    scaled = [(x[i] - float(mean[i])) / float(std[i]) for i in range(k)]
    made = {}
    for c, name in enumerate(names):
        made[name] = sum((scaled[i] * float(vectors[i, c]) for i in range(1, k)), scaled[0] * float(vectors[0, c]))
    info["pca"] = {"columns": columns, "names": names, "rows": int(rows), "ratio": [round(float(r), 6) for r in ratio[:n]],
                   "cumulative": [round(float(r), 6) for r in np.cumsum(ratio)[:n]],
                   "weights": {name: {columns[i]: round(float(vectors[i, c]), 6) for i in range(k)} for c, name in enumerate(names)}}
    return prep_ops.add_columns(t, made), names


# ---- Phase 6: 행 단위 압축·거리 함수(계산 식의 row_*)와 K-Means(plans/phase-6.md) ----
ROW_FUNCS = ("row_sum", "row_mean", "row_std", "row_min", "row_max", "row_count", "row_positive")  # 행 단위 통계(빈 값은 건너뜀)
ROW_PICKS = ("row_argmax", "row_argmin")  # 가장 크거나 작은 값을 가진 컬럼의 이름
ROW_DISTS = ("row_dist", "row_cosine")  # 앞 절반 컬럼(값)과 뒤 절반의 유클리드 거리·코사인 유사도
ROW_MAX = 50  # row_* 함수 하나에 넣을 값 수(사용자 결정)
KMEANS_K = 3  # 군집 수 기본(사용자 결정)
KMEANS_NAME = "cluster"  # 군집 번호 컬럼 이름 기본(사용자 결정)
KMEANS_MAX_K = 10  # 군집 수 위(배정 식이 k²에 비례해 커진다)
KMEANS_MAX_COLS = 10  # 넣을 컬럼 수 위(사용자 결정)
KMEANS_ITERS = 20  # 반복(표 집계) 최대 횟수(사용자 결정)
KMEANS_TOL = 1e-4  # 표준화한 중심이 이보다 덜 움직이면 멈춘다(사용자 결정)
KMEANS_SEED = 0  # k-means++ 첫 중심 고르기의 난수 씨앗(같은 표면 같은 결과)
KMEANS_INIT_ROWS = 1000  # 첫 중심을 고를 앞 행 수(사용자 결정)
KMEANS_DIGITS = 6  # info의 중심 값 반올림 자리


def row_stat(kind: str, values: "list[ibis.expr.types.NumericValue]") -> "ibis.expr.types.NumericValue":
    """[SPECIFICATION] plans/phase-6.md#row_stat
    여러 숫자 값의 행 단위 합·평균·표준편차·최소·최대·빈 값 아닌 수·양수 수를 빈 값을 건너뛰고 구하는 식을 돌려준다."""
    cnt = sum((v.isnull().ifelse(0, 1) for v in values), ibis.literal(0)).cast("int64")
    if kind == "count":
        return cnt
    if kind == "positive":
        return sum((ibis.coalesce(v > 0, False).cast("int64") for v in values), ibis.literal(0)).cast("int64")
    if kind in ("min", "max"):
        first = ibis.coalesce(*values)
        return (ibis.least if kind == "min" else ibis.greatest)(*[ibis.coalesce(v, first) for v in values])
    tot = sum((ibis.coalesce(v, 0) for v in values), ibis.literal(0))
    if kind == "sum":
        return (cnt == 0).ifelse(ibis.null(), tot)
    mean = (cnt == 0).ifelse(ibis.null("float64"), tot.cast("float64") / cnt)
    if kind == "mean":
        return mean
    ss = sum((v.isnull().ifelse(0.0, (v - mean) * (v - mean)) for v in values), ibis.literal(0.0))
    return (cnt < 2).ifelse(ibis.null("float64"), (ss / (cnt - 1)).sqrt())


def row_pick(values: "list[ibis.expr.types.NumericValue]", names: list[str], largest: bool) -> "ibis.expr.types.StringValue":
    """[SPECIFICATION] plans/phase-6.md#row_pick
    행마다 가장 큰(작은) 값을 가진 컬럼의 이름을 돌려주는 식(같으면 앞 컬럼)."""
    first = ibis.coalesce(*values)
    xs = [ibis.coalesce(v, first) for v in values]
    best = (ibis.greatest if largest else ibis.least)(*xs)
    return ibis.cases(*[(v == best, n) for v, n in zip(values, names)], else_=ibis.null("string"))  # 빈 값을 메운 xs가 아니라 원래 값과 비교: 빈 컬럼은 고르지 않는다


def row_distance(values: "list[ibis.expr.types.NumericValue]", kind: str) -> "ibis.expr.types.NumericValue":
    """[SPECIFICATION] plans/phase-6.md#row_distance
    앞 절반과 뒤 절반 값을 두 벡터로 보고 유클리드 거리(euclid) 또는 코사인 유사도(cosine) 식을 돌려준다."""
    half = len(values) // 2
    a, b = [v.cast("float64") for v in values[:half]], [v.cast("float64") for v in values[half:]]
    if kind == "euclid":
        return sum(((x - y) * (x - y) for x, y in zip(a, b)), ibis.literal(0.0)).sqrt()
    dot = sum((x * y for x, y in zip(a, b)), ibis.literal(0.0))
    na = sum((x * x for x in a), ibis.literal(0.0))
    nb = sum((y * y for y in b), ibis.literal(0.0))
    return ((na == 0) | (nb == 0)).ifelse(ibis.null("float64"), dot / (na.sqrt() * nb.sqrt()))


def nearest(values: "list[ibis.expr.types.NumericValue]", centers: list[list[float]]) -> "ibis.expr.types.IntegerValue":
    """[SPECIFICATION] plans/phase-6.md#nearest
    값 벡터에 가장 가까운(제곱 거리) 중심의 번호(0부터)를 돌려주는 식."""
    d = [sum(((v - c) * (v - c) for v, c in zip(values, center)), ibis.literal(0.0)) for center in centers]
    m = ibis.least(*d)
    return ibis.cases(*[(dj == m, j) for j, dj in enumerate(d)], else_=ibis.null("int64")).cast("int64")


def kmeans(t: "ibis.expr.types.Table", spec: dict, info: dict) -> "tuple[ibis.expr.types.Table, list[str]]":
    """[SPECIFICATION] plans/phase-6.md#kmeans
    고른 숫자 컬럼을 표준화해 표 집계로 Lloyd 반복을 돌려 K-Means 군집 번호 컬럼을 붙인다(PC에는 중심만)."""
    import numpy as np
    if not isinstance(spec, dict) or not set(spec) <= {"columns", "k", "name"}:
        raise ValueError('K-Means는 {"columns": [숫자 컬럼들], "k": 군집 수, "name": "cluster"} 모양이어야 해요.')
    columns = spec.get("columns")
    if not isinstance(columns, list) or not 2 <= len(columns) <= KMEANS_MAX_COLS or len(set(columns)) != len(columns):
        raise ValueError(f"K-Means에는 서로 다른 숫자 컬럼을 2~{KMEANS_MAX_COLS}개 고르세요.")
    for c in columns:
        parse.check_name(c, t.columns)
        if not t[c].type().is_numeric() or t[c].type().is_boolean():
            raise ValueError(f"'{c}' 컬럼은 숫자가 아니라 PCA에 넣을 수 없어요. 먼저 숫자로 바꾸세요.")
    k = spec.get("k", KMEANS_K)
    if type(k) is not int or not 2 <= k <= KMEANS_MAX_K:
        raise ValueError(f"군집 수는 2~{KMEANS_MAX_K} 사이의 정수로 입력하세요.")
    name = spec.get("name", KMEANS_NAME)
    check_name(name, t, "군집 번호 컬럼")
    n = len(columns)
    x = [prep_ops.nan_to_null(t[c].cast("float64")) for c in columns]
    whole = t.filter(*[v.notnull() for v in x])
    xs = [whole[c].cast("float64") for c in columns]
    found = stats(whole, rows=whole.count(), **{f"m{i}": xs[i].mean() for i in range(n)}, **{f"s{i}": xs[i].std() for i in range(n)})
    rows = found["rows"] or 0
    if rows < k:
        raise ValueError(f"고른 컬럼이 모두 들어 있는 행이 {k}개보다 적어 K-Means를 할 수 없어요.")
    flat = [columns[i] for i in range(n) if not found[f"s{i}"]]
    if flat:
        raise ValueError(f"값이 모두 같은 컬럼은 K-Means에 넣을 수 없어요: {', '.join(flat)}")
    mean = [float(found[f"m{i}"]) for i in range(n)]
    std = [float(found[f"s{i}"]) for i in range(n)]
    zn = [f"__km_z{i}" for i in range(n)]
    prep_ops.check_new(whole, [*zn, "__km"])
    w = whole.mutate(**{zn[i]: (xs[i] - mean[i]) / std[i] for i in range(n)})
    z = [w[c] for c in zn]
    head = w.select(*zn).limit(KMEANS_INIT_ROWS).to_polars().to_numpy()
    if len(np.unique(head, axis=0)) < k:
        raise ValueError(f"앞 {KMEANS_INIT_ROWS:,}행에 서로 다른 값 묶음이 {k}개보다 적어 첫 중심을 고를 수 없어요.")
    rng = np.random.default_rng(KMEANS_SEED)
    picked = [head[rng.integers(len(head))]]
    for _ in range(1, k):  # k-means++: 가장 가까운 중심까지 제곱 거리에 비례해 고른다
        d2 = ((head[:, None, :] - np.array(picked)[None, :, :]) ** 2).sum(axis=2).min(axis=1)
        picked.append(head[rng.choice(len(head), p=d2 / d2.sum())])
    centers = np.array(picked, dtype=float)
    sizes, converged, iters = np.zeros(k, dtype=int), False, 0
    for it in range(KMEANS_ITERS):
        a = w.mutate(__km=nearest(z, centers.tolist()))
        g = a.group_by("__km").aggregate(__n=a.count(), **{f"c{i}": a[zn[i]].mean() for i in range(n)})
        if it == 0 and not source.compiles(g):
            raise ValueError("이 연결은 K-Means 배정 식(가장 작은 값 고르기)을 계산하지 못해요. DuckDB 파일로 옮겨 쓰세요.")
        got = g.to_polars()
        idx = got["__km"].to_numpy()
        new = centers.copy()
        new[idx] = got.select([f"c{i}" for i in range(n)]).to_numpy()  # 행이 없는 군집은 이전 중심
        sizes = np.zeros(k, dtype=int)
        sizes[idx] = got["__n"].to_numpy()
        iters = it + 1
        moved = float(np.abs(new - centers).max())
        centers = new
        if moved < KMEANS_TOL:
            converged = True
            break
    scaled = [(x[i] - mean[i]) / std[i] for i in range(n)]
    value = ibis.cases((ibis.and_(*[v.notnull() for v in x]), nearest(scaled, centers.tolist())), else_=ibis.null("int64"))
    info["kmeans"] = {"columns": columns, "k": k, "name": name, "rows": int(rows), "iters": iters, "converged": converged,
                      "sizes": [int(s) for s in sizes],
                      "centers": [[round(float(centers[j, i]) * std[i] + mean[i], KMEANS_DIGITS) for i in range(n)] for j in range(k)]}
    return prep_ops.add_columns(t, {name: value}), [name]
