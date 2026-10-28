"""화면 작업 임시 저장(초안)의 라우트. 설정 폴더 아래 drafts/화면.json 하나씩. 앱이 갑자기 꺼져도 만들던 조건·단계·편집을 다시 열 때 이어서 하게 한다.
GET·POST·DELETE /api/draft?screen=search|prep|dashboard — 연결을 쓰지 않으므로 차례(turn)를 기다리지 않는다(FREE).
담는 것: 화면이 보낸 조건·단계·입력값, 연결(열쇠와 사람이 읽을 이름, 비밀번호 없음), 저장 시각. 결과 행은 화면이 보내지 않고, 비밀번호는 문자열마다 가려서 쓴다."""
import json
from datetime import datetime
from urllib.parse import urlsplit

from flask import jsonify, request

from core import server
from core import settings
from core import source
from core.server import app

SCREENS = ("search", "prep", "dashboard")  # 허용하는 화면. 이 밖의 값은 파일 이름이 될 수 없다
MAX_BYTES = 1024 * 1024  # 이보다 크면 저장하지 않는다
server.FREE.add("/api/draft")


def draft_path():
    """요청의 screen에 맞는 초안 파일 경로. 허용 목록 밖이면 ValueError."""
    screen = request.args.get("screen")
    if screen not in SCREENS:
        raise ValueError("알 수 없는 화면이에요.")
    return settings.PATH.parent / "drafts" / f"{screen}.json"


def connection():
    """지금 연결 {key, name}(비밀번호 없음). 연결이 없으면 None. name은 사람이 읽을 이름."""
    opened = server.state["opened"]
    if opened is None:
        return None
    info = source.recent(opened)
    if info["kind"] == "url":
        parts = urlsplit(info["url"])
        name = (parts.hostname or "") + parts.path.rstrip("/") or info["url"]
    else:
        name = f"파일 {len(opened['files'])}개" if opened.get("files") else (opened.get("file") or info["path"]).replace("\\", "/").rsplit("/", 1)[-1]
    return {"key": server.connection_key(), "name": name}


def masked(value):
    """안의 모든 문자열에서 비밀번호를 가린 같은 모양의 값."""
    if isinstance(value, str):
        return source.mask(value, server.state["passwords"])
    if isinstance(value, list):
        return [masked(v) for v in value]
    if isinstance(value, dict):
        return {k: masked(v) for k, v in value.items()}
    return value


def read(path):
    """저장된 초안. 없거나 깨졌거나 모양이 다르면 None."""
    found = settings.read_file(path)
    return found if found and isinstance(found.get("state"), dict) and isinstance(found.get("saved"), str) else None


@app.get("/api/draft")
def get_draft():
    """{draft: {saved, connection, state} 또는 None, current: 지금 연결, same: 이어서 할 수 있는 연결인가}. 대시보드는 연결 없이 고치므로 늘 같다."""
    draft, current = read(draft_path()), connection()
    mine = draft and draft.get("connection")
    same = bool(draft) and (request.args["screen"] == "dashboard" or bool(current and mine and mine.get("key") == current["key"]))
    return jsonify(draft=draft, current=current, same=same)


@app.post("/api/draft")
def save_draft():
    """body {state}를 저장한다. 너무 크면 저장하지 않고 saved=false."""
    path, state = draft_path(), (request.json or {}).get("state")
    if not isinstance(state, dict):
        raise ValueError("저장할 상태가 없어요.")
    data = {"saved": datetime.now().isoformat(timespec="seconds"), "connection": connection(), "state": masked(state)}
    if len(json.dumps(data, ensure_ascii=False).encode()) > MAX_BYTES:
        return jsonify(saved=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    settings.write_atomic(path, data)
    return jsonify(saved=True)


@app.delete("/api/draft")
def delete_draft():
    draft_path().unlink(missing_ok=True)
    return jsonify(ok=True)
