"""Ibis 분석 화면(전처리·분석·그래프)의 라우트. server.app에 달린다. 따로 실행하지 않고 desktop.py가 프로그램 창 안에서 띄운다."""
import datetime as dt
import json
from pathlib import Path

from flask import jsonify, request
from lazy import ibis

import analysis
import held
import parse
import prep
import prep_derive
import prep_profile
import profiler
import result
import server
import settings
import source
import sql_exec
import sql_funcs
import use_counts
from server import app, get_con, run, state, to_json

PREVIEW_ROWS = 20
DEFAULT_SAMPLE = 5_000  # 통계를 낼 표본 행 수의 기본값
FILE_FORMATS = ["parquet", "csv"]
server.CONSTANTS.update(sample=DEFAULT_SAMPLE, sample_methods=profiler.METHODS, analysis=analysis.LIMITS,  # 화면(prep.html)의 표본 행 수 기본값, 표본 방법 이름, 분석의 상한들
                        formula_guide=prep_derive.FORMULA_GUIDE, pca_max=prep_derive.PCA_MAX,  # 계산 식 안내, PCA에 넣을 컬럼 수 상한
                        dedupe_keep={"rows": list(prep.KEEP_ROWS), "merge": {k: v[0] for k, v in sql_funcs.REPRESENTATIVE.items()}, "funcs": sql_funcs.ids_for("prep")})  # 중복 제거에서 남길 것, 계산 컬럼에 쓸 수 있는 함수
TABLE_PREPS = "table_preps.json"  # 테이블별로 마지막에 쓴 전처리 {테이블 열쇠: {"config", "saved"}}
MAX_TABLE_PREPS = 200
SAMPLES = {}  # 마지막으로 읽은 표본 하나 {테이블 열쇠: (크기, pyarrow 표, 표본 정보)}. 통계에 이어 타입 추천이 같은 표본을 쓴다.


@app.get("/api/recipes")
def recipes():
    return jsonify(recipes=settings.recipes())


@app.post("/api/recipes")
def save_recipe():
    """recipe가 null이면 그 이름을 지운다."""
    return jsonify(recipes=settings.save_recipe(request.json.get("name"), request.json.get("recipe")))


@app.get("/prep")
def prep_page():
    return server.page("prep.html")


def table_key(body):
    """지금 연결의 테이블을 알아보는 열쇠(비밀번호 없음): 연결(폴더 경로나 주소·아이디), 스키마, 테이블 이름."""
    if state["opened"] is None:
        raise ValueError("먼저 연결하세요.")
    base = body.get("base")
    if not isinstance(base, str) or not base:
        raise ValueError("테이블을 고르세요.")
    return "|".join([use_counts.connection_key(source.recent(state["opened"])), body.get("schema") or "", base])


@app.get("/api/prep/table")
def table_prep():
    """이 테이블에 마지막으로 쓴 전처리(화면의 입력 상태). 없으면 null. 같은 테이블을 열면 화면이 이것으로 칸을 채운다."""
    found = settings.read(TABLE_PREPS) or {}
    saved = found.get(table_key(request.args))
    return jsonify(config=saved.get("config") if isinstance(saved, dict) else None)


@app.post("/api/prep/table")
def save_table_prep():
    """[미리보기]·[실행]에 쓴 전처리를 그 테이블의 것으로 저장한다. 테이블은 MAX_TABLE_PREPS개까지(넘으면 오래전에 쓴 것부터 뺀다)."""
    key, config = table_key(request.json), request.json.get("config")
    if not isinstance(config, dict) or len(json.dumps(config, ensure_ascii=False)) > settings.MAX_RECIPE_SIZE:
        raise ValueError("저장할 전처리가 올바르지 않거나 너무 큽니다.")
    found = {k: v for k, v in (settings.read(TABLE_PREPS) or {}).items() if isinstance(v, dict) and k != key}
    found[key] = {"config": config, "saved": dt.datetime.now().isoformat(timespec="microseconds")}
    for old in sorted(found, key=lambda k: str(found[k].get("saved", "")))[:-MAX_TABLE_PREPS]:
        found.pop(old)
    settings.write(TABLE_PREPS, found)
    return jsonify(ok=True)


