"""메모리 비우기(설정 창의 [메모리]): 프로그램이 붙잡고 있는 데이터(연결, 받아 둔 결과, 표본 …)를 보이고 고른 것만 놓는다.
붙잡는 모듈이 hold로 자기 것을 등록한다(이름, 지금 쥔 양을 말하는 함수, 놓는 함수). 여기는 무엇이 있는지 모른다.
올라온 라이브러리(Polars·DuckDB 등)는 파이썬이 실행 중에 내릴 수 없어 이름만 보인다(앱을 다시 켜야 내려간다)."""
import ctypes
import gc
import sys
from collections.abc import Callable

HOLDS: dict[str, tuple[str, Callable[[], str], Callable[[], None]]] = {}  # {id: (화면 이름, 쥔 양 글자(없으면 ""), 놓는 함수)} 등록 차례가 화면 차례
HEAVY = ("ibis", "polars", "duckdb", "pyarrow", "numpy", "kiwipiepy")  # 화면에 알릴 큰 라이브러리
LAZY = "_LazyModule"  # importlib.util.LazyLoader가 아직 올리지 않은 모듈의 클래스 이름
MB = 2**20


def hold(key: str, label: str, detail: Callable[[], str], drop: Callable[[], None]) -> None:
    """붙잡는 것 하나를 등록한다. detail은 지금 쥔 것이 없으면 ""를 돌려준다(목록에서 빠진다)."""
    HOLDS[key] = (label, detail, drop)


def size_text(size: int) -> str:
    """바이트 → "12.3MB"."""
    return f"{size / MB:,.1f}MB"


def listing() -> list[dict[str, str]]:
    """지금 쥔 것이 있는 항목들 [{id, label, detail}]."""
    found = [(key, label, detail()) for key, (label, detail, _) in HOLDS.items()]
    return [{"id": key, "label": label, "detail": text} for key, label, text in found if text]


def modules() -> list[str]:
    """실제로 올라온 큰 라이브러리 이름(지연 모듈로만 걸려 있는 것은 뺀다)."""
    return [name for name in HEAVY if name in sys.modules and type(sys.modules[name]).__name__ != LAZY]


def trim() -> None:
    """놓은 메모리를 운영체제에 돌려준다: 파이썬 쓰레기 모으기, Arrow가 쥔 빈 메모리, 그리고 Windows의 작업 집합 비우기(다시 쓰는 것은 그때 읽어 온다)."""
    gc.collect()
    arrow = sys.modules.get("pyarrow")
    if arrow is not None and type(arrow).__name__ != LAZY:
        arrow.default_memory_pool().release_unused()
    if sys.platform == "win32":
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        ctypes.windll.psapi.EmptyWorkingSet(ctypes.c_void_p(kernel.GetCurrentProcess()))


def drop(keys: list[str]) -> None:
    """고른 항목을 놓고 메모리를 돌려준다. 모르는 id가 있으면 아무것도 놓지 않고 ValueError."""
    unknown = [key for key in keys if key not in HOLDS]
    if unknown:
        raise ValueError(f"비울 수 없는 항목이에요: {', '.join(map(str, unknown))}")
    for key in keys:
        HOLDS[key][2]()
    trim()
