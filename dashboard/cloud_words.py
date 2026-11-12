"""워드클라우드 (나) 글 속 단어: 고른 태그가 달린 행의 글 컬럼을 이 PC로 받아 kiwipiepy 형태소 분석으로 나눠 단어별 빈도를 센다.
kiwipiepy는 이 방식을 처음 쓸 때만 올린다(tokenizer 안에서 불러온다, 모듈 맨 위 import 금지). dash_calc는 이 모듈을 Later("dashboard.cloud_words")로 가리킨다."""
from typing import TYPE_CHECKING

from core import dash_store
from core import parse
from core import settings
from lazy import ibis

if TYPE_CHECKING:
    import polars as pl

ROWS_SETTING = "cloud_rows"  # 형태소 분석을 위해 이 PC로 받는 최대 행 수의 설정 이름(settings.SIZES·DEFAULTS, [설정] 창)
CHARS_PER_ROW = 500  # 받는 글 길이 합의 상한 = 행 상한 × 이 값(연결의 length() 합, 넘으면 거부). 설정을 하나만 두려고 행 상한에 묶었다
MIN_LEN = 2  # 기본 최소 글자 수(한 글자 명사 '것'·'수' 같은 것을 뺀다)
STEM_SUFFIX = "다"  # 동사·형용사는 줄기에 붙여 보인다(먹 → 먹다)
STEM_POS = ("verb", "adj")  # 줄기에 STEM_SUFFIX를 붙이는 품사 묶음
DEFAULT_POS = ("noun",)  # 품사를 고르지 않았을 때
NO_KIWI = "글 속 단어 방식에 필요한 형태소 분석기(kiwipiepy)가 설치되어 있지 않아요. 라벨 이름 방식을 쓰거나 kiwipiepy가 든 버전으로 바꾸세요."
KIWI = {}  # 처음 쓸 때 만든 Kiwi 객체 {"kiwi": Kiwi()}. 한 번 올리면 프로그램이 끝날 때까지 둔다


def tokenizer() -> object:
    """[SPECIFICATION] plans/phase-4.md#tokenizer
    kiwipiepy의 Kiwi 객체를 처음 부를 때 만들어 KIWI에 두고 돌려준다(모듈 맨 위에서 import하지 않는다)."""
    import importlib
    import importlib.util
    if "kiwi" not in KIWI:
        if importlib.util.find_spec("kiwipiepy") is None:
            raise ValueError(NO_KIWI)
        KIWI["kiwi"] = importlib.import_module("kiwipiepy").Kiwi()
    return KIWI["kiwi"]


def texts_of(expr: "ibis.expr.types.Table", column: str) -> list[str]:
    """[SPECIFICATION] plans/phase-4.md#texts_of
    글 컬럼의 빈 값이 아닌 글자를 행 수(설정 cloud_rows)·길이 상한을 먼저 센 뒤 이 PC로 받아 목록으로 돌려준다."""
    parse.check_name(column, expr.columns, "글 컬럼")
    if not expr[column].type().is_string():
        raise ValueError("글 컬럼은 글자 컬럼이어야 해요.")
    limit = settings.get(ROWS_SETTING)
    t = expr.select(s=expr[column])
    t = t.filter(t.s.notnull(), t.s != "")
    size = t.aggregate(n=t.count(), c=t.s.length().sum()).to_pyarrow().to_pylist()[0]
    n, c = size["n"], size["c"] or 0
    if n > limit:
        raise ValueError(f"글이 있는 행이 {n:,}개라 이 PC로 받아 단어를 나눌 수 있는 {limit:,}행을 넘어요. 기간·조건·태그를 좁히거나 왼쪽 메뉴 [설정]의 '워드클라우드 단어 분석 최대 행 수'를 늘리세요.")
    if c > limit * CHARS_PER_ROW:
        raise ValueError(f"글 길이 합이 {c:,}자라 이 PC로 받을 수 있는 {limit * CHARS_PER_ROW:,}자를 넘어요. 기간·조건·태그를 좁히세요.")
    return t.to_pyarrow().column("s").to_pylist()


def count_words(texts: list[str], pos: list[str], stop: list[str], min_len: int) -> "pl.DataFrame":
    """[SPECIFICATION] plans/phase-4.md#count_words
    글 목록을 형태소로 나눠 고른 품사·불용어·최소 길이로 거른 단어별 빈도 표(w, n)를 많은 순으로 돌려준다."""
    import polars as pl
    groups = list(pos) or list(DEFAULT_POS)
    tag_group = {tag: g for g in groups for tag in dash_store.CLOUD_POS[g]}
    stems = [tag for g in STEM_POS if g in groups for tag in dash_store.CLOUD_POS[g]]
    found = [(tok.form, tok.tag) for sent in tokenizer().tokenize(texts) for tok in sent] if texts else []  # kiwipiepy 토큰 꺼내기(허용된 예외)
    df = pl.DataFrame(found, schema={"w": pl.String, "tag": pl.String}, orient="row")
    df = df.filter(pl.col("tag").is_in(list(tag_group)))
    df = df.with_columns(w=pl.when(pl.col("tag").is_in(stems)).then(pl.col("w") + STEM_SUFFIX).otherwise(pl.col("w")))
    df = df.filter(pl.col("w").str.len_chars() >= min_len, ~pl.col("w").is_in(list(stop)))
    return df.group_by("w").agg(n=pl.len()).sort(["n", "w"], descending=[True, False]).with_columns(pl.col("n").cast(pl.Int64))


def word_cloud(expr: "ibis.expr.types.Table", column: str, cloud: dict, limit: int) -> dict:
    """[SPECIFICATION] plans/phase-4.md#word_cloud
    식의 글 컬럼으로 워드클라우드 값 {values: {words: [{w, n}]}, note}를 만든다(limit개까지)."""
    df = count_words(texts_of(expr, column), cloud.get("pos") or [], cloud.get("stop") or [], cloud.get("min_len", MIN_LEN))
    words = df.head(limit + 1).to_dicts()
    return {"values": {"words": words[:limit]}, "note": f"단어가 많아 많이 나온 {limit}개만 표시해요." if len(words) > limit else ""}
