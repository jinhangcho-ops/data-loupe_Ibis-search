"""F13 후속: 폴더 연결이 고른 파일(file·files)도 recent에 남긴다. 시작 화면이 지난 연결로 바로 잇던 connectLast는 Phase 16(결정 D5)에서 없앴다.
옛 저장값(file·files 없음)은 그대로 읽힌다."""
import re

from conftest import ROOT
from core import source, use_counts


def test_recent_keeps_chosen_files():
    assert source.recent({"kind": "folder", "path": "C:/d", "file": "C:/d/a.duckdb"}) == {"kind": "folder", "path": "C:/d", "file": "C:/d/a.duckdb"}
    assert source.recent({"kind": "folder", "path": "C:/d", "files": ["C:/d/a.csv", "C:/d/b.csv"]}) == {"kind": "folder", "path": "C:/d", "files": ["C:/d/a.csv", "C:/d/b.csv"]}
    assert source.recent({"kind": "folder", "path": "C:/d"}) == {"kind": "folder", "path": "C:/d"}  # 옛 모양 그대로


def test_last_connection_returns_files(tmp_path, monkeypatch):
    monkeypatch.setattr(use_counts.settings, "PATH", tmp_path / "settings.json")
    info = source.recent({"kind": "folder", "path": "C:/d", "files": ["C:/d/a.csv"]})
    use_counts.use("connections", use_counts.connection_key(info), info)
    assert use_counts.last_connection() == {"kind": "folder", "path": "C:/d", "files": ["C:/d/a.csv"]}


def test_no_control_chars():
    for path in ("core/source.py", "static/core/home.html", "tests_pytest/test_fb_r2d.py"):
        assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", (ROOT / path).read_text(encoding="utf-8")), path