def local_mode(recipe):
    """recipe를 이 PC의 작업 사본(working_copy)에서 계산할지. 원격 DB(주소로 접속)일 때만: 설정 prep_where가 all(기본)이면 늘,
    new면 계산 컬럼·PCA가 있을 때만. 파일·DuckDB 파일 연결은 원래 이 PC에서 계산하므로 사본을 만들지 않는다."""
    if not held.remote(get_con()):
        return False
    return settings.load().get("prep_where", "all") == "all" or bool(recipe.get("formulas") or recipe.get("pca"))


def copy_key(recipe):
    """작업 사본(working_copy)을 가리키는 held 열쇠."""
    return ("prep", table_key(recipe), json.dumps(recipe.get("filters") or [], ensure_ascii=False))


def working_copy(recipe):
    """원격 DB의 기준 테이블에 행 거르기(filters)만 걸어 이 PC의 DuckDB로 한 번 받아 둔 표(held, 상한 없음, 묶음씩 받음).
    같은 테이블·거르기면 다시 쓴다. DB에는 이 단순한 조회 하나만 가고, 평균 같은 통계와 전처리 계산은 이 표에서 한다."""
    con, schema = get_con(), recipe.get("schema") or None
    parse.check_name(recipe["base"], server.table_names(con, schema), "테이블")  # 목록·스키마는 기억해 둔 것(사본을 다시 쓸 때 DB에 묻지 않게)
    t = server.any_table(con, recipe["base"], schema, vts={})
    filters = recipe.get("filters") or []
    if filters:
        t = t.filter(*[parse.predicate(cond, t) for cond in filters])
    key = copy_key(recipe)
    name = run(lambda: held.ensure(key, t, state["cancel"], cap=False))[0]
    local = held.table(name)
    return local.select(*[c for c in local.columns if c != held.ROW])


def built(recipe, info=None):
    """recipe를 (Ibis 식, 결과 컬럼마다 (recipe상 컬럼, 파생 컬럼인지))로 만든다. 평균 등을 구하는 조회가 있어 run으로 돌린다.
    local_mode면 작업 사본에서(행 거르기는 사본을 받을 때 이미 걸었다). info는 prep.build와 같다(PCA의 설명된 분산 비율)."""
    if local_mode(recipe):
        table = working_copy(recipe)
        return run(lambda: prep.build(held.local(), {**recipe, "filters": []}, table, info))
    con = get_con()
    return run(lambda: prep.build(con, recipe, None, info))


def base_table(body):
    con, schema = get_con(), body.get("schema") or None
    parse.check_name(body["base"], source.list_tables(con, schema), "테이블")
    return source.get_table(con, body["base"], schema)


def sample_size(body):
    """body의 sample(통계를 낼 표본 행 수). 없으면 DEFAULT_SAMPLE."""
    low, high = settings.SIZES["sample"]
    n = body.get("sample", DEFAULT_SAMPLE)
    if type(n) is not int or not low <= n <= high:
        raise ValueError(f"표본 행 수(sample)는 {low:,} 이상 {high:,} 이하의 정수로 입력하세요.")
    return n


@app.post("/api/shape")
def shape():
    """행 수와 컬럼의 이름·타입·종류. 조회는 행 수 세기뿐이다. count가 false면(통계를 끈 때) 행 수도 세지 않고 null(데이터를 읽지 않는다)."""
    body = request.json
    table = base_table(body)
    return jsonify(prep_profile.shape(table, server.row_count(body.get("schema"), body["base"]) if body.get("count", True) else None))


