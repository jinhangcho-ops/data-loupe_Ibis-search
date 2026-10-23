"""목표 파일: 형식별 읽기(xlsx·csv·CP949·yaml·json·md·txt), 값 글자, 줄 번호가 든 오류, 겹침, 찾기·모호함, 수정 시각·크기 캐시.
파일은 모두 tmp_path 안에서 만든다."""
import os

import pytest
import xlsxwriter

from core import targets


def write(tmp_path, name, text, encoding="utf-8"):
    path = tmp_path / name
    path.write_bytes(text.encode(encoding))
    return path


def by_name(found):
    return {r["name"]: r for r in found["rows"]}


def row(name, value, period="", column="", scope="", line=1):
    return {"name": name, "value": value, "period": period, "column": column, "scope": scope, "line": line}


@pytest.fixture(autouse=True)
def fresh_cache():
    targets.clear()
    yield
    targets.clear()


# ---------- 값 ----------

@pytest.mark.parametrize("text,want", [
    ("5000000", 5_000_000), ("₩5,000,000", 5_000_000), ("  ₩ 5,000,000 원 ", 5_000_000), ("5,000만", 50_000_000), ("5000만원", 50_000_000),
    ("1.2억", 120_000_000), ("3억", 300_000_000), ("1억2000만", 120_000_000), ("1억 5000", 100_005_000), ("0.5억", 50_000_000),
    ("85%", 0.85), ("12.5 %", 0.125), ("33.3%", 0.333), ("100%", 1), ("0.25", 0.25), ("0", 0), ("0.5", 0.5), ("1", 1),
    ("-1,000", -1000), ("+30", 30), (1234, 1234), (0.4, 0.4), (3000000.0, 3_000_000),
])
def test_parse_value(text, want):
    got = targets.parse_value(text)
    assert got == pytest.approx(want)
    if isinstance(want, int):
        assert isinstance(got, int)


@pytest.mark.parametrize("text", ["", "   ", None, "abc", "5천만", "1억 abc", "%", "₩", "1,2,a", True, float("nan"), float("inf"), "5-3", "만"])
def test_parse_value_rejects(text):
    with pytest.raises(ValueError) as e:
        targets.parse_value(text)
    assert e.value.args[0]


def test_value_error_message_is_friendly():
    with pytest.raises(ValueError) as e:
        targets.parse_value("약 오백")
    assert "‘약 오백’" in str(e.value) and "숫자로 읽을 수 없어요" in str(e.value)


# ---------- 형식별 읽기 ----------

def make_xlsx(tmp_path, sheets, name="targets.xlsx"):
    path = tmp_path / name
    book = xlsxwriter.Workbook(str(path))
    for title, table in sheets.items():
        sheet = book.add_worksheet(title)
        for r, cells in enumerate(table):
            for c, cell in enumerate(cells):
                if cell is not None:
                    sheet.write(r, c, cell)
    book.close()
    return path


def test_xlsx_prefers_sheet_named_target(tmp_path):
    path = make_xlsx(tmp_path, {"메모": [["이름", "값"], ["엉뚱", 1]],
                                "목표": [["이름", "값", "기간", "컬럼", "대상"], ["매출", "₩5,000만", "2026-10", "amount", "강남점"], ["전환율", 0.25, None, None, None], ["방문자", 3000000, "2026", None, None]]})
    found = targets.read(path)
    assert found["errors"] == []
    rows = by_name(found)
    assert set(rows) == {"매출", "전환율", "방문자"}
    assert rows["매출"] == row("매출", 50_000_000, "2026-10", "amount", "강남점", 2)
    assert rows["전환율"]["value"] == 0.25 and rows["전환율"]["line"] == 3
    assert rows["방문자"]["value"] == 3_000_000 and rows["방문자"]["period"] == "2026"


def test_xlsx_first_sheet_when_no_target_sheet(tmp_path):
    path = make_xlsx(tmp_path, {"Sheet1": [["name", "value", "period"], ["Revenue", 100, "2026-10"]], "other": [["이름", "값"], ["x", 1]]})
    found = targets.read(path)
    assert [r["name"] for r in found["rows"]] == ["Revenue"] and found["rows"][0]["value"] == 100


