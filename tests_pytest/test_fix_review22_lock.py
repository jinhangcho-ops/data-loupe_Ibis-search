"""22차: Windows에서 방금 푼 잠금 파일이 아직 지워지는 중이면 os.open이 PermissionError를 낸다. 잠금은 그것을 '못 잡음'으로 보고 다시 시도해야
기록(load_tables 등)이 빠지지 않는다(전에는 오류가 그대로 나가 guarded 안의 일이 돌지 않았다)."""
import os

from core import filelock


def test_permission_error_while_deleting_is_retried(folder, monkeypatch):
    real, calls = os.open, []

    def flaky(path, flags, *args):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError(13, "Permission denied")
        return real(path, flags, *args)
    monkeypatch.setattr(filelock.os, "open", flaky)
    assert filelock.hold(filelock.locks() / "x.lock") is False  # 지워지는 중: 못 잡음(오류를 내지 않는다)
    done = []
    with filelock.guarded("y.lock"):
        done.append(True)
    assert done and len(calls) >= 2 and not (filelock.locks() / "y.lock").exists()
