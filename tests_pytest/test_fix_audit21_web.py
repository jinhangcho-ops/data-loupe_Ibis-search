"""21차 웹 수집: 다음 버튼으로 실제로 넘어간 쪽도 robots.txt로 검사(href 없는 JS 버튼), robots.txt의 *·$ 와일드카드와 가장 긴 규칙 우선.
robots.txt는 127.0.0.1의 가짜 서버, 수집용 창은 가짜(Site)로만 다룬다."""
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from collect import collect_browser, collect_web


@pytest.fixture
def robots_site():
    """robots.txt를 주는 127.0.0.1 서버. 돌려준 dict의 text를 바꾸면 그 내용을 준다."""
    state = {"text": "", "hits": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["hits"] += 1
            body = state["text"].encode()
            self.send_response(200 if self.path == "/robots.txt" else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["base"] = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()
    server.server_close()


def verdicts(site, text, paths):
    site["text"] = text
    cache = {}
    return [collect_web.robots(site["base"] + p, cache)[0] for p in paths]


def test_wildcards_and_longest_match(robots_site):
    text = "User-agent: *\nAllow: /\nDisallow: /private\nDisallow: /*?sort=\nDisallow: /*.pdf$\n"
    paths = ["/private/a", "/list?sort=1", "/a.pdf", "/a.pdf?x=1", "/list", "/"]
    assert verdicts(robots_site, text, paths) == ["금지", "금지", "금지", "허용", "허용", "허용"]
    assert robots_site["hits"] == 1  # 호스트마다 한 번만 받는다


def test_allow_wins_ties_and_longer_allow_wins(robots_site):
    text = "User-agent: *\nDisallow: /a\nAllow: /a\nDisallow: /b\nAllow: /b/open\nDisallow:\n"
    assert verdicts(robots_site, text, ["/a/x", "/b/x", "/b/open/1", "/c"]) == ["허용", "금지", "허용", "허용"]
    assert verdicts(robots_site, "User-agent: *\nDisallow:\n", ["/x"]) == ["허용"]  # 빈 Disallow는 무시


def test_own_agent_group_wins_over_star(robots_site):
    text = "User-agent: other\nUser-agent: DataLoupe\nDisallow: /mine\n\nUser-agent: *\nDisallow: /\n"
    assert verdicts(robots_site, text, ["/mine/1", "/x"]) == ["금지", "허용"]
    assert verdicts(robots_site, "User-agent: other\nDisallow: /\n", ["/x"]) == ["허용"]  # 맞는 묶음이 없으면 막지 않는다
    assert verdicts(robots_site, "User-agent: *\nDisallow: /%ED%95%9C\n", ["/한/1"]) == ["금지"]  # 퍼센트 인코딩을 맞춰 비교


class Site:
    """가짜 수집용 창: 다음 버튼은 href가 없는 JS 버튼이고, 누르면 2쪽(/private?p=2)으로 넘어간다."""
    def __init__(self, base):
        self.base, self.at, self.landing, self.flags = base, 1, None, set()

    def go(self, url):
        self.flags = set()

    def rows(self):
        return [f"{self.at}-{j}" for j in range(3)]

    def url(self):
        return self.base + ("/list" if self.at == 1 else "/private?p=2")

    def run(self, m, *a, timeout=None):
        if m == "tag":
            self.flags.add(a[0])
            return True
        if m == "probe":
            return {"count": 3, "sig": str(self.at), "url": self.url(), "ready": "complete", "same": bool(a[1]) and a[1] in self.flags}
        if m == "read":
            return {"count": 3, "url": self.url(), "rows": [[t] for t in self.rows()[a[2]:]]}
        if m == "next":
            if a[1]:
                self.at, self.flags = self.at + 1, set()
            return {"found": True, "href": None}


def test_next_button_checks_the_page_it_landed_on(client, monkeypatch, tmp_path, robots_site):
    robots_site["text"] = "User-agent: *\nDisallow: /private\n"
    site = Site(robots_site["base"])

    @contextmanager
    def session(show, url):
        site.landing = url
        yield site
    monkeypatch.setattr(collect_browser, "session", session)
    monkeypatch.setattr(collect_web, "STABLE", 0.05)
    monkeypatch.setattr(collect_web, "POLL", 0.01)
    cfg = {"mode": "web", "web": {"url": robots_site["base"] + "/list", "items": "li", "fields": [{"name": "t", "selector": "", "attr": "text"}],
           "paging": {"kind": "next", "next": "a.next", "start": 1, "max_pages": 5}, "wait": 1, "robots": True, "show": True},
           "balance": {"interval": 0, "timeout": 5}, "output": {"format": "parquet", "path": str(tmp_path / "n.parquet"), "dedupe": []}}
    client.post("/api/collect/start", json={"config": cfg})
    while client.get("/api/collect/status").get_json()["running"]:
        time.sleep(0.02)
    s = client.get("/api/collect/status").get_json()
    assert site.at == 2  # 버튼은 눌렀지만
    assert (s["phase"], s["error"], s["rows"]) == ("실패", collect_web.BLOCKED, 3)  # 막힌 2쪽은 저장하지 않고 멈춘다
    assert robots_site["hits"] == 1
