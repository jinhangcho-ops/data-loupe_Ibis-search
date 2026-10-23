"""사전 설정 지표(MetricRegistry): 지표 파일(YAML·JSON)에 적은 지표를 쿼리의 탐색 식(기준 테이블·조인·기간·조건) 위에서 한 번에 구한다.
파일 모양:
    metrics:
      CTR:
        description: 노출 대비 클릭        # 선택
        format: percent                    # 선택: number(기본) 또는 percent(화면에 ×100 %)
        numerator: {agg: count, where: "event = click"}
        denominator: {agg: count, where: "event = impression"}
      매출:
        numerator: {agg: sum, column: amount}
- 분자·분모는 집계 하나: agg는 함수 사전(sql_funcs)의 집계 id(count·sum·mean·min·max·nunique·median·quantile·mode·std·string_agg … 전부),
  column(count는 없어도 됨, user.name 같은 구조 안 필드도 됨), where는 탐색의 조건 줄 하나나 그 목록(and로 잇는다, 한 줄 안의 or도 된다),
  opts는 그 집계의 나머지 인자(분위수 p, 구분자 sep, 둘째 컬럼 y …, 선택). 식(코드)은 쓰지 않으므로 파일이 무엇을 실행할 수는 없다.
- 분모가 있으면 분자 / 분모이고, 분모가 0이면 nullif(0)로 빈 값이 된다(0으로 나누기 오류 없음).
- 계산은 식.group_by(나눌 컬럼).aggregate(지표들) 한 번(나누지 않으면 aggregate만)이다."""
from pathlib import Path

from lazy import ibis

import parse
import settings
import sql_funcs
from result import plain

FILE = "metrics_file.json"  # 고른 지표 파일의 경로 {"path"}
MAX_FILE_BYTES = 1024 * 1024
MAX_METRICS = 200  # 파일 하나의 지표 수
MAX_RUN = 20  # 한 번에 구할 지표 수
MAX_GROUPS = 50  # 나눌 때 보여 줄 그룹 수(값 순)
OLD_AGGS = ("count", "sum", "mean", "min", "max", "nunique")  # 처음부터 있던 집계(목록의 앞에 둔다)
AGGS = (*OLD_AGGS, *[i for i in sql_funcs.ids_for("metrics") if i not in OLD_AGGS])  # 함수 사전의 집계 전부(예전 이름 nunique는 count_distinct와 같다)
NUMERIC_AGGS = ("sum", "mean")
FORMATS = ("number", "percent")
AGG_NAMES = {i: sql_funcs.label_of(i) for i in AGGS}  # 집계 id → 한국어 이름(예전 이름 nunique도 '고유 개수')


# ---------- 지표 파일 ----------

def read_file(path):
    """지표 파일을 읽어 검사한 {이름: 지표}. 지표는 {"description", "format", "numerator", "denominator"(없으면 None)}."""
    path = Path(path)
    if path.suffix.lower() not in (".json", ".yaml", ".yml"):
        raise ValueError("지표 파일은 .yaml, .yml, .json 파일이어야 합니다.")
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"지표 파일은 {MAX_FILE_BYTES // 1024 // 1024}MB까지 읽습니다.")
        text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise ValueError(f"지표 파일을 읽을 수 없습니다: {e.strerror or e}") from None
    except UnicodeDecodeError:
        raise ValueError("지표 파일은 UTF-8이어야 합니다.") from None
    try:
        if path.suffix.lower() == ".json":
            import json
            data = json.loads(text)
        else:
            import yaml
            data = yaml.safe_load(text)
    except Exception as e:
        raise ValueError(f"지표 파일의 형식이 올바르지 않습니다: {str(e)[:200]}") from None
    found = data.get("metrics") if isinstance(data, dict) else None
    if not isinstance(found, dict) or not found:
        raise ValueError('지표 파일에는 지표를 하나 이상 담은 "metrics" 객체가 있어야 합니다.')
    if len(found) > MAX_METRICS:
        raise ValueError(f"지표는 파일 하나에 {MAX_METRICS}개까지입니다.")
    return {str(name): metric(str(name), m) for name, m in found.items()}


def metric(name, m):
    """지표 하나를 검사해 정리한다."""
    where = f"지표 '{name}'"
    if not name.strip() or len(name) > 100:
        raise ValueError("지표 이름은 1~100자여야 합니다.")
    if not isinstance(m, dict):
        raise ValueError(f"{where}: numerator(분자)를 담은 객체여야 합니다.")
    unknown = set(m) - {"description", "format", "numerator", "denominator"}
    if unknown:
        raise ValueError(f"{where}: 모르는 항목 {', '.join(sorted(map(str, unknown)))}. description·format·numerator·denominator만 씁니다.")
    if not isinstance(m.get("description", ""), str):
        raise ValueError(f"{where}: description은 글자여야 합니다.")
    if m.get("format", "number") not in FORMATS:
        raise ValueError(f"{where}: format은 {' 또는 '.join(FORMATS)}입니다.")
    if "numerator" not in m:
        raise ValueError(f"{where}: numerator(분자)가 있어야 합니다.")
    return {"description": m.get("description", ""), "format": m.get("format", "number"),
            "numerator": part(f"{where}의 numerator", m["numerator"]),
            "denominator": part(f"{where}의 denominator", m["denominator"]) if m.get("denominator") is not None else None}


