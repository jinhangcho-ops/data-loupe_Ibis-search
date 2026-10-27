"""연동 화면(/integrations): 다른 앱·서비스와 잇는 곳을 한데 모은다. 서비스마다 하는 일(보내기·받기·가져오기·묻기)과 상태를 보이고,
GA4 가져오기의 설정·확인·실행 라우트를 둔다. 보내기(Slack·Notion·웹훅)는 notify, Slack 봇은 bot_app, LLM은 app의 /api/llm을 쓴다.
연결(데이터 작업)을 쓰지 않으므로 라우트는 차례(turn)를 기다리지 않는다(GA4 결과를 쿼리로 열 때는 /api/collect/open)."""
import os
from pathlib import Path

from flask import jsonify, request

from integrations import bot_app
from lazy import Later, preload
from core import settings
from core.server import FREE, app


adapters = Later("integrations.adapters")
ga4 = Later("integrations.adapters.ga4")
llm = Later("integrations.adapters.llm")
uploads = Later("collect.uploads")

GA4_FILE = "ga4.json"  # {key_path, property, start, end, dimensions, metrics, output}. 서비스 계정 JSON은 경로만(키는 그 파일에만)
GA4_DEFAULT = {"key_path": "", "property": "", "start": "28daysAgo", "end": "yesterday", "dimensions": "date", "metrics": "activeUsers, sessions", "output": ""}
FREE.update({"/api/integrations", "/api/ga4", "/api/ga4/check", "/api/ga4/fetch"})


def ga4_settings():
    found = settings.read(GA4_FILE)
    return {**GA4_DEFAULT, **{k: v for k, v in (found or {}).items() if k in GA4_DEFAULT and isinstance(v, str)}}


def services():
    """서비스 목록과 상태. 새 어댑터는 여기 한 줄과 화면 카드를 더한다(보내기 어댑터는 SENDERS에 넣으면 보내기 카드에 저절로 나온다)."""
    destinations = settings.named("destinations.json")
    count = lambda kind: sum(1 for d in destinations.values() if d.get("kind") == kind)
    bot = bot_app.BOT["bot"]
    try:
        model = llm.client().name
    except ValueError as e:
        model = f"설정 오류: {e}"
    g = ga4_settings()
    out = [{"key": key, "label": kind["label"], "abilities": ["send"], "status": f"보낼 곳 {count(key)}개" if count(key) else "등록 안 됨", "ready": bool(count(key))}
           for key, kind in adapters.SENDERS.items()]
    out += [{"key": "slack_bot", "label": "Slack 봇", "abilities": ["receive"], "status": "켜짐" if bot and bot.running() else "설정됨(꺼짐)" if bot_app.config() else "설정 안 됨",
             "ready": bool(bot and bot.running())},
            {"key": "ga4", "label": "Google Analytics 4", "abilities": ["fetch"], "status": f"속성 {g['property']}" if g["key_path"] and g["property"] else "설정 안 됨",
             "ready": bool(g["key_path"] and g["property"])},
            {"key": "llm", "label": "LLM (질문으로 SQL)", "abilities": ["ask"], "status": model, "ready": model != "없음" and not model.startswith("설정 오류")}]
    return out


PRELOAD = ("integrations.adapters", "integrations.adapters.ga4", "integrations.adapters.llm", "integrations.commands")  # 연동 화면을 열면 뒤에서 미리 올리는 기능 모듈


@app.get("/integrations")
def integrations_page():
    preload(*PRELOAD)
    from core import server
    return server.page("integrations/integrations.html")


@app.get("/api/integrations")
def integrations():
    return jsonify(services=services())


@app.get("/api/ga4")
def ga4_state():
    return jsonify(ga4_settings())


@app.post("/api/ga4")
def save_ga4():
    """GA4 설정을 저장한다(서비스 계정 JSON은 경로만). 보고서 칸은 검사해 둔다."""
    body = {k: (request.json.get(k) or "").strip() if isinstance(request.json.get(k), str) else "" for k in GA4_DEFAULT}
    if body["key_path"]:
        ga4.read_key(body["key_path"])
    if body["property"] or body["metrics"]:
        ga4.check_report({"property": body["property"], "start": body["start"], "end": body["end"], "dimensions": body["dimensions"], "metrics": body["metrics"]})
    settings.write(GA4_FILE, body)
    return jsonify(body)


@app.post("/api/ga4/check")
def check_ga4():
    """보고서를 받지 않고 서비스 계정 인증과 속성 접근을 확인한다."""
    g = ga4_settings()
    if not g["key_path"]:
        raise ValueError("서비스 계정 JSON 경로를 넣고 저장하세요.")
    return jsonify(message=ga4.check(g["key_path"], g["property"]))


@app.post("/api/ga4/fetch")
def fetch_ga4():
    """저장한 설정으로 보고서를 받아 Parquet 파일로 저장한다. 같은 이름이 있으면 덮어쓰지 않는다. 업로드 기록에 남긴다."""
    g = ga4_settings()
    if not g["key_path"]:
        raise ValueError("서비스 계정 JSON 경로를 넣고 저장하세요.")
    out = Path(g["output"]) if g["output"] else None
    if out is None or out.suffix.lower() != ".parquet":
        raise ValueError("저장할 파일 경로(.parquet)를 넣고 저장하세요.")
    exists = lambda: ValueError(f"'{out.name}' 파일이 이미 있습니다. 다른 이름을 쓰세요(덮어쓰지 않습니다).")
    if out.exists():
        raise exists()
    if not out.parent.is_dir():
        raise ValueError("저장할 폴더가 없습니다.")
    notes = []
    frame, total = ga4.fetch(g["key_path"], g, notes)
    part = out.with_name(out.name + ".part")  # 쓰다 끊겨도 반쯤 쓴 파일이 결과 이름으로 남지 않게
    try:
        frame.write_parquet(part)
        if out.exists():  # 보고서를 받는 사이 같은 이름이 생겼을 수 있다: 바꿔치기 직전에 다시 본다
            raise exists()
        os.replace(part, out)
    finally:
        part.unlink(missing_ok=True)
    uploads.log_file(out, frame.height)
    return jsonify(path=str(out), rows=frame.height, columns=frame.columns, total=total, notes=notes)
