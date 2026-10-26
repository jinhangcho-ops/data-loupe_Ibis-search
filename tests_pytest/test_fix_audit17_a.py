"""17차 A: 무한 스크롤은 이어 받지 않고 처음부터(M1), 웹 수집이 최대 쪽 수에서 멈추면 CAPPED로 알림(M2),
덜 넣은 적재로 시작이 막히면 [적재만 다시]가 보이게 상태를 남김(K4), 화면의 첫 쪽 번호 0·파일 이름(M8·M9).
수집 창은 가짜(FakeSite처럼), 적재는 시험 안에서 만든 DuckDB 파일에 한다."""
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import duckdb
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from collect import collect_browser, collect_config, collect_job, collect_web, load_job

ROOT = Path(__file__).resolve().parent.parent


class Site:
    """가짜 수집용 창. pages가 있으면 주소의 page=로 쪽을 고르고, 없으면 무한 스크롤(items 중 shown개가 보임)."""
    def __init__(self, items=(), shown=0, step=0, pages=None):
        self.items, self.shown, self.step, self.pages, self.at, self.landing, self.flags = list(items), shown, step, pages, 1, None, set()

    def go(self, url):
        self.flags = set()
        if self.pages is not None:
            self.at = int(parse_qs(urlsplit(url).query)["page"][0])

    def now(self):
        return self.pages.get(self.at, []) if self.pages is not None else self.items[:self.shown]

    def run(self, m, *a, timeout=None):
        found = self.now()
        if m == "tag":
            self.flags.add(a[0])
            return True
        if m == "probe":
            return {"count": len(found), "sig": "", "url": "http://h.example.com/l", "ready": "complete", "same": bool(a[1]) and a[1] in self.flags}
        if m == "read":
            return {"count": len(found), "url": "http://h.example.com/l", "rows": [[t] for t in found[a[2]:]]}
        if m == "scroll":
            self.shown = min(len(self.items), self.shown + self.step)
            return 1


def fake(monkeypatch, holder):
    @contextmanager
    def session(show, url):
        site = holder["site"]
        site.go(url)
        site.landing = url
        yield site
    monkeypatch.setattr(collect_browser, "session", session)
    monkeypatch.setattr(collect_web, "STABLE", 0.05)
    monkeypatch.setattr(collect_web, "POLL", 0.01)


def web_config(out, url, kind, max_pages, group=""):
    return {"mode": "web", "limit_group": group, "web": {"url": url, "items": "li", "fields": [{"name": "t", "selector": "", "attr": "text"}],
            "paging": {"kind": kind, "next": "", "start": 1, "max_pages": max_pages}, "wait": 1, "robots": False, "show": True},
            "balance": {"interval": 0, "timeout": 5}, "output": {"format": "parquet", "path": str(out), "dedupe": []}}


def run(client, cfg):
    reply = client.post("/api/collect/start", json={"config": cfg}).get_json()
    while client.get("/api/collect/status").get_json()["running"]:
        time.sleep(0.02)
    return reply, client.get("/api/collect/status").get_json()


# ---------- M1 무한 스크롤은 이어 받지 않는다 ----------

def test_scroll_restarts_instead_of_resuming(client, monkeypatch, tmp_path):
    holder = {}
    fake(monkeypatch, holder)
    items = [f"글{j}" for j in range(30)]
    out = tmp_path / "s.parquet"
    cfg = web_config(out, "http://h.example.com/l", "scroll", 50, group="웹")
    client.post("/api/limits", json={"name": "웹", "group": {"per_day": 2, "concurrency": 1}})
    holder["site"] = Site(items, 3, 3)
    _, s = run(client, cfg)  # 하루 한도로 두 쪽(6행)에서 멈춤
    assert (s["phase"], s["rows"]) == ("멈춤", 6)
    assert collect_job.resumable(collect_config.normalize(cfg)) is None  # 예상 확인 화면도 "이어 받기"라고 하지 않는다
    client.post("/api/limits", json={"name": "웹", "group": {"per_day": 100, "concurrency": 1}})
    holder["site"] = Site(items, 5, 3)  # 다시 열면 첫 화면 항목 수가 다르다
    reply, s = run(client, cfg)
    assert reply["resumed"] is False and s["message"] == "스크롤 방식은 이어 받지 않고 처음부터 받습니다."
    assert s["phase"] == "끝" and pl.read_parquet(out)["t"].to_list() == items


# ---------- M2 웹 수집도 최대 쪽 수에서 멈추면 알린다 ----------