def test_xlsx_blank_rows_bad_rows_and_missing_header(tmp_path):
    path = make_xlsx(tmp_path, {"목표": [["이름", "값"], ["매출", 100], [None, None], ["", ""], ["이익", "많이"], [None, 5], ["비용", None]]})
    found = targets.read(path)
    assert [r["name"] for r in found["rows"]] == ["매출"]
    assert [e["line"] for e in found["errors"]] == [5, 6, 7]  # 빈 줄은 건너뛰고 줄 번호는 엑셀 행 번호
    assert "숫자로 읽을 수 없어요" in found["errors"][0]["message"] and found["errors"][0]["message"].startswith("5번째 줄: ")
    assert "이름이 비어" in found["errors"][1]["message"] and "값이 비어" in found["errors"][2]["message"]
    nohead = make_xlsx(tmp_path, {"목표": [["가", "나"], [1, 2]]}, "nohead.xlsx")
    got = targets.read(nohead)
    assert got["rows"] == [] and "머리글" in got["errors"][0]["message"]


def test_xls_and_broken_excel_are_friendly(tmp_path):
    for name in ("old.xls", "bad.xlsx"):
        path = write(tmp_path, name, "이건 엑셀이 아니에요")
        with pytest.raises(ValueError) as e:
            targets.read(path)
        assert "엑셀 파일을 읽을 수 없어요" in str(e.value)
    assert targets.PARSERS[".xls"] is targets.PARSERS[".xlsx"]  # xls도 같은 길(calamine)로 읽는다


CSV = "이름,값,기간,컬럼,대상\n매출,\"₩5,000만\",2026-10,amount,강남점\n전환율,85%,,,\n방문자,\"3,000,000\",2026,,\n"


def test_csv_utf8_with_and_without_bom(tmp_path):
    for encoding in ("utf-8", "utf-8-sig"):
        found = targets.read(write(tmp_path, f"t_{encoding}.csv", CSV, encoding))
        rows = by_name(found)
        assert found["errors"] == []
        assert rows["매출"] == row("매출", 50_000_000, "2026-10", "amount", "강남점", 2)
        assert rows["전환율"]["value"] == 0.85 and rows["방문자"]["value"] == 3_000_000


def test_csv_cp949_fallback(tmp_path):
    found = targets.read(write(tmp_path, "han.csv", CSV.replace("₩", ""), "cp949"))  # CP949에는 ₩가 없다
    assert found["errors"] == [] and by_name(found)["매출"]["value"] == 50_000_000 and by_name(found)["매출"]["scope"] == "강남점"


def test_csv_english_headers_columns_in_any_order_and_bad_lines(tmp_path):
    text = "scope,Target,NAME\n강남점,100,Revenue\n,abc,Cost\n\n홍대점,200,Revenue\n"
    found = targets.read(write(tmp_path, "en.csv", text))
    assert [(r["name"], r["value"], r["scope"], r["line"]) for r in found["rows"]] == [("Revenue", 100, "강남점", 2), ("Revenue", 200, "홍대점", 5)]
    assert [e["line"] for e in found["errors"]] == [3]
    assert found["errors"][0]["message"].startswith("3번째 줄: ")


def test_csv_empty_and_undecodable(tmp_path):
    empty = targets.read(write(tmp_path, "empty.csv", ""))
    assert empty["rows"] == [] and empty["errors"]
    weird = tmp_path / "weird.csv"
    weird.write_bytes(bytes([0xFF, 0xFE, 0x00, 0xD8, 0x00]))  # UTF-8도 CP949도 아닌 바이트
    with pytest.raises(ValueError) as e:
        targets.read(weird)
    assert "글자 모양" in str(e.value)


