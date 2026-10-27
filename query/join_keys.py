"""공통 키 찾기(6-3): 두 테이블 A·B의 컬럼 짝 중 이을 키 후보를 점수로 매긴다.
점수 = 0.35·이름 + 0.45·겹침 + 0.10·타입 + 0.10·고유. 겹침은 A 표본의 서로 다른 값(최대 1,000개)이 B에 있는 비율(표본 기준, [전체로 확인]은 full).
1등 ≥ 0.7이고 2등과 0.15 이상 차이면 자동 선택(auto), 아니면 상위 3개를 근거와 함께 고르게 한다(ask). 후보가 없으면 날짜 맞춤(일·주·월)을 제안한다(none).
단일 키 겹침이 0.5 미만이면 후보 짝의 두 컬럼 묶음도 본다.
원격 DB(held.remote)는 값을 읽는 조회를 새로 보내지 않는다: 부르는 쪽이 이미 있는 표본(samples)을 주면 그것으로, 없으면 겹침을 '표본 없음'으로 두고 이름·타입으로만 점수를 낸다.
데이터를 읽는 함수는 server.run으로 연결 스레드에서 부른다."""
import itertools
import re

from core import parse

SAMPLE_VALUES = 1000  # 겹침을 볼 A의 서로 다른 값 수
SAMPLE_ROWS = 5000  # 후보를 줄일 때 읽는 표본 행 수
MAX_EVAL = 60  # 값으로 겹침을 재 보는 짝 수
PER_COLUMN = 3  # A 컬럼마다 값으로 재 보는 B 쪽 후보 수
DECIDE_MIN, DECIDE_GAP = 0.7, 0.15  # 자동 선택: 1등 점수 이상, 2등과의 차이 이상
MIN_SCORE = 0.3  # 후보로 보이는 점수
COMPOSITE_BELOW = 0.5  # 단일 키 겹침이 이보다 작으면 두 컬럼 묶음도 본다
COMPOSITE_B_MAX = 2_000_000  # 묶음 겹침을 볼 때 B에서 받는 서로 다른 값 쌍의 상한
WEIGHTS = {"name": 0.35, "contain": 0.45, "type": 0.10, "unique": 0.10}
DATE_UNITS = ("day", "week", "month")
CAST_TYPE = 0.3  # 글자↔숫자·날짜처럼 바꿔야 비교되는 짝의 타입 점수
NO_SAMPLE = "표본 없음"
CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
SPLIT = re.compile(r"[\s_\-]+")


# ---------- 이름 ----------

def words(name):
    """컬럼 이름을 낱말들로: camel 분해, 소문자, _/공백/대시로 나눈다."""
    return [w for w in SPLIT.split(CAMEL.sub("_", str(name)).lower()) if w]


def singular(word):
    """테이블 이름 낱말의 단수(영어 -ies/-s)."""
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    return word[:-1] if word.endswith("s") and not word.endswith("ss") and len(word) > 1 else word


def name_score(a, b, a_table=None, b_table=None):
    """컬럼 이름 비슷함 0~1: 정규화(소문자·_/공백/대시 제거·camel 분해)해서 같으면 1, 한쪽이 `테이블이름(단수)+id`이고 다른 쪽이 `id`면 0.9, 아니면 낱말 Jaccard."""
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0
    if "".join(wa) == "".join(wb):
        return 1.0
    for col, other, table in ((a, b, b_table), (b, a, a_table)):  # 컬럼 col이 '테이블이름+id', 다른 쪽(other)이 'id'
        if table and "".join(words(other)) == "id" and "".join(words(col)) == "".join(words(singular("".join(words(table))) + "_id")):
            return 0.9
    sa, sb = set(wa), set(wb)
    return len(sa & sb) / len(sa | sb)


# ---------- 타입 ----------

def pair_type(ka, kb):
    """(타입 점수, 바꿔야 하는지) 또는 None(비교할 수 없는 짝). parse.kind의 종류(int·float·string·date·bool·time)로."""
    if ka == kb and ka != "other":
        return 1.0, False
    if {ka, kb} == {"int", "float"}:
        return 0.8, False
    if "string" in (ka, kb) and {ka, kb} - {"string"} <= {"int", "float", "date", "bool"} and ka != kb:
        return CAST_TYPE, True
    return None


