"""다른 앱으로 보내기의 코어와 라우트: 보낼 곳 저장(settings의 destinations.json), 메시지 만들기, 보내기, 연동 확인.
앱마다 다른 부분(API 부르기)은 adapters의 어댑터가, 비밀 값(토큰·주소)을 어디에 둘지는 credentials가 맡는다(보낼 곳마다 keep: saved·session·env).
화면은 /api/destinations로 종류 표와 보관 방식을 받아 칸을 그린다. 새 앱은 adapters에 어댑터만 더하면 된다."""
import base64
import binascii

from flask import jsonify, request

from lazy import Later
from core import settings
from core import source
from core.server import FREE, app


adapters = Later("integrations.adapters")
credentials = Later("core.credentials")

MAX_DESTINATIONS = 30
MAX_DESTINATION_SIZE = 20_000  # 보낼 곳 하나의 크기(JSON 글자 수)
MAX_TABLE_ROWS = 50  # 글자로 만들어 보내는 표의 최대 행 수
MAX_CELL = 200  # 표 칸 하나의 최대 글자 수(넘으면 잘라 …). 전체 길이는 어댑터가 서비스 한도에서 자른다
MAX_IMAGE = 5 * 1024 * 1024  # 그림(PNG)의 최대 바이트 수
PNG_PREFIX = "data:image/png;base64,"
FILE = "destinations.json"
# 보낼 곳의 종류는 adapters.SENDERS: label·image·fields[{name, label, secret, options?, default?, multiline?, hint?, check?, error?}]·send·check
API_KEYS = ("name", "label", "secret", "options", "default", "multiline", "hint")
FREE.update({"/api/destinations", "/api/destinations/check"})  # 보낼 곳 저장·연동 확인은 연결을 쓰지 않는다


def secret_key(name, field):
    """세션에 둘 때의 열쇠."""
    return f"dest:{name}:{field}"


def kinds_view():
    return {key: {"label": kind["label"], "image": kind["image"], "fields": [{k: f[k] for k in API_KEYS if k in f} for f in kind["fields"]],
                  **({"presets": kind["presets"]} if "presets" in kind else {})} for key, kind in adapters.SENDERS.items()}


def overview():
    """화면에 줄 종류 표·보관 방식과 저장한 보낼 곳. 비밀 값은 넣지 않고 쓸 수 있는지(true/false, env는 변수 이름과 있는지)만 알린다."""
    destinations = {}
    for name, saved in settings.named(FILE).items():
        keep = credentials.keep_of(saved)
        secret = {f["name"] for f in adapters.SENDERS[saved["kind"]]["fields"] if f["secret"]}
        destinations[name] = {**{k: (credentials.shown(secret_key(name, k), keep, v) if k in secret else v) for k, v in saved.items()}, "keep": keep}
    return {"kinds": kinds_view(), "keeps": credentials.KEEPS, "destinations": destinations}


def save(name, destination):
    """보낼 곳을 저장한다(null이면 지운다). 비밀 칸을 비우면 같은 보관 방식으로 둔 값을 그대로 쓴다.
    keep(없으면 saved): saved는 암호문, session은 메모리에만(파일에는 빈 글자), env는 환경변수 이름을 파일에 쓴다."""
    name = name.strip() if isinstance(name, str) else ""
    if destination is None:
        credentials.forget(f"dest:{name}:")
        return settings.save_named(FILE, "보낼 곳", MAX_DESTINATION_SIZE, name, None, MAX_DESTINATIONS)
    if not isinstance(destination, dict) or destination.get("kind") not in adapters.SENDERS:
        raise ValueError(f"보낼 곳의 종류는 {', '.join(adapters.SENDERS)} 중 하나입니다.")
    kind, old = adapters.SENDERS[destination["kind"]], settings.named(FILE).get(name, {})
    keep = credentials.check_keep(destination.get("keep", credentials.DEFAULT_KEEP))
    unknown = set(destination) - {"kind", "done", "keep", *(f["name"] for f in kind["fields"])}
    if unknown or not isinstance(destination.get("done", False), bool):
        raise ValueError(f"보낼 곳에 쓸 수 없는 값이 있습니다: {', '.join(sorted(unknown)) or 'done'}")
    item = {"kind": destination["kind"], "done": destination.get("done", False), "keep": keep}
    same = old.get("kind") == destination["kind"]
    for f in kind["fields"]:
        value = destination.get(f["name"])
        if f["secret"]:
            if value and "check" in f and keep != "env" and not f["check"](value.strip()):
                raise ValueError(f["error"])
            kept = credentials.store(secret_key(name, f["name"]), keep, value, credentials.keep_of(old) if same else None, old.get(f["name"]))
            if kept is None:
                raise ValueError(f"'{f['label']}' 칸을 입력하세요." + (" (환경변수 이름)" if keep == "env" else ""))
            item[f["name"]] = kept
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"'{f['label']}' 칸을 입력하세요.")
        if "options" in f and value not in [v for v, _ in f["options"]]:
            raise ValueError(f"'{f['label']}'은 {', '.join(v for v, _ in f['options'])} 중 하나입니다.")
        if "check" in f and not f["check"](value):
            raise ValueError(f["error"])
        item[f["name"]] = value
    return settings.save_named(FILE, "보낼 곳", MAX_DESTINATION_SIZE, name, item, MAX_DESTINATIONS)


