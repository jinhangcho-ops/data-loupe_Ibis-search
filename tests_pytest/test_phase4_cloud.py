"""Phase 4(plans/phase-4.md) B·C: 워드클라우드 두 방식(라벨 이름·글 속 단어)과 카드의 표시할 그룹 고르기(view.groups) 저장 검사.
형태소 분석기는 가짜(tokenize만 있는 객체)로 바꿔 끼워 kiwipiepy 없이 돈다. 데이터는 시험 안에서 지어낸다.
새 함수가 스켈레톤이면 실패하는 것이 정상이다. 기존 함수 수정((수정) 절)이 끝나야 통과하는 시험은 xfail(strict=True, reason="phase-4 <항목>")로 표시했다."""
import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import polars as pl
import pytest
from lazy import ibis

from core import dash_store
from core import labels
from core import settings
from dashboard import cloud_words
from dashboard import dash_calc

STORE = {"source": "folder:c:/data", "schema": "", "table": "t", "key": "id", "key_type": "int", "text": "txt", "vocab": ["긍정", "부정"],
         "keys": [1, 2, 3], "tags": [["긍정"], ["부정"], ["긍정", "부정"]], "rev": 1}  # 본 파일 모양
TEXTS = {"id": [1, 2, 3, 4], "txt": ["배송 빠름", "배송 늦음", "", "가격 좋음"]}
TOKENS = {"배송 빠름": [("배송", "NNG"), ("빠르", "VA"), ("ㅁ", "ETN")], "배송 늦음": [("배송", "NNG"), ("늦", "VA"), ("음", "ETN")],
          "가격 좋음": [("가격", "NNG"), ("좋", "VA"), ("음", "ETN")], "Apple 폰": [("Apple", "SL"), ("폰", "NNG")]}


class FakeKiwi:
    """kiwipiepy.Kiwi의 tokenize(글 목록) → 글마다 토큰(form, tag) 목록만 흉내 낸다."""

    def tokenize(self, texts):
        return [[types.SimpleNamespace(form=f, tag=t) for f, t in TOKENS[s]] for s in texts]


@pytest.fixture
def fake_kiwi(monkeypatch):
    monkeypatch.setattr(cloud_words, "tokenizer", lambda: FakeKiwi())


@pytest.fixture
def table():
    return ibis.memtable(pl.DataFrame(TEXTS).to_arrow())


# ---------- 글 속 단어 ----------
def test_kiwipiepy_not_loaded_at_start():
    code = "import sys; from dashboard import dash_calc, cloud_words; print('kiwipiepy' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(Path(__file__).parents[1]))
    assert out.stdout.strip() == "False"  # 모듈을 올려도 kiwipiepy는 (나)를 쓸 때까지 올라오지 않는다


def test_tokenizer_missing_package(monkeypatch):
    monkeypatch.setattr(cloud_words, "KIWI", {})
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)  # tokenizer는 importlib.util.find_spec("kiwipiepy")로 설치를 본다
    with pytest.raises(ValueError, match="kiwipiepy"):
        cloud_words.tokenizer()


def test_count_words(fake_kiwi):
    got = cloud_words.count_words(["배송 빠름", "배송 늦음", "가격 좋음"], [], [], cloud_words.MIN_LEN)
    assert got.columns == ["w", "n"] and got.rows() == [("배송", 2), ("가격", 1)]  # 기본 품사는 명사, 많은 순·같으면 글자 순
    got = cloud_words.count_words(["배송 빠름", "배송 늦음", "가격 좋음"], ["noun", "adj"], ["배송"], 1)
    assert got.rows() == [("가격", 1), ("늦다", 1), ("빠르다", 1), ("좋다", 1)]  # 형용사는 줄기 + 다, 불용어는 뺀다
    assert cloud_words.count_words(["Apple 폰"], ["noun"], [], 2).rows() == []  # 한 글자 '폰'은 최소 길이 2에 걸린다
    assert cloud_words.count_words(["Apple 폰"], ["foreign"], [], 2).rows() == [("Apple", 1)]
    assert cloud_words.count_words([], ["noun"], [], 2).rows() == []


def test_texts_of_limits(table, monkeypatch):
    assert sorted(cloud_words.texts_of(table, "txt")) == ["가격 좋음", "배송 늦음", "배송 빠름"]  # 빈 글자·빈 값은 뺀다
    with pytest.raises(ValueError, match="글자 컬럼"):
        cloud_words.texts_of(table, "id")
    monkeypatch.setitem(settings.DEFAULTS, cloud_words.ROWS_SETTING, 2)  # 저장된 설정이 없으면 기본값(settings.get)
    with pytest.raises(ValueError, match="넘어요"):
        cloud_words.texts_of(table, "txt")
    monkeypatch.setitem(settings.DEFAULTS, cloud_words.ROWS_SETTING, 3)
    monkeypatch.setattr(cloud_words, "CHARS_PER_ROW", 1)  # 길이 상한 = 3 × 1자
    with pytest.raises(ValueError, match="길이"):
        cloud_words.texts_of(table, "txt")


