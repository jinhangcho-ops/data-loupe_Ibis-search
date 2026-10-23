"""문장 검사와 만들기(6-4): sentences.py의 틀(데이터)에 빈칸 값을 넣어 문장을 검사(check)하고, 기존 라우트가 받는 요청으로 바꾼다(compile).
데이터를 읽지 않고 값이 밖으로 나가지 않는다(Python 규칙뿐). 어느 틀이든 앞에 {기간}을 붙일 수 있다(period → body.periods의 '날짜컬럼 시작 ~ 끝' 줄).
빈칸 값의 모양: column "이름" 또는 {column, prep(after_q·first_n·date_only·lower, first_n은 n)}(가공은 계산 줄 '식 …'로 바뀐다) · columns 목록 · value 값 하나 · values 목록 ·
text_match {match: 포함·으로 시작·으로 끝, text} · cond 조건 줄(탐색 조건 문법) · agg 함수 사전 집계 id(또는 {agg, opts}) · order "컬럼 [asc|desc]" · n 정수 · unit day·week·month·year ·
metric 지표 이름(지표 파일) · choice 고른 글자 · table 테이블 이름 · text 글자 · transform {func(함수 사전 행 함수 id), args}.
check(tid, slots, columns) → [{slot, level(bad·warn·info), text}] (bad면 실행하지 않는다). columns = {컬럼: 종류}.
compile(tid, slots, body, columns) → {ok, text, body, runs: [{label, route, body}], combine?, notes} 또는 {ok: False, reason}.
runs의 route는 /api/query(행)·/api/pivot(묶어 집계, calc.pivot의 order·top 포함)·metric(지표 값)·nested(대상마다 구한 값을 다시 집계)이다. 값 하나로 합쳐야 하면 combine {op: ratio·percent·share·sub·add·mean_of·change·cumsum, a, b}을 준다.
요청을 실제로 돌려 합치는 것은 sentence_exec(POST /api/sentence/run)가 한다 — 화면은 나누거나 합치지 않는다."""
import copy
import re

from query import flow
from core import metric_file
from core import parse
from query import sentences
from core import sql_funcs

PREPS = {"after_q": "? 뒤 자르기", "first_n": "앞 N글자", "date_only": "날짜만", "lower": "소문자로"}  # 컬럼 가공 옵션 → 계산 줄로
MAX_TOP = 50
TEXT_MATCH = {"포함": "like", "으로 시작": "starts", "으로 끝": "ends"}  # 조건 줄에 있는 글자 비교(parse.LIKE_RE)
SUBTOTAL_BY = {"소계·총계": "rollup", "모든 조합(CUBE)": "cube", "고른 조합(GROUPING SETS)": "sets"}
WORD = re.compile(r"\{([^{}]+)\}")
COMPARED = ("e03", "e04", "e12")  # 값 빈칸을 같은 문장의 {컬럼}과 견주는 틀(컬럼 종류로 읽히는지 본다)


class Bad(ValueError):
    """만들 수 없는 이유(화면에 그대로 보인다)."""


def name_of(v):
    """컬럼 빈칸 값에서 컬럼 이름."""
    return v.get("column") if isinstance(v, dict) else v


def shown(v):
    """빈칸 값을 문장에 넣을 글로."""
    if isinstance(v, dict):
        return str(v.get("column") or v.get("text") or v.get("func") or v.get("agg") or "")
    if isinstance(v, (list, tuple)):
        return ", ".join(shown(x) for x in v)
    return "" if v is None else str(v)


def render(tid, slots):
    """틀 문장의 빈칸을 값으로 채운 글(없는 칸은 {이름} 그대로)."""
    t = sentences.get(tid)
    return WORD.sub(lambda m: shown(slots.get(m.group(1))) or m.group(0), t["text"])


# ---------- 검사 ----------

def issue(slot, level, text):
    return {"slot": slot, "level": level, "text": text}


def convertible(kind, value):
    """값 글자가 컬럼 종류에 맞게 읽히는지(num: 수, date: ISO 날짜·시각)."""
    import datetime as dt
    text = str(value).strip().strip("'\"")
    if kind == "num":
        try:
            float(text)
            return True
        except ValueError:
            return False
    if kind == "date":
        try:
            dt.datetime.fromisoformat(text)
            return True
        except ValueError:
            return False
    if kind == "bool":
        return text.lower() in ("true", "false")
    return True


