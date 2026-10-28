"""비용을 아끼는 컬럼 추론: 테이블 전체를 읽지 않고 메타데이터나 표본(기본 약 5,000행)만 읽어 전처리 통계를 구한다.
표본은 싼 방법부터 차례로 해 보고(FALLBACK), 되지 않거나 너무 적게 오면 다음 방법으로 넘어간다:
BigQuery 무과금 미리보기(list_rows) → 블록 샘플링(TABLESAMPLE SYSTEM) → 데이터 명세서의 최신 파티션만 → 앞 행.
Parquet 파일은 그와 따로 파일 정보(RowGroup 통계)로 전체의 빈 값·최소·최대·단일값 여부를 읽는다(읽기 비용 없음).
추론 결과는 테이블별로 CACHE에 두고, [다시 구하기]를 누를 때만 새로 구한다. 표본의 행 자체는 저장하지 않는다."""
import datetime as dt
import json
from pathlib import Path

from lazy import ibis

from core import result
from core import settings

CACHE = "profile_cache.json"  # {테이블 열쇠: {"saved", "size", "sampled", "profile", "patterns"}}
MAX_CACHE = 200  # 기억할 테이블 수. 넘으면 오래전에 구한 것부터 뺀다.
SPEC = "data_spec.json"  # 고른 데이터 명세서의 경로 {"path"}
MAX_SPEC_BYTES = 5 * 1024 * 1024
MAX_SPEC_ENTRIES = 200_000  # 명세서의 테이블+컬럼 항목 수(YAML 별칭으로 작은 파일이 큰 명세서가 되는 것을 막는다)
FLAG_MAX = 20  # 표본에서 서로 다른 값이 이 수 이하인 컬럼은 플래그로 보고 값 목록을 남긴다.
FLAG_TEXT = 100  # 플래그 값 하나의 최대 글자 수(이보다 길면 플래그로 보지 않는다)
METHODS = {"preview": "무과금 미리보기", "whole": "전체(행 수가 표본보다 적음)", "block": "블록 샘플링", "partition": "최신 파티션", "head": "앞 행"}


# ---------- 데이터 명세서 ----------

SPECS = {}  # {(경로, 바뀐 시각 ns, 크기): 검사한 명세서} 하나만 둔다(화면이 열릴 때마다 YAML을 다시 읽지 않게)


def read_spec(path):
    """load_spec과 같되, 같은 파일(경로·바뀐 시각·크기)을 다시 읽으면 읽어 둔 것을 돌려준다. 돌려준 값은 고치지 않는다."""
    try:
        stat = Path(path).stat()
    except OSError:
        return load_spec(path)  # 오류 문구는 load_spec이 만든다
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in SPECS:
        found = load_spec(path)
        SPECS.clear()
        SPECS[key] = found
    return SPECS[key]


def load_spec(path):
    """데이터 명세서(JSON·YAML)를 읽어 검사한다. 모양:
    {"tables": {"테이블" 또는 "스키마.테이블": {"description": 글자, "partition": {"column": 컬럼, "latest": 값} 또는 {"column", "recent_days": 정수},
                                             "columns": {컬럼: {"description": 글자, "bytes": 양의 정수(선택: 이 컬럼 한 칸의 평균 크기, 원격 DB에서 PC로 받는 양을 어림할 때 씀)}}}}}"""
    path = Path(path)
    if path.suffix.lower() not in (".json", ".yaml", ".yml"):
        raise ValueError("데이터 명세서는 .json, .yaml, .yml 파일이어야 해요.")
    try:
        if path.stat().st_size > MAX_SPEC_BYTES:
            raise ValueError(f"데이터 명세서는 {MAX_SPEC_BYTES // 1024 // 1024}MB까지 읽어요.")
        text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise ValueError(f"데이터 명세서를 읽을 수 없어요: {e.strerror or e}") from None
    except UnicodeDecodeError:
        raise ValueError("데이터 명세서는 UTF-8이어야 해요.") from None
    try:
        if path.suffix.lower() == ".json":
            spec = json.loads(text)
        else:
            import yaml
            spec = yaml.safe_load(text)
    except Exception as e:
        raise ValueError(f"데이터 명세서의 형식이 올바르지 않아요: {str(e)[:200]}") from None
    tables = spec.get("tables") if isinstance(spec, dict) else None
    if not isinstance(tables, dict):
        raise ValueError('데이터 명세서에는 "tables" 객체가 있어야 해요.')
    count = 0
    for name, table in tables.items():
        where = f"명세서의 {name}"
        if not isinstance(table, dict) or not isinstance(table.get("description", ""), str) or not isinstance(table.get("columns", {}), dict):
            raise ValueError(f"{where}: description은 글자, columns는 객체여야 해요.")
        count += 1 + len(table.get("columns", {}))
        if count > MAX_SPEC_ENTRIES:
            raise ValueError(f"데이터 명세서의 테이블·컬럼은 모두 합쳐 {MAX_SPEC_ENTRIES:,}개까지 읽어요.")
        part = table.get("partition")
        if part is not None and (not isinstance(part, dict) or not isinstance(part.get("column"), str) or ("latest" in part) == ("recent_days" in part)
                                 or ("recent_days" in part and (type(part["recent_days"]) is not int or part["recent_days"] < 1))):
            raise ValueError(f'{where}: partition은 {{"column", "latest"}} 또는 {{"column", "recent_days": 1 이상 정수}}여야 해요.')
        for column, info in table.get("columns", {}).items():
            if not isinstance(info, dict) or not isinstance(info.get("description", ""), str):
                raise ValueError(f"{where}의 {column}: description은 글자여야 해요.")
            if isinstance(info.get("bytes"), bool) or ("bytes" in info and (type(info["bytes"]) is not int or info["bytes"] < 1)):
                raise ValueError(f"{where}의 {column}: bytes는 1 이상 정수여야 해요.")
    return spec


