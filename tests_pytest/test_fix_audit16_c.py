"""감사 16C(G2): next_url 둘째 쪽부터도 설정 주소의 user:pass@ Basic 인증이 붙고, 다른 호스트로는 보내지 않는다. 응답은 가짜, 값은 여기서 지어낸다."""
import base64
import json
import threading

import pytest

from collect import collect_config, collect_http, collect_job
from collect import collect_request as req
from collect.collect_http import Halt, Reply


def run(tmp_path, monkeypatch, url, env, pages):
    config = collect_config.normalize({
        "mode": "api", "request": {"url": url}, "response": {"records": "items"},
        "paging": {"kind": "next_url", "next_path": "next", "max_pages": 5},
        "balance": {"interval": 0, "retries": 0}, "output": {"format": "parquet", "path": str(tmp_path / "out.parquet")}})
    path = collect_job.output_path(config)
    record = {"hash": collect_job.digest(config), "mode": "api", "done": [], "rows": 0, "bytes": 0}
    sent = []

    def fetch(request, timeout, secrets=()):
        sent.append(request)
        return Reply(200, [["Content-Type", "application/json"]], json.dumps(pages[request["url"]]).encode(), request["url"])
    monkeypatch.setattr(collect_http, "fetch", fetch)
    return sent, lambda: collect_job.run_api(config, env, path, collect_job.parts(path), record, threading.Event())


def basic(user, password):
    return [["Authorization", "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()]]


def test_next_url_second_page_keeps_basic_auth(tmp_path, monkeypatch, folder):
    sent, go = run(tmp_path, monkeypatch, "https://bob:{env:PW}@api.example.com/items", {"PW": "s3cr:t"},
                   {"https://api.example.com/items": {"items": [{"a": 1}], "next": "/items?p=2"},
                    "https://api.example.com/items?p=2": {"items": [{"a": 2}], "next": None}})
    assert go() is True
    assert [s["url"] for s in sent] == ["https://api.example.com/items", "https://api.example.com/items?p=2"]
    assert [s["headers"] for s in sent] == [basic("bob", "s3cr:t")] * 2


def test_next_url_other_host_gets_no_auth(tmp_path, monkeypatch, folder):
    sent, go = run(tmp_path, monkeypatch, "https://bob:{env:PW}@api.example.com/items", {"PW": "pw1"},
                   {"https://api.example.com/items": {"items": [{"a": 1}], "next": "https://bob@evil.example.net/x"}})
    with pytest.raises(Halt) as error:
        go()
    assert len(sent) == 1 and "pw1" not in str(error.value)


def test_server_userinfo_in_next_url_is_dropped():
    # 서버가 준 주소의 user:pass@는 쓰지 않는다(설정 주소의 인증만), 주소에도 남기지 않는다.
    sent = req.build({"url": "https://api.example.com/items"}, {"env": {}}, "https://eve:x@api.example.com/p2")
    assert sent["url"] == "https://api.example.com/p2" and sent["headers"] == []
