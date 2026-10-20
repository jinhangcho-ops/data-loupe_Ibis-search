"""연동 공간: Notion 보내기, GA4 가져오기(서비스 계정 JWT·runReport), 연동 화면의 서비스 목록, Slack 봇의 질문 명령.
바깥으로 보내지 않는다(transport를 가짜로 바꿔 끼운다). 키·토큰·데이터는 테스트 안에서 지어낸다."""
import base64
import json

import pytest

from adapters import ga4, notion, transport

PAGE = "1a2b3c4d5e6f708192a3b4c5d6e7f809"
TOKEN = "ntn_made_up_token"


@pytest.fixture
def calls(monkeypatch):
    out = []

    def fake(method, url, data=None, headers=None, timeout=15):
        out.append((method, url, json.loads(data) if data and data[:1] in (b"{", b"[") else data, headers))
        if url.endswith(f"/pages/{PAGE}"):
            return json.dumps({"id": PAGE, "properties": {"title": {"type": "title", "title": [{"plain_text": "주간 보고"}]}}}).encode()
        return b'{"object": "list"}'
    monkeypatch.setattr(transport, "request", fake)
    monkeypatch.setattr(transport, "post", lambda url, data, headers, timeout=15: fake("POST", url, data, headers, timeout))
    return out


def test_notion_send_and_check(client, calls, folder):
    put = client.post("/api/destinations", json={"name": "노션", "destination": {"kind": "notion", "keep": "session", "token": TOKEN,
                                                                              "page": f"https://www.notion.so/team/Weekly-{PAGE}?pvs=4"}}).get_json()
    assert put["destinations"]["노션"]["token"] is True
    assert "주간 보고" in client.post("/api/destinations/check", json={"name": "노션"}).get_json()["message"]
    long = "가" * 4500
    assert client.post("/api/send", json={"name": "노션", "title": "끝났습니다", "text": long, "rows": 12, "seconds": 75,
                                          "table": {"columns": ["a"], "rows": [{"a": 1}]}}).status_code == 200
    method, url, body, headers = calls[-1]
    assert (method, url) == ("PATCH", f"https://api.notion.com/v1/blocks/{PAGE}/children") and headers["Authorization"] == f"Bearer {TOKEN}"
    kinds = [b["type"] for b in body["children"]]
    assert kinds == ["heading_3", "paragraph", "paragraph", "code"]
    assert [len(r["text"]["content"]) for r in body["children"][1]["paragraph"]["rich_text"]] == [2000, 2000, 500]  # 2,000자씩 나눔
    assert body["children"][2]["paragraph"]["rich_text"][0]["text"]["content"] == "행 수 12 · 걸린 시간 1분 15초"
    bad = client.post("/api/destinations", json={"name": "x", "destination": {"kind": "notion", "token": "t", "page": "not-a-page"}})
    assert "32자리" in bad.get_json()["error"]


# ---------- GA4 ----------

