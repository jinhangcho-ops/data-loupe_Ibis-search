"""Ibis 전처리 화면의 라우트. server.app에 달린다. 따로 실행하지 않고 desktop.py가 프로그램 창 안에서 띄운다."""
import re

from flask import jsonify, request

import parse
import prep
import prep_profile
import result
import server
import settings
import source
from server import app, get_con, run, state, to_json

PREVIEW_ROWS = 20
DEFAULT_SAMPLE = 10_000  # 앞에서부터 미리 볼 행 수의 기본값
TABLE_NAME = re.compile(r"[A-Za-z0-9_가-힣]+")
FILE_FORMATS = ["parquet", "csv"]


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


def built(recipe):
    """recipe를 (Ibis 식, 결과 컬럼마다 (recipe상 컬럼, 파생 컬럼인지))로 만든다. 평균 등을 구하는 조회가 있어 run으로 돌린다."""
    con = get_con()
    return run(lambda: prep.build(con, recipe))


def base_table(body):
    con, schema = get_con(), body.get("schema") or None
    parse.check_name(body["base"], source.list_tables(con, schema), "테이블")
    return source.get_table(con, body["base"], schema)


def sample_size(body):
    """body의 sample(앞에서부터 미리 볼 행 수). 없으면 DEFAULT_SAMPLE."""
    low, high = settings.SIZES["sample"]
    n = body.get("sample", DEFAULT_SAMPLE)
    if type(n) is not int or not low <= n <= high:
        raise ValueError(f"미리 볼 행 수(sample)는 {low:,} 이상 {high:,} 이하의 정수로 입력하세요.")
    return n


@app.post("/api/shape")
def shape():
    """행 수와 컬럼의 이름·타입·종류. 조회는 행 수 세기뿐이다."""
    body = request.json
    table = base_table(body)
    return jsonify(prep_profile.shape(table, server.row_count(body.get("schema"), body["base"])))


@app.post("/api/profile")
def profile():
    """컬럼별 통계와 모양 검사가 필요 없는 추천. 글자 수는 연결이 글자 수를 세지 않으면 앞 sample행에서만 구한다(len_sampled)."""
    body = request.json
    table, sample, counts_chars = base_table(body), sample_size(body), server.length_counts_chars()
    return jsonify(run(lambda: prep_profile.profile(table, sample, counts_chars)))


@app.post("/api/patterns")
def patterns():
    """글자 컬럼의 타입 추천(날짜로, 숫자로, 글자를 지우고 숫자로). 앞 sample행에서 걸러 낸 뒤 전체에서 확인한다."""
    body = request.json
    table, sample = base_table(body), sample_size(body)
    return jsonify(columns=run(lambda: prep_profile.patterns(table, sample)))


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
        raise ValueError("Parquet·CSV 파일 연결에는 테이블을 만들 DB가 없습니다. 파일로 저장하세요.")
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
