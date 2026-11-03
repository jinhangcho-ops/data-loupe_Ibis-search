"""문장 보기: 막 고른 문장은 빈칸 오류를 늘어놓지 않는다. (옛 대시보드 카드 설정 패널 접기 시험은 옛 화면과 함께 지웠다.)"""
import shutil

import pytest

from test_fix_audit_js import between

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")


def test_new_sentence_hides_empty_slot_errors_until_edited():
    js = between("sentences.js", "  function sentenceCard(item) {", "  // 기간 한 줄")
    assert "const shown = item.touched ? d.issues : [];" in js  # 막 고른 문장에는 오류를 늘어놓지 않는다
    assert "function redo(rebuild) { item.touched = true;" in js  # 칸을 고치면 보인다

