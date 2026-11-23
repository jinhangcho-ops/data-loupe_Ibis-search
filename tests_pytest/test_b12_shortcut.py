"""B12 대시보드 바로가기(plans/dash-blocks.md "B12 세부 명세", 사용자 답 2차 반영): 바로가기 프로세스의 요청 막기(shortcut_gate), 실행 인자·창 제목, 아이콘·실행 명령, .lnk 쓰기,
기억한 연결 찾기·열기, 칸 잠금, 라우트. 연결·대화상자는 가짜로 바꿔 끼우고 데이터·이름은 시험 안에서 지어낸다(창·바깥 요청 없음)."""
import inspect
import subprocess
import sys
import uuid
from pathlib import Path

import psutil
import pytest
from werkzeug.exceptions import Forbidden

from core import dash_store
from core import drivers
from core import filelock
from core import server
from core import source
from core import use_counts
from dashboard import dash_app
from dashboard import dash_files
from dashboard import dash_pull
from dashboard import shortcut
from query import search_app  # noqa: F401  /api/images 라우트를 단다(SHORTCUT_OPEN이 실제 경로인지 볼 때)

ROOT = Path(__file__).resolve().parent.parent
BLOCKED = [("GET", "/search"), ("GET", "/prep"), ("GET", "/dashboard/build"), ("GET", "/"), ("POST", "/api/dash/marks/set"), ("POST", "/api/settings"),
           ("POST", "/api/dash/pull/full"), ("POST", "/api/dash/last"), ("POST", "/api/draft"), ("POST", "/api/dash/settings"), ("POST", "/api/dash/delete"),
           ("POST", "/api/dash/shortcut"), ("POST", "/api/connect"), ("POST", "/api/restart")]
SLOT = "0123456789abcdef"


# ---------- shortcut_gate (U119) ----------

def test_shortcut_gate_allows_view_and_blocks_rest():
    for method, path in [("GET", "/dashboard"), ("POST", "/api/dash"), ("POST", "/api/dash/open"), ("POST", "/api/dash/cards/compute"), ("POST", "/api/dash/pdf"),
                         ("GET", "/next/static/x.js"), ("GET", "/static/core/app.css")]:
        with server.app.test_request_context(path, method=method):
            assert server.shortcut_gate() is None
    for method, path in BLOCKED:
        with server.app.test_request_context(path, method=method):
            with pytest.raises(Forbidden) as e:
                server.shortcut_gate()
            assert e.value.description == server.SHORTCUT_SHUT


def test_shortcut_open_is_real_routes_without_settings():
    rules = {(m, r.rule) for r in server.app.url_map.iter_rules() for m in r.methods}
    assert server.SHORTCUT_OPEN <= rules  # 오타·없어진 경로가 없다
    assert not set(BLOCKED) & server.SHORTCUT_OPEN  # 화면 모드는 그 창에서만(설정에 저장하지 않음)
    assert ("POST", "/api/dash") in server.SHORTCUT_OPEN  # 바로가기도 대시보드를 저장한다(사용자 답 2차 1)
    assert server.PICK_KINDS["icon"] == ("아이콘 (*.ico)", "모든 파일 (*.*)")


# ---------- dashboard/shortcut.py (U121) ----------

def test_shortcut_dash_reads_argv():
    assert shortcut.shortcut_dash(["DataLoupe.exe"]) == ("", "")
    assert shortcut.shortcut_dash(["DataLoupe.exe", "--run", "x"]) == ("", "")
    assert shortcut.shortcut_dash(["DataLoupe.exe", "--dash", "a1_b-2"]) == ("a1_b-2", "")
    assert shortcut.shortcut_dash(["x", "--dash", "d1", "folder:c:\\my data#c:/my data/a.duckdb"]) == ("d1", "folder:c:\\my data#c:/my data/a.duckdb")
    for bad in (["x", "--dash"], ["x", "--dash", ""], ["x", "--dash", "a b"], ["x", "--dash", "a" * 33], ["x", "--dash", "../x"], ["x", "--dash", "d1", ""], ["x", "--dash", "d1", "k", "more"]):
        with pytest.raises(ValueError) as e:
            shortcut.shortcut_dash(bad)
        assert str(e.value) == shortcut.BAD_ARG