# ---------- 값 ----------

def column_values(frame, col):
    """표본 표의 컬럼에서 빈 값을 뺀 서로 다른 값 목록(앞 SAMPLE_VALUES개)."""
    return frame[col].drop_nulls().unique().head(SAMPLE_VALUES).to_list()


def as_text(values):
    return [str(v) for v in values]


def sample_containment(a_vals, b_frame, cb, cast):
    """A 값들 중 B 표본에 있는 비율(표본끼리). A 값이 없으면 None."""
    if not a_vals:
        return None
    b_vals = set(as_text(b_frame[cb].drop_nulls().to_list()) if cast else b_frame[cb].drop_nulls().to_list())
    mine = as_text(a_vals) if cast else a_vals
    return sum(1 for v in mine if v in b_vals) / len(mine)


def db_containment(b, cb, a_vals, cast):
    """A 값들(표본의 서로 다른 값)이 B 컬럼에 있는 비율: B에서 isin으로 찾은 서로 다른 수 / A 값 수. DB에서 센다(원격에서는 부르지 않는다)."""
    if not a_vals:
        return None
    col = b[cb].cast("string") if cast else b[cb]
    found = b.filter(col.isin(as_text(a_vals) if cast else a_vals)).select(col.name("v")).distinct().count().to_pyarrow().as_py()
    return min(1.0, found / len(a_vals))


def full_containment(a, ca, b, cb, cast):
    """A의 서로 다른 값 전체가 B에 있는 비율(semi join). 큰 테이블에서는 오래 걸린다."""
    av = a.filter(a[ca].notnull()).select(v=a[ca].cast("string") if cast else a[ca]).distinct()
    bv = b.filter(b[cb].notnull()).select(w=b[cb].cast("string") if cast else b[cb]).distinct()
    total = av.count().to_pyarrow().as_py()
    if not total:
        return None
    return av.semi_join(bv, av.v == bv.w).count().to_pyarrow().as_py() / total


def unique_in(frame, col):
    """표본 표에서 컬럼 값이 모두 다른지(빈 값은 뺀다)."""
    values = frame[col].drop_nulls()
    return len(values) > 0 and values.n_unique() == len(values)


# ---------- 후보 ----------

def pre_pairs(a, b, a_name, b_name):
    """타입이 비교되는 컬럼 짝 [(ca, cb, 이름 점수, 타입 점수, 바꿔야 하는지)]."""
    ka = {c: parse.kind(t) for c, t in a.schema().items()}
    kb = {c: parse.kind(t) for c, t in b.schema().items()}
    out = []
    for ca, cb in itertools.product(ka, kb):
        typed = pair_type(ka[ca], kb[cb])
        if typed:
            out.append((ca, cb, name_score(ca, cb, a_name, b_name), typed[0], typed[1]))
    return out


def reasons(c):
    """후보의 근거 문장들."""
    out = []
    if c["name"] >= 1:
        out.append("이름이 같아요")
    elif c["name"] >= 0.9:
        out.append("이름이 ‘테이블이름+id’ ↔ ‘id’ 모양이에요")
    elif c["name"] > 0:
        out.append(f"이름이 비슷해요({c['name']:.2f})")
    if c["contain"] is None:
        out.append("값 겹침은 표본이 없어 보지 못했어요(이름·타입으로만 봤어요)")
    else:
        out.append(f"A 값의 {c['contain'] * 100:.0f}%가 B에 있어요({c['basis']})")
    if c["cast"]:
        out.append("타입이 달라 한쪽을 바꿔(cast) 이어야 해요")
    if c["unique"]:
        out.append("한쪽 컬럼 값이 모두 달라요" + (" (표본 기준)" if c["basis"] != "전체" else ""))
    return out