def test_cloud_rows_setting(folder):
    assert settings.SIZES["cloud_rows"] == (1_000, 2_000_000) and settings.DEFAULTS["cloud_rows"] == 100_000
    settings.save({"cloud_rows": 5_000})
    assert settings.get("cloud_rows") == 5_000
    with pytest.raises(ValueError):
        settings.save({"cloud_rows": 999})


def test_word_cloud(table, fake_kiwi):
    got = cloud_words.word_cloud(table, "txt", {"mode": "words", "pos": ["noun"]}, 1)
    assert got == {"values": {"words": [{"w": "배송", "n": 2}]}, "note": "단어가 많아 많이 나온 1개만 표시해요."}
    assert cloud_words.word_cloud(table, "txt", {"mode": "words"}, 10)["note"] == ""


def test_wordcloud_data_modes(table, fake_kiwi, monkeypatch):
    store = labels.from_json(STORE)  # 본 파일 모양 → 메모리 모양(쌍 표)
    monkeypatch.setattr(labels, "store_of", lambda body: store)
    body = {"base": "t", "x": "txt", "labels": {}}
    assert dash_calc.wordcloud_data(table, "txt", {"mode": "labels"}, body)["values"]["words"] == [{"w": "긍정", "n": 2}, {"w": "부정", "n": 2}]
    assert dash_calc.wordcloud_data(table, "txt", {"mode": "labels", "tags": ["부정"]}, body)["values"]["words"] == [{"w": "부정", "n": 2}]
    words = dash_calc.wordcloud_data(table, "txt", {"mode": "words", "rows": "tagged", "match": "any", "tags": ["부정"], "text": "txt"}, body)["values"]["words"]
    assert words == [{"w": "배송", "n": 1}]  # 부정이 달린 행(2, 3) 중 글이 있는 행은 2뿐
    every = dash_calc.wordcloud_data(table, "txt", {"mode": "words", "rows": "tagged", "match": "all", "tags": ["긍정", "부정"], "text": "txt"}, body)
    assert every["values"]["words"] == []  # 둘 다 달린 행은 3뿐이고 글이 비었다
    whole = dash_calc.wordcloud_data(table, "txt", {"mode": "words", "rows": "all", "text": "txt"}, {"base": "t", "x": "txt"})["values"]["words"]
    assert whole == [{"w": "배송", "n": 2}, {"w": "가격", "n": 1}]  # 전체 행: 태그를 켜지 않은 조회도 된다
    assert len(dash_calc.wordcloud_data(table, "txt")["values"]["words"]) == 3  # 예전 방식(컬럼 값별 행 수, 빈 글자 제외)은 그대로
    assert len(dash_calc.wordcloud_data(table, "txt", {"mode": "values"}, body)["values"]["words"]) == 3


def test_definition_includes_cloud():
    card = {"kind": "wordcloud", "source": {"body": {"base": "t", "x": "txt"}}, "view": {"type": "", "unit": "day", "cloud": {"mode": "labels"}, "groups": {"keep": ["a"]}}}
    found = dash_calc.definition(card)
    assert found["view"] == {"unit": "day", "cloud": {"mode": "labels"}}  # 그룹 고르기는 보기라 다시 구하지 않는다


# ---------- 범위 묶기(Phase 17: 늘 서버) ----------
def test_range_label_and_merges_on_server():
    assert [dash_calc.range_label(1, 5), dash_calc.range_label(10, None), dash_calc.range_label(None, 2.5), dash_calc.range_label(1.0, 2)] == ["1~5", "10~", "~2.5", "1~2"]  # JS String과 같게
    chart = lambda aggs, merge=True: {"kind": "chart", "view": {"groups": {"ranges": [[1, 2]], **({"merge": True} if merge else {})}},
                                     "source": {"body": {"series": [{"y": "u", "agg": a} for a in aggs]}}}
    assert dash_calc.merges_on_server(chart(["count", "count_distinct"])) is True
    assert dash_calc.merges_on_server(chart(["nunique"])) is True  # 예전 이름
    assert dash_calc.merges_on_server(chart(["sum", "count"])) is True  # (Phase 17) 집계와 상관없이 서버
    assert dash_calc.merges_on_server(chart(["count_distinct"], merge=False)) is False
    assert dash_calc.merges_on_server({**chart(["count_distinct"]), "kind": "histogram"}) is False