def test_window_title_and_gone(folder):
    d = dash_store.save_dashboard({"name": "보기 하나", "cards": []})
    assert shortcut.window_title(d["id"]) == "보기 하나 - Data Loupe"
    with pytest.raises(ValueError) as e:
        shortcut.window_title("nothere")
    assert str(e.value) == shortcut.GONE


def test_icon_path(tmp_path, monkeypatch):
    monkeypatch.setattr(drivers, "frozen", lambda: False)
    assert shortcut.icon_path("") == str(shortcut.APP_ICON)
    exe = tmp_path / "DataLoupe.exe"
    monkeypatch.setattr(drivers, "frozen", lambda: True)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert shortcut.icon_path("") == str(exe)
    ico = tmp_path / "그림.ICO"
    ico.write_bytes(b"\x00\x00\x01\x00")
    assert shortcut.icon_path(str(ico)) == str(ico)
    (tmp_path / "그림.png").write_bytes(b"x")
    for bad in (str(tmp_path / "그림.png"), str(tmp_path / "없음.ico"), str(tmp_path), 5, None):
        with pytest.raises(ValueError) as e:
            shortcut.icon_path(bad)
        assert str(e.value) == shortcut.BAD_ICON


def test_launch_command_dev_and_frozen(tmp_path, monkeypatch):
    key = "folder:c:\\my data#c:/my data/a.duckdb"  # 띄어쓰기가 든 열쇠는 따옴표로 묶인다
    monkeypatch.setattr(drivers, "frozen", lambda: False)
    assert shortcut.launch_command("d1", "") == (sys.executable, subprocess.list2cmdline([str(shortcut.DESKTOP), "--dash", "d1"]), str(shortcut.DESKTOP.parent))
    exe = tmp_path / "앱" / "DataLoupe.exe"
    monkeypatch.setattr(drivers, "frozen", lambda: True)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert shortcut.launch_command("d1", "") == (str(exe), "--dash d1", str(exe.parent))
    assert shortcut.launch_command("d1", key) == (str(exe), '--dash d1 "folder:c:\\my data#c:/my data/a.duckdb"', str(exe.parent))
    with pytest.raises(ValueError):
        shortcut.launch_command("a b", "")


def test_write_lnk_writes_shell_link(tmp_path):
    path = tmp_path / "바로 가기.lnk"
    shortcut.write_lnk(path, sys.executable, '"C:\\x y\\desktop.py" --dash d1', str(tmp_path), str(shortcut.APP_ICON))
    data = path.read_bytes()
    assert data[:4] == b"\x4c\x00\x00\x00"  # 머리 크기(MS-SHLLINK)
    assert data[4:20] == uuid.UUID(shortcut.CLSID_SHELL_LINK).bytes_le
    assert '"C:\\x y\\desktop.py" --dash d1'.encode("utf-16-le") in data
    assert str(shortcut.APP_ICON).encode("utf-16-le") in data
    assert str(tmp_path).encode("utf-16-le") in data


def test_write_lnk_keeps_icon_when_rewriting(tmp_path):
    path = tmp_path / "보기.lnk"
    ico = tmp_path / "고른.ico"
    ico.write_bytes(b"\x00\x00\x01\x00")
    shortcut.write_lnk(path, sys.executable, "--dash d1", str(tmp_path), str(ico))
    shortcut.write_lnk(path, sys.executable, "--dash d1 url:postgres://h.invalid:5432/m|u", str(tmp_path), None)  # 고쳐 쓰기: 아이콘은 그대로
    data = path.read_bytes()
    assert "--dash d1 url:postgres://h.invalid:5432/m|u".encode("utf-16-le") in data
    assert str(ico).encode("utf-16-le") in data


def test_own_lnk_is_empty_when_not_started_from_shortcut():
    assert shortcut.own_lnk() == ""  # pytest는 바로가기로 띄우지 않았다


