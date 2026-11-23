"""(B12) 대시보드 바로가기: 대시보드 하나를 "보기만" 하는 따로 창(desktop.py --dash id [연결 열쇠])을 여는 .lnk 만들기, 그 창의 제목·연결.
바로가기는 대시보드 이름이 아니라 id를, 연결은 만들 때 본체가 붙어 있던 연결의 열쇠(use_counts.connection_key, 비밀번호 없음)를 기억한다.
비밀번호는 어디에도 저장하지 않는다. 명세: plans/dash-blocks.md "B12 세부 명세"."""
import ctypes
import ctypes.wintypes
import subprocess
import sys
import uuid
from pathlib import Path

from core import dash_store, drivers, server, source, use_counts

SHORTCUT_ARG = "--dash"  # desktop.py의 바로가기 실행 인자(뒤에 대시보드 id, 그 뒤에 연결 열쇠가 있을 수 있다)
TITLE_TAIL = " - Data Loupe"  # 바로가기 창 제목 = 대시보드 이름 + 이것
LNK_SUFFIX = ".lnk"  # 바로가기 파일 확장자
LNK_KINDS = ("바로가기 (*.lnk)",)  # 저장 대화상자의 형식
ICON_SUFFIX = ".ico"  # 고를 수 있는 아이콘 파일 확장자
ROOT = Path(__file__).resolve().parent.parent  # 프로젝트 폴더(소스 실행)
APP_ICON = ROOT / "installer" / "DataLoupe.ico"  # 소스 실행의 앱 아이콘(exe는 exe 안의 아이콘)
DESKTOP = ROOT / "desktop.py"  # 소스 실행의 시작 파일
CLSID_SHELL_LINK = "00021401-0000-0000-C000-000000000046"  # Windows ShellLink COM 클래스
IID_SHELL_LINK_W = "000214F9-0000-0000-C000-000000000046"  # IShellLinkW
IID_PERSIST_FILE = "0000010B-0000-0000-C000-000000000046"  # IPersistFile
GUID_BYTES = 16  # COM GUID의 바이트 수
COINIT_APARTMENTTHREADED = 0x2  # CoInitializeEx 방식
CLSCTX_INPROC_SERVER = 0x1  # CoCreateInstance 방식
QUERY_INTERFACE, RELEASE = 0, 2  # IUnknown 차례(vtable 번호)
SHELL_LINK_SLOTS = {"set_working_directory": 9, "set_arguments": 11, "set_icon_location": 17, "set_path": 20}  # IShellLinkW의 vtable 번호
PERSIST_LOAD, PERSIST_SAVE = 5, 6  # IPersistFile::Load·Save의 vtable 번호
STGM_READ = 0  # IPersistFile::Load 방식(읽기)
STARTF_TITLEISLINKNAME = 0x800  # STARTUPINFO.dwFlags: 바로가기(.lnk)로 실행했고 lpTitle이 그 .lnk 경로다
ICON_INDEX = 0  # 아이콘 파일 안의 몇 번째 아이콘(.ico·exe 모두 첫 것)
REMEMBER = 1  # IPersistFile::Save의 fRemember(TRUE: 저장한 파일을 지금 파일로)
NEED_PASSWORD = "password"  # connect_dash의 needs: 비밀번호만 물으면 된다
NEED_ADDRESS = "address"  # connect_dash의 needs: 기억한 연결이 없거나 기록에서 못 찾음 → 주소·아이디·비밀번호를 묻는다
GONE = "이 바로가기의 대시보드를 찾지 못했어요. 지워졌을 수 있어요."  # 창을 열지 않고 알림 창 하나만
BAD_ARG = "바로가기 실행 인자가 맞지 않아요: --dash 뒤에 대시보드 id(와 연결 열쇠)가 있어야 해요."
BAD_ICON = "아이콘은 있는 .ico 파일이어야 해요."
NOT_SHORTCUT = "바로가기 창에서만 쓸 수 있어요."
BAD_FIELDS = "주소·아이디·비밀번호는 글자여야 해요."
NO_URL = "데이터베이스 주소를 넣어 주세요."


