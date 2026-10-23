"""수집용 창(collect_browser) 감사 재현. 실제 창·바깥 인터넷은 쓰지 않고 가짜 프로세스·연결로 확인한다."""
import json

import pytest

from collect import collect_browser as cb


class FakeProc:
    def __init__(self, running=True):
        self.running, self.killed = running, False

    def poll(self):
        return None if self.running else 0

    def kill(self):
        self.running, self.killed = False, True

    def wait(self, timeout=None):
        pass


class BrokenConn:
    closed = False

    def send_bytes(self, data):
        raise BrokenPipeError()

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    saved = dict(cb.child)
    yield
    cb.child.clear()
    cb.child.update(saved)


def test_start_child_kills_proc_when_first_send_fails(monkeypatch):
    procs = []

    class L:
        address = ("127.0.0.1", 1)

        def accept(self):
            return BrokenConn()

        def close(self):
            pass

    def popen(*a, **k):
        procs.append(FakeProc())
        return procs[-1]

    monkeypatch.setattr(cb, "Listener", lambda *a, **k: L())
    monkeypatch.setattr(cb.subprocess, "Popen", popen)
    with pytest.raises(ValueError):
        cb.launch("https://example.com/")
    assert len(procs) == 2 and all(p.killed for p in procs)


def test_state_caps_long_url(monkeypatch):
    cb.child.update(proc=FakeProc(), conn=object(), gone=False, picking="", pick=None, busy=False)
    monkeypatch.setattr(cb, "call", lambda *a, **k: {"url": "https://e.com/#" + "x" * 5_000_000, "title": "t", "picks": [], "on": False})
    assert len(cb.state()["url"]) <= cb.MAX_URL


def test_forget_refuses_while_disconnected_process_still_runs(folder, monkeypatch):
    cb.FOLDER.mkdir(parents=True, exist_ok=True)
    (cb.FOLDER / "Cookies").write_text("x")
    cb.child.update(proc=FakeProc(), conn=None, gone=True)
    with pytest.raises(ValueError):
        cb.forget()
    assert (cb.FOLDER / "Cookies").exists()


def test_encode_replaces_huge_reply_with_error(monkeypatch):
    monkeypatch.setattr(cb, "MAX_REPLY", 1000)
    data = json.loads(cb.encode({"id": 7, "value": "x" * 5000}))
    assert data["id"] == 7 and "error" in data and "value" not in data
    assert json.loads(cb.encode({"id": 8, "value": "ok"})) == {"id": 8, "value": "ok"}


def test_web_url_rejects_other_schemes():
    for bad in ("file:///c:/x", "javascript:alert(1)", "data:text/html,x", "http://", "", None, "ftp://h/x"):
        with pytest.raises(ValueError):
            cb.web_url(bad)
    assert cb.web_url("https://example.com/a") == "https://example.com/a"


def test_script_arguments_are_json_not_code():
    out = cb.script("read", 'a"); alert(1); ("', [{"selector": "\u2028</script>"}], 0, None)
    assert "\u2028" not in out and '\\"); alert(1)' in out and out.rstrip().endswith("null)")


def test_clean_pick_rejects_bad_shapes():
    good = {"target": "field", "items": "ul > li", "selector": "a", "text": "t" * 999, "count": 3, "attrs": {"href": "u", "onclick": "x", "data-a": 5}}
    got = cb.clean_pick(good)
    assert len(got["text"]) == cb.MAX_TEXT and got["attrs"] == {"href": "u"}
    assert cb.clean_pick({**good, "count": True}) is None and cb.clean_pick({**good, "target": "x"}) is None
    assert cb.clean_pick({**good, "selector": "a" * 5000}) is None


def test_call_times_out_and_cleans_up(monkeypatch):
    class Conn:
        def send_bytes(self, data):
            pass

    conn = Conn()
    cb.child.update(proc=FakeProc(), conn=conn, gone=False, waiting=set(), replies={}, n=0)
    with pytest.raises(TimeoutError):
        cb.call("js", 0.1, script="1")
    assert not cb.child["waiting"] and not cb.child["replies"]


def test_call_raises_gone_when_connection_drops():
    cb.child.update(proc=FakeProc(), conn=BrokenConn(), gone=False, waiting=set(), replies={}, n=0)
    with pytest.raises(cb.Gone):
        cb.call("js", script="1")