def sampled(body, key, size, fresh):
    """이 테이블의 표본 (pyarrow 표, 표본 정보 {rows, method, passed}). 방금 같은 크기로 읽은 것이 있으면 그것(fresh면 새로 읽는다).
    표본은 profiler가 싼 방법부터 차례로 읽는다. 행 수는 /api/shape가 세어 둔 것을 쓴다."""
    held = SAMPLES.get(key)
    if held and held[0] == size and not fresh:
        return held[1], held[2]
    con, table, base = get_con(), base_table(body), body["base"]
    rows = server.row_count(body.get("schema"), base)
    part = profiler.spec_table(body.get("schema") or "", base).get("partition")
    arrow, method, passed = run(lambda: profiler.sample(con, table, body.get("schema") or "", base, size, rows, part))
    info = {"rows": arrow.num_rows, "method": method, "passed": passed}
    SAMPLES.clear()  # 표본은 마지막 테이블 하나만 메모리에 둔다.
    SAMPLES[key] = (size, arrow, info)
    return arrow, info


def file_facts(base):
    """Parquet 파일에 연결했으면 그 테이블 파일의 정보로 구한 전체 기준 값(profiler.parquet_facts). 아니면 None."""
    files = sorted(Path(f) for f in (state["opened"] or {}).get("files", []))  # 연결할 때와 같은 차례(source.table_names)
    for path, name in zip(files, source.table_names(files)):
        if name == base and path.suffix.lower() == ".parquet":
            return profiler.parquet_facts(path)
    return None


def described(found, body):
    """캐시에서 꺼낸 통계에 지금 데이터 명세서의 설명을 붙인다(명세서를 바꾸면 바로 보이게 캐시에는 넣지 않는다)."""
    spec = profiler.spec_table(body.get("schema") or "", body["base"])
    columns = spec.get("columns", {})
    for column in found["columns"]:
        column["infer"] = {**column.get("infer", {}), "description": columns.get(column["name"], {}).get("description", "")}
    return {**found, "description": spec.get("description", "")}


@app.post("/api/profile")
def profile():
    """컬럼별 통계·추천과 추론(infer: 빈 값 비율, 단일값, 날짜 형식, 플래그, Parquet 파일 정보의 전체 기준 값, 명세서 설명).
    테이블 전체가 아니라 profiler의 표본으로 구하고 테이블별로 캐시한다. refresh가 true면 표본을 새로 읽어 다시 구한다."""
    body = request.json
    key, size, fresh = table_key(body), sample_size(body), body.get("refresh") is True
    found = None if fresh else profiler.cached(key, size)
    if found is None or "profile" not in found:
        arrow, info = sampled(body, key, size, fresh)
        stats = run(lambda: prep_profile.profile(ibis.memtable(arrow)))  # 표본은 이 PC에서 센다
        guesses, facts = profiler.infer(arrow), file_facts(body["base"])
        for column in stats["columns"]:
            column["infer"] = {**guesses.get(column["name"], {}), **({"exact": facts["columns"][column["name"]]} if facts and column["name"] in facts["columns"] else {})}
        found = profiler.remember(key, size, profile={**stats, **({"file_rows": facts["rows"]} if facts else {})}, sampled=info)
    return jsonify({**described(found["profile"], body), "sampled": {**found["sampled"], "saved": found["saved"]}})


@app.post("/api/patterns")
def patterns():
    """글자 컬럼의 타입 추천(날짜로, 숫자로, 글자를 지우고 숫자로). profile과 같은 표본에서 구하고 캐시한다(refresh면 다시)."""
    body = request.json
    key, size, fresh = table_key(body), sample_size(body), body.get("refresh") is True
    found = None if fresh else profiler.cached(key, size)
    if found is None or "patterns" not in found:
        arrow, info = sampled(body, key, size, False)  # profile이 방금 읽은(refresh면 새로 읽은) 표본
        found = profiler.remember(key, size, patterns=run(lambda: prep_profile.patterns(ibis.memtable(arrow), size)), sampled=info)
    return jsonify(columns=found["patterns"])


