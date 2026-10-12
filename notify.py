"""다른 앱으로 보내기: 보낼 곳의 종류(KINDS 표), 보낼 곳의 저장(비밀 값은 Windows DPAPI로 암호화), 보내기, 그 라우트.
새 앱을 더하려면 KINDS에 항목 하나와 보내는 함수(send_이름)만 더하면 된다. 화면은 /api/destinations로 이 표를 받아 칸을 그린다."""
import base64
import binascii
import ctypes
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes

from flask import jsonify, request

import settings
import source
from server import app

SLACK_API = "https://slack.com/api"
TIMEOUT = 15  # 요청 하나를 기다리는 시간(초)
MAX_DESTINATIONS = 30
MAX_DESTINATION_SIZE = 20_000  # 보낼 곳 하나의 크기(JSON 글자 수)
MAX_TABLE_ROWS = 50  # 글자로 만들어 보내는 표의 최대 행 수
MAX_IMAGE = 5 * 1024 * 1024  # 그림(PNG)의 최대 바이트 수
PNG_PREFIX = "data:image/png;base64,"
FILE = "destinations.json"


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]


def dpapi(function, data):
    """Windows DPAPI(crypt32의 CryptProtectData/CryptUnprotectData)로 data(bytes)를 암호화하거나 되돌린다.
    지금 로그인한 Windows 계정만 풀 수 있다. 실패하면 None."""
    buffer = ctypes.create_string_buffer(data, len(data))
    given, out = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), Blob()
    call = getattr(ctypes.windll.crypt32, function)
    call.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    call.restype = wintypes.BOOL
    if not call(ctypes.byref(given), None, None, None, None, 0, ctypes.byref(out)):
        return None
    result = ctypes.string_at(out.data, out.size)
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    ctypes.windll.kernel32.LocalFree(ctypes.cast(out.data, ctypes.c_void_p))
    return result


def protect(text):
    locked = dpapi("CryptProtectData", text.encode("utf-8"))
    if locked is None:
        raise ValueError("비밀 값을 암호화하지 못했습니다.")
    return base64.b64encode(locked).decode()


def reveal(stored):
    """protect로 저장한 글자를 되돌린다. 풀 수 없으면(다른 PC·계정에서 온 파일, 깨진 값) None."""
    try:
        opened = dpapi("CryptUnprotectData", base64.b64decode(stored, validate=True))
    except (binascii.Error, ValueError, TypeError):
        return None
    return None if opened is None else opened.decode("utf-8", "replace")


