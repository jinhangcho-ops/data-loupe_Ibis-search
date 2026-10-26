"""기능 선택 설치(묶음 A): features.ini 읽기, 화면 상수, 설정 창의 설치(지문 확인·명령줄), 설치 파일(iss) 글 검사, Kafka 안내, 화면 숨김(node).
다운로드·subprocess·종료는 가짜로 바꾼다. 네트워크는 쓰지 않고 창도 띄우지 않는다."""
import hashlib
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from conftest import ROOT
from core import features, scratch

ALL = ["query", "prep", "dashboard", "collect", "integrations"]


@pytest.fixture
def installed(tmp_path, monkeypatch, folder):
    """frozen exe가 tmp_path에 설치된 모양: exe 옆에 features.ini(version, features)를 쓰는 함수를 돌려준다. 설정 폴더(예약)는 비운다."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "DataLoupe.exe"))

    def write(text):
        (tmp_path / "features.ini").write_text(text, encoding="utf-8")
        return tmp_path
    return write


# ---------- A3 features.ini 읽기 ----------

def test_state_reads_ini(installed):
    installed("[app]\nversion=1.2.3\nfeatures=query,collect\n")
    got = features.state()
    assert got["on"] == ["query", "collect"] and got["version"] == "1.2.3"
    assert [f["id"] for f in got["all"]] == ALL and got["all"][0] == {"id": "query", "name": "쿼리", "path": "/search"}


def test_state_without_file_is_everything(installed):
    got = features.state()
    assert got["on"] == ALL and got["version"] is None


def test_state_when_not_frozen_ignores_ini(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    (tmp_path / "features.ini").write_text("[app]\nversion=9\nfeatures=query\n", encoding="utf-8")
    monkeypatch.delattr(sys, "frozen", raising=False)
    got = features.state()
    assert got["on"] == ALL and got["version"] is None


def test_state_broken_ini_is_everything(installed):
    installed("이건 ini가 아니다\n")
    got = features.state()
    assert got["on"] == ALL and got["version"] is None


def test_screen_constants_carry_features(installed):
    from core import server
    installed("[app]\nversion=1.0.0\nfeatures=prep\n")
    for folder in ("core", "query", "collect", None):
        assert server.constants(folder)["features"]["on"] == ["prep"]


# ---------- A5 기능 목록 검사 ----------

@pytest.mark.parametrize("bad, text", [(["query", "nope"], "모르는 기능"), ([], "하나 이상"), ("query", "올바르지"), (None, "올바르지"), ([1], "올바르지")])
def test_check_rejects(bad, text):
    with pytest.raises(ValueError, match=text):
        features.check(bad)


def test_check_orders_and_dedups():
    assert features.check(["collect", "query", "query"]) == ["query", "collect"]


# ---------- A5 설치: 받기·지문·명령줄 ----------

class Spy:
    """fetch·Popen·종료를 가짜로 바꿔 부른 것을 모은다. files: 주소 끝 → 받아질 바이트."""

    def __init__(self, monkeypatch, files):
        self.urls, self.run, self.timers = [], [], []

        def fetch(url, out):
            self.urls.append(url)
            out.write(files[url.rsplit("/", 1)[1]])
        monkeypatch.setattr(features, "fetch", fetch)
        monkeypatch.setattr(features.subprocess, "Popen", lambda cmd, *a, **k: self.run.append(cmd) or Running(cmd))
        monkeypatch.setattr(features, "threading", types.SimpleNamespace(Timer=lambda delay, fn, args=(): types.SimpleNamespace(start=lambda: self.timers.append(fn))))


class Running:
    """아직 도는 설치 프로그램."""

    def __init__(self, cmd):
        self.cmd = cmd

    def poll(self):
        return None

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired(self.cmd, timeout)


SETUP = b"setup bytes"


def test_install_runs_setup_when_hash_matches(installed, monkeypatch, tmp_path):
    installed("[app]\nversion=1.2.3\nfeatures=query\n")
    spy = Spy(monkeypatch, {"DataLoupe-setup.exe": SETUP, "DataLoupe-setup.exe.sha256": hashlib.sha256(SETUP).hexdigest().upper().encode() + b"  DataLoupe-setup.exe\n"})
    text = features.install(["collect", "query"])
    base = "https://github.com/jinhangcho-ops/data-loupe_Ibis-search/releases/download/v1.2.3/DataLoupe-setup.exe"
    assert sorted(spy.urls) == [base, base + ".sha256"]
    (cmd,) = spy.run
    assert Path(cmd[0]).name == "DataLoupe-setup.exe" and Path(cmd[0]).read_bytes() == SETUP
    assert cmd[1:] == ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL", "/COMPONENTS=query,collect", f"/DIR={tmp_path}", "/RELAUNCH"]
    assert spy.timers == [features.quit_unless_failed] and "다시 열려요" in text  # 응답이 나간 뒤 앱을 닫는다
    scratch.remove(Path(cmd[0]).parent)


def test_install_refuses_when_hash_differs(installed, monkeypatch):
    installed("[app]\nversion=1.2.3\nfeatures=query\n")
    spy = Spy(monkeypatch, {"DataLoupe-setup.exe": SETUP, "DataLoupe-setup.exe.sha256": hashlib.sha256(b"other").hexdigest().encode() + b"  x\n"})
    seen = []
    real_make = scratch.make
    monkeypatch.setattr(features.scratch, "make", lambda: seen.append(real_make()) or seen[-1])
    with pytest.raises(ValueError, match="지문"):
        features.install(["query"])
    assert spy.run == [] and spy.timers == []
    assert not Path(seen[0]).exists()  # 받은 파일은 지운다


def test_install_refuses_empty_fingerprint_file(installed, monkeypatch):
    installed("[app]\nversion=1.2.3\n")
    spy = Spy(monkeypatch, {"DataLoupe-setup.exe": SETUP, "DataLoupe-setup.exe.sha256": b""})
    with pytest.raises(ValueError, match="지문"):
        features.install(["query"])
    assert spy.run == []


def test_install_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    spy = Spy(monkeypatch, {})
    with pytest.raises(ValueError, match="개발 실행"):
        features.install(["query"])
    assert spy.urls == [] and spy.run == []


def test_install_without_version(installed, monkeypatch):
    spy = Spy(monkeypatch, {})
    with pytest.raises(ValueError, match="버전"):
        features.install(["query"])
    installed("[app]\nversion=../evil\n")  # 주소를 바꾸는 버전은 쓰지 않는다
    with pytest.raises(ValueError, match="버전"):
        features.install(["query"])
    assert spy.urls == []


def test_fetch_stops_over_limit(monkeypatch):
    class Res:
        def __init__(self):
            self.left = 3

        def read(self, n):
            self.left -= 1
            return b"x" * 1024 if self.left >= 0 else b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(features.urllib.request, "urlopen", lambda url, timeout: Res())
    monkeypatch.setattr(features, "LIMIT", 2048)
    import io
    with pytest.raises(ValueError, match="너무 커"):
        features.fetch("https://x/y", io.BytesIO())


def test_install_route(client, installed, monkeypatch):
    got = client.post("/api/features/install", json={"features": []})
    assert got.status_code == 400 and "하나 이상" in got.get_json()["error"]
    got = client.post("/api/features/install", json={"features": ["query"]})  # 버전을 모른다
    assert got.status_code == 400 and "버전" in got.get_json()["error"]
    installed("[app]\nversion=1.0.0\n")
    spy = Spy(monkeypatch, {"DataLoupe-setup.exe": SETUP, "DataLoupe-setup.exe.sha256": hashlib.sha256(SETUP).hexdigest().encode()})
    got = client.post("/api/features/install", json={"features": ["prep"]})
    assert got.status_code == 200 and "다시 열려요" in got.get_json()["text"] and "/COMPONENTS=prep" in spy.run[0]
    scratch.remove(Path(spy.run[0][0]).parent)


def test_install_route_is_not_queued_behind_connection_work():
    from core import server
    assert "/api/features/install" in server.FREE


# ---------- A1·A2 설치 파일(iss)을 글로 ----------

ISS = (ROOT / "installer" / "DataLoupe.iss").read_text(encoding="utf-8")


def section(name):
    a = ISS.index(f"[{name}]") + len(name) + 2
    b = ISS.find("\n[", a)
    return ISS[a:b if b > 0 else None]


def test_iss_components():
    lines = [x for x in section("Components").splitlines() if x.startswith("Name:")]
    assert [x.split('"')[1] for x in lines] == ALL and all("Types: full" in x for x in lines)
    types_ = section("Types")
    assert 'Name: "full"' in types_ and "iscustom" in types_  # 기본은 전부(full), 고르면 사용자 지정


def test_iss_kafka_files_only_for_collect():
    files = [x for x in section("Files").splitlines() if x.startswith("Source:")]
    kafka = [x for x in files if "confluent_kafka" in x.split("Excludes")[0]]
    assert len(kafka) == 2 and all("Components: collect" in x for x in kafka)
    (main,) = [x for x in files if x.startswith('Source: "dist\\DataLoupe\\*"')]
    assert "confluent_kafka" in main and "Excludes" in main and "Components" not in main  # 나머지는 늘 설치
    assert all("Components" not in x for x in files if x not in kafka)
    assert 'Name: "{app}\\_internal"' in section("InstallDelete")  # 업그레이드 때 먼저 지운다


def test_iss_writes_features_ini():
    ini = section("INI")
    assert 'Filename: "{app}\\features.ini"; Section: "app"; Key: "version"; String: "{#AppVersion}"' in ini
    assert 'Key: "features"; String: "{code:SelectedFeatures}"' in ini
    code = section("Code")
    for i in ALL:
        assert f"'{i}'" in code
    assert "WizardIsComponentSelected" in code and "wpSelectComponents" in code  # 하나도 안 고르면 다음으로 못 간다
    assert 'Name: "{app}\\features.ini"' in section("UninstallDelete")


def test_iss_relaunch_only_with_switch():
    relaunch = [x for x in section("Run").splitlines() if "HasRelaunch" in x]
    assert len(relaunch) == 1 and "postinstall" not in relaunch[0] and "nowait" in relaunch[0]
    assert "'/RELAUNCH'" in section("Code") and "ParamStr" in section("Code")


# ---------- A6 Kafka 안내 ----------

def test_kafka_missing_message(monkeypatch):
    from collect import load_kafka
    monkeypatch.setitem(sys.modules, "confluent_kafka", None)  # import하면 ImportError
    item = {"servers": "x:9092", "security": "PLAINTEXT", "topic": "t", "ca": ""}
    with pytest.raises(ValueError, match="수집 기능에 들어 있어요. \\[설정\\]에서 수집 기능을 추가하세요"):
        load_kafka.producer(item, "")
    monkeypatch.setattr(load_kafka, "destination", lambda name: item)
    with pytest.raises(ValueError, match="수집 기능에 들어 있어요") as e:  # 접속 확인에서도 "접속하지 못했습니다"로 덮지 않는다
        load_kafka.check("x", "")
    assert "접속하지 못했습니다" not in str(e.value)


# ---------- A4 화면 숨김(node) ----------

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
COMMON = (ROOT / "static" / "core" / "common.js").read_text(encoding="utf-8")
APPLY = COMMON[COMMON.index("function applyFeatures()"):COMMON.index("applyFeatures();\n")]


def run_apply(tmp_path, on, pathname):
    script = tmp_path / "t.js"
    script.write_text(r"""
