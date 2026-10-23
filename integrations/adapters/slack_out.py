"""Slack으로 보내기 어댑터: 봇 토큰으로 Web API(chat.postMessage, 파일 올리기)를 부른다. 연동 확인은 auth.test와 conversations.info."""
import json
import urllib.parse

from integrations.adapters import transport

SLACK_API = "https://slack.com/api"


def call(token, method, body, form=False):
    """토큰으로 Slack API 메서드를 JSON이나 폼으로 부르고, ok가 아니면 오류를 낸다."""
    data = urllib.parse.urlencode(body).encode() if form else json.dumps(body, ensure_ascii=False).encode()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/x-www-form-urlencoded" if form else "application/json; charset=utf-8"}
    reply = json.loads(transport.post(f"{SLACK_API}/{method}", data, headers))
    if not reply.get("ok"):
        raise ValueError(f"Slack 오류: {reply.get('error')}")
    return reply


def send(fields, m):
    """글은 chat.postMessage로. 그림이 있으면 파일 올리기(getUploadURLExternal → 받은 주소에 바이트 → completeUploadExternal)로 글과 함께."""
    token, channel = fields["token"], fields["channel"]
    text = f"*{m['title']}*\n{m['text']}" + (f"\n```\n{m['table']}\n```" if m["table"] else "")
    if not m["image"]:
        call(token, "chat.postMessage", {"channel": channel, "text": text})
        return
    upload = call(token, "files.getUploadURLExternal", {"filename": "image.png", "length": len(m["image"])}, form=True)
    transport.post(upload["upload_url"], m["image"], {"Content-Type": "application/octet-stream"})
    call(token, "files.completeUploadExternal", {"files": [{"id": upload["file_id"], "title": m["title"]}], "channel_id": channel, "initial_comment": text})


def check(fields):
    """메시지를 보내지 않고 확인한다: 토큰(auth.test)과, 권한이 있으면 채널(conversations.info)."""
    who = call(fields["token"], "auth.test", {}, form=True)
    text = f"토큰 확인: 워크스페이스 {who.get('team', '?')}, 봇 {who.get('user', '?')}."
    try:
        info = call(fields["token"], "conversations.info", {"channel": fields["channel"]}, form=True)["channel"]
        member = info.get("is_member")
        return text + f" 채널 #{info.get('name', fields['channel'])}" + ("" if member in (None, True) else " (봇이 아직 채널에 없어 보낼 수 없습니다. 채널에 봇을 초대하세요.)")
    except ValueError as e:
        if "missing_scope" in str(e):
            return text + " 채널은 권한(channels:read)이 없어 확인하지 못했습니다."
        raise ValueError(f"{text} 채널을 확인하지 못했습니다: {e}") from None


KIND = {"label": "Slack", "image": True, "send": send, "check": check, "fields": [
    {"name": "token", "label": "봇 토큰", "secret": True},
    {"name": "channel", "label": "채널 ID(예: C0123456789)", "secret": False}]}