def part(where, p):
    """분자·분모 하나 {"agg", "column"(없으면 None), "where": [조건 줄]}."""
    if not isinstance(p, dict) or p.get("agg") not in AGGS:
        raise ValueError(f"{where}: agg는 {', '.join(AGGS)} 중 하나여야 합니다.")
    unknown = set(p) - {"agg", "column", "where", "opts"}
    if unknown:
        raise ValueError(f"{where}: 모르는 항목 {', '.join(sorted(map(str, unknown)))}. agg·column·where·opts만 씁니다.")
    column = p.get("column")
    if column is not None and (not isinstance(column, str) or not column.strip()):
        raise ValueError(f"{where}: column은 컬럼 이름(글자)이어야 합니다.")
    if column is None and sql_funcs.needs_column(p["agg"]):  # 컬럼 칸이 필수인 집계(count와 조건만 받는 count_if·all·any는 아님)

        raise ValueError(f"{where}: {p['agg']}에는 column이 있어야 합니다.")
    opts = p.get("opts")
    if opts is not None and not isinstance(opts, dict):
        raise ValueError(f"{where}: opts는 집계의 나머지 인자를 담은 객체여야 합니다.")
    conds = p.get("where", [])
    conds = [conds] if isinstance(conds, str) else conds
    if not isinstance(conds, list) or not all(isinstance(c, str) and c.strip() for c in conds):
        raise ValueError(f"{where}: where는 조건 줄(글자)이나 그 목록이어야 합니다.")
    return {"agg": p["agg"], "column": column.strip() if column else None, "where": [c.strip() for c in conds], **({"opts": opts} if opts else {})}


def describe(m):
    """지표의 계산을 사람이 읽을 글로(목록에 보인다). 예: 개수 [event = click] ÷ 개수 [event = impression]"""
    text = lambda p: AGG_NAMES[p["agg"]] + (f"({p['column']})" if p["column"] else "") + (f" [{' and '.join(p['where'])}]" if p["where"] else "")
    return text(m["numerator"]) + (f" ÷ {text(m['denominator'])}" if m["denominator"] else "")


def file_path():
    found = (settings.read(FILE) or {}).get("path")
    return found if isinstance(found, str) and found else ""


def save_file_path(path):
    """지표 파일 경로를 저장한다(빈 글자면 쓰지 않음). 저장하기 전에 읽어 검사하고 그 지표들을 돌려준다."""
    path = path.strip() if isinstance(path, str) else ""
    found = read_file(path) if path else {}
    settings.write(FILE, {"path": path})
    return found


def registry():
    """지금 고른 파일의 지표 {이름: 지표}. 파일이 없으면 {}, 읽을 수 없으면 ValueError."""
    path = file_path()
    return read_file(path) if path else {}


# ---------- 계산 ----------

def aggregate(expr, p):
    """분자·분모 하나의 집계 식(where 조건은 그 집계에만 건다)."""
    preds = [parse.predicate(line, expr) for line in p["where"]]
    where = ibis.and_(*preds) if preds else None
    if p["column"] is None and p["agg"] == "count":
        return expr.count(where=where)
    if p["column"] is not None and p["agg"] in NUMERIC_AGGS and not parse.column(expr, p["column"]).type().is_numeric():
        raise ValueError(f"'{p['column']}'은 숫자 컬럼이 아니라 {AGG_NAMES[p['agg']]}를 구할 수 없습니다.")
    return sql_funcs.aggregate(p["agg"], expr, p["column"], where, p.get("opts"))


def metric_expr(expr, name, m):
    """지표 하나의 식. 분모가 있으면 분자 / 분모(분모 0은 빈 값)."""
    try:
        value = aggregate(expr, m["numerator"])
        if m["denominator"] is None:
            return value
        return value.cast("float64") / aggregate(expr, m["denominator"]).nullif(0).cast("float64")
    except ValueError as e:
        raise ValueError(f"지표 '{name}': {e}") from None


def compute(expr, names, found, by=None):
    """지표들을 expr 위에서 구한다. by(컬럼, 구조 안 필드도)를 주면 그 값마다(앞 MAX_GROUPS개, 값 순).
    (나눈 값들 또는 None, {이름: 값들}, 그룹이 더 있는지)를 돌려준다."""
    if not names:
        raise ValueError("구할 지표를 고르세요.")
    if len(names) > MAX_RUN:
        raise ValueError(f"지표는 한 번에 {MAX_RUN}개까지 구할 수 있습니다.")
    for name in names:
        parse.check_name(name, list(found), "지표")
    metrics = {f"m{i}": metric_expr(expr, name, found[name]) for i, name in enumerate(names)}
    if not by:
        row = expr.aggregate(**metrics).to_polars().row(0, named=True)
        return None, {name: [plain(row[f"m{i}"])] for i, name in enumerate(names)}, False
    key = parse.column(expr, by)
    if key.type().is_nested():
        raise ValueError(f"'{by}'은 중첩 컬럼이라 나눌 수 없습니다.")
    table = expr.group_by(key.name("x")).aggregate(**metrics).order_by("x").limit(MAX_GROUPS + 1).to_polars()
    more = table.height > MAX_GROUPS
    table = table.head(MAX_GROUPS)
    return ([None if v is None else str(v) for v in table["x"].to_list()],
            {name: [plain(v) for v in table[f"m{i}"].to_list()] for i, name in enumerate(names)}, more)