@app.get("/api/spec")
def spec():
    """고른 데이터 명세서의 경로와 그 안의 테이블 이름들. 명세서를 읽을 수 없으면 그 이유(error)도."""
    path = profiler.spec_path()
    try:
        return jsonify(path=path, tables=sorted(profiler.read_spec(path)["tables"]) if path else [])
    except ValueError as e:
        return jsonify(path=path, tables=[], error=str(e))


@app.post("/api/spec")
def save_spec():
    """데이터 명세서 경로를 읽어 검사한 뒤 저장한다(빈 글자면 명세서를 쓰지 않음). 설명은 바로, 파티션은 다음에 표본을 읽을 때부터 쓴다."""
    tables = profiler.save_spec_path(request.json.get("path"))
    return jsonify(path=profiler.spec_path(), tables=tables)


@app.post("/api/head")
def head():
    """정보 탭에 보일 앞 20행(전처리 전): 통계를 구할 때 읽어 둔 표본(이 PC)의 앞부분. 통계를 캐시에서 꺼내 표본이 없으면 통계와 같은 방법으로
    표본을 한 번 읽는다(화면은 통계를 켰을 때만 부른다)."""
    import polars as pl
    body = request.json
    arrow = sampled(body, table_key(body), sample_size(body), False)[0]
    return jsonify(rows=to_json(pl.from_arrow(arrow.slice(0, PREVIEW_ROWS))))


@app.post("/api/spec/table")
def spec_for_table():
    """이 테이블의 명세서 부분: 테이블 설명, 파티션, 컬럼마다 설명. 명세서가 없거나 이 테이블이 없으면 빈 값. 데이터는 읽지 않는다(명세서 탭)."""
    body = request.json
    found = profiler.spec_table(body.get("schema") or "", body["base"])
    return jsonify(path=profiler.spec_path(), description=found.get("description", ""), partition=found.get("partition"),
                   columns={name: item.get("description", "") for name, item in found.get("columns", {}).items()})


@app.post("/api/preview")
def preview():
    """앞 20행, 결과 컬럼의 타입, 컬럼마다 어느 recipe 컬럼에서 나왔는지(sources)와 파생 컬럼인지(derived), 전처리 전후의 행 수.
    행 수를 바꿀 수 있는 일이 없으면 후는 전과 같다."""
    recipe, info = request.json, {}
    expr, origin = built(recipe, info)
    rows = run(lambda: result.get_rows(expr, PREVIEW_ROWS))
    before = server.row_count(recipe.get("schema"), recipe["base"])
    after = run(lambda: result.get_count(expr)) if prep.changes_rows(recipe) else before
    return jsonify(rows=to_json(rows), types=[prep.type_text(t) for t in expr.schema().values()], before=before, after=after,
                   sources=[origin[name][0] for name in expr.columns], derived=[origin[name][1] for name in expr.columns], info=info,
                   local=local_mode(recipe))


@app.post("/api/prune")
def prune():
    """뺄 만한 컬럼 제안(prep_profile.prune): 값이 하나뿐·거의 한 값·상관이 아주 높은 숫자 컬럼. 전처리 탭의 표본(이 PC)으로만 구한다
    (표본을 아직 읽지 않았으면 그때 한 번 읽는다). 빼는 것은 화면에서 사용자가 고른다."""
    body = request.json
    arrow, info = sampled(body, table_key(body), sample_size(body), False)
    return jsonify(columns=prep_profile.prune(arrow), sampled=info)


