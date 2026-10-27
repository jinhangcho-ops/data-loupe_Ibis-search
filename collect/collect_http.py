"""수집 요청 보내기: 넘겨주기(redirect)를 직접 따라가는 요청, 응답 크기 제한, 받는 쪽을 배려하는 동시 수·간격·다시 시도(Balancer)."""
import email.utils
import http.client
import random
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

from core import source

MAX_BYTES = 200 * 1024 * 1024  # 응답 하나의 최대 크기(파일 모드는 제외)
MAX_REDIRECTS = 5
MAX_WAIT = 15 * 60  # Retry-After·한도 초기화를 기다리는 최대 시간(초). 넘으면 멈춘다.
MAX_FAILURES = 20  # 작업 전체에서 다시 시도까지 끝내 실패한 요청 수가 이것을 넘으면 멈춘다(429·일시 5xx 뒤에 받은 것은 세지 않는다).
SLOW_MAX = 8  # 429로 늘린 간격의 상한: 처음 간격(0이면 0.05초)의 이 배수
STEADY = 20  # 이만큼 잇따라 받으면 줄였던 동시 수·간격을 처음대로 돌린다
BACKOFF = 1.0  # 다시 시도의 기다림 기준(초). 시도마다 두 배, 60초까지, 무작위로 0.5~1.5배.
REDIRECTS = (301, 302, 303, 307, 308)
SECRET_HEADERS = {"authorization", "cookie", "proxy-authorization"}  # 이름과 상관없이 비밀인 헤더. 다른 호스트로 넘어가면 빼고, 구성에 값을 그대로 두지 못한다(collect_request).
HIDE = "●●●"  # 오류·상태·표시에서 env 값을 바꿔 넣는 글자
STOPPED = "멈췄습니다. 같은 설정으로 다시 시작하면 이어 받습니다."


class Halt(Exception):
    """수집을 멈추게 하는 일. failed가 False면 사용자의 멈춤이나 한도처럼 실패가 아닌 멈춤이다."""

    def __init__(self, message, failed=True, status=None):
        super().__init__(message)
        self.failed, self.status = failed, status  # status: 거부한 응답의 상태(4xx로 멈췄을 때)


class Keep(urllib.request.HTTPRedirectHandler):
    """넘겨주기를 urllib이 따라가지 않게 한다(open이 직접 따진다)."""

    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(Keep())


def host_of(url):
    parts = urlsplit(url)
    return (parts.hostname or "", parts.port or {"http": 80, "https": 443}.get(parts.scheme.lower()))


def headers_for(target, url, headers, secrets=()):
    """url에 보내려던 headers를 target으로 보낼 때: 다른 호스트면 인증 헤더와 비밀 값이 든 헤더를 뺀다."""
    if host_of(target) == host_of(url):
        return headers
    return [[k, v] for k, v in headers if k.lower() not in SECRET_HEADERS and not any(s and s in v for s in secrets)]


def open_url(method, url, headers, body, timeout, secrets=()):
    """요청을 보내 (응답, 마지막 주소)를 돌려준다. 응답은 상태와 상관없이 돌려준다(오류 상태도 응답).
    넘겨주기는 MAX_REDIRECTS번까지 직접 따라간다. 다른 호스트로 가면 인증 헤더와 비밀 값이 든 헤더를 빼고, https에서 http로는 가지 않는다."""
    headers = [[k, v] for k, v in headers if k.lower() != "accept-encoding"] + [["Accept-Encoding", "identity"]]  # 압축하지 않은 응답만 받는다.
    for _ in range(MAX_REDIRECTS + 1):
        if urlsplit(url).scheme.lower() not in ("http", "https"):
            raise ValueError("http·https 주소만 받을 수 있습니다.")
        request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        try:
            reply = OPENER.open(request, timeout=timeout)
        except urllib.error.HTTPError as e:
            reply = e
        location = reply.headers.get("Location")
        if reply.status not in REDIRECTS or not location:
            return reply, url
        reply.close()
        target = urljoin(url, location)
        if urlsplit(url).scheme.lower() == "https" and urlsplit(target).scheme.lower() == "http":
            raise ValueError("https 주소에서 http 주소로 넘겨주는 것은 따라가지 않습니다.")
        headers = headers_for(target, url, headers, secrets)
        if reply.status == 303 or (reply.status in (301, 302) and method not in ("GET", "HEAD")):
            method, body = "GET", None
            headers = [[k, v] for k, v in headers if k.lower() != "content-type"]
        url = target
    raise ValueError(f"넘겨주기(redirect)가 {MAX_REDIRECTS}번을 넘었습니다.")


