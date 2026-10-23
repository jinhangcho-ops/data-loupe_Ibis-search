"""앱 이름을 바꾼 뒤(ibis_search → DataLoupe) 예전 설정 폴더 옮기기. 값은 시험 안에서 지어내고 임시 폴더에서만 한다."""
import os

from core import scratch, settings


def make(tmp_path):
    old = tmp_path / "ibis_search"
    (old / "drivers").mkdir(parents=True)
    (old / "settings.json").write_text('{"theme": "dark"}', encoding="utf-8")
    return old, tmp_path / "DataLoupe" / "settings.json"


def test_moves_old_folder_whole(tmp_path):
    old, path = make(tmp_path)
    assert settings.migrate(path) is None
    assert not old.exists()
    assert path.read_text(encoding="utf-8") == '{"theme": "dark"}' and (path.parent / "drivers").is_dir()


def test_new_folder_exists_or_no_old_folder_does_nothing(tmp_path):
    old, path = make(tmp_path)
    path.parent.mkdir()
    assert settings.migrate(path) is None
    assert old.is_dir() and not path.exists()  # 새 폴더가 이미 있으면 예전 폴더를 건드리지 않는다
    assert settings.migrate(tmp_path / "x" / "DataLoupe" / "settings.json") is None  # 예전 폴더가 없으면 아무것도 만들지 않는다
    assert not (tmp_path / "x").exists()


def test_failure_keeps_old_folder_and_leaves_reason(tmp_path, monkeypatch):
    old, path = make(tmp_path)

    def locked(src, dst):
        raise PermissionError("다른 프로세스가 사용 중")

    monkeypatch.setattr(os, "replace", locked)
    reason = settings.migrate(path)
    assert str(old) in reason and "다른 프로세스가 사용 중" in reason
    assert (old / "settings.json").is_file()  # 예전 폴더는 그대로
    assert (path.parent / settings.FAILED).read_text(encoding="utf-8") == reason
    assert settings.migrate(path) is None  # 새 폴더가 생겼으니 다음 시작부터는 다시 옮기려 하지 않는다


def test_temp_folder_is_new_name_only():
    assert scratch.ROOT.name == "DataLoupe" and scratch.MARK == ".DataLoupe"
