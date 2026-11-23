"""B7 템플릿 내려받기(plans/dash-blocks.md "B7 세부 명세"): 템플릿 형식 검사(check_template), 대시보드에서 템플릿 만들기(template_from),
파일 읽기(read_template), 기본 템플릿 폴더(builtin_templates), [더 받기](dash_templates, 요청 함수를 바꿔 끼워 바깥에 나가지 않는다), 라우트.
값은 시험 안에서 지어낸다."""
import base64
import json
import urllib.error

import pytest

from core import dash_store as store
from core import server
from dashboard import dash_templates

EMPTY_COLORS = {"series": {}, "parts": {}, "order": [], "parts_order": []}
DEFAULT_VIEW = {"type": "", "unit": "day", "compare": {"mode": "span"}, "better": "up"}


def lay(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def sec(section_id, layout, **more):
    return {"id": section_id, "title": "", "layout": layout, "follow": True, "view": {}, **more}


def piece(kind, section, layout, **more):
    return {"kind": kind, "title": f"{kind} 조각", "section": section, "layout": layout, **more}


def tpl(**more):
    """섹션 하나(6×4칸)에 KPI 조각 하나인 형식 1 템플릿."""
    return {"format": 1, "name": "시험 템플릿", "sections": [sec("s1", lay(0, 0, 6, 4))], "pieces": [piece("kpi", "s1", lay(0, 0, 96, 32))], **more}




# ---------- check_template ----------

def test_check_template_fills_defaults():
    found = store.check_template(tpl())
    assert store.ID.fullmatch(found.pop("id"))
    assert found == {"format": 1, "name": "시험 템플릿", "help": "", "pages": [], "sections": [sec("s1", lay(0, 0, 6, 4))],
                     "pieces": [{"kind": "kpi", "title": "kpi 조각", "section": "s1", "layout": lay(0, 0, 96, 32), "colors": EMPTY_COLORS, "view": DEFAULT_VIEW}],
                     "unknown": []}


def test_check_template_keeps_shape_settings_only():
    view = {"type": "bar", "format": "percent", "digits": 1, "zeros": True, "unit_text": "원", "compare": {"mode": "point"}, "better": "down"}
    found = store.check_template(tpl(id="t1", help=" 설명 ", pages=["첫째"], pieces=[piece("chart", "s1", lay(0, 0, 96, 32), view=view)]))
    assert found["id"] == "t1" and found["help"] == "설명" and found["pages"] == ["첫째"]
    assert found["pieces"][0]["view"] == {**DEFAULT_VIEW, **view}


@pytest.mark.parametrize("one, words", [
    (piece("table", "s1", lay(0, 0, 96, 32), view={"group_cols": ["region"]}), "담을 수 없는"),
    (piece("kpi", "s1", lay(0, 0, 96, 32), view={"cond_rules": [{"column": "amt", "match": ">0", "color": "#f00"}]}), "담을 수 없는"),
    (piece("chart", "s1", lay(0, 0, 96, 32), view={"link_to": []}), "담을 수 없는"),
    (piece("kpi", "s1", lay(0, 0, 96, 32), view={"page": "첫째"}), "담을 수 없는"),
    (piece("kpi", "s1", lay(0, 0, 96, 32), view={"frame": {}}), "담을 수 없는"),
    (piece("chip_row", "s1", lay(0, 0, 96, 32), view={"frame": {"column": "region"}}), "담을 수 없는"),
    (piece("segment", "s1", lay(0, 0, 96, 32), view={"frame": {"cells": []}}), "담을 수 없는"),
    (piece("kpi", "s1", lay(0, 0, 96, 32), source={"table": "sales"}), "쓸 수 없는 항목"),
    (piece("kpi", "s1", lay(0, 0, 96, 32), data={}), "쓸 수 없는 항목"),
    (piece("button", "s1", lay(0, 0, 96, 32), view={"frame": {"label": "크게", "action": "zoom", "arg": "c1"}}), "카드를 가리키는"),
    (piece("nav_rail", "s1", lay(0, 0, 96, 32), view={"frame": {"items": [{"label": "표", "action": "csv", "arg": "c1"}]}}), "카드를 가리키는"),
    (piece("kpi", "s1", lay(0, 0, 96, 32), view={"press": "cond"}), "숫자 타일에만"),
    (piece("a b", "s1", lay(0, 0, 96, 32)), "종류"),
    (piece("kpi", "s9", lay(0, 0, 96, 32)), "섹션이 없어요"),
    (piece("kpi", "s1", lay(0, 0, 97, 32)), "섹션 밖"),
])
def test_check_template_rejects_piece(one, words):
    with pytest.raises(ValueError, match=words):
        store.check_template(tpl(pieces=[one]))


def test_check_template_keeps_page_action_button():
    frame = {"label": "다음", "action": "page", "arg": "둘째"}
    found = store.check_template(tpl(pieces=[piece("button", "s1", lay(0, 0, 32, 8), view={"frame": frame})]))
    assert found["pieces"][0]["view"] == {**DEFAULT_VIEW, "frame": {"label": "다음", "icon": "", "action": "page", "arg": "둘째", "ambient": False}}


@pytest.mark.parametrize("kind", ["future_kind", "recent_rows"])
def test_check_template_keeps_unknown_kind_without_settings(kind):
    found = store.check_template(tpl(pieces=[piece("kpi", "s1", lay(0, 0, 40, 32)),
                                             piece(kind, "s1", lay(41, 0, 40, 32), colors={"x": 1}, view={"anything": 1})]))
    assert found["pieces"][1] == {"kind": kind, "title": f"{kind} 조각", "section": "s1", "layout": lay(41, 0, 40, 32)}
    assert found["unknown"] == [kind]


def test_check_template_allows_many_pieces_in_one_section_but_keeps_gap():
    two = [piece("kpi", "s1", lay(0, 0, 40, 32)), piece("text", "s1", lay(41, 0, 40, 32), view={"frame": {"text": "안내", "size": "sm"}})]
    found = store.check_template(tpl(pieces=two))
    assert [p["section"] for p in found["pieces"]] == ["s1", "s1"]
    assert found["pieces"][1]["view"]["frame"] == {"text": "안내", "size": "sm", "align": "left"}
    with pytest.raises(ValueError, match="겹치거나 붙어"):
        store.check_template(tpl(pieces=[two[0], {**two[1], "layout": lay(40, 0, 40, 32)}]))


@pytest.mark.parametrize("bad, words", [
    ({"format": 2}, "새 버전"),
    ({"format": 0}, "형식 번호"),
    ({"format": "1"}, "형식 번호"),
    ({"format": True}, "형식 번호"),
    ({"id": "a b"}, "템플릿 id"),
    ({"name": ""}, "템플릿 이름"),
    ({"help": "가" * 101}, "템플릿 설명"),
    ({"pages": ["a", "a"]}, "같은 이름의 쪽"),
    ({"pieces": []}, "조각이 1개 이상"),
    ({"pieces": [piece("kpi", "s1", lay(0, 0, 8, 8))] * (store.MAX_CARDS + 1)}, "조각이 1개 이상"),
    ({"cards": []}, "템플릿에 쓸 수 없는 항목"),
])
def test_check_template_rejects(bad, words):
    with pytest.raises(ValueError, match=words):
        store.check_template(tpl(**bad))


def test_check_template_needs_format():
    with pytest.raises(ValueError, match="형식 번호"):
        store.check_template({"name": "번호 없음", "sections": [], "pieces": []})
    with pytest.raises(ValueError, match="템플릿은 객체"):
        store.check_template(["템플릿"])


def test_check_template_reads_old_saved_shape():
    old = {"id": "old1", "name": "옛 템플릿", "cards": [{"kind": "kpi", "title": "옛 카드", "layout": lay(2, 0, 4, 4), "colors": None},
                                                    {"kind": "chart", "title": "옛 차트", "layout": lay(6, 0, 6, 8)}]}
    found = store.check_template(old)
    assert found == {"format": 1, "id": "old1", "name": "옛 템플릿", "help": "", "pages": [],
                     "sections": [sec("s0", lay(2, 0, 4, 4)), sec("s1", lay(6, 0, 6, 8))],
                     "pieces": [{"kind": "kpi", "title": "옛 카드", "section": "s0", "layout": lay(0, 0, 64, 32), "colors": EMPTY_COLORS, "view": DEFAULT_VIEW},
                                {"kind": "chart", "title": "옛 차트", "section": "s1", "layout": lay(0, 0, 96, 64), "colors": EMPTY_COLORS, "view": DEFAULT_VIEW}],
                     "unknown": []}


def test_check_template_twice_is_same():
    once = store.check_template(tpl(pages=["첫째"], pieces=[piece("chart", "s1", lay(0, 0, 40, 32), view={"type": "line", "digits": 2}),
                                                            piece("future_kind", "s1", lay(41, 0, 40, 32))]))
    assert store.check_template(once) == once


# ---------- template_from ----------

def dashboard_body():
    """편집 중 대시보드: 섹션 하나에 KPI(값 조건·테이블이 든)·카드를 가리키는 단추·컬럼을 고른 칩 줄."""
    return {"pages": ["첫째"], "sections": [{"id": "s1", "layout": lay(0, 0, 6, 4), "view": {"page": "첫째"}}],
            "cards": [{"id": "c1", "kind": "kpi", "title": "매출", "section": "s1", "layout": lay(0, 0, 40, 16), "source": {"table": "sales"},
                       "view": {"format": "currency", "digits": 2, "cond_rules": [{"column": "amt", "match": ">0", "color": "#f00"}]}},
                      {"id": "c2", "kind": "button", "title": "단추", "section": "s1", "layout": lay(41, 0, 20, 8),
                       "view": {"frame": {"label": "크게", "action": "zoom", "arg": "c1"}}},
                      {"id": "c3", "kind": "chip_row", "title": "칩", "section": "s1", "layout": lay(0, 17, 40, 8),
                       "view": {"frame": {"column": "region"}, "link_to": ["c1"]}}]}


def test_template_from_keeps_shape_and_drops_data_words():
    found = store.template_from(" 내 배치 ", dashboard_body())
    assert found["name"] == "내 배치" and found["pages"] == ["첫째"] and found["unknown"] == []
    assert found["sections"] == [sec("s1", lay(0, 0, 6, 4), view={"page": "첫째"})]
    views = [p["view"] for p in found["pieces"]]
    assert views == [{**DEFAULT_VIEW, "format": "currency", "digits": 2},
                     {**DEFAULT_VIEW, "frame": {"label": "크게", "icon": "", "action": "", "arg": "", "ambient": False}},
                     {**DEFAULT_VIEW, "frame": {"ambient": False}}]
    text = json.dumps({k: v for k, v in found.items() if k != "id"}, ensure_ascii=False)  # id는 무작위 16진수라 "c1"이 우연히 들 수 있다
    assert "amt" not in text and "region" not in text and "sales" not in text and "c1" not in text


def test_template_from_rejects_bad_name_and_empty():
    with pytest.raises(ValueError, match="템플릿 이름"):
        store.template_from("", dashboard_body())
    with pytest.raises(ValueError, match="조각이 1개 이상"):
        store.template_from("빈 것", {"pages": [], "sections": [], "cards": []})


# ---------- read_template·builtin_templates ----------

def test_read_template_sets_id_and_checks():
    raw = json.dumps({**tpl(), "id": "from-file"}, ensure_ascii=False).encode("utf-8")
    assert store.read_template(raw, "given")["id"] == "given"
    assert store.ID.fullmatch(store.read_template(b"\xef\xbb\xbf" + raw, None)["id"])  # UTF-8 BOM도 읽는다


@pytest.mark.parametrize("raw, words", [
    (b"{not json", "JSON으로 읽을 수"),
    (b"\xff\xfe", "JSON으로 읽을 수"),
    (b"[1]", "템플릿은 객체"),
    (b" " * (store.MAX_TEMPLATE_BYTES + 1), "KB까지"),
    (json.dumps({**tpl(), "format": 9}).encode(), "새 버전"),
], ids=["not-json", "not-utf8", "list", "big", "newer"])  # 큰 값이 시험 이름(환경 변수)에 들어가지 않게
def test_read_template_rejects(raw, words):
    with pytest.raises(ValueError, match=words):
        store.read_template(raw, None)


def test_builtin_templates_are_three_files():
    found = store.builtin_templates()
    assert [t["id"] for t in found] == ["goal", "monitor", "overview"]
    assert [len(t["pieces"]) for t in found] == [5, 5, 7] and all(t["unknown"] == [] for t in found)


def test_builtin_template_files_have_no_work_words():
    for path in sorted(store.TEMPLATE_DIR.glob("*" + store.TEMPLATE_SUFFIX)):
        text = path.read_text(encoding="utf-8")
        assert not any(word in text for word in ("신고", "조치", "기한")), path.name
        assert json.loads(text)["format"] == store.TEMPLATE_FORMAT


def test_builtin_templates_need_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "TEMPLATE_DIR", tmp_path / "없음")
    with pytest.raises(ValueError, match="기본 템플릿 폴더"):
        store.builtin_templates()


