"""적재(수집 3단계)의 라우트. server.app에 달린다. 쿼리·분석 화면의 연결을 쓰지 않으므로 모두 차례(turn)를 기다리지 않는다.
비밀번호는 요청에서 받아 그 일에만 쓰고 저장하지 않는다. 오류 문구에서는 ●●●로 가린다."""
from flask import jsonify, request

from lazy import Later
from core import server
from core import source
from core.server import app


collect_config = Later("collect.collect_config")
collect_job = Later("collect.collect_job")
load_job = Later("collect.load_job")
load_kafka = Later("collect.load_kafka")
load_table = Later("collect.load_table")

server.FREE.update({"/api/load/kafka", "/api/load/kafka/check", "/api/load/table/check", "/api/load/start"})


def password_of(value):
    """요청에 실려 온 비밀번호(없으면 "")."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("비밀번호는 글자여야 합니다.")
    return value


def secrets_password(body):
    """시작 요청의 secrets.password."""
    return password_of((body.get("secrets") or {}).get("password"))


def hidden(work, password):
    """work()의 오류 문구에서 비밀번호를 가린다."""
    try:
        return work()
    except Exception as e:
        raise ValueError(source.mask(str(e), [password], load_kafka.HIDE)) from None


@app.get("/api/load/kafka")
def kafka_list():
    return jsonify(kafka=load_kafka.destinations())


@app.post("/api/load/kafka")
def kafka_save():
    """item이 null이면 그 이름을 지운다."""
    return jsonify(kafka=load_kafka.save(request.json.get("name"), request.json.get("item")))


@app.post("/api/load/kafka/check")
def kafka_check():
    password = password_of(request.json.get("password"))
    return jsonify(hidden(lambda: load_kafka.check(request.json.get("name") or "", password), password))


@app.post("/api/load/table/check")
def table_check():
    """적재 전 확인: 테이블이 있는지, 결과 파일과 컬럼이 맞는지, 이 프로그램이 만든 테이블인지. 결과 파일이 아직 없으면 columns는 null."""
    password = password_of(request.json.get("password"))
    config = collect_config.normalize(request.json.get("config"))
    if config["load"]["target"] != "table":
        raise ValueError("적재할 곳이 DB 테이블이 아닙니다.")
    load_table.ready(config)

    def check():
        path = collect_job.output_path(config) if config["output"]["path"].strip() else None
        columns = None
        if path is not None and path.is_file():
            columns = load_table.arrow_columns(load_job.file_schema(path))
        return load_table.check(config, password, columns)

    return jsonify(hidden(check, password))


@app.post("/api/load/start")
def load_start():
    password = secrets_password(request.json)
    collect_job.start_load(request.json.get("config"), password)
    return jsonify(ok=True)
