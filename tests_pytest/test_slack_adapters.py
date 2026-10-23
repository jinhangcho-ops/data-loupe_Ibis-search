"""명세 4번: 어댑터 분리와 자격 증명 공급자, Slack 봇(받기).
Slack에는 실제로 보내지 않는다(transport.post와 slack_sdk 클라이언트를 가짜로 바꿔 끼운다). 데이터·토큰은 테스트 안에서 지어낸다."""
import json
import types

import duckdb
import polars as pl
import pytest
from lazy import ibis

from integrations import commands
from core import credentials
from integrations.adapters import slack_bot, transport

TOKEN, APP = "xoxb-made-up-token-1", "xapp-made-up-token-2"


@pytest.fixture
def sent(monkeypatch):
    """transport.post로 나간 요청들. Slack은 늘 ok."""
    out = []

    def fake(url, data, headers):
        out.append((url, data, headers))
        if url.endswith("auth.test"):
            return json.dumps({"ok": True, "team": "팀", "user": "ibis"}).encode()
        if url.endswith("conversations.info"):
            return json.dumps({"ok": False, "error": "missing_scope"}).encode()
        return b'{"ok": true}'
    monkeypatch.setattr(transport, "post", fake)
    return out


def test_keep_modes(client, sent, monkeypatch, folder):
    from core import settings
    put = lambda name, **d: client.post("/api/destinations", json={"name": name, "destination": {"kind": "slack", "channel": "C1", **d}}).get_json()

    # session: 파일에는 토큰이 없고, 이번 실행 동안만 쓴다
    r = put("s", keep="session", token=TOKEN)
    assert r["destinations"]["s"]["token"] is True and r["destinations"]["s"]["keep"] == "session"
    raw = (folder / "destinations.json").read_text(encoding="utf-8")
    assert TOKEN not in raw and settings.named("destinations.json")["s"]["token"] == ""
    assert client.post("/api/send", json={"name": "s", "title": "t"}).status_code == 200
    credentials.SESSION.clear()  # 프로그램을 다시 켠 것처럼
    assert client.get("/api/destinations").get_json()["destinations"]["s"]["token"] is False
    assert "다시 입력" in client.post("/api/send", json={"name": "s", "title": "t"}).get_json()["error"]
    assert put("s", keep="session", token="")["error"] == "'봇 토큰' 칸을 입력하세요."

    # env: 파일에는 변수 이름만
    monkeypatch.delenv("IBIS_TEST_TOKEN", raising=False)
    r = put("e", keep="env", token="IBIS_TEST_TOKEN")
    assert r["destinations"]["e"]["token"] == {"env": "IBIS_TEST_TOKEN", "set": False}
    assert "환경변수 IBIS_TEST_TOKEN이 없습니다" in client.post("/api/send", json={"name": "e", "title": "t"}).get_json()["error"]
    monkeypatch.setenv("IBIS_TEST_TOKEN", TOKEN)
    assert client.get("/api/destinations").get_json()["destinations"]["e"]["token"]["set"] is True
    sent.clear()
    assert client.post("/api/send", json={"name": "e", "title": "t"}).status_code == 200
    assert sent[0][2]["Authorization"] == f"Bearer {TOKEN}"
    assert put("e2", keep="env", token="NOT VALID")["error"].startswith("환경변수 이름은")
    assert put("x", keep="cloud", token="a")["error"].startswith("비밀 값 보관 방식은")

    # 예전 파일(keep 없음)은 saved로 읽는다
    data = settings.named("destinations.json")
    data["old"] = {"kind": "slack", "done": False, "token": credentials.protect(TOKEN), "channel": "C9"}
    settings.write("destinations.json", data)
    assert client.get("/api/destinations").get_json()["destinations"]["old"]["keep"] == "saved"
    assert client.post("/api/send", json={"name": "old", "title": "t"}).status_code == 200


def test_check_route(client, sent, folder):
    client.post("/api/destinations", json={"name": "s", "destination": {"kind": "slack", "keep": "session", "token": TOKEN, "channel": "C1"}})
    sent.clear()
    msg = client.post("/api/destinations/check", json={"name": "s"}).get_json()["message"]
    assert "워크스페이스 팀" in msg and "channels:read" in msg
    assert [s[0].rsplit("/", 1)[1] for s in sent] == ["auth.test", "conversations.info"]  # 메시지는 보내지 않았다
    client.post("/api/destinations", json={"name": "h", "destination": {"kind": "webhook", "keep": "saved", "url": "https://hooks.example.com/x", "content": "json",
                                                                         "template": '{"t": "{제목}"}'}})
    sent.clear()
    assert "[시험 보내기]" in client.post("/api/destinations/check", json={"name": "h"}).get_json()["message"] and sent == []


# ---------- 채팅 명령(코어) ----------

ORDERS = pl.DataFrame({"day": ["d1", "d1", "d2"], "status": ["ok", "cancel", "ok"], "amount": [100, 50, 300]})


