"""무거운 데이터 라이브러리(ibis, polars, duckdb, pyarrow)를 처음 쓸 때 올리게 한다.
프로젝트 모듈은 `import ibis` 대신 `from lazy import ibis`로 쓴다: sys.modules에 든 지연 모듈에 `import` 문을 쓰면 그 자리에서 올라와 버리기 때문이다.
설치되어 있지 않은 것은 건너뛰고, 이미 올라와 있는 것은 그대로 둔다."""
import importlib
import importlib.util
import os
import sys

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
