"""설치한 기능(쿼리·분석·대시보드·수집·연동)과 설정 창의 [적용](기능 바꾸기).
설치 프로그램이 exe 옆에 쓰는 features.ini를 읽는다. 개발 실행이거나 파일이 없으면 기능은 전부, 버전은 None이다.
기능을 바꾸려면 같은 버전의 DataLoupe-setup.exe를 GitHub Releases에서 받아 지문을 확인한 뒤 /COMPONENTS를 달아 조용히 실행한다."""
import configparser
import errno
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import lazy
from core import scratch

ALL = {"query": ("쿼리", "/search"), "prep": ("분석", "/prep"), "dashboard": ("대시보드", "/dashboard"),
       "collect": ("수집", "/collect"), "integrations": ("연동", "/integrations")}  # id: (이름, 화면 주소). 설치 파일 [Components]의 이름과 같다.
RELEASES = "https://github.com/jinhangcho-ops/data-loupe_Ibis-search/releases/download"
LIMIT = 400 * 1024 * 1024  # 받는 크기 상한(바이트)
WAIT = 30  # 서버가 답하기를 기다리는 시간(초)
VERSION = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+-]*")


def ini_path():
    """exe 옆의 features.ini. 개발 실행(frozen 아님)이면 None."""
    return Path(sys.executable).parent / "features.ini" if getattr(sys, "frozen", False) else None


def state():
    """화면에 줄 값: on(켜진 기능 id, ALL 순서), version(모르면 None), all([{id, name, path}])."""
    on, version = list(ALL), None
    path = ini_path()
    if path is not None and path.is_file():
        ini = configparser.ConfigParser(interpolation=None)
        try:
            ini.read(path, encoding="utf-8")
            version = ini.get("app", "version", fallback="").strip() or None
            if ini.has_option("app", "features"):
                chosen = {x.strip() for x in ini.get("app", "features").split(",")}
                on = [i for i in ALL if i in chosen]
        except (configparser.Error, OSError, UnicodeError):  # 깨진 파일이면 없는 것으로 본다
            on, version = list(ALL), None
    return {"on": on, "version": version, "all": [{"id": i, "name": n, "path": p} for i, (n, p) in ALL.items()]}


def check(ids):
    """고른 기능 id 목록을 검사해 ALL 순서로 돌려준다. 모르는 id나 빈 목록은 ValueError."""
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise ValueError("기능 목록이 올바르지 않아요.")
    unknown = [i for i in ids if i not in ALL]
    if unknown:
        raise ValueError(f"모르는 기능이에요: {', '.join(unknown)}")
    if not ids:
        raise ValueError("기능을 하나 이상 고르세요.")
    return [i for i in ALL if i in ids]


def fetch(url, out):
    """url(https)을 받아 out(바이너리 파일)에 쓴다. LIMIT을 넘으면 ValueError."""
    got = 0
    with urllib.request.urlopen(url, timeout=WAIT) as res:
        while chunk := res.read(1024 * 1024):
            got += len(chunk)
            if got > LIMIT:
                raise ValueError("받는 파일이 너무 커서 멈췄어요.")
            out.write(chunk)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def quit_app():
    """창을 닫아 desktop.py가 정리하며 끝나게 한다. 닫히지 않으면 10초 뒤 강제로 끝낸다."""
    threading.Timer(10, os._exit, (0,)).start()
    try:
        import webview
        for window in list(webview.windows):
            window.destroy()
    except Exception:
        os._exit(0)


def quit_unless_failed(setup):
    """설치 프로그램이 벌써 실패로 끝났으면(관리자 확인 거절 등) 앱을 그대로 두고, 아니면 닫는다."""
    if setup.poll() in (None, 0):
        quit_app()


def size(folder):
    """folder 안 파일 크기의 합(바이트). 없으면 0."""
    return sum(f.stat().st_size for f in Path(folder).rglob("*") if f.is_file())


