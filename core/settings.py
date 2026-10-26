"""사용자 설정(화면 모드, 차트 비율, 차트 저장 모드, 따옴표 해석, 조건 입력 방식, 전체 행 수 세기, 끌어서 조절한 크기, 직전 실행의 행당 시간)을 사용자 폴더의 파일에 저장해 다음 실행 때도 쓴다."""
import contextlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

PATH = Path(os.environ.get("APPDATA") or Path.home()) / "DataLoupe" / "settings.json"
OLD_NAME = "ibis_search"  # 앱 이름을 바꾸기 전의 설정 폴더 이름
FAILED = "옮기기_실패.txt"
TASKS_MOVED = "old_tasks_moved.json"  # integrations/schedule.MOVED와 같은 이름: 예약 옮기기가 새 폴더에 남기는 표식(설정 파일이 아니다)


def _has_content(folder):
    """folder 안에 실패 표식·예약 옮기기 표식·locks 폴더 밖의 파일이 있는가(빈 하위 폴더는 무시)."""
    for root, dirs, files in os.walk(folder):
        if Path(root) == folder:
            dirs[:] = [d for d in dirs if d != "locks"]
            files = [f for f in files if f not in (FAILED, TASKS_MOVED)]
        if files:
            return True
    return False


def left_behind(path=None):
    """옮기기에 한 번 실패한 뒤 새 폴더에 설정 파일이 생겨 migrate가 다시 옮기지 않는 채로 옛 폴더(OLD_NAME)가 그대로면, 그때 적어 둔 이유 글자. 아니면 None. 병합은 하지 않고 안내만 다시 띄우게 한다."""
    new = (path or PATH).parent
    reason = new / FAILED
    if not (new.with_name(OLD_NAME).is_dir() and reason.is_file()):
        return None
    try:
        return reason.read_text(encoding="utf-8") if _has_content(new) else None
    except OSError:
        return None


def migrate(path=None):
    """앱이 시작할 때 한 번: 예전 설정 폴더(OLD_NAME)가 있고 새 폴더가 없으면 폴더째 옮긴다. 옮기지 못하면(예: 예전 앱이 켜져 있어 파일이 잠김)
    새 폴더로 시작하고 예전 폴더는 그대로 두며, 이유를 새 폴더의 FAILED 파일에 적는다. 옮기지 못한 이유 글자 또는 None을 돌려준다."""
    new = (path or PATH).parent
    old = new.with_name(OLD_NAME)
    if not old.is_dir():
        return None
    if new.exists():
        # 지난번에 옮기지 못한 새 폴더는 "아직 안 옮김": 실제 설정 파일이 없으면(실패 표식·locks 폴더·빈 하위 폴더만) 치우고 다시 옮긴다. 파일이 있으면 이미 쓰는 폴더라 건드리지 않는다.
        try:
            if not new.is_dir() or _has_content(new):
                return None
            from core import filelock  # 순환 import를 피하려고 여기서
            locks = new / "locks"
            if locks.is_dir() and any(not filelock.left_behind(f) for f in locks.iterdir()):
                return None  # 살아 있는 잠금(앱이 켜져 있음)이 있으면 지우지 않는다: 지우면 단일 실행 잠금이 사라져 두 번째 실행이 된다
            shutil.rmtree(new)  # 다른 앱이 locks 안의 잠금을 쥐고 있으면 지워지지 않아 OSError: 이미 쓰는 중이니 건드리지 않는다
        except OSError:
            return None
    try:
        os.replace(old, new)
        return None  # 새 폴더는 예전 폴더 그대로라 실패 표식이 남지 않는다
    except OSError as e:
        if not old.exists():
            return None  # 다른 프로세스가 동시에 옮겼다
        reason = f"예전 설정 폴더({old})를 새 폴더({new})로 옮기지 못했습니다: {e}\n예전 앱을 모두 끈 뒤 새 폴더의 내용을 지우고 예전 폴더 이름을 {new.name}로 바꾸면 이어 쓸 수 있습니다. 앱을 다시 켜면 새 폴더에 설정 파일이 아직 없을 때는 다시 옮기려 합니다."
        with contextlib.suppress(OSError):
            new.mkdir(parents=True, exist_ok=True)
            (new / FAILED).write_text(reason, encoding="utf-8")
        return reason

