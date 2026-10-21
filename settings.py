"""사용자 설정(화면 모드, 차트 비율, 차트 저장 모드, 따옴표 해석, 조건 입력 방식, 전체 행 수 세기, 끌어서 조절한 크기, 직전 실행의 행당 시간)을 사용자 폴더의 파일에 저장해 다음 실행 때도 쓴다."""
import json
import os
import re
import shutil
from pathlib import Path

PATH = Path(os.environ.get("APPDATA") or Path.home()) / "ibis_search" / "settings.json"

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
}

# 끌어서 조절한 크기(px)와 전처리가 미리 볼 행 수(sample), 오래 걸린 작업이 끝났을 때 알림을 보내는 기준 시간(notify_after, 초, 기본 30), DuckDB 파일 조회의 작업 메모리 상한(memory, MB, 기본은 제한 없음), DuckDB 계산에 쓰는 코어 수(threads, 기본은 전부): (최소, 최대). 화면은 이 범위를 server.CONSTANTS로 받는다. 0을 저장하면 기본값으로 돌아간다.
SIZES = {"panel": (240, 640), "rows": (120, 1200), "summary": (120, 1200), "chart": (200, 1000), "sample": (1000, 1_000_000), "notify_after": (5, 3600), "memory": (256, 1_000_000), "threads": (1, os.cpu_count() or 1)}

# 직전 실행의 행당 시간(나노초/행). 작업 종류(검색·집계·차트·저장·전처리의 모양 보기·미리보기·실행)와 기준 테이블 행 수의 구간(10배 단위)마다 하나.
# 이름은 time_종류_구간이고 구간은 행 수의 자릿수 - 1을 TIME_BANDS 안으로 맞춘 것(1,000행 미만은 모두 2)이다. 화면이 예상 시간을 구할 때 같은 구간의 값만 쓴다.
# 화면(common.js)은 구간과 상한(MAX_TIME)을 server.CONSTANTS로 받는다.
TIME_BANDS = (2, 12)
TIME = re.compile(rf"time_(search|summary|chart|metric|export|profile|preview|run|analyze)_({'|'.join(str(n) for n in range(TIME_BANDS[0], TIME_BANDS[1] + 1))})")
MAX_TIME = 10**12


def valid(name, value):
    if TIME.fullmatch(name):
        return type(value) is int and 1 <= value <= MAX_TIME
    if name in SIZES:
        return type(value) is int and SIZES[name][0] <= value <= SIZES[name][1]
    return value in CHOICES.get(name, [])


def read_file(path):
    """JSON 파일에 든 객체. 파일이 없거나 읽을 수 없으면 None."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read(name):
    """설정 폴더의 JSON 파일에 든 객체. 없으면 None. 깨져서 읽을 수 없으면 덮어쓰기 전에 남긴 name.bak을 읽는다(그것도 없으면 None)."""
    path = PATH.parent / name
    found = read_file(path)
    return found if found is not None or not path.exists() else read_file(backup_of(path))


def backup_of(path):
    return path.with_name(path.name + ".bak")


def backup(path):
    """덮어쓰기 전에 원래 파일을 path.bak으로 남긴다(한 세대). 원래 파일이 깨졌으면 남기지 않아, 그 전의 성한 .bak을 지킨다."""
    if read_file(path) is not None:
        shutil.copyfile(path, backup_of(path))


def write(name, data):
    """설정 폴더에 name 파일로 data를 JSON으로 쓴다. 덮어쓰기 전에 원래 파일을 name.bak으로 남긴다."""
    PATH.parent.mkdir(parents=True, exist_ok=True)
    path = PATH.parent / name
    backup(path)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load():
    """저장된 설정 중 쓸 수 있는 값만. 파일이 없거나 읽을 수 없으면 빈 설정."""
    return {name: value for name, value in (read(PATH.name) or {}).items() if valid(name, value)}


def save(changes):
    """바뀐 설정만 받아 기존 설정에 더해 저장한다."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("저장할 설정이 없습니다.")
    for name, value in changes.items():
        if not valid(name, value) and not (name in SIZES and value == 0 and type(value) is int):
            raise ValueError(f"'{name}' 설정에 쓸 수 없는 값입니다: {value}")
    write(PATH.name, {name: value for name, value in {**load(), **changes}.items() if value != 0})


MAX_SAVED = 100  # 이름을 붙여 저장해 둘 수 있는 검색(구성) 수
MAX_SEARCH_SIZE = 20_000  # 검색 하나의 크기(JSON 글자 수)
MAX_RECIPE_SIZE = 100_000  # 전처리 구성 하나의 크기. 컬럼 100개에 전처리를 아홉 개씩 정해도 약 3만 자라 검색보다 크게 잡는다.
MAX_BOARD_CHARTS = 24  # 대시보드 하나에 넣을 수 있는 그래프 수
MAX_BOARD_SIZE = MAX_BOARD_CHARTS * MAX_SEARCH_SIZE  # 대시보드 하나의 크기. 그래프마다 검색 하나만큼


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


def dashboards():
    """이름을 붙여 저장한 대시보드 {이름: {"charts": [그래프]}}. 그래프마다 다시 그릴 조회(테이블·조건·X·계열)만 있고 연결 정보와 값은 없다."""
    return named("dashboards.json")


def save_dashboard(name, board):
    if board is not None and not (isinstance(board, dict) and isinstance(board.get("charts"), list) and 1 <= len(board["charts"]) <= MAX_BOARD_CHARTS):
        raise ValueError(f"대시보드에는 그래프가 1개 이상 {MAX_BOARD_CHARTS}개 이하로 있어야 합니다.")
    return save_named("dashboards.json", "대시보드", MAX_BOARD_SIZE, name, board)
