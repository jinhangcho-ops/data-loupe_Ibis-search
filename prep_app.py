"""Ibis 전처리 화면의 라우트. server.app에 달린다. 따로 실행하지 않고 desktop.py가 프로그램 창 안에서 띄운다."""
import datetime as dt
import json
import re
from pathlib import Path

from flask import jsonify, request
from lazy import ibis

import analysis
import parse
import prep
import prep_profile
import profiler
import result
import server
import settings
import source
import use_counts
from server import app, get_con, run, state, to_json

PREVIEW_ROWS = 20
DEFAULT_SAMPLE = 5_000  # 통계를 낼 표본 행 수의 기본값
TABLE_NAME = re.compile(r"[A-Za-z0-9_가-힣]+")
FILE_FORMATS = ["parquet", "csv"]
server.CONSTANTS.update(sample=DEFAULT_SAMPLE, sample_methods=profiler.METHODS, analysis=analysis.LIMITS)  # 화면(prep.html)의 표본 행 수 기본값, 표본 방법 이름, 분석의 상한들
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


def built(recipe):
    """recipe를 (Ibis 식, 결과 컬럼마다 (recipe상 컬럼, 파생 컬럼인지))로 만든다. 평균 등을 구하는 조회가 있어 run으로 돌린다."""
    con = get_con()
    return run(lambda: prep.build(con, recipe))


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
    """행 수와 컬럼의 이름·타입·종류. 조회는 행 수 세기뿐이다."""
    body = request.json
    table = base_table(body)
    return jsonify(prep_profile.shape(table, server.row_count(body.get("schema"), body["base"])))


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


@app.post("/api/preview")
def preview():
    """앞 20행, 결과 컬럼의 타입, 컬럼마다 어느 recipe 컬럼에서 나왔는지(sources)와 파생 컬럼인지(derived), 전처리 전후의 행 수.
    행 수를 바꿀 수 있는 일이 없으면 후는 전과 같다."""
    recipe = request.json
    expr, origin = built(recipe)
    rows = run(lambda: result.get_rows(expr, PREVIEW_ROWS))
    before = server.row_count(recipe.get("schema"), recipe["base"])
    after = run(lambda: result.get_count(expr)) if prep.changes_rows(recipe) else before
    return jsonify(rows=to_json(rows), types=[prep.type_text(t) for t in expr.schema().values()], before=before, after=after,
                   sources=[origin[name][0] for name in expr.columns], derived=[origin[name][1] for name in expr.columns])


@app.post("/api/analyze")
def analyze():
    """분석(analysis.analyze). body = {recipe, how, sample, full}. recipe의 정렬은 분석에 쓰지 않아 뺀다.
    기본은 전처리 화면과 같은 표본(sample행, 같은 테이블이면 읽어 둔 것)에 recipe를 적용해 이 PC에서 구한다.
    full이면 테이블 전체에 recipe를 적용한 식을 연결(DB)에서 구한다. 결과에 어느 쪽으로 구했는지(data)를 붙인다."""
    body = request.json
    recipe, how = {**body.get("recipe", {}), "sorts": []}, body.get("how") or {}
    if body.get("full") is True:
        expr = built(recipe)[0]
        return jsonify({**run(lambda: analysis.analyze(expr, how)), "data": {"full": True}})
    key, size = table_key(recipe), sample_size(body)
    arrow, info = sampled(recipe, key, size, False)
    con = get_con()
    found = run(lambda: analysis.analyze(prep.build(con, recipe, ibis.memtable(arrow))[0], how))  # 표본은 이 PC에서 센다
    return jsonify({**found, "data": {"full": False, **info}})


@app.post("/api/dupes")
def dupes():
    """dedupe 기준으로 중복 묶음 수와 빠질 행 수."""
    recipe, con = request.json, get_con()
    return jsonify(run(lambda: prep.dupes(con, recipe)))


def check_table_name(name, what="테이블"):
    """새로 만들거나 넣을 테이블(스키마) 이름 검사. 적재(load_table)도 같은 규칙을 쓴다."""
    if not isinstance(name, str) or not TABLE_NAME.fullmatch(name):
        raise ValueError(f"{what} 이름은 영문·숫자·밑줄·한글만 쓸 수 있습니다(공백·따옴표·점 없이).")


def make_table(recipe, name):
    """연결한 DB에 recipe의 결과를 새 테이블로 만들고 그 행 수를 돌려준다.
    폴더의 DuckDB 파일 연결은 읽기 전용이라 만드는 동안만 쓰기로 다시 열고, 끝나면 읽기 전용으로 되돌린다."""
    con, opened, schema = get_con(), state["opened"], recipe.get("schema") or None
    if "files" in opened:
        raise ValueError("Parquet·CSV·JSON·Excel 파일 연결에는 테이블을 만들 DB가 없습니다. 파일로 저장하세요.")
    check_table_name(name)
    if name.lower() in [t.lower() for t in source.list_tables(con, schema)]:
        raise ValueError(f"'{name}'은 이미 있는 테이블입니다.")
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
        if reopen:
            if state["con"] is not None:
                source.close(state["con"])
            state["con"] = server.open_con(opened)


@app.post("/api/run")
def run_recipe():
    """target이 {"kind": "file", "format": "parquet" | "csv"}면 파일로 내려보내고, {"kind": "table", "name": 이름}이면 DB에 새 테이블로 만든다."""
    recipe, target = request.json["recipe"], request.json["target"]
    if target.get("kind") == "table":
        return jsonify(ok=True, rows=make_table(recipe, target.get("name", "")))
    if target.get("kind") != "file" or target.get("format") not in FILE_FORMATS:
        raise ValueError("저장 방식은 file(parquet, csv) 또는 table이어야 합니다.")
    return server.download(built(recipe)[0], target["format"], guard=False)  # 다시 데이터로 읽을 파일이라 값을 바꾸지 않는다.
