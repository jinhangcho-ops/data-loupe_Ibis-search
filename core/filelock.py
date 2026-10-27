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
BROKEN_AFTER = 10  # 비었거나 깨진 잠금 파일이 이 시간(초)보다 오래됐으면 만든 프로세스가 쓰다 멈춘 것으로 본다(만든 직후 쓰기 전의 빈 파일은 지우지 않는다)


def locks():
    return settings.PATH.parent / "locks"


def lock_path(name):
    return locks() / (hashlib.sha256(name.encode("utf-8")).hexdigest()[:24] + ".lock")


def left_behind(path):
    """잠금 파일이 끝난 프로세스의 것인지. 비었거나 깨졌으면(만든 프로세스가 쓰기 전에 멈춘 빈 파일 등) 고친 지 BROKEN_AFTER초가 넘었을 때만 남은 것으로 본다."""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        pid, started = path.read_text(encoding="ascii").split()
        return scratch.ended(pid, started)
    except (OSError, psutil.Error):  # 프로세스를 볼 수 없다(권한 등): 살아 있을 수 있으니 빼앗지 않는다
        return False
    except ValueError:  # 비었거나 깨진 내용
        try:
            return time.time() - path.stat().st_mtime > BROKEN_AFTER
        except OSError:
            return False


def clear_stale(path, stale=left_behind):
    """끝난 프로세스가 남긴 잠금을 지운다(stale(파일)이 남은 것으로 보는 파일). 판단한 뒤 지우기 전에 다른 프로세스가 새로 잡았을 수 있으므로, 먼저 다른 이름으로 옮기고 옮긴 것이 정말 남은 것인지 다시 본다(아니면 되돌린다)."""
    moved = path.with_name(f"{path.name}.{os.getpid()}.old")
    try:
        os.replace(path, moved)
    except OSError:
        return
    if not stale(moved):
        try:
            os.link(moved, path)  # 남의 살아 있는 잠금을 옮겼다: 그 자리가 비어 있으면 돌려놓는다
        except OSError:
            pass
    moved.unlink(missing_ok=True)


def hold(path):
    """잠금 파일을 만든다(이미 있으면 그 프로세스가 끝났을 때만 지우고 다시). 잡았으면 True."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if not left_behind(path):
                return False
            clear_stale(path)
            continue
        except PermissionError:  # Windows: 다른 쪽이 방금 푼 잠금 파일이 아직 지워지는 중이면 만들 수 없다. 못 잡은 것으로 보고 잠시 뒤 다시
            return False
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