# 설정 이름: 고를 수 있는 값. 화면(index.html)의 선택지와 같아야 한다.
CHOICES = {
    "theme": ["light", "dark"],  # 화면 모드. 고른 적이 없으면 Windows 설정을 따른다.
    "ratio": ["auto", "square", "wide"],  # 차트 비율: 데이터 양에 따라 / 1:1 / 16:9
    "image": ["light", "dark"],  # 차트와 집계 표를 그림으로 저장할 때의 모드
    "quotes": ["on", "off"],  # 조건을 넣을 때 따옴표 해석: 값을 감싸는 기호로 / 글자 그대로
    "cond": ["fields", "line"],  # 조건 입력 방식: 칸으로 선택 / 한 줄 입력
    "count": ["on", "off"],  # 검색할 때 전체 행 수를 셀지: 센다 / 세지 않는다
    "board_ratio": ["screen", "square", "4:3", "wide"],  # 대시보드·그래프 그림의 비율: 화면대로 / 1:1 / 4:3 / 16:9
    "board_format": ["png", "jpeg"],  # 대시보드·그래프 그림의 형식
    "stats": ["on", "off"],
    "update_check": ["on", "off"],  # 앱을 열 때 GitHub Releases에 새 버전이 있는지 묻기(기본 on)
    "scan_unknown": ["run", "block"],  # 처리 용량을 미리 알 수 없을 때(그 연결이 추정을 못 함): 그대로 실행한다 / 실행하지 않는다(기본은 실행)
    "prep_where": ["all", "new"],  # 원격 DB(주소로 접속)의 전처리를 이 PC의 작업 사본에서 계산하는 범위: 전처리 전체 / 계산 컬럼·PCA가 있을 때만(나머지는 DB에서)  # 전처리 화면에서 테이블을 고를 때 표본을 읽어 컬럼 통계·추천을 구할지(끄면 이름·타입만, 데이터를 읽지 않는다)
}

# 끌어서 조절한 크기(px)와 전처리가 미리 볼 행 수(sample), 오래 걸린 작업이 끝났을 때 알림을 보내는 기준 시간(notify_after, 초, 기본 30), DuckDB 파일 조회의 작업 메모리 상한(memory, MB, 기본은 제한 없음), DuckDB 계산에 쓰는 코어 수(threads, 기본은 전부): (최소, 최대). 화면은 이 범위를 server.CONSTANTS로 받는다. 0을 저장하면 기본값으로 돌아간다.
SIZES = {"panel": (240, 640), "rows": (120, 1200), "summary": (120, 1200), "chart": (200, 1000), "sample": (1000, 1_000_000), "notify_after": (5, 3600), "memory": (256, 1_000_000), "threads": (1, os.cpu_count() or 1)}

# 비율 설정: (최소, 최대). 비우거나 0을 저장하면 기본값(DEFAULTS)으로 돌아간다.
# sample_too_few: 블록 샘플링이 기대한 행의 이만큼도 못 가져오면 다음 표본 방법으로 넘어가는 기준(profiler).
FLOATS = {"sample_too_few": (0.05, 0.9)}
DEFAULTS = {"sample_too_few": 0.2}

# 직전 실행의 행당 시간(나노초/행). 작업 종류(검색·집계·차트·저장·전처리의 모양 보기·미리보기·실행)와 기준 테이블 행 수의 구간(10배 단위)마다 하나.
# 이름은 time_종류_구간이고 구간은 행 수의 자릿수 - 1을 TIME_BANDS 안으로 맞춘 것(1,000행 미만은 모두 2)이다. 화면이 예상 시간을 구할 때 같은 구간의 값만 쓴다.
# 화면(common.js)은 구간과 상한(MAX_TIME)을 server.CONSTANTS로 받는다.
TIME_BANDS = (2, 12)
TIME = re.compile(rf"time_(search|summary|chart|metric|export|profile|preview|run|analyze)_({'|'.join(str(n) for n in range(TIME_BANDS[0], TIME_BANDS[1] + 1))})")
MAX_TIME = 10**12
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")  # env 파일의 이름


def valid(name, value):
    if TIME.fullmatch(name):
        return type(value) is int and 1 <= value <= MAX_TIME
    if name in SIZES:
        return type(value) is int and SIZES[name][0] <= value <= SIZES[name][1]
    if name in FLOATS:
        return type(value) in (int, float) and FLOATS[name][0] <= value <= FLOATS[name][1]
    return value in CHOICES.get(name, [])


