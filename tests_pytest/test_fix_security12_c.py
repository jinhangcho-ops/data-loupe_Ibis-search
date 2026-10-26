"""보안 12C: 목표 파일 작성자가 작은 파일로 앱을 멈추게 하지 못한다(YAML 별칭 폭발, 겹침 오류 제곱, JSON 줄 찾기 제곱, 긴 줄 정규식 되돌아감, 깊은 중첩).
파일은 모두 tmp_path 안에서 만든다."""
import time

import pytest

from core import targets


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def fresh_cache():
    targets.clear()
    yield
    targets.clear()


def timed(fn):
    start = time.perf_counter()
    try:
        return fn(), time.perf_counter() - start
    except ValueError as e:
        return e, time.perf_counter() - start


def test_yaml_alias_bomb_is_refused_fast(tmp_path):
    names = "abcdefg"
    text = "lol: &a [x, x, x, x, x, x, x, x, x]\n"
    for prev, name in zip(names, names[1:]):
        text += f"{name}: &{name} [" + ", ".join([f"*{prev}"] * 9) + "]\n"
    text += "목표:\n  매출: 1\n"
    assert len(text.encode()) < 500
    got, secs = timed(lambda: targets.read(write(tmp_path, "bomb.yaml", text)))
    assert isinstance(got, ValueError) and "너무" in str(got) and secs < 2


def test_plain_yaml_still_reads(tmp_path):
    found = targets.read(write(tmp_path, "ok.yaml", "기본: &d {기간: 2026-10}\n목표:\n  매출: {값: 100, 기간: 2026-10}\n  이익: 5\n"))
    assert [(r["name"], r["value"], r["line"]) for r in found["rows"]] == [("매출", 100, 3), ("이익", 5, 4)] and found["errors"] == []


def test_many_overlaps_are_fast_and_short(tmp_path):
    got, secs = timed(lambda: targets.read(write(tmp_path, "dup.txt", "a=1\n" * 8000 + "b=2\n")))
    assert secs < 1
    assert [r["name"] for r in got["rows"]] == ["b"]  # 겹친 줄은 모두 빠진다
    assert got["error_total"] == len(got["errors"]) == 8000 and sum(len(e["message"]) for e in got["errors"]) < 1_000_000  # 다른 줄 번호는 앞 5개만이라 문구가 줄 수에 비례해서만 커진다
    assert got["errors"][0]["message"].startswith("1번째 줄: ") and "2·3·4·5·6…번째 줄과" in got["errors"][0]["message"]
    assert [e["line"] for e in got["errors"]] == list(range(1, 8001))  # 자르지 않고 모두(화면은 쪽으로 넘겨 본다, 12D)


def test_json_line_numbers_for_many_keys_are_fast(tmp_path):
    body = ",\n".join(f'    "k{i}": {i}' for i in range(8000))
    got, secs = timed(lambda: targets.read(write(tmp_path, "many.json", '{\n  "목표": {\n' + body + "\n  }\n}\n")))
    assert secs < 1 and got["errors"] == []
    assert [r["line"] for r in got["rows"]] == list(range(3, 8003)) and got["rows"][-1]["name"] == "k7999"


def test_json_repeated_and_tricky_keys_keep_line_numbers(tmp_path):
    text = '{\n  "목표": {\n    "a": {"메모": "\\"a\\": 1", "값": 1},\n    "a": 2,\n    "b\\"c": 3\n  }\n}\n'
    found = targets.read(write(tmp_path, "t.json", text))
    assert [(e["line"]) for e in found["errors"]] == [3, 4]  # 같은 키 두 번 → 둘 다 겹침, 줄은 차례로
    assert [(r["name"], r["line"]) for r in found["rows"]] == [('b"c', 5)]


@pytest.mark.parametrize("name,text", [
    ("bullet.md", "- " + "a" * 16000 + "\n"),
    ("table.md", "| 이름 | 값 |\n|" + " " * 16000 + "x\n"),
    ("bracket.txt", "a = " + "[" * 16000 + "\n"),
], ids=["bullet", "table", "bracket"])
def test_long_line_is_refused_fast(tmp_path, name, text):
    got, secs = timed(lambda: targets.read(write(tmp_path, name, text)))
    assert isinstance(got, ValueError) and "너무 길어요" in str(got) and secs < 1


def test_normal_md_and_txt_still_read(tmp_path):
    assert targets.read(write(tmp_path, "ok.md", "- 매출: 100 (2026-10)\n"))["rows"][0]["period"] == "2026-10"
    assert targets.read(write(tmp_path, "ok.txt", "매출 = 100 [2026-10]\n"))["rows"][0]["value"] == 100


@pytest.mark.parametrize("name,text", [("deep.json", "[" * 100000), ("deep.yaml", "목표: " + "[" * 100000)], ids=["json", "yaml"])
def test_deep_nesting_is_value_error(tmp_path, name, text):
    with pytest.raises(ValueError, match="너무 깊"):
        targets.read(write(tmp_path, name, text))
