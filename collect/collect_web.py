"""수집의 웹페이지 모드: 수집용 창(collect_browser)에서 쪽을 열고 항목·컬럼을 읽어 쪽마다 저장한다(쪽 넘기기 none·next·url·scroll).
robots.txt 판단과 미리보기(지금 페이지에서 읽기)도 여기에 있다. 저장·이어 받기·결과 파일 만들기는 API 모드와 같은 것(collect_store)을 쓴다. collect_job을 부르지 않는다(collect_job이 이 모듈을 부른다).
창은 collect_browser.session이 주는 Page(go, run)로만 다룬다(검사에서는 가짜 Page로 바꿔 끼운다)."""
import datetime as dt
import hashlib
import http.client
import json
import secrets
import time
from urllib.parse import quote, unquote, urlsplit

from collect import collect_browser
from collect import collect_config
from collect import collect_http
from collect import collect_request as req
from collect import collect_store
from collect.collect_http import Halt

STABLE = 0.5  # 항목 수가 이만큼(초) 그대로면 다 그려진 것으로 본다.
POLL = 0.1  # 기다리는 동안 페이지를 보는 간격(초)
SAMPLE_ROWS = 20
MAX_ROWS = 100_000  # 한 쪽에서 읽는 최대 항목 수
MAX_VALUE = 100_000  # 값 하나의 최대 글자 수
ROBOTS_TIMEOUT = 10
ROBOTS_MAX = 512 * 1024  # robots.txt를 이만큼(바이트)까지만 읽는다.
AGENT = "dataloupe"  # robots.txt에서 이 앱의 User-agent 이름(소문자). 이 이름의 묶음이 있으면 * 묶음 대신 따른다.
EMPTY = "항목을 찾지 못했습니다(0건). 페이지 구조가 바뀌었거나 로그인이 필요할 수 있습니다."
BLOCKED = "robots.txt가 이 주소를 막고 있습니다."
CLOSED = "수집용 창이 닫혀 멈췄습니다."


# ---------- 설정 ----------

def page_url(web, i, now=None):
    """i번째 쪽(0부터)의 주소: {page}는 start + i, {today}·{now}는 지금 시각."""
    return req.fill_url(web["url"], {"env": {}, "page": web["paging"]["start"] + i, "offset": None, "cursor": "", "now": now or dt.datetime.now()})


def ready(web):
    """시작 전 검사: {env:…}는 쓰지 않음, http·https 주소, 항목 선택자와 컬럼, 다음 버튼 선택자."""
    if req.env_names({"url": web["url"]}):
        raise ValueError("웹페이지 주소에는 {env:…}를 쓸 수 없습니다(로그인은 수집용 창에서 하세요).")
    collect_browser.web_url(page_url(web, 0))
    if not web["items"].strip():
        raise ValueError("항목 선택자를 정하세요.")
    if not web["fields"]:
        raise ValueError("컬럼을 하나 이상 정하세요.")
    if web["paging"]["kind"] == "next" and not web["paging"]["next"].strip():
        raise ValueError("다음 버튼 선택자를 정하세요.")


def fields_of(web):
    return [{"selector": f["selector"], "attr": f["attr"]} for f in web["fields"]]


def rows_of(found, web):
    """페이지가 준 읽기 결과를 검사해 (항목 수, 행 [{이름: 값}], 주소). 모양이 틀리면 Halt."""
    names = [f["name"] for f in web["fields"]]
    if isinstance(found, dict) and isinstance(found.get("error"), str):
        raise Halt(found["error"][:300])
    if (not isinstance(found, dict) or type(found.get("count")) is not int or not isinstance(found.get("rows"), list) or not isinstance(found.get("url"), str)
            or not all(isinstance(r, list) and len(r) == len(names) and all(v is None or isinstance(v, str) for v in r) for r in found["rows"])):
        raise Halt("페이지에서 읽은 값의 모양이 올바르지 않습니다.")
    rows = [{n: v if v is None else v[:MAX_VALUE] for n, v in zip(names, r)} for r in found["rows"][:MAX_ROWS]]
    return found["count"], rows, found["url"]


def sample(config):
    """수집용 창의 지금 페이지에서 읽기만 한다(이동하지 않음, 사용 한도에 세지 않음)."""
    web = collect_config.normalize(config)["web"]
    if not web["items"].strip() or not web["fields"]:
        raise ValueError("항목 선택자와 컬럼을 정하세요.")
    try:
        found = collect_browser.page().run("read", web["items"], fields_of(web), 0, SAMPLE_ROWS)
    except collect_browser.Gone:
        raise ValueError(collect_browser.CLOSED) from None
    except TimeoutError:
        raise ValueError("수집용 창이 답하지 않습니다. 페이지가 다 열린 뒤에 다시 하세요.") from None
    try:
        count, rows, _ = rows_of(found, web)
    except Halt as e:
        raise ValueError(str(e)) from None
    return {"count": count, "columns": [f["name"] for f in web["fields"]], "rows": rows}


