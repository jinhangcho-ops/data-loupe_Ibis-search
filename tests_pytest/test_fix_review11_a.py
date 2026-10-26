"""11차 묶음 A: 설정 창 [적용](기능 바꾸기)의 거절 조건·실패 처리, 임시 폴더 표식, 빌드 워크플로 고정.
네트워크는 쓰지 않는다(받기 실패는 닫힌 로컬 포트로). 설치 프로그램 대신 바로 끝나는 python을 띄운다. 창은 띄우지 않는다."""
import hashlib
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

from conftest import ROOT
from core import features, scratch, settings

SETUP = b"setup bytes"
POPEN = subprocess.Popen  # 진짜 Popen(시험이 features.subprocess.Popen을 바꾸기 전)
PYTHON = sys.executable  # installed가 sys.executable을 바꾸기 전
GOOD = {"DataLoupe-setup.exe": SETUP, "DataLoupe-setup.exe.sha256": hashlib.sha256(SETUP).hexdigest().encode()}


@pytest.fixture
def installed(tmp_path, monkeypatch, folder):
    """frozen exe가 tmp_path에 버전 1.2.3으로 설치된 모양(설정 폴더는 비어 있다)."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "DataLoupe.exe"))
    (tmp_path / "features.ini").write_text("[app]\nversion=1.2.3\nfeatures=query,collect\n", encoding="utf-8")
    return tmp_path


class Spy:
    """fetch·Popen·Timer를 바꿔 부른 것을 모은다. code: 띄운 설치 프로그램 대신 끝날 python의 종료 코드(None이면 띄운 척만)."""

    def __init__(self, monkeypatch, files=GOOD, code=None):
        self.urls, self.run, self.timers = [], [], []

        def fetch(url, out):
            self.urls.append(url)
            out.write(files[url.rsplit("/", 1)[1]])

        def popen(cmd, *a, **k):
            self.run.append(cmd)
            if code is None:
                return types.SimpleNamespace(poll=lambda: None, wait=lambda timeout=None: (_ for _ in ()).throw(subprocess.TimeoutExpired(cmd, timeout)))
            return POPEN([PYTHON, "-c", f"raise SystemExit({code})"])
        monkeypatch.setattr(features, "fetch", fetch)
        monkeypatch.setattr(features.subprocess, "Popen", popen)

    def clean(self):
        for cmd in self.run:
            scratch.remove(Path(cmd[0]).parent)


# ---------- 11A1 수집 중이면 거절 ----------

def test_install_refused_while_collecting(installed, monkeypatch):
    from collect import collect_job
    stop = threading.Event()
    thread = threading.Thread(target=stop.wait, daemon=True)
    thread.start()
    monkeypatch.setitem(collect_job.current, "thread", thread)
    spy = Spy(monkeypatch)
    try:
        with pytest.raises(ValueError, match="수집이 돌고"):
            features.install(["query", "collect"])
    finally:
        stop.set()
        thread.join()
    assert spy.urls == [] and spy.run == []


# ---------- 11A2·11A4 명령줄: 권한 스위치 없음, /NOCANCEL ----------

def test_command_has_no_privilege_switch_and_no_cancel(installed, monkeypatch):
    spy = Spy(monkeypatch)
    features.install(["query", "collect"])
    (cmd,) = spy.run
    assert cmd[1:] == ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL", "/CLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS", "/COMPONENTS=query,collect", f"/DIR={installed}", "/RELAUNCH"]
    assert not hasattr(features, "writable")
    spy.clean()


# ---------- 11A3 설치 프로그램이 바로 실패하면 앱을 닫지 않는다 ----------

def test_install_fails_fast_keeps_app_open(installed, monkeypatch):
    spy = Spy(monkeypatch, code=2)
    with pytest.raises(ValueError, match="코드 2"):
        features.install(["query", "collect"])
    spy.clean()


def test_app_does_not_close_itself_while_setup_runs(installed, monkeypatch):
    """관리자 확인을 늦게 거절해도 앱이 닫힌 채 남지 않게, 앱은 스스로 닫지 않고 설치 프로그램이 파일을 바꾸기 직전에 닫는다(Restart Manager)."""
    spy = Spy(monkeypatch)
    text = features.install(["query", "collect"])
    (cmd,) = spy.run
    assert "/CLOSEAPPLICATIONS" in cmd and "/NORESTARTAPPLICATIONS" in cmd and cmd[-1] == "/RELAUNCH"  # 다시 열기는 /RELAUNCH 하나
    assert not hasattr(features, "quit_unless_failed") and not hasattr(features, "quit_app") and "거절하면 앱은 그대로" in text
    spy.clean()


def test_setup_pending_blocks_second_install_and_new_collect(installed, monkeypatch):
    """설치 프로그램이 앱을 닫기 전(관리자 확인을 기다리는 동안)에는 두 번째 설치와 새 수집을 막는다(설치가 수집을 끊지 않게)."""
    from collect import collect_job
    spy = Spy(monkeypatch)
    features.install(["query", "collect"])
    with pytest.raises(ValueError, match="설치 프로그램이 진행 중"):
        features.install(["query", "collect"])
    for start in (collect_job.start, collect_job.start_load):
        with pytest.raises(ValueError, match="설치 프로그램이 진행 중"):
            start({})
    assert len(spy.run) == 1
    spy.clean()


# ---------- 11A4 설치 드라이브 공간 ----------

def test_install_refused_when_disk_is_short(installed, monkeypatch):
    (installed / "_internal").mkdir()
    (installed / "_internal" / "big.bin").write_bytes(b"x" * 1000)
    monkeypatch.setattr(features.shutil, "disk_usage", lambda p: types.SimpleNamespace(free=1400))
    spy = Spy(monkeypatch)
    with pytest.raises(ValueError, match="공간"):
        features.install(["query", "collect"])
    assert spy.urls == []
    monkeypatch.setattr(features.shutil, "disk_usage", lambda p: types.SimpleNamespace(free=1600))
    features.install(["query", "collect"])
    spy.clean()


# ---------- 11A5 시험 빌드·받기 실패 ----------

def test_install_refused_for_untagged_build(installed, monkeypatch):
    (installed / "features.ini").write_text("[app]\nversion=0.0.0\n", encoding="utf-8")
    spy = Spy(monkeypatch)
    with pytest.raises(ValueError, match="시험 빌드"):
        features.install(["query"])
    assert spy.urls == []


def test_install_download_failure_is_plain(installed, monkeypatch):
    monkeypatch.setattr(features, "RELEASES", "http://127.0.0.1:9")  # 닫힌 로컬 포트: 진짜 urlopen이 URLError를 낸다
    monkeypatch.setattr(features, "WAIT", 5)
    seen = []
    real_make = scratch.make
    monkeypatch.setattr(features.scratch, "make", lambda: seen.append(real_make()) or seen[-1])
    with pytest.raises(ValueError, match="인터넷"):
        features.install(["query", "collect"])
    assert not Path(seen[0]).exists()


def test_install_disk_full_while_downloading(installed, monkeypatch):
    def full(url, out):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(features, "fetch", full)
    with pytest.raises(ValueError, match="공간"):
        features.install(["query", "collect"])


# ---------- 11A6 수집을 빼는데 예약이 남아 있으면 거절 ----------

def test_install_refused_when_dropping_collect_with_schedules(installed, monkeypatch):
    settings.write("schedules.json", {"a": {"every": "daily", "inapp": True}, "b": {"every": "daily", "windows": True, "task": "x"}})
    spy = Spy(monkeypatch)
    with pytest.raises(ValueError, match="예약 2개"):
        features.install(["query"])
    assert spy.urls == []
    features.install(["query", "collect"])  # 수집을 남기면 예약이 있어도 된다
    spy.clean()


# ---------- 11A7 잠긴 파일 때문에 남은 임시 폴더는 표식을 지킨다 ----------

def test_remove_keeps_mark_when_folder_stays():
    folder = scratch.make()
    held = open(Path(folder, "setup.exe"), "wb")  # Windows에서 열린 파일은 지울 수 없다
    try:
        scratch.remove(folder)
        assert Path(folder).is_dir() and Path(folder, scratch.MARK).read_text(encoding="ascii") == scratch.stamp()
    finally:
        held.close()
    scratch.remove(folder)
    assert not Path(folder).exists()


# ---------- 11F 빌드 워크플로 ----------

def test_workflows_pin_python_and_tools():
    from core import drivers
    build = (ROOT / ".github" / "workflows" / "build-exe.yml").read_text(encoding="utf-8")
    test = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    for text in (build, test):
        assert f'python-version: "{drivers.FROZEN_PYTHON}"' in text and "-c constraints.txt" in text
    assert "runs-on: windows-2025" in build and "choco install innosetup --version=" in build
    pins = {name.lower(): version for name, version in (line.split("==") for line in (ROOT / "constraints.txt").read_text(encoding="utf-8").splitlines() if "==" in line and not line.startswith("#"))}
    for path in ("requirements.txt", "requirements-dev.txt"):
        for line in (ROOT / path).read_text(encoding="utf-8").splitlines():
            if "==" in line and not line.startswith("#"):
                name, version = line.split("==")
                assert pins[name.split("[")[0].lower()] == version  # requirements와 어긋나지 않는다
