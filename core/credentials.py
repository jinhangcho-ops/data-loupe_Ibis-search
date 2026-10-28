"""자격 증명 공급자: 다른 앱(Slack·웹훅)의 비밀 값(토큰·주소)을 어디에 둘지 세 방식 중 고른다.
- saved: Windows DPAPI로 암호화해 설정 파일에 둔다(지금 로그인한 Windows 계정·이 PC만 풀 수 있다).
- session: 이번 실행 동안 메모리(SESSION)에만 둔다. 파일에는 아무것도 남지 않고, 프로그램을 끄면 사라진다.
- env: 환경변수에서 읽는다. 파일에는 변수 이름만 남는다(값은 프로그램을 띄운 환경이 준다).
보관 방식을 적지 않은 예전 파일은 saved로 읽는다."""
import base64
import binascii
import ctypes
import os
import re
from ctypes import wintypes

KEEPS = {"saved": "암호화해 저장(이 PC·이 계정만)", "session": "이번 실행 동안만(메모리)", "env": "환경변수"}
DEFAULT_KEEP = "saved"  # 보관 방식이 없는 예전 파일
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,99}")
SESSION = {}  # {열쇠: 값}. 파일에 쓰지 않는다.


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_char))]


def dpapi(function, data):
    """Windows DPAPI(crypt32의 CryptProtectData/CryptUnprotectData)로 data(bytes)를 암호화하거나 되돌린다.
    지금 로그인한 Windows 계정만 풀 수 있다. 실패하면 None."""
    buffer = ctypes.create_string_buffer(data, len(data))
    given, out = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), Blob()
    call = getattr(ctypes.windll.crypt32, function)
    call.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    call.restype = wintypes.BOOL
    if not call(ctypes.byref(given), None, None, None, None, 0, ctypes.byref(out)):
        return None
    result = ctypes.string_at(out.data, out.size)
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    ctypes.windll.kernel32.LocalFree(ctypes.cast(out.data, ctypes.c_void_p))
    return result


def protect(text):
    locked = dpapi("CryptProtectData", text.encode("utf-8"))
    if locked is None:
        raise ValueError("비밀 값을 암호화하지 못했어요.")
    return base64.b64encode(locked).decode()


def reveal(stored):
    """protect로 저장한 글자를 되돌린다. 풀 수 없으면(다른 PC·계정에서 온 파일, 깨진 값) None."""
    try:
        opened = dpapi("CryptUnprotectData", base64.b64decode(stored, validate=True))
    except (binascii.Error, ValueError, TypeError):
        return None
    return None if opened is None else opened.decode("utf-8", "replace")


def keep_of(item):
    """저장한 항목의 보관 방식(없거나 모르는 값이면 DEFAULT_KEEP)."""
    keep = item.get("keep") if isinstance(item, dict) else None
    return keep if keep in KEEPS else DEFAULT_KEEP


def check_keep(keep):
    if keep not in KEEPS:
        raise ValueError(f"비밀 값 보관 방식은 {', '.join(KEEPS)} 중 하나예요.")
    return keep


def store(key, keep, value, old_keep=None, old=None):
    """비밀 값 하나를 보관하고 파일에 쓸 값을 돌려준다. value가 비었으면 같은 방식으로 이미 둔 값을 그대로 쓴다(없으면 오류는 부르는 쪽이 낸다: None).
    saved → 암호문, session → ""(값은 SESSION에만), env → 환경변수 이름."""
    value = value.strip() if isinstance(value, str) else ""
    if not value:
        return old if old_keep == keep and available(key, keep, old) else None
    if keep == "saved":
        SESSION.pop(key, None)
        return protect(value)
    if keep == "session":
        SESSION[key] = value
        return ""
    if not ENV_NAME.fullmatch(value):
        raise ValueError("환경변수 이름은 영문·숫자·밑줄만(숫자로 시작하지 않게) 100자까지 쓸 수 있어요.")
    SESSION.pop(key, None)
    return value


def resolve(key, keep, stored):
    """보관한 비밀 값. 없으면(세션이 끝남, 환경변수 없음, 풀 수 없음) None."""
    if keep == "session":
        return SESSION.get(key)
    if keep == "env":
        return (os.environ.get(stored) or None) if isinstance(stored, str) and stored else None
    return reveal(stored) if isinstance(stored, str) and stored else None


def available(key, keep, stored):
    return resolve(key, keep, stored) is not None


def shown(key, keep, stored):
    """화면에 보일 상태(비밀 값 자체는 보이지 않는다): saved·session은 쓸 수 있는지(true/false), env는 {"env": 이름, "set": 있는지}."""
    if keep == "env":
        return {"env": stored if isinstance(stored, str) else "", "set": available(key, keep, stored)}
    return available(key, keep, stored)


def missing(keep, label, env_name=""):
    """비밀 값이 없을 때의 안내."""
    if keep == "session":
        return f"'{label}'은 이번 실행 동안만 두는 값이라 다시 입력해야 해요(프로그램을 다시 켰거나 예약 실행)."
    if keep == "env":
        return f"'{label}'의 환경변수 {env_name or '(이름 없음)'}이 없어요. 프로그램을 띄우는 환경에 넣으세요."
    return f"'{label}'을 읽을 수 없어요(다른 PC·계정에서 온 파일). 다시 입력해 저장하세요."


