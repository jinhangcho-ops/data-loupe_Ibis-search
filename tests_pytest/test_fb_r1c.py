"""F31: /api/pick — .env 경로를 파일 대화상자로 고른다(경로만 돌려주고 내용은 읽지 않는다)."""
from core import server


def test_pick_env_경로만_돌려준다(client, monkeypatch, tmp_path):
    env = tmp_path / "llm.env"
    env.write_text("LLM_API_KEY=비밀값", encoding="utf-8")
    monkeypatch.setattr(server, "ask_open", lambda: str(env))
    r = client.post("/api/pick", json={"kind": "env"})
    assert r.get_json() == {"path": str(env)} and "비밀값" not in r.get_data(as_text=True)


def test_pick_env_취소와_창_없음은_null(client, monkeypatch):
    monkeypatch.setattr(server, "ask_open", lambda: None)
    assert client.post("/api/pick", json={"kind": "env"}).get_json() == {"path": None}
    monkeypatch.setattr(server, "ask_open", None)
    assert client.post("/api/pick", json={"kind": "env"}).get_json() == {"path": None}


def test_pick_알_수_없는_종류는_400(client):
    assert client.post("/api/pick", json={"kind": "x"}).status_code == 400
    assert client.post("/api/pick", json={}).status_code == 400


def test_pick은_FREE(client):
    assert "/api/pick" in server.FREE