def get(name):
    """저장된 설정 값. 없거나 쓸 수 없으면 기본값(DEFAULTS, 없으면 None)."""
    return load().get(name, DEFAULTS.get(name))


def read_file(path):
    """JSON 파일에 든 객체. 파일이 없거나 읽을 수 없으면 None."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read(name):
    """설정 폴더의 JSON 파일에 든 객체. 없으면 None. JSON이 깨졌으면 덮어쓰기 전에 남긴 name.bak을 읽는다(그것도 없으면 None). 파일을 읽지 못하면(OSError) 오류."""
    path = PATH.parent / name
    try:
        data = retrying(lambda: json.loads(path.read_text(encoding="utf-8")))  # 잠깐 못 읽는 것(백신 등)은 기다려 다시 읽고, 그래도 안 되면 OSError를 그대로 낸다: 한 세대 전 .bak으로 떨어지면 옛 값을 진짜로 착각한다
    except FileNotFoundError:
        return None
    except ValueError:
        data = None
    return data if isinstance(data, dict) else read_file(backup_of(path))


def saved_text(name, key="path"):
    """설정 폴더의 name 파일에 저장된 key 값(글자). 없거나 글자가 아니거나 비었으면 빈 글자."""
    found = (read(name) or {}).get(key)
    return found if isinstance(found, str) and found else ""


def backup_of(path):
    return path.with_name(path.name + ".bak")


def backup(path):
    """덮어쓰기 전에 원래 파일을 path.bak으로 남긴다(한 세대). 원래 파일이 깨졌으면 남기지 않아, 그 전의 성한 .bak을 지킨다."""
    if read_file(path) is not None:
        retrying(lambda: shutil.copyfile(path, backup_of(path)))


REPLACE_TRIES = 10  # 다른 프로세스가 바꿀 파일을 읽는 중이면 바꿔치기가 거부될 수 있어(Windows) 잠깐씩 기다리며 다시 한다.


def retrying(action):
    """action()을 하다 PermissionError가 나면 잠깐씩 기다리며 REPLACE_TRIES번까지 다시 한다(Windows에서 다른 프로세스가 그 파일을 열어 둘 때)."""
    for tries in range(REPLACE_TRIES):
        try:
            return action()
        except PermissionError:
            if tries == REPLACE_TRIES - 1:
                raise
            time.sleep(0.02)


def write_atomic(path, data):
    """data를 JSON으로 path 옆 임시 파일에 쓴 뒤 바꿔치기한다(쓰는 중에 꺼져도 path가 반쯤 깨지지 않는다). 임시 파일 이름은 프로세스·스레드마다 달라 함께 써도 부딪치지 않는다."""
    temp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.new")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        retrying(lambda: os.replace(temp, path))
    finally:
        temp.unlink(missing_ok=True)


def write(name, data):
    """설정 폴더에 name 파일로 data를 JSON으로 쓴다(임시 파일 → 바꿔치기). 덮어쓰기 전에 원래 파일을 name.bak으로 남긴다."""
    PATH.parent.mkdir(parents=True, exist_ok=True)
    path = PATH.parent / name
    backup(path)
    write_atomic(path, data)


LOCK = threading.RLock()  # 같은 프로세스의 스레드끼리: 읽고-고치고-쓰기를 한 번에 하나씩


@contextlib.contextmanager
def updating(name):
    """name 파일을 읽고-고치고-쓰는 동안 다른 스레드와 다른 프로세스가 같은 일을 하지 못하게 묶는다(안 그러면 동시에 저장한 키가 사라진다). filelock은 settings를 import하므로 쓸 때 불러온다."""
    from core import filelock
    with LOCK, filelock.guarded("settings-" + name):
        yield


def load():
    """저장된 설정 중 쓸 수 있는 값만. 파일이 없거나 읽을 수 없으면 빈 설정."""
    return {name: value for name, value in (read(PATH.name) or {}).items() if valid(name, value)}


def save(changes):
    """바뀐 설정만 받아 기존 설정에 더해 저장한다."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("저장할 설정이 없습니다.")
    for name, value in changes.items():
        if not valid(name, value) and not ((name in SIZES or name in FLOATS) and value == 0 and type(value) in (int, float)):
            raise ValueError(f"'{name}' 설정에 쓸 수 없는 값입니다: {value}")
    with updating(PATH.name):
        write(PATH.name, {name: value for name, value in {**load(), **changes}.items() if value != 0})