def analyzed(body):
    """분석(analysis.analyze). body = {recipe, how, sample, full}. recipe의 정렬은 분석에 쓰지 않아 뺀다. 대시보드 카드(dash_calc)도 쓴다.
    기본은 전처리 탭과 같은 표본(sample행, 같은 테이블이면 읽어 둔 것)에 recipe를 적용해 이 PC에서 구한다.
    full이면 테이블 전체에 recipe를 적용한 식을 연결(DB, local_mode면 이 PC의 작업 사본)에서 구한다. 결과에 어느 쪽으로 구했는지(data)를 붙인다."""
    recipe, how = {**body.get("recipe", {}), "sorts": []}, body.get("how") or {}
    if body.get("full") is True:
        expr = built(recipe)[0]
        return {**run(lambda: analysis.analyze(expr, how)), "data": {"full": True}}
    key, size = table_key(recipe), sample_size(body)
    arrow, info = sampled(recipe, key, size, False)
    con = get_con()
    found = run(lambda: analysis.analyze(prep.build(con, recipe, ibis.memtable(arrow))[0], how))  # 표본은 이 PC에서 센다
    return {**found, "data": {"full": False, **info}}


@app.post("/api/analyze")
def analyze():
    return jsonify(analyzed(request.json))


@app.post("/api/dupes")
def dupes():
    """dedupe 기준으로 중복 묶음 수와 빠질 행 수. local_mode면 작업 사본에서 센다."""
    recipe, con = request.json, get_con()
    if local_mode(recipe):
        table = working_copy(recipe)
        return jsonify(run(lambda: prep.dupes(held.local(), {**recipe, "filters": []}, table)))
    return jsonify(run(lambda: prep.dupes(con, recipe)))


def make_table(recipe, name):
    """연결한 DB에 recipe의 결과를 새 테이블로 만들고 그 행 수를 돌려준다.
    폴더의 DuckDB 파일 연결은 읽기 전용이라 만드는 동안만 쓰기로 다시 열고, 끝나면 읽기 전용으로 되돌린다.
    local_mode면 이 PC의 작업 사본에서 계산한 결과를 묶음씩 넣기만 한다(DB는 계산하지 않는다). 넣다가 실패하면 만들던 테이블을 지운다."""
    con, opened, schema = get_con(), state["opened"], recipe.get("schema") or None
    if "files" in opened:
        raise ValueError("Parquet·CSV·JSON·Excel 파일 연결에는 테이블을 만들 DB가 없습니다. 파일로 저장하세요.")
    parse.check_table_name(name)
    if name.lower() in [t.lower() for t in source.list_tables(con, schema)]:
        raise ValueError(f"'{name}'은 이미 있는 테이블입니다.")
    if local_mode(recipe):
        expr, db = built(recipe)[0], con
        return run(lambda: insert_all(db, name, schema, expr))
    reopen = opened["kind"] == "folder"
    if reopen:
        source.close(con)
        state["con"] = None
    try:
        if reopen:
            try:
                state["con"] = server.open_con(opened, read_only=False)
            except Exception as e:
                raise ValueError(f"쓰기로 열 수 없습니다. 다른 프로그램이 이 파일을 열고 있는지 확인하세요. ({e})") from None
        db = get_con()

        def create():
            db.create_table(name, prep.build(db, recipe)[0], database=schema)
            return result.get_count(source.get_table(db, name, schema))

        return run(create)
    finally:
        server.forget_tables()  # 새 테이블이 생겼고, 다시 연 연결의 테이블 식을 써야 한다
        if reopen:
            if state["con"] is not None:
                source.close(state["con"])
            state["con"] = server.open_con(opened)


def plain_rows(arrow):
    """Arrow 표를 Ibis가 DB에 올릴 pandas 표로. Ibis의 SQL 연결은 memtable을 pandas로 바꿔 한 행씩 넣는데, 기본 변환은 빈 값이 있는
    정수 컬럼을 실수로 바꿔 큰 정수가 틀어지고(2^53 넘는 값) 빈 날짜시각은 NaT가 되어 넣지 못한다. 그래서 값을 Python 값(정수·날짜·None)으로 둔다."""
    frame = arrow.to_pandas(integer_object_nulls=True, date_as_object=True, timestamp_as_object=True)
    return frame.astype(object).where(frame.notna(), None)