def duration_text(seconds):
    seconds = round(seconds)
    return f"{seconds // 60}분 {seconds % 60}초" if seconds >= 60 else f"{seconds}초"


def table_text(table):
    """표 {columns, rows}를 머리글과 행을 ' | '로 구분한 여러 줄 글자로. 행은 MAX_TABLE_ROWS까지, 칸은 MAX_CELL자까지."""
    if not isinstance(table, dict) or not isinstance(table.get("columns"), list) or not isinstance(table.get("rows"), list):
        raise ValueError('표는 {"columns": [...], "rows": [...]} 모양이어야 합니다.')
    columns, rows = table["columns"], table["rows"]
    cell = lambda value: "" if value is None else (text if len(text := str(value)) <= MAX_CELL else text[:MAX_CELL - 1] + "…")
    lines = [" | ".join(map(cell, columns))]
    for row in rows[:MAX_TABLE_ROWS]:
        lines.append(" | ".join(map(cell, [row.get(c) for c in columns] if isinstance(row, dict) else row)))
    return "\n".join(lines + ([f"… 외 {len(rows) - MAX_TABLE_ROWS}행"] if len(rows) > MAX_TABLE_ROWS else []))


def message(body):
    """/api/send의 body를 보내는 함수들이 받는 메시지로. 빠진 값은 빈 글자(그림은 None)."""
    title, text, rows, seconds, image = body.get("title", ""), body.get("text", ""), body.get("rows"), body.get("seconds"), body.get("image")
    if not isinstance(title, str) or not isinstance(text, str):
        raise ValueError("제목과 내용은 글자여야 합니다.")
    if (rows is not None and type(rows) is not int) or (seconds is not None and type(seconds) not in (int, float)):
        raise ValueError("행수는 정수, 걸린 시간은 수(초)여야 합니다.")
    png = None
    if image is not None:
        if not isinstance(image, str) or not image.startswith(PNG_PREFIX):
            raise ValueError("그림은 PNG data URL(data:image/png;base64,…)이어야 합니다.")
        if len(image) > MAX_IMAGE * 4 // 3 + len(PNG_PREFIX) + 4:
            raise ValueError(f"그림은 {MAX_IMAGE // 1024 // 1024}MB까지 보낼 수 있습니다.")
        try:
            png = base64.b64decode(image[len(PNG_PREFIX):], validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("그림을 읽을 수 없습니다.") from None
    return {"title": title, "text": text, "rows": "" if rows is None else f"{rows:,}", "duration": "" if seconds is None else duration_text(seconds),
            "table": table_text(body["table"]) if body.get("table") is not None else "", "image": png}


def fields_of(name):
    """저장한 보낼 곳 name의 (종류, 칸 값(비밀 값은 풀어서), 비밀 값들). 비밀 값이 없으면(세션 끝남, 환경변수 없음, 풀 수 없음) 그 이유로 ValueError."""
    saved = settings.named(FILE).get(name)
    if saved is None:
        raise ValueError(f"'{name}' 보낼 곳이 없습니다.")
    kind, keep = adapters.SENDERS[saved["kind"]], credentials.keep_of(saved)
    fields, secrets = {}, []
    for f in kind["fields"]:
        if not f["secret"]:
            fields[f["name"]] = saved[f["name"]]
            continue
        value = credentials.resolve(secret_key(name, f["name"]), keep, saved.get(f["name"]))
        if value is None:
            raise ValueError(f"'{name}': " + credentials.missing(keep, f["label"], saved.get(f["name"], "")))
        fields[f["name"]], secrets = value, secrets + [value]
    return kind, fields, secrets


def send(name, body):
    kind, fields, secrets = fields_of(name)
    try:
        kind["send"](fields, message(body))
    except ValueError as e:
        raise ValueError(source.mask(str(e), secrets)) from None  # 오류 문구에서 비밀 값(토큰, 주소)을 가린다.


def check(name):
    """저장한 보낼 곳을 메시지 없이 확인한다(어댑터의 check). 확인 글을 돌려준다."""
    kind, fields, secrets = fields_of(name)
    try:
        return kind["check"](fields)
    except ValueError as e:
        raise ValueError(source.mask(str(e), secrets)) from None


@app.get("/api/destinations")
def destinations():
    return jsonify(overview())


@app.post("/api/destinations")
def save_destination():
    """destination이 null이면 그 이름을 지운다. 저장·삭제 뒤 GET과 같은 모양."""
    save(request.json.get("name"), request.json.get("destination"))
    return jsonify(overview())


@app.post("/api/send")
def send_message():
    """저장한 보낼 곳으로 보낸다. 데이터 작업과 상관없어 차례(turn)를 기다리지 않는다."""
    send(request.json.get("name"), request.json)
    return jsonify(ok=True)


@app.post("/api/destinations/check")
def check_destination():
    """저장한 보낼 곳의 연동 확인(메시지는 보내지 않는다)."""
    return jsonify(message=check(request.json.get("name")))
