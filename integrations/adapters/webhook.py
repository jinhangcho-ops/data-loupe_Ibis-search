"""웹훅(주소로 보내기) 어댑터: 본문 틀의 자리를 채워 그 주소로 POST한다(Teams·Discord·일반 JSON·글자)."""
import base64
import json
import re
import urllib.parse

from integrations.adapters import transport

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


def content_size(body):
    """Discord가 재는 값: JSON 본문의 content 글자 수(JSON이 아니면 0)."""
    try:
        value = json.loads(body)
    except ValueError:
        return 0
    return len(str(value.get("content") or "")) if isinstance(value, dict) else 0


# 주소 호스트(그 하위 호스트 포함) → (한도, 본문에서 재는 값). 넘으면 {표}를 잘라 '… 잘림'을 붙인다. 그 밖의 주소는 한도를 모른다.
LIMITS = [
    (("discord.com", "discordapp.com"), 2_000, content_size),  # Discord Execute Webhook 문서: content 2000자
    (("webhook.office.com", "logic.azure.com", "powerplatform.com"), 28_000, lambda body: len(body.encode("utf-8"))),  # Teams 웹훅·워크플로 문서: 메시지 28KB
]


def send(fields, m):
    """본문 틀의 {제목} {내용} {행수} {걸린시간} {표} {그림} 자리를 본문 종류에 맞게 이스케이프해 채워 보낸다. 모르는 {…}는 그대로 둔다.
    아는 서비스(LIMITS)는 한도를 넘으면 {표}를 넘친 만큼 줄여 다시 채운다."""
    secure(fields)
    content_type, escape = BODIES[fields["content"]]
    values = {"제목": m["title"], "내용": m["text"], "행수": m["rows"], "걸린시간": m["duration"], "표": m["table"], "그림": base64.b64encode(m["image"]).decode() if m["image"] else ""}
    fill = lambda: re.sub(r"\{(" + "|".join(SLOTS) + r")\}", lambda found: escape(values[found[1]]), fields["template"])
    body, host = fill(), urllib.parse.urlsplit(fields["url"]).hostname or ""
    for hosts, most, size in LIMITS:
        if any(host == h or host.endswith("." + h) for h in hosts):
            while values["표"] and (over := size(body) - most) > 0 and (shorter := transport.cut(values["표"], len(values["표"]) - over)) != values["표"]:
                values["표"] = shorter
                body = fill()
    if fields["content"] == "json":
        try:
            json.loads(body)
        except ValueError:
            raise ValueError("본문 틀이 JSON이 아니에요. 값을 채운 결과가 JSON으로 읽히지 않아 보내지 않았어요.") from None
    transport.post(fields["url"], body.encode("utf-8"), {"Content-Type": content_type})


def secure(fields):
    """환경변수로 둔 주소는 저장할 때 검사하지 않는다: 데이터 값을 평문으로 보내지 않게 보낼 때·확인할 때 다시 본다."""
    if not str(fields.get("url", "")).startswith("https://"):
        raise ValueError("주소는 https://로 시작해야 해요.")


def check(fields):
    """웹훅은 보내 보지 않고는 확인할 방법이 없다. 주소 모양과 본문 틀만 본다."""
    secure(fields)
    if fields["content"] == "json":
        values = {"제목": "", "내용": "", "행수": "", "걸린시간": "", "표": "", "그림": ""}
        try:
            json.loads(re.sub(r"\{(" + "|".join(SLOTS) + r")\}", lambda found: values[found[1]], fields["template"]))
        except ValueError:
            raise ValueError("본문 틀이 JSON이 아니에요.") from None
    return "주소와 본문 틀의 모양은 맞아요. 웹훅은 보내 봐야 받는지 알 수 있으니 [시험 보내기]를 쓰세요."


KIND = {"label": "웹훅(주소로 보내기)", "image": True, "send": send, "check": check, "presets": PRESETS, "fields": [
    {"name": "url", "label": "주소", "secret": True, "check": lambda v: v.startswith("https://"), "error": "주소는 https://로 시작해야 해요."},
    {"name": "content", "label": "본문 종류", "secret": False, "options": [["json", "JSON"], ["text", "글자"], ["form", "폼(URL 인코딩)"]], "default": "json"},
    {"name": "template", "label": "본문 틀", "secret": False, "multiline": True, "default": PRESETS[0]["template"],
     "hint": "쓸 수 있는 자리: {제목} {내용} {행수} {걸린시간} {표} {그림}"}]}