# ---------- dash_templates(바깥 요청은 바꿔 끼운다) ----------

class Reply:
    """urlopen이 돌려주는 답 흉내: 받은 주소(geturl)와 본문(read)."""

    def __init__(self, body: bytes, url: str = dash_templates.REMOTE_LIST):
        self.body, self.url, self.asked = body, url, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def geturl(self):
        return self.url

    def read(self, n=-1):
        self.asked.append(n)
        return self.body if n < 0 else self.body[:n]


def fake_urlopen(monkeypatch, reply=None, error=None):
    calls = []

    def urlopen(url, timeout=None):
        calls.append((url, timeout))
        if error is not None:
            raise error
        return reply
    monkeypatch.setattr(dash_templates.urllib.request, "urlopen", urlopen)
    return calls


def test_fetch_json_reads_json_with_timeout(monkeypatch):
    calls = fake_urlopen(monkeypatch, Reply(b'{"a": 1}'))
    assert dash_templates.fetch_json(dash_templates.REMOTE_LIST) == {"a": 1}
    assert calls == [(dash_templates.REMOTE_LIST, dash_templates.FETCH_WAIT)]


def test_fetch_json_https_only_without_request(monkeypatch):
    calls = fake_urlopen(monkeypatch, Reply(b"{}"))
    with pytest.raises(ValueError, match="https 주소만"):
        dash_templates.fetch_json("http://example.invalid/templates")
    assert calls == []


