"""어댑터가 함께 쓰는 HTTP 보내기(표준 라이브러리). 테스트는 post·request를 바꿔 끼워 실제로 보내지 않는다."""
import urllib.error
import urllib.request

TIMEOUT = 15  # 요청 하나를 기다리는 시간(초)


def request(method, url, data=None, headers=None, timeout=TIMEOUT):
    """요청을 보내고 응답 몸통(bytes)을 돌려준다. 실패하면 상태 코드와 응답 앞부분을 넣은 한국어 오류."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers or {}, method=method), timeout=timeout) as reply:
            return reply.read()
    except urllib.error.HTTPError as e:
        raise ValueError(f"보내지 못했습니다(상태 {e.code}): {e.read().decode('utf-8', 'replace')[:200]}") from None
    except (urllib.error.URLError, OSError) as e:
        raise ValueError(f"보내지 못했습니다: {getattr(e, 'reason', e)}") from None


def post(url, data, headers, timeout=TIMEOUT):
    return request("POST", url, data, headers, timeout)