def test_yaml_mapping_and_scalar_entries_with_line_numbers(tmp_path):
    text = ("목표:\n"
            "  매출:\n"
            "    값: 5,000만\n"
            "    기간: 2026-10\n"
            "    컬럼: amount\n"
            "    대상: 강남점\n"
            "  전환율: 85%\n"
            "  방문자: 3000000\n"
            "  이익: 많이\n")
    found = targets.read(write(tmp_path, "t.yaml", text))
    rows = by_name(found)
    assert rows["매출"] == row("매출", 50_000_000, "2026-10", "amount", "강남점", 2)
    assert rows["전환율"]["value"] == 0.85 and rows["전환율"]["line"] == 7
    assert rows["방문자"]["value"] == 3_000_000
    assert [e["line"] for e in found["errors"]] == [9]
    assert found["errors"][0]["message"].startswith("9번째 줄")


def test_yml_english_keys_list_form_and_yaml_numbers(tmp_path):
    text = ("targets:\n"
            "  - name: Revenue\n"
            "    value: 1500000\n"
            "    period: 2026\n"
            "    scope: HQ\n"
            "  - name: Rate\n"
            "    value: 0.3\n"
            "  - value: 5\n"
            "  - plain\n")
    found = targets.read(write(tmp_path, "t.yml", text))
    rows = by_name(found)
    assert rows["Revenue"] == row("Revenue", 1_500_000, "2026", "", "HQ", 2)  # YAML이 정수로 읽은 기간 2026도 글자로
    assert rows["Rate"]["value"] == 0.3
    assert [e["line"] for e in found["errors"]] == [8, 9]
    assert "이름이 비어" in found["errors"][0]["message"]


def test_yaml_problems_raise_friendly_errors(tmp_path):
    with pytest.raises(ValueError) as e:
        targets.read(write(tmp_path, "broken.yaml", "목표:\n  매출: [1, 2\n  이익: 3\n"))
    assert "YAML 형식이 올바르지 않아요" in str(e.value)
    with pytest.raises(ValueError) as e:
        targets.read(write(tmp_path, "norooot.yaml", "매출: 100\n"))
    assert "목표:" in str(e.value)
    with pytest.raises(ValueError):
        targets.read(write(tmp_path, "list.yaml", "- 1\n- 2\n"))
    with pytest.raises(ValueError):
        targets.read(write(tmp_path, "emptyroot.yaml", "목표:\n"))


def test_yaml_duplicate_keys_are_overlaps(tmp_path):
    text = "목표:\n  매출: {값: 100, 기간: 2026-10}\n  매출: {값: 200, 기간: 2026-10}\n  이익: 5\n"
    found = targets.read(write(tmp_path, "dup.yaml", text))
    assert [r["name"] for r in found["rows"]] == ["이익"]
    assert [e["line"] for e in found["errors"]] == [2, 3]


def test_json_with_line_numbers_and_list_form(tmp_path):
    text = ('{\n  "목표": {\n    "매출": {"값": "5,000만", "기간": "2026-10", "대상": "강남점"},\n    "전환율": {"value": 0.85},\n    "이익": {"값": "많이"}\n  }\n}\n')
    found = targets.read(write(tmp_path, "t.json", text))
    rows = by_name(found)
    assert rows["매출"] == row("매출", 50_000_000, "2026-10", "", "강남점", 3)
    assert rows["전환율"]["value"] == 0.85 and rows["전환율"]["line"] == 4
    assert [e["line"] for e in found["errors"]] == [5]
    listed = targets.read(write(tmp_path, "l.json", '{"targets": [{"name": "A", "value": 1}, {"name": "B", "value": "x"}]}'))
    assert [r["name"] for r in listed["rows"]] == ["A"]
    assert listed["errors"][0]["message"].startswith("2번째 항목: ")  # 줄을 모르면 몇 번째 항목인지
    with pytest.raises(ValueError) as e:
        targets.read(write(tmp_path, "bad.json", '{"목표": {'))
    assert "JSON 형식이 올바르지 않아요" in str(e.value)


