"""12차 리뷰: 수집 CSV→CSV는 파일 전체로 글자 컬럼을 정해 뒤쪽 수식도 막고, 숫자 모양 글자(-1500)는 막지 않는다. YAML 키에 둔 별칭 폭발은 바로 거절한다.
파일은 모두 tmp_path 안에서 만든다."""
import time

import ibis

from collect import collect_job
from collect.collect_store import duck, write_output
from core import targets


def test_late_formula_in_numeric_looking_column_is_guarded(tmp_path, monkeypatch):
    raw = tmp_path / "late.csv"
    raw.write_text("id,note\n" + "".join(f"{i},{i}\n" for i in range(30000)) + '30000,=HYPERLINK("http://x")\n', encoding="utf-8")
    out = tmp_path / "out.csv"
    monkeypatch.setattr(collect_job, "replace", lambda found, path: None)
    monkeypatch.setattr(collect_job, "update", lambda **kw: None)
    collect_job.finish_file({"file": {"kind": "csv", "encoding": "utf-8"}, "output": {"format": "csv"}}, tmp_path / "final", {"part": raw, "out": out}, {})
    assert out.read_text(encoding="utf-8-sig").splitlines()[-1] == '30000,"\'=HYPERLINK(""http://x"")"'


def test_numbers_as_text_stay_numbers(tmp_path):
    con, folder = duck()
    try:
        out = tmp_path / "api.csv"
        write_output(con, ibis.memtable({"amount": ["-1500", "+2", "=1+1", "-x"]}), "csv", out, folder)
    finally:
        con.disconnect()
    assert out.read_text(encoding="utf-8-sig").splitlines() == ["amount", "-1500", "+2", "'=1+1", "'-x"]


def test_alias_bomb_in_keys_is_refused_fast(tmp_path):
    lines = ["? &a0 [x, x, x, x, x, x, x, x, x, x]\n: 1"] + [f"? &a{i} [" + ", ".join([f"*a{i - 1}"] * 10) + "]\n: 1" for i in range(1, 9)]
    path = tmp_path / "keys.yaml"
    path.write_text("\n".join(lines) + "\n목표:\n  매출: 1\n", encoding="utf-8")
    targets.clear()
    start = time.perf_counter()
    try:
        got = targets.read(path)
    except ValueError as e:
        got = e
    assert time.perf_counter() - start < 2 and "글자" in (str(got) if isinstance(got, ValueError) else got["errors"][0]["message"])
