"""Phase 17(plans/phase-17.md): 조건 값의 천 단위 쉼표, 문자 검색 특수문자 무시 설정, 값 목록 전체 찾기(q), 차트 범위 묶기를 서버의 고유 개수로.
데이터는 시험 안에서 지어낸다. 새 함수가 스켈레톤이면 실패하는 것이 정상이다.
기존 함수 수정((수정) 절)이 끝나야 통과하는 시험은 xfail(strict=True, reason="phase-17 <항목>")로 표시했다. 그 항목을 끝낸 쪽이 표시를 지운다."""
import json
import shutil
import subprocess

import duckdb
import polars as pl
import pytest
from lazy import ibis

from conftest import ROOT
from core import dash_store
from core import parse
from core import result
from core import settings
from core import source
from dashboard import dash_calc

STATIC = ROOT / "static"


def marks_on(monkeypatch):
    """설정 ignore_marks를 켠 것처럼(settings.get만 바꾼다. 설정 파일·CHOICES와 상관없이)."""
    real = settings.get
    monkeypatch.setattr(settings, "get", lambda name: "on" if name == parse.MARKS_SETTING else real(name))


def count(t, line):
    return t.filter(parse.parse_condition(line, t)).count().execute()


# ---------- 17A 숫자 값의 천 단위 쉼표(parse.convert) ----------

def test_convert_reads_grouped_numbers():
    assert parse.convert(ibis.dtype("int64"), "1,138,757") == 1138757
    assert parse.convert(ibis.dtype("int64"), " -1,000 ") == -1000
    assert parse.convert(ibis.dtype("float64"), "1,234.5") == 1234.5
    assert parse.convert(ibis.dtype("int64"), "1000") == 1000  # 쉼표 없는 값은 그대로
    for dtype, text in [("int64", "1,5"), ("float64", "1,5"), ("int64", "1,0000"), ("int64", "12,34,567"), ("int64", "1 000")]:  # 천 단위 모양이 아니면 거부(1,5를 15로 읽지 않는다)
        with pytest.raises(ValueError, match="값이 아니에요"):
            parse.convert(ibis.dtype(dtype), text)
    with pytest.raises(ValueError, match="값이 아니에요"):
        parse.convert(ibis.dtype("date"), "2024,01")  # 날짜는 쉼표를 빼지 않는다


def test_convert_grouped_decimal_and_text_untouched():
    t = ibis.memtable(pl.DataFrame({"d": ["1234567.25"], "s": ["1,000"]}).to_arrow()).mutate(d=ibis._.d.cast("decimal(18, 2)"))
    assert count(t, "d = 1,234,567.25") == 1
    assert count(t, "s = 1,000") == 1  # 문자 컬럼은 쉼표를 그대로 둔다


def test_grouped_numbers_in_all_condition_forms():
    t = ibis.memtable({"user_id": [1138757, 2000, 5]})
    assert count(t, "user_id = 1,138,757") == 1
    assert count(t, "user_id between 1,000 2,000") == 1
    assert count(t, "user_id in '1,138,757', '2,000'") == 2  # in은 쉼표로 값을 나누니 따옴표로 감싼다
    assert count(t, "user_id >= 2,000") == 2


# ---------- 17B 특수문자 무시(parse.strip_marks·unmarked·like_one) ----------

def test_strip_marks():
    assert parse.strip_marks("a-b_c.d") == "abcd"
    assert parse.strip_marks("가.나 (다)!") == "가나 다"
    assert parse.strip_marks("x１²") == "x１²"  # 글자·숫자(전각·윗첨자 포함)는 남긴다
    for text in ["%%", "-_-", ""]:
        with pytest.raises(ValueError, match="찾을 글자가 없어요"):
            parse.strip_marks(text)


def test_unmarked_on_duckdb():
    t = ibis.memtable({"s": ["a-b", "가.나", "x y!", None]})
    got = t.select(v=parse.unmarked(t, t.s)).to_pyarrow()["v"].to_pylist()
    assert got == ["ab", "가나", "x y", None]


