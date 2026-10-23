"""잠금 파일: 설정 폴더의 locks 폴더 아래 파일을 배타적으로 만들어 잡는다. 같은 PC의 여러 프로세스(프로그램 창, Windows 예약 작업)가 같은 일을 번갈아 하게 한다.
파일에는 잡은 프로세스("프로세스 번호 시작 시각")를 적어, 그 프로세스가 끝났으면 남은 잠금을 지우고 다시 잡는다. 수집 시작·예약(schedule)·업로드 기록(uploads)·적재 기록(load_table)이 쓴다."""
import contextlib
import hashlib
import os
import time

import psutil

from core import scratch
from core import settings

WAIT = 5  # guarded가 잠금이 풀리기를 기다리는 시간(초)


def locks():
    return settings.PATH.parent / "locks"


def lock_path(name):
    return locks() / (hashlib.sha256(name.encode("utf-8")).hexdigest()[:24] + ".lock")


def left_behind(path):
    """잠금 파일이 끝난 프로세스의 것인지. 읽을 수 없거나 깨졌으면 모르는 것이라 거짓(지우지 않는다)."""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        pid, started = path.read_text(encoding="ascii").split()
        return scratch.ended(pid, started)
    except (OSError, ValueError, psutil.Error):
        return False


def hold(path):
    """잠금 파일을 만든다(이미 있으면 그 프로세스가 끝났을 때만 지우고 다시). 잡았으면 True."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if not left_behind(path):
                return False
            path.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(scratch.stamp())
        return True
    return False


def let_go(path):
    """이 프로세스가 잡은 잠금만 푼다."""
    try:
        if path.read_text(encoding="ascii") == scratch.stamp():
            path.unlink()
    except (OSError, ValueError):  # 없거나 남의 것(읽을 수 없는 글자)
        pass


@contextlib.contextmanager
def guarded(name):
    """name 잠금을 WAIT초까지 기다려 잡고 안의 일을 한다(끝나면 푼다). 끝내 못 잡아도 일은 한다(잠금을 남긴 프로세스가 멈춘 경우에 기록이 영영 막히지 않게)."""
    path = locks() / name
    end = time.monotonic() + WAIT
    held = hold(path)
    while not held and time.monotonic() < end:
        time.sleep(0.05)
        held = hold(path)
    try:
        yield
    finally:
        if held:
            let_go(path)
