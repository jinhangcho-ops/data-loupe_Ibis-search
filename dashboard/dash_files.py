"""(B11) 받은 데이터 폴더 관리: 설정 폴더 dash_data\\<칸 id>\\(설명 pull.json + part-*.parquet). 칸 하나 = (연결, 테이블, 암호화 묶음) 하나이고 여러 대시보드가 같이 쓴다(pull.json의 users).
크기, 대시보드를 지울 때 그 대시보드를 users에서 빼고 아무도 안 쓰는 칸만 지우기, 쓰는 대시보드가 없는 칸(주인 없는 칸) 찾기·지우기.
앱이 만든 표식(pull.json)이 맞고 모르는 파일이 없는 칸만 지운다(모르는 것은 남긴다). 명세: plans/dash-blocks.md "B11 세부 명세"."""
import contextlib
import json
import os
import re
import threading
from collections.abc import Iterator
from pathlib import Path

from core import filelock, settings

PULL_DIR = "dash_data"  # 설정 폴더 아래 받은 데이터 폴더
MANIFEST = "pull.json"  # 칸 폴더의 설명(표식)
PART_RE = re.compile(r"part-\d{6}\.parquet")  # 조각 파일 이름 모양
SLOT_RE = re.compile(r"[0-9a-f]{16}")  # 칸 폴더 이름 모양(dash_store.def_hash)
VERSION = 3  # pull.json 모양 번호
MANIFEST_KEYS = ("v", "source", "schema", "table", "virtual", "crypt", "columns", "held", "users", "parts", "rows", "pulled_at", "lost")  # pull.json의 항목
LOCK = threading.RLock()  # 설명 파일 읽고 고쳐 쓰기·지우기를 하나씩(dash_pull도 이것을 쓴다. 안에서 다시 잡을 수 있게 RLock)
SLOT_LOCK = "dash-pull-{slot}"  # (B12) 칸 하나를 받는 동안 다른 프로세스(본체·바로가기 창)가 같은 칸을 받지 못하게 잡는 잠금 이름(filelock.lock_path)
PULL_BUSY = "다른 Data Loupe 창이 이 데이터를 받고 있어요. 끝난 뒤 다시 [최신화]를 눌러 주세요."  # (B12)
HELD: set[str] = set()  # (B12) 이 프로세스가 지금 잡고 있는 칸 잠금(LOCK 안에서만 고친다. 안에서 같은 칸을 다시 잡으면 그대로 지나간다)


@contextlib.contextmanager
def slot_held(slot: str) -> Iterator[None]:
    """[SPECIFICATION] plans/dash-blocks.md#slot_held
    (B12) LOCK 안에서 칸(slot)의 프로세스 사이 잠금을 잡고 안의 일을 한다(다른 프로세스가 잡았으면 PULL_BUSY로 거부, 이 프로세스가 이미 잡았으면 그대로, 끝나면 푼다)."""
    if slot in HELD:
        yield
        return
    path = filelock.lock_path(SLOT_LOCK.format(slot=slot))
    if not filelock.hold(path):
        raise ValueError(PULL_BUSY)
    HELD.add(slot)
    try:
        yield
    finally:
        HELD.discard(slot)
        filelock.let_go(path)


def root() -> Path:
    """[SPECIFICATION] plans/dash-blocks.md#files_root
    받은 데이터 폴더(설정 폴더 아래 PULL_DIR)의 경로."""
    return settings.PATH.parent / PULL_DIR


def read_manifest(slot: str) -> dict | None:
    """[SPECIFICATION] plans/dash-blocks.md#read_manifest
    칸 폴더의 pull.json을 읽어 돌려준다(없으면 None, 모양이 틀리면 ValueError)."""
    path = root() / slot / MANIFEST
    if not path.is_file():
        return None
    man = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(man, dict)
        or set(man) != set(MANIFEST_KEYS)
        or man["v"] != VERSION
        or not isinstance(man["parts"], list)
        or not all(isinstance(p, str) and PART_RE.fullmatch(p) for p in man["parts"])
        or not isinstance(man["users"], dict)
        or not isinstance(man["held"], list)
        or not isinstance(man["crypt"], dict)
        or set(man["crypt"]) != {"group", "salt"}
    ):
        raise ValueError(f"받은 데이터 설명 파일이 깨졌어요: {path}")
    return man