def spec_path():
    return settings.saved_text(SPEC)


def save_spec_path(path):
    """명세서 경로를 저장한다(빈 글자면 쓰지 않음). 저장하기 전에 읽어 검사한다. 명세서의 테이블 이름 목록을 돌려준다."""
    path = path.strip() if isinstance(path, str) else ""
    tables = sorted(read_spec(path)["tables"]) if path else []
    settings.write(SPEC, {"path": path})
    return tables


def spec_table(schema, base):
    """명세서에서 이 테이블의 부분("스키마.테이블"을 먼저, 없으면 "테이블"). 명세서가 없거나 읽을 수 없으면 {}(통계는 명세서 없이 구한다)."""
    path = spec_path()
    try:
        tables = read_spec(path)["tables"] if path else {}
    except ValueError:
        return {}
    return (schema and tables.get(f"{schema}.{base}")) or tables.get(base) or {}


# ---------- 파일 정보(Parquet) ----------

def parquet_facts(path):
    """Parquet 파일 정보(RowGroup 통계)로 구한 전체의 행 수와 컬럼별 빈 값·최소·최대·단일값 여부. 통계가 없는 컬럼은 뺀다. 데이터는 읽지 않는다."""
    import pyarrow.parquet as pq
    meta = pq.read_metadata(path)
    columns = {}
    for i in range(meta.num_columns):
        name = meta.schema.column(i).name
        stats = [meta.row_group(g).column(i).statistics for g in range(meta.num_row_groups)]
        if not stats or any(s is None or not s.has_null_count for s in stats):
            continue
        nulls = sum(s.null_count for s in stats)
        bounded = [s for s in stats if s.has_min_max]
        low = min((s.min for s in bounded), default=None)
        high = max((s.max for s in bounded), default=None)
        every = len(bounded) == len(stats)  # 최소·최대는 모든 행 묶음에 있을 때만 믿는다(NaN만 있는 묶음 등은 통계가 없다)
        columns[name] = {"nulls": nulls, "min": plain(low), "max": plain(high),
                         "constant": bool(nulls == meta.num_rows or (nulls == 0 and every and low == high))}
    return {"rows": meta.num_rows, "columns": columns}


def plain(value):
    """JSON으로 보낼 수 있는 값(result.plain: NaN·무한은 빈 값, Decimal은 실수, 날짜 등은 글자로. 바이트는 글자로 푼다)."""
    return result.plain(value.decode("utf-8", "replace") if isinstance(value, bytes) else value)


# ---------- 표본 ----------