def check(tid, slots, columns=None):
    """문장 검사: 빈칸이 모두 찼는지, 컬럼이 있고 종류가 맞는지, 값이 컬럼 종류로 읽히는지, 범위·선택이 맞는지. bad가 있으면 실행하지 않는다."""
    t = sentences.get(tid)
    slots = slots or {}
    out = []
    for s in t["slots"]:
        if s["when"] and not all(slots.get(k) in v for k, v in s["when"].items()):
            continue  # 다른 칸의 값에 따라 나타나는 곁가지 칸
        name, raw = s["name"], slots.get(s["name"])
        if raw in (None, "", []):
            if not s["optional"] and s["default"] is None:
                out.append(issue(name, "bad", f"‘{s['label']}’ 칸을 채워 주세요."))
            continue
        kind = s["type"]
        if kind in ("column", "columns", "order"):
            for c in raw if isinstance(raw, list) else [raw]:
                col = (flow.head_name(name_of(c), columns) or name_of(c).split()[0]) if kind == "order" and isinstance(name_of(c), str) else name_of(c)
                if columns is not None and col not in columns:
                    out.append(issue(name, "bad", f"‘{col}’ 컬럼이 이 테이블에 없어요."))
                elif columns is not None and kind == "column" and not fits(s["kinds"], columns[col]):
                    out.append(issue(name, "bad", f"‘{col}’은 {flow.kind_text(columns[col])} 컬럼이라 쓸 수 없어요({sql_funcs.kinds_text(s['kinds'])} 컬럼이 필요해요)."))
                if isinstance(c, dict) and c.get("prep") not in (None, *PREPS):
                    out.append(issue(name, "bad", f"컬럼 가공은 {', '.join(PREPS.values())} 중 하나예요."))
        elif kind == "n":
            if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
                out.append(issue(name, "bad", f"‘{s['label']}’에는 1 이상의 정수를 넣어 주세요."))
            elif raw > MAX_TOP and "상위" in t["text"]:
                out.append(issue(name, "warn", f"{MAX_TOP}개까지만 보여 드려요."))
        elif kind in ("choice", "unit") and s["options"] and raw not in s["options"]:
            out.append(issue(name, "bad", f"‘{s['label']}’은 {', '.join(s['options'])} 중 하나예요."))
        elif kind == "agg":
            f = None
            try:
                f = sql_funcs.get(raw["agg"] if isinstance(raw, dict) else raw)
            except ValueError as e:
                out.append(issue(name, "bad", str(e)))
            if f and not f["aggregate"]:
                out.append(issue(name, "bad", f"‘{f['label']}’은(는) 집계 함수가 아니에요."))
            elif f and columns is not None:
                out += agg_vs_value(t, slots, f, columns, name)
        elif kind == "cond":
            for line in raw if isinstance(raw, list) else [raw]:
                for col in flow.cond_columns(line, columns):
                    if columns is not None and col not in columns:
                        out.append(issue(name, "bad", f"조건의 ‘{col}’ 컬럼이 이 테이블에 없어요."))
        elif kind in ("value", "values", "text_match") and columns is not None and (kind == "text_match" or tid in COMPARED):
            out += value_issues(t, slots, s, raw, columns)
        elif kind == "period" and not (isinstance(raw, str) or isinstance(raw, dict)):
            out.append(issue(name, "bad", "기간은 ‘날짜컬럼 시작 ~ 끝’ 글이나 {column, start, end}로 넣어 주세요."))
    if tid in ("e14", "e46") and (tid == "e46" or slots.get("정확도") == "빠르게(근사)"):
        out.append(issue("정확도", "info", "근사 값이에요. 정확한 값과 조금 다를 수 있어요."))
    if tid == "e47":
        out.append(issue("식", "info", "SQL 식은 읽기만 하는 식인지 실행 전에 한 번 더 검사해요."))
    return out


def fits(kinds, kind):
    return kinds in ("any", None) or kind in ([kinds] if isinstance(kinds, str) else kinds)


def agg_vs_value(t, slots, f, columns, name):
    """집계가 숫자 컬럼을 요구하는데 값 컬럼이 숫자가 아니면 bad."""
    for key in ("값", "컬럼"):
        col = name_of(slots.get(key))
        if f["kind_in"] == "num" and col in columns and columns[col] != "num":
            return [issue(name, "bad", f"‘{col}’은 {flow.kind_text(columns[col])} 컬럼이라 {f['label']}을(를) 구할 수 없어요. 숫자 컬럼을 고르거나 개수·고유 개수를 쓰세요.")]
    return []


def value_issues(t, slots, s, raw, columns):
    """값 빈칸이 같은 문장의 컬럼 종류로 읽히는지."""
    col = name_of(slots.get("컬럼"))
    kind = columns.get(col)
    if s["type"] == "text_match":
        return [issue(s["name"], "warn", "대소문자를 가리지 않고 찾아요.")] if (isinstance(raw, dict) and raw.get("match", "포함") == "포함") or isinstance(raw, str) else []
    if kind is None:
        return []
    bad = [v for v in (raw if isinstance(raw, list) else [raw]) if not convertible(kind, v)]
    return [issue(s["name"], "bad", f"‘{bad[0]}’은 {flow.kind_text(kind)} 값으로 읽을 수 없어요(‘{col}’은 {flow.kind_text(kind)} 컬럼이에요).")] if bad else []


# ---------- 만들기 ----------