def drop_memtable(db, op):
    """insert가 DB에 올린 임시 테이블(memtable)을 지운다. Ibis의 drop_table이 안 되는 연결(SQLite: 'incomplete input')은
    그 방언으로 따옴표를 친 DROP TABLE IF EXISTS를 직접 보낸다. 지우지 못해도 넣은 결과에는 영향이 없어 오류는 내지 않는다."""
    import contextlib
    try:
        db.drop_table(op.name, force=True)
    except Exception:
        exp = sql_exec.glot()[1]
        with contextlib.suppress(Exception):
            cursor = db.raw_sql(f"DROP TABLE IF EXISTS {exp.to_identifier(op.name, quoted=True).sql(sql_exec.dialect_of(db))}")
            if cursor is not getattr(db, "con", None):
                getattr(cursor, "close", lambda: None)()
    getattr(db, "_memtables", set()).discard(op)  # Ibis가 다시 올리지 않았다고 여기지 않게


def insert_all(db, name, schema, expr):
    """db에 name 테이블을 expr의 스키마로 만들고 expr(이 PC의 식)의 결과를 묶음씩 넣는다. 행 수를 돌려준다. 실패하면 만든 테이블을 지운다.
    Ibis는 묶음을 DB의 임시 테이블(memtable)로 올린 뒤 INSERT … SELECT로 넣는데, 그 임시 테이블을 프로그램이 끝날 때까지 두고
    SQLite에서는 지우는 코드가 돌지 않는다. 묶음마다 바로 지운다(이름이 ibis_pyarrow_memtable_무작위라 사용자 테이블과 겹치지 않는다)."""
    import pyarrow as pa
    db.create_table(name, schema=expr.schema(), database=schema)
    try:
        rows = 0
        for batch in result.batches(expr):
            part = ibis.memtable(plain_rows(pa.Table.from_batches([batch])), schema=expr.schema())
            try:
                db.insert(name, part, database=schema)
            finally:
                drop_memtable(db, part.op())
            rows += batch.num_rows
    except BaseException:
        db.drop_table(name, database=schema, force=True)
        raise
    finally:
        server.forget_tables()  # 새 테이블이 생겼다
    return rows


def scan_sql(recipe):
    """전처리 실행이 읽을 기준 테이블(행 거르기 포함)의 (SQL, 방언). 처리 용량 확인에 쓴다. 작업 사본으로 계산해도 DB에서 읽는 양은 이것이다."""
    con, schema = get_con(), recipe.get("schema") or None
    parse.check_name(recipe["base"], server.table_names(con, schema), "테이블")
    t = server.any_table(con, recipe["base"], schema, vts={})
    if recipe.get("filters"):
        t = t.filter(*[parse.predicate(cond, t) for cond in recipe["filters"]])
    return server.expr_sql(con, t)


@app.post("/api/run")
def run_recipe():
    """target이 {"kind": "file", "format": "parquet" | "csv"}면 파일로 내려보내고, {"kind": "table", "name": 이름}이면 DB에 새 테이블로 만든다."""
    recipe, target = request.json["recipe"], request.json["target"]
    con = get_con()  # 실행 전 처리 용량 확인: 원격 DB는 작업 사본으로 받을 때(아직 없으면)만, 나머지는 읽는 양
    if not held.remote(con) or (local_mode(recipe) and copy_key(recipe) not in held.store["items"]):
        if ask := server.scan_gate(request.json, lambda: scan_sql(recipe)):
            return ask
    if target.get("kind") == "table":
        return jsonify(ok=True, rows=make_table(recipe, target.get("name", "")))
    if target.get("kind") != "file" or target.get("format") not in FILE_FORMATS:
        raise ValueError("저장 방식은 file(parquet, csv) 또는 table이어야 합니다.")
    return server.download(built(recipe)[0], target["format"], guard=False)  # 다시 데이터로 읽을 파일이라 값을 바꾸지 않는다.