def install(ids):
    """ids 기능만 남도록 설치 프로그램을 받아 실행한다. 실행하면 앱은 곧 닫힌다. 안내 글을 돌려준다.
    수집 중이거나, 수집을 빼는데 예약이 남았거나, 설치 드라이브 공간이 모자라면 받기 전에 거절한다."""
    ids = check(ids)
    path = ini_path()
    if path is None:
        raise ValueError("개발 실행에서는 기능을 바꿀 수 없어요.")
    version = state()["version"]
    if not version or not VERSION.fullmatch(version):
        raise ValueError("설치된 버전을 알 수 없어요(features.ini가 없어요). 설치 파일을 다시 받아 설치해 주세요.")
    if version == "0.0.0":  # 태그 없이 만든 빌드: 그 버전의 릴리스가 없다
        raise ValueError("시험 빌드라 기능을 바꿀 수 없어요. 릴리스 설치 파일로 설치하세요.")
    collect_job = lazy.loaded("collect.collect_job")  # 올라와 있지 않으면 수집이 돈 적이 없다(desktop.py closing과 같게)
    running = getattr(collect_job, "running", None)
    if running is not None and running():  # 앱이 닫히면 10초 뒤 강제로 끝나 수집 정리가 끊긴다
        raise ValueError(collect_job.RUNNING)
    if "collect" not in ids:
        from integrations import schedule
        count = len(schedule.schedules())  # 프로그램 안 예약과 Windows 예약이 모두 여기에 있다
        if count:
            raise ValueError(f"수집을 빼려면 수집 화면에서 예약 {count}개를 먼저 지우세요.")
    if shutil.disk_usage(path.parent).free < size(path.parent / "_internal") * 1.5:
        raise ValueError("설치할 드라이브의 빈 공간이 모자라요. 공간을 비운 뒤 다시 시도하세요.")
    base = f"{RELEASES}/v{version}/DataLoupe-setup.exe"
    folder = scratch.make()
    setup = os.path.join(folder, "DataLoupe-setup.exe")
    try:
        text = io.BytesIO()
        fetch(base + ".sha256", text)
        want = text.getvalue().decode("ascii", "replace").split()
        with open(setup, "wb") as out:  # 받는 중
            fetch(base, out)
        if not want or want[0].lower() != sha256(setup):  # 확인 중
            raise ValueError("받은 설치 파일이 릴리스의 지문과 달라서 실행하지 않았어요. 잠시 뒤 다시 시도하세요.")
    except OSError as e:  # URLError·HTTPError도 OSError다
        scratch.remove(folder)
        if e.errno == errno.ENOSPC:
            raise ValueError("임시 폴더가 있는 드라이브의 공간이 모자라 설치 파일을 받지 못했어요. 공간을 비운 뒤 다시 시도하세요.") from e
        raise ValueError("인터넷(프록시)에 닿지 못했어요. 설치 파일을 직접 받아 [사용자 지정]으로 다시 설치하세요.") from e
    except BaseException:
        scratch.remove(folder)
        raise
    # 권한 스위치는 달지 않는다: 설치 프로그램이 이전 설치 방식(나만·모두)을 따른다(UsePreviousPrivileges). 모두를 위한 설치면 관리자 확인 창이 뜬다.
    run = subprocess.Popen([setup, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL", f"/COMPONENTS={','.join(ids)}", f"/DIR={path.parent}", "/RELAUNCH"])
    try:
        code = run.wait(timeout=2)  # 바로 실패하면(시작 못 함) 앱을 닫지 않고 알린다
    except subprocess.TimeoutExpired:
        code = None
    if code:
        scratch.remove(folder)
        raise ValueError(f"설치 프로그램이 바로 끝났어요(코드 {code}). 관리자 확인을 거절했거나 시작하지 못했어요. 앱은 그대로예요.")
    threading.Timer(1, quit_unless_failed, (run,)).start()  # 응답이 화면에 닿은 뒤 닫는다
    return "설치 프로그램을 실행했어요. 앱이 닫혔다가 설치가 끝나면 다시 열려요."
