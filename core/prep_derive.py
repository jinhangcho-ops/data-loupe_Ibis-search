"""전처리의 새 컬럼: 계산 컬럼(formulas)과 PCA(차원 축소). recipe의 컬럼별 일 뒤에 붙는다(prep.shape).
계산 식은 실행하지 않고 Python ast로 읽어 정해 둔 연산·함수만 Ibis 식으로 바꾼다(SQL 글자를 받지 않는다).
PCA는 고른 숫자 컬럼의 평균, 평균을 뺀 곱의 평균(집계 두 번)으로 상관 행렬을 만들어 numpy로 성분을 구하고, 성분 = Σ 계수 × 표준화한 컬럼 식으로 붙인다."""
import ast
import operator

from lazy import ibis

from core import parse
from core import prep_ops
from core import sql_funcs
from core.prep_ops import stats

MAX_FORMULAS = 50  # 계산 컬럼 수
MAX_TEXT = 500  # 식 하나의 글자 수
MAX_NODES = 200  # 식 하나의 요소 수(너무 복잡한 식을 막는다)
PCA_MAX = 30  # PCA에 넣을 컬럼 수(상관 행렬을 구하는 집계가 컬럼 수의 제곱만큼 는다)
NAME = sql_funcs.NAME  # 새 컬럼 이름
FORMULA_GUIDE = ("사칙연산(+ - * / %)과 괄호, 비교(== != > >= < <=)와 and·or·not, '글자', 숫자. 함수: iif(조건, 참일 때, 거짓일 때), "
                 "round(x, 자리), abs, sqrt, ln, log10, upper, lower, length, concat(a, b, …), coalesce(a, b, …), col(\"공백 있는 이름\"). "
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
            raise ValueError(f"식은 {MAX_TEXT}자까지 쓸 수 있습니다.")
        try:
            tree = ast.parse(self.text.strip(), mode="eval")
        except SyntaxError as e:
            raise ValueError(f"식을 읽을 수 없습니다({e.offset}번째 글자 근처). {FORMULA_GUIDE}") from None
        if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
            raise ValueError("식이 너무 복잡합니다. 여러 계산 컬럼으로 나누세요.")
        value = self.node(tree.body)
        if not isinstance(value, ibis.expr.types.Value):
            value = ibis.literal(value)
        return value

    def fail(self, what):
        raise ValueError(f"식에 쓸 수 없는 것이 있습니다: {what}. {FORMULA_GUIDE}")

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
            if kind in ("columns", "list"):
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
    if not isinstance(name, str) or not NAME.fullmatch(name) or len(name.encode("utf-8")) > 63:  # PostgreSQL은 63바이트를 넘는 이름을 말없이 자른다
        raise ValueError(f"{what} 이름은 영문·한글·숫자·밑줄로 쓰고 글자로 시작해야 합니다(영문 63자, 한글 21자까지).")
    if name.lower() in {c.lower() for c in t.columns}:  # 대소문자만 달라도 같은 컬럼(DuckDB·SQLite·SQL Server)
        raise ValueError(f"'{name}' 컬럼이 이미 있어 {what}을 만들 수 없습니다.")


def apply_formulas(t, formulas):
    """formulas([{name, expr}])를 차례로 붙인다. 뒤의 식은 앞에서 만든 계산 컬럼을 쓸 수 있다. (식, 만든 컬럼 이름들)"""
    if not isinstance(formulas, list) or len(formulas) > MAX_FORMULAS:
        raise ValueError(f"계산 컬럼은 {MAX_FORMULAS}개까지의 목록이어야 합니다.")
    made = []
    for item in formulas:
        if not isinstance(item, dict) or not {"name", "expr"} <= set(item) <= {"name", "expr", "order", "group"}:
            raise ValueError('계산 컬럼은 {"name": "새 컬럼", "expr": "식", "order": ["정렬 기준 [desc]"], "group": ["묶음 기준"]} 모양이어야 합니다(order·group은 선택).')
        for key in ("order", "group"):
            if not isinstance(item.get(key, []), list) or not all(isinstance(v, str) for v in item.get(key, [])):
                raise ValueError(f"계산 컬럼의 {key}은 컬럼 이름(글자) 목록이어야 합니다.")
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
        raise ValueError('PCA는 {"columns": [숫자 컬럼들], "n": 성분 수, "prefix": "pca"} 모양이어야 합니다.')
    columns, n, prefix = spec.get("columns"), spec.get("n"), spec.get("prefix", "pca")
    if not isinstance(columns, list) or not 2 <= len(columns) <= PCA_MAX or len(set(columns)) != len(columns):
        raise ValueError(f"PCA에는 서로 다른 숫자 컬럼을 2~{PCA_MAX}개 고르세요.")
    for name in columns:
        parse.check_name(name, t.columns)
        if not t[name].type().is_numeric() or t[name].type().is_boolean():
            raise ValueError(f"'{name}' 컬럼은 숫자가 아니라 PCA에 넣을 수 없습니다. 먼저 숫자로 바꾸세요.")
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
        raise ValueError("고른 컬럼이 모두 들어 있는 행이 2개보다 적어 PCA를 구할 수 없습니다. 빈 값을 먼저 채우세요.")
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
        raise ValueError(f"값이 모두 같은 컬럼은 PCA에 넣을 수 없습니다: {', '.join(flat)}")
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