class Reply:
    """다 받은 응답: 상태, 헤더 [[이름, 값]], 몸통(bytes), 마지막 주소."""

    def __init__(self, status, headers, body, url):
        self.status, self.headers, self.body, self.url = status, headers, body, url

    def header(self, name):
        return next((v for k, v in self.headers if k.lower() == name.lower()), None)

    def text(self):
        """charset을 따르고 없으면 UTF-8로 읽은 글자."""
        charset = "utf-8"
        for part in (self.header("Content-Type") or "").split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.lower() == "charset" and value:
                charset = value.strip('"\'')
        try:
            return self.body.decode(charset, "replace")
        except LookupError:
            return self.body.decode("utf-8", "replace")


def fetch(sent, timeout, secrets=()):
    """요청(collect_request.build의 모양)을 보내 다 받은 Reply. MAX_BYTES를 넘는 응답은 받지 않는다."""
    reply, url = open_url(sent["method"], sent["url"], sent["headers"], sent["body"], timeout, secrets)
    with reply:
        size = reply.headers.get("Content-Length")
        too_big = ValueError(f"응답이 {MAX_BYTES // 1024 // 1024}MB를 넘습니다. 큰 파일은 파일 주소로 받으세요(파일 모드).")
        if size and size.isdigit() and int(size) > MAX_BYTES:
            raise too_big
        parts, total = [], 0
        while chunk := reply.read(1 << 20):
            total += len(chunk)
            if total > MAX_BYTES:
                raise too_big
            parts.append(chunk)
        return Reply(reply.status, list(reply.headers.items()), b"".join(parts), url)