def post(url, data, headers):
    """요청을 보내고 응답 몸통(bytes)을 돌려준다. 실패하면 상태 코드와 응답 앞부분을 넣은 한국어 오류."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers, method="POST"), timeout=TIMEOUT) as reply:
            return reply.read()
    except urllib.error.HTTPError as e:
        raise ValueError(f"보내지 못했습니다(상태 {e.code}): {e.read().decode('utf-8', 'replace')[:200]}") from None
    except (urllib.error.URLError, OSError) as e:
        raise ValueError(f"보내지 못했습니다: {getattr(e, 'reason', e)}") from None


def slack_call(token, method, body, form=False):
    data = urllib.parse.urlencode(body).encode() if form else json.dumps(body, ensure_ascii=False).encode()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/x-www-form-urlencoded" if form else "application/json; charset=utf-8"}
    reply = json.loads(post(f"{SLACK_API}/{method}", data, headers))
    if not reply.get("ok"):
        raise ValueError(f"Slack 오류: {reply.get('error')}")
    return reply


def send_slack(fields, m):
    """글은 chat.postMessage로. 그림이 있으면 파일 올리기(getUploadURLExternal → 받은 주소에 바이트 → completeUploadExternal)로 글과 함께."""
    token, channel = fields["token"], fields["channel"]
    text = f"*{m['title']}*\n{m['text']}" + (f"\n```\n{m['table']}\n```" if m["table"] else "")
    if not m["image"]:
        slack_call(token, "chat.postMessage", {"channel": channel, "text": text})
        return
    upload = slack_call(token, "files.getUploadURLExternal", {"filename": "image.png", "length": len(m["image"])}, form=True)
    post(upload["upload_url"], m["image"], {"Content-Type": "application/octet-stream"})
    slack_call(token, "files.completeUploadExternal", {"files": [{"id": upload["file_id"], "title": m["title"]}], "channel_id": channel, "initial_comment": text})


# 본문 종류 → (Content-Type, 넣는 값의 이스케이프)
BODIES = {
    "json": ("application/json", lambda value: json.dumps(value, ensure_ascii=False)[1:-1]),  # JSON 글자 안에 들어갈 수 있게(바깥 따옴표 없이)
    "text": ("text/plain; charset=utf-8", lambda value: value),
    "form": ("application/x-www-form-urlencoded", urllib.parse.quote_plus),
}
SLOTS = ["제목", "내용", "행수", "걸린시간", "표", "그림"]
PRESETS = [  # 서비스가 받는 모양의 본문 틀. 실제 서비스에 보내 확인한 것이 아니다.
    {"label": "일반 JSON", "content": "json", "template": '{"title": "{제목}", "text": "{내용}", "rows": "{행수}", "seconds": "{걸린시간}", "table": "{표}", "image": "{그림}"}'},
    {"label": "Teams", "content": "json", "template": r'{"text": "**{제목}**\n\n{내용}\n\n{표}"}'},
    {"label": "Discord", "content": "json", "template": r'{"content": "**{제목}**\n{내용}\n{표}"}'},
    {"label": "글자", "content": "text", "template": "{제목}\n{내용}\n{표}"},
]


def send_webhook(fields, m):
    """본문 틀의 {제목} {내용} {행수} {걸린시간} {표} {그림} 자리를 본문 종류에 맞게 이스케이프해 채워 보낸다. 모르는 {…}는 그대로 둔다."""
    content_type, escape = BODIES[fields["content"]]
    values = {"제목": m["title"], "내용": m["text"], "행수": m["rows"], "걸린시간": m["duration"], "표": m["table"], "그림": base64.b64encode(m["image"]).decode() if m["image"] else ""}
    body = re.sub(r"\{(" + "|".join(SLOTS) + r")\}", lambda found: escape(values[found[1]]), fields["template"])
    if fields["content"] == "json":
        try:
            json.loads(body)
        except ValueError:
            raise ValueError("본문 틀이 JSON이 아닙니다. 값을 채운 결과가 JSON으로 읽히지 않아 보내지 않았습니다.") from None
    post(fields["url"], body.encode("utf-8"), {"Content-Type": content_type})


# 보낼 곳의 종류. label: 화면에 보일 이름, image: 그림을 보낼 수 있는지(webhook은 틀의 {그림} 자리로),
# fields: 설정 칸 [{name, label, secret, options?, default?, multiline?, hint?(칸 아래에 보일 안내), check?(검사 함수), error?(검사에 걸렸을 때 문구)}], send: 보내는 함수(칸 값, 메시지).
KINDS = {
    "slack": {"label": "Slack", "image": True, "send": send_slack, "fields": [
        {"name": "token", "label": "봇 토큰", "secret": True},
        {"name": "channel", "label": "채널 ID(예: C0123456789)", "secret": False}]},
    "webhook": {"label": "웹훅(주소로 보내기)", "image": True, "send": send_webhook, "presets": PRESETS, "fields": [
        {"name": "url", "label": "주소", "secret": True, "check": lambda v: v.startswith("https://"), "error": "주소는 https://로 시작해야 합니다."},
        {"name": "content", "label": "본문 종류", "secret": False, "options": [["json", "JSON"], ["text", "글자"], ["form", "폼(URL 인코딩)"]], "default": "json"},
        {"name": "template", "label": "본문 틀", "secret": False, "multiline": True, "default": PRESETS[0]["template"],
         "hint": "쓸 수 있는 자리: {제목} {내용} {행수} {걸린시간} {표} {그림}"}]},
}
API_KEYS = ("name", "label", "secret", "options", "default", "multiline", "hint")


def kinds_view():
    return {key: {"label": kind["label"], "image": kind["image"], "fields": [{k: f[k] for k in API_KEYS if k in f} for f in kind["fields"]],
                  **({"presets": kind["presets"]} if "presets" in kind else {})} for key, kind in KINDS.items()}


def overview():
    """화면에 줄 종류 표와 저장한 보낼 곳. 비밀 값은 넣지 않고 저장돼 있는지(true)·다시 입력해야 하는지(false)만 알린다."""
    destinations = {}
    for name, saved in settings.named(FILE).items():
        secret = {f["name"] for f in KINDS[saved["kind"]]["fields"] if f["secret"]}
        destinations[name] = {k: (reveal(v) is not None if k in secret else v) for k, v in saved.items()}
    return {"kinds": kinds_view(), "destinations": destinations}


def save(name, destination):
    """보낼 곳을 저장한다(null이면 지운다). 비밀 칸을 비우면 저장된 값을 그대로 둔다."""
    name = name.strip() if isinstance(name, str) else ""
    if destination is None:
        return settings.save_named(FILE, "보낼 곳", MAX_DESTINATION_SIZE, name, None, MAX_DESTINATIONS)
    if not isinstance(destination, dict) or destination.get("kind") not in KINDS:
        raise ValueError(f"보낼 곳의 종류는 {', '.join(KINDS)} 중 하나입니다.")
    kind, old = KINDS[destination["kind"]], settings.named(FILE).get(name, {})
    unknown = set(destination) - {"kind", "done", *(f["name"] for f in kind["fields"])}
    if unknown or not isinstance(destination.get("done", False), bool):
        raise ValueError(f"보낼 곳에 쓸 수 없는 값이 있습니다: {', '.join(sorted(unknown)) or 'done'}")
    item = {"kind": destination["kind"], "done": destination.get("done", False)}
    for f in kind["fields"]:
        value = destination.get(f["name"])
        if f["secret"] and not value and old.get("kind") == destination["kind"] and reveal(old.get(f["name"], "")) is not None:
            item[f["name"]] = old[f["name"]]  # 비밀 칸을 비우면 저장된 값을 그대로 둔다.
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"'{f['label']}' 칸을 입력하세요.")
        if "options" in f and value not in [v for v, _ in f["options"]]:
            raise ValueError(f"'{f['label']}'은 {', '.join(v for v, _ in f['options'])} 중 하나입니다.")
        if "check" in f and not f["check"](value):
            raise ValueError(f["error"])
        item[f["name"]] = protect(value) if f["secret"] else value
    return settings.save_named(FILE, "보낼 곳", MAX_DESTINATION_SIZE, name, item, MAX_DESTINATIONS)


def duration_text(seconds):
    seconds = round(seconds)
    return f"{seconds // 60}분 {seconds % 60}초" if seconds >= 60 else f"{seconds}초"


def table_text(table):
    """표 {columns, rows}를 머리글과 행을 ' | '로 구분한 여러 줄 글자로. 행은 MAX_TABLE_ROWS까지."""
    if not isinstance(table, dict) or not isinstance(table.get("columns"), list) or not isinstance(table.get("rows"), list):
        raise ValueError('표는 {"columns": [...], "rows": [...]} 모양이어야 합니다.')
    columns, rows = table["columns"], table["rows"]
    cell = lambda value: "" if value is None else str(value)
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


def send(name, body):
    saved = settings.named(FILE).get(name)
    if saved is None:
        raise ValueError(f"'{name}' 보낼 곳이 없습니다.")
    kind = KINDS[saved["kind"]]
    fields = {f["name"]: reveal(saved[f["name"]]) if f["secret"] else saved[f["name"]] for f in kind["fields"]}
    secrets = [fields[f["name"]] for f in kind["fields"] if f["secret"]]
    if None in secrets:
        raise ValueError(f"'{name}'의 비밀 값을 읽을 수 없습니다(다른 PC·계정에서 온 파일). 보낼 곳을 다시 저장하세요.")
    try:
        kind["send"](fields, message(body))
    except ValueError as e:
        raise ValueError(source.mask(str(e), secrets)) from None  # 오류 문구에서 비밀 값(토큰, 주소)을 가린다.


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