def score_of(name, contain, typ, uniq):
    """네 점수의 합. 겹침을 못 봤으면(None) 이름·타입 가중치로 나눠 0~1로 맞춘다."""
    w = WEIGHTS
    if contain is None:
        return (w["name"] * name + w["type"] * typ) / (w["name"] + w["type"])
    return w["name"] * name + w["contain"] * contain + w["type"] * typ + w["unique"] * uniq


def candidate(keys, name, contain, typ, uniq, cast, basis):
    c = {"keys": [list(k) for k in keys], "a": keys[0][0], "b": keys[0][1], "name": round(name, 4), "contain": None if contain is None else round(contain, 4),
         "type": round(typ, 4), "unique": uniq, "cast": cast, "basis": basis}
    c["score"] = round(score_of(name, contain, typ, uniq), 4)
    c["reasons"] = reasons(c)
    return c


def decide(cands):
    """(결정, 후보들): auto(1등 ≥ 0.7, 2등과 차이 ≥ 0.15) / ask(상위 3개) / none(후보 없음)."""
    if not cands:
        return "none", []
    top = cands[0]["score"]
    second = cands[1]["score"] if len(cands) > 1 else 0.0
    if top >= DECIDE_MIN and top - second >= DECIDE_GAP:
        return "auto", cands[:1]
    return "ask", cands[:3]


def date_alignment(a, b, a_name, b_name):
    """키 후보가 없을 때: 양쪽 날짜 컬럼을 일·주·월로 맞추는 제안 {a, b, units, default}. 날짜 컬럼이 없으면 None."""
    dates = lambda t: [c for c, ty in t.schema().items() if parse.kind(ty) == "date"]
    pairs = [(ca, cb) for ca in dates(a) for cb in dates(b)]
    if not pairs:
        return None
    ca, cb = max(pairs, key=lambda p: name_score(p[0], p[1], a_name, b_name))
    return {"a": ca, "b": cb, "units": list(DATE_UNITS), "default": "day", "text": f"‘{ca}’과 ‘{cb}’을 일·주·월 단위로 맞춰 이을 수 있어요."}


def sample_frames(a, b, samples, remote):
    """(A 표본, B 표본) Polars 표 또는 (None, None). 주어진 표본이 있으면 그것, 로컬 연결이면 앞 SAMPLE_ROWS행, 원격이면 없음(새 조회를 보내지 않는다)."""
    if samples:
        return samples.get("a"), samples.get("b")
    if remote:
        return None, None
    return a.limit(SAMPLE_ROWS).to_polars(), b.limit(SAMPLE_ROWS).to_polars()


def suggest(a, b, a_name="", b_name="", remote=False, samples=None, full=False):
    """A·B(Ibis 표)의 공통 키 제안 {decision, candidates, date, basis, note}.
    remote: 원격 DB면 값을 읽는 새 조회를 보내지 않는다. samples = {a, b}(Polars 표): 이미 있는 표본(없으면 로컬은 앞 SAMPLE_ROWS행). full: 전체로 확인(로컬만). decision: auto·ask·none."""
    pairs = pre_pairs(a, b, a_name, b_name)
    fa, fb = sample_frames(a, b, samples, remote)
    sampled = fa is not None and fb is not None
    cheap = {}
    for ca, cb, ns, ts, cast in pairs:  # 1차: 표본끼리 겹침으로 값을 읽어 볼 짝을 고른다
        cheap[ca, cb] = sample_containment(column_values(fa, ca), fb, cb, cast) if sampled else None
    rank = lambda p: -(WEIGHTS["name"] * p[2] + WEIGHTS["type"] * p[3] + WEIGHTS["contain"] * (cheap[p[0], p[1]] or 0))
    chosen, per = [], {}
    for p in sorted(pairs, key=rank):
        if per.get(p[0], 0) < PER_COLUMN and len(chosen) < MAX_EVAL:
            per[p[0]] = per.get(p[0], 0) + 1
            chosen.append(p)
    cands, uniq_cache = [], {}
    for ca, cb, ns, ts, cast in chosen:
        if sampled and (remote or not fa.height):
            contain, basis = cheap[ca, cb], "표본 기준"
        elif sampled:
            values = column_values(fa, ca)
            if full:
                contain, basis = full_containment(a, ca, b, cb, cast), "전체"
            else:
                contain, basis = db_containment(b, cb, values, cast), "표본 기준"
        else:
            contain, basis = None, NO_SAMPLE
        if sampled:
            for side, frame, col in (("a", fa, ca), ("b", fb, cb)):
                uniq_cache.setdefault((side, col), unique_in(frame, col))
            uniq = uniq_cache["a", ca] or uniq_cache["b", cb]
        else:
            uniq = False
        cands.append(candidate([(ca, cb)], ns, contain, ts, uniq, cast, basis))
    singles = sorted((c for c in cands if c["score"] >= MIN_SCORE), key=lambda c: (-c["score"], c["a"], c["b"]))
    best = max((c["contain"] for c in singles if c["contain"] is not None), default=None)
    if sampled and not remote and (best is None or best < COMPOSITE_BELOW) and len(singles) >= 2:
        singles += composites(a, b, singles[:4], fa, a_name, b_name)
        singles.sort(key=lambda c: (-c["score"], c["a"], c["b"]))
    decision, top = decide(singles)
    basis = NO_SAMPLE if not sampled else ("전체" if full else "표본 기준")
    note = ("원격 DB라 값을 읽는 조회를 새로 보내지 않았어요. 이미 받아 둔 표본이 없어서 이름·타입으로만 점수를 냈어요." if remote and not sampled
            else "표본 기준이에요. [전체로 확인]을 누르면 전체 값으로 다시 재요." if basis == "표본 기준" else "")
    return {"decision": decision, "candidates": top, "date": date_alignment(a, b, a_name, b_name) if decision == "none" else None, "basis": basis, "note": note}


