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
import urllib.request
from pathlib import Path

import lazy
from core import scratch

ALL = {"query": ("쿼리", "/search"), "prep": ("분석", "/prep"), "dashboard": ("대시보드", "/dashboard"),
       "collect": ("수집", "/collect"), "integrations": ("연동", "/integrations")}  # id: (이름, 화면 주소). 설치 파일 [Components]의 이름과 같다.
RELEASES = "https://github.com/jinhangcho-ops/data-loupe_Ibis-search/releases/download"
LATEST = "https://github.com/jinhangcho-ops/data-loupe_Ibis-search/releases/latest"  # 최신 릴리스 쪽으로 넘겨준다(…/releases/tag/v1.2.3)
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


def numbers(version):
    """"1.10.2" → (1, 10, 2). 숫자가 아닌 조각이 있으면 None(견주지 않는다)."""
    parts = version.split(".")
    return tuple(int(p) for p in parts) if all(p.isdigit() for p in parts) else None


UPDATE = {}  # 이번 실행에서 한 번 물은 결과(앱을 열 때마다 한 번)
NOTES_LIMIT = 50_000  # 화면에 보일 릴리스 노트의 최대 바이트
SETUP = {"run": None}  # 이번 실행에서 띄운 설치 프로그램(설치가 앱을 닫기 전까지 앱은 그대로 쓸 수 있다)
INSTALLING = "설치 프로그램이 진행 중이에요. 설치가 끝나거나 관리자 확인을 거절한 뒤 다시 시도하세요."


def installing():
    run = SETUP["run"]
    return run is not None and run.poll() is None


def update():
    """새 버전 {current, latest, newer, notes(새 버전의 RELEASE_NOTES.md 글, 못 받으면 None), check(확인을 켰는지)}. 개발 실행·시험 빌드·확인을 끈 경우·인터넷이 안 되면 newer=False.
    GitHub에는 최신 릴리스 주소를 여는 요청과, 새 버전이 있으면 그 릴리스 노트를 받는 요청만 보낸다(데이터 값은 보내지 않는다)."""
    from core import settings
    current, on = state()["version"], settings.get("update_check") != "off"
    if ini_path() is None or not current or current == "0.0.0" or not on:
        return {"current": current, "latest": None, "newer": False, "notes": None, "check": on}
    if "latest" not in UPDATE:
        try:
            with urllib.request.urlopen(LATEST, timeout=WAIT) as res:
                tag = res.geturl().rstrip("/").rsplit("/", 1)[-1]
            UPDATE["latest"] = tag[1:] if tag.startswith("v") and VERSION.fullmatch(tag[1:]) else None
        except OSError:  # 인터넷(프록시)이 안 되면 조용히 넘어간다
            UPDATE["latest"] = None
    latest = UPDATE["latest"]
    now, new = numbers(current), numbers(latest) if latest else None
    newer = bool(now and new and new > now)
    if newer and "notes" not in UPDATE:  # 새 버전이 있을 때만 그 릴리스 노트(추가·개선·삭제)를 받는다
        try:
            text = io.BytesIO()
            fetch(f"{RELEASES}/v{latest}/RELEASE_NOTES.md", text)
            UPDATE["notes"] = text.getvalue()[:NOTES_LIMIT].decode("utf-8", "replace") or None
        except (OSError, ValueError):  # 노트가 없는 옛 릴리스·인터넷 오류면 노트 없이 알린다
            UPDATE["notes"] = None
    return {"current": current, "latest": latest, "newer": newer, "notes": UPDATE.get("notes") if newer else None, "check": on}


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


def size(folder):
    """folder 안 파일 크기의 합(바이트). 없으면 0."""
    return sum(f.stat().st_size for f in Path(folder).rglob("*") if f.is_file())


def install(ids, target=None):
    """ids 기능만 남도록 설치 프로그램(target이 있으면 그 버전, 없으면 지금 버전)을 받아 실행한다. 설치 프로그램이 파일을 바꾸기 직전에 앱을 닫는다. 안내 글을 돌려준다.
    수집 중이거나, 수집을 빼는데 예약이 남았거나, 설치 드라이브 공간이 모자라면 받기 전에 거절한다."""
    if installing():  # 두 번 누르면 설치 프로그램 둘이 같은 폴더에 설치한다
        raise ValueError(INSTALLING)
    ids = check(ids)
    if target is not None and not (isinstance(target, str) and VERSION.fullmatch(target)):
        raise ValueError("업데이트할 버전이 올바르지 않아요.")
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
    if running is not None and running():  # 설치 프로그램이 앱을 닫으면 수집 정리가 끊긴다
        raise ValueError(collect_job.RUNNING)
    from integrations import schedule
    if any(r["result"] == "도는 중" for r in schedule.runs_view()["runs"]):  # Windows 예약(DataLoupe.exe --run)은 다른 프로세스라 running()이 모른다
        raise ValueError("예약 수집이 도는 중이에요. 끝난 뒤 다시 시도하세요.")
    if "collect" not in ids:
        count = len(schedule.schedules())  # 프로그램 안 예약과 Windows 예약이 모두 여기에 있다
        if count:
            raise ValueError(f"수집을 빼려면 수집 화면에서 예약 {count}개를 먼저 지우세요.")
    if shutil.disk_usage(path.parent).free < size(path.parent / "_internal") * 1.5:
        raise ValueError("설치할 드라이브의 빈 공간이 모자라요. 공간을 비운 뒤 다시 시도하세요.")
    base = f"{RELEASES}/v{target or version}/DataLoupe-setup.exe"
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
    # 앱은 스스로 닫지 않는다: 설치 프로그램이 파일을 바꾸기 직전에 닫는다(/CLOSEAPPLICATIONS, Restart Manager). 그래서 관리자 확인을 늦게 거절해도 앱은 그대로다.
    # 다시 여는 것은 /RELAUNCH 하나만(Restart Manager의 다시 열기는 끈다: 두 번 열리지 않게).
    run = subprocess.Popen([setup, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL", "/CLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS",
                            f"/COMPONENTS={','.join(ids)}", f"/DIR={path.parent}", "/RELAUNCH"])
    try:
        code = run.wait(timeout=2)  # 바로 실패하면(시작 못 함) 앱을 닫지 않고 알린다
    except subprocess.TimeoutExpired:
        code = None
    if code:
        scratch.remove(folder)
        raise ValueError(f"설치 프로그램이 바로 끝났어요(코드 {code}). 관리자 확인을 거절했거나 시작하지 못했어요. 앱은 그대로예요.")
    SETUP["run"] = run  # 앱이 닫히기 전까지 새 수집·두 번째 설치를 막는다(collect_job.start)
    return "설치 프로그램을 실행했어요. 설치가 파일을 바꾸기 직전에 앱이 닫히고, 끝나면 다시 열려요(관리자 확인을 거절하면 앱은 그대로예요)."