class Plan:
    """문장 하나를 요청으로 바꾸는 동안의 상태: 바탕 body(조건·기간·계산을 더해 간다), 요청들, 메모."""

    def __init__(self, tid, slots, body, columns):
        self.tid, self.t, self.s = tid, sentences.get(tid), slots or {}
        self.body = copy.deepcopy(body or {})
        for k in ("joins", "periods", "conditions", "calcs"):
            self.body.setdefault(k, [])
        self.columns, self.runs, self.notes, self.combine, self.used = columns, [], [], None, set()

    def get(self, name, default=None):
        v = self.s.get(name)
        return default if v in (None, "", []) else v

    def need(self, name):
        v = self.get(name)
        if v is None:
            raise Bad(f"‘{name}’ 칸을 채워 주세요.")
        return v

    def col(self, name):
        """컬럼 빈칸 값의 컬럼 이름. 가공(prep)이 있으면 계산 줄을 더하고 새 컬럼 이름을 돌려준다."""
        v = self.need(name)
        if not isinstance(v, dict) or not v.get("prep"):
            return name_of(v)
        c, prep = v["column"], v["prep"]
        text = {"after_q": f"split_part(col({c!r}), '?', 1)", "first_n": f"left(col({c!r}), {int(v.get('n') or 1)})",
                "date_only": f"to_date(col({c!r}))", "lower": f"lower(col({c!r}))"}.get(prep)
        if text is None:
            raise Bad(f"컬럼 가공은 {', '.join(PREPS.values())} 중 하나예요.")
        new = re.sub(r"[\W_]+", "_", f"{c}_{prep}").strip("_")  # 공백·기호는 _로(새 컬럼 이름이 계산 줄에서 한 낱말이어야 한다)
        self.calc(f"식 {text} 이름 {new}")
        return new

    def cols(self, name):
        """컬럼 목록 빈칸의 컬럼 이름들(가공이 있는 항목은 계산 줄을 더한다)."""
        saved = self.s.get(name)
        out = []
        for i, c in enumerate(self.need(name)):
            self.s[name] = c
            out.append(self.col(name))
        self.s[name] = saved
        return out

    def calc(self, line):
        if line not in self.body["calcs"]:
            self.body["calcs"].append(line)

    def where(self, line):
        for item in line if isinstance(line, list) else [line]:
            self.body["conditions"].append(item)

    def period(self):
        """앞에 붙는 {기간}을 body.periods에."""
        p = self.get("기간")
        if p and (isinstance(p, str) or p.get("start") or p.get("end")):
            if not isinstance(p, str) and not p.get("column"):
                raise Bad("기간에는 날짜 컬럼을 골라 주세요.")
            self.body["periods"].append(p if isinstance(p, str) else f"{p['column']} {p.get('start', '')} ~ {p.get('end', '')}")

    def run(self, label, route, body, **extra):
        self.runs.append({"label": label, "route": route, "body": body, **extra})
        return len(self.runs) - 1

    def rows(self, label="결과", columns=None, sorts=None, limit=None):
        body = {**self.body, **({"columns": [columns] if isinstance(columns, str) else [", ".join(columns)]} if columns else {}), **({"sorts": sorts} if sorts else {}), **({"limit": limit} if limit else {})}
        return self.run(label, "/api/query", body)

    def group(self, label, rows, how, value=None, opts=None, where=None, having=None, order=None, top=None, column=None, subtotals=None, sets=None):
        """묶어 집계하는 요청(/api/pivot). rows = 묶는 컬럼들, how = 함수 사전 집계 id. 결과 컬럼 이름 = ‘전체 …’."""
        how = sql_funcs.ALIASES.get(how, how)
        spec = {"rows": list(rows), "how": how, **{k: v for k, v in (("value", value), ("opts", opts), ("where", where), ("having", having), ("order", order),
                                                                  ("top", top), ("column", column), ("subtotals", subtotals), ("sets", sets)) if v}}
        return self.run(label, "/api/pivot", {**self.body, "pivot": spec}, result=result_col(how))

    def result(self, text=None):
        if not self.runs:
            raise Bad("이 문장은 아직 요청으로 만들 수 없어요.")
        return {"ok": True, "template": self.tid, "text": text or render(self.tid, self.s), "body": self.body, "runs": self.runs,
                **({"combine": self.combine} if self.combine else {}), "notes": self.notes}


def result_col(how):
    """묶어 집계한 값 컬럼의 이름: ‘전체 …’."""
    return f"전체 {sql_funcs.label_of(sql_funcs.ALIASES.get(how, how))}"


def agg_of(spec):
    """{집계} 칸 값 → (집계 id, 옵션)."""
    if isinstance(spec, dict):
        return sql_funcs.ALIASES.get(spec["agg"], spec["agg"]), spec.get("opts") or None
    return sql_funcs.ALIASES.get(spec, spec), None


def quote(v):
    """조건 줄에 넣을 값: or·쉼표·따옴표가 있으면 따옴표로 감싼다."""
    text = str(v)
    return f'"{text}"' if re.search(r"[,\s]|\bor\b|['\"]", text, re.I) else text


def formula_of(cond, columns=None):
    """탐색 조건(글자 한 줄·목록(and)·{any}(or)·{line})을 계산 식(Formula) 글로. 한 줄 안의 or도 나눠서 잇는다. columns를 주면 공백 있는 컬럼 이름도 한 낱말로 읽는다."""
    if isinstance(cond, list):
        return " and ".join(f"({formula_of(c, columns)})" for c in cond)
    if isinstance(cond, dict):
        return " or ".join(f"({formula_of(c, columns)})" for c in cond["any"]) if "any" in cond else formula_of(cond["line"], columns)
    parts = [x.strip() for x in parse.split(parse.OR_RE, cond.strip())]
    return parts[0] and (formula_one(parts[0], columns) if len(parts) == 1 else " or ".join(f"({formula_one(x, columns)})" for x in parts))