def test_json_duplicate_keys_are_overlaps(tmp_path):
    found = targets.read(write(tmp_path, "d.json", '{"목표": {"매출": 1, "매출": 2, "이익": 3}}'))
    assert [r["name"] for r in found["rows"]] == ["이익"] and len(found["errors"]) == 2


MD_TABLE = ("# 올해 목표\n\n설명 글은 건너뜁니다.\n\n"
            "| 이름 | 값 | 기간 | 컬럼 | 대상 |\n"
            "|---|:---:|---|---|---|\n"
            "| 매출 | ₩5,000만 | 2026-10 | amount | 강남점 |\n"
            "| 전환율 | 85% | | | |\n"
            "| 이익 | 많이 | | | |\n"
            "\n"
            "| 이름 | 값 |\n|---|---|\n| 다른 표 | 1 |\n")


def test_md_first_table_only(tmp_path):
    found = targets.read(write(tmp_path, "t.md", MD_TABLE))
    rows = by_name(found)
    assert set(rows) == {"매출", "전환율"}  # 두 번째 표는 읽지 않는다
    assert rows["매출"] == row("매출", 50_000_000, "2026-10", "amount", "강남점", 7)
    assert rows["전환율"]["value"] == 0.85 and rows["전환율"]["line"] == 8
    assert [e["line"] for e in found["errors"]] == [9]


def test_md_bullet_list(tmp_path):
    text = ("# 목표\n\n- 매출: ₩5,000만 (2026-10)\n- 전환율: 85%\n* 방문자: 3,000,000 (2026)\n- 메모만 있는 줄\n- 이익: 많이 (2026-10)\n일반 문장: 건너뜀\n- 비용:\n")
    found = targets.read(write(tmp_path, "t.md", text))
    rows = by_name(found)
    assert rows["매출"] == row("매출", 50_000_000, "2026-10", "", "", 3)
    assert rows["전환율"]["value"] == 0.85 and rows["전환율"]["period"] == ""
    assert rows["방문자"]["period"] == "2026"
    assert [e["line"] for e in found["errors"]] == [7, 9]  # 콜론 없는 목록 줄과 일반 문장은 건너뛰고, 값이 비었거나 틀린 줄은 오류
    none = targets.read(write(tmp_path, "none.md", "# 제목\n그냥 글\n"))
    assert none["rows"] == [] and "찾지 못했어요" in none["errors"][0]["message"]


def test_md_table_with_unknown_header(tmp_path):
    found = targets.read(write(tmp_path, "h.md", "| a | b |\n|---|---|\n| 1 | 2 |\n"))
    assert found["rows"] == [] and "머리글" in found["errors"][0]["message"]


def test_txt_lines(tmp_path):
    text = ("# 주석\n"
            "매출 = ₩5,000만 [2026-10]\n"
            "\n"
            "전환율 = 85%\n"
            "방문자 = 3,000,000 2026\n"
            "비용 = 1 억 2026-Q1\n"
            "이익 = 많이\n"
            "아무말\n"
            " = 5\n")
    found = targets.read(write(tmp_path, "t.txt", text))
    rows = by_name(found)
    assert rows["매출"] == row("매출", 50_000_000, "2026-10", "", "", 2)
    assert rows["전환율"] == row("전환율", 0.85, "", "", "", 4)
    assert rows["방문자"]["value"] == 3_000_000 and rows["방문자"]["period"] == "2026"
    assert targets.split_value_period("5 000") == ("5 000", "")  # 공백으로 끊어 쓴 자릿수는 값
    assert rows["비용"]["value"] == 100_000_000 and rows["비용"]["period"] == "2026-Q1"
    assert [e["line"] for e in found["errors"]] == [7, 8, 9]
    assert "‘이름 = 값 [기간]’" in found["errors"][1]["message"]


def test_txt_cp949(tmp_path):
    found = targets.read(write(tmp_path, "han.txt", "매출 = 5,000만 [2026-10]\n", "cp949"))
    assert found["rows"][0]["value"] == 50_000_000


# ---------- 겹침 ----------

