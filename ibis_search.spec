# PyInstaller 빌드 설정: pyinstaller ibis_search.spec → dist/ibis_search/ (exe + 라이브러리 폴더)
import sys

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

sys.path.insert(0, SPECPATH)  # 이 폴더의 drivers.py를 읽기 위해
from drivers import DRIVERS

# exe에 넣을 DB. Java가 필요한 pyspark·flink는 뺀다.
BACKENDS = ["polars"] + [name for name, (*_, program) in DRIVERS.items() if program != "java"]
DRIVER_MODULES = [module for name, (_, module, *_) in DRIVERS.items() if name in BACKENDS]

# 실행 중에 이름으로 불러오는 모듈은 PyInstaller가 찾지 못하므로 직접 넣는다.
hidden = collect_submodules("sqlglot.dialects")
for name in BACKENDS:
    hidden += collect_submodules(f"ibis.backends.{name}")
for module in DRIVER_MODULES + ["cryptography", "xlsxwriter"]:  # cryptography: Oracle 접속 암호화, xlsxwriter: Excel 저장
    hidden += collect_submodules(module)
# Kafka 적재(load_kafka)는 함수 안에서 불러온다. librdkafka DLL(confluent_kafka.libs)은 PyInstaller가 delvewheel 폴더에서 찾아 넣는다.
hidden += ["confluent_kafka", "confluent_kafka.cimpl", "fastexcel"]  # fastexcel: polars가 Excel을 읽을 때만 불러온다

a = Analysis(
    ["desktop.py"],
    datas=[("static", "static")] + copy_metadata("ibis-framework"),  # Ibis는 설치 정보로 백엔드를 찾는다.
    hiddenimports=hidden,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, exclude_binaries=True, name="ibis_search", console=False)
COLLECT(exe, a.binaries, a.datas, name="ibis_search")