def formula_one(text, columns=None):
    """조건 한 줄(or 없음)을 계산 식 글로. 공백 있는 컬럼 이름으로 시작하면 그 자리를 잠깐 한 낱말(parse.SPACED)로 바꿔 읽는다."""
    m = parse.GEO_RE.match(text)
    if m and len(m.group(2).replace(",", " ").split()) == parse.GEO_COUNTS[m.group(1).lower()]:  # 좌표 조건 near·box는 함수 사전의 within_km·in_box
        w = m.group(2).replace(",", " ").split()
        return f"{'within_km' if m.group(1).lower() == 'near' else 'in_box'}(col({w[0]!r}), col({w[1]!r}), {', '.join(num(x) for x in w[2:])})"
    shown, head = text, flow.head_name(text, columns, condition=True)  # shown: 오류 문구에 보일 원래 글(자리표시로 바꾸기 전)
    if head:
        text = parse.SPACED + text.lstrip()[len(head):]

    def name(g):
        return head if g == parse.SPACED else g

    def lit(g, v):  # 값을 식에 넣을 글로: 글자 컬럼이면 숫자처럼 보여도 글자로(탐색의 parse.convert와 같은 규칙)
        kind = columns.get(name(g)) if isinstance(columns, dict) else None
        return repr(str(v)) if kind == "text" else num(v)
    m = parse.NULL_RE.match(text)
    if m:
        return f"{'notnull' if m.group(2) else 'isnull'}(col({name(m.group(1))!r}))"
    m = parse.BETWEEN_RE.match(text)
    if m:
        return f"between(col({name(m.group(1))!r}), {lit(m.group(1), m.group(2))}, {lit(m.group(1), m.group(3))})"
    m = parse.IN_RE.match(text)
    if m:
        items = [lit(m.group(1), parse.unquote(x)) for x in parse.split(parse.COMMA_RE, m.group(3)) if x.strip()]  # 따옴표 안의 쉼표는 값의 일부
        inner = f"isin(col({name(m.group(1))!r}), [{', '.join(items)}])"
        return inner if m.group(2).lower() == "in" else f"not {inner}"
    m = parse.LIKE_RE.match(text)
    if m:
        fn = {"starts": "startswith", "ends": "endswith"}.get(m.group(2).lower(), "contains")
        return f"{fn}(col({name(m.group(1))!r}), {m.group(3).strip().strip(chr(39) + chr(34))!r})"
    m = parse.COND_RE.match(text)
    if m:
        op = {"=": "==", "!=": "!="}.get(m.group(2), m.group(2))
        return f"col({name(m.group(1))!r}) {op} {lit(m.group(1), m.group(3).strip().strip(chr(39) + chr(34)))}"
    raise Bad(f"조건 ‘{shown}’을 계산 식으로 바꿀 수 없어요.")


def num(text):
    """식에 넣을 값: 평범한 십진수(-1.5)면 그대로, 아니면 글자 상수('007'·nan·1e3은 글자)."""
    return str(text) if re.fullmatch(r"[+-]?((0|[1-9]\d*)(\.\d+)?|\.\d+)", str(text)) else repr(str(text))


def func_call(spec, column):
    """{가공} 칸 값 {func, args} → '함수(col(컬럼), 인자…)' 글. 인자는 함수 사전의 칸 차례."""
    if not isinstance(spec, dict) or not spec.get("func"):
        raise Bad("가공할 함수를 골라 주세요.")
    f = sql_funcs.get(spec["func"])
    if f["aggregate"] or f["window"] or f["table"] or f.get("raw"):
        raise Bad(f"‘{f['label']}’은 행마다 계산하는 함수가 아니에요.")
    args = spec.get("args") or {}
    parts = [f"col({column!r})"]
    for a in f["args"][1:]:
        if a["kind"] in ("cond", "order", "group", "cases", "dict"):
            continue
        v = args.get(a["name"], a.get("default"))
        if v in (None, "") and not a.get("optional"):
            raise Bad(f"‘{f['label']}’의 ‘{a['label']}’ 값을 넣어 주세요.")
        if v not in (None, ""):
            parts.append(repr(v) if isinstance(v, (list, tuple)) else num(v) if isinstance(v, (int, float)) else repr(str(v)))
    return f"{f['id']}({', '.join(parts)})"


def window_line(func, value, order=None, group=None, args=()):
    """함수 사전 창 함수 줄 '<id> 컬럼 인자… 기준 … 그룹 …'."""
    return " ".join([func, *([value] if value else []), *map(str, args), *(["기준", order] if order else []), *(["그룹", group] if group else [])])


def compile(tid, slots, body=None, columns=None):
    """문장을 요청으로. 검사에서 bad가 있으면 ok False. 지금 못 만드는 문장은 이유와 함께 ok False."""
    found = check(tid, slots, columns)
    bad = [i for i in found if i["level"] == "bad"]
    if bad:
        return {"ok": False, "reason": bad[0]["text"], "issues": found}
    plan = Plan(tid, slots, body, columns)
    try:
        plan.period()
        if plan.t["kind"] == "metric":
            out = metric_def(plan)
        else:
            COMPILERS[tid](plan)
            out = plan.result()
    except Bad as e:
        return {"ok": False, "reason": str(e), "issues": found}
    return {**out, "issues": found}


# ---------- 탐색 틀 1~50 ----------

def e01(p):
    p.rows("모든 데이터")


def e02(p):
    cols = p.cols("컬럼들")
    if p.get("중복", "두고") == "빼고":
        p.group("중복을 뺀 값", cols, "count")
        p.notes.append("같은 줄은 하나로 합치고 ‘전체 개수’에 몇 줄이었는지 보여 줘요.")
    else:
        p.rows("컬럼", columns=cols)


def e03(p):
    p.where(f"{p.col('컬럼')} = {quote(p.need('값'))}")
    p.rows("행", columns=p.cols("컬럼들"))


def e04(p):
    p.where(f"{p.col('컬럼')} in {', '.join(quote(v) for v in p.need('값 목록'))}")
    p.rows("행")