@pytest.mark.parametrize("reply, words", [
    (Reply(b"[" + b" " * dash_templates.MAX_REPLY_BYTES + b"]"), "넘어 멈췄어요"),
    (Reply(b"{}", url="http://example.invalid/moved"), "https 주소만"),
    (Reply(b"<html>"), "답을 읽을 수 없어요"),
    (Reply(b"\xff"), "답을 읽을 수 없어요"),
])
def test_fetch_json_rejects(monkeypatch, reply, words):
    fake_urlopen(monkeypatch, reply)
    with pytest.raises(ValueError, match=words):
        dash_templates.fetch_json(dash_templates.REMOTE_LIST)


def test_fetch_json_network_error(monkeypatch):
    fake_urlopen(monkeypatch, error=urllib.error.URLError("막힘"))
    with pytest.raises(ValueError, match="닿지 못했어요"):
        dash_templates.fetch_json(dash_templates.REMOTE_LIST)


def fake_remote(monkeypatch, files: dict[str, object]):
    """fetch_json을 바꿔 끼운다: 목록 주소는 files의 목록, 파일 주소는 그 파일의 contents API 답."""
    calls = []
    listing = [{"type": "file", "name": "README.md", "size": 5}, {"type": "dir", "name": "sub", "size": 0},
               *[{"type": "file", "name": name, "size": len(json.dumps(body))} for name, body in files.items()]]

    def fetch(url):
        calls.append(url)
        if url == dash_templates.REMOTE_LIST:
            return listing
        body = json.dumps(files[url.rsplit("/", 1)[-1]], ensure_ascii=False).encode("utf-8")
        encoded = base64.b64encode(body).decode("ascii")
        return {"type": "file", "encoding": "base64", "content": "\n".join(encoded[i:i + 60] for i in range(0, len(encoded), 60))}
    monkeypatch.setattr(dash_templates, "fetch_json", fetch)
    return calls


