"""(B7) [더 받기]: 사용자가 단추를 누를 때만 GitHub 저장소의 templates_more 폴더 목록과 고른 파일을 받는다(GitHub contents API, 주소 요청만 보낸다).
받은 것은 JSON으로만 읽고(실행하지 않음) dash_store.read_template의 같은 검사를 거친다. 시험은 fetch_json을 바꿔 끼운다. 명세: plans/dash-blocks.md "B7 세부 명세"."""
import base64
import json
import re
import urllib.request

from core import dash_store

REMOTE_LIST = "https://api.github.com/repos/jinhangcho-ops/data-loupe_Ibis-search/contents/templates_more"  # 저장소 templates_more 폴더(소유자·저장소 이름은 core/features.RELEASES와 같다)
HTTPS = "https://"  # 받을 수 있는 주소의 머리
FETCH_WAIT = 15  # 서버가 답하기를 기다리는 시간(초)
MAX_REPLY_BYTES = 1024 * 1024  # 답 하나의 크기 상한(바이트, base64로 늘어난 템플릿 파일 포함)
MAX_REMOTE_FILES = 100  # 목록에 받는 파일 수 상한
REMOTE_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\.json")  # 받을 수 있는 파일 이름
NOT_HTTPS = "https 주소만 받을 수 있어요."
NO_NET = "템플릿 저장소에 닿지 못했어요. 인터넷(프록시)을 확인한 뒤 다시 눌러 주세요."
BAD_REPLY = "템플릿 저장소의 답을 읽을 수 없어요."
BIG_REPLY = f"템플릿 저장소의 답이 {MAX_REPLY_BYTES // 1024}KB를 넘어 멈췄어요."
BAD_NAME = "받을 템플릿 파일 이름이 맞지 않아요."


def fetch_json(url: str) -> object:
    """[SPECIFICATION] plans/dash-blocks.md#fetch_json
    https 주소 하나를 시간 제한·크기 상한 안에서 받아 JSON으로만 해석해 돌려준다(시험이 바꿔 끼우는 요청 함수)."""
    if not url.startswith(HTTPS):
        raise ValueError(NOT_HTTPS)
    try:
        with urllib.request.urlopen(url, timeout=FETCH_WAIT) as res:
            if not res.geturl().startswith(HTTPS):
                raise ValueError(NOT_HTTPS)
            body = res.read(MAX_REPLY_BYTES + 1)
    except OSError as e:
        raise ValueError(NO_NET) from e
    if len(body) > MAX_REPLY_BYTES:
        raise ValueError(BIG_REPLY)
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError as e:
        raise ValueError(BAD_REPLY) from e


def remote_list() -> list[dict[str, object]]:
    """[SPECIFICATION] plans/dash-blocks.md#remote_list
    저장소 templates_more 폴더의 JSON 파일 목록 [{file, size}](이름 차례)을 돌려준다."""
    got = fetch_json(REMOTE_LIST)
    if not isinstance(got, list) or not all(isinstance(item, dict) for item in got):
        raise ValueError(BAD_REPLY)
    files: list[dict[str, object]] = []
    for item in got:
        name = item.get("name")
        if item.get("type") != "file" or not isinstance(name, str) or not REMOTE_NAME.fullmatch(name):
            continue
        size = item.get("size")
        if type(size) is not int:
            raise ValueError(BAD_REPLY)
        files.append({"file": name, "size": size})
    if len(files) > MAX_REMOTE_FILES:
        raise ValueError(f"템플릿 파일이 {MAX_REMOTE_FILES}개를 넘어요.")
    return sorted(files, key=lambda f: str(f["file"]))


def remote_get(file: str) -> dict:
    """[SPECIFICATION] plans/dash-blocks.md#remote_get
    저장소 templates_more 폴더의 파일 하나를 받아 base64를 풀고 dash_store.read_template로 검사한 템플릿을 돌려준다."""
    if not REMOTE_NAME.fullmatch(file):
        raise ValueError(BAD_NAME)
    got = fetch_json(f"{REMOTE_LIST}/{file}")
    if not isinstance(got, dict) or got.get("type") != "file" or got.get("encoding") != "base64" or not isinstance(got.get("content"), str):
        raise ValueError(BAD_REPLY)
    try:
        raw = base64.b64decode("".join(got["content"].split()), validate=True)
    except ValueError as e:
        raise ValueError(BAD_REPLY) from e
    return dash_store.read_template(raw, None)
