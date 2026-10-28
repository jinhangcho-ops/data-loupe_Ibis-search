"""프로그램이 쓰는 임시 폴더. 전용 폴더(ROOT) 아래에만 만들고, 강제 종료로 남은 것을 다음 실행 때 지운다.
지울 때는 이 프로그램이 만든 것이 확실한 폴더만 지운다(left_behind의 조건). 하나라도 어긋나면 그대로 둔다."""
import os
import re
import shutil
import tempfile
from pathlib import Path

import psutil

ROOT = Path(tempfile.gettempdir(), "DataLoupe")  # 이 프로그램만 쓰는 폴더. 이 밖은 건드리지 않는다.
MARK = ".DataLoupe"  # 만든 프로세스를 적어 두는 표식 파일: "프로세스 번호 시작 시각"
NAME = re.compile(r"(\d+)_\w{8}")  # 폴더 이름: 프로세스 번호_무작위 8자(tempfile이 붙인다)


def make():
    """이 프로세스의 임시 폴더를 ROOT 아래에 하나 만들고 표식을 넣는다. 다 쓰면 만든 쪽이 지운다."""
    ROOT.mkdir(exist_ok=True)
    if ROOT.is_symlink() or os.path.isjunction(ROOT):  # 다른 곳을 가리키는 링크면 그 안에 만들지 않는다.
        raise OSError(f"임시 폴더({ROOT})가 링크라서 쓸 수 없어요.")
    folder = tempfile.mkdtemp(prefix=f"{os.getpid()}_", dir=ROOT)
    Path(folder, MARK).write_text(stamp(), encoding="ascii")
    return folder


def stamp():
    """이 프로세스를 알아보는 글자 "프로세스 번호 시작 시각". 표식·잠금 파일에 적는다."""
    return f"{os.getpid()} {psutil.Process().create_time()}"


def ended(pid, started):
    """그 번호·그 시작 시각의 프로세스가 지금은 없는지(같은 번호를 다른 프로세스가 다시 쓴 경우도 없는 것이다).
    글자가 깨졌으면 ValueError, 프로세스를 볼 수 없으면 psutil.Error — 부르는 쪽은 모르는 것으로 다룬다."""
    try:
        return abs(psutil.Process(int(pid)).create_time() - float(started)) > 1
    except psutil.NoSuchProcess:
        return True


def remove(folder):
    """make로 만든 폴더를 지운다. 빈 ROOT는 남긴다(지우면 다른 창·스레드의 make가 ROOT를 만든 뒤 그 안에 만들기 전에 사라질 수 있다).
    잠긴 파일 때문에 폴더가 남으면 표식을 다시 써 둔다(표식이 없으면 sweep이 영원히 지우지 않는다)."""
    mark = Path(folder, MARK)
    try:
        text = mark.read_text(encoding="ascii")
    except OSError:
        text = None
    shutil.rmtree(folder, ignore_errors=True)
    if text and Path(folder).is_dir() and not mark.exists():
        try:
            mark.write_text(text, encoding="ascii")
        except OSError:
            pass


def left_behind(folder):
    """folder가 끝난 프로세스가 남긴 이 프로그램의 임시 폴더인지. 아래를 모두 채워야 참이다.
    ROOT 바로 아래의 진짜 폴더(링크·정션이 아님)이고, 이름이 NAME 모양이고, 표식의 번호가 이름의 번호와 같고,
    그 번호·그 시작 시각의 프로세스가 지금은 없다."""
    try:
        match = NAME.fullmatch(folder.name)
        if not match or folder.is_symlink() or os.path.isjunction(folder) or not folder.is_dir() or folder.resolve().parent != ROOT.resolve():
            return False
        mark = Path(folder, MARK)
        if mark.is_symlink() or not mark.is_file():
            return False
        pid, started = mark.read_text(encoding="ascii").split()
        if pid != match[1]:
            return False
        return ended(pid, started)
    except (OSError, ValueError, psutil.Error):  # 읽을 수 없거나 표식이 깨졌으면 모르는 것이다.
        return False


def sweep():
    """ROOT 바로 아래에서 끝난 프로세스가 남긴 임시 폴더만 지운다. 켜져 있는 다른 창의 폴더와 모르는 것은 둔다."""
    if ROOT.is_symlink() or os.path.isjunction(ROOT) or not ROOT.is_dir():
        return
    for folder in ROOT.iterdir():
        if left_behind(folder):
            remove(folder)