def test_range_data(monkeypatch):
    t = ibis.memtable(pl.DataFrame({"n": [1, 2, 2, 3, 10, 12], "u": ["a", "a", "b", "c", "d", "d"]}).to_arrow())
    from core import querying
    monkeypatch.setattr(querying, "runnable", lambda body, **kw: t)
    monkeypatch.setattr(querying, "build", lambda body, **kw: t)
    card = {"kind": "chart", "view": {"groups": {"ranges": [[1, 2], [10, None]], "merge": True}},
            "source": {"body": {"base": "t", "x": "n", "series": [{"y": "u", "agg": "count_distinct"}, {"y": "", "agg": "count"}]}}}
    got = dash_calc.range_data(card)
    assert got["values"] == {"labels": ["1~2", "10~"], "series": [{"name": "고유 개수 (u)", "values": [2, 1]}, {"name": "개수", "values": [3, 2]}], "merged": True}
    assert got["x_kind"] == "num"
    with pytest.raises(ValueError, match="숫자"):
        dash_calc.range_data({**card, "source": {"body": {**card["source"]["body"], "x": "u"}}})


# ---------- 저장 검사 ----------
def test_check_cloud():
    assert dash_store.check_cloud({"mode": "values"}) == {"mode": "values"}
    assert dash_store.check_cloud({}) == {"mode": "values"}
    assert dash_store.check_cloud({"mode": "labels", "tags": ["긍정", "긍정"]}) == {"mode": "labels", "tags": ["긍정"]}
    assert dash_store.check_cloud({"mode": "words", "rows": "all", "text": "txt", "pos": ["noun", "adj", "noun"], "stop": [" 배송 "], "min_len": 1}) == \
        {"mode": "words", "rows": "all", "text": "txt", "pos": ["noun", "adj"], "stop": ["배송"], "min_len": 1}
    assert dash_store.check_cloud({"mode": "words", "rows": "tagged", "match": "all", "tags": ["긍정"], "text": "txt"}) == \
        {"mode": "words", "rows": "tagged", "match": "all", "tags": ["긍정"], "text": "txt"}
    w = {"mode": "words", "text": "t"}
    bad = [([], "객체"), ({"mode": "x"}, "방식"), ({"mode": "words", "rows": "all"}, "글 컬럼"), (w, "뽑을 행"), ({**w, "rows": "some"}, "뽑을 행"),
           ({**w, "rows": "tagged", "match": "any"}, "태그를 하나 이상"), ({**w, "rows": "tagged", "tags": ["a"]}, "하나라도"),
           ({**w, "rows": "all", "tags": ["a"]}, "전체 행"), ({**w, "rows": "all", "pos": ["verbs"]}, "품사"),
           ({**w, "rows": "all", "min_len": 0}, "최소 글자"), ({**w, "rows": "all", "min_len": True}, "최소 글자"),
           ({**w, "rows": "all", "stop": ["가" * (dash_store.MAX_STOP_CHARS + 1)]}, "불용어"), ({"mode": "values", "tags": ["a"]}, "컬럼 값 방식"),
           ({"mode": "labels", "rows": "all"}, "라벨 이름 방식"), ({"mode": "labels", "color": "#fff"}, "쓸 수 없는"), ({"mode": "labels", "tags": "a"}, "태그"),
           ({"mode": "labels", "tags": [" a"]}, "앞뒤")]
    for cloud, words in bad:
        with pytest.raises(ValueError, match=words):
            dash_store.check_cloud(cloud)


def test_check_groups():
    assert dash_store.check_groups({}) == {}
    assert dash_store.check_groups({"keep": ["a", "b", "a"], "series": [], "merge": False}) == {"keep": ["a", "b"]}
    assert dash_store.check_groups({"ranges": [[1, 5], [10, None]], "merge": True, "rename": {"1~5": "적음"}}) == {"ranges": [[1, 5], [10, None]], "merge": True, "rename": {"1~5": "적음"}}
    assert dash_store.check_groups({"ranges": [["2026-01-01", "2026-01-31"]]}) == {"ranges": [["2026-01-01", "2026-01-31"]]}
    assert dash_store.check_groups({"ranges": [[1, 5], [3, 8]]}) == {"ranges": [[1, 5], [3, 8]]}  # 보이기만은 겹쳐도 된다
    assert dash_store.check_groups({"sets": {"수도권": ["서울", "경기", "서울"]}}) == {"sets": {"수도권": ["서울", "경기"]}}
    bad = [([], "객체"), ({"hide": ["a"]}, "쓸 수 없는"), ({"keep": "a"}, "keep"), ({"keep": [""]}, "그룹 이름"),
           ({"ranges": [[1, 5], [5, 8]], "merge": True}, "겹치지"), ({"sets": {"a": []}}, "영역 묶음"), ({"sets": {"a": ["x"], "b": ["x"]}}, "두 묶음"),
           ({"sets": ["a"]}, "영역 묶음"),
           ({"ranges": [[5, 1]]}, "범위"), ({"ranges": [[None, None]]}, "범위"), ({"ranges": [[1, "2026-01-01"]]}, "범위"), ({"ranges": [[True, 2]]}, "범위"),
           ({"ranges": [["2026/01/01", None]]}, "범위"), ({"ranges": [[1, 2]] * (dash_store.MAX_RANGES + 1)}, "범위"), ({"merge": True}, "범위를 하나 이상"),
           ({"merge": "yes", "ranges": [[1, 2]]}, "merge"), ({"rename": {"a": ""}}, "이름 바꾸기"), ({"rename": ["a"]}, "이름 바꾸기"),
           ({"rename": {"a": "가" * (dash_store.MAX_SHOWN_NAME + 1)}}, "이름 바꾸기")]
    for groups, words in bad:
        with pytest.raises(ValueError, match=words):
            dash_store.check_groups(groups)