def e05(p):
    g = p.need("글자")
    mode, text = (g.get("match", "포함"), g.get("text")) if isinstance(g, dict) else ("포함", g)
    if mode not in TEXT_MATCH:
        raise Bad(f"글자 비교는 {', '.join(TEXT_MATCH)} 중 하나예요.")
    p.where(f"{p.col('컬럼')} {TEXT_MATCH[mode]} {quote(text)}")
    p.rows("행")


def e06(p):
    p.group("행 수", [], "count")


def e07(p):
    p.group("대상 수", [], "count_distinct", p.col("대상"), where=p.need("조건"))


def e08(p):
    p.group("무엇별 대상 수", [p.col("무엇")], "count_distinct", p.col("대상"))


def e09(p):
    n = p.get("N", 2)
    p.group("건수", [p.col("무엇")], "count", having=f"전체 개수 >= {n}", order="전체 개수 desc")


def e10(p):
    p.where(p.need("조건"))
    p.group("건수", [p.col("무엇")], "count", order="전체 개수 desc")


def e11(p):
    p.group("두 번 이상 나온 값", [p.col("컬럼")], "count", having="전체 개수 >= 2", order="전체 개수 desc")


def e12(p):
    c = p.col("컬럼")
    p.where(f"{c} in {', '.join(quote(v) for v in p.need('값 목록'))}")
    p.group("값별 건수", [c], "count")


def e13(p):
    how, opts = agg_of(p.need("집계"))
    p.group("상위", [p.col("무엇")], how, p.col("값"), opts, order=f"{result_col(how)} desc", top=min(int(p.get("N", 10)), MAX_TOP))


def e14(p):
    approx = p.get("정확도", "정확") == "빠르게(근사)" or p.tid == "e46"
    p.group("고유 대상 수", [p.col("무엇")], "approx_count_distinct" if approx else "count_distinct", p.col("대상"))


def e15(p):
    p.group("단계별 고유 대상 수", [p.col("단계")], "count_distinct", p.col("대상"))


def e16(p):
    a, b, key = p.need("테이블 A"), p.need("테이블 B"), p.col("연결 컬럼")
    p.body.update(base=a, joins=[f"{b} inner {key}={key}"])
    p.body.pop("union", None)
    p.group("이어진 행이 많은 순", [key], "count", order="전체 개수 desc", top=10)
    p.notes.append("두 테이블에 같은 이름의 연결 컬럼으로 이어요. 많은 순 앞 10개를 보여 줘요.")


def e17(p):
    c = p.need("조건")
    a = p.group("조건인 행", [], "count", where=c)
    b = p.group("전체 행", [], "count")
    p.combine = {"op": "percent", "a": a, "b": b}


def e18(p):
    g = p.col("무엇")
    a = p.group("무엇별 수", [g], "count", order="전체 개수 desc")
    b = p.group("전체 수", [], "count")
    p.combine = {"op": "share", "a": a, "b": b}


def e19(p):
    g, t = p.col("무엇"), p.col("대상")
    a = p.group("조건을 한 대상", [g], "count_distinct", t, where=p.need("조건"))
    b = p.group("전체 대상", [g], "count_distinct", t)
    p.combine = {"op": "ratio", "a": a, "b": b, "by": [g]}


def e20(p):
    g, t = p.col("무엇"), p.col("대상")
    a = p.group("값 합계", [g], "sum", p.col("값"))
    b = p.group("대상 수", [g], "count_distinct", t)
    p.combine = {"op": "ratio", "a": a, "b": b, "by": [g]}


def e21(p):
    a, b = metric_run(p, "지표 A", metric_ref(p, "지표 A")), metric_run(p, "지표 B", metric_ref(p, "지표 B"))
    p.combine = {"op": "ratio", "a": a, "b": b}


def unit_line(p, new="기간"):
    """날짜 컬럼을 단위로 자르는 계산 줄을 더하고 새 컬럼 이름."""
    d, u = p.col("날짜"), p.get("단위", "day")
    p.calc(f"식 truncate(col({d!r}), {u!r}) 이름 {new}")
    return new


def e22(p):
    p.group("단위별 고유 대상 수", [unit_line(p)], "count_distinct", p.col("대상"))


def e23(p):
    new = unit_line(p)
    t = p.col("대상")
    a = p.group("대상 수", [new], "count_distinct", t)
    b = p.group("값 합계", [new], "sum", p.col("값"))
    p.combine = {"op": "ratio", "a": b, "b": a, "by": [new], "show_b": True}  # 대상 수도 함께 보인다


def e24(p):
    t, c, order = p.col("대상"), p.col("컬럼"), p.need("정렬 기준")
    new = f"처음_{c}"
    p.calc(window_line("first_value", c, order, t) + f" 이름 {new}")
    how, opts = agg_of(p.need("집계"))
    p.group("처음 값 기준 집계", [new], how, None if not sql_funcs.needs_column(how) else c, opts)


def e25(p):
    lst, g = p.col("목록 컬럼"), p.col("무엇")
    p.calc(f"펼치기 {lst}")
    how, opts = agg_of(p.need("집계"))
    p.group("펼쳐 집계", [g], how, lst if sql_funcs.needs_column(how) else None, opts, order=f"{result_col(how)} desc", top=min(int(p.get("N", 10)), MAX_TOP))


def e26(p):
    kind = p.get("종류", "중앙값")
    how, opts = {"중앙값": ("median", None), "P50": ("p50", None), "P90": ("p90", None), "P95": ("p95", None), "P99": ("p99", None)}.get(kind, ("quantile", {"p": float(p.get("P", 0.5))}))
    p.group(kind, [p.col("무엇")], how, p.col("값"), opts)