@pytest.fixture
def shop(client, tmp_path, folder):
    from core import server
    from core import settings
    path = tmp_path / "shop.duckdb"
    with duckdb.connect(str(path)) as raw:
        raw.register("df", ORDERS.to_arrow())
        raw.execute("create table orders as select * from df")
    server.state.update(con=ibis.duckdb.connect(str(path), read_only=True), opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})
    (tmp_path / "m.yaml").write_text("""metrics:
  취소율:
    format: percent
    numerator: {agg: count, where: "status = cancel"}
    denominator: {agg: count}
""", encoding="utf-8")
    client.post("/api/metrics", json={"path": str(tmp_path / "m.yaml")})
    settings.save_search("전체 주문", {"schema": "", "base": "orders", "union": {"tables": [], "how": "left"}, "joins": [], "periods": [], "sorts": ["amount desc"],
                                     "columns": [], "conditions": [{"line": "status = ok", "or": True}, "amount > 200"], "limit": "20"})
    return server


def test_commands(shop):
    assert commands.answer("") == commands.HELP
    assert "취소율" in commands.answer("지표")
    assert "전체 주문" in commands.answer("검색")
    # 조건 (status = ok or amount > 200)
    assert commands.answer('지표 취소율 검색="전체 주문"') == "취소율 (전체 주문): 0.00%"
    out = commands.answer('검색 "전체 주문" 행=1')
    assert out.startswith("전체 주문: 2행 (앞 1행)") and "300" in out
    assert "검색=<이름>" in commands.answer("지표 취소율")
    assert "비슷한" in commands.answer('지표 취소율 검색="전체주문"') or "없" in commands.answer('지표 취소율 검색="전체주문"')
    assert commands.answer('검색 "열린').startswith("할 수 없습니다: 따옴표")
    assert commands.answer("삭제 orders").startswith("모르는 명령입니다")
    shop.state["con"] = None
    assert "먼저 데이터에 연결" in commands.answer('검색 "전체 주문"')


def test_grouped():
    from core import querying
    assert querying.grouped(["a = 1", {"line": "b = 2", "or": True}, {"line": "c = 3", "quotes": False}, "d = 4"]) == \
        ["a = 1", {"any": ["b = 2", {"line": "c = 3", "quotes": False}]}, "d = 4"]


# ---------- Slack 봇(받기) ----------

class FakeSocket:
    def __init__(self):
        self.socket_mode_request_listeners, self.responses, self.connected = [], [], False

    def connect(self):
        self.connected = True

    def close(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    def send_socket_mode_response(self, response):
        self.responses.append(response.to_dict())


def fake_clients(bot_token, app_token):
    web = types.SimpleNamespace(auth_test=lambda: {"team": "팀", "user": "ibis"})
    return web, FakeSocket()


def slash(user, channel, text):
    return types.SimpleNamespace(type="slash_commands", envelope_id="e1",
                                 payload={"user_id": user, "channel_id": channel, "text": text, "response_url": "https://hooks.slack.com/commands/x"})


def test_bot_allow_and_answer(monkeypatch):
    replies = []
    bot = slack_bot.SlackBot(TOKEN, APP, ["U1"], ["C1"], lambda text: f"답: {text}", make=fake_clients, send=lambda url, text: replies.append((url, text)))
    bot.start()
    assert bot.running() and bot.team == "팀"
    socket = bot.socket
    threads = []
    monkeypatch.setattr(slack_bot.threading, "Thread", lambda target, args, daemon: types.SimpleNamespace(start=lambda: threads.append(target(*args))))
    bot.on_request(socket, slash("U2", "C1", "지표"))  # 허용하지 않은 사용자
    bot.on_request(socket, slash("U1", "C9", "지표"))  # 허용하지 않은 채널
    bot.on_request(socket, slash("U1", "C1", "지표"))
    assert [r["payload"]["text"].startswith(("이 사용자", "이 사용자", "처리 중: 지표")[i]) for i, r in enumerate(socket.responses)] == [True] * 3
    assert replies == [("https://hooks.slack.com/commands/x", "답: 지표")]
    assert [e["allowed"] for e in bot.log] == [False, False, True]
    bot.stop()
    assert not bot.running()


def test_bot_routes(client, monkeypatch, folder):
    monkeypatch.setattr(slack_bot, "clients", fake_clients)
    from integrations import bot_app
    save = lambda **d: client.post("/api/bot", json={"keep": "session", "bot_token": TOKEN, "app_token": APP, "users": "", "channels": "", **d}).get_json()
    assert save(bot_token="abc")["error"] == "봇 토큰은 xoxb-로 시작합니다."
    assert "Slack ID가 아닌" in save(users="u1; drop")["error"]
    assert client.post("/api/bot", json={"keep": "session", "bot_token": TOKEN, "app_token": APP, "users": "U1"}).get_json()["channels"] == []  # 채널을 안 보내면 빈 목록
    assert save(channels=5)["error"] == "허용할 채널은 쉼표로 나눈 ID여야 합니다."
    r = save()
    assert r["saved"] and r["bot_token"] is True and r["users"] == []
    assert TOKEN not in (folder / "slack_bot.json").read_text(encoding="utf-8")
    assert "허용할 사용자" in client.post("/api/bot/start", json={}).get_json()["error"]  # 허용한 사람이 없으면 켜지 않는다
    r = save(bot_token="", app_token="", users="u1, U2", channels="c1")
    assert r["users"] == ["U1", "U2"] and r["channels"] == ["C1"]
    r = client.post("/api/bot/start", json={}).get_json()
    assert r["running"] and r["team"] == "팀"
    assert client.post("/api/bot/stop", json={}).get_json()["running"] is False
    credentials.SESSION.clear()
    assert "다시 입력" in client.post("/api/bot/start", json={}).get_json()["error"]
    bot_app.stop()
