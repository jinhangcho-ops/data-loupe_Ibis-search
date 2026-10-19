"""비밀번호를 디스크에 남기지 않는지. 연결·적재 계정의 아이디는 사용자가 정한 대로 저장한다(비밀번호만 저장하지 않음)."""
from urllib.parse import quote, quote_plus

import pytest

import collect_job
import load_kafka
import server

FIELD = "Field-S3cret!"  # 비밀번호 칸에 넣은 값(지어낸 값)
INLINE = "Inline-P4ss"  # 주소 안에 넣은 비밀번호(지어낸 값)


def everything(folder):
    """설정 폴더의 모든 파일 내용(바이트)."""
    return b"".join(p.read_bytes() for p in folder.rglob("*") if p.is_file())


def test_password_never_reaches_disk(client, folder):
    for url, password in ((f"postgres://dbuser:{INLINE}@db.example.invalid:5432/sales", FIELD), ("postgres://db.example.invalid/sales", FIELD)):
        assert client.post("/api/connect", json={"kind": "url", "url": url, "user": "analyst", "password": password}).status_code == 200
    client.post("/api/settings", json={"theme": "dark"})
    client.post("/api/searches", json={"name": "s", "search": {"base": "orders"}})
    saved = everything(folder)
    assert saved, "연결 기록이 남아 있어야 한다"
    for secret in (FIELD, INLINE):
        for form in {secret, quote(secret, safe=""), quote_plus(secret, safe="")}:
            assert form.encode() not in saved


def test_connection_list_and_recent_have_no_password(client):
    client.post("/api/connect", json={"kind": "url", "url": f"postgres://dbuser:{INLINE}@db.example.invalid/sales", "user": "analyst", "password": FIELD})
    listed = client.get("/api/connections").get_json()["connections"]
    recent = client.get("/api/recent").get_json()["recent"]
    for info in [c["info"] for c in listed] + [recent]:
        assert "password" not in info
        assert INLINE not in str(info) and FIELD not in str(info)
    assert recent["user"] == "analyst"


def test_restart_requires_login_again(client):
    """다시 켠 것처럼 메모리의 연결을 비우면, 연결은 끊긴 상태이고 칸에는 비밀번호 없이 주소·아이디만 채워진다."""
    client.post("/api/connect", json={"kind": "url", "url": "postgres://db.example.invalid/sales", "user": "analyst", "password": FIELD})
    server.state.update(con=None, passwords=[], opened=None)
    assert client.get("/api/state").get_json() == {"connected": False}
    assert client.get("/api/recent").get_json()["recent"] == {"kind": "url", "url": "postgres://db.example.invalid/sales", "user": "analyst"}


def test_saved_configs_refuse_passwords(folder):
    with pytest.raises(ValueError, match="비밀번호"):
        collect_job.normalize({"load": {"target": "table", "table": {"connection": {"kind": "url", "url": f"postgres://u:{INLINE}@h/db"}}}})
    with pytest.raises(ValueError, match="비밀번호"):
        load_kafka.save("k", {"servers": "h:9092", "topic": "t", "password": FIELD})
    assert FIELD.encode() not in everything(folder) and INLINE.encode() not in everything(folder)