def e27(p):
    kind, pop = p.need("종류"), p.get("기준", "표본") == "모집단"
    how = {"표준편차": "std_pop" if pop else "std", "분산": "var_pop" if pop else "var", "범위": "range"}[kind]
    p.group(kind, [p.col("무엇")], how, p.col("값"))


def e28(p):
    p.group("최빈값", [p.col("무엇")], "mode", p.col("컬럼"))


def e29(p):
    choice = p.need("대표 값 고르기")
    if choice not in sql_funcs.REPRESENTATIVE:
        raise Bad(f"대표 값 고르기는 {', '.join(sql_funcs.REPRESENTATIVE)} 중 하나예요.")
    _, how, needs_order = sql_funcs.REPRESENTATIVE[choice]
    opts = {}
    if needs_order and how in ("first", "last"):
        opts["order"] = [p.need("정렬 기준")]
    elif how == "string_agg":
        opts["sep"] = p.get("구분자", ",")
        if p.get("정렬 기준"):
            opts["order"] = [p.get("정렬 기준")]
    p.group(sql_funcs.REPRESENTATIVE[choice][0], [p.col("무엇")], how, p.col("컬럼"), opts or None)


def e30(p):
    opts = {"sep": p.get("구분자", ","), "distinct": "yes" if p.get("중복", "두고") == "빼고" else "no"}
    if p.get("정렬 기준") and opts["distinct"] == "no":
        opts["order"] = [p.get("정렬 기준")]
    elif p.get("정렬 기준"):
        p.notes.append("중복을 빼면 값 순으로 이어 붙여요(정렬 기준은 쓰지 않아요).")
    p.group("이어 붙이기", [p.col("무엇")], "string_agg", p.col("컬럼"), opts)


def e31(p):
    p.group("목록", [p.col("무엇")], "collect", p.col("컬럼"))


def e32(p):
    p.group("참거짓", [p.col("무엇")], "all" if p.need("범위") == "모두" else "any", None, {"cond": p.need("조건")})


def e33(p):
    desc = p.need("방향") == "가장 늦은"
    p.calc(f"창거르기 row_number <= 1 기준 {str(p.need('정렬 기준')).split()[0]} {'desc' if desc else 'asc'} 그룹 {p.col('무엇')}")  # 방향은 ‘가장 이른/늦은’이 정한다
    p.rows("행")


def e34(p):
    p.calc(f"창거르기 rank <= {int(p.get('N', 3))} 기준 {p.col('값')} desc 그룹 {p.col('무엇')}")
    p.rows("행")


def e35(p):
    func = {"차이": "diff", "변화율": "pct_change", "이전 값": "lag", "다음 값": "lead"}[p.need("보기")]
    v, g = p.col("값"), p.col("무엇")
    p.calc(window_line(func, v, p.need("정렬 기준"), g, [1] if func in ("lag", "lead") else []))
    p.rows("행")


def e36(p):
    kind = p.need("종류")
    func = {"누적 합": "cum_sum", "누적 평균": "cum_mean", "누적 최소": "cum_min", "누적 최대": "cum_max", "이동 합": "moving_sum", "이동 평균": "moving_mean",
            "이동 최소": "moving_min", "이동 최대": "moving_max"}[kind]
    args = [int(p.get("N", 3)), "rows"] if func.startswith("moving") else []
    p.calc(window_line(func, p.col("값"), p.need("정렬 기준"), None, args))
    p.rows("행")


def e37(p):
    g, d, v = p.col("무엇"), p.col("나눌 것"), p.col("값")
    a = p.group("나눈 값 합계", [g, d], "sum", v)
    b = p.group("묶음 합계", [g], "sum", v)
    p.combine = {"op": "share", "a": a, "b": b, "by": [g]}


def e38(p):
    func = {"백분위 순위": "percent_rank", "누적 분포": "cume_dist", "N등분": "ntile"}[p.need("방식")]
    p.calc(window_line(func, None if func != "ntile" else None, f"{p.col('값')} asc", None, [int(p.get("N", 4))] if func == "ntile" else []))
    p.rows("행")


def e39(p):
    how, opts = agg_of(p.need("집계"))
    rows = [p.col("무엇"), p.col("나눌 것")]
    by = SUBTOTAL_BY[p.need("방식")]
    p.group("소계·총계", rows, how, p.col("값"), opts, subtotals=by, sets=[rows, rows[:1], []] if by == "sets" else None)


def e40(p):
    c, new = p.col("컬럼"), p.need("새 이름")
    p.calc(f"식 {func_call(p.need('가공'), c)} 이름 {new}")
    p.rows("새 컬럼")


def e41(p):
    new, parts = p.need("새 이름"), []
    for i in ("1", "2"):
        if p.get("조건" + i):
            parts.append(f"{formula_of(p.get('조건' + i), p.columns)}, {num(p.need('값' + i))}")
    if not parts:
        raise Bad("조건을 하나 이상 넣어 주세요.")
    p.calc(f"식 case({', '.join(parts)}, {num(p.need('값3'))}) 이름 {new}")
    p.rows("분류")


def e42(p):
    p.calc(f"식 date_diff(col({p.col('날짜 A')!r}), col({p.col('날짜 B')!r}), {p.get('단위', 'day')!r}) 이름 날짜차이")
    p.rows("날짜 차이")