@pytest.fixture
def key_file(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    path = tmp_path / "sa.json"
    path.write_text(json.dumps({"type": "service_account", "client_email": "reader@made-up.iam.gserviceaccount.com", "private_key": pem,
                                "token_uri": "https://oauth2.googleapis.com/token"}), encoding="utf-8")
    return path, private.public_key()


@pytest.fixture
def google(monkeypatch):
    """토큰 주소와 runReport(2쪽)를 흉내 낸다."""
    seen = []

    def fake(method, url, data=None, headers=None, timeout=15):
        seen.append((method, url, data, headers))
        if url.endswith("/token"):
            return json.dumps({"access_token": "made-up-access", "expires_in": 3600}).encode()
        if url.endswith("/metadata"):
            return json.dumps({"dimensions": [{}] * 3, "metrics": [{}] * 2}).encode()
        body = json.loads(data)
        rows = [{"dimensionValues": [{"value": f"2026-01-0{i + 1}"}], "metricValues": [{"value": str(10 + i)}, {"value": str(0.5 + i)}]} for i in range(3)]
        page = rows[body["offset"]:body["offset"] + 2]
        return json.dumps({"rowCount": 3, "metricHeaders": [{"name": "activeUsers", "type": "TYPE_INTEGER"}, {"name": "bounceRate", "type": "TYPE_FLOAT"}],
                           "rows": page}).encode()
    monkeypatch.setattr(transport, "request", fake)
    monkeypatch.setattr(transport, "post", lambda url, data, headers, timeout=15: fake("POST", url, data, headers, timeout))
    monkeypatch.setattr(ga4, "PAGE_ROWS", 2)
    return seen


def test_ga4_jwt_and_fetch(key_file, google):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    path, public = key_file
    frame, total = ga4.fetch(str(path), {"property": "properties/123", "start": "7daysAgo", "end": "yesterday", "dimensions": "date", "metrics": "activeUsers, bounceRate"})
    assert total == 3 and frame.columns == ["date", "activeUsers", "bounceRate"]
    assert frame["activeUsers"].to_list() == [10, 11, 12] and frame["bounceRate"].to_list() == [0.5, 1.5, 2.5] and str(frame["activeUsers"].dtype) == "Int64"
    token_call = google[0]
    form = dict(x.split("=", 1) for x in token_call[2].decode().split("&"))
    head, claims, sig = form["assertion"].split(".")
    pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    public.verify(pad(sig), f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())  # 서명이 맞다
    assert json.loads(pad(claims))["scope"] == ga4.SCOPE and json.loads(pad(claims))["iss"].startswith("reader@")
    reports = [c for c in google if ":runReport" in c[1]]
    assert [json.loads(c[2])["offset"] for c in reports] == [0, 2] and reports[0][3]["Authorization"] == "Bearer made-up-access"


@pytest.mark.parametrize("spec, message", [
    ({"property": "abc", "start": "today", "end": "today", "metrics": "x"}, "속성 ID"),
    ({"property": "1", "start": "어제", "end": "today", "metrics": "x"}, "기간"),
    ({"property": "1", "start": "today", "end": "today", "metrics": ""}, "측정항목"),
    ({"property": "1", "start": "today", "end": "today", "metrics": "a;b"}, "이름이 올바르지"),
])
def test_ga4_bad_spec(spec, message):
    with pytest.raises(ValueError, match=message):
        ga4.check_report(spec)


def test_ga4_routes(client, key_file, google, tmp_path, folder):
    import uploads
    path, _ = key_file
    out = tmp_path / "ga4.parquet"
    body = {"key_path": str(path), "property": "123", "start": "7daysAgo", "end": "yesterday", "dimensions": "date", "metrics": "activeUsers, bounceRate", "output": str(out)}
    assert client.post("/api/ga4", json=body).get_json()["property"] == "123"
    assert "private_key" not in (folder / "ga4.json").read_text(encoding="utf-8")  # 경로만 저장
    assert "측정기준 3개" in client.post("/api/ga4/check", json={}).get_json()["message"]
    d = client.post("/api/ga4/fetch", json={}).get_json()
    assert d["rows"] == 3 and out.exists() and uploads.entries()[0]["source"] == str(out)
    assert "이미 있습니다" in client.post("/api/ga4/fetch", json={}).get_json()["error"]  # 덮어쓰지 않는다
    services = {s["key"]: s for s in client.get("/api/integrations").get_json()["services"]}
    assert set(services) == {"slack", "notion", "webhook", "slack_bot", "ga4", "llm"} and services["ga4"]["ready"] is True
    assert services["notion"]["abilities"] == ["send"] and services["slack_bot"]["status"] == "설정 안 됨"
    bad = tmp_path / "bad.json"
    bad.write_text('{"type": "authorized_user"}', encoding="utf-8")
    assert "서비스 계정" in client.post("/api/ga4", json={**body, "key_path": str(bad)}).get_json()["error"]


def test_slack_question(client, tmp_path, folder, monkeypatch):
    import duckdb
    from lazy import ibis

    import commands
    import server
    from adapters import llm
    path = tmp_path / "shop.duckdb"
    with duckdb.connect(str(path)) as raw:
        raw.execute("create table orders as select 1 as id, 100 as amount")
    server.state.update(con=ibis.duckdb.connect(str(path), read_only=True), opened={"kind": "folder", "path": str(tmp_path), "file": str(path)})

    class Fake:
        name = "가짜"

        def complete(self, system, question):
            self.q = question
            return "```sql\nselect sum(amount) from orders\n```"
    fake = Fake()
    monkeypatch.setattr(llm, "client", lambda: fake)
    out = commands.answer("질문 orders의 'amount' 합계는?")  # 따옴표가 섞여도 된다
    assert "select sum(amount) from orders" in out and "실행하지 않았습니다" in out and fake.q == "orders의 'amount' 합계는?"
    monkeypatch.setattr(llm, "client", lambda: type("F", (), {"name": "f", "complete": lambda self, s, q: "delete from orders"})())
    assert commands.answer("질문 다 지워").startswith("할 수 없습니다")
    assert commands.answer("질문").startswith("질문을 쓰세요")
