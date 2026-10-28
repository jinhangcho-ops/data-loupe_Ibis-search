"""감사 32차: busy가 끝나며 켠 단추를 다시 맞추기(SQL 쪽 넘기기·문장 실행·수집 고르기), 조인 키 컬럼 기억 비우기, NL2SQL 빈 값 0%."""
from core import profiler
from core import settings
from query import nl2sql
from test_fix_audit_js import between, run_node


def test_sql_next_stays_off_on_last_page(tmp_path):
    busy = between("common.js", "async function busy(", "\n}\n") + "\n}\n"
    pager = between("index.html", "function sqlPager()", "\n")
    body = """
const btns = { "sql-prev": { disabled: false }, "sql-next": { disabled: false, textContent: "다음", style: {}, offsetWidth: 10 } };
let sqlSkip = 1000, sqlMore = true;
const $ = id => btns[id];
%s
async function runSql() { sqlMore = false; sqlPager(); }
(async () => { await busy($("sql-next"), () => runSql()).finally(sqlPager); console.log(JSON.stringify([btns["sql-prev"].disabled, btns["sql-next"].disabled])); })();
""" % pager
    assert run_node(tmp_path, "async function " + busy.split("async function ", 1)[1] + body) == [False, True]  # 예전엔 busy가 [다음]을 다시 켰다
    page = between("index.html", "function sqlPager()", "// 테이블 칩")
    assert page.count(".finally(sqlPager)") == 2


def test_other_busy_buttons_are_set_again_after_work():
    assert "runOne(item, out)).finally(recheck)" in between("sentences.js", "  function sentenceCard(item) {", "  // 기간 한 줄")
    assert "}).finally(() => lastBrowser && showBrowser(lastBrowser));" in between("collect.html", "const pickWith", '$("pick-field").onclick')


def test_join_key_columns_forgotten_on_switch_and_virtual_save():
    assert "for (const k in tableCols) delete tableCols[k];" in between("index.html", "function clearScreen() {", "\n}\n")
    assert "for (const k in tableCols) delete tableCols[k];" in between("index.html", "async function saveVirtual(body) {", "\n}\n")


def test_nl2sql_keeps_zero_null_ratio(folder):
    settings.write(profiler.CACHE, {"k||t": {"profile": {"columns": [{"name": "a", "infer": {"null_ratio": 0.0}}, {"name": "b", "infer": {"null_ratio": 0.25}}]}}})
    assert nl2sql.cached_infer("k", "", "t") == {"a": {"null_ratio": 0.0}, "b": {"null_ratio": 0.25}}  # 예전엔 a가 {}
