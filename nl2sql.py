"""자연어 질문 → SQL(NL2SQL, 고도화 명세 5번의 뼈대). 어느 LLM인지 모르고 adapters.llm의 어댑터에 묻는다(지금은 스텁).
1. 문맥: 지금 연결의 테이블·컬럼(이름·타입) + 데이터 명세서(설명·파티션) + 캐시된 컬럼 추론(빈 값 비율·단일값·날짜 형식·플래그인지) + 지표 정의.
   실제 데이터 값(플래그 값 목록, 최소·최대 등)은 넣지 않는다. 바깥(LLM 회사)으로 나갈 글이기 때문이다.
2. 시스템 프롬프트로 만들어 어댑터에 묻는다.
3. 돌아온 답에서 SQL을 꺼내 sql_exec.check(SafeIbisSQLExecutor의 검사)로 읽기만 하는 SELECT 하나인지 본다. 실행은 하지 않는다(화면이 편집기에 넣고 사람이 실행)."""
import re

import metrics
import profiler
import settings
import source
import sql_exec
from adapters import llm

MAX_TABLES = 30  # 문맥에 넣을 테이블 수
MAX_COLUMNS = 80  # 테이블 하나에서 넣을 컬럼 수
MAX_PROMPT = 30_000  # 시스템 프롬프트 글자 수 상한
MAX_QUESTION = 1_000
SQL_BLOCK = re.compile(r"```(?:sql)?\s*(.*?)```", re.S | re.I)


def cached_infer(conn_key, schema, table):
    """그 테이블의 캐시된 추론 {컬럼: {null_ratio, constant, date_format, flag}}. 값(플래그 값·최소·최대)은 뺀다. 없으면 {}."""
    prefix = "|".join([conn_key, schema or "", table])
    found = [v for k, v in (settings.read(profiler.CACHE) or {}).items() if k == prefix and isinstance(v, dict) and isinstance(v.get("profile"), dict)]
    if not found:
        return {}
    out = {}
    for column in found[-1]["profile"].get("columns", []):
        guess = column.get("infer") or {}
        out[column.get("name")] = {k: v for k, v in {"null_ratio": guess.get("null_ratio"), "constant": guess.get("constant"),
                                                     "date_format": guess.get("date_format"), "flag": bool(guess.get("flags"))}.items() if v not in (None, False)}
    return out


def context(con, conn_key, schema=None, vts=None):
    """LLM에 줄 문맥(데이터 값 없음). {"dialect", "tables": [{name, description, partition, columns: [{name, type, description, infer}]}], "metrics": [...], "virtual": [...]}."""
    spec_tables = {}
    try:
        path = profiler.spec_path()
        spec_tables = profiler.read_spec(path)["tables"] if path else {}
    except ValueError:
        pass
    tables = []
    for name in source.list_tables(con, schema)[:MAX_TABLES]:
        spec = (schema and spec_tables.get(f"{schema}.{name}")) or spec_tables.get(name) or {}
        guesses, described = cached_infer(conn_key, schema, name), spec.get("columns", {})
        cols = []
        for col, dtype in list(source.get_table(con, name, schema).schema().items())[:MAX_COLUMNS]:
            cols.append({"name": col, "type": str(dtype).lstrip("!"), **({"description": described[col]["description"]} if described.get(col, {}).get("description") else {}),
                         **({"infer": guesses[col]} if guesses.get(col) else {})})
        part = spec.get("partition")
        tables.append({"name": name, **({"description": spec["description"]} if spec.get("description") else {}),
                       **({"partition": part["column"]} if part else {}), "columns": cols})
    try:
        found = metrics.registry()
    except ValueError:
        found = {}
    listed = [{"name": n, "description": m["description"], "definition": metrics.describe(m, values=False), "format": m["format"]}
              for n, m in found.items()]
    dialect = sql_exec.dialect_of(con)
    return {"dialect": getattr(dialect, "__name__", dialect) or "ANSI", "tables": tables, "metrics": listed, "virtual": sorted(vts or {})}


