"""무거운 데이터 라이브러리(ibis, polars, duckdb, pyarrow)를 처음 쓸 때 올리게 한다.
프로젝트 모듈은 `import ibis` 대신 `from lazy import ibis`로 쓴다: sys.modules에 든 지연 모듈에 `import` 문을 쓰면 그 자리에서 올라와 버리기 때문이다.
설치되어 있지 않은 것은 건너뛰고, 이미 올라와 있는 것은 그대로 둔다."""
import importlib
import importlib.util
import os
import sys
import threading

# 쿼리가 끝나면 그동안 쓴 메모리를 바로 운영체제에 돌려준다(Polars가 쓰는 메모리 관리자 mimalloc의 설정. 라이브러리를 올리기 전에 정해야 한다).
# 기본값이면 다음 쿼리 때까지 쥐고 있어, 큰 Parquet·CSV를 집계한 뒤 1GB 넘게 남았다.
os.environ.setdefault("MIMALLOC_PURGE_DELAY", "0")

for name in ("ibis", "polars", "duckdb", "pyarrow"):
    spec = importlib.util.find_spec(name)
    if spec is not None and name not in sys.modules:
        spec.loader = importlib.util.LazyLoader(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)

ibis = sys.modules["ibis"] if "ibis" in sys.modules else importlib.import_module("ibis")


class Later:
    """기능 모듈의 지연 참조: 속성을 처음 쓸 때 그 모듈을 올린다(한 번 올린 뒤에는 그대로 돌려준다). 라우트 파일이 모듈 맨 위에서 기능 모듈을 직접 import하지 않고 이것으로 가리킨다.
    쓰기·지우기도 실제 모듈에 한다(시험이 monkeypatch로 바꾸어 끼울 수 있다). 모듈 이름은 패키지부터 쓴다: Later("query.flow")."""

    def __init__(self, name):
        object.__setattr__(self, "_name", name)

    def _module(self):
        return importlib.import_module(self._name)

    def __getattr__(self, attr):
        return getattr(self._module(), attr)

    def __setattr__(self, attr, value):
        setattr(self._module(), attr, value)

    def __delattr__(self, attr):
        delattr(self._module(), attr)

    def __repr__(self):
        return f"<Later {self._name}>"


def loaded(name):
    """이미 올라온 모듈(없으면 None). 올리지 않고 상태만 볼 때 쓴다."""
    return sys.modules.get(name)


def preload(*names):
    """기능 모듈 묶음을 뒤 스레드에서 미리 올린다(페이지 이동 때). 기다리지 않는다. 그 사이에 같은 모듈을 쓰는 요청이 오면 import 잠금이 한 번만 올리게 한다."""
    def work():
        for name in names:
            try:
                importlib.import_module(name)
            except Exception:  # 미리 올리기는 덤이다. 실패해도 쓸 때 다시 올리며 그때 오류가 난다.
                pass
    threading.Thread(target=work, daemon=True).start()