def test_remember_lnk_rewrites_own_shortcut(tmp_path, monkeypatch):
    written = []
    monkeypatch.setattr(server, "only_dash", "d1")
    monkeypatch.setattr(server, "only_conn", "")
    monkeypatch.setattr(shortcut, "write_lnk", lambda *a: written.append(a))
    monkeypatch.setattr(shortcut, "own_lnk", lambda: "")
    assert shortcut.remember_lnk("url:postgres://h.invalid:5432/m|u") is False  # 자기 .lnk를 모르면 고치지 않는다
    assert written == [] and server.only_conn == ""
    lnk = tmp_path / "옮긴 곳" / "보기.lnk"
    monkeypatch.setattr(shortcut, "own_lnk", lambda: str(lnk))
    assert shortcut.remember_lnk("url:postgres://h.invalid:5432/m|u") is True
    assert written == [(lnk, *shortcut.launch_command("d1", "url:postgres://h.invalid:5432/m|u"), None)]
    assert server.only_conn == "url:postgres://h.invalid:5432/m|u"


def folder_info(tmp_path: Path, name: str) -> dict:
    return {"kind": "folder", "path": str(tmp_path), "file": str(tmp_path / name)}


def remember(info: dict) -> str:
    """연결 사용 기록(usage.json)에 info를 적고 그 연결 열쇠(바로가기가 기억하는 것)를 돌려준다."""
    use_counts.use("connections", use_counts.connection_key(info), info)
    return use_counts.connection_key(info)


def test_saved_connection_finds_by_key(folder, tmp_path):
    a = folder_info(tmp_path, "a.duckdb")
    assert shortcut.saved_connection(remember(a)) == a
    assert shortcut.saved_connection("") is None  # 연결 없는 바로가기
    assert shortcut.saved_connection("url:postgres://nohost.invalid:5432/main|x") is None  # 기록에서 밀려남


@pytest.fixture
def opened(folder, monkeypatch):
    """바로가기 프로세스처럼: only_dash를 채우고 연결 바꾸기를 가짜로. 바꾼 연결 요청 값을 모은 목록을 돌려준다."""
    calls: list[dict] = []
    monkeypatch.setattr(server, "only_dash", "d1")
    monkeypatch.setattr(server, "switch", lambda info: calls.append(info) or [])
    return calls


def test_connect_dash_needs_shortcut_process(folder, monkeypatch):
    monkeypatch.setattr(server, "only_dash", "")
    with pytest.raises(ValueError) as e:
        shortcut.connect_dash({})
    assert str(e.value) == shortcut.NOT_SHORTCUT


def test_connect_dash_opens_file_connection_without_asking(opened, monkeypatch, tmp_path):
    info = folder_info(tmp_path, "a.duckdb")
    monkeypatch.setattr(server, "only_conn", remember(info))
    assert shortcut.connect_dash({}) == {"connected": True, "needs": "", "label": dash_store.source_label(info), "user": "", "remembered": False}
    assert opened == [info]
    lite = {"kind": "url", "url": "sqlite:///" + str(tmp_path / "x.db").replace("\\", "/"), "user": ""}
    monkeypatch.setattr(server, "only_conn", remember(lite))
    assert shortcut.connect_dash({})["connected"] is True
    assert opened[-1] == lite


def test_connect_dash_asks_password_for_db(opened, monkeypatch):
    info = {"kind": "url", "url": "postgres://db.example.invalid:5432/main", "user": "reader"}
    monkeypatch.setattr(server, "only_conn", remember(info))
    assert shortcut.connect_dash({}) == {"connected": False, "needs": "password", "label": "db.example.invalid/main", "user": "reader", "remembered": False}
    assert opened == []
    assert shortcut.connect_dash({"password": "pw"}) == {"connected": True, "needs": "", "label": "db.example.invalid/main", "user": "reader", "remembered": False}
    assert opened == [{**info, "password": "pw"}]
    for bad in (5, {"password": 5}, {"url": 5}, {"url": "x", "user": None}):
        with pytest.raises(ValueError) as e:
            shortcut.connect_dash(bad)
        assert str(e.value) == shortcut.BAD_FIELDS