def test_unmarked_refuses_when_connection_cannot(monkeypatch):
    t = ibis.memtable({"s": ["a"]})
    monkeypatch.setattr(source, "compiles", lambda expr: False)
    with pytest.raises(ValueError, match="특수문자를 빼고 찾을 수 없어요"):
        parse.unmarked(t, t.s)


def test_like_ignores_marks_when_setting_on(monkeypatch):
    t = ibis.memtable({"s": ["a-b", "AB", "a.c", None]})
    assert count(t, "s like ab") == 1  # 끈 때: 그대로
    marks_on(monkeypatch)
    assert count(t, "s like ab") == 2  # a-b, AB
    assert count(t, "s like a-b") == 2  # 검색어 쪽도 지운다
    assert count(t, "s not like ab") == 2  # a.c와 빈 값
    assert count(t, "s starts a-") == 1  # starts·ends는 바꾸지 않는다(a-b만)
    with pytest.raises(ValueError, match="찾을 글자가 없어요"):
        count(t, "s like %%")


def test_ignore_marks_setting(folder):
    assert settings.CHOICES[parse.MARKS_SETTING] == ["on", "off"] and settings.get(parse.MARKS_SETTING) == "off"
    settings.save({parse.MARKS_SETTING: "on"})
    assert settings.get(parse.MARKS_SETTING) == "on"


# ---------- 17C 값 목록 전체 찾기(result.find_values) ----------

def table():
    return ibis.memtable(pl.DataFrame({
        "name": [f"v{i}" for i in range(1, 301)] + ["Hello-World", "50% off", "500", None],
        "amount": [i * 1000 for i in range(1, 301)] + [1138757, 1, 2, 3],
        "day": pl.date_range(pl.date(2024, 1, 1), pl.date(2024, 10, 30), eager=True)[:304],
    }).to_arrow())


def test_find_values_searches_whole_table(monkeypatch):
    monkeypatch.setattr(result, "VALUE_SCAN", 5)  # 앞 행 제한을 쓰지 않는다
    assert result.find_values(table(), "name", "v29") == (["v29"] + [f"v29{i}" for i in range(10)], False)
    assert result.find_values(table(), "name", "hello") == (["Hello-World"], False)  # 대소문자 무시
    assert result.find_values(table(), "name", "50%") == (["50% off"], False)  # %는 글자 그대로(500은 아님)
    assert result.find_values(table(), "name", "없는값") == ([], False)


def test_find_values_more_and_types(monkeypatch):
    monkeypatch.setattr(result, "MAX_VALUES", 3)
    found, more = result.find_values(table(), "name", "v1")
    assert found == ["v1", "v10", "v100"] and more is True  # 글자 차례, MAX_VALUES개까지
    amounts, _ = result.find_values(table(), "amount", "1,138")  # 숫자 컬럼은 검색어의 쉼표를 뺀다
    assert "1138757" in amounts and all("1138" in v for v in amounts)
    days, more = result.find_values(table(), "day", "2024-02-1")
    assert days == [f"2024-02-1{i}" for i in range(3)] and more is True


def test_find_values_rejects_bad_column():
    with pytest.raises(ValueError):
        result.find_values(table(), "nope", "a")
    nested = ibis.memtable({"s": [{"a": 1}]})
    with pytest.raises(ValueError, match="구조"):
        result.find_values(nested, "s", "1")


def test_find_values_ignores_marks(monkeypatch):
    marks_on(monkeypatch)
    assert result.find_values(table(), "name", "helloworld") == (["Hello-World"], False)


# ---------- 라우트(/api/values q, /api/chart merge_ranges) ----------