def test_remote_list_keeps_json_files_in_name_order(monkeypatch):
    fake_remote(monkeypatch, {"b.json": tpl(), "a.json": tpl()})
    found = dash_templates.remote_list()
    assert [f["file"] for f in found] == ["a.json", "b.json"] and all(type(f["size"]) is int for f in found)


def test_remote_list_rejects_odd_reply(monkeypatch):
    monkeypatch.setattr(dash_templates, "fetch_json", lambda url: {"message": "Not Found"})
    with pytest.raises(ValueError, match="답을 읽을 수 없어요"):
        dash_templates.remote_list()
    many = [{"type": "file", "name": f"t{i}.json", "size": 1} for i in range(dash_templates.MAX_REMOTE_FILES + 1)]
    monkeypatch.setattr(dash_templates, "fetch_json", lambda url: many)
    with pytest.raises(ValueError, match="개를 넘어요"):
        dash_templates.remote_list()


def test_remote_get_decodes_and_checks(monkeypatch):
    calls = fake_remote(monkeypatch, {"a.json": tpl(name="받은 것")})
    found = dash_templates.remote_get("a.json")
    assert found["name"] == "받은 것" and store.ID.fullmatch(found["id"])
    assert calls == [dash_templates.REMOTE_LIST + "/a.json"]