# ---------- robots.txt ----------

def robots_path(text):
    """경로를 같은 퍼센트 인코딩으로 맞춘다(robots.txt의 /한글 과 주소의 /%ED%95%9C… 를 같게)."""
    return quote(unquote(text), safe="/?&=:;@!$'()*+,~")


def robots_parse(text):
    """robots.txt 글자 → {User-agent 이름(소문자): [(허용인지, 경로 패턴)]}. 이어진 User-agent 줄은 한 묶음, 같은 이름의 묶음은 합친다(RFC 9309)."""
    groups, agents, rules_seen = {}, [], True
    for line in text.splitlines():
        key, _, value = line.split("#", 1)[0].partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if rules_seen:
                agents, rules_seen = [], False
            agents.append(value.lower())
            groups.setdefault(value.lower(), [])
        elif key in ("allow", "disallow"):
            rules_seen = True
            if value:  # 빈 Disallow(·Allow)는 아무것도 정하지 않는다
                for agent in agents:
                    groups[agent].append((key == "allow", robots_path(value)))
    return groups


def robots_allows(groups, url):
    """이 앱(AGENT) 묶음, 없으면 * 묶음에서 경로+쿼리에 맞는 가장 긴 패턴을 따른다(같은 길이면 Allow). *는 아무 글자, 끝의 $는 끝 고정."""
    parts = urlsplit(url)
    path = robots_path((parts.path or "/") + (f"?{parts.query}" if parts.query else ""))
    best = (-1, True)
    for allow, pattern in groups.get(AGENT, groups.get("*", [])):
        if robots_match(pattern, path) and (len(pattern), allow) > best:
            best = (len(pattern), allow)
    return best[1]


def robots_match(pattern, path):
    """robots.txt 패턴이 path 앞부분에 맞는지(*는 아무 글자, 끝 $는 끝 고정). 정규식 대신 조각을 앞에서부터 찾아, *가 많은 패턴도 주소 길이에 비례한 시간만 든다."""
    end = pattern.endswith("$")
    pieces = (pattern[:-1] if end else pattern).split("*")
    if not path.startswith(pieces[0]):
        return False
    if len(pieces) == 1:
        return not end or path == pieces[0]
    at = len(pieces[0])
    for piece in pieces[1:-1]:  # 가운데 조각은 가장 앞에 맞는 곳(뒤 조각에 자리를 가장 많이 남긴다)
        found = path.find(piece, at)
        if found < 0:
            return False
        at = found + len(piece)
    last = pieces[-1]
    return path.endswith(last) and len(path) - len(last) >= at if end else path.find(last, at) >= 0


def robots_rules(base):
    """base(방식://호스트)의 robots.txt. 읽은 규칙(robots_parse) 또는 확인하지 못한 이유(글자).
    4xx는 모두 허용, 5xx·연결 실패는 확인 못 함. 사용 한도에는 세지 않는다."""
    try:
        reply, _ = collect_http.open_url("GET", base + "/robots.txt", [], None, ROBOTS_TIMEOUT)
        with reply:
            status, body = reply.status, reply.read(ROBOTS_MAX)
    except (OSError, ValueError, http.client.HTTPException) as e:
        return f"robots.txt를 받지 못했습니다({getattr(e, 'reason', e)})"
    if 400 <= status < 500:
        return {}
    if 200 <= status < 300:
        return robots_parse(body.decode("utf-8-sig", "replace"))  # 앞에 BOM이 붙은 파일도 첫 User-agent 줄을 읽는다
    return f"robots.txt 응답 상태가 {status}입니다"


def robots(url, cache):
    """url을 robots.txt(이 앱 또는 User-agent *)가 허용하는지: ("허용"|"금지"|"확인 못 함", 이유). 호스트마다 한 번만 받는다(cache)."""
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in cache:
        cache[base] = robots_rules(base)
    rules = cache[base]
    if isinstance(rules, str):
        return "확인 못 함", rules
    return ("허용", "") if robots_allows(rules, url) else ("금지", parts.path or "/")


def robots_text(web):
    """미리 보기(estimate)용 글자: "허용" | "금지: 경로" | "확인 못 함: 이유"."""
    try:
        url = collect_browser.web_url(page_url(web, 0))
    except ValueError as e:
        return f"확인 못 함: {e}"
    verdict, why = robots(url, {})
    return f"{verdict}: {why}" if why else verdict


# ---------- 작업 ----------

def signature(rows):
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()