MAX_SAVED = 100  # 이름을 붙여 저장해 둘 수 있는 검색(구성) 수
MAX_SEARCH_SIZE = 20_000  # 검색 하나의 크기(JSON 글자 수)
MAX_RECIPE_SIZE = 100_000  # 전처리 구성 하나의 크기. 컬럼 100개에 전처리를 아홉 개씩 정해도 약 3만 자라 검색보다 크게 잡는다.


def josa(word, with_final, without_final):
    """word 끝 글자에 받침이 있으면 with_final("이"·"은"), 없으면 without_final("가"·"는")을 붙인다."""
    last = word[-1:]
    final = "가" <= last <= "힣" and (ord(last) - ord("가")) % 28 != 0
    return word + (with_final if final else without_final)


def named(file):
    """이름을 붙여 저장한 것 {이름: 내용}을 담은 설정 폴더의 JSON 파일. 없거나 읽을 수 없으면 빈 것."""
    return read(file) or {}


def save_named(file, what, limit, name, item, count=MAX_SAVED):
    """item(what은 "검색"·"구성" 같은 낱말)을 그 이름으로 file에 저장한다(같은 이름은 덮어쓴다). item이 None이면 그 이름을 지운다. 바뀐 전체를 돌려준다.
    이름은 1~50자, 내용은 객체이고 JSON으로 limit자 이하, 개수는 count개까지(기본 MAX_SAVED)."""
    name = name.strip() if isinstance(name, str) else ""
    if not 1 <= len(name) <= 50:
        raise ValueError(f"{what} 이름은 1자 이상 50자 이하로 입력하세요.")
    with updating(file):
        found = named(file)
        if item is None:
            found.pop(name, None)
        elif not isinstance(item, dict):
            raise ValueError(f"저장할 {josa(what, '이', '가')} 올바르지 않습니다.")
        elif len(json.dumps(item, ensure_ascii=False)) > limit:
            raise ValueError(f"저장할 {josa(what, '이', '가')} 너무 큽니다.")
        elif name not in found and len(found) >= count:
            raise ValueError(f"저장한 {josa(what, '은', '는')} {count}개까지입니다. 쓰지 않는 것을 지우세요.")
        else:
            found[name] = item
        write(file, found)
        return found


def searches():
    """이름을 붙여 저장한 검색 {이름: 검색}. 연결 정보는 들어 있지 않다."""
    return named("searches.json")


def save_search(name, search):
    return save_named("searches.json", "검색", MAX_SEARCH_SIZE, name, search)


def recipes():
    """이름을 붙여 저장한 전처리 구성 {이름: 구성}. 연결 정보는 들어 있지 않다."""
    return named("recipes.json")


def save_recipe(name, recipe):
    return save_named("recipes.json", "구성", MAX_RECIPE_SIZE, name, recipe)


def read_env(path):
    """KEY=VALUE 줄의 env 파일을 {이름: 값}으로. export 앞말, 따옴표, # 주석, BOM, 빈 줄을 다룬다. 오류 문구에는 값을 넣지 않는다. 수집({env:이름})과 LLM 설정이 쓴다."""
    try:
        text = open(path, encoding="utf-8-sig").read()
    except OSError as e:
        raise ValueError(f"env 파일을 읽을 수 없습니다: {e.strerror or e}") from None
    except UnicodeDecodeError:
        raise ValueError("env 파일은 UTF-8이어야 합니다.") from None
    found = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, eq, value = re.sub(r"^export\s+", "", line).partition("=")
        name, value = name.strip(), value.strip()
        if not eq or not ENV_NAME.fullmatch(name):
            raise ValueError(f"env 파일 {number}번째 줄을 읽을 수 없습니다(이름=값 모양이어야 합니다).")
        if value[:1] in ("'", '"'):
            end = 1
            while end < len(value) and value[end] != value[0]:
                end += 2 if value[0] == '"' and value[end] == "\\" else 1  # 큰따옴표 안의 \" 와 \ 는 두 글자가 한 글자
            if end >= len(value):
                raise ValueError(f"env 파일 {number}번째 줄의 따옴표가 닫히지 않았습니다.")
            inner = value[1:end]
            value = re.sub(r"\\([nt\"\\])", lambda m: {"n": "\n", "t": "\t"}.get(m[1], m[1]), inner) if value[0] == '"' else inner
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        found[name] = value
    return found
