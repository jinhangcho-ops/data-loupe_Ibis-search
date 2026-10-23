# PyInstaller 빌드 설정: pyinstaller ibis_search.spec → dist/ibis_search/ (exe + 라이브러리 폴더)
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

sys.path.insert(0, SPECPATH)  # 이 폴더의 core/drivers.py를 읽기 위해
from core.drivers import DRIVERS

# exe에 넣을 DB. Java가 필요한 pyspark·flink는 뺀다.
BACKENDS = ["polars"] + [name for name, (*_, program) in DRIVERS.items() if program != "java"]
DRIVER_MODULES = [module for name, (_, module, *_) in DRIVERS.items() if name in BACKENDS]

# 실행 중에 이름으로 불러오는 모듈은 PyInstaller가 찾지 못하므로 직접 넣는다.
hidden = collect_submodules("sqlglot.dialects")
# 기능 패키지의 모듈은 lazy.Later가 이름(문자열)으로 처음 쓸 때 불러오므로 PyInstaller가 import 문으로는 찾지 못한다. 패키지별로 통째로 넣는다.
for package in ["core", "query", "prep", "analysis", "metrics", "dashboard", "collect", "integrations", "geo"]:
    hidden += collect_submodules(package)
for name in BACKENDS:
    hidden += collect_submodules(f"ibis.backends.{name}")
for module in DRIVER_MODULES + ["psycopg_binary", "cryptography", "xlsxwriter"]:  # cryptography: Oracle 접속 암호화, xlsxwriter: Excel 저장
    hidden += collect_submodules(module)
# Kafka 적재(load_kafka)는 함수 안에서 불러온다. librdkafka DLL(confluent_kafka.libs)은 PyInstaller가 delvewheel 폴더에서 찾아 넣는다.
hidden += ["confluent_kafka", "confluent_kafka.cimpl", "fastexcel"]  # fastexcel: polars가 Excel을 읽을 때만 불러온다
# Slack 봇(integrations.adapters.slack_bot)은 봇을 켤 때 slack_sdk를 불러온다(Socket Mode 웹소켓은 slack_sdk 안의 것).
hidden += collect_submodules("slack_sdk.socket_mode.builtin") + ["slack_sdk.web", "slack_sdk.webhook", "slack_sdk.socket_mode.response"]

# 좌표 변환(geo/tm.py)은 처음 쓸 때 pyproj를 불러온다. 좌표계 목록(proj.db 등)은 데이터 파일이라 따로 넣어야 exe 안에서 TM 변환이 된다.
hidden += collect_submodules("pyproj")

a = Analysis(
    ["desktop.py"],
    datas=[("static", "static")] + copy_metadata("ibis-framework") + collect_data_files("pyproj"),  # Ibis는 설치 정보로 백엔드를 찾는다.
    hiddenimports=hidden,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, exclude_binaries=True, name="ibis_search", console=False)
COLLECT(exe, a.binaries, a.datas, name="ibis_search")