def e43(p):
    p.calc(f"식 json_get(col({p.col('JSON 컬럼')!r}), {str(p.need('경로'))!r}, 'string') 이름 json_{re.sub(r'[^0-9A-Za-z가-힣_]', '_', str(p.need('경로')))}")
    p.rows("꺼낸 값")


def e44(p):
    to = {"숫자": "float", "날짜": "date", "글자": "string"}[p.need("바꿀 형")]
    c = p.col("컬럼")
    p.calc(f"식 try_cast(col({c!r}), {to!r}) 이름 {c}_{to}")
    p.rows("바꾼 값")


def e45(p):
    how = "corr" if p.need("종류") == "상관계수" else ("cov_pop" if p.get("기준", "표본") == "모집단" else "cov")
    p.group(p.need("종류"), [], how, p.col("값 A"), {"y": p.col("값 B")})


def e47(p):
    p.calc(f"SQL식 {p.need('식')} 이름 {p.need('새 이름')}")
    p.rows("새 컬럼")
    p.notes.append("SQL 식은 실행 전에 읽기만 하는지 한 번 더 검사해요.")


def measure(p):
    """좌표 틀 48~50의 {값}·{집계}: (집계 id, 옵션, 값 컬럼 또는 None). 집계가 컬럼을 요구하는데 값 칸이 비었으면 Bad."""
    how, opts = agg_of(p.need("집계"))
    if not sql_funcs.needs_column(how):
        return how, opts, None
    if p.get("값") is None:
        raise Bad(f"‘{sql_funcs.label_of(how)}’을(를) 구하려면 ‘값’ 칸을 채워 주세요.")
    return how, opts, p.col("값")