def test_web_max_pages_is_reported(client, monkeypatch, tmp_path):
    holder = {}
    fake(monkeypatch, holder)
    pages = {k: [f"상품{k}-{j}" for j in range(3)] for k in (1, 2, 3)}
    url = "http://h.example.com/l?page={page}"
    holder["site"] = Site(pages=pages)
    _, s = run(client, web_config(tmp_path / "a.parquet", url, "url", 2))
    assert (s["phase"], s["rows"]) == ("끝", 6) and s["message"] == collect_job.CAPPED.format(2)
    holder["site"] = Site(pages=pages)
    _, s = run(client, web_config(tmp_path / "b.parquet", url, "url", 10))  # 빈 쪽으로 끝났다
    assert (s["phase"], s["rows"], s["message"]) == ("끝", 9, "")
    holder["site"] = Site([f"글{j}" for j in range(30)], 3, 3)
    _, s = run(client, web_config(tmp_path / "c.parquet", "http://h.example.com/l", "scroll", 2))
    assert s["rows"] == 6 and s["message"] == collect_job.CAPPED.format(2)
    holder["site"] = Site(pages=pages)
    _, s = run(client, web_config(tmp_path / "d.parquet", url, "none", 1))  # 넘기지 않는 방식은 한 쪽이 끝
    assert (s["rows"], s["message"]) == (3, "")
    holder["site"] = Site(pages=pages)
    _, s = run(client, web_config(tmp_path / "e.parquet", url, "url", 1))  # 쪽 번호 방식의 최대 쪽 수 1도 한도
    assert (s["rows"], s["message"]) == (3, collect_job.CAPPED.format(1))


# ---------- K4 덜 넣은 적재로 막히면 [적재만 다시]가 보일 상태를 남긴다 ----------

def test_blocked_start_leaves_load_failure_state(client, tmp_path):
    path, db = tmp_path / "out.parquet", tmp_path / "t.duckdb"
    con = duckdb.connect(str(db))
    con.execute("create table t (id BIGINT, price BIGINT)")
    con.close()
    pq.write_table(pa.table({"id": [1], "price": ["비쌈"]}), path)  # 타입이 달라 덧붙일 수 없다
    cfg = collect_config.normalize({"mode": "api", "request": {"url": "https://x.example/i?page={page}"}, "response": {"records": "items"},
                                    "paging": {"kind": "page", "start": 1, "max_pages": 5}, "output": {"format": "parquet", "path": str(path)},
                                    "load": {"target": "table", "table": {"connection": {"kind": "folder", "file": str(db)}, "name": "t", "how": "append"}, "retries": 0}})
    try:  # 예약 프로세스(다른 프로세스)가 적재에 실패한 것: 이 앱의 상태에는 남지 않는다
        load_job.run(cfg, path, "", threading.Event(), lambda **k: None)
    except ValueError:
        pass
    assert load_job.read_record(path) is not None
    reply = client.post("/api/collect/start", json={"config": cfg})
    assert reply.status_code >= 400
    s = client.get("/api/collect/status").get_json()
    assert s["phase"] == "실패" and not s["running"] and s["load"] and "지난 적재가 끝나지 않았습니다" in s["error"]  # 화면의 표시 조건(s.load && 실패)
    assert {"where", "rows", "batches", "skipped", "failures"} <= set(s["load"])


def test_start_while_other_process_loads_says_busy(client, tmp_path):
    """다른 프로세스(예약)가 이 결과 파일을 적재하는 중이면 '덜 넣은 적재·기록 지우기'가 아니라 바쁘다고 알린다."""
    from core import filelock
    path = tmp_path / "out.parquet"
    pq.write_table(pa.table({"id": [1]}), path)
    cfg = collect_config.normalize({"mode": "api", "request": {"url": "https://x.example/i?page={page}"}, "response": {"records": "items"},
                                    "paging": {"kind": "page", "start": 1, "max_pages": 5}, "output": {"format": "parquet", "path": str(path)},
                                    "load": {"target": "table", "table": {"connection": {"kind": "folder", "file": str(tmp_path / "t.duckdb")}, "name": "t", "how": "append"}, "retries": 0}})
    load_job.write_record(path, {"sign": load_job.signature(cfg), "file": load_job.file_stamp(path), "rows": 0, "batches": 0, "inserted": 0, "skipped": 0, "failures": 0})  # 적재가 시작할 때 쓰는 기록
    lock = collect_job.lock_of(cfg)
    assert filelock.hold(lock)
    try:
        before = client.get("/api/collect/status").get_json()["phase"]
        reply = client.post("/api/collect/start", json={"config": cfg}).get_json()
        assert "지난 적재" not in str(reply) and client.get("/api/collect/status").get_json()["phase"] == before
    finally:
        filelock.let_go(lock)


# ---------- M8·M9·K4 화면 ----------

def test_collect_screen_bits():
    html = (ROOT / "static/collect/collect.html").read_text(encoding="utf-8")
    assert 'Number(val("w-start")) || 1' not in html and 'val("w-start") === "" ? 1 : Number(val("w-start"))' in html  # 첫 쪽 0이 1이 되지 않게
    summaries = html[html.index("function showCollectSummaries"):]
    assert "const file = " not in summaries[:summaries.index("\n}")]  # fileName을 쓴다
    start = html[html.index('$("start").onclick'):]
    assert "poll()" in start[:start.index("\n});")]  # 막혀도 상태를 다시 읽어 [적재만 다시]를 보인다
