"""Notion으로 보내기 어댑터: 통합(integration) 토큰으로 페이지 끝에 블록을 덧붙인다(제목·내용·표). 연동 확인은 그 페이지를 읽어 본다.
Notion에서 통합을 만들고 보낼 페이지에 그 통합을 연결(공유)해 두어야 한다. 그림은 보내지 않는다(Notion API로 파일을 바로 올리지 않음)."""
import json
import re

from integrations.adapters import transport

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
TEXT_LIMIT = 2000  # rich_text 한 조각의 글자 수 상한
RICH_LIMIT = 100  # rich_text 배열 하나의 조각 수 상한(Notion API 문서 Request limits: arrays 100 elements)
TABLE_BYTES = 400_000  # 표 글의 UTF-8 바이트 상한(Notion 요청 전체 500KB 한도 안에 다른 블록·JSON 자리를 남긴다)
PAGE_ID = re.compile(r"[0-9a-fA-F]{32}$")  # 주소 끝(빼기를 지운 뒤)의 ID


def page_id(value):
    """페이지 주소나 ID에서 ID(32자리)를 꺼낸다: 주소의 마지막 부분에서 빼기를 지운 끝 32자리."""
    tail = value.strip().split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1].replace("-", "")
    found = PAGE_ID.search(tail)
    if not found:
        raise ValueError("Notion 페이지 주소나 ID(32자리)를 넣으세요.")
    return found[0].lower()


def call(token, method, path, body=None):
    """Notion API를 부르고 JSON 답을 돌려준다."""
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": VERSION, "Content-Type": "application/json"}
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    return json.loads(transport.request(method, f"{API}{path}", data, headers))


def rich(text):
    """긴 글은 TEXT_LIMIT자씩 나눈 rich_text 조각들. 조각은 RICH_LIMIT개까지(넘는 글은 잘라 '… 잘림')."""
    text = transport.cut(text, TEXT_LIMIT * RICH_LIMIT)
    return [{"type": "text", "text": {"content": text[i:i + TEXT_LIMIT]}} for i in range(0, len(text), TEXT_LIMIT)] or [{"type": "text", "text": {"content": ""}}]


def blocks(m):
    """메시지를 Notion 블록들로: 제목(heading_3), 내용(문단), 행 수·걸린 시간(문단), 표(코드 블록)."""
    out = [{"object": "block", "type": "heading_3", "heading_3": {"rich_text": rich(m["title"] or "Data Loupe")}}]
    if m["text"]:
        out.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich(m["text"])}})
    facts = " · ".join(x for x in (m["rows"] and f"행 수 {m['rows']}", m["duration"] and f"걸린 시간 {m['duration']}") if x)
    if facts:
        out.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich(facts)}})
    if m["table"]:
        table = m["table"]
        if len(table.encode()) > TABLE_BYTES:  # 한글은 한 글자 3바이트: 글자 수 한도만으로는 요청 크기를 넘는다
            table = transport.cut(table, len(table.encode()[:TABLE_BYTES - 100].decode(errors="ignore")))
        out.append({"object": "block", "type": "code", "code": {"language": "plain text", "rich_text": rich(table)}})
    return out


def send(fields, m):
    """페이지 끝에 블록을 덧붙인다(PATCH /blocks/{page}/children)."""
    call(fields["token"], "PATCH", f"/blocks/{page_id(fields['page'])}/children", {"children": blocks(m)})


def check(fields):
    """메시지를 보내지 않고 페이지를 읽어 본다(토큰이 맞고 페이지가 통합에 연결돼 있는지)."""
    page = call(fields["token"], "GET", f"/pages/{page_id(fields['page'])}")
    title = next(("".join(t.get("plain_text", "") for t in p.get("title", [])) for p in page.get("properties", {}).values() if p.get("type") == "title"), "")
    return f"페이지를 읽었어요: {title or page.get('id', '')}. 이 페이지 끝에 덧붙여요."


KIND = {"label": "Notion(페이지에 덧붙이기)", "image": False, "send": send, "check": check, "fields": [
    {"name": "token", "label": "통합 토큰(ntn_… 또는 secret_…)", "secret": True},
    {"name": "page", "label": "페이지 주소나 ID", "secret": False, "hint": "Notion에서 그 페이지의 [연결]에 이 통합을 추가해 두세요.",
     "check": lambda v: bool(PAGE_ID.search(v.strip().split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1].replace("-", ""))),
     "error": "Notion 페이지 주소나 ID(32자리)를 넣으세요."}]}