class Walk:
    """한 작업의 쪽 넘기기. page는 수집용 창(Page), 받은 쪽은 collect_job.save_page로 저장한다."""

    def __init__(self, config, page, found, record, halt, balancer):
        self.web, self.page, self.found, self.record, self.halt, self.balancer = config["web"], page, found, record, halt, balancer
        self.timeout = config["balance"]["timeout"]
        self.done = set(record["done"])
        self.cache = {}
        self.host = None
        self.nav = "__ibis_nav" + secrets.token_hex(6)  # 이동한 뒤 새 문서인지 알아보는 표시의 이름

    def sleep(self, seconds):
        if self.halt.wait(seconds):
            raise Halt(collect_http.STOPPED, failed=False)

    def allowed(self, url):
        if self.web["robots"]:
            verdict, why = robots(url, self.cache)
            if verdict == "금지":
                raise Halt(BLOCKED)
            if verdict == "확인 못 함":
                raise Halt(f"robots.txt를 확인하지 못해 멈췄습니다: {why}")

    def turn(self):
        """쪽 하나를 열기 전에: 간격(balance.interval)을 지키고 사용 한도에 하나를 센다."""
        self.balancer.enter()
        try:
            self.balancer.take()
        finally:
            self.balancer.leave()

    def probe(self, nav=None):
        try:
            found = self.page.run("probe", self.web["items"], nav, timeout=5)
        except TimeoutError:  # 이동 중이거나 바쁜 페이지
            return None
        return found if isinstance(found, dict) and type(found.get("count")) is int else None

    def settle(self, more_than=0):
        """항목이 more_than개보다 많아지고 그 수가 STABLE초 동안 그대로일 때까지 기다린다(최대 web.wait초). 늘었는지."""
        end, last, since = time.monotonic() + self.web["wait"], None, 0.0
        while time.monotonic() < end:
            found = self.probe()
            count = found["count"] if found else None
            if count is not None and count > more_than:
                if count != last:
                    last, since = count, time.monotonic()
                elif time.monotonic() - since >= STABLE:
                    return True
            self.sleep(POLL)
        return last is not None

    def changed(self, before):
        """다음 버튼을 누른 뒤: 새 문서가 열리거나(표시가 사라짐) 같은 문서에서 항목이 바뀔 때까지 기다린다(최대 balance.timeout초)."""
        end = time.monotonic() + self.timeout
        while time.monotonic() < end:
            found = self.probe(self.nav)
            if found and ((not found["same"] and found["ready"] != "loading") or (found["same"] and found["count"] > 0 and found["sig"] != before.get("sig"))):
                return True
            self.sleep(POLL)
        return False

    def open(self, url):
        """url로 이동해 새 문서가 열리기를 기다린 뒤 항목을 기다린다. 방금 이 주소로 띄운 창이면 이동하지 않는다."""
        if self.page.landing == url:
            self.page.landing = None
        else:
            self.page.run("tag", self.nav)
            self.page.go(url)
            end = time.monotonic() + self.timeout
            while True:
                found = self.probe(self.nav)
                if found and not found["same"] and found["ready"] != "loading":
                    break
                if time.monotonic() > end:
                    raise Halt(f"{collect_http.wait_text(self.timeout)} 안에 페이지가 열리지 않았습니다: {url}")
                self.sleep(POLL)
        self.settle()

    def read(self, start=0):
        """지금 페이지의 행(start번째 항목부터). 처음 연 주소와 호스트가 달라졌으면 로그인이 풀린 것으로 보고 멈춘다.
        실제로 열린 주소도 robots.txt로 검사한다(href 없는 다음 버튼·넘겨주기로 간 쪽은 미리 검사할 수 없다)."""
        try:
            found = self.page.run("read", self.web["items"], fields_of(self.web), start, None, timeout=max(30, self.timeout))
        except TimeoutError:
            raise Halt("수집용 창이 답하지 않습니다.") from None
        _, rows, url = rows_of(found, self.web)
        host = urlsplit(url).hostname
        if self.host is None:
            self.host = host
        elif host != self.host:
            raise Halt(f"다시 로그인이 필요할 수 있습니다: 페이지가 다른 주소({host})로 넘어갔습니다. 수집용 창에서 로그인한 뒤 다시 시작하세요.")
        if urlsplit(url).scheme.lower() in ("http", "https"):
            self.allowed(url)
        return rows

    def keep(self, i, rows):
        """쪽 i를 저장한다. 이어 받기에서 이미 받은 쪽(다시 넘기며 지나는 쪽)은 저장하지 않는다."""
        if i not in self.done:
            with collect_store.guard:
                self.record["sigs"][str(i)] = signature(rows)  # 쪽마다 묶음 전체를 복사하지 않고 더한다
            collect_store.save_page(self.found, self.record, i, rows, len(json.dumps(rows, ensure_ascii=False).encode()))

    def first(self):
        url = page_url(self.web, 0, collect_store.began(self.record))
        self.allowed(url)
        self.turn()
        self.open(url)
        rows = self.read()
        if not rows:
            raise Halt(EMPTY)
        self.host = self.host or urlsplit(url).hostname
        return rows

    def run(self):
        """쪽을 넘기며 받는다. 자연스럽게 끝나면(더 없음) 진행 기록에 finished를 남긴다. 최대 쪽 수에서 멈춘 것은 남기지 않는다(늘리면 이어 받는다).
        최대 쪽 수에서 멈췄으면 True."""
        paging = self.web["paging"]
        kind, limit = paging["kind"], 1 if paging["kind"] == "none" else paging["max_pages"]
        ended = getattr(self, kind if kind in ("next", "scroll") else "by_url")(limit)
        if ended:
            with collect_store.guard:
                self.record["finished"] = True
                collect_store.write_record(self.found["record"], self.record)
        return not ended and kind != "none"

    def by_url(self, limit):
        """주소의 {page}로 넘긴다. 받은 쪽은 건너뛴다. 0건이거나 바로 전 쪽과 같으면 끝."""
        previous = None
        for i in range(limit):
            if i in self.done:
                previous = self.record["sigs"].get(str(i))
                continue
            if i == 0:
                rows = self.first()
            else:
                url = page_url(self.web, i, collect_store.began(self.record))
                self.allowed(url)
                self.turn()
                self.open(url)
                rows = self.read()
            if not rows or signature(rows) == previous:
                return True
            self.keep(i, rows)
            previous = signature(rows)
        return self.web["paging"]["kind"] == "none"  # 'none'은 한 쪽이 끝. 쪽 번호 방식은 최대 쪽 수에 닿은 것(1이어도)

    def next(self, limit):
        """다음 버튼을 눌러 넘긴다. 버튼이 없거나, 눌러도 바뀌지 않거나, 0건이거나, 바로 전 쪽과 같으면 끝."""
        selector = self.web["paging"]["next"]
        rows = self.first()
        self.keep(0, rows)
        previous = signature(rows)
        for i in range(1, limit):
            target = self.page.run("next", selector, False)
            if isinstance(target, dict) and isinstance(target.get("error"), str):
                raise Halt(target["error"][:300])
            if not isinstance(target, dict) or not target.get("found"):
                return True
            href = target.get("href")
            if isinstance(href, str) and urlsplit(href).scheme.lower() in ("http", "https"):
                self.allowed(href)
            before = self.probe() or {}
            self.turn()
            self.page.run("tag", self.nav)
            self.page.run("next", selector, True)
            if not self.changed(before):
                return True
            self.settle()
            rows = self.read()
            if not rows or signature(rows) == previous:
                return True
            self.keep(i, rows)
            previous = signature(rows)
        return False

    def scroll(self, limit):
        """끝까지 내려 더 불러온다. 새 항목이 web.wait초 안에 생기지 않으면 끝. 쪽 하나 = 한 번 내려 새로 생긴 항목들."""
        rows = self.first()
        self.keep(0, rows)
        count = len(rows)
        for i in range(1, limit):
            self.turn()
            self.page.run("scroll", self.web["items"])
            if not self.settle(count):
                return True
            rows = self.read(count)
            if not rows:
                return True
            self.keep(i, rows)
            count += len(rows)
        return False


