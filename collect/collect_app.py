"""수집 화면의 라우트. server.app에 달린다. 따로 실행하지 않고 desktop.py가 프로그램 창 안에서 띄운다.
연결을 쓰지 않으므로 /api/collect/open(결과 파일에 연결)만 빼고 차례(turn)를 기다리지 않는다."""
from pathlib import Path

from flask import jsonify, request

from collect import load_app  # 적재 라우트가 앱에 달린다.
from lazy import Later, preload
from core import server
from core import settings
from core import source
from core.server import app, get_con


collect_browser = Later("collect.collect_browser")
collect_config = Later("collect.collect_config")
collect_job = Later("collect.collect_job")
collect_limits = Later("collect.collect_limits")
collect_request = Later("collect.collect_request")
collect_store = Later("collect.collect_store")
collect_web = Later("collect.collect_web")
load_kafka = Later("collect.load_kafka")
uploads = Later("collect.uploads")

MAX_COLLECTION_SIZE = 100_000  # 구성 하나의 크기(JSON 글자 수)
FILE_TYPES = {"parquet": ("Parquet (*.parquet)",), "csv": ("CSV (*.csv)",)}

# 수집 화면(collect.html)이 쓰는 기본값과 숫자 칸의 범위. 범위 이름은 구성의 경로(paging.max_pages)나 limit.이름.
server.FREE.update({"/api/collect/parse", "/api/collect/secretize", "/api/collect/env", "/api/collect/try", "/api/collect/sample",
                    "/api/collect/estimate", "/api/collect/ask_path", "/api/collect/start", "/api/collect/stop", "/api/collect/status",
                    "/api/collections", "/api/limits", "/api/limits/reset", "/api/collect/browser/open", "/api/collect/browser/close",
                    "/api/collect/browser/state", "/api/collect/browser/pick", "/api/collect/browser/forget", "/api/collect/web/sample",
                    "/api/uploads", "/api/uploads/forget"})


@server.late_constants("collect")
def collect_constants(found):
    found.update(collect=collect_config.DEFAULT, kafka=load_kafka.DEFAULT, limit=collect_limits.DEFAULT)
    found.setdefault("numbers", {}).update({
        **{".".join(where): server.number_range(*spec) for where, spec in collect_config.NUMBERS.items()},
        **{f"limit.{key}": server.number_range(*spec[:3]) for key, spec in collect_limits.NUMBERS.items()}})  # schedule_app이 예약 칸의 범위를 더한다


PRELOAD = ("collect.collect_config", "collect.collect_job", "collect.collect_store", "collect.collect_limits", "collect.collect_request", "collect.collect_http", "collect.collect_web",
           "collect.load_job", "collect.load_table", "collect.load_kafka", "collect.uploads")  # 수집 화면을 열면 뒤에서 미리 올리는 기능 모듈(웹페이지 수집 창은 처음 쓸 때)


@app.get("/collect")
def collect_page():
    preload(*PRELOAD)
    return server.page("collect/collect.html")


@app.post("/api/collect/parse")
def parse_example():
    found, kind, unread = collect_request.parse(request.json.get("text"))
    return jsonify(request=found, format=kind, unread=unread, secrets=collect_request.secrets(found))


@app.post("/api/collect/secretize")
def secretize():
    body = request.json
    return jsonify(request=collect_request.secretize(collect_config.normalize({"request": body.get("request")})["request"], body.get("replace") or []))


@app.post("/api/collect/env")
def env_names():
    """env 파일의 이름들. 값은 주지 않는다."""
    return jsonify(names=list(settings.read_env(request.json.get("path", ""))))


@app.post("/api/collect/try")
def try_first():
    config, env = collect_job.ready(request.json.get("config"), output=False)
    if config["mode"] == "web":
        raise ValueError("웹페이지 모드는 [브라우저 열기]와 미리보기로 확인하세요.")
    try:
        return jsonify(collect_job.first_page(config, env))
    except Exception as e:  # 오류 문구에서 env 값을 가린다.
        raise ValueError(collect_store.req_mask(str(e), collect_request.hidden(env))) from None


@app.post("/api/collect/sample")
def sample():
    return jsonify(collect_job.sample(request.json.get("config"), request.json.get("text")))


@app.post("/api/collect/estimate")
def estimate():
    return jsonify(collect_job.estimate(request.json.get("config")))


@app.post("/api/collect/ask_path")
def ask_path():
    """창의 저장 대화상자로 결과 파일 경로를 묻는다. 형식의 확장자로 끝나지 않으면 붙인다."""
    fmt = request.json.get("format")
    if fmt not in FILE_TYPES:
        raise ValueError("형식은 parquet, csv 중 하나입니다.")
    if server.ask_save is None:
        raise ValueError("저장 위치를 물을 수 없습니다. 프로그램 창에서만 쓸 수 있습니다.")
    name = request.json.get("name") or f"collected.{fmt}"
    chosen = server.ask_save(name if name.lower().endswith("." + fmt) else f"{name}.{fmt}", FILE_TYPES[fmt])
    if not chosen:
        return jsonify(path=None)
    return jsonify(path=chosen if chosen.lower().endswith("." + fmt) else f"{chosen}.{fmt}")


