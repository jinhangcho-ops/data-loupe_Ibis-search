"""대시보드 카드 설정 패널을 접었다 펴도 내용을 다시 그리지 않는다(고르던 데이터 값이 남는다)."""
import shutil

import pytest

from test_fix_audit_js import between, run_node

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


def test_fold_keeps_panel_content(tmp_path):
    fold = between("dash_panel.js", "  fold(on) {", "\n  }\n") + "\n  }\n"
    body = """
const kids = [], classes = new Set();
const root = { append: k => kids.push(k), classList: { toggle: (c, on) => on ? classes.add(c) : classes.delete(c) },
  querySelector: () => kids.find(k => k.className === "side-strip") ?? null };
const el = (tag, p = {}) => ({ ...p, remove() { kids.splice(kids.indexOf(this), 1); } });
const picked = { className: "pdata", value: "orders" };
kids.push(picked);
const panel = { root, folded: false, %s };
panel.fold(true);
const strip = kids.find(k => k.className === "side-strip");
const folded = [classes.has("folded"), !!strip, kids.includes(picked)];
strip.onclick();
console.log(JSON.stringify([folded, classes.has("folded"), kids.length, kids[0] === picked && picked.value]));
""" % fold.strip()
    assert run_node(tmp_path, body) == [[True, True, True], False, 1, "orders"]


def test_new_sentence_hides_empty_slot_errors_until_edited():
    js = between("sentences.js", "  function sentenceCard(item) {", "  // 기간 한 줄")
    assert "const shown = item.touched ? d.issues : [];" in js  # 막 고른 문장에는 오류를 늘어놓지 않는다
    assert "function redo(rebuild) { item.touched = true;" in js  # 칸을 고치면 보인다

