"""(B11) 받은 Parquet 암호화 열쇠: 앱 열쇠(AES-128·AES-256 종류마다 하나, 같은 종류를 고른 대시보드끼리 같은 칸을 같이 쓴다)와 대시보드마다 사용자가 적은 암호문을
설정 폴더의 dash_keys.dat 한 파일에 {"v":1,"blob": credentials.protect(JSON)}로 둔다(이 PC·이 계정만 풀 수 있다). 암호문 열쇠는 칸의 소금(pull.json)과 PBKDF2로 만든다.
열쇠는 화면으로 보내지 않는다. 명세: plans/dash-blocks.md "B11 세부 명세"."""
import base64
import hashlib
import json
import secrets
import threading

from core import credentials, dash_store, settings

KEYS_FILE = "dash_keys.dat"  # 설정 폴더의 열쇠 파일
VERSION = 1  # 열쇠 파일 모양 번호
KEY_BYTES = {"aes128": 16, "aes256": 32}  # 앱 열쇠 종류 → 바이트 수
PASS_BYTES = 32  # 암호문 열쇠 바이트 수(AES-256, 사용자 답 2-9)
SALT_BYTES = 16  # 칸 소금 바이트 수
PASS_ROUNDS = 600_000  # PBKDF2-HMAC-SHA256 반복 수(사용자 답 2-9)
GROUP_SALT = b"DataLoupe.dash_pull.group"  # 같은 암호문끼리 같은 칸을 쓰게 하는 묶음 이름을 만들 때의 고정 소금(열쇠에는 쓰지 않는다)
MAX_PASS = 200  # 암호문 글자 수 상한
LOCK = threading.Lock()  # 열쇠 파일 읽고 고쳐 쓰기를 하나씩
DERIVED = {}  # 이번 실행에서 만든 암호문 열쇠·묶음 이름 {(암호문, 소금 또는 "group"): 결과}. 600,000회를 요청마다 되풀이하지 않게(파일에는 쓰지 않는다)
NO_PASS = "암호문을 대시보드 설정에서 적어 주세요."
KEYS_LOCKED = "이 PC에서 만든 열쇠 파일이 아니라 받은 데이터의 열쇠를 읽을 수 없어요. 파일은 그대로 두었어요."


def load() -> dict:
    """[SPECIFICATION] plans/dash-blocks.md#keys_load
    열쇠 파일을 풀어 {"app": {종류: 열쇠}, "pass": {대시보드 id: 암호문}}로 돌려준다(없으면 빈 꼴, 풀 수 없으면 ValueError(KEYS_LOCKED))."""
    path = settings.PATH.parent / KEYS_FILE
    if not path.exists():
        return {"app": {}, "pass": {}}
    found = json.loads(path.read_text(encoding="utf-8"))
    blob = found.get("blob") if found.get("v") == VERSION else None
    plain = credentials.reveal(blob) if isinstance(blob, str) else None
    if plain is None:
        raise ValueError(KEYS_LOCKED)
    data = json.loads(plain)
    if not (isinstance(data, dict) and isinstance(data.get("app"), dict) and isinstance(data.get("pass"), dict)):
        raise ValueError(KEYS_LOCKED)
    return data


def set_pass(dash_id: str, text: str) -> None:
    """[SPECIFICATION] plans/dash-blocks.md#set_pass
    그 대시보드의 암호문을 저장한다(다른 PC에서 다시 적으면 칸의 소금으로 같은 열쇠가 나온다)."""
    with LOCK:
        text = (text or "").strip()
        if not text or len(text) > MAX_PASS:
            raise ValueError(f"암호문은 1~{MAX_PASS}자로 적어 주세요.")
        found = load()
        found["pass"][dash_id] = text
        settings.write_atomic(
            settings.PATH.parent / KEYS_FILE,
            {"v": VERSION, "blob": credentials.protect(json.dumps(found))},
        )


def app_key(kind: str) -> str:
    """[SPECIFICATION] plans/dash-blocks.md#app_key
    AES 종류의 앱 열쇠(base64). 없으면 만들어 저장한다."""
    if kind not in KEY_BYTES:
        raise ValueError(f"앱 열쇠 종류는 {', '.join(KEY_BYTES)} 중 하나예요.")
    with LOCK:
        found = load()
        existing = found["app"].get(kind)
        if existing is not None:
            return existing
        key = base64.b64encode(secrets.token_bytes(KEY_BYTES[kind])).decode()
        found["app"][kind] = key
        settings.write_atomic(
            settings.PATH.parent / KEYS_FILE,
            {"v": VERSION, "blob": credentials.protect(json.dumps(found))},
        )
        return key


def group_of(dash_id: str, crypt: str) -> str:
    """[SPECIFICATION] plans/dash-blocks.md#group_of
    암호화 묶음 이름: none·aes128·aes256은 그 이름, 암호문은 "pass:" + 암호문 지문(같은 암호문끼리 같은 칸을 쓴다)."""
    if crypt in ("none", *KEY_BYTES):
        return crypt
    text = load()["pass"].get(dash_id)
    if not text:
        raise ValueError(NO_PASS)
    if (text, "group") not in DERIVED:
        derived = hashlib.pbkdf2_hmac("sha256", text.encode("utf-8"), GROUP_SALT, PASS_ROUNDS, PASS_BYTES)
        DERIVED[(text, "group")] = "pass:" + dash_store.def_hash(base64.b64encode(derived).decode())
    return DERIVED[(text, "group")]


def slot_key(group: str, dash_id: str, salt: str) -> str | None:
    """[SPECIFICATION] plans/dash-blocks.md#slot_key
    칸 하나의 열쇠(base64): none은 None, AES는 앱 열쇠, 암호문은 그 대시보드 암호문과 칸 소금의 PBKDF2."""
    if group == "none":
        return None
    if group in KEY_BYTES:
        return app_key(group)
    if not group.startswith("pass:"):
        raise ValueError(f"모르는 암호화 묶음이에요: {group}")
    text = load()["pass"].get(dash_id)
    if not text:
        raise ValueError(NO_PASS)
    if (text, salt) not in DERIVED:
        derived = hashlib.pbkdf2_hmac("sha256", text.encode("utf-8"), base64.b64decode(salt), PASS_ROUNDS, PASS_BYTES)
        DERIVED[(text, salt)] = base64.b64encode(derived).decode()
    return DERIVED[(text, salt)]


def forget(dash_id: str) -> None:
    """[SPECIFICATION] plans/dash-blocks.md#keys_forget
    지운 대시보드의 암호문을 열쇠 파일에서 뺀다(앱 열쇠는 둔다)."""
    with LOCK:
        found = load()
        if dash_id not in found["pass"]:
            return
        del found["pass"][dash_id]
        settings.write_atomic(
            settings.PATH.parent / KEYS_FILE,
            {"v": VERSION, "blob": credentials.protect(json.dumps(found))},
        )