@pytest.mark.parametrize("name", ["../a.json", "a.txt", "a b.json", ""])
def test_remote_get_bad_name_sends_nothing(monkeypatch, name):
    calls = fake_remote(monkeypatch, {})
    with pytest.raises(ValueError, match="파일 이름이 맞지 않아요"):
        dash_templates.remote_get(name)
    assert calls == []


def test_remote_get_rejects_odd_reply(monkeypatch):
    monkeypatch.setattr(dash_templates, "fetch_json", lambda url: {"type": "file", "encoding": "utf-8", "content": "{}"})
    with pytest.raises(ValueError, match="답을 읽을 수 없어요"):
        dash_templates.remote_get("a.json")


# ---------- 라우트 ----------

def test_templates_list_route_has_builtin(client):
    found = client.get("/api/dash/templates").get_json()
    assert found["templates"] == [] and [t["id"] for t in found["builtin"]] == ["goal", "monitor", "overview"]


def test_templates_save_route_from_dashboard(client):
    found = client.post("/api/dash/templates", json={"name": "내 배치", "dashboard": dashboard_body()})
    assert found.status_code == 200
    saved = found.get_json()["templates"]
    assert [t["name"] for t in saved] == ["내 배치"] and len(saved[0]["pieces"]) == 3
    assert client.post("/api/dash/templates", json={"name": "빈 것"}).status_code == 400


def test_templates_import_route_checks_like_download(client):
    found = client.post("/api/dash/templates/import", json={"text": json.dumps(tpl(name="가져온 것"), ensure_ascii=False)})
    assert [t["name"] for t in found.get_json()["templates"]] == ["가져온 것"]
    newer = client.post("/api/dash/templates/import", json={"text": json.dumps({**tpl(name="새 것"), "format": 2})})
    assert newer.status_code == 400 and "새 버전" in newer.get_json()["error"]
    assert client.post("/api/dash/templates/import", json={"text": None}).status_code == 400
    assert [t["name"] for t in client.get("/api/dash/templates").get_json()["templates"]] == ["가져온 것"]