def test_connect_dash_asks_address_when_nothing_remembered(opened, monkeypatch):
    remembered: list[str] = []
    monkeypatch.setattr(shortcut, "remember_lnk", lambda key: remembered.append(key) or True)
    monkeypatch.setattr(server, "only_conn", "")  # 연결 없는 바로가기
    assert shortcut.connect_dash({}) == {"connected": False, "needs": "address", "label": "", "user": "", "remembered": False}
    monkeypatch.setattr(server, "only_conn", "url:postgres://gone.invalid:5432/main|x")  # 기록에서 밀려남
    assert shortcut.connect_dash({"password": "pw"})["needs"] == "address"
    assert opened == [] and remembered == []
    got = shortcut.connect_dash({"url": " postgres://db.example.invalid:5432/main ", "user": "reader", "password": "pw"})
    assert got == {"connected": True, "needs": "", "label": "db.example.invalid/main", "user": "reader", "remembered": True}
    db = {"kind": "url", "url": "postgres://db.example.invalid:5432/main", "user": "reader"}
    assert opened == [{**db, "password": "pw"}]
    assert remembered == [use_counts.connection_key(db)]  # 비밀번호 없는 열쇠만 바로가기에
    with pytest.raises(ValueError) as e:
        shortcut.connect_dash({"url": "  "})
    assert str(e.value) == shortcut.NO_URL


def test_connect_dash_reports_lnk_rewrite_failure_after_connecting(opened, monkeypatch):
    def refuse(key: str) -> bool:
        raise PermissionError("Access is denied")
    monkeypatch.setattr(shortcut, "remember_lnk", refuse)
    monkeypatch.setattr(server, "only_conn", "")
    with pytest.raises(PermissionError):  # 고쳐 쓰기 실패는 그대로 오른다(연결은 된 채)
        shortcut.connect_dash({"url": "postgres://db.example.invalid:5432/main", "user": "", "password": ""})
    assert len(opened) == 1


# ---------- 칸 잠금 (U120) ----------

def other_process_holds(slot: str) -> Path:
    """살아 있는 다른 프로세스(이 pytest의 부모)가 칸 잠금을 잡은 것처럼 잠금 파일을 둔다."""
    parent = psutil.Process().parent()
    path = filelock.lock_path(dash_files.SLOT_LOCK.format(slot=slot))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{parent.pid} {parent.create_time()}", encoding="ascii")
    return path


def test_slot_held_refuses_other_process_and_nests_in_own(folder):
    with dash_files.LOCK:
        with dash_files.slot_held(SLOT):
            with dash_files.slot_held(SLOT):  # 이 프로세스 안에서 다시 잡으면 그대로
                pass
            assert SLOT in dash_files.HELD  # 안쪽이 끝나도 바깥 잠금은 남는다
        assert SLOT not in dash_files.HELD
        held = other_process_holds(SLOT)
        with pytest.raises(ValueError) as e:
            with dash_files.slot_held(SLOT):
                pass
        assert str(e.value) == dash_files.PULL_BUSY
        held.unlink()
        with dash_files.slot_held(SLOT):  # 풀렸다
            pass


def test_slot_writers_hold_slot_lock():
    assert "with LOCK, slot_held(slot):" in inspect.getsource(dash_files.drop_slot)
    assert "with LOCK, slot_held(slot):" in inspect.getsource(dash_files.drop_user)
    assert "with slot_held(slot):" in inspect.getsource(dash_files.drop_orphans)
    assert 'dash_files.slot_held(need["slot"])' in inspect.getsource(dash_pull.pull_slot)
    assert "dash_files.slot_held(slot)" in inspect.getsource(dash_pull.refetch_range)
    assert "dash_files.slot_held(old), dash_files.slot_held(s2)" in inspect.getsource(dash_pull.rewrite_crypt)


# ---------- 라우트 (U122) ----------