def retry_after(value):
    """Retry-After(초 또는 HTTP 날짜)를 지금부터 기다릴 초로. 없거나 읽을 수 없으면 None."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        return max(0.0, email.utils.parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def wait_text(seconds):
    return f"{seconds:.1f}초" if seconds < 10 else f"{round(seconds)}초" if seconds < 120 else f"{round(seconds / 60)}분"


class Balancer:
    """받는 쪽을 배려해 보낸다: 동시 요청 수, 요청 시작 사이의 최소 간격, 모두 쉬기(pause), 다시 시도.
    note(글자)는 상태의 wait 칸에 쉬는 이유를 알린다. take()는 보내기 전에 부르는 사용 한도 확인(collect_limits).
    heard(응답)은 응답마다 불러 서버가 알려 준 한도를 읽고, 쉬어야 할 초를 돌려주면 모두 그만큼 쉰다."""

    def __init__(self, concurrency, interval, retries, stop, note=lambda text: None, take=lambda: None, heard=lambda reply: None, secrets=()):
        self.limit, self.interval, self.retries, self.stop = concurrency, interval, retries, stop
        self.note, self.take, self.heard, self.secrets = note, take, heard, secrets
        self.busy, self.last, self.until, self.failures, self.sent = 0, 0.0, 0.0, 0, 0
        self.first, self.streak, self.lost = (concurrency, interval), 0, 0  # 처음 동시 수·간격, 잇따라 받은 수, 끝내 실패한 요청 수
        self.slow_on = (429,)  # 동시 수를 줄이는 응답 상태(파일 모드는 503도)
        self.quit = threading.Event()  # 같은 작업의 다른 줄기가 실패했다: 남은 차례는 자리를 잡거나 한도에 세기 전에 끝낸다(사용자 멈춤 stop과 따로)
        self.cond = threading.Condition()

    def sleep(self, seconds):
        """seconds초 쉰다. 멈춤 요청이 오면 바로 멈춘다."""
        if self.stop.wait(max(0.0, seconds)):
            raise Halt(STOPPED, failed=False)

    def enter(self):
        with self.cond:
            while True:
                if self.stop.is_set() or self.quit.is_set():
                    raise Halt(STOPPED, failed=False)
                now = time.monotonic()
                wait = max(self.until - now, self.last + self.interval - now)
                if self.busy < self.limit and wait <= 0:
                    self.busy, self.last, self.sent = self.busy + 1, now, self.sent + 1
                    self.note("")
                    return
                self.cond.wait(min(max(wait, 0.01), 0.2) if wait > 0 else 0.2)

    def leave(self):
        with self.cond:
            self.busy -= 1
            self.cond.notify_all()

    def slow(self):
        """동시 수를 반으로, 간격을 두 배로(처음 간격의 SLOW_MAX배까지)."""
        with self.cond:
            self.limit, self.streak = max(1, self.limit // 2), 0
            self.interval = min(max(self.interval * 2, 0.05), max(self.first[1], 0.05) * SLOW_MAX)

    def fine(self):
        """잘 받았다: STEADY번 잇따르면 동시 수·간격을 처음대로."""
        with self.cond:
            self.streak += 1
            if self.streak >= STEADY:
                (self.limit, self.interval), self.streak = self.first, 0

    def pause(self, seconds, why):
        """모든 요청을 seconds초 쉬게 한다. MAX_WAIT를 넘으면 멈춘다."""
        if seconds > MAX_WAIT:
            raise Halt(f"{why}: 서버가 {wait_text(seconds)} 뒤에 다시 하라고 합니다({MAX_WAIT // 60}분 넘게 기다리지 않습니다). 나중에 다시 시작하면 이어 받습니다.", failed=False)
        with self.cond:
            self.until = max(self.until, time.monotonic() + seconds)
        self.note(f"{why}: {wait_text(seconds)} 쉼")

    def backoff(self, attempt):
        return min(BACKOFF * 2 ** attempt, 60) * random.uniform(0.5, 1.5)

    def failed(self, what, lost=False):
        """실패를 센다(상태에 보임). lost(다시 시도까지 끝내 실패)만 MAX_FAILURES로 견준다."""
        with self.cond:
            self.failures, self.lost, self.streak = self.failures + 1, self.lost + lost, 0
        if self.lost > MAX_FAILURES:
            raise Halt(f"실패가 {MAX_FAILURES}번을 넘어 멈췄습니다. 마지막: {what}")

    def call(self, attempt):
        """attempt()(한 번 보내고 Reply 같은 것을 돌려줌)를 차례·간격·한도를 지켜 부르고, 실패하면 정해진 것만 다시 시도한다.
        다시 시도: 429·5xx·시간 초과·연결 오류. 401·403은 바로 멈춤, 다른 4xx는 다시 시도 없이 멈춤."""
        last = ""
        for n in range(self.retries + 1):
            self.enter()
            try:
                self.take()  # 자리를 잡은 뒤에 세어, 센 시각이 실제로 보내는 시각과 가깝게 한다.
                reply = attempt()
            except (OSError, http.client.HTTPException) as e:  # 시간 초과, 연결 오류(URLError도 OSError)
                reply, last = None, source.mask(f"연결 오류: {getattr(e, 'reason', e)}", self.secrets, HIDE)
            finally:
                self.leave()
            rest = self.heard(reply) if reply is not None else None
            if rest:
                self.pause(rest, "서버가 알려 준 남은 횟수가 0")
            if reply is not None and 200 <= reply.status < 300:
                self.fine()
                return reply
            if reply is not None:
                last = source.mask(f"상태 {reply.status}", self.secrets, HIDE)
                if reply.status in (401, 403):
                    raise Halt(f"인증을 확인하세요(상태 {reply.status}). env 파일의 키와 헤더를 확인하세요.")
                if reply.status != 429 and reply.status < 500:
                    raise Halt(source.mask(f"요청이 거부됐습니다(상태 {reply.status}): {reply.text()[:200]}", self.secrets, HIDE), status=reply.status)
            self.failed(last, lost=n == self.retries)
            if n == self.retries:
                break
            after = retry_after(reply.header("Retry-After")) if reply is not None else None
            if reply is not None and reply.status in self.slow_on:
                self.slow()
                self.pause(after if after is not None else self.backoff(n), f"{reply.status} 응답")
            elif after is not None:
                self.pause(after, f"{reply.status} 응답")
            else:
                wait = self.backoff(n)
                self.note(f"{last}: {wait_text(wait)} 뒤 다시 시도")
                self.sleep(wait)
        raise Halt(f"{self.retries}번 다시 시도해도 실패했습니다: {last}")