def test_templates_export_route_writes_file(client, monkeypatch, tmp_path):
    saved = client.post("/api/dash/templates/import", json={"text": json.dumps(tpl(name="내보낼 것"), ensure_ascii=False)}).get_json()["templates"][0]
    asked = []
    monkeypatch.setattr(server, "ask_save", lambda name, kinds: asked.append((name, kinds)) or str(tmp_path / "out"))
    found = client.post("/api/dash/templates/export", json={"id": saved["id"]}).get_json()
    assert found["path"].endswith("out.json") and asked == [("내보낼 것.json", ("템플릿 (*.json)",))]
    written = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert list(written) == list(store.TEMPLATE_FILE_KEYS)
    assert store.read_template((tmp_path / "out.json").read_bytes(), None)["name"] == "내보낼 것"
    monkeypatch.setattr(server, "ask_save", lambda name, kinds: None)
    assert client.post("/api/dash/templates/export", json={"id": saved["id"]}).get_json() == {"path": None}
    assert client.post("/api/dash/templates/export", json={"id": "없는것"}).status_code == 400
    monkeypatch.setattr(server, "ask_save", None)
    gone = client.post("/api/dash/templates/export", json={"id": saved["id"]})
    assert gone.status_code == 400 and "파일 창" in gone.get_json()["error"]


def test_templates_remote_route(client, monkeypatch):
    fake_remote(monkeypatch, {"a.json": tpl()})
    assert client.post("/api/dash/templates/remote", json={}).get_json()["files"][0]["file"] == "a.json"


def test_templates_fetch_route_checks_all_before_saving(client, monkeypatch):
    fake_remote(monkeypatch, {"a.json": tpl(name="받은 것"), "bad.json": {**tpl(name="깨진 것"), "format": 2}})
    both = client.post("/api/dash/templates/fetch", json={"files": ["a.json", "bad.json"]})
    assert both.status_code == 400 and client.get("/api/dash/templates").get_json()["templates"] == []
    one = client.post("/api/dash/templates/fetch", json={"files": ["a.json"]})
    assert [t["name"] for t in one.get_json()["templates"]] == ["받은 것"]
    assert client.post("/api/dash/templates/fetch", json={"files": []}).status_code == 400
    assert client.post("/api/dash/templates/fetch", json={"files": "a.json"}).status_code == 400


# ---------- (B8) 더 받기 전용 폴더 ----------

MORE_DIR = store.TEMPLATE_DIR.parent / dash_templates.REMOTE_LIST.rsplit("/", 1)[1]


def test_more_folder_is_not_the_builtin_folder():
    assert MORE_DIR != store.TEMPLATE_DIR and MORE_DIR.is_dir()


def test_more_templates_pass_the_file_check_without_work_words():
    paths = sorted(MORE_DIR.glob("*" + store.TEMPLATE_SUFFIX))
    assert paths
    for path in paths:
        raw = path.read_bytes()
        assert not any(word in raw.decode("utf-8") for word in ("신고", "조치", "기한")), path.name
        assert store.read_template(raw, path.stem)["unknown"] == [], path.name


def test_more_templates_make_a_dashboard_that_saves():
    for path in sorted(MORE_DIR.glob("*" + store.TEMPLATE_SUFFIX)):
        t = store.read_template(path.read_bytes(), path.stem)
        cards = [{"id": f"c{i}", "kind": p["kind"], "title": p["title"], "section": p["section"], "layout": p["layout"], "colors": p["colors"], "view": p["view"], "source": {}, "data": {}}
                 for i, p in enumerate(t["pieces"])]
        saved = store.check_dashboard({"name": t["name"], "pages": t["pages"], "sections": t["sections"], "cards": cards})
        assert saved["pages"] == t["pages"] and len(saved["cards"]) == len(cards), path.name
