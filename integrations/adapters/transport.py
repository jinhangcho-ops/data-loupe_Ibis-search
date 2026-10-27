"""어댑터가 함께 쓰는 HTTP 보내기(표준 라이브러리). 테스트는 post·request를 바꿔 끼워 실제로 보내지 않는다.
넘겨주기(3xx)는 따라가지 않는다(인증 헤더·키가 다른 호스트나 http로 새지 않게). 보낸 요청은 설정 폴더의 egress_log.jsonl에
시각·종류(GET·POST)·호스트·경로만 남긴다(값·쿼리 문자열·헤더 없음, 낱말 모양이 아닌 경로 조각은 *로 가림)."""
import datetime as dt
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from core import settings

TIMEOUT = 15  # 요청 하나를 기다리는 시간(초)
LOG = "egress_log.jsonl"
MAX_LOG = 1_000_000  # 기록 파일의 크기 상한(바이트). 넘으면 오래된 앞쪽 절반을 지운다.
# 그대로 남길 경로 조각: 23자까지의 낱말 모양(chat.postMessage, completions)과 v1·v1beta. 그 밖(웹훅 주소의 비밀 조각·ID, Slack 웹훅 비밀 24자)은 *로.
# shortcut: 23자 이하이면서 낱말 모양(소문자 뒤 대문자 하나씩)인 글자 비밀은 남는다, 그런 웹훅이 생기면 경로를 빼고 호스트만 남긴다.
SAFE_PART = re.compile(r"(?=.{1,23}\Z)[a-z]+(?:[._-]?[A-Za-z][a-z]+)*|v\d+[a-z]{0,8}")


class Keep(urllib.request.HTTPRedirectHandler):
    """넘겨주기를 따라가지 않는다(3xx는 HTTPError가 된다). collect/collect_http.Keep과 같은 것(연동 화면이 수집 모듈을 올리지 않게 따로 둔다: test_lazy_loading_memory)."""

    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(Keep())
CUT = "\n… 잘림"


def cut(text, limit):
    """text가 limit자를 넘으면 limit자 안에 들어가게 자르고 '… 잘림'을 붙인다(서비스 한도에서 거절당하지 않게)."""
    return text if len(text) <= limit else text[:max(limit - len(CUT), 0)] + CUT


def log(method, url):
    """밖으로 나간 요청 한 줄을 남긴다. 기록이 안 돼도 보내기는 막지 않는다."""
    parts = urlsplit(url)
    path = "/".join(p if not p or SAFE_PART.fullmatch(p) else "*" for p in parts.path.split("/"))
    line = json.dumps({"at": dt.datetime.now().isoformat(timespec="seconds"), "kind": method, "host": parts.netloc.rpartition("@")[2], "path": path}, ensure_ascii=False)
    file = settings.PATH.parent / LOG
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        with settings.LOCK:
            if file.exists() and file.stat().st_size > MAX_LOG:
                lines = file.read_text(encoding="utf-8").splitlines()
                file.write_text("".join(x + "\n" for x in lines[len(lines) // 2:]), encoding="utf-8")
            with file.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError:
        pass


def request(method, url, data=None, headers=None, timeout=TIMEOUT):
    """요청을 보내고 응답 몸통(bytes)을 돌려준다. 실패하면 상태 코드와 응답 앞부분을 넣은 한국어 오류."""
    log(method, url)
    try:
        with OPENER.open(urllib.request.Request(url, data=data, headers=headers or {}, method=method), timeout=timeout) as reply:
            return reply.read()
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            raise ValueError(f"보내지 못했습니다(상태 {e.code}): 다른 주소로 넘겨주는 응답은 따라가지 않습니다(키가 다른 곳으로 가지 않게). 최종 주소를 직접 넣으세요.") from None
        raise ValueError(f"보내지 못했습니다(상태 {e.code}): {e.read().decode('utf-8', 'replace')[:200]}") from None
    except (urllib.error.URLError, OSError) as e:
        raise ValueError(f"보내지 못했습니다: {getattr(e, 'reason', e)}") from None


def post(url, data, headers, timeout=TIMEOUT):
    return request("POST", url, data, headers, timeout)