def card(kind, view, body=None):
    return {"kind": kind, "title": "c", "view": {"type": "", "unit": "day", **view}, "source": {"body": body or {"base": "t", "x": "x", "series": [{"y": "", "agg": "count"}]}}}


def test_view_keeps_groups_and_cloud():
    view = dash_store.check_view({"groups": {"keep": ["a"], "rename": {"a": "에이"}}, "cloud": {"mode": "labels"}})
    assert view["groups"] == {"keep": ["a"], "rename": {"a": "에이"}} and view["cloud"] == {"mode": "labels"}
    assert "groups" not in dash_store.check_view({"groups": {}})  # 빈 고르기는 저장하지 않는다


def test_check_card_group_rules():
    assert dash_store.check_card(card("chart", {"groups": {"ranges": [[1, 5]], "merge": True}}))["view"]["groups"]["merge"] is True
    assert dash_store.check_card(card("calheat", {"groups": {"ranges": [["2026-01-01", None]]}}))["view"]["groups"]
    assert dash_store.check_card(card("wordcloud", {"cloud": {"mode": "labels"}}, {"base": "t", "x": "x", "labels": {}}))["view"]["cloud"] == {"mode": "labels"}
    assert dash_store.check_card(card("wordcloud", {"cloud": {"mode": "words", "rows": "all", "text": "x"}}))["view"]["cloud"]["rows"] == "all"  # 전체 행은 태그 없이
    distinct = {"base": "t", "x": "x", "series": [{"y": "u", "agg": "count_distinct"}, {"y": "v", "agg": "sum"}]}
    assert dash_store.check_card(card("chart", {"groups": {"ranges": [[1, 5]], "merge": True}}, distinct))["view"]["groups"]["merge"] is True
    geo = {"geo": {"regions": {"table": "r"}, "name": "n", "agg": "sum", "value": "v", "lat": "la", "lon": "lo"}}
    assert dash_store.check_card(card("geomap", {**geo, "groups": {"sets": {"수도권": ["서울", "경기"]}, "rename": {"서울": "서울시"}}}))["view"]["groups"]["sets"]
    bad = [(card("kpi", {"groups": {"keep": ["a"]}}), "그룹을 고를 수 없어요"),
           (card("table", {"groups": {"keep": ["a"]}}), "그룹을 고를 수 없어요"),
           (card("chart", {"groups": {"ranges": [[1, 5]], "merge": True}}, {**distinct, "split": "g"}), "나누기"),
           (card("chart", {"groups": {"sets": {"a": ["x"]}}}), "지도 카드에서만"),
           (card("geomap", {"geo": {**geo["geo"], "agg": "mean"}, "groups": {"sets": {"a": ["x"]}}}), "합계·개수"),
           (card("wordcloud", {"cloud": {"mode": "words", "rows": "tagged", "match": "any", "tags": ["a"], "text": "x"}}), "태그를 켠 조회"),
           (card("wordcloud", {"groups": {"ranges": [[1, 2]]}}), "범위"),
           (card("histogram", {"groups": {"ranges": [["2026-01-01", None]]}}), "범위는"),
           (card("histogram", {"groups": {"ranges": [[1, 2]], "merge": True}}), "차트 카드에서만"),
           (card("chart", {"cloud": {"mode": "values"}}), "워드클라우드 카드에만"),
           (card("wordcloud", {"cloud": {"mode": "labels"}}), "태그를 켠 조회")]
    for c, words in bad:
        with pytest.raises(ValueError, match=words):
            dash_store.check_card(c)