def number_slot(p, name, default=None):
    """숫자 칸(글자로 와도 숫자면 된다)의 값."""
    raw = p.get(name, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise Bad(f"‘{name}’에는 숫자를 넣어 주세요.") from None


def e48(p):
    how, opts, value = measure(p)
    p.group("영역별", [p.col("영역")], how, value, opts, order=f"{result_col(how)} desc", top=MAX_TOP)
    p.notes.append("영역 이름 컬럼은 전처리의 [좌표] → 영역 붙이기로 만들 수 있어요.")


def e49(p):
    how, opts, value = measure(p)
    lat, lon = p.col("위도"), p.col("경도")
    km = number_slot(p, "반경")
    p.where(f"near {lat} {lon} {number_slot(p, '기준 위도', 37.5665):g} {number_slot(p, '기준 경도', 126.978):g} {km:g}")
    p.group(f"{km:g}km 안", [], how, value, opts)


def e50(p):
    how, opts, value = measure(p)
    size = number_slot(p, "칸 크기", 0.01)
    p.calc(f"식 grid_id(col({p.col('위도')!r}), col({p.col('경도')!r}), {size:g}) 이름 격자")
    p.group("격자별", ["격자"], how, value, opts, order=f"{result_col(how)} desc", top=MAX_TOP)


COMPILERS = {f"e{n:02d}": globals()[f"e{n:02d}"] for n in range(1, 51) if f"e{n:02d}" in globals()}
COMPILERS["e46"] = e14


# ---------- 지표 틀 1~15 ----------

def part(agg, column=None, where=None, opts=None):
    """metrics.py 모양의 분자·분모 하나."""
    return {"agg": agg, "column": column, "where": [where] if where else [], **({"opts": opts} if opts else {})}


def known_metric(ref):
    """지표 파일의 이름이거나 내 지표 id인지."""
    from metrics import my_metrics
    try:
        if ref in metric_file.registry():
            return True
    except ValueError:
        pass
    try:
        my_metrics.get(ref)
        return True
    except ValueError:
        return False


def metric_ref(p, name):
    """지표 빈칸의 지표(이름 또는 id). 목록에 없으면 Bad."""
    ref = p.need(name)
    if not known_metric(ref):
        raise Bad(f"지표 ‘{ref}’를 지표 목록에서 찾지 못했어요. 지표 파일의 지표나 내 지표를 골라 주세요.")
    return ref


def metric_run(p, label, ref, by=None, body=None):
    """지표 하나를 (by가 있으면 그 값마다) 구하는 요청. 서버가 지표 정의로 식을 만들어 구한다(sentence_exec)."""
    return p.run(label, "metric", copy.deepcopy(body or p.body), ref=ref, **({"by": by} if by else {}))


def period_parts(p):
    """{기간} 빈칸에서 (날짜 컬럼, 시작, 끝). 시작·끝이 날짜가 아니면 Bad."""
    import datetime as dt
    v = p.get("기간")
    if v is None:
        raise Bad("이 지표는 날짜 컬럼이 필요해요. 앞의 {기간}에 ‘날짜컬럼 시작 ~ 끝’을 넣어 주세요.")
    if isinstance(v, str):
        col, _, rng = v.partition(" ")
        start, _, end = (x.strip() for x in rng.partition("~"))
    else:
        col, start, end = v.get("column"), v.get("start", ""), v.get("end", "")
        if not col:
            raise Bad("기간에는 날짜 컬럼을 골라 주세요.")
    try:
        return col, dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    except ValueError:
        raise Bad("직전 같은 기간과 견주려면 {기간}에 시작과 끝 날짜를 모두 넣어 주세요.") from None


def metric_def(p):
    """지표 틀 → 요청들(runs)과 합치는 방법(combine). 지표 정의(metrics.py 모양)로 나타낼 수 있는 틀(1·2·3·5·6·9, 분모 없는 지표끼리)은 definition도 준다.
    1~3·9는 묶음 없는 집계(/api/pivot), 4는 처음 값 기준 계산 줄, 5~8·13~15는 지표를 구하는 요청(route "metric")을 합치고,
    10~12는 대상마다 구한 값을 다시 집계하는 요청(route "nested")이다."""
    no, definition = p.t["no"], None
    if no == 1:
        p.group("행 수", [], "count")
        definition = {"numerator": part("count")}
    elif no == 2:
        p.group("고유 대상 수", [], "count_distinct", p.col("대상"))
        definition = {"numerator": part("count_distinct", p.col("대상"))}
    elif no == 3:
        how, opts = agg_of(p.need("집계"))
        value = p.col("값") if sql_funcs.needs_column(how) else None
        p.group("값의 집계", [], how, value, opts)
        definition = {"numerator": part(how, value, None, opts)}
    elif no == 4:
        t, c, order = p.col("대상"), p.col("컬럼"), p.need("정렬 기준")
        new = f"처음_{c}"
        p.calc(window_line("first_value", c, order, t) + f" 이름 {new}")
        p.group("처음 값 기준 대상 수", [], "count_distinct", t, where=f"{new} = {quote(p.need('값'))}")
    elif no == 9:
        a = p.group("값 전체 합", [], "sum", p.col("값"))
        b = p.group("대상 수", [], "count_distinct", p.col("대상"))
        p.combine = {"op": "ratio", "a": a, "b": b}
        definition = {"numerator": part("sum", p.col("값")), "denominator": part("count_distinct", p.col("대상"))}
    elif no in (5, 6, 7, 8):
        ra, rb = metric_ref(p, "지표 A"), metric_ref(p, "지표 B")
        a, b = metric_run(p, "지표 A", ra), metric_run(p, "지표 B", rb)
        p.combine = {"op": {5: "ratio", 6: "percent", 7: "sub", 8: "add"}[no], "a": a, "b": b}
        try:
            found = metric_file.registry()
        except ValueError:
            found = {}
        x, y = found.get(ra), found.get(rb)
        if no in (5, 6) and x and y and not x["denominator"] and not y["denominator"]:
            definition = {"numerator": x["numerator"], "denominator": y["numerator"], **({"format": "percent"} if no == 6 else {})}
    elif no == 10:
        how, opts = agg_of(p.need("집계"))
        p.run("대상마다 구한 값", "nested", copy.deepcopy(p.body), entity=p.col("대상"),
              inner={"how": how, "value": p.col("값") if sql_funcs.needs_column(how) else None, "opts": opts}, outer={"op": "mean"})
    elif no == 11:
        p.run("N번 이상 한 대상 비율", "nested", copy.deepcopy(p.body), entity=p.col("대상"),
              inner={"how": "count_if", "opts": {"cond": p.need("조건")}}, outer={"op": "share_ge", "n": int(p.get("N", 2))})
    elif no == 12:
        p.run("단계 A 중 단계 B까지 간 비율", "nested", copy.deepcopy(p.body), entity=p.col("대상"),
              inner={"flags": {"a": p.need("단계 A"), "b": p.need("단계 B")}}, outer={"op": "both_ratio"})
        p.notes.append("두 단계를 한 번이라도 했는지로 봐요(순서·시간은 보지 않아요). 순서까지 보려면 퍼널을 쓰세요.")
    elif no == 13:
        col = period_column(p)
        new = "기간"
        p.calc(f"식 truncate(col({col!r}), {p.get('단위', 'day')!r}) 이름 {new}")
        i = metric_run(p, "단위마다 지표", metric_ref(p, "지표"), by=new)
        p.combine = {"op": "mean_of", "a": i}
    elif no == 14:
        col, start, end = period_parts(p)
        import datetime as dt
        days = (end - start).days + 1
        before = f"{col} {start - dt.timedelta(days=days)} ~ {start - dt.timedelta(days=1)}"
        prev = copy.deepcopy(p.body)
        prev["periods"] = [x for x in prev["periods"] if not str(x).startswith(f"{col} ")] + [before]
        ref = metric_ref(p, "지표")
        a, b = metric_run(p, "이번 기간", ref), metric_run(p, "직전 같은 기간", ref, body=prev)
        p.combine = {"op": "change", "a": a, "b": b}
        p.notes.append(f"직전 같은 기간은 {start - dt.timedelta(days=days)} ~ {start - dt.timedelta(days=1)}({days}일)이에요.")
    elif no == 15:
        order = str(p.need("정렬 기준"))
        order = flow.head_name(order, p.columns) or order.split()[0]
        i = metric_run(p, "정렬 기준마다 지표", metric_ref(p, "지표"), by=order)
        p.combine = {"op": "cumsum", "a": i}
    else:
        raise Bad(f"‘{p.t['name']}’ 지표를 아직 요청으로 만들 수 없어요.")
    out = p.result()
    if definition:
        definition = {"description": render(p.tid, p.s), "format": definition.pop("format", "number"), "denominator": None, **definition}
    return {**out, "kind": "metric", "definition": definition}


def period_column(p):
    """{기간} 빈칸의 날짜 컬럼 이름(시작·끝이 없어도 된다). 없으면 Bad."""
    v = p.get("기간")
    col = v.get("column") if isinstance(v, dict) else str(v).split()[0] if v else None
    if not col:
        raise Bad("단위마다 나누려면 날짜 컬럼이 필요해요. 앞의 {기간}에 날짜 컬럼을 넣어 주세요.")
    return col