def test_shortcut_make_route(client, monkeypatch, tmp_path):
    d = dash_store.save_dashboard({"name": "보기:하나", "cards": []})
    asked, written = [], []
    monkeypatch.setattr(server, "ask_save", lambda name, kinds: asked.append((name, kinds)) or str(tmp_path / "고른 곳"))
    monkeypatch.setattr(shortcut, "write_lnk", lambda *a: written.append(a))
    monkeypatch.setattr(drivers, "frozen", lambda: False)
    r = client.post("/api/dash/shortcut", json={"id": d["id"], "icon": ""})  # 본체가 연결돼 있지 않다: 연결 없는 바로가기
    assert r.status_code == 200 and r.json == {"path": str(tmp_path / "고른 곳.lnk")}
    assert asked == [(dash_app.pdf_stem("보기:하나") + ".lnk", shortcut.LNK_KINDS)]
    assert written == [(tmp_path / "고른 곳.lnk", *shortcut.launch_command(d["id"], ""), str(shortcut.APP_ICON))]
    db = {"kind": "url", "url": "postgres://db.example.invalid:5432/main", "user": "reader", "password": "pw"}
    server.state.update(opened=db)  # 본체가 붙어 있던 연결: 비밀번호를 뺀 열쇠만 바로가기에
    client.post("/api/dash/shortcut", json={"id": d["id"], "icon": ""})
    key = use_counts.connection_key(source.recent(db))
    assert written[-1] == (tmp_path / "고른 곳.lnk", *shortcut.launch_command(d["id"], key), str(shortcut.APP_ICON))
    assert "pw" not in written[-1][2]
    monkeypatch.setattr(server, "ask_save", lambda name, kinds: None)
    assert client.post("/api/dash/shortcut", json={"id": d["id"], "icon": ""}).json == {"path": None}
    bad = client.post("/api/dash/shortcut", json={"id": d["id"], "icon": str(tmp_path / "x.png")})
    assert bad.status_code == 400 and bad.json["error"] == shortcut.BAD_ICON
    monkeypatch.setattr(server, "ask_save", None)
    gone = client.post("/api/dash/shortcut", json={"id": d["id"], "icon": ""})
    assert gone.status_code == 400 and gone.json["error"] == dash_app.NO_WINDOW
    assert len(written) == 2


def test_shortcut_connect_route(client, monkeypatch, tmp_path):
    info = folder_info(tmp_path, "a.duckdb")
    monkeypatch.setattr(server, "only_dash", "d1")
    monkeypatch.setattr(server, "only_conn", remember(info))
    r = client.post("/api/dash/shortcut/connect", json={})
    assert r.status_code == 200 and r.json["connected"] is True
    assert server.state["opened"] == info  # 가짜 open_con(conftest)으로 실제 switch를 탄다
    monkeypatch.setattr(server, "only_conn", "")
    assert client.post("/api/dash/shortcut/connect", json={}).json["needs"] == "address"
    monkeypatch.setattr(server, "only_dash", "")
    assert client.post("/api/dash/shortcut/connect", json={}).json["error"] == shortcut.NOT_SHORTCUT


def test_constants_tell_shortcut(client, monkeypatch):
    assert client.get("/api/dash/constants").json["shortcut"] == ""
    monkeypatch.setattr(server, "only_dash", "d1")
    assert client.get("/api/dash/constants").json["shortcut"] == "d1"


# ---------- desktop.py (U123) ----------

def test_desktop_runs_shortcut_without_app_lock():
    text = (ROOT / "desktop.py").read_text(encoding="utf-8")
    assert 'if sys.argv[1:2] == ["--dash"]:' in text
    assert "ONLY_DASH, ONLY_CONN = shortcut.shortcut_dash(sys.argv)" in text
    assert "if not ONLY_DASH and not filelock.hold(APP_LOCK):" in text
    assert "app.before_request(server.shortcut_gate)" in text
    assert "server.only_dash, server.only_conn = ONLY_DASH, ONLY_CONN" in text
    assert text.index("app.before_request(server.shortcut_gate)") < text.index("http = make_server(")
