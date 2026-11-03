"""대시보드 화면(Next.js 정적 내보내기): /dashboard는 web/out/dashboard.html만 내려준다(옛 화면은 없다). 빌드 결과가 없으면 빌드하라는 짧은 안내.
빌드 결과가 있든 없든 같은 결과가 나오게 dash_app.NEXT_PAGE를 임시 파일로 바꿔 끼운다."""
import re

from conftest import ROOT
from core import server
from dashboard import dash_app


def csp_nonce(response):
    return re.search(r"script-src 'nonce-([^']+)'", response.headers["Content-Security-Policy"]).group(1)


def test_not_built_says_how_to_build(client, monkeypatch, tmp_path):
    monkeypatch.setattr(dash_app, "NEXT_PAGE", tmp_path / "없음.html")
    for url in ("/dashboard", "/dashboard?legacy=1"):  # 옛 화면으로 되돌리는 길은 없다
        r = client.get(url)
        assert r.status_code == 200 and "대시보드 화면이 빌드되지 않았어요" in r.text and "npm run build" in r.text
        assert "<script" not in r.text and 'id="d-only"' not in r.text
    assert not (ROOT / "static" / "dashboard").exists()


def test_spec_requires_web_out():
    """exe 빌드는 web/out을 늘 넣고, 없으면 멈춘다(옛 화면이 없어 빠진 채 묶이면 대시보드가 빈다)."""
    spec = (ROOT / "DataLoupe.spec").read_text(encoding="utf-8")
    assert '("web/out", "web/out")' in spec and 'if not os.path.isfile("web/out/dashboard.html"):' in spec and "sys.exit(" in spec
    assert 'if os.path.isdir("web/out")' not in spec


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
    legacy = client.get("/dashboard?legacy=1")  # 예전 비교용 주소도 Next 화면
    assert "/next/_next/a.js" in legacy.text and "'strict-dynamic'" in legacy.headers["Content-Security-Policy"]


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
