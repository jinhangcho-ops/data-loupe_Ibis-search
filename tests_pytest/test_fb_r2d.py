"""F13 후속: 폴더 연결이 고른 파일(file·files)도 recent에 남기고, 시작 화면 connectLast가 그 파일로 바로 잇는다.
옛 저장값(file·files 없음)은 1차 동작(파일 하나일 때만)으로 읽힌다."""
import json
import re

import pytest

from conftest import ROOT
from core import source, use_counts
from test_fb_r1d import FOLDER, click, connects


def test_recent_keeps_chosen_files():
    assert source.recent({"kind": "folder", "path": "C:/d", "file": "C:/d/a.duckdb"}) == {"kind": "folder", "path": "C:/d", "file": "C:/d/a.duckdb"}
    assert source.recent({"kind": "folder", "path": "C:/d", "files": ["C:/d/a.csv", "C:/d/b.csv"]}) == {"kind": "folder", "path": "C:/d", "files": ["C:/d/a.csv", "C:/d/b.csv"]}
    assert source.recent({"kind": "folder", "path": "C:/d"}) == {"kind": "folder", "path": "C:/d"}  # 옛 모양 그대로


def test_last_connection_returns_files(tmp_path, monkeypatch):
    monkeypatch.setattr(use_counts.settings, "PATH", tmp_path / "settings.json")
    info = source.recent({"kind": "folder", "path": "C:/d", "files": ["C:/d/a.csv"]})
    use_counts.use("connections", use_counts.connection_key(info), info)
    assert use_counts.last_connection() == {"kind": "folder", "path": "C:/d", "files": ["C:/d/a.csv"]}


def test_saved_file_connects_without_search(tmp_path):
    saved = {**FOLDER, "file": "C:/data/b.duckdb"}
    out = click(tmp_path, "/search", recent=saved, files=["C:/data/a.duckdb", "C:/data/b.duckdb"])
    assert connects(out) == [saved] and not any(c["url"] == "/api/files" for c in out["calls"])
    assert out["href"] == "/search" and out["shown"][0]["flat"] is False


def test_saved_files_connect_as_flat(tmp_path):
    saved = {**FOLDER, "files": ["C:/data/a.csv", "C:/data/b.csv"]}
    out = click(tmp_path, "/prep", recent=saved)
    assert connects(out) == [saved] and out["shown"][0]["flat"] is True and out["href"] == "/prep"


def test_vanished_saved_file_shows_card(tmp_path):
    out = click(tmp_path, "/search", recent={**FOLDER, "file": "C:/data/gone.duckdb"}, connect={"error": "파일이 없어요."})
    assert out["href"] == "" and out["msgs"] == ["파일이 없어요."] and len(out["open"]) == 1


def test_old_saved_value_keeps_first_behavior(tmp_path):
    one = click(tmp_path, "/search", recent=FOLDER, files=["C:/data/a.duckdb"])
    assert connects(one) == [{**FOLDER, "file": "C:/data/a.duckdb"}]
    many = click(tmp_path, "/search", recent=FOLDER, flat=["C:/data/a.csv", "C:/data/b.csv"])
    assert connects(many) == [] and len(many["open"]) == 1


def test_no_control_chars():
    for path in ("core/source.py", "static/core/home.html", "tests_pytest/test_fb_r2d.py"):
        assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", (ROOT / path).read_text(encoding="utf-8")), path