def write_manifest(slot: str, manifest: dict) -> None:
    """[SPECIFICATION] plans/dash-blocks.md#write_manifest
    칸 폴더를 (없으면) 만들고 pull.json을 바꿔치기로 쓴다."""
    with LOCK:
        (root() / slot).mkdir(parents=True, exist_ok=True)
        settings.write_atomic(root() / slot / MANIFEST, manifest)


def all_slots() -> dict[str, dict]:
    """[SPECIFICATION] plans/dash-blocks.md#all_slots
    표식이 맞는 모든 칸 {칸 id: 설명}."""
    base = root()
    if not base.is_dir():
        return {}
    found: dict[str, dict] = {}
    for item in sorted(base.iterdir()):
        if not item.is_dir() or item.is_symlink() or os.path.isjunction(item):
            continue
        if not SLOT_RE.fullmatch(item.name):
            continue
        man = read_manifest(item.name)
        if man is not None:
            found[item.name] = man
    return found


def slots(dash_id: str) -> dict[str, dict]:
    """[SPECIFICATION] plans/dash-blocks.md#slots
    그 대시보드가 쓰는(users에 든) 칸들 {칸 id: 설명}."""
    return {s: m for s, m in all_slots().items() if dash_id in m["users"]}


def size_of(dash_id: str) -> int:
    """[SPECIFICATION] plans/dash-blocks.md#size_of
    그 대시보드가 쓰는 칸들의 받은 파일 크기 합(바이트, 다른 대시보드와 같이 쓰는 칸도 센다)."""
    return sum(
        (root() / slot / part).stat().st_size
        for slot, man in slots(dash_id).items()
        for part in man["parts"]
        if (root() / slot / part).is_file()
    )


def drop_slot(slot: str) -> bool:
    """[SPECIFICATION] plans/dash-blocks.md#drop_slot
    칸 폴더 하나를 지운다. 모르는 파일이 있거나 잠겨 못 지우면 남기고 False."""
    with LOCK, slot_held(slot):
        folder = root() / slot
        man = read_manifest(slot)
        if man is None or man["users"] or not {p.name for p in folder.iterdir()} <= {MANIFEST, *man["parts"]}:
            return False
        try:
            for name in (*man["parts"], MANIFEST):
                (folder / name).unlink(missing_ok=True)
            folder.rmdir()
        except OSError:
            return False
        return True


def drop_user(slot: str, dash_id: str) -> bool:
    """[SPECIFICATION] plans/dash-blocks.md#drop_user
    칸의 users에서 그 대시보드를 빼고, 아무도 안 쓰게 되면 칸을 지운다. 칸 파일이 지워졌으면 True."""
    with LOCK, slot_held(slot):
        man = read_manifest(slot)
        if man is None or dash_id not in man["users"]:
            return False
        del man["users"][dash_id]
        write_manifest(slot, man)
        if man["users"]:
            return False
        return drop_slot(slot)


def forget(dash_id: str) -> list[str]:
    """[SPECIFICATION] plans/dash-blocks.md#forget
    지운 대시보드를 모든 칸의 users에서 빼고 아무도 안 쓰는 칸을 지운다. 지우지 못한 칸 id들을 돌려준다(다음 실행 때 orphans가 묻는다)."""
    return [s for s in slots(dash_id) if not drop_user(s, dash_id) and read_manifest(s) is not None and not read_manifest(s)["users"]]


def orphans(known: set[str]) -> list[dict]:
    """[SPECIFICATION] plans/dash-blocks.md#orphans
    저장한 대시보드(known) 가운데 아무도 쓰지 않는 칸 [{slot, schema, table, bytes}]."""
    return [
        {
            "slot": slot,
            "schema": man["schema"],
            "table": man["table"],
            "bytes": sum((root() / slot / part).stat().st_size for part in man["parts"] if (root() / slot / part).is_file()),
        }
        for slot, man in all_slots().items()
        if known.isdisjoint(man["users"])
    ]


def drop_orphans(known: set[str]) -> list[str]:
    """[SPECIFICATION] plans/dash-blocks.md#drop_orphans
    사용자가 확인한 뒤 주인 없는 칸을 지우고 지운 칸 id들을 돌려준다."""
    dropped: list[str] = []
    with LOCK:
        for item in orphans(known):
            slot = item["slot"]
            with slot_held(slot):
                man = read_manifest(slot)
                if man is None:
                    continue
                write_manifest(slot, {**man, "users": {}})
                if drop_slot(slot):
                    dropped.append(slot)
    return dropped