def run_web(config, env, path, found, record, halt):
    """웹페이지 작업. 받은 쪽은 API 모드와 같은 모양으로 .part 폴더에 두고, 끝나면 같은 방법으로 결과 파일을 만든다(만들었으면 True)."""
    web = config["web"]
    found["part"].mkdir(exist_ok=True)
    for key, empty in (("columns", []), ("kinds", {}), ("rename", {}), ("sigs", {})):
        record.setdefault(key, empty)
    capped = False
    if not record.get("finished"):
        if record["done"] and web["paging"]["kind"] == "next":  # 스크롤은 이어 받지 않는다(collect_job.start)
            collect_store.update(message="이어 받습니다. 처음 쪽부터 다시 넘기며 받은 쪽은 저장하지 않습니다.")
        balancer = collect_store.balancer_for(config, halt, 1)
        done = set(record["done"])
        first = 0 if web["paging"]["kind"] in ("next", "scroll") else min(set(range(len(done) + 1)) - done)  # 처음 열 쪽(창을 띄울 주소)
        try:
            with collect_browser.session(web["show"], page_url(web, first, collect_store.began(record))) as page:
                capped = Walk(config, page, found, record, halt, balancer).run()
        except collect_browser.Gone:
            raise Halt(CLOSED) from None
    made = collect_store.finish_api(config, path, found, record)
    if made and capped:  # API 방식(run_api)처럼 알리고, 예약은 이 문구로 실패로 본다
        collect_store.update(message=collect_store.CAPPED.format(web["paging"]["max_pages"]))
    return made