def wider(left, right):
    """두 polars 타입을 값이 잘리지 않게 비교할 타입: 같으면 그대로, 숫자끼리 다르면 Float64, 날짜와 시각이면 시각, 그 밖은 왼쪽."""
    import polars as pl
    if left == right:
        return left
    if left.is_numeric() and right.is_numeric():
        return pl.Float64
    if {left.base_type(), right.base_type()} == {pl.Date, pl.Datetime}:
        return pl.Datetime("us")
    return left


def composites(a, b, top, fa, a_name, b_name):
    """상위 후보 짝(서로 다른 컬럼)의 두 컬럼 묶음 후보. A 표본의 값 쌍이 B에 있는 비율(B의 서로 다른 값 쌍을 받아 PC에서 비교, COMPOSITE_B_MAX까지)."""
    import polars as pl
    out = []
    for x, y in itertools.combinations(top, 2):
        if x["a"] == y["a"] or x["b"] == y["b"]:
            continue
        ca, cb = [x["a"], y["a"]], [x["b"], y["b"]]
        tuples = fa.select(ca).drop_nulls().unique().head(SAMPLE_VALUES)
        if not tuples.height:
            continue
        bt = b.select(cb).distinct().limit(COMPOSITE_B_MAX + 1).to_polars().drop_nulls()
        if bt.height > COMPOSITE_B_MAX:
            continue
        cast = x["cast"] or y["cast"]
        # 두 쪽을 같은 타입으로: 글자로 바꾸는 짝이면 글자, 정수↔실수는 실수(1.5가 1로 잘려 맞지 않게), 날짜↔시각은 시각
        kinds = [pl.String if cast else wider(fa.schema[a_col], bt.schema[c]) for a_col, c in zip(ca, cb)]
        left = tuples.select([pl.col(c).cast(k, strict=False) for c, k in zip(ca, kinds)]).rename(dict(zip(ca, cb)))
        right = bt.select([pl.col(c).cast(k, strict=False) for c, k in zip(cb, kinds)])
        contain = left.join(right.unique(), on=cb, how="semi").height / left.height
        mine = fa.select(ca).drop_nulls()
        uniq = mine.height > 0 and mine.n_unique() == mine.height
        out.append(candidate(list(zip(ca, cb)), (x["name"] + y["name"]) / 2, contain, (x["type"] + y["type"]) / 2, uniq, cast, "표본 기준"))
    return [c for c in out if c["score"] >= MIN_SCORE]