def test_overlap_drops_all_copies_with_line_numbers(tmp_path):
    text = "이름,값,기간,대상\n매출,100,2026-10,\n매출,200,2026-10,\n매출,300,2026-11,\n매출,400,2026-10,강남점\n이익,5,,\n매출,999,2026-10,\n"
    found = targets.read(write(tmp_path, "dup.csv", text))
    assert sorted((r["name"], r["period"], r["scope"]) for r in found["rows"]) == [("매출", "2026-10", "강남점"), ("매출", "2026-11", ""), ("이익", "", "")]
    overlaps = [e for e in found["errors"] if "겹침" in e["message"]]
    assert [e["line"] for e in overlaps] == [2, 3, 7]  # 세 줄 모두 빠지고 각각 알려 준다
    assert "3·7번째 줄과" in overlaps[0]["message"] and "모두 뺐어요" in overlaps[0]["message"]
    assert targets.lookup(found["rows"], "매출", "2026-10", "강남점")["row"]["value"] == 400


def test_same_name_and_period_with_different_scope_is_not_overlap(tmp_path):
    found = targets.read(write(tmp_path, "scopes.csv", "이름,값,기간,대상\n매출,100,2026-10,강남점\n매출,200,2026-10,홍대점\n"))
    assert len(found["rows"]) == 2 and found["errors"] == []


# ---------- 찾기 ----------

ROWS = [row("매출", 100, "2026-10", scope="강남점", line=2), row("매출", 200, "2026-10", scope="홍대점", line=3), row("매출", 150, "2026-11", scope="강남점", line=4),
        row("전환율", 0.3, line=5), row("방문자", 10, "2026-10", line=6), row("방문자", 20, "2026-11", line=7), row("이익", 7, "2026", scope="", line=8), row("이익", 9, "", line=9)]


def test_lookup_exact_matches():
    assert targets.lookup(ROWS, "매출", "2026-10", "강남점")["row"]["value"] == 100
    assert targets.lookup(ROWS, "매출", "2026-10", "홍대점")["row"]["value"] == 200
    assert targets.lookup(ROWS, "매출", "2026-11", "강남점")["row"]["value"] == 150
    assert targets.lookup(ROWS, "전환율")["row"]["value"] == 0.3
    assert targets.lookup(ROWS, " 전환율 ")["row"]["value"] == 0.3
    got = targets.lookup(ROWS, "방문자", "2026-11")
    assert got["row"]["value"] == 20 and got["error"] is None and got["ambiguous"] is False


def test_lookup_reports_ambiguity_instead_of_guessing():
    by_scope = targets.lookup(ROWS, "매출", "2026-10")
    assert by_scope["row"] is None and by_scope["ambiguous"] is True
    assert "어느 대상인지 알 수 없어요" in by_scope["error"] and "강남점" in by_scope["error"] and "홍대점" in by_scope["error"]
    by_period = targets.lookup(ROWS, "방문자")
    assert by_period["ambiguous"] is True and "어느 기간인지 알 수 없어요" in by_period["error"] and "2026-11" in by_period["error"]
    both = targets.lookup(ROWS, "매출")
    assert both["row"] is None and both["ambiguous"] is True


def test_lookup_not_found_is_not_ambiguous():
    for args in [("없는 지표",), ("매출", "2030"), ("매출", "2026-10", "부산점"), ("방문자", "2027")]:
        got = targets.lookup(ROWS, *args)
        assert got["row"] is None and got["ambiguous"] is False and "찾지 못했어요" in got["error"], args


def test_lookup_falls_back_to_rows_without_period_or_scope():
    assert targets.lookup(ROWS, "이익", "2026")["row"]["value"] == 7  # 같은 기간이 있으면 그것
    assert targets.lookup(ROWS, "이익", "2027")["row"]["value"] == 9  # 없으면 기간이 빈 줄(모든 기간)
    assert targets.lookup(ROWS, "전환율", "2026-10", "강남점")["row"]["value"] == 0.3  # 기간·대상이 빈 줄은 어디에나 해당
    assert targets.lookup([], "매출")["error"]