def shortcut_dash(argv: list[str]) -> tuple[str, str]:
    """[SPECIFICATION] plans/dash-blocks.md#shortcut_dash
    실행 인자에서 (대시보드 id, 연결 열쇠)를 읽는다(바로가기 실행이 아니면 ("", ""), 연결 없는 바로가기면 열쇠 "", 모양이 틀리면 BAD_ARG)."""
    if argv[1:2] != [SHORTCUT_ARG]:
        return "", ""
    rest = argv[2:]
    if not 1 <= len(rest) <= 2 or not dash_store.ID.fullmatch(rest[0]) or (len(rest) == 2 and rest[1] == ""):
        raise ValueError(BAD_ARG)
    return rest[0], rest[1] if len(rest) == 2 else ""


def window_title(dash_id: str) -> str:
    """[SPECIFICATION] plans/dash-blocks.md#window_title
    바로가기 창 제목 "대시보드 이름 - Data Loupe"(대시보드가 없으면 GONE)."""
    found = dash_store.load()["dashboards"].get(dash_id)
    if found is None:
        raise ValueError(GONE)
    return found["name"] + TITLE_TAIL


def icon_path(icon: object) -> str:
    """[SPECIFICATION] plans/dash-blocks.md#icon_path
    바로가기에 넣을 아이콘 경로: 비면 앱 아이콘, 아니면 있는 .ico 파일(아니면 BAD_ICON)."""
    if icon == "":
        return sys.executable if drivers.frozen() else str(APP_ICON)
    if not isinstance(icon, str) or Path(icon).suffix.lower() != ICON_SUFFIX or not Path(icon).is_file():
        raise ValueError(BAD_ICON)
    return icon


def launch_command(dash_id: str, conn_key: str) -> tuple[str, str, str]:
    """[SPECIFICATION] plans/dash-blocks.md#launch_command
    바로가기의 (대상 실행 파일, 인자, 시작 폴더): exe면 exe와 --dash id [열쇠], 소스 실행이면 python과 desktop.py --dash id [열쇠]."""
    if not dash_store.ID.fullmatch(dash_id):
        raise ValueError(BAD_ARG)
    tail = [SHORTCUT_ARG, dash_id, *([conn_key] if conn_key else [])]
    if drivers.frozen():
        return sys.executable, subprocess.list2cmdline(tail), str(Path(sys.executable).parent)
    return sys.executable, subprocess.list2cmdline([str(DESKTOP), *tail]), str(DESKTOP.parent)


def write_lnk(path: Path, target: str, arguments: str, workdir: str, icon: str | None) -> None:
    """[SPECIFICATION] plans/dash-blocks.md#write_lnk
    Windows ShellLink(COM, ctypes)로 path에 .lnk 파일을 쓴다(icon이 None이면 있는 .lnk를 읽어 아이콘은 그대로 두고 고쳐 쓴다)."""
    def guid(text: str) -> ctypes.Array[ctypes.c_ubyte]:
        return (ctypes.c_ubyte * GUID_BYTES).from_buffer_copy(uuid.UUID(text).bytes_le)

    ctypes.oledll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    try:
        link = ctypes.c_void_p()
        ctypes.oledll.ole32.CoCreateInstance(ctypes.byref(guid(CLSID_SHELL_LINK)), None, CLSCTX_INPROC_SERVER, ctypes.byref(guid(IID_SHELL_LINK_W)), ctypes.byref(link))
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong)(RELEASE, "Release")
        set_text = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_wchar_p)
        with_number = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_wchar_p, ctypes.c_int)
        try:
            persist = ctypes.c_void_p()
            ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p)(QUERY_INTERFACE, "QueryInterface")(link, ctypes.byref(guid(IID_PERSIST_FILE)), ctypes.byref(persist))
            try:
                if icon is None:
                    ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_wchar_p, ctypes.c_ulong)(PERSIST_LOAD, "Load")(persist, str(path), STGM_READ)
                set_text(SHELL_LINK_SLOTS["set_path"], "SetPath")(link, target)
                set_text(SHELL_LINK_SLOTS["set_arguments"], "SetArguments")(link, arguments)
                set_text(SHELL_LINK_SLOTS["set_working_directory"], "SetWorkingDirectory")(link, workdir)
                if icon is not None:
                    with_number(SHELL_LINK_SLOTS["set_icon_location"], "SetIconLocation")(link, icon, ICON_INDEX)
                with_number(PERSIST_SAVE, "Save")(persist, str(path), REMEMBER)
            finally:
                release(persist)
        finally:
            release(link)
    finally:
        ctypes.windll.ole32.CoUninitialize()


