"""무거운 데이터 라이브러리(ibis, polars, duckdb, pyarrow)를 처음 쓸 때 올리게 한다.
프로젝트 모듈은 `import ibis` 대신 `from lazy import ibis`로 쓴다: sys.modules에 든 지연 모듈에 `import` 문을 쓰면 그 자리에서 올라와 버리기 때문이다.
설치되어 있지 않은 것은 건너뛰고, 이미 올라와 있는 것은 그대로 둔다."""
import importlib
import importlib.util
import sys

for name in ("ibis", "polars", "duckdb", "pyarrow"):
    spec = importlib.util.find_spec(name)
    if spec is not None and name not in sys.modules:
        spec.loader = importlib.util.LazyLoader(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)

ibis = sys.modules["ibis"] if "ibis" in sys.modules else importlib.import_module("ibis")