def fetch(con, expr, **kwargs):
    """식의 결과를 pyarrow 표로. 드라이버가 빈 값이 든 정수 컬럼을 실수로 거쳐 넘겨 큰 정수가 넘치면(SQLite 등) DB-API 커서로 행을 직접 받아 원래 타입으로 만든다."""
    import pyarrow as pa
    try:
        return expr.to_pyarrow(**kwargs)
    except pa.ArrowInvalid:
        cursor = con.raw_sql(ibis.to_sql(expr))
        try:
            names = [d[0] for d in cursor.description]
            rows = [dict(zip(names, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()
        return pa.Table.from_pylist(rows, schema=expr.schema().to_pyarrow())


def preview(con, t, schema, base, size, rows, part):
    """BigQuery의 무과금 미리보기(tabledata.list): 쿼리를 보내지 않아 요금이 없다. BigQuery가 아니면 None."""
    if getattr(con, "name", "") != "bigquery":
        return None
    project = getattr(con, "current_catalog", None) or getattr(con, "billing_project", None)
    dataset = schema or getattr(con, "current_database", None)
    ref = ".".join(p for p in (project, dataset, base) if p)
    return con.client.list_rows(ref, max_results=size).to_arrow()


def whole(con, t, schema, base, size, rows, part):
    """행 수가 표본보다 적으면 그대로 읽는다(그것이 가장 싸다)."""
    return fetch(con, t.limit(size)) if rows is not None and rows <= size else None


def block(con, t, schema, base, size, rows, part):
    """블록 샘플링(TABLESAMPLE SYSTEM): 저장 블록 일부만 읽는다. 행 수를 모르거나 너무 적게 오면 None."""
    if rows is None:
        return None
    picked = t.sample(min(1.0, size * 1.2 / rows), method="block").limit(size)
    found = fetch(con, picked, **result.streaming(picked))  # 파일 연결(Polars)은 streaming으로 읽어 최대 메모리를 줄인다(표본은 그대로 무작위)
    return found if found.num_rows >= min(size, rows) * settings.get("sample_too_few") else None  # 기대한 행의 이만큼도 못 가져오면(작은 테이블·뷰) 다음 방법으로(설정 sample_too_few, 기본 0.2)


def partition(con, t, schema, base, size, rows, part):
    """데이터 명세서의 파티션 키로 최신 파티션만 걸러 앞 size행. 명세서에 파티션이 없으면 None."""
    if not part:
        return None
    column = part["column"]
    if column not in t.columns:
        raise ValueError(f"명세서의 파티션 컬럼 '{column}'이 테이블에 없어요.")
    if "latest" in part:
        pick = t[column] == part["latest"]
    else:
        pick = t[column] >= dt.date.today() - dt.timedelta(days=part["recent_days"] - 1)
    return fetch(con, t.filter(pick).limit(size))


def head(con, t, schema, base, size, rows, part):
    """앞 size행. 마지막 방법이다(DW에 따라 전체를 읽을 수 있다)."""
    return fetch(con, t.limit(size))


FALLBACK = [("preview", preview), ("whole", whole), ("block", block), ("partition", partition), ("head", head)]


def sample(con, t, schema, base, size, rows, part):
    """(표본 pyarrow 표, 쓴 방법, 넘어간 방법들 [[방법, 이유]]). 방법마다 오류가 나거나 None이면 다음으로."""
    passed = []
    for name, step in FALLBACK:
        try:
            found = step(con, t, schema, base, size, rows, part)
        except Exception as e:
            passed.append([name, str(e)[:200] or type(e).__name__])
            continue
        if found is not None:
            return found, name, passed
    raise ValueError("표본을 읽지 못했어요: " + " / ".join(f"{METHODS[n]}: {why}" for n, why in passed))


# ---------- 추론 ----------

DATE_SHAPES = [("YYYY-MM-DD HH:MM:SS", r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}"), ("YYYY-MM-DD", r"\d{4}-\d{2}-\d{2}"),
               ("YYYYMMDD", r"(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])"), ("YYYY/MM/DD", r"\d{4}/\d{2}/\d{2}")]


def infer(arrow):
    """표본으로 컬럼별 추론: 빈 값 비율, 서로 다른 값 수, 단일값인지, 날짜 형식, 플래그 값 목록. {컬럼: {...}}"""
    import polars as pl
    frame = pl.from_arrow(arrow)
    out = {}
    for name in frame.columns:
        col = frame[name]
        values = col.drop_nulls()
        unique = values.n_unique()
        found = {"null_ratio": round(col.null_count() / len(col), 4) if len(col) else None, "unique": unique, "constant": unique <= 1}
        if col.dtype == pl.String and len(values):
            texts = values.str.strip_chars()
            found["date_format"] = next((label for label, shape in DATE_SHAPES if texts.str.contains(f"^(?:{shape})$").all()), None)
        elif col.dtype in (pl.Date, pl.Datetime):
            found["date_format"] = "날짜" if col.dtype == pl.Date else "날짜시간"
        if 1 <= unique <= FLAG_MAX and unique * 2 <= len(values) and (col.dtype == pl.String or col.dtype.is_integer() or col.dtype == pl.Boolean):
            flags = values.unique().sort().to_list()  # 값이 되풀이되는(서로 다른 값이 표본의 절반 이하) 컬럼만. 번호 같은 컬럼은 아니다.
            if all(len(str(v)) <= FLAG_TEXT for v in flags):
                found["flags"] = flags
        out[name] = found
    return out


# ---------- 캐시 ----------

def cached(key, size):
    """같은 표본 크기로 구해 둔 추론. 없으면 None."""
    found = (settings.read(CACHE) or {}).get(key)
    return found if isinstance(found, dict) and found.get("size") == size else None


def remember(key, size, **parts):
    """추론 결과(profile·patterns·sampled)를 테이블 열쇠로 둔다. 같은 크기로 구한 것이면 나머지 부분은 지킨다."""
    with settings.updating(CACHE):
        found = {k: v for k, v in (settings.read(CACHE) or {}).items() if isinstance(v, dict)}
        old = found.get(key) if isinstance(found.get(key), dict) and found[key].get("size") == size else {}
        found[key] = {**old, **parts, "size": size, "saved": dt.datetime.now().isoformat(timespec="seconds")}
        for stale in sorted(found, key=lambda k: str(found[k].get("saved", "")))[:-MAX_CACHE]:
            found.pop(stale)
        settings.write(CACHE, found)
        return found[key]
