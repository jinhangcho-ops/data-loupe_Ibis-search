"""화면 임시 저장(초안): 저장·불러오기·삭제 라우트, 허용 화면, 비밀번호·크기 상한, 깨진 파일, 같은 연결 판단, 디바운스 논리(node). 데이터는 테스트 안에서 지어낸다."""
import json
import shutil
import subprocess

import pytest

from core import draft_app
from conftest import ROOT

SECRET = "Draft-S3cret!"  # 지어낸 비밀번호


def connect(client, url="postgres://db.example.invalid/sales", password=SECRET):
    assert client.post("/api/connect", json={"kind": "url", "url": url, "user": "analyst", "password": password}).status_code == 200


def test_save_get_delete(client, folder):
    connect(client)
    state = {"body": {"base": "orders", "conditions": ["amt >= 10"]}}
    assert client.post("/api/draft?screen=search", json={"state": state}).get_json() == {"saved": True}
    assert (folder / "drafts" / "search.json").exists()
    got = client.get("/api/draft?screen=search").get_json()
    assert got["draft"]["state"] == state and got["same"] is True
    assert got["draft"]["connection"] == got["current"] and got["current"]["name"] == "db.example.invalid/sales"
    assert client.delete("/api/draft?screen=search").status_code == 200
    assert not (folder / "drafts" / "search.json").exists()
    assert client.get("/api/draft?screen=search").get_json()["draft"] is None
    assert client.delete("/api/draft?screen=search").status_code == 200  # 없어도 된다


def test_other_connection_is_not_same(client):
    connect(client)
    client.post("/api/draft?screen=prep", json={"state": {"base": "t"}})
    connect(client, "postgres://other.example.invalid/hr")
    got = client.get("/api/draft?screen=prep").get_json()
    assert got["draft"]["connection"]["name"] == "db.example.invalid/sales" and got["same"] is False
    client.post("/api/draft?screen=dashboard", json={"state": {"id": "a"}})
    assert client.get("/api/draft?screen=dashboard").get_json()["same"] is True  # 대시보드는 연결 없이 고친다


@pytest.mark.parametrize("screen", ["", "../settings", "x/y", "search.json", "Search", "other"])
def test_screen_allow_list(client, folder, screen):
    for call in (client.get, client.delete):
        assert call("/api/draft", query_string={"screen": screen}).status_code == 400
    assert client.post("/api/draft", query_string={"screen": screen}, json={"state": {"a": 1}}).status_code == 400
    assert client.get("/api/draft").status_code == 400
    assert not (folder / "drafts").exists()


def test_password_not_on_disk(client, folder):
    connect(client)
    client.post("/api/draft?screen=search", json={"state": {"body": {"conditions": [f"memo = '{SECRET}'", {"any": [f"x = {SECRET}"]}]}, "note": SECRET}})
    text = (folder / "drafts" / "search.json").read_text(encoding="utf-8")
    assert SECRET not in text and "***" in text
    for p in folder.rglob("*"):
        if p.is_file():
            assert SECRET.encode() not in p.read_bytes()


def test_size_limit(client, folder, monkeypatch):
    monkeypatch.setattr(draft_app, "MAX_BYTES", 2000)
    assert client.post("/api/draft?screen=search", json={"state": {"x": "a" * 100}}).get_json() == {"saved": True}
    assert client.post("/api/draft?screen=search", json={"state": {"x": "a" * 5000}}).get_json() == {"saved": False}
    assert "a" * 5000 not in (folder / "drafts" / "search.json").read_text(encoding="utf-8")  # 큰 것은 쓰지 않고, 앞의 초안은 그대로


def test_state_must_be_object(client):
    assert client.post("/api/draft?screen=search", json={"state": "text"}).status_code == 400
    assert client.post("/api/draft?screen=search", json={}).status_code == 400


@pytest.mark.parametrize("content", ["{깨진", "[]", '{"saved": "2026-01-01T00:00:00"}', '{"state": {}}', ""])
def test_broken_file_is_none(client, folder, content):
    (folder / "drafts").mkdir()
    (folder / "drafts" / "prep.json").write_text(content, encoding="utf-8")
    assert client.get("/api/draft?screen=prep").get_json()["draft"] is None


def test_no_connection_saves_without_it(client):
    client.post("/api/draft?screen=dashboard", json={"state": {"id": "a"}})
    got = client.get("/api/draft?screen=dashboard").get_json()
    assert got["draft"]["connection"] is None and got["same"] is True


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_debounce_logic(tmp_path):
    """draftTick: 바뀐 뒤 조용해야 보내고, 같은 것은 다시 보내지 않으며, 저장할 것이 없으면(null) 보내지 않는다."""
    script = tmp_path / "tick.js"
    script.write_text((ROOT / "static" / "core" / "draft.js").read_text(encoding="utf-8") + """
const m = { sent: "", seen: "", at: 0 }, out = [];
const t = (now, snap) => out.push(draftTick(m, now, snap, 2000));
t(0, '{"a":1}'); t(1000, '{"a":1}'); t(1999, '{"a":1}'); t(2000, '{"a":1}'); t(3000, '{"a":1}');   // false false false true false
t(3500, '{"a":2}'); t(5000, '{"a":2}'); t(5600, '{"a":3}'); t(7599, '{"a":3}'); t(7600, '{"a":3}');   // false false false false true
t(8000, null); t(20000, null);                                                                      // 저장할 것 없음: false false
t(21000, '{"a":3}'); t(24000, '{"a":3}');                                                           // 이미 보낸 것과 같다: false false
console.log(JSON.stringify(out));
""", encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == [False, False, False, True, False, False, False, False, False, True, False, False, False, False]