def infer_text(guess):
    parts = []
    if "null_ratio" in guess:
        parts.append(f"빈 값 {guess['null_ratio']:.0%}")
    if guess.get("constant"):
        parts.append("값이 하나뿐")
    if guess.get("date_format"):
        parts.append(f"날짜 형식 {guess['date_format']}")
    if guess.get("flag"):
        parts.append("값 종류가 적은 구분 컬럼")
    return ", ".join(parts)


def system_prompt(ctx):
    """문맥으로 시스템 프롬프트(한국어 지시 + 스키마). MAX_PROMPT를 넘으면 뒤 테이블부터 줄인다."""
    head = [f"너는 {ctx['dialect']} SQL을 쓰는 도우미다. 사용자의 질문에 답하는 SQL 하나만 쓴다.",
            "규칙:",
            "- 읽기만 하는 SELECT 문 하나(WITH·JOIN·UNION 가능). INSERT·UPDATE·DELETE·CREATE·DROP 같은 바꾸는 문장, 여러 문장(;)은 쓰지 않는다.",
            "- 아래에 있는 테이블과 컬럼만 쓴다. 모르면 추측하지 말고 SQL 대신 '-- 모름: 이유' 한 줄을 쓴다.",
            "- 답은 ```sql 코드 블록 하나로만 쓴다.",
            "- 지표를 물으면 아래 지표 정의(분자 ÷ 분모, 분모가 0이면 NULL)대로 계산한다."]
    body = ["", "## 테이블"]
    for t in ctx["tables"]:
        body.append(f"### {t['name']}" + (f" — {t['description']}" if t.get("description") else "") + (f" (파티션 컬럼: {t['partition']})" if t.get("partition") else ""))
        for c in t["columns"]:
            extra = "; ".join(x for x in (c.get("description", ""), infer_text(c.get("infer", {}))) if x)
            body.append(f"- {c['name']} {c['type']}" + (f" — {extra}" if extra else ""))
    if ctx["virtual"]:
        body += ["", "## 가상 테이블(테이블처럼 쓸 수 있음)", ", ".join(ctx["virtual"])]
    if ctx["metrics"]:
        body += ["", "## 지표 정의"] + [f"- {m['name']}: {m['definition']}" + (f" — {m['description']}" if m["description"] else "") +
                                      (" (비율)" if m["format"] == "percent" else "") for m in ctx["metrics"]]
    text = "\n".join(head + body)
    if len(text) > MAX_PROMPT:
        text = text[:MAX_PROMPT].rsplit("\n", 1)[0] + "\n… (길어서 뒤를 줄임)"
    return text


def check_question(question):
    if not isinstance(question, str) or not question.strip():
        raise ValueError("질문을 입력하세요.")
    if len(question) > MAX_QUESTION:
        raise ValueError(f"질문은 {MAX_QUESTION:,}자까지입니다.")
    return question.strip()


def extract_sql(reply):
    """LLM 답에서 SQL을 꺼낸다(```sql 블록이 있으면 그 안, 없으면 전체). '-- 모름' 답이면 그 이유로 ValueError."""
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("LLM이 빈 답을 주었습니다.")
    m = SQL_BLOCK.search(reply)
    sql = (m[1] if m else reply).strip()
    if sql.startswith("-- 모름"):
        raise ValueError("LLM이 SQL을 만들지 못했습니다: " + sql.removeprefix("-- 모름").lstrip(": ").strip())
    return sql


def ask(question, ctx, dialect, known, client=None):
    """질문을 LLM에 묻고 검사한 SQL을 돌려준다(실행하지 않음). 바꾸는 문장이면 SecurityViolationError, 문법 오류는 SqlSyntaxError,
    이 연결에 없는 테이블(known: 테이블·가상 테이블 이름)을 쓰면 ValueError."""
    question = check_question(question)
    reply = (client or llm.client()).complete(system_prompt(ctx), question)
    sql = extract_sql(reply)
    tree = sql_exec.check(sql, dialect)
    lower = {k.lower() for k in known}
    missing = [n for n in dict.fromkeys(sql_exec.table_names(tree)) if n.lower() not in lower]
    if missing:
        raise ValueError(f"LLM이 이 연결에 없는 테이블을 썼습니다: {', '.join(missing)}")
    return sql