@pytest.fixture
def searching(folder, tmp_path):
    from core import server
    from query.search_app import app
    raw = duckdb.connect(str(tmp_path / "shop.duckdb"))
    raw.execute("create table t as select i as id, 'v' || i as name, i * 1000 as amount, date '2024-01-01' + i::int as day, i % 3 + 1 as cust, 'o' as kind from range(1, 301) r(i)")
    raw.close()
    c = app.test_client()
    c.get("/?key=" + server.KEY)
    assert c.post("/api/connect", json={"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / "shop.duckdb")}).status_code == 200
    yield lambda api, **body: c.post(api, json=body).get_json()
    c.post("/api/disconnect", json={})


def test_values_route_with_q(searching):
    first = searching("/api/values", base="t", column="name")
    assert len(first["values"]) == result.MAX_VALUES and first["more"] is True  # q가 없으면 지금처럼
    got = searching("/api/values", base="t", column="name", q=" v29 ")
    assert got == {"values": ["v29"] + [f"v29{i}" for i in range(10)], "more": False, "partial": False}
    assert searching("/api/values", base="t", column="name", q="   ")["values"] == first["values"]  # 공백뿐이면 q 없음
    assert "error" in searching("/api/values", base="t", column="name", q=5)


def test_chart_route_merges_on_server_for_any_agg(searching):
    got = searching("/api/chart", base="t", x="cust", series=[{"y": "amount", "agg": "sum"}], merge_ranges=[[1, 2], [3, None]])
    assert got["merged"] is True and got["labels"] == ["1~2", "3~"]


# ---------- 17D 범위 묶기는 고유 개수(dash_calc·dash_store) ----------

def test_merges_on_server_for_every_agg():
    chart = lambda aggs, merge=True: {"kind": "chart", "view": {"groups": {"ranges": [[1, 2]], **({"merge": True} if merge else {})}},
                                     "source": {"body": {"series": [{"y": "u", "agg": a} for a in aggs]}}}
    assert dash_calc.merges_on_server(chart(["sum", "count"])) is True
    assert dash_calc.merges_on_server(chart(["mean"])) is True
    assert dash_calc.merges_on_server(chart(["sum"], merge=False)) is False
    assert dash_calc.merges_on_server({**chart(["sum"]), "kind": "histogram"}) is False


def test_range_data_counts_unique_values(monkeypatch):
    t = ibis.memtable(pl.DataFrame({"n": [1, 2, 2, 3, 10, 12, None], "u": ["a", "a", "b", None, "d", "d", "z"], "v": [5, 5, 7, 1, 2, 2, 9]}).to_arrow())
    from core import querying
    monkeypatch.setattr(querying, "runnable", lambda body, **kw: t)
    monkeypatch.setattr(querying, "build", lambda body, **kw: t)
    series = [{"y": "v", "agg": "sum"}, {"y": "u", "agg": "max"}, {"y": "", "agg": "count"}, {"y": "v", "agg": "mean"}]
    card = {"kind": "chart", "view": {"groups": {"ranges": [[1, 2], [3, 3], [10, None]], "merge": True}}, "source": {"body": {"base": "t", "x": "n", "series": series}}}
    got = dash_calc.range_data(card)["values"]
    assert got["labels"] == ["1~2", "3~3", "10~"] and got["merged"] is True
    assert [s["values"] for s in got["series"]] == [[2, 1, 1], [2, 0, 1], [3, 1, 2], [2, 1, 1]]  # 고른 집계는 쓰지 않는다. 빈 값은 세지 않고 X가 빈 행·범위 밖 행은 빠진다
    assert [s["name"] for s in got["series"]] == [dash_calc.series_name(s) for s in dash_calc.clean_series(series)]  # 이름은 그대로(화면이 계열을 이름으로 맞춘다)
    with pytest.raises(ValueError, match="나누기"):
        dash_calc.range_data({**card, "source": {"body": {**card["source"]["body"], "split": "u"}}})


def card(view, body):
    return {"kind": "chart", "title": "c", "view": {"type": "", "unit": "day", **view}, "source": {"body": body}}


def test_check_card_merges_any_agg_but_not_split():
    mean = {"base": "t", "x": "x", "series": [{"y": "v", "agg": "mean"}]}
    assert dash_store.check_card(card({"groups": {"ranges": [[1, 5]], "merge": True}}, mean))["view"]["groups"]["merge"] is True
    with pytest.raises(ValueError, match="나누기"):
        dash_store.check_card(card({"groups": {"ranges": [[1, 5]], "merge": True}}, {**mean, "split": "g"}))
    assert not hasattr(dash_store, "MERGE_AGGS") and not hasattr(dash_store, "SERVER_MERGE")


def test_dash_constants_send_merge_note(folder):
    from dashboard import dash_app
    found = {}
    dash_app.dash_constants(found)
    assert found["dash"]["merge_note"] == dash_store.MERGE_NOTE and "server_merge" not in found["dash"]


# ---------- 화면(정적 검사·node) ----------

def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_find_as_typed_waits_enter_and_drops_late(tmp_path):
    common = text("static/core/common.js")
    part = common[common.index("const TYPE_WAIT"):common.index("// 우클릭 메뉴(")]
    body = r"""
const sleep = ms => new Promise(r => setTimeout(r, ms));
const box = { value: "", on: {}, addEventListener(n, f) { (this.on[n] ??= []).push(f); } };
const fire = (n, e = {}) => (box.on[n] ?? []).forEach(f => f({ isComposing: false, preventDefault() {}, ...e }));
const asked = [], shown = [];
const delays = { a: 0, ab: 200, abc: 10 };
findAsTyped(box, q => { asked.push(q); return new Promise(r => setTimeout(() => r("got " + q), delays[q] ?? 0)); }, (d, e) => shown.push(e ? "error " + e.message : d));
(async () => {
  box.value = "a"; fire("input"); await sleep(100);
  box.value = "ab"; fire("input");
  const early = asked.length;           // 300ms 안에 이어진 입력: 아직 묻지 않는다
  await sleep(TYPE_WAIT + 50);           // 마지막 입력 뒤 300ms → ab 하나만
  const afterWait = [...asked];
  box.value = "abc"; fire("keydown", { key: "Enter" });
  const afterEnter = [...asked];         // Enter는 바로
  box.value = "abcd"; fire("keydown", { key: "Enter", isComposing: true });
  await sleep(400);
  console.log(JSON.stringify({ early, afterWait, afterEnter, asked, shown }));
})();
"""
    script = tmp_path / "t.js"
    script.write_text(part + "\n" + body, encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    got = json.loads(done.stdout.strip().splitlines()[-1])
    assert got["early"] == 0 and got["afterWait"] == ["ab"] and got["afterEnter"] == ["ab", "abc"]
    assert got["asked"] == ["ab", "abc"]  # 조합 중 Enter는 무시
    assert got["shown"] == ["got abc"]  # 늦게 온 ab 응답은 버린다


def test_ignore_marks_checkbox():
    assert 'id="ignore-marks"' in text("static/core/status.html")
    common = text("static/core/common.js")
    assert '$("ignore-marks").onchange = () => saveSetting({ ignore_marks:' in common


def test_value_list_asks_server_when_find_flag():
    menu = text("static/core/prepmenu.js")
    part = menu[menu.index("function valueList("):menu.index("function valuesBox(")]
    assert "findAsTyped(find" in part and "first?.find" in part
    assert "values: q => values?.(col.value, q)" in text("static/core/newcol.js")


def test_builder_lists_values_for_every_type():
    page = text("static/query/index.html")
    assert '$("blist").classList.toggle("hidden", byColumn() || op.endsWith("null"));' in page
    assert 'id="bfind"' in page and 'findAsTyped($("bfind")' in page


def test_col_values_sends_q():
    page = text("static/query/index.html")
    assert 'const colValues = (column, q = "") =>' in page and "values = q => colValues(col, q)" in page


def test_query_chart_merge_always_on_server():
    page = text("static/query/index.html")
    assert "server_merge" not in page and "SERVER.dash.merge_note" in page


def test_group_picker_merge_always_on_server():
    picker = text("web/components/edit/panel/GroupPicker.tsx")
    assert "server_merge" not in picker and "merge_note" in picker
    assert "server_merge" not in text("web/lib/types.ts")
