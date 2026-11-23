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


def test_streamed_download_is_not_pulled_or_gzipped():
    from flask import Response
    pulled = []

    def chunks():
        for i in range(4):
            pulled.append(i)
            yield b"x" * 30000

    download = Response(chunks(), headers={"Content-Length": "120000", "Content-Disposition": "attachment; filename=a.csv"})  # server.download과 같은 모양
    with server.app.test_request_context(headers={"Accept-Encoding": "gzip"}):
        got = server.squeeze(download)
    assert got.headers.get("Content-Encoding") is None and pulled == []  # 내보내기를 통째로 읽어 압축하지 않는다
    assert len(b"".join(got.response)) == 120000


BIG = 5 * server.SEND_PIECE // 2  # 조각 두 개 반
SAFE_PAGE = 64 * 1024  # 화면 응답이 이 아래면 통째로 보내던 때에도 끊기지 않았다(B13 측정)


def test_big_body_goes_out_in_pieces():
    """B13: 큰 응답 몸은 SEND_PIECE 조각으로 나눠 보낸다. gzip을 하든 안 하든(안 받는 요청·200이 아닌 것·이미 압축한 것) 같다."""
    from flask import Response
    body = bytes(range(256)) * (BIG // 256)
    with server.app.test_request_context(headers={"Accept-Encoding": "identity"}):
        plain = server.squeeze(Response(body, mimetype="text/html"))
        failed = server.squeeze(Response(body, status=500, mimetype="application/json"))
    with server.app.test_request_context(headers={"Accept-Encoding": "gzip"}):
        packed = server.squeeze(Response(body, mimetype="application/zip"))
        zipped = server.squeeze(Response(b"a" * BIG * 100, mimetype="application/json"))
    for got in (plain, failed, packed):
        assert [len(p) for p in got.response] == [server.SEND_PIECE, server.SEND_PIECE, BIG - 2 * server.SEND_PIECE]
        assert b"".join(got.response) == body and got.content_length == len(body) and got.headers.get("Content-Encoding") is None
    assert zipped.headers["Content-Encoding"] == "gzip" and gzip.decompress(b"".join(zipped.response)) == b"a" * BIG * 100
    assert zipped.content_length == sum(len(p) for p in zipped.response) and max(len(p) for p in zipped.response) <= server.SEND_PIECE


def test_prep_page_is_small_and_scripts_are_static_files(client):
    """B13: 전처리 화면은 스크립트를 정적 파일로 불러, 화면 응답(gzip)이 SAFE_PAGE 아래다."""
    page = client.get("/prep", headers={"Accept-Encoding": "gzip"})
    assert page.status_code == 200 and page.headers["Content-Encoding"] == "gzip" and len(page.data) < SAFE_PAGE
    for name in ("prep.js", "prep_tail.js", "analysis.js"):
        assert client.get(f"/static/prep/{name}").status_code == 200, name


def test_small_response_is_left_alone(client):
    small = client.get("/api/usage", headers={"Accept-Encoding": "gzip"})
    assert small.status_code == 200 and small.headers.get("Content-Encoding") is None
