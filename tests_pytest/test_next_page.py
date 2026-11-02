"""대시보드 화면 전환(Next.js 정적 내보내기): static/next/dashboard.html이 있으면 그것을, 없으면 옛 화면을 내려준다.
빌드 결과가 있든 없든 같은 결과가 나오게 dash_app.NEXT_PAGE를 임시 파일로 바꿔 끼운다."""
import re

from core import server
from dashboard import dash_app


def csp_nonce(response):
    return re.search(r"script-src 'nonce-([^']+)'", response.headers["Content-Security-Policy"]).group(1)


def test_old_dashboard_without_next(client, monkeypatch, tmp_path):
    monkeypatch.setattr(dash_app, "NEXT_PAGE", tmp_path / "없음.html")
    r = client.get("/dashboard")
    assert r.status_code == 200 and 'id="d-only"' in r.text
    assert "'strict-dynamic'" not in r.headers["Content-Security-Policy"]  # 옛 page()의 CSP는 그대로


def test_next_dashboard(client, monkeypatch, tmp_path):
    page = tmp_path / "dashboard.html"
    page.write_text('<html><head><link rel="preload" as="script" href="/next/_next/b.js"/><script src="/next/_next/a.js"></script></head><body><script>1</script></body></html>', encoding="utf-8")
    monkeypatch.setattr(dash_app, "NEXT_PAGE", page)
    r = client.get("/dashboard")
    nonce = csp_nonce(r)
    assert r.status_code == 200 and 'id="d-only"' not in r.text
    assert r.text.count("<script") == r.text.count(f'<script nonce="{nonce}"') == 2
    assert f'<link nonce="{nonce}" rel="preload" as="script"' in r.text  # 미리 받기 링크도 script-src 검사를 받는다
    assert f"script-src 'nonce-{nonce}' 'strict-dynamic';" in r.headers["Content-Security-Policy"]
    assert any(h.startswith("key=") for h in r.headers.getlist("Set-Cookie"))
    legacy = client.get("/dashboard?legacy=1")  # 전환 기간의 비교용: 빌드 결과가 있어도 옛 화면
    assert 'id="d-only"' in legacy.text and "'strict-dynamic'" not in legacy.headers["Content-Security-Policy"]


def test_next_assets_served_from_web_out(client, monkeypatch, tmp_path):
    """자산은 web/out/(static/ 밖)에서 /next/...로. 없는 파일은 404, 폴더 밖은 못 나간다."""
    (tmp_path / "_next").mkdir()
    (tmp_path / "_next" / "a.js").write_text("1", encoding="utf-8")
    monkeypatch.setattr(server, "NEXT_DIR", tmp_path)
    assert client.get("/next/_next/a.js").status_code == 200 and client.get("/next/_next/b.js").status_code == 404
    assert client.get("/next/..%2Fcore%2Fserver.py").status_code in (404, 403)


def test_dash_constants(client):
    r = client.get("/api/dash/constants")
    assert r.status_code == 200 and r.is_json
    assert r.get_json() == server.app.json.loads(server.app.json.dumps(server.constants("dashboard")))
    assert "/api/dash/constants" in server.FREE