@app.post("/api/collect/start")
def start():
    """secrets.password(적재할 곳의 비밀번호)는 작업 동안 메모리에만 두고 저장하지 않는다."""
    return jsonify(ok=True, resumed=collect_job.start(request.json.get("config"), load_app.secrets_password(request.json)))


@app.post("/api/collect/stop")
def stop():
    collect_job.stop()
    return jsonify(ok=True)


@app.get("/api/collect/status")
def status():
    return jsonify(collect_job.snapshot())


@app.post("/api/collect/browser/open")
def browser_open():
    """수집용 창을 띄우거나 그 주소로 옮긴다. 주소의 {page}는 start(없으면 1)로 채운다."""
    start = request.json.get("start", 1)
    if type(start) is not int:
        raise ValueError("start는 정수여야 합니다.")
    url = request.json.get("url")
    if not isinstance(url, str) or collect_request.env_names({"url": url}):
        raise ValueError("http:// 또는 https:// 주소만 열 수 있습니다({env:…}는 쓸 수 없습니다).")
    collect_browser.open_page(collect_browser.web_url(collect_web.page_url({"url": url, "paging": {"start": start}}, 0)))
    return jsonify(ok=True)


@app.post("/api/collect/browser/close")
def browser_close():
    collect_browser.close()
    return jsonify(ok=True)


@app.get("/api/collect/browser/state")
def browser_state():
    return jsonify(collect_browser.state())


@app.post("/api/collect/browser/pick")
def browser_pick():
    collect_browser.pick(request.json.get("target"), request.json.get("items") or "")
    return jsonify(ok=True)


@app.post("/api/collect/browser/forget")
def browser_forget():
    collect_browser.forget()
    return jsonify(ok=True)


@app.post("/api/collect/web/sample")
def web_sample():
    return jsonify(collect_web.sample(request.json.get("config")))


@app.post("/api/collect/open")
def open_result():
    """결과 파일 하나에 연결한다(차례를 기다린다). 화면은 이어서 /search나 /prep?base=테이블로 간다."""
    path = Path(request.json.get("path") or "")
    if path.suffix.lower() not in (".parquet", ".csv") or not path.is_file():
        raise ValueError("열 결과 파일(Parquet·CSV)이 없습니다.")
    server.switch({"kind": "folder", "path": str(path.parent), "files": [str(path)]})
    return jsonify(table=source.list_tables(get_con())[0])


@app.get("/api/collections")
def collections():
    return jsonify(collections=settings.named(collect_config.COLLECTIONS))


@app.post("/api/collections")
def save_collection():
    """collection이 null이면 그 이름을 지운다. 비밀처럼 보이는 자리에 값이 그대로 있으면 저장하지 않는다."""
    item = request.json.get("collection")
    if item is not None:
        item = collect_config.normalize(item)
        collect_job.check_literals(item)
    name = request.json.get("name")
    if item is None:  # 구성을 지우면 그 예약과 Windows 작업도 지운다(남으면 없는 구성을 계속 실행하려다 실패한다). 예약을 먼저 지워, 지우지 못하면 구성이 남아 다시 지울 수 있다
        from integrations import schedule
        if name in schedule.schedules():
            schedule.save(name, None)
    saved = settings.save_named(collect_config.COLLECTIONS, "구성", MAX_COLLECTION_SIZE, name, item)
    return jsonify(collections=saved)


def limits_view():
    found = collect_limits.groups()
    return {"groups": found, "usage": {name: collect_limits.usage(name) for name in found}}


@app.get("/api/limits")
def limits():
    return jsonify(limits_view())


@app.post("/api/limits")
def save_limit():
    """group이 null이면 그 묶음을 지운다."""
    collect_limits.save(request.json.get("name"), request.json.get("group"))
    return jsonify(limits_view())


@app.post("/api/limits/reset")
def reset_limit():
    name = request.json.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("묶음 이름을 주세요.")
    collect_limits.reset(name.strip())
    return jsonify(limits_view())


@app.get("/api/uploads")
def uploads_list():
    """업로드 기록(새것이 앞)과 기록마다 지울 때 쓸 명령(removal). 보여 주기만 하고 지우지 않는다."""
    return jsonify(entries=[{**e, "removal": uploads.removal(e)} for e in uploads.entries()], statuses=uploads.STATUSES)


@app.post("/api/uploads/forget")
def uploads_forget():
    """기록 하나를 목록에서 뺀다(데이터는 그대로)."""
    uploads.forget(request.json.get("id"))
    return uploads_list()