const hidden = {}, notes = [];
const items = ["query", "prep", "dashboard", "collect", "integrations"].map(id => ({ dataset: { feature: id }, classList: { toggle: (c, f) => { hidden[id] = f; } } }));
globalThis.SERVER = { features: { on: %s, all: [["query", "/search"], ["prep", "/prep"], ["dashboard", "/dashboard"], ["collect", "/collect"], ["integrations", "/integrations"]].map(([id, path]) => ({ id, path })) } };
globalThis.location = { pathname: %s };
globalThis.document = { querySelectorAll: () => items, querySelector: () => ({ after: n => notes.push(n.textContent) }) };
globalThis.el = (tag, props) => props;
""" % (json.dumps(on), json.dumps(pathname)) + APPLY + """
applyFeatures();
console.log(JSON.stringify({ hidden, notes }));
""", encoding="utf-8")
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@needs_node
def test_hides_features_not_installed(tmp_path):
    got = run_apply(tmp_path, ["query", "prep"], "/search")
    assert got["hidden"] == {"query": False, "prep": False, "dashboard": True, "collect": True, "integrations": True}
    assert got["notes"] == []  # 설치한 기능의 화면에는 안내가 없다


@needs_node
def test_direct_entry_to_missing_feature_shows_notice(tmp_path):
    got = run_apply(tmp_path, ["query"], "/collect")
    assert got["notes"] == ["이 기능은 설치하지 않았어요. [설정]에서 추가할 수 있어요."]
    assert run_apply(tmp_path, ["query"], "/")["notes"] == []  # 첫 화면은 기능이 아니다


def test_screens_mark_each_feature():
    status = (ROOT / "static" / "core" / "status.html").read_text(encoding="utf-8")
    home = (ROOT / "static" / "core" / "home.html").read_text(encoding="utf-8")
    for i, path in (("query", "/search"), ("prep", "/prep"), ("dashboard", "/dashboard"), ("collect", "/collect"), ("integrations", "/integrations")):
        assert f'href="{path}" data-feature="{i}"' in status and f'data-go="{path}" data-feature="{i}"' in home
    assert 'id="feat-open"' in status and 'id="feat-apply"' in status and "/api/features/install" in COMMON


# ---------- 새 버전 알림 ----------

class Latest:
    """releases/latest가 넘겨준 주소(geturl)만 흉내 낸다."""

    def __init__(self, tag):
        self.url = f"https://github.com/jinhangcho-ops/data-loupe_Ibis-search/releases/tag/{tag}"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def geturl(self):
        return self.url


NOTES = "### 추가\n- 새 버전 알림\n\n### 삭제\n- 없음\n"


def ask(monkeypatch, tag, notes=NOTES):
    """releases/latest는 tag로, 릴리스 노트 받기는 notes로(None이면 없는 파일) 흉내 낸다. 물은 주소를 모은다."""
    asked = []
    monkeypatch.setattr(features, "UPDATE", {})
    monkeypatch.setattr(features.urllib.request, "urlopen", lambda url, timeout=None: asked.append(url) or Latest(tag))

    def fetch(url, out):
        asked.append(url)
        if notes is None:
            raise OSError("404")
        out.write(notes.encode("utf-8"))
    monkeypatch.setattr(features, "fetch", fetch)
    return asked


def test_update_finds_newer_release_once(installed, monkeypatch):
    installed("[app]\nversion=1.9.0\nfeatures=query\n")
    asked = ask(monkeypatch, "v1.10.0")
    assert features.update() == {"current": "1.9.0", "latest": "1.10.0", "newer": True, "notes": NOTES, "check": True}  # 1.10은 1.9보다 새것(글자 비교가 아님)
    features.update()
    assert asked == [features.LATEST, f"{features.RELEASES}/v1.10.0/RELEASE_NOTES.md"]  # 실행마다 한 번만 묻는다


def test_update_without_notes_still_tells(installed, monkeypatch):
    installed("[app]\nversion=1.0.0\nfeatures=query\n")
    ask(monkeypatch, "v1.1.0", notes=None)
    got = features.update()
    assert got["newer"] and got["notes"] is None


@pytest.mark.parametrize("tag", ["v1.9.0", "v1.8.5", "vbad/../x", "nightly"])
def test_update_not_newer(installed, monkeypatch, tag):
    installed("[app]\nversion=1.9.0\nfeatures=query\n")
    asked = ask(monkeypatch, tag)
    got = features.update()
    assert got["newer"] is False and got["notes"] is None and asked == [features.LATEST]  # 새것이 아니면 노트를 받지 않는다


def test_update_off_or_offline_asks_nothing_or_stays_quiet(installed, monkeypatch):
    from core import settings
    installed("[app]\nversion=1.0.0\nfeatures=query\n")
    asked = ask(monkeypatch, "v2.0.0")
    settings.save({"update_check": "off"})
    assert features.update() == {"current": "1.0.0", "latest": None, "newer": False, "notes": None, "check": False} and asked == []
    settings.save({"update_check": "on"})

    def offline(url, timeout=None):
        raise OSError("no network")
    monkeypatch.setattr(features.urllib.request, "urlopen", offline)
    assert features.update()["newer"] is False


def test_install_target_version_downloads_that_release(installed, monkeypatch, tmp_path):
    installed("[app]\nversion=1.2.3\nfeatures=query\n")
    spy = Spy(monkeypatch, {"DataLoupe-setup.exe": SETUP, "DataLoupe-setup.exe.sha256": hashlib.sha256(SETUP).hexdigest().encode() + b"  DataLoupe-setup.exe\n"})
    features.install(["query"], "1.3.0")
    assert all("/v1.3.0/" in u for u in spy.urls)
    scratch.remove(Path(spy.run[0][0]).parent)
    with pytest.raises(ValueError, match="버전"):
        features.install(["query"], "../x")


def test_changelog_sections_and_release_uses_them():
    import re
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    parts = re.split(r"^## (v[0-9.]+)\s*$", text, flags=re.M)[1:]
    assert parts, "CHANGELOG.md에 '## v버전' 절이 없어요"
    for version, body in zip(parts[::2], parts[1::2]):
        assert [h for h in re.findall(r"^### (.+)$", body, flags=re.M)] == ["추가", "개선", "삭제"], version
    flow = (ROOT / ".github" / "workflows" / "build-exe.yml").read_text(encoding="utf-8")
    assert "CHANGELOG.md" in flow and flow.count("RELEASE_NOTES.md") >= 4 and "--generate-notes" not in flow