def test_lookup_with_read_rows(tmp_path):
    found = targets.read(write(tmp_path, "t.csv", "이름,값,기간,대상\n매출,100,2026-10,강남점\n매출,200,2026-10,홍대점\n"))
    assert targets.lookup(found["rows"], "매출", "2026-10")["ambiguous"] is True
    assert targets.lookup(found["rows"], "매출", "2026-10", "홍대점")["row"]["value"] == 200


# ---------- 파일 수준 ----------

def test_unsupported_missing_and_large_files(tmp_path, monkeypatch):
    with pytest.raises(ValueError) as e:
        targets.read(write(tmp_path, "t.pdf", "x"))
    assert ".xlsx" in str(e.value)
    with pytest.raises(ValueError) as e:
        targets.read(tmp_path / "없는.csv")
    assert "찾을 수 없어요" in str(e.value)
    monkeypatch.setattr(targets, "MAX_FILE_BYTES", 10)
    with pytest.raises(ValueError) as e:
        targets.read(write(tmp_path, "big.csv", "이름,값\n" + "매출,1\n" * 10))
    assert "MB까지" in str(e.value) or "읽어요" in str(e.value)


def test_extension_is_case_insensitive(tmp_path):
    assert targets.read(write(tmp_path, "UP.TXT", "a = 1\n"))["rows"][0]["value"] == 1
    assert targets.read(write(tmp_path, "UP.CSV", "이름,값\na,2\n"))["rows"][0]["value"] == 2


# ---------- 캐시 ----------

def test_cache_reuses_until_mtime_or_size_changes(tmp_path, monkeypatch):
    calls = []
    original = targets.PARSERS[".txt"]
    monkeypatch.setitem(targets.PARSERS, ".txt", lambda path: calls.append(path) or original(path))
    path = write(tmp_path, "t.txt", "매출 = 100\n")
    first = targets.read(path)
    assert targets.read(path) == first and len(calls) == 1  # 같은 수정 시각·크기면 다시 읽지 않는다
    assert targets.read(str(path)) == first and len(calls) == 1  # 문자열 경로도 같은 항목
    # 크기가 바뀌면 다시 읽는다
    stat = os.stat(path)
    path.write_bytes("매출 = 100\n이익 = 5\n".encode("utf-8"))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # 수정 시각은 그대로, 크기만 다르게
    changed = targets.read(path)
    assert len(calls) == 2 and [r["name"] for r in changed["rows"]] == ["매출", "이익"]
    # 수정 시각만 바뀌어도 다시 읽는다(크기는 같게)
    path.write_bytes("매출 = 900\n이익 = 5\n".encode("utf-8"))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    again = targets.read(path)
    assert len(calls) == 3 and again["rows"][0]["value"] == 900


def test_cache_returns_copies_and_clear_forgets(tmp_path, monkeypatch):
    calls = []
    original = targets.PARSERS[".txt"]
    monkeypatch.setitem(targets.PARSERS, ".txt", lambda path: calls.append(path) or original(path))
    path = write(tmp_path, "t.txt", "매출 = 100\n")
    got = targets.read(path)
    got["rows"][0]["value"] = -1
    got["rows"].clear()
    got["errors"].append("x")
    assert targets.read(path)["rows"][0]["value"] == 100 and targets.read(path)["errors"] == []  # 호출자가 고쳐도 캐시는 그대로
    targets.clear()
    targets.read(path)
    assert len(calls) == 2


def test_cache_is_per_path_and_failures_are_not_cached(tmp_path):
    a = write(tmp_path, "a.txt", "x = 1\n")
    b = write(tmp_path, "b.txt", "y = 2\n")
    assert targets.read(a)["rows"][0]["name"] == "x" and targets.read(b)["rows"][0]["name"] == "y"
    broken = write(tmp_path, "c.json", "{")
    for _ in range(2):
        with pytest.raises(ValueError):
            targets.read(broken)
    broken.write_text('{"목표": {"z": 3}}', encoding="utf-8")
    assert targets.read(broken)["rows"][0]["name"] == "z"
