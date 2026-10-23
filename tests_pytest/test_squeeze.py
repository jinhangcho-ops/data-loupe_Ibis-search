"""큰 응답은 gzip으로(로컬 통신을 검사하는 보안 프로그램이 큰 응답을 가끔 끊는 것을 피한다), 작은 응답과 gzip을 못 받는 쪽은 그대로."""
import gzip
import json

from core import server


def test_big_json_is_gzipped_and_same_after_unzip(client):
    from metrics import catalog_app  # noqa: F401  지표 사전 라우트(큰 JSON)
    plain = client.get("/api/catalog")
    zipped = client.get("/api/catalog", headers={"Accept-Encoding": "gzip, deflate"})
    assert plain.headers.get("Content-Encoding") is None and len(plain.data) > server.GZIP_FROM
    assert zipped.headers["Content-Encoding"] == "gzip" and len(zipped.data) < len(plain.data) // 3
    assert json.loads(gzip.decompress(zipped.data)) == plain.get_json()


def test_small_response_is_left_alone(client):
    small = client.get("/api/usage", headers={"Accept-Encoding": "gzip"})
    assert small.status_code == 200 and small.headers.get("Content-Encoding") is None