def own_lnk() -> str:
    """[SPECIFICATION] plans/dash-blocks.md#own_lnk
    이 프로세스를 띄운 바로가기(.lnk)의 지금 경로(Windows STARTUPINFO). 바로가기로 실행하지 않았으면 ""."""
    class StartupInfo(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.wintypes.DWORD), ("lpReserved", ctypes.wintypes.LPWSTR), ("lpDesktop", ctypes.wintypes.LPWSTR),
            ("lpTitle", ctypes.wintypes.LPWSTR), ("dwX", ctypes.wintypes.DWORD), ("dwY", ctypes.wintypes.DWORD),
            ("dwXSize", ctypes.wintypes.DWORD), ("dwYSize", ctypes.wintypes.DWORD), ("dwXCountChars", ctypes.wintypes.DWORD),
            ("dwYCountChars", ctypes.wintypes.DWORD), ("dwFillAttribute", ctypes.wintypes.DWORD), ("dwFlags", ctypes.wintypes.DWORD),
            ("wShowWindow", ctypes.wintypes.WORD), ("cbReserved2", ctypes.wintypes.WORD), ("lpReserved2", ctypes.wintypes.LPBYTE),
            ("hStdInput", ctypes.wintypes.HANDLE), ("hStdOutput", ctypes.wintypes.HANDLE), ("hStdError", ctypes.wintypes.HANDLE),
        ]

    info = StartupInfo()
    info.cb = ctypes.sizeof(info)
    ctypes.windll.kernel32.GetStartupInfoW(ctypes.byref(info))
    if info.dwFlags & STARTF_TITLEISLINKNAME and info.lpTitle and info.lpTitle.lower().endswith(LNK_SUFFIX):
        return info.lpTitle
    return ""


def remember_lnk(conn_key: str) -> bool:
    """[SPECIFICATION] plans/dash-blocks.md#remember_lnk
    이 바로가기 파일을 conn_key를 기억하게 고쳐 쓴다(아이콘·이름 그대로). 자기 .lnk를 모르면 고치지 않고 False."""
    lnk = own_lnk()
    if not lnk:
        return False
    write_lnk(Path(lnk), *launch_command(server.only_dash, conn_key), None)
    server.only_conn = conn_key
    return True


def saved_connection(conn_key: str) -> dict | None:
    """[SPECIFICATION] plans/dash-blocks.md#saved_connection
    연결 열쇠로 사용 기록(usage.json)에 남은 연결 칸 값(비밀번호 없음)을 찾는다(열쇠가 비었거나 기록에서 밀려났으면 None)."""
    if conn_key == "":
        return None
    entry = use_counts.load()["connections"].get(conn_key)
    return entry["info"] if isinstance(entry, dict) and isinstance(entry.get("info"), dict) else None


def connect_dash(body: object) -> dict[str, object]:
    """[SPECIFICATION] plans/dash-blocks.md#connect_dash
    바로가기 창의 연결을 연다: 넣은 주소가 있으면 그것으로(성공하면 바로가기 파일이 기억), 아니면 기억한 연결(파일은 바로, 비밀번호가 필요하면 password가 올 때만) {connected, needs, label, user, remembered}."""
    if not server.only_dash:
        raise ValueError(NOT_SHORTCUT)
    if not isinstance(body, dict):
        raise ValueError(BAD_FIELDS)
    password, url, user = body.get("password"), body.get("url"), body.get("user", "")
    if not (password is None or isinstance(password, str)) or not (url is None or isinstance(url, str)) or not isinstance(user, str):
        raise ValueError(BAD_FIELDS)
    if url is not None:
        if not url.strip():
            raise ValueError(NO_URL)
        info = {"kind": "url", "url": source.without_placeholder(url.strip()), "user": user}
        server.switch({**info, "password": password or ""})
        remembered = remember_lnk(use_counts.connection_key(source.recent(info)))
        return {"connected": True, "needs": "", "label": dash_store.source_label(info), "user": user, "remembered": remembered}
    saved = saved_connection(server.only_conn)
    if saved is None:
        return {"connected": False, "needs": NEED_ADDRESS, "label": "", "user": "", "remembered": False}
    label, saved_user = dash_store.source_label(saved), saved.get("user", "")
    needs = saved["kind"] == "url" and saved["url"].partition("://")[0].lower() not in dash_store.FILE_SCHEMES
    if needs and password is None:
        return {"connected": False, "needs": NEED_PASSWORD, "label": label, "user": saved_user, "remembered": False}
    server.switch(saved if password is None else {**saved, "password": password})
    return {"connected": True, "needs": "", "label": label, "user": saved_user, "remembered": False}
