"""예시 데이터: 연결할 데이터가 없어도 화면을 둘러볼 수 있게 지어낸 표 세 개를 DuckDB 파일로 만든다.
행은 파이썬으로 돌지 않고 DuckDB의 range()·hash()로 만든다(실행마다 같은 값). 파일은 설정 폴더의 demo\\demo.duckdb에 한 번만 만든다."""
import os

import duckdb

from core import settings

FOLDER, FILE = "demo", "demo.duckdb"
START = "2025-01-01"  # 주문일·시각의 첫 날
DAYS = 365  # 주문일·시각이 퍼지는 날 수
SIGNUP_START, SIGNUP_DAYS = "2024-01-01", 600  # 가입일의 첫 날과 퍼지는 날 수
ORDERS, CUSTOMERS, VISITS = 5000, 300, 8000  # 표마다 행 수
# (만드는 문장, 넣을 값). hash(i, 씨앗)을 나눈 나머지로 값을 고른다
TABLES = (
    ("""create table 주문 as select i + 1 as 주문ID, cast(? as date) + cast(hash(i, 1) % ? as integer) as 주문일, cast(hash(i, 2) % ? as integer) + 1 as 고객ID,
        list_extract(['식품', '의류', '가전', '도서', '생활'], cast(hash(i, 3) % 5 as integer) + 1) as 상품분류,
        list_extract(['서울', '경기', '부산', '대구', '광주', '제주'], cast(hash(i, 4) % 6 as integer) + 1) as 지역,
        list_extract(['카드', '계좌이체', '간편결제'], cast(hash(i, 5) % 3 as integer) + 1) as 결제수단,
        cast(hash(i, 6) % 5 as integer) + 1 as 수량, (cast(hash(i, 7) % 200 as integer) + 5) * 500 as 금액,
        list_extract(['완료', '완료', '완료', '완료', '취소', '환불'], cast(hash(i, 8) % 6 as integer) + 1) as 상태
        from range(?) t(i)""", (START, DAYS, CUSTOMERS, ORDERS)),
    ("""create table 고객 as select i + 1 as 고객ID, cast(? as date) + cast(hash(i, 11) % ? as integer) as 가입일,
        list_extract(['일반', '일반', '실버', '골드'], cast(hash(i, 12) % 4 as integer) + 1) as 등급,
        list_extract(['서울', '경기', '부산', '대구', '광주', '제주'], cast(hash(i, 13) % 6 as integer) + 1) as 지역
        from range(?) t(i)""", (SIGNUP_START, SIGNUP_DAYS, CUSTOMERS)),
    ("""create table 방문기록 as select cast(hash(i, 21) % ? as integer) + 1 as 사용자,
        list_extract(['방문', '방문', '방문', '방문', '상품보기', '상품보기', '상품보기', '장바구니', '장바구니', '결제'], cast(hash(i, 22) % 10 as integer) + 1) as 단계,
        cast(? as timestamp) + to_seconds(cast(hash(i, 23) % ? as bigint)) as 시각
        from range(?) t(i)""", (CUSTOMERS, START, DAYS * 86400, VISITS)),
)


def make() -> dict[str, str]:
    """예시 파일이 없으면 만들고, 그 파일에 연결할 칸 값({"kind", "path", "file"})을 돌려준다. 있으면 다시 만들지 않는다."""
    path = settings.PATH.parent / FOLDER / FILE
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(FILE + ".part")  # 만들다 끊겨도 반쪽 파일이 예시 파일 자리에 남지 않게
        part.unlink(missing_ok=True)
        with duckdb.connect(str(part)) as con:
            for sql, values in TABLES:
                con.execute(sql, values)
        os.replace(part, path)
    return {"kind": "folder", "path": str(path.parent), "file": str(path)}
